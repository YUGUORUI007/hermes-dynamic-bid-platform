from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
root = Path(tempfile.mkdtemp(prefix="bid-api-test-"))
os.environ["BID_PLATFORM_DATABASE_URL"] = f"sqlite:///{(root / 'test.db').as_posix()}"
os.environ["BID_PLATFORM_INSTANCE_DIR"] = str(root / "instance")
os.environ["BID_PLATFORM_STORAGE_DIR"] = str(root / "storage")
TEST_SECRET_KEY = secrets.token_urlsafe(32)
TEST_ADMIN_PASSWORD = secrets.token_urlsafe(24)
os.environ["BID_PLATFORM_SECRET_KEY"] = TEST_SECRET_KEY
os.environ["BID_PLATFORM_ADMIN_PASSWORD"] = TEST_ADMIN_PASSWORD
os.environ["BID_PLATFORM_PUBLIC_BASE_URL"] = "https://bid.example.test"

from fastapi.testclient import TestClient

from platform_app.api_v1 import create_api_token
from platform_app.auth import hash_password
from platform_app.database import session_scope
from platform_app.dynamic_schema import validate_project_payload
from platform_app.main import create_app
from platform_app.models import ApiToken, AuditLog, Project, User


app = create_app()
with session_scope() as session:
    _, raw_token = create_api_token(
        session,
        name="Integration Hermes",
        scopes={"projects:read", "projects:write", "projects:archive"},
        created_by="test",
    )
    _, read_only_token = create_api_token(session, name="Read only", scopes={"projects:read"}, created_by="test")

client = TestClient(app)
headers = {"Authorization": f"Bearer {raw_token}"}

unauthorized = client.get("/api/v1/projects")
assert unauthorized.status_code == 401
assert unauthorized.json()["error"]["code"] == "missing_token"
forbidden = client.post("/api/v1/validate/project", headers={"Authorization": f"Bearer {read_only_token}"}, json={})
assert forbidden.status_code == 403
assert forbidden.json()["error"]["code"] == "insufficient_scope"

for private_path in ("/", "/workspace", "/projects/1"):
    private = client.get(private_path, follow_redirects=False)
    assert private.status_code == 302, f"{private_path}: {private.status_code}"
    assert private.headers["location"] == "/login"

legacy = client.post("/upload", headers={"Accept": "application/json"})
assert legacy.status_code == 410, legacy.text
assert legacy.json()["error"]["code"] == "legacy_ai_disabled"

oversized = client.post("/api/v1/validate/project", headers={**headers, "Content-Length": str(2 * 1024 * 1024 + 1)}, content=b"{}")
assert oversized.status_code == 413, oversized.text
malformed_length = client.post("/api/v1/validate/project", headers={**headers, "Content-Length": "not-a-number"}, content=b"{}")
assert malformed_length.status_code == 400, malformed_length.text
assert malformed_length.json()["error"]["code"] == "invalid_content_length"
invalid_json = client.post("/api/v1/validate/project", headers={**headers, "Content-Type": "application/json"}, content=b"{")
assert invalid_json.status_code == 400
assert invalid_json.json()["error"]["code"] == "invalid_json"
payload = {
    "title": "API 验收项目",
    "status": "tracking",
    "owner": "测试人员",
    "summary": "用于验证动态项目 API。",
    "tender_code": "API-2026-001",
    "buyer": "Test buyer",
    "agency": "Test tender agency",
    "contact_name": "Test contact",
    "contact_phone": "13800000000",
    "submission_datetime": "2026-07-30T17:00:00",
    "bid_datetime": "2026-07-31T09:30:00",
    "schema_version": "1.0",
    "content": {
        "workflow": {"signup": "done", "deposit": "pending", "proposal": "in_progress", "deposit_refund": "pending"},
        "sections": [
            {
                "id": "overview",
                "title": "项目概览",
                "visibility": "summary",
                "blocks": [
                    {"id": "budget", "type": "field", "label": "预算", "value": "100 万元", "semantic": "amount"},
                    {"id": "risk", "type": "callout", "tone": "warning", "content": "<script>alert('xss')</script>保证金尚未确认。"},
                ],
            }
        ]
    },
}

schema_response = client.get("/api/v1/schema/project", headers=headers)
assert schema_response.status_code == 200, schema_response.text
assert "table" in schema_response.json()["block_types"]
for workflow_status in ("pending_signup", "registered", "pending_prequalification", "deposit_pending", "deposit_done", "preparing", "sealed", "ready_deliver"):
    assert validate_project_payload({**payload, "status": workflow_status})["status"] == workflow_status
openapi = client.get("/openapi.json").json()
for path, method in (("/api/v1/validate/project", "post"), ("/api/v1/projects", "post"), ("/api/v1/projects/{project_id}", "patch"), ("/api/v1/projects/{project_id}/followups", "post"), ("/api/v1/projects/{project_id}/status", "post"), ("/api/v1/projects/{project_id}/archive", "post")):
    assert "example" in openapi["paths"][path][method]["requestBody"]["content"]["application/json"], path

unknown = client.post("/api/v1/validate/project", headers=headers, json={**payload, "unexpected": True})
assert unknown.status_code == 422, unknown.text
assert unknown.json()["error"]["code"] == "schema_validation_failed"

validation = client.post("/api/v1/validate/project", headers=headers, json=payload)
assert validation.status_code == 200, validation.text
validated = validation.json()
confirmed_payload = dict(payload)
confirmed_payload["validation_token"] = validated["validation_token"]
confirmed_payload["confirmation"] = {
    "confirmed_by": "测试用户",
    "confirmed_at": "2026-07-21T15:00:00+08:00",
    "summary": "确认创建 API 验收项目",
}

create_headers = {**headers, "Idempotency-Key": "integration-create-1"}
created = client.post("/api/v1/projects", headers=create_headers, json=confirmed_payload)
assert created.status_code == 201, created.text
created_body = created.json()
project_id = created_body["project"]["id"]
assert created_body["project"]["version"] == 1
assert created_body["project"]["url"] == f"https://bid.example.test/projects/{project_id}"
assert created_body["project"]["agency"] == payload["agency"]
assert created_body["project"]["bid_datetime"].startswith("2026-07-31T09:30:00")

replayed = client.post("/api/v1/projects", headers=create_headers, json=confirmed_payload)
assert replayed.status_code == 201, replayed.text
assert replayed.headers["Idempotency-Replayed"] == "true"
assert replayed.json()["project"]["id"] == project_id

read = client.get(f"/api/v1/projects/{project_id}", headers=headers)
assert read.status_code == 200
assert read.json()["content"]["sections"][0]["title"] == "项目概览"

patch = {"summary": "已确认保证金，进入标书制作。", "change_summary": "更新项目摘要"}
patch_validation = client.post("/api/v1/validate/project?partial=true", headers=headers, json=patch)
assert patch_validation.status_code == 200, patch_validation.text
patch["validation_token"] = patch_validation.json()["validation_token"]
patch["confirmation"] = {
    "confirmed_by": "测试用户",
    "confirmed_at": "2026-07-21T15:05:00+08:00",
    "summary": "确认更新项目摘要",
}
update_headers = {**headers, "Idempotency-Key": "integration-update-1", "If-Match": "1"}
updated = client.patch(f"/api/v1/projects/{project_id}", headers=update_headers, json=patch)
assert updated.status_code == 200, updated.text
assert updated.json()["project"]["version"] == 2
assert updated.json()["project"]["summary"] == patch["summary"]

stale_headers = {**headers, "Idempotency-Key": "integration-update-stale", "If-Match": "1"}
stale = client.patch(f"/api/v1/projects/{project_id}", headers=stale_headers, json=patch)
assert stale.status_code == 409, stale.text
assert stale.json()["error"]["code"] == "version_conflict"

login = client.post(
    "/login",
    data={"username": "admin", "password": TEST_ADMIN_PASSWORD},
    follow_redirects=False,
)
assert login.status_code == 302, login.text

anonymous_status_update = TestClient(app).patch(f"/api/projects/{project_id}/status", json={"status": "registered"})
assert anonymous_status_update.status_code == 401, anonymous_status_update.text
with session_scope() as session:
    VIEWER_PASSWORD = secrets.token_urlsafe(24)
    session.add(User(username="workflow-viewer", display_name="流程查看者", password_hash=hash_password(VIEWER_PASSWORD), role="viewer"))
viewer_client = TestClient(app)
viewer_login = viewer_client.post("/login", data={"username": "workflow-viewer", "password": VIEWER_PASSWORD}, follow_redirects=False)
assert viewer_login.status_code == 302, viewer_login.text
viewer_status_update = viewer_client.patch(f"/api/projects/{project_id}/status", json={"status": "registered"})
assert viewer_status_update.status_code == 403, viewer_status_update.text
invalid_web_status = client.patch(f"/api/projects/{project_id}/status", json={"status": "not-a-status"})
assert invalid_web_status.status_code == 422, invalid_web_status.text
with session_scope() as session:
    project = session.get(Project, project_id)
    project.agency = "测试代理机构"
    project.contact_name = "王工"
    project.contact_phone = "13800000000"
web_status_update = client.patch(f"/api/projects/{project_id}/status", json={"status": "registered"})
assert web_status_update.status_code == 200, web_status_update.text
assert web_status_update.json() == {"ok": True, "status": "registered", "status_label": "已报名"}
with session_scope() as session:
    assert session.get(Project, project_id).status == "registered"
    assert session.query(AuditLog).filter(AuditLog.entity_id == project_id, AuditLog.action == "update_project_status").count() >= 1
workflow_update = client.patch(f"/api/projects/{project_id}/workflow/deposit", json={"state": "done"})
assert workflow_update.status_code == 200, workflow_update.text
assert workflow_update.json()["state_label"] == "保证金已汇出"
invalid_workflow = client.patch(f"/api/projects/{project_id}/workflow/not-a-stage", json={"state": "done"})
assert invalid_workflow.status_code == 422, invalid_workflow.text
with session_scope() as session:
    assert json.loads(session.get(Project, project_id).dynamic_content)["workflow"]["deposit"] == "done"
    assert session.query(AuditLog).filter(AuditLog.entity_id == project_id, AuditLog.action == "update_project_workflow").count() == 1
editor_projects_page = client.get("/workspace/projects")
assert "project-row-v3-states" in editor_projects_page.text
viewer_projects_page = viewer_client.get("/workspace/projects")
assert "project-row-v3-states" in viewer_projects_page.text and 'class="status-select' not in viewer_projects_page.text

token_create = client.post("/settings/api-tokens", data={"name": "Web test", "expires_in_days": "90", "projects_read": "1"}, follow_redirects=False)
assert token_create.status_code in {302, 303}, f"{token_create.status_code} {token_create.headers} {token_create.text[:500]}"
with session_scope() as session:
    web_token = session.query(ApiToken).filter(ApiToken.name == "Web test").one()
    web_token_id = web_token.id
    assert web_token.revoked_at is None
token_revoke = client.post(f"/settings/api-tokens/{web_token_id}/revoke", follow_redirects=False)
assert token_revoke.status_code in {302, 303}, token_revoke.text
with session_scope() as session:
    assert session.get(ApiToken, web_token_id).revoked_at is not None
page_checks = {
    "/workspace": "在投项目",
    "/workspace/calendar": "关键节点日历",
    "/workspace/archives": "已投项目归档",
    "/workspace/settings": "Hermes Skill 接入",
    f"/projects/{project_id}": "项目概览",
    f"/projects/{project_id}/dynamic-editor": "动态内容编辑器",
}
projects_redirect = client.get("/workspace/projects", follow_redirects=False)
assert projects_redirect.status_code == 302
assert projects_redirect.headers["location"] == "/workspace"
for path, expected_text in page_checks.items():
    page = client.get(path)
    assert page.status_code == 200, f"{path}: {page.status_code} {page.text[:500]}"
    assert expected_text in page.text, f"{path}: missing {expected_text}"
detail_page = client.get(f"/projects/{project_id}")
assert "<script>alert('xss')</script>" not in detail_page.text
assert "&lt;script&gt;alert" in detail_page.text
assert "执行状态" in detail_page.text
assert 'class="workflow-state-select' in detail_page.text and "保证金已汇出" in detail_page.text
editor_page = client.get(f"/projects/{project_id}/dynamic-editor")
assert "data-add-section" in editor_page.text and "data-editor-preview" in editor_page.text

manual_content = dict(payload["content"])
manual_content["sections"] = list(manual_content["sections"]) + [{"id": "manual-note", "title": "人工补充", "blocks": [{"id": "manual-text", "type": "text", "title": "说明", "content": "人工编辑器保存成功。"}]}]
manual_save = client.post(f"/projects/{project_id}/dynamic-editor", data={"expected_version": "2", "title": payload["title"], "status": "tracking", "owner": "测试人员", "summary": "人工维护后的摘要", "content_json": json.dumps(manual_content, ensure_ascii=False)}, follow_redirects=False)
assert manual_save.status_code == 302, manual_save.text
manual_detail = client.get(f"/projects/{project_id}")
assert "人工补充" in manual_detail.text and "人工编辑器保存成功" in manual_detail.text

confirmation = {"confirmed_by": "测试用户", "confirmed_at": "2026-07-21T15:10:00+08:00", "summary": "确认追加跟进并归档"}
bad_confirmation = client.post(f"/api/v1/projects/{project_id}/followups", headers={**headers, "Idempotency-Key": "integration-bad-confirmation"}, json={"content": "不应写入", "confirmation": {"confirmed_by": "测试用户", "confirmed_at": "not-a-date"}})
assert bad_confirmation.status_code == 422
assert bad_confirmation.json()["error"]["code"] == "invalid_confirmation"
bad_status = client.post(f"/api/v1/projects/{project_id}/status", headers={**headers, "Idempotency-Key": "integration-bad-status"}, json={"status": "made_up", "confirmation": confirmation})
assert bad_status.status_code == 422
assert bad_status.json()["error"]["code"] == "invalid_status"
followup = client.post(
    f"/api/v1/projects/{project_id}/followups",
    headers={**headers, "Idempotency-Key": "integration-followup-1"},
    json={"content": "已完成 API 验收。", "confirmation": confirmation},
)
assert followup.status_code == 201, followup.text
status_update = client.post(
    f"/api/v1/projects/{project_id}/status",
    headers={**headers, "Idempotency-Key": "integration-status-1"},
    json={"status": "result_pending", "confirmation": confirmation},
)
assert status_update.status_code == 200, status_update.text
assert status_update.json()["project"]["version"] == 4
archive = client.post(
    f"/api/v1/projects/{project_id}/archive",
    headers={**headers, "Idempotency-Key": "integration-archive-1"},
    json={"final_status": "archived", "confirmation": confirmation},
)
assert archive.status_code == 200, archive.text
assert archive.json()["archived"] is True
missing = client.get(f"/api/v1/projects/{project_id}", headers=headers)
assert missing.status_code == 404, missing.text

print(json.dumps({"ok": True, "project_id": project_id, "checks": 45}))
