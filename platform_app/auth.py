from __future__ import annotations

import bcrypt
from fastapi import Request

from .config import get_admin_password, get_admin_username, is_open_access_mode
from .database import session_scope
from .models import User


ROLE_LABELS = {
    "admin": "管理员",
    "project_owner": "项目负责人",
    "bid_writer": "标书人员",
    "viewer": "只读查看",
}

EDIT_ROLES = {"admin", "project_owner", "bid_writer"}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def normalize_role(role: str | None) -> str:
    candidate = (role or "").strip()
    if candidate not in ROLE_LABELS:
        return "viewer"
    return candidate


def can_edit_projects(user: User | None) -> bool:
    if is_open_access_mode():
        return False
    if user is None:
        return False
    return normalize_role(user.role) in EDIT_ROLES


def can_manage_users(user: User | None) -> bool:
    if is_open_access_mode():
        return False
    if user is None:
        return False
    return normalize_role(user.role) == "admin"


def can_configure_system(user: User | None) -> bool:
    if is_open_access_mode():
        return False
    if user is None:
        return False
    return normalize_role(user.role) == "admin"


def ensure_admin_user() -> None:
    with session_scope() as session:
        existing = session.query(User).filter(User.username == get_admin_username()).first()
        if existing:
            if existing.role == "admin" and existing.display_name != "系统管理员":
                existing.display_name = "系统管理员"
            return
        session.add(
            User(
                username=get_admin_username(),
                display_name="系统管理员",
                password_hash=hash_password(get_admin_password()),
                role="admin",
            )
        )


def get_current_user(request: Request) -> User | None:
    if is_open_access_mode():
        # A persisted actor lets existing read-only templates render without a browser login.
        with session_scope() as session:
            return session.query(User).filter(User.username == get_admin_username()).first()
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with session_scope() as session:
        return session.get(User, user_id)
