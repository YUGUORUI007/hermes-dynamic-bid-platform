from __future__ import annotations

import argparse
import copy
import html
import getpass
import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests


class AcceptanceClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def request(self, method: str, path: str, *, expected: int, **kwargs) -> requests.Response:
        response = self.session.request(method, urljoin(self.base_url, path.lstrip("/")), timeout=30, **kwargs)
        if response.status_code != expected:
            raise RuntimeError(f"{method} {path} 返回 HTTP {response.status_code}: {response.text[:600]}")
        return response


class AdminSession:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.session = requests.Session()
        login = self.session.post(urljoin(self.base_url, "login"), data={"username": username, "password": password}, timeout=30)
        if login.status_code != 200 or "/workspace" not in login.url:
            raise RuntimeError("管理员网页登录失败。")

    def post(self, path: str, data: dict[str, str], *, expected: tuple[int, ...] = (200,)) -> requests.Response:
        response = self.session.post(urljoin(self.base_url, path.lstrip("/")), data=data, timeout=30, allow_redirects=False)
        if response.status_code not in expected:
            raise RuntimeError(f"POST {path} 返回 HTTP {response.status_code}: {response.text[:500]}")
        return response

    def settings_html(self) -> str:
        response = self.session.get(urljoin(self.base_url, "workspace/settings"), timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"系统管理页访问失败：HTTP {response.status_code}")
        return response.text

    def create_user(self, suffix: str) -> tuple[int, str]:
        username = f"accept_{suffix}"
        self.post("users/create", {"username": username, "display_name": f"验收用户 {suffix}", "password": secrets.token_urlsafe(12), "role": "viewer"}, expected=(302, 303))
        page = self.settings_html()
        match = re.search(rf">{re.escape(username)}<.*?/users/(\d+)/delete", page, re.S)
        if not match:
            raise RuntimeError("无法定位临时验收用户。")
        return int(match.group(1)), username

    def create_token(self, suffix: str) -> tuple[int, str]:
        name = f"Acceptance {suffix}"
        response = self.post("settings/api-tokens", {"name": name, "expires_in_days": "1", "projects_read": "1", "projects_write": "1", "projects_archive": "1"}, expected=(302, 303))
        page = self.session.get(urljoin(self.base_url, response.headers["location"].lstrip("/")), timeout=30).text
        token_match = re.search(r'<code id="new-token">([^<]+)</code>', page)
        id_match = re.search(rf">{re.escape(name)}<.*?/settings/api-tokens/(\d+)/revoke", page, re.S)
        if not token_match or not id_match:
            raise RuntimeError("无法读取一次性验收 Token。")
        return int(id_match.group(1)), html.unescape(token_match.group(1)).strip()


def confirmation(summary: str) -> dict[str, str]:
    return {
        "confirmed_by": "生产验收脚本用户",
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
    }


def sample_payload(suffix: str) -> dict:
    return {
        "title": f"动态 API 验收项目 {suffix}",
        "status": "tracking",
        "owner": "验收人员",
        "summary": "临时验收数据，结束后自动归档清理。",
        "schema_version": "1.0",
        "content": {"sections": [
            {"id": "overview", "title": "项目概览", "visibility": "summary", "blocks": [
                {"id": "budget", "type": "field", "label": "预算", "value": "100 万元", "semantic": "amount"},
                {"id": "risk", "type": "callout", "tone": "warning", "content": "这是自动验收创建的临时项目。"},
            ]},
            {"id": "schedule", "title": "关键节点", "blocks": [
                {"id": "timeline", "type": "timeline", "items": [{"label": "递交文件", "at": "2026-08-01 09:00", "status": "待完成", "tone": "warning"}]},
            ]},
            {"id": "checklist", "title": "准备清单", "blocks": [
                {"id": "items", "type": "checklist", "items": [{"label": "核对授权文件", "done": False}]},
                {"id": "score", "type": "table", "columns": ["板块", "分值"], "rows": [["技术", "60"]]},
            ]},
        ]},
    }


def run_acceptance(base_url: str, username: str, password: str) -> dict[str, object]:
    suffix = uuid.uuid4().hex[:8]
    admin = AdminSession(base_url, username, password)
    user_id = token_id = archive_id = None
    try:
        user_id, temporary_username = admin.create_user(suffix)
        token_id, api_token = admin.create_token(suffix)
        client = AcceptanceClient(base_url, api_token)
        payload = sample_payload(suffix)
        client.request("GET", "/api/v1/schema/project", expected=200)

        validated = client.request("POST", "/api/v1/validate/project", expected=200, json=payload).json()
        create_body = copy.deepcopy(payload)
        create_body["validation_token"] = validated["validation_token"]
        create_body["confirmation"] = confirmation("确认创建临时验收项目")
        create_headers = {"Idempotency-Key": f"accept-create-{suffix}"}
        created_response = client.request("POST", "/api/v1/projects", expected=201, headers=create_headers, json=create_body)
        project = created_response.json()["project"]
        project_id = project["id"]
        create_request_id = created_response.json()["request_id"]

        replay = client.request("POST", "/api/v1/projects", expected=201, headers=create_headers, json=create_body)
        if replay.headers.get("Idempotency-Replayed") != "true" or replay.json()["project"]["id"] != project_id:
            raise RuntimeError("幂等重放验证失败。")

        read = client.request("GET", f"/api/v1/projects/{project_id}", expected=200).json()
        if len(read["content"]["sections"]) != 3:
            raise RuntimeError("读取后的动态内容与提交内容不一致。")
        dashboard = admin.session.get(urljoin(admin.base_url, "workspace"), timeout=30)
        detail = admin.session.get(urljoin(admin.base_url, f"projects/{project_id}"), timeout=30)
        if dashboard.status_code != 200 or payload["title"] not in dashboard.text or detail.status_code != 200 or "项目概览" not in detail.text:
            raise RuntimeError("工作台或动态详情页浏览器内容检查失败。")

        patch = {"summary": "验收更新已确认。", "change_summary": "生产验收更新摘要"}
        patch_validation = client.request("POST", "/api/v1/validate/project?partial=true", expected=200, json=patch).json()
        patch["validation_token"] = patch_validation["validation_token"]
        patch["confirmation"] = confirmation("确认更新临时验收项目")
        client.request("PATCH", f"/api/v1/projects/{project_id}", expected=200, headers={"Idempotency-Key": f"accept-update-{suffix}", "If-Match": "1"}, json=patch)
        stale = client.session.patch(urljoin(client.base_url, f"api/v1/projects/{project_id}"), timeout=30, headers={"Idempotency-Key": f"accept-stale-{suffix}", "If-Match": "1"}, json=patch)
        if stale.status_code != 409 or stale.json().get("error", {}).get("code") != "version_conflict":
            raise RuntimeError("乐观锁冲突验证失败。")

        confirmed = confirmation("确认跟进、状态变更和归档")
        client.request("POST", f"/api/v1/projects/{project_id}/followups", expected=201, headers={"Idempotency-Key": f"accept-followup-{suffix}"}, json={"content": "生产动态 API 自动验收完成。", "confirmation": confirmed})
        client.request("POST", f"/api/v1/projects/{project_id}/status", expected=200, headers={"Idempotency-Key": f"accept-status-{suffix}"}, json={"status": "result_pending", "confirmation": confirmed})
        archive = client.request("POST", f"/api/v1/projects/{project_id}/archive", expected=200, headers={"Idempotency-Key": f"accept-archive-{suffix}"}, json={"final_status": "archived", "confirmation": confirmed}).json()
        archive_id = archive["archive"]["id"]
        client.request("GET", f"/api/v1/projects/{project_id}", expected=404)
        audit_page = admin.settings_html()
        if create_request_id not in audit_page or payload["title"] not in audit_page:
            raise RuntimeError("审计页未找到 Hermes 写入的项目名称和请求 ID。")
        return {"ok": True, "project_id": project_id, "temporary_user": temporary_username, "checks": 14}
    finally:
        if archive_id is not None:
            admin.post(f"archives/{archive_id}/delete", {}, expected=(302, 303))
        if token_id is not None:
            admin.post(f"settings/api-tokens/{token_id}/revoke", {}, expected=(302, 303))
        if user_id is not None:
            admin.post(f"users/{user_id}/delete", {}, expected=(302, 303))
        settings_after_cleanup = admin.settings_html()
        archives_after_cleanup = admin.session.get(urljoin(admin.base_url, "workspace/archives"), timeout=30).text
        if f">accept_{suffix}<" in settings_after_cleanup:
            raise RuntimeError("临时验收用户清理失败。")
        if f"动态 API 验收项目 {suffix}" in archives_after_cleanup:
            raise RuntimeError("临时验收归档记录清理失败。")


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes 动态投标平台生产验收")
    parser.add_argument("--base-url", required=True, help="站点根地址")
    parser.add_argument("--username", default=os.getenv("BID_PLATFORM_ACCEPTANCE_USERNAME", ""), help="管理员用户名，也可使用 BID_PLATFORM_ACCEPTANCE_USERNAME")
    parser.add_argument("--password", default=os.getenv("BID_PLATFORM_ACCEPTANCE_PASSWORD", ""), help="管理员密码；推荐使用 BID_PLATFORM_ACCEPTANCE_PASSWORD")
    args = parser.parse_args()
    if not args.username:
        parser.error("必须提供 --username 或 BID_PLATFORM_ACCEPTANCE_USERNAME")
    password = args.password or getpass.getpass("管理员密码：")
    result = run_acceptance(args.base_url, args.username, password)
    print(f"生产验收通过：project_id={result['project_id']}，checks={result['checks']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"生产验收失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
