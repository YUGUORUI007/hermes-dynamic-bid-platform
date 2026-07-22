from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    ROLE_LABELS,
    can_configure_system,
    can_edit_projects,
    can_manage_users,
    ensure_admin_user,
    get_current_user,
    hash_password,
    normalize_role,
    verify_password,
)
from .config import INSTANCE_DIR, PROJECT_STORAGE_DIR, STATIC_DIR, STORAGE_DIR, TEMPLATES_DIR, TMP_DIR, get_secret_key, legacy_ai_routes_enabled
from .database import Base, engine, session_scope
from .models import (
    ApiToken,
    ArchivedProject,
    AuditLog,
    ExtractionField,
    ExtractionJob,
    Project,
    ProjectFile,
    ProjectFollowup,
    ProjectMessage,
    ProjectMilestone,
    ProjectRequirement,
    ProjectContentVersion,
    ReminderState,
    SystemSetting,
    User,
)
from .services.ledger_import import read_ledger_rows
from .services.project_archive import archive_project_data
from .services.dynamic_ui import build_archive_data, build_calendar_data, build_workspace_data, serialize_project_detail
from .services.ai_pipeline import (
    ANSWER_MODE_LABELS,
    FIELD_LABELS,
    QUESTION_ANSWER_STATUS_LABELS,
    REQUIREMENT_CATEGORY_LABELS,
    ai_extract_fields,
    answer_project_question,
    build_project_context,
    extract_document_text,
    format_datetime,
    get_runtime_ai_mode_label,
    get_runtime_ai_settings,
    get_runtime_ocr_settings,
    parse_question_answer_metadata,
    ping_deepseek,
    repair_mojibake_text,
)
from .api_v1 import ApiProblem, create_api_token, error_response as api_error_response, router as api_v1_router
from .dynamic_schema import SchemaValidationError, validate_project_payload


jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


STATUS_LABELS = {
    "tracking": "待跟进",
    "pending_signup": "待报名",
    "registered": "已报名",
    "deposit_pending": "待缴保证金",
    "deposit_done": "保证金已汇出",
    "preparing": "待制作投标方案",
    "sealed": "标书已制作并封标",
    "ready_deliver": "待送标",
    "submitted": "已递交",
    "result_pending": "待结果",
    "won": "已中标",
    "lost": "未中标",
    "abandoned": "放弃投标",
    "partner_completed": "陪标完成",
    "archived": "已归档",
}

ACTIVE_PROJECT_STATUSES = {"tracking", "pending_signup", "registered", "deposit_pending", "deposit_done", "preparing", "sealed", "ready_deliver", "submitted", "result_pending"}
TERMINAL_PROJECT_STATUSES = {"won", "lost", "abandoned", "partner_completed", "archived"}
PROJECT_STATUS_FLOW = ("tracking", "pending_signup", "registered", "deposit_pending", "deposit_done", "preparing", "sealed", "ready_deliver", "submitted", "result_pending")
STATUS_TONES = {
    "tracking": "warning", "pending_signup": "warning", "registered": "success", "deposit_pending": "warning",
    "deposit_done": "info", "preparing": "warning", "sealed": "success", "ready_deliver": "danger",
    "submitted": "info", "result_pending": "success", "won": "success", "lost": "danger",
    "abandoned": "neutral", "partner_completed": "info", "archived": "neutral",
}

SETTINGS_LABELS = {
    "deepseek_api_key": "DeepSeek API Key",
    "paddleocr_api_url": "PaddleOCR 接口地址",
    "paddleocr_access_token": "PaddleOCR Access Token",
    "paddleocr_timeout_seconds": "PaddleOCR 超时时间",
}

BID_MODE_LABELS = {
    "self": "自投",
    "partner": "陪标",
}

MILESTONE_TYPE_LABELS = {
    "document_sale": "文件购买",
    "signup": "报名",
    "deposit": "保证金",
    "clarification": "澄清",
    "site_visit": "踏勘",
    "submission": "递交",
    "bid_open": "开标",
    "result": "结果",
    "custom": "自定义",
}

MILESTONE_STATUS_LABELS = {
    "pending": "未开始",
    "in_progress": "进行中",
    "done": "已完成",
    "overdue": "已过期",
}

WORKSPACE_PATH = "/workspace"
WORKSPACE_PROJECTS_PATH = "/workspace/projects"
WORKSPACE_REVIEWS_PATH = "/workspace/reviews"
WORKSPACE_ARCHIVES_PATH = "/workspace/archives"
WORKSPACE_SETTINGS_PATH = "/workspace/settings"
WORKSPACE_CALENDAR_PATH = "/workspace/calendar"
PUBLIC_HOME_PATH = "/"

EXECUTION_MILESTONE_ORDER = (
    "signup",
    "deposit",
    "submission",
    "bid_open",
    "result",
    "document_sale",
    "clarification",
    "site_visit",
)

EXECUTION_STATUS_LABELS = {
    "signup": {
        "pending": "未报名",
        "in_progress": "报名中",
        "done": "已报名",
        "overdue": "报名逾期",
    },
    "deposit": {
        "pending": "未汇出",
        "in_progress": "汇款中",
        "done": "已汇出",
        "overdue": "保证金逾期",
    },
    "submission": {
        "pending": "未递交",
        "in_progress": "递交中",
        "done": "已递交",
        "overdue": "递交逾期",
    },
    "bid_open": {
        "pending": "待开标",
        "in_progress": "开标中",
        "done": "已开标",
        "overdue": "开标已过",
    },
    "result": {
        "pending": "待结果",
        "in_progress": "跟进中",
        "done": "已落实",
        "overdue": "结果未跟进",
    },
    "document_sale": {
        "pending": "未购买",
        "in_progress": "购买中",
        "done": "已购买",
        "overdue": "购买逾期",
    },
    "clarification": {
        "pending": "未处理",
        "in_progress": "处理中",
        "done": "已处理",
        "overdue": "澄清逾期",
    },
    "site_visit": {
        "pending": "未踏勘",
        "in_progress": "踏勘中",
        "done": "已踏勘",
        "overdue": "踏勘已过",
    },
}

WORKBOARD_STAGE_LABELS = {
    "pending_intake": "待识别",
    "pending_signup": "待报名",
    "preparing_bid": "标书制作中",
    "pending_deposit": "待缴保证金",
    "pending_submission": "待递交",
    "submitted": "已递交",
    "pending_result": "待结果",
}

REQUIREMENT_IMPORTANCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

PROJECT_SCHEMA_UPDATES: list[tuple[str, str, str]] = [
    ("projects", "summary", "ALTER TABLE projects ADD COLUMN summary TEXT"),
    ("projects", "dynamic_content", "ALTER TABLE projects ADD COLUMN dynamic_content TEXT"),
    ("projects", "schema_version", "ALTER TABLE projects ADD COLUMN schema_version VARCHAR(16) DEFAULT '1.0'"),
    ("projects", "content_version", "ALTER TABLE projects ADD COLUMN content_version INTEGER DEFAULT 1"),
    ("projects", "buyer", "ALTER TABLE projects ADD COLUMN buyer VARCHAR(255)"),
    ("projects", "project_type", "ALTER TABLE projects ADD COLUMN project_type VARCHAR(128)"),
    ("projects", "service_scope", "ALTER TABLE projects ADD COLUMN service_scope TEXT"),
    ("projects", "budget_amount", "ALTER TABLE projects ADD COLUMN budget_amount VARCHAR(128)"),
    ("projects", "document_sale_deadline", "ALTER TABLE projects ADD COLUMN document_sale_deadline DATETIME"),
    ("projects", "clarification_deadline", "ALTER TABLE projects ADD COLUMN clarification_deadline DATETIME"),
    ("projects", "site_visit_time", "ALTER TABLE projects ADD COLUMN site_visit_time DATETIME"),
    ("projects", "bid_location", "ALTER TABLE projects ADD COLUMN bid_location VARCHAR(255)"),
    ("projects", "file_fee", "ALTER TABLE projects ADD COLUMN file_fee VARCHAR(128)"),
    ("projects", "payment_info", "ALTER TABLE projects ADD COLUMN payment_info TEXT"),
    ("projects", "bid_document_status", "ALTER TABLE projects ADD COLUMN bid_document_status VARCHAR(64)"),
    ("projects", "has_defense", "ALTER TABLE projects ADD COLUMN has_defense VARCHAR(32)"),
    ("projects", "defense_presenter", "ALTER TABLE projects ADD COLUMN defense_presenter VARCHAR(128)"),
    ("projects", "invalidation_risks", "ALTER TABLE projects ADD COLUMN invalidation_risks TEXT"),
    ("projects", "submission_notes", "ALTER TABLE projects ADD COLUMN submission_notes TEXT"),
    ("projects", "seal_notes", "ALTER TABLE projects ADD COLUMN seal_notes TEXT"),
]

BID_DOCUMENT_STATUS_LABELS = {
    "not_started": "未编制",
    "drafting": "编制中",
    "reviewing": "审核中",
    "ready": "已编制",
}

DEFENSE_OPTION_LABELS = {
    "unknown": "待确认",
    "yes": "需要答辩",
    "no": "无需答辩",
}

AUDIT_ACTION_LABELS = {
    "login": "登录系统",
    "logout": "退出系统",
    "upload_document": "上传招标文件",
    "confirm_review": "确认解析结果",
    "reject_review": "驳回解析结果",
    "create_project": "创建项目",
    "edit_project": "编辑项目",
    "update_project_status": "更新项目状态",
    "archive_project": "归档项目",
    "ask_project": "项目问答",
    "create_milestone": "新增节点",
    "edit_milestone": "编辑节点",
    "create_followup": "新增跟进",
    "mark_reminder_done": "完成提醒",
    "create_user": "创建用户",
    "edit_user": "编辑用户",
    "update_deepseek": "更新 DeepSeek 配置",
    "update_paddleocr": "更新 OCR 配置",
}

REVIEW_FIELD_QUEUE_LABELS = {
    "source_ready": "有来源，可入库",
    "manual": "人工填写",
    "source_missing": "缺少来源，确认时将跳过",
    "empty": "未提取到值",
}

REVIEW_FIELD_STATUS_LABELS = {
    "pending_review": "待确认",
    "confirmed": "已按原文入库",
    "manual": "人工填写",
    "skipped_no_source": "未入库（缺少来源）",
    "empty": "未写入",
    "rejected": "已驳回",
}

PROJECT_FILE_KIND_LABELS = {
    "intake": "初始招标文件",
    "supplement": "补充文件",
    "project": "项目资料",
}

PROJECT_FILE_STATE_LABELS = {
    "active": "可供 AI 查阅",
    "deleted": "已清理",
}

BASIS_ITEM_KIND_LABELS = {
    "quote": "原文摘录",
    "structured": "结构化要点",
    "history": "历史来源",
}

PROJECT_TEXT_FIELD_ATTRS = {
    "name": "name",
    "short_name": "short_name",
    "tender_code": "tender_code",
    "buyer": "buyer",
    "project_type": "project_type",
    "bid_mode": "bid_mode",
    "owner_name": "owner_name",
    "agency": "agency",
    "contact_name": "contact_name",
    "contact_phone": "contact_phone",
    "location": "location",
    "service_scope": "service_scope",
    "contract_term": "contract_term",
    "budget_amount": "budget_amount",
    "bid_location": "bid_location",
    "file_fee": "file_fee",
    "payment_info": "payment_info",
    "bid_document_status": "bid_document_status",
    "has_defense": "has_defense",
    "defense_presenter": "defense_presenter",
    "invalidation_risks": "invalidation_risks",
    "submission_notes": "submission_notes",
    "seal_notes": "seal_notes",
    "notes": "notes",
}

PROJECT_DATETIME_FIELD_ATTRS = {
    "signup_deadline": "signup_deadline",
    "document_sale_deadline": "document_sale_deadline",
    "clarification_deadline": "clarification_deadline",
    "site_visit_time": "site_visit_time",
    "deposit_deadline": "deposit_deadline",
    "bid_datetime": "bid_datetime",
    "submission_datetime": "submission_datetime",
}

REVIEW_UPDATE_PROTECTED_FIELDS = {
    "name",
    "short_name",
    "tender_code",
    "buyer",
    "project_type",
    "bid_mode",
    "owner_name",
    "notes",
}


def ensure_directories() -> None:
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    PROJECT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def ensure_schema_updates() -> None:
    with engine.begin() as connection:
        inspector = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        existing_tables = {row[0] for row in inspector}
        for table_name, column_name, ddl in PROJECT_SCHEMA_UPDATES:
            if table_name not in existing_tables:
                continue
            columns = connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
            existing_columns = {row[1] for row in columns}
            if column_name in existing_columns:
                continue
            connection.exec_driver_sql(ddl)


def build_project_subdirs(project_id: int | str) -> dict[str, Path]:
    root = PROJECT_STORAGE_DIR / str(project_id)
    return {
        "root": root,
        "source": root / "source",
        "text": root / "text",
        "parse": root / "parse",
    }


def build_upload_storage_name(file_name: str) -> str:
    safe_name = Path(file_name or "upload").name or "upload"
    return f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(4)}-{safe_name}"


def build_cache_artifact_paths(root: Path, cache_stem: str) -> dict[str, Path]:
    safe_stem = Path(cache_stem).stem or "document"
    return {
        "text_path": root / "text" / f"{safe_stem}.txt",
        "parse_path": root / "parse" / f"{safe_stem}.json",
        "summary_path": root / "parse" / f"{safe_stem}.summary.txt",
    }


def write_project_cache_files(
    base_id: int | str,
    file_name: str,
    extracted_text: str,
    parsed_payload: dict[str, object],
    summary: str,
    *,
    cache_stem: str | None = None,
) -> dict[str, str]:
    dirs = build_project_subdirs(base_id)
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    cache_paths = build_cache_artifact_paths(dirs["root"], cache_stem or file_name)
    text_path = cache_paths["text_path"]
    parse_path = cache_paths["parse_path"]
    summary_path = cache_paths["summary_path"]

    text_path.write_text(extracted_text, encoding="utf-8")
    parse_path.write_text(json.dumps(parsed_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(summary, encoding="utf-8")

    return {
        "text_path": str(text_path),
        "parse_path": str(parse_path),
        "summary_path": str(summary_path),
    }


def add_audit_log(
    session,
    *,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: object | None = None,
    project_name: str | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_name=actor.display_name if actor else None,
            actor_role=actor.role if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            project_name=project_name,
            detail=detail,
        )
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_template(name: str, request: Request, **context: object) -> HTMLResponse:
    user = get_current_user(request)
    template = jinja_env.get_template(name)
    app_section = context.pop("app_section", "")
    html = template.render(
        request=request,
        current_user=user,
        app_section=app_section,
        status_labels=STATUS_LABELS,
        status_tones=STATUS_TONES,
        active_project_statuses=ACTIVE_PROJECT_STATUSES,
        terminal_project_statuses=TERMINAL_PROJECT_STATUSES,
        project_status_flow=PROJECT_STATUS_FLOW,
        settings_labels=SETTINGS_LABELS,
        role_labels=ROLE_LABELS,
        bid_mode_labels=BID_MODE_LABELS,
        bid_document_status_labels=BID_DOCUMENT_STATUS_LABELS,
        defense_option_labels=DEFENSE_OPTION_LABELS,
        milestone_type_labels=MILESTONE_TYPE_LABELS,
        milestone_status_labels=MILESTONE_STATUS_LABELS,
        requirement_category_labels=REQUIREMENT_CATEGORY_LABELS,
        requirement_importance_labels=REQUIREMENT_IMPORTANCE_LABELS,
        audit_action_labels=AUDIT_ACTION_LABELS,
        can_edit_projects=can_edit_projects,
        can_manage_users=can_manage_users,
        can_configure_system=can_configure_system,
        get_milestone_status_label=get_milestone_status_label,
        format_datetime=format_datetime,
        public_home_path=PUBLIC_HOME_PATH,
        workspace_path=WORKSPACE_PATH,
        workspace_projects_path=WORKSPACE_PROJECTS_PATH,
        workspace_reviews_path=WORKSPACE_REVIEWS_PATH,
        workspace_archives_path=WORKSPACE_ARCHIVES_PATH,
        workspace_settings_path=WORKSPACE_SETTINGS_PATH,
        **context,
    )
    return HTMLResponse(html)


def build_preview_text(text: str | None, limit: int = 12000) -> tuple[str, bool]:
    if not text:
        return "", False
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized, False
    return normalized[:limit].rstrip() + "\n\n[已截断，更多内容请下载源文件核对]", True


def normalize_public_text(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(repair_mojibake_text(text).split()).strip()


def build_public_text_excerpt(text: str | None, *, fallback: str, limit: int = 30) -> str:
    normalized = normalize_public_text(text).strip("，,；;、 ")
    if not normalized:
        return fallback
    normalized = normalized.replace("；", "，").replace(";", "，")
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip("，,；;、 ") + "…"


def build_public_display_text(text: str | None, *, fallback: str) -> str:
    normalized = normalize_public_text(text)
    if not normalized:
        return fallback
    unclear_count = sum(1 for char in normalized if char in {"?", "？"})
    meaningful_count = sum(1 for char in normalized if char not in {"?", "？", "-", "_", "/", "|", ".", "·", " "})
    if meaningful_count == 0 or unclear_count >= max(2, len(normalized) // 2):
        return fallback
    cleaned = re.sub(r"[?？]{2,}", "待补录", normalized).strip()
    return cleaned or fallback


def has_meaningful_public_text(text: str | None) -> bool:
    return build_public_display_text(text, fallback="") != ""


def build_public_requirement_item(item: ProjectRequirement) -> dict[str, str]:
    return {
        "title": build_public_display_text(item.title, fallback="条款标题待补录"),
        "content": build_public_display_text(item.content, fallback="条款内容待补录"),
        "source_location": build_public_display_text(item.source_location, fallback=""),
    }


def build_public_requirement_items(items: list[ProjectRequirement], limit: int = 6) -> list[dict[str, str]]:
    public_items: list[dict[str, str]] = []
    for item in items:
        if not has_meaningful_public_text(item.title) and not has_meaningful_public_text(item.content):
            continue
        public_items.append(build_public_requirement_item(item))
        if len(public_items) >= limit:
            break
    return public_items


def get_public_status_pill_class(status: str | None) -> str:
    normalized = (status or "").strip()
    if normalized in {"done", "ready", "no"}:
        return "success"
    if normalized in {"in_progress", "drafting", "reviewing", "yes"}:
        return "warning"
    if normalized == "overdue":
        return "danger"
    return "pending"


def build_archive_display_name(project_name: str | None) -> str:
    return build_public_display_text(project_name, fallback="项目名称待补录")


def get_review_field_value(field: ExtractionField) -> str:
    return (field.final_value or field.extracted_value or "").strip()


def normalize_project_file_source_kind(source_kind: str | None) -> str:
    candidate = (source_kind or "").strip()
    if candidate in {"intake", "supplement"}:
        return candidate
    return "upload"


def review_field_has_source(field: ExtractionField) -> bool:
    return bool((field.source_location or "").strip() or (field.source_quote or "").strip())


def get_review_field_queue_status(field: ExtractionField) -> str:
    value = get_review_field_value(field)
    if not value:
        return "empty"
    if field.status == "manual":
        return "manual"
    if review_field_has_source(field):
        return "source_ready"
    return "source_missing"


def annotate_review_queue_fields(fields: list[ExtractionField]) -> dict[str, int]:
    stats = {"source_ready": 0, "manual": 0, "source_missing": 0, "empty": 0}
    for field in fields:
        queue_status = get_review_field_queue_status(field)
        field.review_queue_status = queue_status
        field.review_queue_label = REVIEW_FIELD_QUEUE_LABELS.get(queue_status, queue_status)
        field.review_has_source = review_field_has_source(field)
        field.review_value = get_review_field_value(field)
        stats[queue_status] = stats.get(queue_status, 0) + 1
    return stats


def annotate_confirmed_review_fields(fields: list[ExtractionField]) -> list[ExtractionField]:
    for field in fields:
        stored_status = field.status if field.status in REVIEW_FIELD_STATUS_LABELS else "pending_review"
        field.audit_status_label = REVIEW_FIELD_STATUS_LABELS.get(stored_status, stored_status)
        field.audit_value = get_review_field_value(field)
    return fields


def derive_project_file_kind(file_record: ProjectFile, oldest_file_id: int | None, total_files: int) -> str:
    normalized_kind = normalize_project_file_source_kind(file_record.source_kind)
    if normalized_kind in PROJECT_FILE_KIND_LABELS:
        return normalized_kind
    if total_files <= 1:
        return "project"
    if oldest_file_id is not None and file_record.id == oldest_file_id:
        return "intake"
    return "supplement"


def serialize_project_file_entries(files: list[ProjectFile], project_id: int) -> list[dict[str, object]]:
    if not files:
        return []

    oldest_file = min(files, key=lambda item: (item.created_at or datetime.min, item.id or 0))
    oldest_file_id = oldest_file.id
    total_files = len(files)
    entries: list[dict[str, object]] = []

    for file_record in files:
        kind = derive_project_file_kind(file_record, oldest_file_id, total_files)
        state = "deleted" if file_record.deleted_at else "active"
        summary = repair_mojibake_text((file_record.extracted_summary or "").strip())
        entries.append(
            {
                "id": file_record.id,
                "original_name": file_record.original_name,
                "size_kb": round((file_record.file_size or 0) / 1024, 1),
                "created_at": file_record.created_at,
                "created_by": file_record.created_by,
                "summary": summary,
                "kind": kind,
                "kind_label": PROJECT_FILE_KIND_LABELS.get(kind, kind),
                "state": state,
                "state_label": PROJECT_FILE_STATE_LABELS.get(state, state),
                "download_url": None if file_record.deleted_at else f"/projects/{project_id}/files/{file_record.id}",
            }
        )

    return entries


def build_project_file_lookup(files: list[ProjectFile]) -> dict[str, ProjectFile]:
    lookup: dict[str, ProjectFile] = {}
    for file_record in files:
        current = lookup.get(file_record.original_name)
        if current is None:
            lookup[file_record.original_name] = file_record
            continue
        if current.deleted_at and not file_record.deleted_at:
            lookup[file_record.original_name] = file_record
    return lookup


def classify_basis_item_kind(item: dict[str, str]) -> str:
    source_location = item.get("source_location", "")
    explanation = item.get("explanation", "")
    if source_location == "历史来源说明":
        return "history"
    if "结构化" in source_location or "结构化" in explanation:
        return "structured"
    return "quote"


def enrich_basis_items(
    basis_items: list[dict[str, str]],
    file_lookup: dict[str, ProjectFile],
    project_id: int,
) -> list[dict[str, str]]:
    enriched: list[dict[str, str]] = []
    for item in basis_items:
        file_name = item.get("file_name", "")
        file_record = file_lookup.get(file_name) if file_name else None
        basis_kind = classify_basis_item_kind(item)
        source_quote = repair_mojibake_text((item.get("source_quote", "") or "").strip())
        explanation = repair_mojibake_text((item.get("explanation", "") or "").strip())
        enriched.append(
            {
                "file_name": file_name,
                "source_location": item.get("source_location", ""),
                "source_quote": source_quote,
                "explanation": explanation,
                "download_url": "" if file_record is None or file_record.deleted_at else f"/projects/{project_id}/files/{file_record.id}",
                "basis_kind": basis_kind,
                "basis_kind_label": BASIS_ITEM_KIND_LABELS.get(basis_kind, basis_kind),
            }
        )
    return enriched


def serialize_pending_review_entries(pending_reviews: list[ExtractionJob], project_id: int | None = None) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for job in pending_reviews:
        file_record = job.project_file
        raw_kind = normalize_project_file_source_kind(getattr(file_record, "source_kind", None))
        if raw_kind == "upload":
            if project_id is None:
                raw_kind = "supplement" if job.matched_project_id or job.project_id else "intake"
            else:
                raw_kind = "supplement" if job.matched_project_id == project_id or job.project_id == project_id else "intake"
        entries.append(
            {
                "id": job.id,
                "file_name": file_record.original_name if file_record else "待确认文件",
                "created_at": job.created_at,
                "created_by": job.created_by,
                "summary": repair_mojibake_text((job.confidence_summary or "").strip()),
                "kind": raw_kind,
                "kind_label": PROJECT_FILE_KIND_LABELS.get(raw_kind, PROJECT_FILE_KIND_LABELS["project"]),
                "status_label": "待确认",
                "ai_mode_label": repair_mojibake_text((job.ai_model or "").strip()) or "未标记",
                "matched_project_id": job.matched_project_id,
                "review_url": f"/reviews/{job.id}",
            }
        )
    return entries


def requirement_has_source(item: dict[str, object]) -> bool:
    return bool(str(item.get("source_location", "") or "").strip() or str(item.get("source_quote", "") or "").strip())


def apply_confirmed_review_data(project: Project, approved_data: dict[str, str]) -> None:
    for field_key, attr_name in PROJECT_TEXT_FIELD_ATTRS.items():
        value = approved_data.get(field_key, "").strip()
        if not value:
            continue
        if field_key == "bid_mode" and value not in BID_MODE_LABELS:
            continue
        setattr(project, attr_name, value)

    for field_key, attr_name in PROJECT_DATETIME_FIELD_ATTRS.items():
        value = approved_data.get(field_key, "").strip()
        if not value:
            continue
        setattr(project, attr_name, to_dt(value))

    deposit_amount = approved_data.get("deposit_amount", "").strip()
    if deposit_amount:
        try:
            project.deposit_amount = float(deposit_amount)
        except ValueError as exc:
            raise ValueError("保证金金额必须是数字。") from exc

    if not project.name:
        project.name = "未命名项目"
    if not project.short_name:
        project.short_name = project.name[:18]
    if not project.owner_name:
        project.owner_name = "待分配"
    if project.bid_mode not in BID_MODE_LABELS:
        project.bid_mode = "self"
    project.updated_at = datetime.utcnow()


def sanitize_review_update_data(project: Project, approved_data: dict[str, str], fields: list[ExtractionField]) -> dict[str, str]:
    sanitized = dict(approved_data)
    field_map = {field.field_key: field for field in fields}

    for field_key in REVIEW_UPDATE_PROTECTED_FIELDS:
        if field_key not in sanitized:
            continue
        current_value = str(getattr(project, PROJECT_TEXT_FIELD_ATTRS.get(field_key, field_key), "") or "").strip()
        if not current_value:
            continue
        field = field_map.get(field_key)
        is_manual_override = field is not None and field.status == "manual"
        if is_manual_override:
            continue
        incoming_value = sanitized.get(field_key, "").strip()
        if not incoming_value or incoming_value == current_value:
            continue
        sanitized.pop(field_key, None)

    return sanitized


def build_message_redirect_url(path: str, message: str | None = None, *, field: str = "notice", fragment: str | None = None) -> str:
    target = path
    if message:
        separator = "&" if "?" in target else "?"
        target = f"{target}{separator}{field}={quote(message)}"
    if fragment:
        target = f"{target}#{fragment.lstrip('#')}"
    return target


def redirect_with_message(path: str, message: str, *, field: str = "notice", fragment: str | None = None) -> RedirectResponse:
    return RedirectResponse(build_message_redirect_url(path, message, field=field, fragment=fragment), status_code=302)


def request_wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    requested_with = request.headers.get("x-requested-with", "")
    return "application/json" in accept.lower() or requested_with.lower() == "fetch"


def upload_success_response(request: Request, redirect_url: str, message: str) -> JSONResponse | RedirectResponse:
    if request_wants_json(request):
        return JSONResponse({"ok": True, "message": message, "redirect_url": redirect_url})
    return RedirectResponse(redirect_url, status_code=302)


def upload_error_response(request: Request, fallback_path: str, message: str) -> JSONResponse | RedirectResponse:
    cleaned_message = message.strip() or "解析失败，请稍后重试。"
    if request_wants_json(request):
        return JSONResponse({"ok": False, "error": cleaned_message}, status_code=400)
    return redirect_with_message(fallback_path, cleaned_message, field="error")


def format_upload_exception(exc: Exception) -> str:
    detail = str(exc).strip()
    if not detail:
        detail = exc.__class__.__name__
    return f"解析失败：{detail[:500]}"


def redirect_request_back_with_message(
    request: Request,
    message: str,
    *,
    field: str = "error",
    fallback_path: str = WORKSPACE_PROJECTS_PATH,
) -> RedirectResponse:
    referer = request.headers.get("referer", "").strip()
    target = fallback_path
    fragment: str | None = None
    if referer:
        parsed = urlsplit(referer)
        same_origin = not parsed.netloc or parsed.netloc == request.url.netloc
        candidate_path = parsed.path or fallback_path
        if same_origin and candidate_path and not (request.method == "GET" and candidate_path == request.url.path):
            target = candidate_path
            if parsed.query:
                target = f"{target}?{parsed.query}"
            fragment = parsed.fragment or None
    return RedirectResponse(build_message_redirect_url(target, message, field=field, fragment=fragment), status_code=302)


def redirect_home_with_notice(message: str) -> RedirectResponse:
    return redirect_with_message(WORKSPACE_PROJECTS_PATH, message)


def redirect_archives_with_notice(message: str) -> RedirectResponse:
    return redirect_with_message(WORKSPACE_ARCHIVES_PATH, message)


def redirect_settings_with_notice(message: str, *, fragment: str | None = None) -> RedirectResponse:
    return redirect_with_message(WORKSPACE_SETTINGS_PATH, message, fragment=fragment)


def build_archive_notice(project_name: str, final_status: str) -> str:
    status_label = STATUS_LABELS.get(final_status, final_status)
    return f"{project_name} 已转入已投项目，状态：{status_label}"


def get_post_login_redirect_path() -> str:
    return WORKSPACE_PROJECTS_PATH


def get_redirect_path_for_user(user: User | None) -> str:
    return WORKSPACE_PROJECTS_PATH if user is not None else PUBLIC_HOME_PATH


def require_user(request: Request) -> User | RedirectResponse:
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return user


def require_project_editor(request: Request) -> User | RedirectResponse:
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not can_edit_projects(user):
        raise ValueError("当前账号没有项目编辑权限。")
    return user


def require_admin(request: Request) -> User | RedirectResponse:
    user = require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not can_manage_users(user):
        raise ValueError("只有管理员可以执行该操作。")
    return user


def to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M")


def get_milestone_status_label(milestone_type: str, status: str) -> str:
    custom_labels = EXECUTION_STATUS_LABELS.get(milestone_type)
    if custom_labels and status in custom_labels:
        return custom_labels[status]
    return MILESTONE_STATUS_LABELS.get(status, status)


def get_setting(session, key: str) -> str | None:
    setting = session.get(SystemSetting, key)
    return setting.value if setting else None


def set_setting(session, key: str, value: str) -> None:
    setting = session.get(SystemSetting, key)
    if setting is None:
        session.add(SystemSetting(key=key, value=value))
        return
    setting.value = value
    setting.updated_at = datetime.utcnow()


def get_deepseek_api_key(session) -> str | None:
    return get_setting(session, "deepseek_api_key")


def run_deepseek_connectivity_test(session) -> dict[str, str]:
    api_key = get_deepseek_api_key(session)
    return ping_deepseek(api_key)


def get_runtime_system_status(session) -> dict[str, object]:
    stored_deepseek_key = get_deepseek_api_key(session)
    runtime_ai = get_runtime_ai_settings(stored_deepseek_key)
    ocr_settings = get_paddleocr_settings(session)

    ai_source = "database" if stored_deepseek_key else ("env" if runtime_ai.get("configured") else "missing")
    ocr_source = "database" if get_setting(session, "paddleocr_api_url") and get_setting(session, "paddleocr_access_token") else ("env" if ocr_settings.get("configured") else "missing")

    return {
        "ai_configured": bool(runtime_ai.get("configured")),
        "ai_source": ai_source,
        "ai_source_label": {"database": "已保存在系统设置", "env": "来自服务器环境变量", "missing": "未配置"}.get(ai_source, ai_source),
        "ai_model": str(runtime_ai.get("model") or ""),
        "ai_base_url": str(runtime_ai.get("base_url") or ""),
        "ocr_configured": bool(ocr_settings.get("configured")),
        "ocr_source": ocr_source,
        "ocr_source_label": {"database": "已保存在系统设置", "env": "来自服务器环境变量", "missing": "未配置"}.get(ocr_source, ocr_source),
        "ocr_api_url": str(ocr_settings.get("api_url") or ""),
        "ocr_timeout_seconds": str(ocr_settings.get("timeout_seconds") or ""),
        "scan_pdf_enabled": bool(ocr_settings.get("configured")),
    }


def get_paddleocr_settings(session) -> dict[str, object]:
    return get_runtime_ocr_settings(
        api_url=get_setting(session, "paddleocr_api_url"),
        access_token=get_setting(session, "paddleocr_access_token"),
        timeout_seconds=get_setting(session, "paddleocr_timeout_seconds"),
    )


def create_pending_review_job(
    session,
    *,
    temp_path: Path,
    original_name: str,
    content_type: str | None,
    created_by: str | None,
    api_key: str | None,
    ocr_settings: dict[str, object],
    matched_project_id: int | None = None,
    pending_base_id: str | None = None,
) -> tuple[ProjectFile, ExtractionJob]:
    extracted_text = extract_document_text(temp_path, original_name, ocr_settings)
    parsed_project, fields, requirements, summary = ai_extract_fields(original_name, extracted_text, api_key)
    _, ai_mode_label = get_runtime_ai_mode_label(api_key)

    matched_project = session.get(Project, matched_project_id) if matched_project_id else None
    if matched_project_id is not None and matched_project is None:
        raise ValueError("目标项目不存在，无法绑定补充文件。")

    if matched_project is None:
        tender_code = (parsed_project.get("tender_code") or "").strip()
        if tender_code:
            matched_project = session.query(Project).filter(Project.tender_code == tender_code).first()
        if matched_project is None and parsed_project.get("name"):
            matched_project = session.query(Project).filter(Project.name == parsed_project["name"]).first()

    date_folder = datetime.utcnow().strftime("%Y%m")
    base_id = pending_base_id or f"pending/{date_folder}"
    pending_dirs = build_project_subdirs(base_id)
    for path in pending_dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    target_path = pending_dirs["source"] / temp_path.name
    shutil.move(str(temp_path), target_path)

    raw_payload = {"project": parsed_project, "fields": fields, "requirements": requirements}
    write_project_cache_files(
        base_id,
        original_name,
        extracted_text,
        raw_payload,
        summary,
        cache_stem=target_path.stem,
    )

    file_record = ProjectFile(
        original_name=original_name,
        content_type=content_type,
        storage_path=str(target_path),
        file_hash=sha256_file(target_path),
        file_size=target_path.stat().st_size,
        extracted_text=extracted_text,
        extracted_summary=summary,
        source_kind="supplement" if matched_project else "intake",
        created_by=created_by,
    )
    session.add(file_record)
    session.flush()

    job = ExtractionJob(
        project_id=matched_project.id if matched_project_id is not None and matched_project is not None else None,
        project_file_id=file_record.id,
        status="pending_review",
        matched_project_id=matched_project.id if matched_project else None,
        ai_model=ai_mode_label,
        raw_result=json.dumps(raw_payload, ensure_ascii=False, indent=2),
        confidence_summary=summary,
        created_by=created_by,
    )
    session.add(job)
    session.flush()

    for field_key, field_label in FIELD_LABELS.items():
        source_match = next((item for item in fields if item.get("field_key") == field_key), {})
        session.add(
            ExtractionField(
                extraction_job_id=job.id,
                field_key=field_key,
                field_label=field_label,
                extracted_value=str(parsed_project.get(field_key, "") or ""),
                source_location=str(source_match.get("source_location", "") or ""),
                source_quote=str(source_match.get("source_quote", "") or ""),
                confidence=str(source_match.get("confidence", "") or ""),
                final_value=str(parsed_project.get(field_key, "") or ""),
            )
        )

    return file_record, job


def build_reminder_key(project_id: int, label: str, due_at: datetime) -> str:
    return f"{project_id}:{label}:{due_at.strftime('%Y%m%d%H%M')}"


def build_project_reminders(project: Project, reminder_states: dict[str, ReminderState]) -> list[dict[str, object]]:
    reminders: list[dict[str, object]] = []
    for label, value in (
        ("文件购买截止", project.document_sale_deadline),
        ("澄清截止", project.clarification_deadline),
        ("现场踏勘", project.site_visit_time),
        ("报名截止", project.signup_deadline),
        ("保证金截止", project.deposit_deadline),
        ("递交时间", project.submission_datetime),
        ("开标时间", project.bid_datetime),
    ):
        if value is None:
            continue
        delta = (value.date() - datetime.now().date()).days
        if delta > 7:
            continue
        reminder_key = build_reminder_key(project.id, label, value)
        state = reminder_states.get(reminder_key)
        reminders.append(
            {
                "reminder_key": reminder_key,
                "project": project,
                "label": label,
                "due_at": value,
                "delta_days": delta,
                "done": state is not None,
                "done_at": state.done_at if state else None,
                "done_by": state.done_by if state else None,
            }
        )
    return reminders


def serialize_reminder_item(item: dict[str, object]) -> dict[str, object]:
    project = item["project"]
    return {
        "reminder_key": item["reminder_key"],
        "project_id": project.id,
        "project_name": project.name,
        "project_status": project.status,
        "project_status_label": STATUS_LABELS.get(project.status, project.status),
        "label": item["label"],
        "due_at": format_datetime(item["due_at"]),
        "delta_days": item["delta_days"],
        "done": item["done"],
        "done_at": format_datetime(item["done_at"]),
        "done_by": item["done_by"] or "",
    }


def serialize_next_action_item(action: dict[str, str]) -> dict[str, str]:
    return {
        "label": action["label"],
        "due_at": action["due_at"],
        "status": action["status"],
    }


def collect_dashboard_reminders(
    active_projects: list[Project],
    milestone_map: dict[int, list[ProjectMilestone]],
    reminder_states: dict[str, ReminderState],
) -> list[dict[str, object]]:
    reminders: list[dict[str, object]] = []
    for project in active_projects:
        milestones = milestone_map.get(project.id, [])
        project_reminders: list[dict[str, object]] = []
        for milestone in sorted(milestones, key=lambda item: item.due_at or datetime.max):
            if milestone.due_at is None or milestone.status == "done":
                continue
            delta = (milestone.due_at.date() - datetime.now().date()).days
            if delta > 7:
                continue
            reminder_key = build_reminder_key(project.id, milestone.title, milestone.due_at)
            state = reminder_states.get(reminder_key)
            project_reminders.append(
                {
                    "reminder_key": reminder_key,
                    "project": project,
                    "label": milestone.title,
                    "due_at": milestone.due_at,
                    "delta_days": delta,
                    "done": state is not None,
                    "done_at": state.done_at if state else None,
                    "done_by": state.done_by if state else None,
                }
            )
        if not project_reminders and not milestones:
            project_reminders = build_project_reminders(project, reminder_states)
        reminders.extend(project_reminders)
    reminders.sort(key=lambda item: (item["done"], item["due_at"]))
    return reminders


def get_auto_completed_system_milestone_types(project_status: str) -> set[str]:
    if project_status == "submitted":
        return {"document_sale", "clarification", "site_visit", "signup", "deposit", "submission"}
    if project_status == "result_pending":
        return {"document_sale", "clarification", "site_visit", "signup", "deposit", "submission", "bid_open"}
    return set()


def resolve_system_milestone_status(
    project: Project,
    milestone_type: str,
    due_at: datetime,
    current_status: str | None = None,
) -> str:
    if milestone_type in get_auto_completed_system_milestone_types(project.status):
        return "done"
    if current_status == "done":
        return "done"
    if due_at < datetime.now():
        return "overdue"
    if current_status == "in_progress":
        return "in_progress"
    return "pending"


def build_project_next_actions(project: Project, milestones: list[ProjectMilestone]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for milestone in sorted(milestones, key=lambda item: item.due_at or datetime.max):
        if milestone.status == "done":
            continue
        actions.append(
            {
                "label": milestone.title,
                "due_at": format_datetime(milestone.due_at),
                "status": MILESTONE_STATUS_LABELS.get(milestone.status, milestone.status),
            }
        )
    if not actions and project.status == "result_pending":
        actions.append({"label": "跟进中标结果", "due_at": "", "status": "未开始"})
    if not actions and project.status == "submitted":
        actions.append({"label": "确认开标安排", "due_at": format_datetime(project.bid_datetime), "status": "未开始"})
    if not actions:
        if project.signup_deadline:
            actions.append({"label": "关注报名截止", "due_at": format_datetime(project.signup_deadline), "status": "未开始"})
        if project.deposit_deadline:
            actions.append({"label": "处理保证金", "due_at": format_datetime(project.deposit_deadline), "status": "未开始"})
        if project.bid_datetime:
            actions.append({"label": "准备开标/递交", "due_at": format_datetime(project.bid_datetime), "status": "未开始"})
    if not actions:
        actions.append({"label": "补充项目节点信息", "due_at": "", "status": "未开始"})
    return actions[:5]


def build_project_stage_snapshot(project: Project, milestones: list[ProjectMilestone]) -> dict[str, object]:
    now = datetime.now()
    pending_milestones = sorted(
        [item for item in milestones if item.status != "done"],
        key=lambda item: item.due_at or datetime.max,
    )
    next_milestone = pending_milestones[0] if pending_milestones else None

    stage_key = "pending_intake"
    if project.status == "submitted":
        stage_key = "submitted"
    elif project.status == "result_pending":
        stage_key = "pending_result"
    elif project.status in {"abandoned", "partner_completed"}:
        stage_key = "pending_result"
    elif project.signup_deadline and project.signup_deadline >= now:
        stage_key = "pending_signup"
    elif project.deposit_deadline and project.deposit_deadline >= now:
        stage_key = "pending_deposit"
    elif project.submission_datetime and project.submission_datetime >= now:
        stage_key = "pending_submission"
    elif project.bid_datetime and project.bid_datetime >= now:
        stage_key = "preparing_bid"
    elif any(item.milestone_type in {"submission", "bid_open"} for item in pending_milestones):
        stage_key = "preparing_bid"

    next_due = None
    next_label = "待补充下一步"
    if next_milestone is not None:
        next_due = next_milestone.due_at
        next_label = next_milestone.title
    else:
        for label, due_at in (
            ("报名截止", project.signup_deadline),
            ("保证金截止", project.deposit_deadline),
            ("递交时间", project.submission_datetime),
            ("开标时间", project.bid_datetime),
        ):
            if due_at and due_at >= now:
                next_due = due_at
                next_label = label
                break

    delta_days = None
    if next_due is not None:
        delta_days = (next_due.date() - now.date()).days

    risk_level = "normal"
    risk_reason = "当前暂无近期待办风险。"
    if next_due is None:
        risk_level = "watch"
        risk_reason = "关键时间尚未补齐，建议尽快完善节点。"
    elif delta_days is not None and delta_days < 0:
        risk_level = "critical"
        risk_reason = f"{next_label}已超期 {abs(delta_days)} 天。"
    elif delta_days is not None and delta_days <= 1:
        risk_level = "critical"
        risk_reason = f"{next_label}将在 {max(delta_days, 0)} 天内到期。"
    elif delta_days is not None and delta_days <= 3:
        risk_level = "warning"
        risk_reason = f"{next_label}将在 {delta_days} 天内到期。"
    elif any(item.status == "overdue" for item in pending_milestones):
        risk_level = "warning"
        risk_reason = "存在已过期但未完成的项目节点。"

    return {
        "stage_key": stage_key,
        "stage_label": WORKBOARD_STAGE_LABELS.get(stage_key, stage_key),
        "next_due": next_due,
        "next_due_text": format_datetime(next_due),
        "next_label": next_label,
        "delta_days": delta_days,
        "risk_level": risk_level,
        "risk_reason": risk_reason,
        "next_milestone": next_milestone,
    }


def build_execution_status_items(project: Project, milestones: list[ProjectMilestone]) -> list[dict[str, object]]:
    milestone_by_type = {item.milestone_type: item for item in milestones}
    items: list[dict[str, object]] = []
    for milestone_type in EXECUTION_MILESTONE_ORDER:
        milestone = milestone_by_type.get(milestone_type)
        if milestone is None:
            continue
        items.append(
            {
                "id": milestone.id,
                "title": milestone.title,
                "milestone_type": milestone.milestone_type,
                "type_label": MILESTONE_TYPE_LABELS.get(milestone.milestone_type, milestone.milestone_type),
                "status": milestone.status,
                "status_label": get_milestone_status_label(milestone.milestone_type, milestone.status),
                "due_at": milestone.due_at,
                "source_quote": milestone.source_quote or "",
                "created_by": milestone.created_by,
                "updated_at": milestone.updated_at,
            }
        )
    return items


def build_public_project_brief(project: Project, milestones: list[ProjectMilestone], requirements: list[ProjectRequirement]) -> dict[str, object]:
    risk_items = [item for item in requirements if item.category == "risk"]
    response_items = [item for item in requirements if item.category == "response"]
    document_items = [item for item in requirements if item.category == "document"]
    scoring_items = [item for item in requirements if item.category == "scoring"]
    return {
        "bid_document_status_label": BID_DOCUMENT_STATUS_LABELS.get(project.bid_document_status or "not_started", "未编制"),
        "has_defense_label": DEFENSE_OPTION_LABELS.get(project.has_defense or "unknown", "待确认"),
        "defense_presenter": build_public_display_text(project.defense_presenter, fallback=""),
        "project_name": build_public_display_text(project.name, fallback="项目名称待补录"),
        "address": build_public_display_text(project.location, fallback="待补充"),
        "bid_location": build_public_display_text(project.bid_location, fallback="待补充"),
        "project_type": build_public_display_text(project.project_type, fallback=""),
        "invalidation_risks": build_public_display_text(project.invalidation_risks, fallback=""),
        "submission_notes": build_public_display_text(project.submission_notes, fallback=""),
        "seal_notes": build_public_display_text(project.seal_notes, fallback=""),
        "risk_items": build_public_requirement_items(risk_items),
        "response_items": build_public_requirement_items(response_items),
        "document_items": build_public_requirement_items(document_items),
        "scoring_items": build_public_requirement_items(scoring_items),
        "milestone_count": len(milestones),
    }


def build_public_project_card_snapshot(project: Project, milestones: list[ProjectMilestone]) -> dict[str, str]:
    milestone_statuses = {
        item.milestone_type: get_milestone_status_label(item.milestone_type, item.status)
        for item in milestones
    }
    milestone_raw_statuses = {item.milestone_type: (item.status or "pending") for item in milestones}
    bid_document_status = (project.bid_document_status or "not_started").strip() or "not_started"
    defense_state = (project.has_defense or "unknown").strip() or "unknown"
    defense_label = DEFENSE_OPTION_LABELS.get(project.has_defense or "unknown", "待确认")
    if (project.has_defense or "").strip() == "yes" and (project.defense_presenter or "").strip():
        defense_label = f"答辩人：{project.defense_presenter.strip()}"
    risk_label_map = {
        "critical": "高风险",
        "warning": "中风险",
        "watch": "待补信息",
        "normal": "正常推进",
    }
    return {
        "bid_document_status_label": BID_DOCUMENT_STATUS_LABELS.get(bid_document_status, "未编制"),
        "bid_document_status_class": get_public_status_pill_class(
            {"not_started": "pending", "drafting": "drafting", "reviewing": "reviewing", "ready": "ready"}.get(
                bid_document_status,
                "pending",
            )
        ),
        "deposit_status_label": milestone_statuses.get("deposit", "待跟进"),
        "deposit_status_class": get_public_status_pill_class(milestone_raw_statuses.get("deposit", "pending")),
        "submission_status_label": milestone_statuses.get("submission", "待跟进"),
        "submission_status_class": get_public_status_pill_class(milestone_raw_statuses.get("submission", "pending")),
        "bid_open_status_label": milestone_statuses.get("bid_open", "待跟进"),
        "defense_label": defense_label,
        "defense_label_class": get_public_status_pill_class(defense_state),
        "project_name": build_public_display_text(project.name, fallback="项目名称待补录"),
        "address": build_public_display_text(project.location, fallback="待补充"),
        "bid_location": build_public_display_text(project.bid_location, fallback="待补充"),
        "bid_datetime_text": format_datetime(project.bid_datetime) or "待补充",
        "submission_datetime_text": format_datetime(project.submission_datetime) or "待补充",
        "deposit_amount_text": f"{project.deposit_amount} 万" if project.deposit_amount is not None else "待补充",
        "buyer_text": build_public_display_text(project.buyer, fallback="待补充"),
        "submission_focus": build_public_text_excerpt(project.submission_notes, fallback="递交要求待补充"),
        "seal_focus": build_public_text_excerpt(project.seal_notes, fallback="封标要求待补充"),
        "risk_focus": build_public_text_excerpt(project.invalidation_risks, fallback="废标项待补充"),
        "risk_label": risk_label_map.get(build_project_stage_snapshot(project, milestones).get("risk_level", "normal"), "正常推进"),
    }


def build_public_dashboard_data(session) -> dict[str, object]:
    dashboard = build_dashboard_data(session)
    active_projects = list(dashboard["active_projects"])
    archived_projects = [
        {
            "project_name": item.project_name,
            "display_name": build_archive_display_name(item.project_name),
            "bid_at": item.bid_at,
            "final_status": item.final_status,
            "archived_at": item.archived_at,
        }
        for item in dashboard["archived_projects"]
    ]
    milestone_map = dashboard["milestone_map"]
    public_active_projects: list[dict[str, object]] = []
    for item in dashboard["workboard_projects"]:
        project = item["project"]
        milestones = milestone_map.get(project.id, [])
        public_active_projects.append(
            {
                "project": project,
                "stage": item["stage"],
                "next_actions": item["next_actions"][:2],
                "card": build_public_project_card_snapshot(project, milestones),
            }
        )
    return {
        "active_count": len(active_projects),
        "archived_count": len(archived_projects),
        "pending_result_count": dashboard["pending_result_count"],
        "week_bid_open_count": dashboard["week_bid_open_count"],
        "active_projects": public_active_projects,
        "archived_projects": archived_projects[:12],
    }


def build_dashboard_data(
    session,
    *,
    keyword: str = "",
    stage_filter: str = "",
    risk_filter: str = "",
    status_filter: str = "",
) -> dict[str, object]:
    projects = (
        session.query(Project)
        .filter(Project.archived_at.is_(None))
        .order_by(Project.updated_at.desc())
        .all()
    )
    active_projects = [project for project in projects if project.status in ACTIVE_PROJECT_STATUSES]
    archived_projects = session.query(ArchivedProject).order_by(ArchivedProject.archived_at.desc()).limit(20).all()
    reminder_states = {state.reminder_key: state for state in session.query(ReminderState).all()}
    milestone_map = {
        project_id: items
        for project_id, items in (
            (
                project.id,
                session.query(ProjectMilestone).filter(ProjectMilestone.project_id == project.id).order_by(ProjectMilestone.due_at.asc()).all(),
            )
            for project in projects
        )
    }

    reminders = []
    for project in active_projects:
        for label, value in (
            ("报名截止", project.signup_deadline),
            ("保证金截止", project.deposit_deadline),
            ("开标时间", project.bid_datetime),
        ):
            if value is None:
                continue
            delta = (value.date() - datetime.now().date()).days
            if delta > 7:
                continue
            reminder_key = build_reminder_key(project.id, label, value)
            state = reminder_states.get(reminder_key)
            reminders.append(
                {
                    "reminder_key": reminder_key,
                    "project": project,
                    "label": label,
                    "due_at": value,
                    "delta_days": delta,
                    "done": state is not None,
                    "done_at": state.done_at if state else None,
                    "done_by": state.done_by if state else None,
                }
            )
    reminders.sort(key=lambda item: (item["done"], item["due_at"]))
    reminders = collect_dashboard_reminders(active_projects, milestone_map, reminder_states)

    pending_reviews = (
        session.query(ExtractionJob)
        .options(joinedload(ExtractionJob.project_file))
        .filter(ExtractionJob.status == "pending_review")
        .order_by(ExtractionJob.created_at.desc())
        .all()
    )
    runtime_status = get_runtime_system_status(session)
    users = session.query(User).order_by(User.created_at.asc()).all()
    project_stage_map = {project.id: build_project_stage_snapshot(project, milestone_map.get(project.id, [])) for project in projects}
    workboard_projects: list[dict[str, object]] = []
    for project in active_projects:
        snapshot = project_stage_map.get(project.id, {})
        workboard_projects.append(
            {
                "project": project,
                "stage": snapshot,
                "next_actions": build_project_next_actions(project, milestone_map.get(project.id, [])),
            }
        )
    workboard_projects.sort(
        key=lambda item: (
            {"critical": 0, "warning": 1, "watch": 2, "normal": 3}.get(str(item["stage"].get("risk_level")), 9),
            item["stage"].get("next_due") or datetime.max,
            getattr(item["project"], "updated_at", datetime.max),
        )
    )

    filtered_workboard_projects = workboard_projects
    normalized_keyword = keyword.strip()
    if normalized_keyword:
        lowered_keyword = normalized_keyword.lower()
        filtered_workboard_projects = [
            item
            for item in filtered_workboard_projects
            if lowered_keyword in (item["project"].name or "").lower()
            or lowered_keyword in (item["project"].tender_code or "").lower()
            or lowered_keyword in (item["project"].owner_name or "").lower()
            or lowered_keyword in (item["project"].buyer or "").lower()
        ]

    if status_filter and status_filter in STATUS_LABELS:
        filtered_workboard_projects = [item for item in filtered_workboard_projects if item["project"].status == status_filter]

    if stage_filter and stage_filter in WORKBOARD_STAGE_LABELS:
        filtered_workboard_projects = [item for item in filtered_workboard_projects if item["stage"].get("stage_key") == stage_filter]

    if risk_filter in {"critical", "warning", "watch", "normal"}:
        filtered_workboard_projects = [item for item in filtered_workboard_projects if item["stage"].get("risk_level") == risk_filter]

    stage_groups: list[dict[str, object]] = []
    for stage_key, stage_label in WORKBOARD_STAGE_LABELS.items():
        entries = [item for item in filtered_workboard_projects if item["stage"].get("stage_key") == stage_key]
        stage_groups.append({"stage_key": stage_key, "stage_label": stage_label, "entries": entries})

    risk_projects = [item for item in filtered_workboard_projects if item["stage"].get("risk_level") in {"critical", "warning", "watch"}][:8]
    due_today_count = sum(1 for item in reminders if item["delta_days"] == 0 and not item["done"])
    week_bid_open_count = sum(
        1
        for project in active_projects
        if project.bid_datetime is not None and 0 <= (project.bid_datetime.date() - datetime.now().date()).days <= 7
    )
    pending_deposit_count = sum(
        1
        for project in active_projects
        if project.deposit_deadline is not None and (project.deposit_deadline.date() - datetime.now().date()).days >= 0
    )
    pending_result_count = sum(1 for project in active_projects if project.status == "result_pending")

    return {
        "projects": projects,
        "active_projects": active_projects,
        "archived_projects": archived_projects,
        "pending_reviews": pending_reviews,
        "all_reminders": reminders,
        "reminders": reminders[:12],
        "deepseek_configured": bool(runtime_status["ai_configured"]),
        "ocr_configured": bool(runtime_status["ocr_configured"]),
        "ocr_api_url": str(runtime_status["ocr_api_url"]),
        "ocr_timeout_seconds": str(runtime_status["ocr_timeout_seconds"]),
        "runtime_status": runtime_status,
        "users": users,
        "next_actions": {project.id: build_project_next_actions(project, milestone_map.get(project.id, [])) for project in projects},
        "project_stage_map": project_stage_map,
        "workboard_projects": filtered_workboard_projects,
        "stage_groups": stage_groups,
        "risk_projects": risk_projects,
        "due_today_count": due_today_count,
        "week_bid_open_count": week_bid_open_count,
        "pending_deposit_count": pending_deposit_count,
        "pending_result_count": pending_result_count,
        "filters": {
            "keyword": normalized_keyword,
            "stage": stage_filter if stage_filter in WORKBOARD_STAGE_LABELS else "",
            "risk": risk_filter if risk_filter in {"critical", "warning", "watch", "normal"} else "",
            "status": status_filter if status_filter in STATUS_LABELS else "",
        },
        "milestone_map": milestone_map,
    }


def build_workspace_review_data(session) -> dict[str, object]:
    pending_reviews = (
        session.query(ExtractionJob)
        .options(joinedload(ExtractionJob.project_file))
        .filter(ExtractionJob.status == "pending_review")
        .order_by(ExtractionJob.created_at.desc())
        .all()
    )
    entries = serialize_pending_review_entries(pending_reviews)
    matched_ids = {int(item["matched_project_id"]) for item in entries if item.get("matched_project_id")}
    matched_project_names: dict[int, str] = {}
    if matched_ids:
        matched_project_names = {
            project.id: project.name
            for project in session.query(Project).filter(Project.id.in_(matched_ids)).all()
        }
    for item in entries:
        matched_project_id = item.get("matched_project_id")
        item["matched_project_name"] = matched_project_names.get(int(matched_project_id), "") if matched_project_id else ""

    today = datetime.now().date()
    return {
        "pending_reviews": entries,
        "stats": {
            "pending_count": len(entries),
            "today_count": sum(1 for item in entries if item["created_at"] and item["created_at"].date() == today),
            "intake_count": sum(1 for item in entries if item["kind"] == "intake"),
            "supplement_count": sum(1 for item in entries if item["kind"] == "supplement"),
            "matched_count": sum(1 for item in entries if item.get("matched_project_id")),
        },
    }


def build_workspace_settings_data(session, *, include_admin_data: bool = False) -> dict[str, object]:
    runtime_status = get_runtime_system_status(session)
    users = session.query(User).order_by(User.created_at.asc()).all() if include_admin_data else []
    recent_logs = (
        session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(18).all()
        if include_admin_data
        else []
    )
    return {
        "runtime_status": runtime_status,
        "deepseek_configured": bool(runtime_status["ai_configured"]),
        "ocr_configured": bool(runtime_status["ocr_configured"]),
        "ocr_api_url": str(runtime_status["ocr_api_url"]),
        "ocr_timeout_seconds": str(runtime_status["ocr_timeout_seconds"]),
        "users": users,
        "recent_logs": recent_logs,
        "stats": {
            "active_project_count": session.query(Project)
            .filter(Project.archived_at.is_(None), Project.status.in_(ACTIVE_PROJECT_STATUSES))
            .count(),
            "pending_review_count": session.query(ExtractionJob)
            .filter(ExtractionJob.status == "pending_review")
            .count(),
            "archive_count": session.query(ArchivedProject).count(),
            "team_count": len(users) if include_admin_data else session.query(User).count(),
        },
    }


def group_requirements(requirements: list[ProjectRequirement]) -> list[dict[str, object]]:
    grouped: dict[str, list[ProjectRequirement]] = {}
    for item in requirements:
        grouped.setdefault(item.category, []).append(item)
    ordered: list[dict[str, object]] = []
    for category in REQUIREMENT_CATEGORY_LABELS:
        if category not in grouped:
            continue
        ordered.append(
            {
                "category": category,
                "label": REQUIREMENT_CATEGORY_LABELS.get(category, category),
                "entries": grouped[category],
            }
        )
    for category, items in grouped.items():
        if category in REQUIREMENT_CATEGORY_LABELS:
            continue
        ordered.append({"category": category, "label": category, "entries": items})
    return ordered


def serialize_project_messages(
    messages: list[ProjectMessage],
    *,
    file_lookup: dict[str, ProjectFile] | None = None,
    project_id: int | None = None,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for message in messages:
        parsed = parse_question_answer_metadata(message.answer, message.citations)
        basis_items = parsed["basis_items"]
        if file_lookup is not None and project_id is not None:
            basis_items = enrich_basis_items(parsed["basis_items"], file_lookup, project_id)
        items.append(
            {
                "id": message.id,
                "question": message.question,
                "answer": parsed["answer"],
                "answer_mode": parsed["answer_mode"],
                "answer_mode_label": ANSWER_MODE_LABELS.get(str(parsed["answer_mode"]), str(parsed["answer_mode"])),
                "answer_status": parsed["answer_status"],
                "answer_status_label": QUESTION_ANSWER_STATUS_LABELS.get(str(parsed["answer_status"]), str(parsed["answer_status"])),
                "basis_items": basis_items,
                "ai_suggestion": parsed["ai_suggestion"],
                "created_by": message.created_by,
                "created_at": message.created_at,
            }
        )
    return items


def build_project_checklists(
    project: Project,
    milestones: list[ProjectMilestone],
    requirements: list[ProjectRequirement],
    followups: list[ProjectFollowup],
) -> list[dict[str, object]]:
    sections: list[dict[str, object]] = []

    todo_items: list[dict[str, str]] = []
    for milestone in sorted(milestones, key=lambda item: item.due_at or datetime.max):
        if milestone.status == "done":
            continue
        todo_items.append(
            {
                "title": milestone.title,
                "meta": f"{MILESTONE_STATUS_LABELS.get(milestone.status, milestone.status)}"
                + (f" · {format_datetime(milestone.due_at)}" if milestone.due_at else ""),
                "detail": milestone.source_quote or "请结合项目节点安排推进。",
            }
        )
    if not todo_items:
        for action in build_project_next_actions(project, milestones):
            todo_items.append(
                {
                    "title": action["label"],
                    "meta": action["status"] + (f" · {action['due_at']}" if action["due_at"] else ""),
                    "detail": "当前暂无未完成节点，建议按下一步动作继续跟进。",
                }
            )
    sections.append({"title": "待办清单", "items": todo_items[:8]})

    prep_items: list[dict[str, str]] = []
    important_categories = {"qualification", "document", "business", "technical", "pricing", "response"}
    for item in requirements:
        if item.category not in important_categories:
            continue
        prep_items.append(
            {
                "title": f"{REQUIREMENT_CATEGORY_LABELS.get(item.category, item.category)} · {item.title}",
                "meta": REQUIREMENT_IMPORTANCE_LABELS.get(item.importance, item.importance),
                "detail": item.content,
            }
        )
    sections.append({"title": "标书准备清单", "items": prep_items[:12]})

    risk_items: list[dict[str, str]] = []
    for item in requirements:
        if item.category not in {"risk", "scoring"}:
            continue
        risk_items.append(
            {
                "title": f"{REQUIREMENT_CATEGORY_LABELS.get(item.category, item.category)} · {item.title}",
                "meta": REQUIREMENT_IMPORTANCE_LABELS.get(item.importance, item.importance),
                "detail": item.content,
            }
        )
    for followup in followups[:3]:
        risk_items.append(
            {
                "title": f"跟进记录 · {followup.created_by or '系统记录'}",
                "meta": format_datetime(followup.created_at),
                "detail": followup.content,
            }
        )
    sections.append({"title": "风险与跟进", "items": risk_items[:10]})

    return sections


def sync_project_requirements_from_payload(
    session,
    *,
    project_id: int,
    requirement_payloads: list[object],
    created_by: str | None,
) -> tuple[int, int, int]:
    existing_keys = {
        (
            item.category,
            item.title,
            item.content,
            item.source_location or "",
            item.source_quote or "",
        )
        for item in session.query(ProjectRequirement).filter(ProjectRequirement.project_id == project_id).all()
    }

    added_count = 0
    skipped_count = 0
    duplicate_count = 0

    for item in requirement_payloads:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "").strip()
        content = str(item.get("content", "") or "").strip()
        if not title and not content:
            continue
        if not requirement_has_source(item):
            skipped_count += 1
            continue
        category = str(item.get("category", "") or "other").strip() or "other"
        if category not in REQUIREMENT_CATEGORY_LABELS:
            category = "other"
        importance = str(item.get("importance", "") or "medium").strip() or "medium"
        if importance not in REQUIREMENT_IMPORTANCE_LABELS:
            importance = "medium"
        normalized_title = title or content[:80] or "未命名要求"
        normalized_content = content or title
        source_location = str(item.get("source_location", "") or "").strip()
        source_quote = str(item.get("source_quote", "") or "").strip()
        signature = (category, normalized_title, normalized_content, source_location, source_quote)
        if signature in existing_keys:
            duplicate_count += 1
            continue
        existing_keys.add(signature)
        session.add(
            ProjectRequirement(
                project_id=project_id,
                category=category,
                title=normalized_title,
                content=normalized_content,
                importance=importance,
                source_location=source_location or None,
                source_quote=source_quote or None,
                created_by=created_by,
            )
        )
        added_count += 1

    return added_count, skipped_count, duplicate_count


def sync_system_milestones(session, project: Project, actor_name: str | None) -> None:
    system_milestones = [
        ("document_sale", "文件购买截止", project.document_sale_deadline),
        ("clarification", "澄清截止", project.clarification_deadline),
        ("site_visit", "现场踏勘", project.site_visit_time),
        ("signup", "报名截止", project.signup_deadline),
        ("deposit", "保证金截止", project.deposit_deadline),
        ("submission", "递交时间", project.submission_datetime),
        ("bid_open", "开标时间", project.bid_datetime),
    ]

    existing = {
        item.milestone_type: item
        for item in session.query(ProjectMilestone)
        .filter(
            ProjectMilestone.project_id == project.id,
            ProjectMilestone.milestone_type.in_([item[0] for item in system_milestones]),
        )
        .all()
    }

    for milestone_type, title, due_at in system_milestones:
        current = existing.get(milestone_type)
        if due_at is None:
            if current is not None:
                session.delete(current)
            continue

        if current is None:
            session.add(
                ProjectMilestone(
                    project_id=project.id,
                    milestone_type=milestone_type,
                    title=title,
                    due_at=due_at,
                    status=resolve_system_milestone_status(project, milestone_type, due_at),
                    created_by=actor_name,
                )
            )
            continue

        current.title = title
        current.due_at = due_at
        current.status = resolve_system_milestone_status(project, milestone_type, due_at, current.status)
        if not current.created_by and actor_name:
            current.created_by = actor_name

    session.flush()


def archive_project(session, project: Project, final_status: str) -> None:
    archive_project_data(session, project, final_status)


def repair_terminal_projects(session) -> int:
    terminal_projects = (
        session.query(Project)
        .filter(Project.status.in_(sorted(TERMINAL_PROJECT_STATUSES)))
        .all()
    )
    repaired_count = 0
    for project in terminal_projects:
        add_audit_log(
            session,
            actor=None,
            action="archive_project",
            entity_type="project",
            entity_id=project.id,
            project_name=project.name,
            detail=f"启动时修复历史终态项目，归档状态：{project.status}",
        )
        archive_project(session, project, project.status)
        repaired_count += 1
    return repaired_count


def repair_active_project_milestones(session) -> int:
    repaired_count = 0
    projects = (
        session.query(Project)
        .filter(Project.archived_at.is_(None))
        .all()
    )
    for project in projects:
        sync_system_milestones(session, project, "系统修复")
        repaired_count += 1
    return repaired_count


def repair_project_text_content(session) -> int:
    repaired_count = 0
    for project in session.query(Project).all():
        for attr in PROJECT_TEXT_FIELD_ATTRS.values():
            value = getattr(project, attr, None)
            if not isinstance(value, str) or not value.strip():
                continue
            repaired_value = repair_mojibake_text(value).strip()
            if repaired_value and repaired_value != value:
                setattr(project, attr, repaired_value)
                repaired_count += 1
    for archived in session.query(ArchivedProject).all():
        if not archived.project_name or not archived.project_name.strip():
            continue
        repaired_name = repair_mojibake_text(archived.project_name).strip()
        if repaired_name and repaired_name != archived.project_name:
            archived.project_name = repaired_name
            repaired_count += 1
    return repaired_count


def apply_project_form(project: Project, form_data: dict[str, str]) -> None:
    project.name = form_data["name"].strip()
    project.short_name = form_data.get("short_name", "").strip() or project.name[:18]
    project.tender_code = form_data.get("tender_code", "").strip() or None
    project.buyer = form_data.get("buyer", "").strip() or None
    project.project_type = form_data.get("project_type", "").strip() or None
    project.bid_mode = form_data.get("bid_mode", "self").strip() or "self"
    project.status = form_data.get("status", "tracking").strip() or "tracking"
    project.owner_name = form_data.get("owner_name", "").strip() or None
    project.agency = form_data.get("agency", "").strip() or None
    project.contact_name = form_data.get("contact_name", "").strip() or None
    project.contact_phone = form_data.get("contact_phone", "").strip() or None
    project.location = form_data.get("location", "").strip() or None
    project.service_scope = form_data.get("service_scope", "").strip() or None
    project.contract_term = form_data.get("contract_term", "").strip() or None
    project.budget_amount = form_data.get("budget_amount", "").strip() or None
    bid_document_status = form_data.get("bid_document_status", "").strip() or "not_started"
    project.bid_document_status = bid_document_status if bid_document_status in BID_DOCUMENT_STATUS_LABELS else "not_started"
    has_defense = form_data.get("has_defense", "").strip() or "unknown"
    project.has_defense = has_defense if has_defense in DEFENSE_OPTION_LABELS else "unknown"
    project.defense_presenter = form_data.get("defense_presenter", "").strip() or None
    project.invalidation_risks = form_data.get("invalidation_risks", "").strip() or None
    project.submission_notes = form_data.get("submission_notes", "").strip() or None
    project.seal_notes = form_data.get("seal_notes", "").strip() or None
    project.notes = form_data.get("notes", "").strip() or None
    project.deposit_amount = float(form_data["deposit_amount"]) if form_data.get("deposit_amount", "").strip() else None
    project.signup_deadline = to_dt(form_data.get("signup_deadline"))
    project.document_sale_deadline = to_dt(form_data.get("document_sale_deadline"))
    project.clarification_deadline = to_dt(form_data.get("clarification_deadline"))
    project.site_visit_time = to_dt(form_data.get("site_visit_time"))
    project.deposit_deadline = to_dt(form_data.get("deposit_deadline"))
    project.bid_datetime = to_dt(form_data.get("bid_datetime"))
    project.submission_datetime = to_dt(form_data.get("submission_datetime"))
    project.bid_location = form_data.get("bid_location", "").strip() or None
    project.file_fee = form_data.get("file_fee", "").strip() or None
    project.payment_info = form_data.get("payment_info", "").strip() or None
    project.updated_at = datetime.utcnow()


def validate_project_form(form_data: dict[str, str]) -> None:
    if not form_data.get("name", "").strip():
        raise ValueError("项目名称不能为空。")
    bid_mode = form_data.get("bid_mode", "self").strip() or "self"
    if bid_mode not in BID_MODE_LABELS:
        raise ValueError("投标性质不支持。")
    status = form_data.get("status", "tracking").strip() or "tracking"
    if status not in STATUS_LABELS:
        raise ValueError("项目状态不支持。")
    bid_document_status = form_data.get("bid_document_status", "not_started").strip() or "not_started"
    if bid_document_status not in BID_DOCUMENT_STATUS_LABELS:
        raise ValueError("标书编制状态不支持。")
    has_defense = form_data.get("has_defense", "unknown").strip() or "unknown"
    if has_defense not in DEFENSE_OPTION_LABELS:
        raise ValueError("答辩状态不支持。")
    if form_data.get("deposit_amount", "").strip():
        try:
            float(form_data["deposit_amount"])
        except ValueError as exc:
            raise ValueError("保证金金额必须是数字。") from exc
    for key in ("signup_deadline", "document_sale_deadline", "clarification_deadline", "site_visit_time", "deposit_deadline", "bid_datetime", "submission_datetime"):
        value = form_data.get(key, "").strip()
        if value:
            to_dt(value)


def create_app() -> FastAPI:
    ensure_directories()
    Base.metadata.create_all(bind=engine)
    ensure_schema_updates()
    ensure_admin_user()
    with session_scope() as session:
        repair_project_text_content(session)
        repair_terminal_projects(session)
        repair_active_project_milestones(session)

    app = FastAPI(title="投标项目智能管理平台")
    app.add_middleware(SessionMiddleware, secret_key=get_secret_key())
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(api_v1_router)

    @app.middleware("http")
    async def enforce_api_and_legacy_boundaries(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/v1"):
            content_length = request.headers.get("content-length")
            try:
                body_size = int(content_length) if content_length else 0
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"code": "invalid_content_length", "message": "Content-Length 必须是非负整数。", "errors": []}},
                )
            if body_size < 0:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"code": "invalid_content_length", "message": "Content-Length 必须是非负整数。", "errors": []}},
                )
            if body_size > 2 * 1024 * 1024:
                return JSONResponse(
                    status_code=413,
                    content={"error": {"code": "payload_too_large", "message": "API 请求体不能超过 2MB。", "errors": []}},
                )
        if not legacy_ai_routes_enabled():
            legacy_route = (
                path == WORKSPACE_REVIEWS_PATH
                or path == "/upload"
                or path.startswith("/reviews/")
                or bool(re.fullmatch(r"/projects/\d+/(upload|ask)", path))
                or path.startswith("/settings/deepseek")
                or path.startswith("/settings/paddleocr")
            )
            if legacy_route:
                if path.startswith("/api/") or request.headers.get("accept", "").startswith("application/json"):
                    return JSONResponse(status_code=410, content={"error": {"code": "legacy_ai_disabled", "message": "站内 AI/OCR 流程已停用，请通过 Hermes Skill 更新项目。", "errors": []}})
                return RedirectResponse(f"{WORKSPACE_SETTINGS_PATH}?notice={quote('站内 AI/OCR 已停用，请通过 Hermes Skill 更新项目。')}", status_code=303)
        return await call_next(request)

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or f"req_{secrets.token_hex(8)}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiProblem)
    async def handle_api_problem(request: Request, exc: ApiProblem):
        return api_error_response(request, exc)

    def render_workspace_projects_page(request: Request, *, error: str | None = None, notice: str | None = None) -> HTMLResponse:
        keyword = request.query_params.get("keyword", "").strip()
        status_filter = request.query_params.get("status", "").strip()
        owner_filter = request.query_params.get("owner", "").strip()
        with session_scope() as session:
            workspace_data = build_workspace_data(session, keyword=keyword, status=status_filter, owner=owner_filter)
        return render_template(
            "app_projects.html",
            request,
            workspace=workspace_data,
            error=error,
            notice=notice,
            app_section="workspace_projects",
        )

    def render_workspace_reviews_page(request: Request, *, error: str | None = None, notice: str | None = None) -> HTMLResponse:
        with session_scope() as session:
            review_data = build_workspace_review_data(session)
        return render_template(
            "workspace_reviews.html",
            request,
            error=error,
            notice=notice,
            app_section="workspace_reviews",
            **review_data,
        )

    def render_workspace_settings_page(
        request: Request,
        *,
        user: User | None = None,
        error: str | None = None,
        notice: str | None = None,
    ) -> HTMLResponse:
        viewer = user or get_current_user(request)
        include_admin_data = viewer is not None and can_manage_users(viewer)
        with session_scope() as session:
            settings_data = build_workspace_settings_data(session, include_admin_data=include_admin_data)
            api_tokens = session.query(ApiToken).order_by(ApiToken.created_at.desc()).all() if include_admin_data else []
        return render_template(
            "app_settings.html",
            request,
            error=error,
            notice=notice,
            app_section="workspace_settings",
            api_tokens=api_tokens,
            new_api_token=request.session.pop("new_api_token", None),
            **settings_data,
        )

    def render_archives_page_content(
        request: Request,
        *,
        error: str | None = None,
        notice: str | None = None,
        app_section: str = "",
    ) -> HTMLResponse:
        keyword = request.query_params.get("keyword", "").strip()
        status = request.query_params.get("status", "").strip()
        with session_scope() as session:
            archive_data = build_archive_data(session, keyword=keyword, status=status)
        return render_template(
            "app_archives.html",
            request,
            archive=archive_data,
            error=error,
            notice=notice,
            app_section=app_section,
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Request, exc: ValueError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": str(exc)}, status_code=400)
        user = get_current_user(request)
        if request.url.path.startswith("/login"):
            return render_template("login.html", request, error=str(exc))
        if user is None:
            return RedirectResponse(PUBLIC_HOME_PATH, status_code=302)
        return redirect_request_back_with_message(request, str(exc), fallback_path=get_post_login_redirect_path())

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return RedirectResponse(WORKSPACE_PATH if get_current_user(request) else "/login", status_code=302)

    @app.get(WORKSPACE_PATH, response_class=HTMLResponse)
    def workspace(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return RedirectResponse("/login", status_code=302)
        with session_scope() as session:
            workspace_data = build_workspace_data(session)
        return render_template("app_dashboard.html", request, workspace=workspace_data, error=None, notice=request.query_params.get("notice"), app_section="workspace_dashboard")

    @app.get(WORKSPACE_PROJECTS_PATH, response_class=HTMLResponse)
    def workspace_projects(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return RedirectResponse("/login", status_code=302)
        return render_workspace_projects_page(request, notice=request.query_params.get("notice"))

    @app.get(WORKSPACE_CALENDAR_PATH, response_class=HTMLResponse)
    def workspace_calendar(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return RedirectResponse("/login", status_code=302)
        now = datetime.now()
        year = int(request.query_params.get("year", now.year))
        month = int(request.query_params.get("month", now.month))
        if month < 1 or month > 12 or year < 2000 or year > 2100:
            raise ValueError("日历月份无效。")
        with session_scope() as session:
            calendar = build_calendar_data(session, year, month)
        return render_template("app_calendar.html", request, calendar=calendar, error=None, notice=None, app_section="workspace_calendar")

    @app.get(WORKSPACE_REVIEWS_PATH, response_class=HTMLResponse)
    def workspace_reviews(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return RedirectResponse("/login", status_code=302)
        return render_workspace_reviews_page(request, notice=request.query_params.get("notice"))

    @app.get(WORKSPACE_ARCHIVES_PATH, response_class=HTMLResponse)
    def workspace_archives(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return RedirectResponse("/login", status_code=302)
        return render_archives_page_content(
            request,
            notice=request.query_params.get("notice"),
            app_section="workspace_archives",
        )

    @app.get(WORKSPACE_SETTINGS_PATH, response_class=HTMLResponse)
    def workspace_settings(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return RedirectResponse("/login", status_code=302)
        return render_workspace_settings_page(request, user=user, notice=request.query_params.get("notice"))

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if get_current_user(request):
            return RedirectResponse(get_post_login_redirect_path(), status_code=302)
        return render_template("login.html", request, error=None)

    @app.get("/guide", response_class=HTMLResponse)
    def guide_page(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return user
        return render_template("guide.html", request, error=None, notice=None, app_section="workspace_settings")

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
        with session_scope() as session:
            user = session.query(User).filter(User.username == username.strip()).first()
            if user is None or not verify_password(password, user.password_hash):
                return render_template("login.html", request, error="用户名或密码不正确。")
            request.session["user_id"] = user.id
            add_audit_log(session, actor=user, action="login", entity_type="session", entity_id=user.id, detail=f"用户名：{user.username}")
        return RedirectResponse(get_post_login_redirect_path(), status_code=302)

    @app.post("/logout")
    def logout(request: Request):
        user = get_current_user(request)
        if user is not None:
            with session_scope() as session:
                db_user = session.get(User, user.id)
                add_audit_log(session, actor=db_user, action="logout", entity_type="session", entity_id=user.id, detail=f"用户名：{user.username}")
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    @app.post("/settings/api-tokens")
    async def create_api_token_web(
        request: Request,
        name: str = Form("Hermes Skill"),
        expires_in_days: int = Form(90),
        projects_read: str = Form(""),
        projects_write: str = Form(""),
        projects_archive: str = Form(""),
    ):
        user = require_admin(request)
        if isinstance(user, RedirectResponse):
            return user
        scopes = set()
        if projects_read:
            scopes.add("projects:read")
        if projects_write:
            scopes.add("projects:write")
        if projects_archive:
            scopes.add("projects:archive")
        if not scopes:
            raise ValueError("请至少选择一个 API 权限。")
        with session_scope() as session:
            db_user = session.get(User, user.id)
            token_record, raw_token = create_api_token(
                session,
                name=name,
                scopes=scopes,
                created_by=db_user.display_name if db_user else user.display_name,
                expires_in_days=expires_in_days,
            )
            add_audit_log(
                session,
                actor=db_user,
                action="create_api_token",
                entity_type="api_token",
                entity_id=token_record.id,
                detail=f"名称：{token_record.name}；权限：{token_record.scopes}",
            )
        request.session["new_api_token"] = raw_token
        return redirect_settings_with_notice("Token 已创建，请立即保存；离开页面后不再显示。", fragment="hermes")

    @app.post("/settings/api-tokens/{token_id}/revoke")
    def revoke_api_token_web(token_id: int, request: Request):
        user = require_admin(request)
        if isinstance(user, RedirectResponse):
            return user
        with session_scope() as session:
            token_record = session.get(ApiToken, token_id)
            if not token_record:
                raise ValueError("API Token 不存在。")
            if token_record.revoked_at is None:
                token_record.revoked_at = datetime.utcnow()
            db_user = session.get(User, user.id)
            add_audit_log(
                session,
                actor=db_user,
                action="revoke_api_token",
                entity_type="api_token",
                entity_id=token_record.id,
                detail=f"撤销 Token：{token_record.name}",
            )
        return redirect_settings_with_notice("Token 已撤销。", fragment="hermes")

    @app.post("/settings/deepseek")
    async def update_deepseek_settings(request: Request, api_key: str = Form("")):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return user
        if not can_configure_system(user):
            raise ValueError("只有管理员可以配置系统参数。")
        with session_scope() as session:
            value = api_key.strip()
            if not value:
                runtime_status = get_runtime_system_status(session)
                if not runtime_status["ai_configured"]:
                    raise ValueError("DeepSeek API Key 不能为空。")
                return RedirectResponse(WORKSPACE_SETTINGS_PATH, status_code=302)
            set_setting(session, "deepseek_api_key", value)
            db_user = session.get(User, user.id)
            add_audit_log(session, actor=db_user, action="update_deepseek", entity_type="system_setting", entity_id="deepseek_api_key", detail="更新 DeepSeek API Key")
        return redirect_settings_with_notice("DeepSeek 配置已保存。")

    @app.post("/settings/deepseek/test")
    async def test_deepseek_settings(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return user
        if not can_configure_system(user):
            raise ValueError("只有管理员可以配置系统参数。")
        with session_scope() as session:
            result = run_deepseek_connectivity_test(session)
            db_user = session.get(User, user.id)
            add_audit_log(
                session,
                actor=db_user,
                action="update_deepseek",
                entity_type="system_setting",
                entity_id="deepseek_connectivity_test",
                detail=f"DeepSeek 连通成功：{result['model']} @ {result['base_url']}",
            )
        return redirect_settings_with_notice("DeepSeek 连通测试成功。")

    @app.post("/settings/paddleocr")
    async def update_paddleocr_settings(
        request: Request,
        api_url: str = Form(""),
        access_token: str = Form(""),
        timeout_seconds: str = Form("600"),
    ):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return user
        if not can_configure_system(user):
            raise ValueError("只有管理员可以配置系统参数。")
        cleaned_api_url = api_url.strip()
        cleaned_token = access_token.strip()
        cleaned_timeout = timeout_seconds.strip() or "600"
        try:
            timeout_value = float(cleaned_timeout)
            if timeout_value <= 0:
                raise ValueError
        except ValueError as exc:
            raise ValueError("PaddleOCR 超时时间必须是大于 0 的数字秒数。") from exc
        with session_scope() as session:
            existing_api_url = get_setting(session, "paddleocr_api_url") or ""
            existing_token = get_setting(session, "paddleocr_access_token") or ""
            if not cleaned_api_url:
                cleaned_api_url = existing_api_url.strip()
            if not cleaned_token:
                cleaned_token = existing_token.strip()
            if not cleaned_api_url:
                raise ValueError("PaddleOCR 接口地址不能为空。")
            if not cleaned_token:
                raise ValueError("PaddleOCR Access Token 不能为空。")
            set_setting(session, "paddleocr_api_url", cleaned_api_url)
            set_setting(session, "paddleocr_access_token", cleaned_token)
            set_setting(session, "paddleocr_timeout_seconds", str(timeout_value))
            db_user = session.get(User, user.id)
            add_audit_log(
                session,
                actor=db_user,
                action="update_paddleocr",
                entity_type="system_setting",
                entity_id="paddleocr_api_url",
                detail="更新 PaddleOCR 文档解析配置",
            )
        return redirect_settings_with_notice("PaddleOCR 配置已保存。")

    @app.post("/projects/create")
    async def create_project_manual(
        request: Request,
        name: str = Form(...),
        short_name: str = Form(""),
        tender_code: str = Form(""),
        buyer: str = Form(""),
        project_type: str = Form(""),
        bid_mode: str = Form("self"),
        status: str = Form("tracking"),
        owner_name: str = Form(""),
        agency: str = Form(""),
        contact_name: str = Form(""),
        contact_phone: str = Form(""),
        location: str = Form(""),
        service_scope: str = Form(""),
        contract_term: str = Form(""),
        budget_amount: str = Form(""),
        bid_document_status: str = Form("not_started"),
        has_defense: str = Form("unknown"),
        defense_presenter: str = Form(""),
        invalidation_risks: str = Form(""),
        submission_notes: str = Form(""),
        seal_notes: str = Form(""),
        deposit_amount: str = Form(""),
        signup_deadline: str = Form(""),
        document_sale_deadline: str = Form(""),
        clarification_deadline: str = Form(""),
        site_visit_time: str = Form(""),
        deposit_deadline: str = Form(""),
        bid_datetime: str = Form(""),
        submission_datetime: str = Form(""),
        bid_location: str = Form(""),
        file_fee: str = Form(""),
        payment_info: str = Form(""),
        notes: str = Form(""),
    ):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        form_data = {
            "name": name,
            "short_name": short_name,
            "tender_code": tender_code,
            "buyer": buyer,
            "project_type": project_type,
            "bid_mode": bid_mode,
            "status": status,
            "owner_name": owner_name,
            "agency": agency,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "location": location,
            "service_scope": service_scope,
            "contract_term": contract_term,
            "budget_amount": budget_amount,
            "bid_document_status": bid_document_status,
            "has_defense": has_defense,
            "defense_presenter": defense_presenter,
            "invalidation_risks": invalidation_risks,
            "submission_notes": submission_notes,
            "seal_notes": seal_notes,
            "deposit_amount": deposit_amount,
            "signup_deadline": signup_deadline,
            "document_sale_deadline": document_sale_deadline,
            "clarification_deadline": clarification_deadline,
            "site_visit_time": site_visit_time,
            "deposit_deadline": deposit_deadline,
            "bid_datetime": bid_datetime,
            "submission_datetime": submission_datetime,
            "bid_location": bid_location,
            "file_fee": file_fee,
            "payment_info": payment_info,
            "notes": notes,
        }
        validate_project_form(form_data)
        with session_scope() as session:
            project = Project(name=name.strip(), bid_mode=bid_mode.strip() or "self", status=status.strip() or "tracking")
            apply_project_form(project, form_data)
            session.add(project)
            session.flush()
            db_user = session.get(User, user.id)
            if project.status in TERMINAL_PROJECT_STATUSES:
                project_name = project.name
                final_status = project.status
                add_audit_log(
                    session,
                    actor=db_user,
                    action="create_project",
                    entity_type="project",
                    entity_id=project.id,
                    project_name=project.name,
                    detail=f"手动创建后直接归档，招标编号：{project.tender_code or '-'}",
                )
                add_audit_log(
                    session,
                    actor=db_user,
                    action="archive_project",
                    entity_type="project",
                    entity_id=project.id,
                    project_name=project.name,
                    detail=f"归档状态：{final_status}",
                )
                archive_project(session, project, final_status)
                return redirect_archives_with_notice(build_archive_notice(project_name, final_status))
            sync_system_milestones(session, project, user.display_name)
            add_audit_log(session, actor=db_user, action="create_project", entity_type="project", entity_id=project.id, project_name=project.name, detail=f"招标编号：{project.tender_code or '-'}")
            project_id = project.id
        return RedirectResponse(f"/projects/{project_id}", status_code=302)

    @app.post("/imports/ledger")
    async def import_ledger(request: Request, ledger_file: UploadFile = File(...)):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user

        safe_name = Path(ledger_file.filename or "ledger").name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".xlsx", ".csv"}:
            raise ValueError("当前仅支持导入 .xlsx 或 .csv 台账。")

        tmp_path = TMP_DIR / f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-ledger-{safe_name}"
        with tmp_path.open("wb") as handle:
            shutil.copyfileobj(ledger_file.file, handle)

        try:
            rows = read_ledger_rows(tmp_path, safe_name)
            if not rows:
                raise ValueError("台账中没有识别到可导入的项目行。")

            created_count = 0
            updated_count = 0
            archived_count = 0
            skipped_count = 0
            with session_scope() as session:
                db_user = session.get(User, user.id)
                for form_data in rows:
                    validate_project_form(form_data)
                    project = None
                    tender_code = form_data.get("tender_code", "").strip()
                    if tender_code:
                        project = session.query(Project).filter(Project.tender_code == tender_code).first()
                    if project is None and form_data.get("name", "").strip():
                        project = session.query(Project).filter(Project.name == form_data["name"].strip()).first()

                    if project is None:
                        project = Project(
                            name=form_data["name"].strip(),
                            bid_mode=form_data.get("bid_mode", "self").strip() or "self",
                            status=form_data.get("status", "tracking").strip() or "tracking",
                        )
                        apply_project_form(project, form_data)
                        session.add(project)
                        session.flush()
                        if project.status in TERMINAL_PROJECT_STATUSES:
                            archived_count += 1
                            add_audit_log(
                                session,
                                actor=db_user,
                                action="create_project",
                                entity_type="project",
                                entity_id=project.id,
                                project_name=project.name,
                                detail=f"台账导入后直接归档：{safe_name}",
                            )
                            add_audit_log(
                                session,
                                actor=db_user,
                                action="archive_project",
                                entity_type="project",
                                entity_id=project.id,
                                project_name=project.name,
                                detail=f"归档状态：{project.status}；来源：{safe_name}",
                            )
                            archive_project(session, project, project.status)
                            continue
                        sync_system_milestones(session, project, user.display_name)
                        created_count += 1
                        add_audit_log(
                            session,
                            actor=db_user,
                            action="create_project",
                            entity_type="project",
                            entity_id=project.id,
                            project_name=project.name,
                            detail=f"台账导入：{safe_name}",
                        )
                        continue

                    before_snapshot = (
                        project.status,
                        project.bid_datetime,
                        project.deposit_deadline,
                        project.signup_deadline,
                        project.submission_datetime,
                        project.notes or "",
                    )
                    apply_project_form(project, form_data)
                    if project.status in TERMINAL_PROJECT_STATUSES:
                        archived_count += 1
                        add_audit_log(
                            session,
                            actor=db_user,
                            action="archive_project",
                            entity_type="project",
                            entity_id=project.id,
                            project_name=project.name,
                            detail=f"归档状态：{project.status}；来源：{safe_name}",
                        )
                        archive_project(session, project, project.status)
                        continue
                    sync_system_milestones(session, project, user.display_name)
                    after_snapshot = (
                        project.status,
                        project.bid_datetime,
                        project.deposit_deadline,
                        project.signup_deadline,
                        project.submission_datetime,
                        project.notes or "",
                    )
                    if before_snapshot == after_snapshot:
                        skipped_count += 1
                    else:
                        updated_count += 1
                        add_audit_log(
                            session,
                            actor=db_user,
                            action="edit_project",
                            entity_type="project",
                            entity_id=project.id,
                            project_name=project.name,
                            detail=f"台账导入更新：{safe_name}",
                        )
            return redirect_home_with_notice(
                f"台账导入完成：新增 {created_count} 个，更新 {updated_count} 个，归档 {archived_count} 个，跳过 {skipped_count} 个。"
            )
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    def project_detail(project_id: int, request: Request):
        current_user = require_user(request)
        if isinstance(current_user, RedirectResponse):
            return RedirectResponse("/login", status_code=302)
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                return RedirectResponse(get_redirect_path_for_user(current_user), status_code=302)
            runtime_status = get_runtime_system_status(session)
            files = session.query(ProjectFile).filter(ProjectFile.project_id == project.id).order_by(ProjectFile.created_at.desc()).all()
            file_lookup = build_project_file_lookup(files)
            file_entries = serialize_project_file_entries(files, project.id)
            pending_reviews = (
                session.query(ExtractionJob)
                .options(joinedload(ExtractionJob.project_file))
                .filter(
                    ExtractionJob.status == "pending_review",
                    or_(ExtractionJob.project_id == project.id, ExtractionJob.matched_project_id == project.id),
                )
                .order_by(ExtractionJob.created_at.desc())
                .all()
            )
            pending_review_entries = serialize_pending_review_entries(pending_reviews, project.id)
            raw_messages = session.query(ProjectMessage).filter(ProjectMessage.project_id == project.id).order_by(ProjectMessage.created_at.desc()).all()
            messages = serialize_project_messages(raw_messages, file_lookup=file_lookup, project_id=project.id)
            milestones = session.query(ProjectMilestone).filter(ProjectMilestone.project_id == project.id).order_by(ProjectMilestone.due_at.asc(), ProjectMilestone.created_at.asc()).all()
            followups = session.query(ProjectFollowup).filter(ProjectFollowup.project_id == project.id).order_by(ProjectFollowup.created_at.desc()).all()
            requirements = session.query(ProjectRequirement).filter(ProjectRequirement.project_id == project.id).order_by(ProjectRequirement.category.asc(), ProjectRequirement.created_at.asc()).all()
            extraction_fields = (
                session.query(ExtractionField)
                .join(ExtractionJob, ExtractionField.extraction_job_id == ExtractionJob.id)
                .filter(ExtractionJob.project_id == project.id, ExtractionJob.status == "confirmed")
                .order_by(ExtractionJob.confirmed_at.desc(), ExtractionField.id.asc())
                .all()
            )
            review_audit_fields = annotate_confirmed_review_fields(extraction_fields)
            next_actions = build_project_next_actions(project, milestones)
            execution_status_items = build_execution_status_items(project, milestones)
            grouped_requirements = group_requirements(requirements)
            public_brief = build_public_project_brief(project, milestones, requirements)
            project_checklists = build_project_checklists(project, milestones, requirements, followups)
            project_dirs = build_project_subdirs(project.id)
            cache_files = []
            for section in ("text", "parse"):
                path = project_dirs[section]
                if not path.exists():
                    continue
                for child in sorted(path.iterdir()):
                    if child.is_file():
                        cache_files.append({"section": section, "name": child.name, "size_kb": round(child.stat().st_size / 1024, 1)})
        return render_template(
            "app_project_detail.html",
            request,
            app_section="workspace_projects" if current_user else "public_board",
            project=project,
            dynamic_project=serialize_project_detail(project),
            runtime_status=runtime_status,
            files=file_entries,
            pending_reviews=pending_review_entries,
            messages=messages,
            milestones=milestones,
            execution_status_items=execution_status_items,
            public_brief=public_brief,
            followups=followups,
            requirements=requirements,
            grouped_requirements=grouped_requirements,
            cache_files=cache_files,
            next_actions=next_actions,
            review_audit_fields=review_audit_fields,
            project_checklists=project_checklists,
            error=request.query_params.get("error"),
            notice=request.query_params.get("notice"),
        )

    @app.get("/projects/{project_id}/dynamic-editor", response_class=HTMLResponse)
    def dynamic_project_editor(project_id: int, request: Request):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        with session_scope() as session:
            project = session.get(Project, project_id)
            if not project:
                raise HTTPException(status_code=404, detail="项目不存在")
            dynamic_project = serialize_project_detail(project)
            content_json = json.dumps(dynamic_project["content"], ensure_ascii=False, indent=2)
        return render_template(
            "app_dynamic_editor.html",
            request,
            project=dynamic_project,
            content_json=content_json,
            error=request.query_params.get("error"),
            notice=request.query_params.get("notice"),
            app_section="workspace_projects",
        )

    @app.post("/projects/{project_id}/dynamic-editor")
    async def save_dynamic_project_editor(
        project_id: int,
        request: Request,
        title: str = Form(...),
        status: str = Form(...),
        owner: str = Form(""),
        summary: str = Form(""),
        content_json: str = Form(...),
        expected_version: int = Form(...),
    ):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        try:
            content = json.loads(content_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"动态内容不是有效 JSON：第 {exc.lineno} 行第 {exc.colno} 列。") from exc
        payload = {"title": title, "status": status, "owner": owner, "summary": summary, "schema_version": "1.0", "content": content}
        try:
            normalized = validate_project_payload(payload)
        except SchemaValidationError as exc:
            details = "；".join(f"{item['path']}：{item['message']}" for item in exc.errors[:5])
            raise ValueError(f"动态内容校验失败：{details}") from exc
        with session_scope() as session:
            project = session.get(Project, project_id)
            if not project:
                raise HTTPException(status_code=404, detail="项目不存在")
            if (project.content_version or 1) != expected_version:
                raise ValueError("项目已被其他操作更新，请刷新页面后重新编辑。")
            project.name = normalized["title"]
            project.status = normalized["status"]
            project.owner_name = normalized.get("owner") or None
            project.summary = normalized.get("summary") or None
            project.dynamic_content = json.dumps(normalized["content"], ensure_ascii=False)
            project.schema_version = normalized["schema_version"]
            project.content_version = (project.content_version or 1) + 1
            project.updated_at = datetime.utcnow()
            db_user = session.get(User, user.id)
            session.add(
                ProjectContentVersion(
                    project_id=project.id,
                    version=project.content_version,
                    schema_version=project.schema_version,
                    title=project.name,
                    summary=project.summary,
                    content_json=project.dynamic_content,
                    change_summary="网站动态编辑器更新",
                    actor_name=db_user.display_name if db_user else user.display_name,
                    request_id=request.state.request_id,
                )
            )
            add_audit_log(
                session,
                actor=db_user,
                action="edit_dynamic_project",
                entity_type="project",
                entity_id=project.id,
                project_name=project.name,
                detail=f"动态内容版本更新为 {project.content_version}；请求 ID：{request.state.request_id}",
            )
        return redirect_with_message(f"/projects/{project_id}", "动态项目内容已保存。")

    @app.post("/projects/{project_id}/edit")
    async def edit_project(
        project_id: int,
        request: Request,
        name: str = Form(...),
        short_name: str = Form(""),
        tender_code: str = Form(""),
        buyer: str = Form(""),
        project_type: str = Form(""),
        bid_mode: str = Form("self"),
        status: str = Form("tracking"),
        owner_name: str = Form(""),
        agency: str = Form(""),
        contact_name: str = Form(""),
        contact_phone: str = Form(""),
        location: str = Form(""),
        service_scope: str = Form(""),
        contract_term: str = Form(""),
        budget_amount: str = Form(""),
        bid_document_status: str = Form("not_started"),
        has_defense: str = Form("unknown"),
        defense_presenter: str = Form(""),
        invalidation_risks: str = Form(""),
        submission_notes: str = Form(""),
        seal_notes: str = Form(""),
        deposit_amount: str = Form(""),
        signup_deadline: str = Form(""),
        document_sale_deadline: str = Form(""),
        clarification_deadline: str = Form(""),
        site_visit_time: str = Form(""),
        deposit_deadline: str = Form(""),
        bid_datetime: str = Form(""),
        submission_datetime: str = Form(""),
        bid_location: str = Form(""),
        file_fee: str = Form(""),
        payment_info: str = Form(""),
        notes: str = Form(""),
    ):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        form_data = {
            "name": name,
            "short_name": short_name,
            "tender_code": tender_code,
            "buyer": buyer,
            "project_type": project_type,
            "bid_mode": bid_mode,
            "status": status,
            "owner_name": owner_name,
            "agency": agency,
            "contact_name": contact_name,
            "contact_phone": contact_phone,
            "location": location,
            "service_scope": service_scope,
            "contract_term": contract_term,
            "budget_amount": budget_amount,
            "bid_document_status": bid_document_status,
            "has_defense": has_defense,
            "defense_presenter": defense_presenter,
            "invalidation_risks": invalidation_risks,
            "submission_notes": submission_notes,
            "seal_notes": seal_notes,
            "deposit_amount": deposit_amount,
            "signup_deadline": signup_deadline,
            "document_sale_deadline": document_sale_deadline,
            "clarification_deadline": clarification_deadline,
            "site_visit_time": site_visit_time,
            "deposit_deadline": deposit_deadline,
            "bid_datetime": bid_datetime,
            "submission_datetime": submission_datetime,
            "bid_location": bid_location,
            "file_fee": file_fee,
            "payment_info": payment_info,
            "notes": notes,
        }
        validate_project_form(form_data)
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                return RedirectResponse("/", status_code=302)
            apply_project_form(project, form_data)
            db_user = session.get(User, user.id)
            if project.status in TERMINAL_PROJECT_STATUSES:
                project_name = project.name
                final_status = project.status
                add_audit_log(
                    session,
                    actor=db_user,
                    action="archive_project",
                    entity_type="project",
                    entity_id=project.id,
                    project_name=project.name,
                    detail=f"归档状态：{final_status}（编辑项目时触发）",
                )
                archive_project(session, project, final_status)
                return redirect_archives_with_notice(build_archive_notice(project_name, final_status))
            sync_system_milestones(session, project, user.display_name)
            add_audit_log(session, actor=db_user, action="edit_project", entity_type="project", entity_id=project.id, project_name=project.name, detail=f"状态：{project.status}")
        return RedirectResponse(f"/projects/{project_id}", status_code=302)

    @app.post("/projects/{project_id}/milestones")
    async def create_project_milestone(
        project_id: int,
        request: Request,
        milestone_type: str = Form("custom"),
        title: str = Form(...),
        due_at: str = Form(""),
        status: str = Form("pending"),
        source_quote: str = Form(""),
    ):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        if milestone_type not in MILESTONE_TYPE_LABELS:
            raise ValueError("节点类型不支持。")
        if status not in MILESTONE_STATUS_LABELS:
            raise ValueError("节点状态不支持。")
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                return RedirectResponse("/", status_code=302)
            session.add(
                ProjectMilestone(
                    project_id=project_id,
                    milestone_type=milestone_type,
                    title=title.strip(),
                    due_at=to_dt(due_at.strip()) if due_at.strip() else None,
                    status=status,
                    source_quote=source_quote.strip() or None,
                    created_by=user.display_name,
                )
            )
            db_user = session.get(User, user.id)
            add_audit_log(session, actor=db_user, action="create_milestone", entity_type="project", entity_id=project.id, project_name=project.name, detail=f"节点：{title.strip()}")
        return RedirectResponse(f"/projects/{project_id}", status_code=302)

    @app.post("/projects/{project_id}/milestones/{milestone_id}/edit")
    async def edit_project_milestone(
        project_id: int,
        milestone_id: int,
        request: Request,
        title: str = Form(...),
        milestone_type: str = Form("custom"),
        due_at: str = Form(""),
        status: str = Form("pending"),
        source_quote: str = Form(""),
    ):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        if milestone_type not in MILESTONE_TYPE_LABELS:
            raise ValueError("节点类型不支持。")
        if status not in MILESTONE_STATUS_LABELS:
            raise ValueError("节点状态不支持。")
        with session_scope() as session:
            milestone = session.get(ProjectMilestone, milestone_id)
            if milestone is None or milestone.project_id != project_id:
                raise ValueError("节点不存在。")
            project = session.get(Project, project_id)
            milestone.title = title.strip()
            milestone.milestone_type = milestone_type
            milestone.due_at = to_dt(due_at.strip()) if due_at.strip() else None
            milestone.status = status
            milestone.source_quote = source_quote.strip() or None
            milestone.updated_at = datetime.utcnow()
            db_user = session.get(User, user.id)
            add_audit_log(session, actor=db_user, action="edit_milestone", entity_type="project", entity_id=project_id, project_name=project.name if project else None, detail=f"节点：{milestone.title}")
        return RedirectResponse(f"/projects/{project_id}", status_code=302)

    @app.post("/projects/{project_id}/execution/{milestone_id}")
    async def update_project_execution_status(
        project_id: int,
        milestone_id: int,
        request: Request,
        status: str = Form(...),
    ):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        if status not in MILESTONE_STATUS_LABELS:
            raise ValueError("执行状态不支持。")
        with session_scope() as session:
            milestone = session.get(ProjectMilestone, milestone_id)
            if milestone is None or milestone.project_id != project_id:
                raise ValueError("执行事项不存在。")
            project = session.get(Project, project_id)
            if project is None:
                return RedirectResponse(get_redirect_path_for_user(user), status_code=302)
            milestone.status = status
            milestone.updated_at = datetime.utcnow()
            db_user = session.get(User, user.id)
            detail = f"{milestone.title}：{get_milestone_status_label(milestone.milestone_type, status)}"
            add_audit_log(
                session,
                actor=db_user,
                action="edit_milestone",
                entity_type="project",
                entity_id=project.id,
                project_name=project.name,
                detail=detail,
            )
        return RedirectResponse(f"/projects/{project_id}", status_code=302)

    @app.post("/projects/{project_id}/followups")
    async def create_project_followup(project_id: int, request: Request, content: str = Form(...)):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        if not content.strip():
            raise ValueError("跟进记录不能为空。")
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                return RedirectResponse("/", status_code=302)
            session.add(ProjectFollowup(project_id=project_id, content=content.strip(), created_by=user.display_name))
            db_user = session.get(User, user.id)
            add_audit_log(session, actor=db_user, action="create_followup", entity_type="project", entity_id=project.id, project_name=project.name, detail=content.strip()[:120])
        return RedirectResponse(f"/projects/{project_id}", status_code=302)

    @app.get("/projects/{project_id}/files/{file_id}")
    def download_project_file(project_id: int, file_id: int, request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return user
        with session_scope() as session:
            file_record = session.get(ProjectFile, file_id)
            if file_record is None or file_record.project_id != project_id:
                raise HTTPException(status_code=404, detail="文件不存在。")
            file_path = Path(file_record.storage_path)
            if file_record.deleted_at or not file_path.exists():
                raise HTTPException(status_code=404, detail="文件已被清理。")
            return FileResponse(file_path, filename=file_record.original_name)

    @app.post("/projects/{project_id}/upload")
    async def upload_project_document(project_id: int, request: Request, source_file: UploadFile = File(...)):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user

        safe_name = Path(source_file.filename or "upload").name
        tmp_path = TMP_DIR / build_upload_storage_name(safe_name)
        with tmp_path.open("wb") as handle:
            shutil.copyfileobj(source_file.file, handle)

        try:
            with session_scope() as session:
                project = session.get(Project, project_id)
                if project is None:
                    tmp_path.unlink(missing_ok=True)
                    return RedirectResponse("/", status_code=302)
                if project.status in TERMINAL_PROJECT_STATUSES:
                    tmp_path.unlink(missing_ok=True)
                    return upload_error_response(request, f"/projects/{project_id}", "已归档项目不能继续上传补充文件。")
                ocr_settings = get_paddleocr_settings(session)
                api_key = get_deepseek_api_key(session)
                _, job = create_pending_review_job(
                    session,
                    temp_path=tmp_path,
                    original_name=safe_name,
                    content_type=source_file.content_type,
                    created_by=user.display_name,
                    api_key=api_key,
                    ocr_settings=ocr_settings,
                    matched_project_id=project.id,
                    pending_base_id=f"pending/project-{project.id}/{datetime.utcnow().strftime('%Y%m')}",
                )
                db_user = session.get(User, user.id)
                add_audit_log(
                    session,
                    actor=db_user,
                    action="upload_document",
                    entity_type="extraction_job",
                    entity_id=job.id,
                    project_name=project.name,
                    detail=f"项目补充文件：{safe_name}",
                )
        except Exception as exc:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            return upload_error_response(request, f"/projects/{project_id}", format_upload_exception(exc))

        redirect_url = f"/reviews/{job.id}?notice={quote('补充文件已进入解析确认，确认后才会纳入项目正式资料。')}"
        return upload_success_response(request, redirect_url, "补充文件解析成功，已进入解析确认。")

    @app.post("/projects/{project_id}/ask")
    async def ask_project(project_id: int, request: Request, question: str = Form(...)):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return user
        normalized_question = repair_mojibake_text(question.strip())
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                return RedirectResponse("/", status_code=302)
            api_key = get_deepseek_api_key(session)
            files = (
                session.query(ProjectFile)
                .filter(ProjectFile.project_id == project.id, ProjectFile.extracted_text.is_not(None))
                .all()
            )
            requirements = session.query(ProjectRequirement).filter(ProjectRequirement.project_id == project.id).order_by(ProjectRequirement.category.asc(), ProjectRequirement.created_at.asc()).all()
            context = build_project_context(project.name, STATUS_LABELS.get(project.status, project.status), files, requirements)
            answer, citations = answer_project_question(normalized_question, context, api_key)
            session.add(ProjectMessage(project_id=project.id, question=normalized_question, answer=answer, citations=citations, created_by=user.display_name))
            db_user = session.get(User, user.id)
            add_audit_log(session, actor=db_user, action="ask_project", entity_type="project", entity_id=project.id, project_name=project.name, detail=normalized_question[:120])
        return RedirectResponse(f"/projects/{project_id}", status_code=302)

    @app.post("/projects/{project_id}/status")
    async def update_project_status(project_id: int, request: Request, status: str = Form(...)):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                return RedirectResponse("/", status_code=302)
            if status not in STATUS_LABELS:
                raise ValueError("不支持的项目状态。")
            db_user = session.get(User, user.id)
            if status in TERMINAL_PROJECT_STATUSES:
                project_name = project.name
                add_audit_log(session, actor=db_user, action="archive_project", entity_type="project", entity_id=project.id, project_name=project.name, detail=f"归档状态：{status}")
                archive_project(session, project, status)
                return redirect_archives_with_notice(build_archive_notice(project_name, status))
            project.status = status
            project.updated_at = datetime.utcnow()
            add_audit_log(session, actor=db_user, action="update_project_status", entity_type="project", entity_id=project.id, project_name=project.name, detail=f"状态更新为：{status}")
        return RedirectResponse(f"/projects/{project_id}", status_code=302)

    @app.patch("/api/projects/{project_id}/status")
    async def update_project_status_api(project_id: int, request: Request):
        user = get_current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录。")
        if not can_edit_projects(user):
            raise HTTPException(status_code=403, detail="当前账号没有项目编辑权限。")
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="请求体必须是 JSON。")
        if not isinstance(body, dict) or not isinstance(body.get("status"), str):
            raise HTTPException(status_code=422, detail="status 必须是字符串。")
        status = body["status"].strip()
        if status not in STATUS_LABELS:
            raise HTTPException(status_code=422, detail="不支持的项目状态。")
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="项目不存在。")
            project.status = status
            project.updated_at = datetime.utcnow()
            db_user = session.get(User, user.id)
            add_audit_log(session, actor=db_user, action="update_project_status", entity_type="project", entity_id=project.id, project_name=project.name, detail=f"状态更新为：{status}")
        return JSONResponse({"ok": True, "status": status, "status_label": STATUS_LABELS[status]})

    @app.get("/reviews/{job_id}", response_class=HTMLResponse)
    def review_detail(job_id: int, request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return user
        with session_scope() as session:
            job = session.get(ExtractionJob, job_id)
            if job is None:
                return RedirectResponse("/", status_code=302)
            runtime_status = get_runtime_system_status(session)
            session.refresh(job, attribute_names=["project_file"])
            fields = session.query(ExtractionField).filter(ExtractionField.extraction_job_id == job.id).all()
            candidate_project = session.get(Project, job.matched_project_id) if job.matched_project_id else None
            candidate_project_files = []
            candidate_project_milestones = []
            if candidate_project is not None:
                candidate_project_files = (
                    session.query(ProjectFile)
                    .filter(ProjectFile.project_id == candidate_project.id)
                    .order_by(ProjectFile.created_at.desc())
                    .limit(5)
                    .all()
                )
                candidate_project_milestones = (
                    session.query(ProjectMilestone)
                    .filter(ProjectMilestone.project_id == candidate_project.id)
                    .order_by(ProjectMilestone.due_at.asc(), ProjectMilestone.created_at.asc())
                    .limit(6)
                    .all()
                )
            parsed_payload = {}
            if job.raw_result:
                try:
                    parsed_payload = json.loads(job.raw_result)
                except json.JSONDecodeError:
                    parsed_payload = {}
            draft_requirements = parsed_payload.get("requirements", []) if isinstance(parsed_payload, dict) else []
            review_field_stats = annotate_review_queue_fields(fields)
            extraction_summary = (job.confidence_summary or getattr(job.project_file, "extracted_summary", None) or "").strip()
            extracted_text_preview, extracted_text_truncated = build_preview_text(getattr(job.project_file, "extracted_text", None))
        return render_template(
            "review_detail.html",
            request,
            app_section="workspace_reviews",
            job=job,
            runtime_status=runtime_status,
            fields=fields,
            candidate_project=candidate_project,
            candidate_project_files=candidate_project_files,
            candidate_project_milestones=candidate_project_milestones,
            draft_requirements=draft_requirements,
            review_field_stats=review_field_stats,
            extraction_summary=extraction_summary,
            extracted_text_preview=extracted_text_preview,
            extracted_text_truncated=extracted_text_truncated,
            error=request.query_params.get("error"),
            notice=request.query_params.get("notice"),
        )

    @app.get("/reviews/{job_id}/source")
    def download_review_source_file(job_id: int, request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return user
        with session_scope() as session:
            job = session.get(ExtractionJob, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="解析任务不存在。")
            file_record = session.get(ProjectFile, job.project_file_id)
            if file_record is None:
                raise HTTPException(status_code=404, detail="源文件不存在。")
            file_path = Path(file_record.storage_path)
            if file_record.deleted_at or not file_path.exists():
                raise HTTPException(status_code=404, detail="源文件已被清理。")
            return FileResponse(file_path, filename=file_record.original_name)

    @app.post("/reviews/{job_id}/confirm")
    async def review_confirm(job_id: int, request: Request, action: str = Form(...)):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        with session_scope() as session:
            job = session.get(ExtractionJob, job_id)
            if job is None:
                return RedirectResponse("/", status_code=302)
            file_record = session.get(ProjectFile, job.project_file_id)
            fields = session.query(ExtractionField).filter(ExtractionField.extraction_job_id == job.id).all()
            review_field_stats = annotate_review_queue_fields(fields)
            approved_data: dict[str, str] = {}
            skipped_fields: list[str] = []
            for field in fields:
                value = field.review_value
                if not value:
                    field.status = "empty"
                    continue
                if field.review_queue_status in {"source_ready", "manual"}:
                    approved_data[field.field_key] = value
                else:
                    skipped_fields.append(field.field_label)
            raw_payload = {}
            if job.raw_result:
                try:
                    raw_payload = json.loads(job.raw_result)
                except json.JSONDecodeError:
                    raw_payload = {}
            requirement_payloads = raw_payload.get("requirements", []) if isinstance(raw_payload, dict) else []
            requirement_added_count = 0
            requirement_skipped_count = 0
            requirement_duplicate_count = 0

            if action == "reject":
                job.status = "rejected"
                job.confirmed_by = user.display_name
                job.confirmed_at = datetime.utcnow()
                db_user = session.get(User, user.id)
                add_audit_log(session, actor=db_user, action="reject_review", entity_type="extraction_job", entity_id=job.id, detail=f"文件：{file_record.original_name if file_record else '-'}")
                return redirect_with_message(WORKSPACE_REVIEWS_PATH, "已驳回解析结果。")

            if action == "update" and job.matched_project_id:
                project = session.get(Project, job.matched_project_id)
            else:
                bootstrap_name = approved_data.get("name") or (file_record.original_name if file_record else "未命名项目")
                project = Project(name=bootstrap_name, status="tracking", bid_mode=(approved_data.get("bid_mode") or "self"))
                session.add(project)
                session.flush()

            if action == "update":
                approved_data = sanitize_review_update_data(project, approved_data, fields)
            apply_confirmed_review_data(project, approved_data)
            if not project.name:
                project.name = file_record.original_name if file_record else "未命名项目"
                project.short_name = project.name[:18]

            file_record.project_id = project.id
            job.project_id = project.id
            job.status = "confirmed"
            job.confirmed_by = user.display_name
            job.confirmed_at = datetime.utcnow()

            file_path = Path(file_record.storage_path)
            original_cache_root = file_path.parent.parent if file_path.parent.name == "source" else None
            cache_stem = file_path.stem
            project_dirs = build_project_subdirs(project.id)
            for path in project_dirs.values():
                path.mkdir(parents=True, exist_ok=True)
            if file_path.exists() and "pending" in file_path.parts:
                target_path = project_dirs["source"] / file_path.name
                shutil.move(str(file_path), target_path)
                file_record.storage_path = str(target_path)

            project_cache_paths = build_cache_artifact_paths(project_dirs["root"], cache_stem)
            if file_record.extracted_text:
                project_cache_paths["text_path"].write_text(file_record.extracted_text, encoding="utf-8")
            project_cache_paths["parse_path"].write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            if job.confidence_summary:
                project_cache_paths["summary_path"].write_text(job.confidence_summary, encoding="utf-8")

            if original_cache_root is not None:
                pending_cache_paths = build_cache_artifact_paths(original_cache_root, cache_stem)
                for stale_path in pending_cache_paths.values():
                    stale_path.unlink(missing_ok=True)

            for field in fields:
                if not field.final_value:
                    field.final_value = field.extracted_value
                if not field.review_value:
                    field.status = "empty"
                elif field.review_queue_status == "source_ready":
                    field.status = "confirmed"
                elif field.review_queue_status == "manual":
                    field.status = "manual"
                else:
                    field.status = "skipped_no_source"

            if action == "update":
                merged_requirement_payloads: list[dict[str, object]] = []
                seen_requirement_keys: set[tuple[str, str, str, str, str]] = set()
                for existing in session.query(ProjectRequirement).filter(ProjectRequirement.project_id == project.id).all():
                    signature = (
                        existing.category,
                        existing.title,
                        existing.content,
                        existing.source_location or "",
                        existing.source_quote or "",
                    )
                    if signature in seen_requirement_keys:
                        continue
                    seen_requirement_keys.add(signature)
                    merged_requirement_payloads.append(
                        {
                            "category": existing.category,
                            "title": existing.title,
                            "content": existing.content,
                            "importance": existing.importance,
                            "source_location": existing.source_location or "",
                            "source_quote": existing.source_quote or "",
                        }
                    )
                for item in requirement_payloads:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "") or "").strip()
                    content = str(item.get("content", "") or "").strip()
                    category = str(item.get("category", "") or "other").strip() or "other"
                    source_location = str(item.get("source_location", "") or "").strip()
                    source_quote = str(item.get("source_quote", "") or "").strip()
                    signature = (category, title, content, source_location, source_quote)
                    if signature in seen_requirement_keys:
                        requirement_duplicate_count += 1
                        continue
                    seen_requirement_keys.add(signature)
                    merged_requirement_payloads.append(item)
                requirement_payloads = merged_requirement_payloads

            session.query(ProjectRequirement).filter(ProjectRequirement.project_id == project.id).delete()
            for item in requirement_payloads:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "") or "").strip()
                content = str(item.get("content", "") or "").strip()
                if not title and not content:
                    continue
                if not requirement_has_source(item):
                    requirement_skipped_count += 1
                    continue
                category = str(item.get("category", "") or "other").strip() or "other"
                if category not in REQUIREMENT_CATEGORY_LABELS:
                    category = "other"
                importance = str(item.get("importance", "") or "medium").strip() or "medium"
                if importance not in REQUIREMENT_IMPORTANCE_LABELS:
                    importance = "medium"
                requirement_added_count += 1
                session.add(
                    ProjectRequirement(
                        project_id=project.id,
                        category=category,
                        title=title or content[:80] or "未命名要求",
                        content=content or title,
                        importance=importance,
                        source_location=str(item.get("source_location", "") or "").strip() or None,
                        source_quote=str(item.get("source_quote", "") or "").strip() or None,
                        created_by=user.display_name,
                    )
                )

            sync_system_milestones(session, project, user.display_name)

            db_user = session.get(User, user.id)
            detail_parts = [
                f"解析单：{job.id}",
                f"有来源 {review_field_stats.get('source_ready', 0)} 项",
                f"人工填写 {review_field_stats.get('manual', 0)} 项",
                f"要点入库 {requirement_added_count} 条",
            ]
            if skipped_fields:
                detail_parts.append(f"缺少来源未入库 {len(skipped_fields)} 项")
            if requirement_skipped_count:
                detail_parts.append(f"缺少来源未入库要点 {requirement_skipped_count} 条")
            if requirement_duplicate_count:
                detail_parts.append(f"重复要点未重复入库 {requirement_duplicate_count} 条")
            add_audit_log(session, actor=db_user, action="confirm_review", entity_type="project", entity_id=project.id, project_name=project.name, detail="；".join(detail_parts))

        return RedirectResponse(f"/projects/{project.id}", status_code=302)

    @app.post("/reviews/{job_id}/field")
    async def update_review_field(job_id: int, request: Request, field_id: int = Form(...), final_value: str = Form("")):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user
        with session_scope() as session:
            field = session.get(ExtractionField, field_id)
            if field is not None and field.extraction_job_id == job_id:
                field.final_value = final_value.strip()
                field.status = "manual"
        return RedirectResponse(f"/reviews/{job_id}", status_code=302)

    @app.post("/upload")
    async def upload_document(request: Request, source_file: UploadFile = File(...)):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return user

        safe_name = Path(source_file.filename or "upload").name
        tmp_path = TMP_DIR / build_upload_storage_name(safe_name)
        with tmp_path.open("wb") as handle:
            shutil.copyfileobj(source_file.file, handle)

        try:
            with session_scope() as session:
                ocr_settings = get_paddleocr_settings(session)
                api_key = get_deepseek_api_key(session)
                _, job = create_pending_review_job(
                    session,
                    temp_path=tmp_path,
                    original_name=safe_name,
                    content_type=source_file.content_type,
                    created_by=user.display_name,
                    api_key=api_key,
                    ocr_settings=ocr_settings,
                )
                db_user = session.get(User, user.id)
                add_audit_log(session, actor=db_user, action="upload_document", entity_type="extraction_job", entity_id=job.id, detail=f"文件：{safe_name}")
        except Exception as exc:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            return upload_error_response(request, WORKSPACE_PROJECTS_PATH, format_upload_exception(exc))

        return upload_success_response(request, f"/reviews/{job.id}?notice={quote('解析成功，已生成待确认结果。')}", "解析成功，已生成待确认结果。")

    @app.get("/users", response_class=HTMLResponse)
    def users_page(request: Request):
        user = require_admin(request)
        if isinstance(user, RedirectResponse):
            return user
        return RedirectResponse(build_message_redirect_url(WORKSPACE_SETTINGS_PATH, fragment="members"), status_code=302)

    @app.get("/archives", response_class=HTMLResponse)
    def archives_page(request: Request):
        return RedirectResponse(WORKSPACE_ARCHIVES_PATH if get_current_user(request) else "/login", status_code=302)

    @app.post("/archives/{archive_id}/delete")
    def delete_archive_record(archive_id: int, request: Request):
        current = require_admin(request)
        if isinstance(current, RedirectResponse):
            return current
        with session_scope() as session:
            archived = session.get(ArchivedProject, archive_id)
            if archived is None:
                raise ValueError("归档记录不存在。")
            project_name = archived.project_name
            db_current = session.get(User, current.id)
            add_audit_log(session, actor=db_current, action="delete_archive", entity_type="archived_project", entity_id=archived.id, project_name=project_name, detail="管理员永久删除最小归档记录")
            session.delete(archived)
        return RedirectResponse(f"{WORKSPACE_ARCHIVES_PATH}?notice={quote('归档记录已永久删除。')}", status_code=302)

    @app.get("/audit-logs", response_class=HTMLResponse)
    def audit_logs_page(request: Request):
        user = require_admin(request)
        if isinstance(user, RedirectResponse):
            return user
        return RedirectResponse(f"{WORKSPACE_SETTINGS_PATH}#audit", status_code=302)

    @app.post("/users/create")
    async def create_user(request: Request, username: str = Form(...), display_name: str = Form(...), password: str = Form(...), role: str = Form("viewer")):
        current = require_admin(request)
        if isinstance(current, RedirectResponse):
            return current
        normalized_role = normalize_role(role)
        if len(password.strip()) < 6:
            raise ValueError("密码至少 6 位。")
        with session_scope() as session:
            exists = session.query(User).filter(User.username == username.strip()).first()
            if exists:
                raise ValueError("用户名已存在。")
            new_user = User(username=username.strip(), display_name=display_name.strip(), password_hash=hash_password(password.strip()), role=normalized_role)
            session.add(new_user)
            session.flush()
            db_current = session.get(User, current.id)
            add_audit_log(session, actor=db_current, action="create_user", entity_type="user", entity_id=new_user.id, detail=f"用户名：{new_user.username}，角色：{new_user.role}")
        return redirect_settings_with_notice("用户已创建。", fragment="members")

    @app.post("/users/{user_id}/edit")
    async def edit_user(user_id: int, request: Request, display_name: str = Form(...), role: str = Form("viewer"), password: str = Form("")):
        current = require_admin(request)
        if isinstance(current, RedirectResponse):
            return current
        normalized_role = normalize_role(role)
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None:
                raise ValueError("用户不存在。")
            user.display_name = display_name.strip()
            user.role = normalized_role
            if password.strip():
                if len(password.strip()) < 6:
                    raise ValueError("密码至少 6 位。")
                user.password_hash = hash_password(password.strip())
            db_current = session.get(User, current.id)
            add_audit_log(session, actor=db_current, action="edit_user", entity_type="user", entity_id=user.id, detail=f"用户名：{user.username}，角色：{user.role}")
        return redirect_settings_with_notice("用户已更新。", fragment="members")

    @app.post("/users/{user_id}/delete")
    def delete_user(user_id: int, request: Request):
        current = require_admin(request)
        if isinstance(current, RedirectResponse):
            return current
        if current.id == user_id:
            raise ValueError("不能删除当前登录账号。")
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None:
                raise ValueError("用户不存在。")
            username = user.username
            db_current = session.get(User, current.id)
            add_audit_log(session, actor=db_current, action="delete_user", entity_type="user", entity_id=user.id, detail=f"用户名：{username}")
            session.delete(user)
        return redirect_settings_with_notice("用户已删除。", fragment="members")

    @app.get("/api/reminders")
    def reminders_api(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return {"items": []}
        with session_scope() as session:
            dashboard = build_dashboard_data(session)
            items = [serialize_reminder_item(item) for item in dashboard["all_reminders"]]
        return {"items": items, "count": len(items)}

    @app.get("/api/system/status")
    def system_status_api(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return JSONResponse({"error": "未登录"}, status_code=401)
        if not can_configure_system(user):
            return JSONResponse({"error": "没有权限"}, status_code=403)
        with session_scope() as session:
            runtime_status = get_runtime_system_status(session)
        return {
            "ai": {
                "configured": runtime_status["ai_configured"],
                "source": runtime_status["ai_source"],
                "source_label": runtime_status["ai_source_label"],
                "model": runtime_status["ai_model"],
                "base_url": runtime_status["ai_base_url"],
                "connectivity_test_available": True,
            },
            "ocr": {
                "configured": runtime_status["ocr_configured"],
                "source": runtime_status["ocr_source"],
                "source_label": runtime_status["ocr_source_label"],
                "api_url": runtime_status["ocr_api_url"],
                "timeout_seconds": runtime_status["ocr_timeout_seconds"],
                "scan_pdf_enabled": runtime_status["scan_pdf_enabled"],
            },
        }

    @app.post("/api/reminders/{reminder_key}/done")
    def mark_reminder_done(reminder_key: str, request: Request):
        user = require_project_editor(request)
        if isinstance(user, RedirectResponse):
            return JSONResponse({"error": "未登录"}, status_code=401)
        with session_scope() as session:
            dashboard = build_dashboard_data(session)
            reminder = next((item for item in dashboard["all_reminders"] if item["reminder_key"] == reminder_key), None)
            if reminder is None:
                return JSONResponse({"error": "提醒不存在"}, status_code=404)
            state = session.get(ReminderState, reminder_key)
            if state is None:
                session.add(ReminderState(reminder_key=reminder_key, project_id=reminder["project"].id, label=reminder["label"], due_at=reminder["due_at"], done_by=user.display_name))
            else:
                state.done_by = user.display_name
                state.done_at = datetime.utcnow()
            db_user = session.get(User, user.id)
            add_audit_log(session, actor=db_user, action="mark_reminder_done", entity_type="reminder", entity_id=reminder_key, project_name=reminder["project"].name, detail=reminder["label"])
        if request.headers.get("accept", "").find("text/html") >= 0:
            return RedirectResponse("/", status_code=302)
        return {"ok": True, "reminder_key": reminder_key}

    @app.post("/api/webhooks/reminders")
    def reminder_webhook_placeholder(request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return JSONResponse({"error": "未登录"}, status_code=401)
        return {"ok": True, "message": "提醒 webhook 接口已预留，后续可对接企业微信或邮件服务。"}

    @app.get("/api/projects/{project_id}/next-actions")
    def project_next_actions(project_id: int, request: Request):
        user = require_user(request)
        if isinstance(user, RedirectResponse):
            return JSONResponse({"error": "未登录"}, status_code=401)
        with session_scope() as session:
            project = session.get(Project, project_id)
            if project is None:
                return JSONResponse({"error": "项目不存在"}, status_code=404)
            milestones = session.query(ProjectMilestone).filter(ProjectMilestone.project_id == project.id).order_by(ProjectMilestone.due_at.asc()).all()
            actions = build_project_next_actions(project, milestones)
            snapshot = build_project_stage_snapshot(project, milestones)
        return {
            "project": {
                "id": project.id,
                "name": project.name,
                "status": project.status,
                "status_label": STATUS_LABELS.get(project.status, project.status),
            },
            "stage": {
                "stage_key": snapshot["stage_key"],
                "stage_label": snapshot["stage_label"],
                "next_due_at": format_datetime(snapshot["next_due"]),
                "next_label": snapshot["next_label"],
                "delta_days": snapshot["delta_days"],
                "risk_level": snapshot["risk_level"],
                "risk_reason": snapshot["risk_reason"],
            },
            "items": [serialize_next_action_item(item) for item in actions],
            "count": len(actions),
        }

    @app.get("/healthz")
    def health_check():
        return {"ok": True, "service": "bid-platform", "time": datetime.utcnow().isoformat()}

    @app.head("/healthz")
    def health_check_head():
        return JSONResponse({"ok": True, "service": "bid-platform", "time": datetime.utcnow().isoformat()})

    return app
