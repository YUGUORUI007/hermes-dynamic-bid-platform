from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
work = Path(tempfile.mkdtemp(prefix="hermes-skill-e2e-"))
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

os.environ.update({
    "BID_PLATFORM_DATABASE_URL": f"sqlite:///{(work / 'platform.db').as_posix()}",
    "BID_PLATFORM_INSTANCE_DIR": str(work / "instance"),
    "BID_PLATFORM_STORAGE_DIR": str(work / "storage"),
    "BID_PLATFORM_SECRET_KEY": "hermes-skill-e2e-secret",
    "BID_PLATFORM_ADMIN_PASSWORD": "hermes-skill-e2e-password",
    "BID_PLATFORM_PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
})

from platform_app.api_v1 import create_api_token
from platform_app.database import Base, engine, session_scope
from platform_app.models import AuditLog


Base.metadata.create_all(bind=engine)
with session_scope() as session:
    _, raw_token = create_api_token(session, name="Hermes Skill E2E", scopes={"projects:read", "projects:write"}, created_by="test")

env = dict(os.environ)
env["BID_PLATFORM_API_URL"] = f"http://127.0.0.1:{port}/api/v1"
env["BID_PLATFORM_API_TOKEN"] = raw_token
server = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "platform_server:app", "--host", "127.0.0.1", "--port", str(port)],
    cwd=ROOT,
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
client_script = ROOT / "hermes-skill" / "manage-bid-projects" / "scripts" / "bid_platform.py"


def wait_for_server() -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("隔离 Hermes Skill 测试服务器未就绪。")


def skill(*args: str) -> dict:
    result = subprocess.run([sys.executable, str(client_script), *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"Skill 命令失败: {' '.join(args)}\n{result.stderr}")
    return json.loads(result.stdout)


try:
    wait_for_server()
    payload = {
        "title": "Hermes Skill 样例招标项目",
        "status": "tracking",
        "owner": "测试负责人",
        "summary": "由样例招标材料整理的动态项目。",
        "schema_version": "1.0",
        "content": {"sections": [{"id": "qualification", "title": "资格要求", "blocks": [{"id": "license", "type": "checklist", "items": [{"label": "营业执照", "done": False}]}]}]},
    }
    payload_path = work / "create.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    validated = skill("validate", str(payload_path))
    payload["validation_token"] = validated["validation_token"]
    payload["confirmation"] = {"confirmed_by": "测试用户", "confirmed_at": "2026-07-21T16:00:00+08:00", "summary": "确认创建样例项目"}
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    created = skill("create", str(payload_path), "--idempotency-key", "skill-e2e-create")
    project_id = created["project"]["id"]
    read = skill("get", str(project_id))
    if read["content"]["sections"][0]["title"] != "资格要求":
        raise RuntimeError("Skill 读取内容与创建内容不一致。")

    patch = {"summary": "增量补充：已完成营业执照核对。", "change_summary": "补充资格核对进度"}
    patch_path = work / "patch.json"
    patch_path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
    patch_validation = skill("validate", "--partial", str(patch_path))
    patch["validation_token"] = patch_validation["validation_token"]
    patch["confirmation"] = {"confirmed_by": "测试用户", "confirmed_at": "2026-07-21T16:05:00+08:00", "summary": "确认增量更新资格进度"}
    patch_path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
    updated = skill("update", str(project_id), str(patch_path), "--version", "1", "--idempotency-key", "skill-e2e-update")
    if updated["project"]["version"] != 2:
        raise RuntimeError("Skill 增量更新未生成版本 2。")
    with session_scope() as session:
        actions = {row.action for row in session.query(AuditLog).filter(AuditLog.entity_id == str(project_id)).all()}
        if not {"api_create_project", "api_update_project"}.issubset(actions):
            raise RuntimeError("Skill 写入审计链不完整。")
    print(json.dumps({"ok": True, "project_id": project_id, "checks": 8}))
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
