#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def configuration() -> tuple[str, str]:
    base_url = os.getenv("BID_PLATFORM_API_URL", "").rstrip("/")
    token = os.getenv("BID_PLATFORM_API_TOKEN", "")
    if not base_url or not token:
        raise SystemExit("BID_PLATFORM_API_URL and BID_PLATFORM_API_TOKEN are required")
    return base_url, token


def health_check() -> dict:
    api_url = os.getenv("BID_PLATFORM_API_URL", "").rstrip("/")
    parsed = urllib.parse.urlsplit(api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit("BID_PLATFORM_API_URL must be an absolute HTTP(S) URL")
    health_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/healthz", "", ""))
    request = urllib.request.Request(health_url, headers={"Accept": "application/json", "User-Agent": "Hermes-Bid-Skill"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {"ok": response.status == 200, "url": health_url, "status": response.status, "response": payload}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "url": health_url, "status": exc.code, "error": "http_error"}
    except urllib.error.URLError as exc:
        return {"ok": False, "url": health_url, "status": None, "error": "network_error", "detail": str(exc.reason)}


def read_json(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("JSON payload must be an object")
    return value


def require_confirmation(payload: dict) -> None:
    confirmation = payload.get("confirmation")
    if not isinstance(confirmation, dict) or not all(str(confirmation.get(key, "")).strip() for key in ("confirmed_by", "confirmed_at", "summary")):
        raise SystemExit("Payload must include a complete confirmation object before it can be applied")


def validated_payload(payload: dict, *, partial: bool) -> dict:
    """Attach a fresh server validation token without weakening confirmation rules."""
    require_confirmation(payload)
    candidate = dict(payload)
    candidate.pop("validation_token", None)
    suffix = "?partial=true" if partial else ""
    validation = request("POST", f"/validate/project{suffix}", payload=candidate)
    token = validation.get("validation_token")
    if not isinstance(token, str) or not token:
        raise SystemExit("Platform validation did not return a validation token")
    candidate["validation_token"] = token
    return candidate


def apply_create(payload: dict, *, idempotency_key: str) -> dict:
    return request("POST", "/projects", payload=validated_payload(payload, partial=False), headers={"Idempotency-Key": idempotency_key})


def apply_update(project_id: int, payload: dict, *, idempotency_key: str) -> dict:
    current = request("GET", f"/projects/{project_id}")
    version = current.get("version")
    if not isinstance(version, int) or version < 1:
        raise SystemExit("Platform returned an invalid project version; no update was sent")
    body = validated_payload(payload, partial=True)
    return request(
        "PATCH",
        f"/projects/{project_id}",
        payload=body,
        headers={"Idempotency-Key": idempotency_key, "If-Match": str(version)},
    )


def request(method: str, path: str, *, payload: dict | None = None, headers: dict[str, str] | None = None) -> dict:
    base_url, token = configuration()
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "X-Request-ID": f"hermes_{uuid.uuid4().hex[:16]}"}
    if body is not None:
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request_headers.update(headers or {})
    req = urllib.request.Request(f"{base_url}{path}", data=body, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"error": {"code": "http_error", "message": raw or str(exc)}}
        print(json.dumps(detail, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot connect to bidding platform: {exc.reason}") from exc


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes client for the Hejia bidding platform")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("doctor", help="Check health and authenticated API access without writing data")
    sub.add_parser("schema")
    projects = sub.add_parser("projects")
    projects.add_argument("--query", default="")
    projects.add_argument("--status", default="")
    get = sub.add_parser("get")
    get.add_argument("project_id", type=int)
    validate = sub.add_parser("validate")
    validate.add_argument("payload")
    validate.add_argument("--partial", action="store_true")
    create = sub.add_parser("create")
    create.add_argument("payload")
    create.add_argument("--idempotency-key", required=True)
    update = sub.add_parser("update")
    update.add_argument("project_id", type=int)
    update.add_argument("payload")
    update.add_argument("--version", type=int, required=True)
    update.add_argument("--idempotency-key", required=True)
    apply = sub.add_parser("apply", help="Validate and apply an already confirmed payload in one safe command")
    apply_sub = apply.add_subparsers(dest="apply_command", required=True)
    apply_create_command = apply_sub.add_parser("create")
    apply_create_command.add_argument("payload")
    apply_create_command.add_argument("--idempotency-key", required=True)
    apply_update_command = apply_sub.add_parser("update")
    apply_update_command.add_argument("project_id", type=int)
    apply_update_command.add_argument("payload")
    apply_update_command.add_argument("--idempotency-key", required=True)
    for name in ("followup", "status", "archive"):
        command = sub.add_parser(name)
        command.add_argument("project_id", type=int)
        command.add_argument("payload")
        command.add_argument("--idempotency-key", required=True)
    args = parser.parse_args()

    if args.command == "health":
        result = health_check()
        emit(result)
        if not result["ok"]:
            raise SystemExit(2)
    elif args.command == "doctor":
        health = health_check()
        if not health["ok"]:
            emit({"ok": False, "stage": "network_or_tls", "health": health})
            raise SystemExit(2)
        try:
            schema = request("GET", "/schema/project")
        except SystemExit:
            emit({"ok": False, "stage": "api_authentication", "health": health})
            raise
        emit({"ok": True, "stage": "ready", "health": health, "schema_version": schema.get("schema_version")})
    elif args.command == "schema":
        emit(request("GET", "/schema/project"))
    elif args.command == "projects":
        query = urllib.parse.urlencode({"q": args.query, "status": args.status})
        emit(request("GET", f"/projects?{query}"))
    elif args.command == "get":
        emit(request("GET", f"/projects/{args.project_id}"))
    elif args.command == "validate":
        suffix = "?partial=true" if args.partial else ""
        emit(request("POST", f"/validate/project{suffix}", payload=read_json(args.payload)))
    elif args.command == "create":
        emit(request("POST", "/projects", payload=read_json(args.payload), headers={"Idempotency-Key": args.idempotency_key}))
    elif args.command == "update":
        emit(request("PATCH", f"/projects/{args.project_id}", payload=read_json(args.payload), headers={"Idempotency-Key": args.idempotency_key, "If-Match": str(args.version)}))
    elif args.command == "apply":
        payload = read_json(args.payload)
        if args.apply_command == "create":
            emit(apply_create(payload, idempotency_key=args.idempotency_key))
        else:
            emit(apply_update(args.project_id, payload, idempotency_key=args.idempotency_key))
    else:
        emit(request("POST", f"/projects/{args.project_id}/{args.command}", payload=read_json(args.payload), headers={"Idempotency-Key": args.idempotency_key}))


if __name__ == "__main__":
    main()
