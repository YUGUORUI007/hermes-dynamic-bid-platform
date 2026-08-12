from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import or_

from .config import get_public_base_url, get_secret_key
from .database import session_scope
from .dynamic_schema import (
    SCHEMA_VERSION,
    PROJECT_STATUSES,
    SchemaValidationError,
    PROJECT_METADATA_FIELDS,
    merge_project_payload,
    payload_fingerprint,
    project_schema_document,
    validate_project_payload,
)
from .models import ApiToken, AuditLog, IdempotencyRecord, Project, ProjectContentVersion, ProjectFollowup
from .services.project_archive import archive_project_data
from .services.dynamic_ui import apply_auto_lifecycle


router = APIRouter(prefix="/api/v1", tags=["Hermes API"])
VALIDATION_TOKEN_MAX_AGE_SECONDS = 30 * 60
RATE_LIMIT_REQUESTS = 120
RATE_LIMIT_WINDOW_SECONDS = 60
ALLOWED_SCOPES = {"projects:read", "projects:write", "projects:archive"}
TERMINAL_STATUSES = {"won", "lost", "abandoned", "partner_completed", "archived"}
_rate_windows: dict[int, deque[float]] = defaultdict(deque)

PROJECT_REQUEST_EXAMPLE = {
    "title": "示例物业服务项目",
    "status": "tracking",
    "owner": "项目负责人",
    "summary": "Hermes 根据招标材料整理，用户确认后写入。",
    "schema_version": "1.0",
    "content": {"sections": [{"id": "overview", "title": "项目概览", "blocks": [{"id": "budget", "type": "field", "label": "最高限价", "value": "100 万元", "semantic": "amount"}]}]},
}


def request_body_example(example: dict[str, Any], description: str) -> dict[str, Any]:
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}, "example": example}}}, "description": description}


class ApiProblem(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, *, errors: list[dict[str, str]] | None = None):
        super().__init__(status_code=status_code, detail={"code": code, "message": message, "errors": errors or []})


def error_response(request: Request, exc: ApiProblem) -> JSONResponse:
    detail = exc.detail
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": detail,
            "request_id": request.headers.get("X-Request-ID") or request.state.request_id,
        },
    )


def token_hash(raw_token: str) -> str:
    material = f"{get_secret_key()}:{raw_token}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def create_api_token(
    session,
    *,
    name: str,
    scopes: set[str],
    created_by: str | None,
    expires_in_days: int = 90,
) -> tuple[ApiToken, str]:
    invalid = scopes - ALLOWED_SCOPES
    if invalid:
        raise ValueError(f"不支持的 Scope: {', '.join(sorted(invalid))}")
    raw_token = f"hbp_live_{secrets.token_urlsafe(32)}"
    prefix = raw_token[:20]
    record = ApiToken(
        name=name.strip() or "Hermes Skill",
        token_prefix=prefix,
        token_hash=token_hash(raw_token),
        scopes=" ".join(sorted(scopes)),
        created_by=created_by,
        expires_at=datetime.utcnow() + timedelta(days=max(1, min(expires_in_days, 3650))),
    )
    session.add(record)
    session.flush()
    return record, raw_token


def parse_scopes(token: ApiToken) -> set[str]:
    return {item for item in token.scopes.split() if item}


def authenticate_token(request: Request, authorization: str | None = Header(default=None)) -> ApiToken:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiProblem(401, "missing_token", "请使用 Bearer Token 调用接口。")
    raw_token = authorization[7:].strip()
    if not raw_token:
        raise ApiProblem(401, "missing_token", "Bearer Token 不能为空。")
    digest = token_hash(raw_token)
    with session_scope() as session:
        token = session.query(ApiToken).filter(ApiToken.token_hash == digest).first()
        if not token or token.revoked_at is not None:
            raise ApiProblem(401, "invalid_token", "Token 无效或已撤销。")
        if token.expires_at and token.expires_at <= datetime.utcnow():
            raise ApiProblem(401, "expired_token", "Token 已过期，请在系统管理中轮换。")
        token.last_used_at = datetime.utcnow()
        now = time.monotonic()
        window = _rate_windows[token.id]
        while window and window[0] <= now - RATE_LIMIT_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= RATE_LIMIT_REQUESTS:
            raise ApiProblem(429, "rate_limit_exceeded", "调用频率过高，请稍后重试。")
        window.append(now)
        request.state.api_token_id = token.id
        session.flush()
        session.expunge(token)
        return token


def require_scope(scope: str):
    def dependency(token: ApiToken = Depends(authenticate_token)) -> ApiToken:
        if scope not in parse_scopes(token):
            raise ApiProblem(403, "insufficient_scope", f"当前 Token 缺少 {scope} 权限。")
        return token

    return dependency


def validation_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_secret_key(), salt="dynamic-project-validation-v1")


def validation_core(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in ("title", "status", "owner", "summary", "schema_version", "content", *PROJECT_METADATA_FIELDS) if key in payload}


def sign_validation(payload: dict[str, Any], *, partial: bool) -> str:
    return validation_serializer().dumps({"fingerprint": payload_fingerprint(validation_core(payload)), "partial": partial})


def verify_validation(payload: dict[str, Any], token: str, *, partial: bool) -> None:
    try:
        signed = validation_serializer().loads(token, max_age=VALIDATION_TOKEN_MAX_AGE_SECONDS)
    except SignatureExpired as exc:
        raise ApiProblem(409, "validation_expired", "校验令牌已过期，请重新调用校验接口。") from exc
    except BadSignature as exc:
        raise ApiProblem(400, "invalid_validation_token", "校验令牌无效。") from exc
    if bool(signed.get("partial")) != partial or signed.get("fingerprint") != payload_fingerprint(validation_core(payload)):
        raise ApiProblem(409, "payload_changed_after_validation", "项目内容在校验后发生变化，请重新校验并确认。")


def parse_json_body(payload: Any, *, partial: bool = False) -> dict[str, Any]:
    try:
        return validate_project_payload(payload, partial=partial)
    except SchemaValidationError as exc:
        raise ApiProblem(422, "schema_validation_failed", "动态项目内容未通过校验。", errors=exc.errors) from exc


async def read_json_object(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ApiProblem(400, "invalid_json", "请求体必须是有效的 JSON 对象。") from exc
    if not isinstance(value, dict):
        raise ApiProblem(400, "invalid_json", "请求体必须是 JSON 对象。")
    return value


def serialize_project(project: Project) -> dict[str, Any]:
    apply_auto_lifecycle(project)
    try:
        content = json.loads(project.dynamic_content or "{\"sections\":[]}")
    except json.JSONDecodeError:
        content = {"sections": []}
    return {
        "id": project.id,
        "title": project.name,
        "status": project.status,
        "owner": project.owner_name or "",
        "summary": project.summary or "",
        "tender_code": project.tender_code or "",
        "buyer": project.buyer or "",
        "agency": project.agency or "",
        "contact_name": project.contact_name or "",
        "contact_phone": project.contact_phone or "",
        "signup_deadline": project.signup_deadline.isoformat() if project.signup_deadline else None,
        "deposit_deadline": project.deposit_deadline.isoformat() if project.deposit_deadline else None,
        "submission_datetime": project.submission_datetime.isoformat() if project.submission_datetime else None,
        "bid_datetime": project.bid_datetime.isoformat() if project.bid_datetime else None,
        "schema_version": project.schema_version or SCHEMA_VERSION,
        "version": project.content_version or 1,
        "content": content,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "url": f"{get_public_base_url()}/projects/{project.id}",
    }


def parse_project_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise ApiProblem(422, "invalid_datetime", "Project date fields must use ISO-8601 datetime values.") from exc


def apply_project_metadata(project: Project, payload: dict[str, Any]) -> None:
    text_fields = ("tender_code", "buyer", "agency", "contact_name", "contact_phone")
    for field in text_fields:
        if field in payload:
            setattr(project, field, payload[field] or None)
    for field in ("signup_deadline", "deposit_deadline", "submission_datetime", "bid_datetime"):
        if field in payload:
            setattr(project, field, parse_project_datetime(payload[field]))


def audit_token_action(session, token: ApiToken, action: str, project: Project, detail: dict[str, Any], request_id: str) -> None:
    session.add(
        AuditLog(
            actor_name=f"Hermes: {token.name}",
            actor_role="api_token",
            action=action,
            entity_type="project",
            entity_id=str(project.id),
            project_name=project.name,
            detail=json.dumps({**detail, "request_id": request_id}, ensure_ascii=False),
        )
    )


def store_version(session, project: Project, token: ApiToken, request_id: str, change_summary: str | None) -> None:
    session.add(
        ProjectContentVersion(
            project_id=project.id,
            version=project.content_version or 1,
            schema_version=project.schema_version or SCHEMA_VERSION,
            title=project.name,
            summary=project.summary,
            content_json=project.dynamic_content or '{"sections":[]}',
            change_summary=change_summary,
            actor_name=f"Hermes: {token.name}",
            request_id=request_id,
        )
    )


def request_hash(payload: Any) -> str:
    return payload_fingerprint(payload)


def require_confirmation(value: Any, message: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ApiProblem(400, "confirmation_required", message)
    try:
        normalized = validate_project_payload({"confirmation": value}, partial=True)
    except SchemaValidationError as exc:
        raise ApiProblem(422, "invalid_confirmation", "用户确认信息格式无效。", errors=exc.errors) from exc
    return normalized["confirmation"]


def replay_idempotent(session, token: ApiToken, key: str, digest: str) -> JSONResponse | None:
    existing = (
        session.query(IdempotencyRecord)
        .filter(IdempotencyRecord.token_id == token.id, IdempotencyRecord.idempotency_key == key)
        .first()
    )
    if not existing:
        return None
    if existing.request_hash != digest:
        raise ApiProblem(409, "idempotency_conflict", "同一 Idempotency-Key 已用于不同请求。")
    return JSONResponse(status_code=existing.response_status, content=json.loads(existing.response_json), headers={"Idempotency-Replayed": "true"})


def save_idempotency(session, token: ApiToken, key: str, digest: str, status_code: int, response: dict[str, Any]) -> None:
    session.add(
        IdempotencyRecord(
            token_id=token.id,
            idempotency_key=key,
            request_hash=digest,
            response_status=status_code,
            response_json=json.dumps(response, ensure_ascii=False),
        )
    )


def require_idempotency_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key or len(key) > 128:
        raise ApiProblem(400, "invalid_idempotency_key", "写请求必须提供 1-128 字符的 Idempotency-Key。")
    return key


@router.get("/schema/project")
def get_project_schema(_: ApiToken = Depends(require_scope("projects:read"))):
    return project_schema_document()


@router.post("/validate/project", openapi_extra=request_body_example(PROJECT_REQUEST_EXAMPLE, "校验并规范化动态项目内容，不写入数据库。"))
async def validate_project(
    request: Request,
    partial: bool = Query(default=False),
    _: ApiToken = Depends(require_scope("projects:write")),
):
    payload = parse_json_body(await read_json_object(request), partial=partial)
    candidates: list[dict[str, Any]] = []
    title = payload.get("title", "")
    if title:
        with session_scope() as session:
            rows = session.query(Project).filter(Project.name.ilike(f"%{title}%")).limit(5).all()
            candidates = [{"id": row.id, "title": row.name, "status": row.status, "version": row.content_version or 1} for row in rows]
    return {
        "valid": True,
        "normalized": payload,
        "validation_token": sign_validation(payload, partial=partial),
        "validation_expires_in_seconds": VALIDATION_TOKEN_MAX_AGE_SECONDS,
        "duplicate_candidates": candidates,
    }


@router.get("/projects")
def list_projects(
    q: str = Query(default="", max_length=255),
    status: str = Query(default="", max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: ApiToken = Depends(require_scope("projects:read")),
):
    with session_scope() as session:
        query = session.query(Project)
        if q:
            query = query.filter(or_(Project.name.ilike(f"%{q}%"), Project.tender_code.ilike(f"%{q}%"), Project.owner_name.ilike(f"%{q}%"), Project.dynamic_content.ilike(f"%{q}%")))
        if status:
            query = query.filter(Project.status == status)
        total = query.count()
        rows = query.order_by(Project.updated_at.desc()).offset(offset).limit(limit).all()
        return {"items": [serialize_project(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/projects/{project_id}")
def get_project(project_id: int, _: ApiToken = Depends(require_scope("projects:read"))):
    with session_scope() as session:
        project = session.get(Project, project_id)
        if not project:
            raise ApiProblem(404, "project_not_found", "项目不存在。")
        return serialize_project(project)


@router.post("/projects", status_code=201, openapi_extra=request_body_example({**PROJECT_REQUEST_EXAMPLE, "validation_token": "<validate 接口返回值>", "confirmation": {"confirmed_by": "确认人", "confirmed_at": "2026-07-21T15:00:00+08:00", "summary": "确认创建项目"}}, "创建经用户确认的动态项目，必须提供 Idempotency-Key。"))
async def create_project(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    token: ApiToken = Depends(require_scope("projects:write")),
):
    raw_payload = await read_json_object(request)
    key = require_idempotency_key(idempotency_key)
    digest = request_hash(raw_payload)
    with session_scope() as session:
        replay = replay_idempotent(session, token, key, digest)
        if replay:
            return replay
        payload = parse_json_body(raw_payload)
        confirmation = payload.get("confirmation")
        validation_token = payload.get("validation_token")
        if not confirmation or not validation_token:
            raise ApiProblem(400, "confirmation_required", "正式写入必须提供 validation_token 和用户确认信息。")
        verify_validation(payload, validation_token, partial=False)
        project = Project(
            name=payload["title"],
            status=payload["status"],
            owner_name=payload.get("owner") or None,
            summary=payload.get("summary") or None,
            dynamic_content=json.dumps(payload["content"], ensure_ascii=False),
            schema_version=payload["schema_version"],
            content_version=1,
        )
        apply_project_metadata(project, payload)
        session.add(project)
        session.flush()
        request_id = request.state.request_id
        store_version(session, project, token, request_id, payload.get("change_summary") or confirmation["summary"])
        audit_token_action(session, token, "api_create_project", project, {"confirmation": confirmation}, request_id)
        response = {"project": serialize_project(project), "created": True, "request_id": request_id}
        save_idempotency(session, token, key, digest, 201, response)
        return JSONResponse(status_code=201, content=response)


@router.patch("/projects/{project_id}", openapi_extra=request_body_example({"summary": "更新后的摘要", "validation_token": "<partial validate 返回值>", "confirmation": {"confirmed_by": "确认人", "confirmed_at": "2026-07-21T15:05:00+08:00", "summary": "确认更新摘要"}}, "局部更新项目，必须提供 Idempotency-Key 和 If-Match。"))
async def update_project(
    project_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    token: ApiToken = Depends(require_scope("projects:write")),
):
    raw_payload = await read_json_object(request)
    key = require_idempotency_key(idempotency_key)
    digest = request_hash({"project_id": project_id, "payload": raw_payload, "if_match": if_match})
    with session_scope() as session:
        replay = replay_idempotent(session, token, key, digest)
        if replay:
            return replay
        project = session.get(Project, project_id)
        if not project:
            raise ApiProblem(404, "project_not_found", "项目不存在。")
        try:
            expected_version = int((if_match or "").strip().strip('W/"'))
        except ValueError as exc:
            raise ApiProblem(428, "version_required", "更新必须通过 If-Match 提供当前项目版本。") from exc
        if expected_version != (project.content_version or 1):
            raise ApiProblem(409, "version_conflict", "项目已被其他操作更新，请重新读取后再提交。")
        patch = parse_json_body(raw_payload, partial=True)
        confirmation = patch.get("confirmation")
        validation_token = patch.get("validation_token")
        if not confirmation or not validation_token:
            raise ApiProblem(400, "confirmation_required", "正式更新必须提供 validation_token 和用户确认信息。")
        verify_validation(patch, validation_token, partial=True)
        current = serialize_project(project)
        merged = merge_project_payload(current, patch)
        full = parse_json_body(merged)
        project.name = full["title"]
        project.status = full["status"]
        project.owner_name = full.get("owner") or None
        project.summary = full.get("summary") or None
        apply_project_metadata(project, full)
        project.dynamic_content = json.dumps(full["content"], ensure_ascii=False)
        project.schema_version = full["schema_version"]
        project.content_version = (project.content_version or 1) + 1
        project.updated_at = datetime.utcnow()
        request_id = request.state.request_id
        store_version(session, project, token, request_id, patch.get("change_summary") or confirmation["summary"])
        audit_token_action(session, token, "api_update_project", project, {"confirmation": confirmation, "from_version": expected_version}, request_id)
        response = {"project": serialize_project(project), "updated": True, "request_id": request_id}
        save_idempotency(session, token, key, digest, 200, response)
        return response


@router.post("/projects/{project_id}/followups", status_code=201, openapi_extra=request_body_example({"content": "已完成资格文件核对。", "confirmation": {"confirmed_by": "确认人", "confirmed_at": "2026-07-21T15:10:00+08:00", "summary": "确认追加跟进"}}, "追加经用户确认的项目跟进。"))
async def add_followup(
    project_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    token: ApiToken = Depends(require_scope("projects:write")),
):
    body = await read_json_object(request)
    key = require_idempotency_key(idempotency_key)
    content = str(body.get("content") or "").strip()
    confirmation = require_confirmation(body.get("confirmation"), "追加跟进前必须提供用户确认信息。")
    if not content or len(content) > 10_000:
        raise ApiProblem(422, "invalid_followup", "跟进内容不能为空且不能超过 10000 字符。")
    digest = request_hash({"project_id": project_id, "body": body})
    with session_scope() as session:
        replay = replay_idempotent(session, token, key, digest)
        if replay:
            return replay
        project = session.get(Project, project_id)
        if not project:
            raise ApiProblem(404, "project_not_found", "项目不存在。")
        followup = ProjectFollowup(project_id=project.id, content=content, created_by=f"Hermes: {token.name}")
        session.add(followup)
        session.flush()
        audit_token_action(session, token, "api_add_followup", project, {"confirmation": confirmation}, request.state.request_id)
        response = {"id": followup.id, "project_id": project.id, "content": content, "request_id": request.state.request_id}
        save_idempotency(session, token, key, digest, 201, response)
        return JSONResponse(status_code=201, content=response)


@router.post("/projects/{project_id}/status", openapi_extra=request_body_example({"status": "result_pending", "confirmation": {"confirmed_by": "确认人", "confirmed_at": "2026-07-21T15:15:00+08:00", "summary": "确认更新项目状态"}}, "更新项目生命周期状态。"))
async def update_status(
    project_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    token: ApiToken = Depends(require_scope("projects:write")),
):
    body = await read_json_object(request)
    key = require_idempotency_key(idempotency_key)
    status = str(body.get("status") or "").strip()
    confirmation = require_confirmation(body.get("confirmation"), "更新状态前必须提供用户确认信息。")
    if status not in PROJECT_STATUSES:
        raise ApiProblem(422, "invalid_status", "项目状态无效。")
    digest = request_hash({"project_id": project_id, "body": body})
    with session_scope() as session:
        replay = replay_idempotent(session, token, key, digest)
        if replay:
            return replay
        project = session.get(Project, project_id)
        if not project:
            raise ApiProblem(404, "project_not_found", "项目不存在。")
        previous = project.status
        project.status = status
        project.content_version = (project.content_version or 1) + 1
        project.updated_at = datetime.utcnow()
        store_version(session, project, token, request.state.request_id, confirmation.get("summary") or f"状态从 {previous} 更新为 {status}")
        audit_token_action(session, token, "api_update_status", project, {"from": previous, "to": status, "confirmation": confirmation}, request.state.request_id)
        response = {"project": serialize_project(project), "request_id": request.state.request_id}
        save_idempotency(session, token, key, digest, 200, response)
        return response


@router.post("/projects/{project_id}/archive", openapi_extra=request_body_example({"final_status": "archived", "confirmation": {"confirmed_by": "确认人", "confirmed_at": "2026-07-21T15:20:00+08:00", "summary": "确认归档项目"}}, "归档项目并清理详细项目数据，需要 projects:archive Scope。"))
async def archive_project_api(
    project_id: int,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    token: ApiToken = Depends(require_scope("projects:archive")),
):
    body = await read_json_object(request)
    key = require_idempotency_key(idempotency_key)
    final_status = str(body.get("final_status") or "archived").strip()
    confirmation = require_confirmation(body.get("confirmation"), "归档前必须提供用户确认信息。")
    if final_status not in TERMINAL_STATUSES:
        raise ApiProblem(422, "invalid_final_status", "归档状态必须是终态。")
    digest = request_hash({"project_id": project_id, "body": body})
    with session_scope() as session:
        replay = replay_idempotent(session, token, key, digest)
        if replay:
            return replay
        project = session.get(Project, project_id)
        if not project:
            raise ApiProblem(404, "project_not_found", "项目不存在。")
        audit_token_action(session, token, "api_archive_project", project, {"final_status": final_status, "confirmation": confirmation}, request.state.request_id)
        project_name = project.name
        archived = archive_project_data(session, project, final_status)
        response = {
            "archive": {
                "id": archived.id,
                "original_project_id": project_id,
                "project_name": project_name,
                "final_status": final_status,
                "archived_at": archived.archived_at.isoformat() if archived.archived_at else None,
            },
            "archived": True,
            "request_id": request.state.request_id,
        }
        save_idempotency(session, token, key, digest, 200, response)
        return response
