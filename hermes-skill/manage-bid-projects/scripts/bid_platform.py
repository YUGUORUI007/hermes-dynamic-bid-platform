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


def read_json(path: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("JSON payload must be an object")
    return value


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
    for name in ("followup", "status", "archive"):
        command = sub.add_parser(name)
        command.add_argument("project_id", type=int)
        command.add_argument("payload")
        command.add_argument("--idempotency-key", required=True)
    args = parser.parse_args()

    if args.command == "schema":
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
    else:
        emit(request("POST", f"/projects/{args.project_id}/{args.command}", payload=read_json(args.payload), headers={"Idempotency-Key": args.idempotency_key}))


if __name__ == "__main__":
    main()
