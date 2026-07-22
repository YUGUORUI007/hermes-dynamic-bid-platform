from __future__ import annotations

import json
import calendar as calendar_module
import math
from datetime import datetime, timedelta
from typing import Any

from ..dynamic_schema import SCHEMA_VERSION
from ..models import ArchivedProject, Project


ACTIVE_STATUSES = {"tracking", "pending_signup", "registered", "pending_prequalification", "deposit_pending", "deposit_done", "preparing", "sealed", "ready_deliver", "submitted", "result_pending"}
STATUS_LABELS = {
    "tracking": "进行中",
    "pending_signup": "待报名",
    "registered": "已报名",
    "pending_prequalification": "待提交资格预审资料",
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
STATUS_TONES = {
    "tracking": "warning",
    "pending_signup": "warning",
    "registered": "success",
    "pending_prequalification": "warning",
    "deposit_pending": "warning",
    "deposit_done": "info",
    "preparing": "warning",
    "sealed": "success",
    "ready_deliver": "danger",
    "submitted": "info",
    "result_pending": "success",
    "won": "success",
    "lost": "danger",
    "abandoned": "neutral",
    "partner_completed": "info",
    "archived": "neutral",
}

WORKFLOW_STAGES = (
    ("signup", "报名", "待报名", "已报名"),
    ("prequalification", "资格预审资料", "待提交资格预审资料", "资格预审资料已提交"),
    ("deposit", "保证金", "待缴保证金", "保证金已汇出"),
    ("proposal", "标书制作", "待制作标书", "标书已制作"),
    ("sealing", "封标", "待封标", "已封标"),
    ("delivery", "送标", "待送标", "已送标"),
    ("bid_open", "开标", "待开标", "已开标"),
    ("deposit_refund", "投标保证金退还", "投标保证金待退还", "投标保证金已退还"),
)
WORKFLOW_STAGE_IDS = {stage[0] for stage in WORKFLOW_STAGES}
WORKFLOW_STATE_TONES = {"pending": "warning", "in_progress": "info", "done": "success", "not_applicable": "neutral"}


def workflow_status_items(content: dict[str, Any], project_status: str, bid_datetime: datetime | None = None, now: datetime | None = None) -> list[dict[str, str]]:
    now = now or datetime.now()
    saved = content.get("workflow") if isinstance(content.get("workflow"), dict) else {}
    legacy_done = {
        "registered": {"signup"},
        "pending_prequalification": {"signup"},
        "deposit_pending": {"signup", "prequalification"},
        "deposit_done": {"signup", "prequalification", "deposit"},
        "preparing": {"signup", "prequalification", "deposit"},
        "sealed": {"signup", "prequalification", "deposit", "proposal", "sealing"},
        "ready_deliver": {"signup", "prequalification", "deposit", "proposal", "sealing"},
        "submitted": {"signup", "prequalification", "deposit", "proposal", "sealing", "delivery"},
    }.get(project_status, set())
    items = []
    for stage_id, label, pending_label, done_label in WORKFLOW_STAGES:
        state = saved.get(stage_id, "done" if stage_id in legacy_done else "pending")
        # Qualification pre-review is project-specific. Omit it until Hermes has
        # confirmed the tender requires it; a saved not_applicable state stays hidden.
        if stage_id == "prequalification" and (
            state == "not_applicable"
            or (stage_id not in saved and project_status != "pending_prequalification")
        ):
            continue
        state_label = {"pending": pending_label, "in_progress": f"{label}进行中", "done": done_label, "not_applicable": f"{label}不适用"}.get(state, pending_label)
        tone = WORKFLOW_STATE_TONES.get(state, "neutral")
        refund_overdue_days = 0
        if stage_id == "deposit_refund" and state in {"pending", "in_progress"} and bid_datetime and now.date() > (bid_datetime + timedelta(days=14)).date():
            refund_overdue_days = (now.date() - (bid_datetime + timedelta(days=14)).date()).days
            tone = "danger"
            state_label = f"投标保证金待退还（逾期 {refund_overdue_days} 天）"
        items.append({"id": stage_id, "label": label, "state": state, "state_label": state_label, "tone": tone, "refund_overdue_days": refund_overdue_days})
    return items


def load_dynamic_content(project: Project) -> dict[str, Any]:
    if project.dynamic_content:
        try:
            value = json.loads(project.dynamic_content)
            if isinstance(value, dict) and isinstance(value.get("sections"), list):
                return value
        except json.JSONDecodeError:
            pass
    return build_legacy_content(project)


def build_legacy_content(project: Project) -> dict[str, Any]:
    fields = []
    for field_id, label, value, semantic in (
        ("tender-code", "招标编号", project.tender_code, "text"),
        ("buyer", "采购人", project.buyer, "text"),
        ("budget", "预算/控制价", project.budget_amount, "amount"),
        ("location", "项目地点", project.location, "text"),
        ("contract", "服务期限", project.contract_term, "text"),
        ("agency", "代理机构", project.agency, "text"),
    ):
        if value not in (None, ""):
            fields.append({"id": field_id, "type": "field", "label": label, "value": value, "semantic": semantic, "width": "half"})

    dates = []
    for label, value in (
        ("报名截止", project.signup_deadline),
        ("保证金截止", project.deposit_deadline),
        ("投标文件递交", project.submission_datetime),
        ("开标时间", project.bid_datetime),
    ):
        if value:
            dates.append({"label": label, "at": value.strftime("%Y-%m-%d %H:%M"), "status": "待处理", "tone": "warning"})

    sections: list[dict[str, Any]] = []
    if fields:
        sections.append(
            {
                "id": "legacy-overview",
                "title": "项目概览",
                "description": "从原系统字段迁移生成",
                "icon": "layout-dashboard",
                "priority": "normal",
                "visibility": "summary",
                "collapsible": False,
                "blocks": fields,
            }
        )
    if dates:
        sections.append(
            {
                "id": "legacy-dates",
                "title": "关键节点",
                "description": "从原系统时间字段迁移生成",
                "icon": "calendar-range",
                "priority": "important",
                "visibility": "detail",
                "collapsible": False,
                "blocks": [{"id": "legacy-timeline", "type": "timeline", "width": "full", "items": dates}],
            }
        )
    notes = []
    if project.invalidation_risks:
        notes.append({"id": "legacy-risk", "type": "callout", "tone": "danger", "title": "风险提醒", "content": project.invalidation_risks, "width": "full"})
    if project.notes:
        notes.append({"id": "legacy-notes", "type": "text", "title": "备注", "content": project.notes, "width": "full"})
    if notes:
        sections.append(
            {
                "id": "legacy-notes",
                "title": "风险与备注",
                "description": "原项目记录",
                "icon": "triangle-alert",
                "priority": "important",
                "visibility": "detail",
                "collapsible": False,
                "blocks": notes,
            }
        )
    if not sections:
        sections.append(
            {
                "id": "overview",
                "title": "项目概览",
                "description": "等待 Hermes 同步项目内容",
                "icon": "layout-dashboard",
                "priority": "normal",
                "visibility": "detail",
                "collapsible": False,
                "blocks": [],
            }
        )
    return {"sections": sections}


DEADLINE_FIELDS = (
    ("signup_deadline", "报名截止"),
    ("document_sale_deadline", "文件购买截止"),
    ("clarification_deadline", "疑问澄清截止"),
    ("site_visit_time", "现场踏勘"),
    ("deposit_deadline", "保证金截止"),
    ("submission_datetime", "投标递交截止"),
    ("bid_datetime", "开标时间"),
)


def parse_calendar_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).replace(tzinfo=None)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
            try:
                return datetime.strptime(normalized, pattern)
            except ValueError:
                continue
    return None


def dynamic_timeline_entries(project: Project) -> list[dict[str, Any]]:
    content = load_dynamic_content(project)
    entries: list[dict[str, Any]] = []
    for section in content.get("sections", []):
        for block in section.get("blocks", []):
            if block.get("type") != "timeline":
                continue
            for item in block.get("items", []):
                if not isinstance(item, dict):
                    continue
                deadline_at = parse_calendar_datetime(item.get("at"))
                label = item.get("label")
                if deadline_at is None or not isinstance(label, str) or not label.strip():
                    continue
                entries.append({"label": label.strip(), "deadline_at": deadline_at})
    return entries


def project_deadline_entries(project: Project, now: datetime | None = None, *, within_days: int | None = None, include_past: bool = False) -> list[dict[str, Any]]:
    now = now or datetime.now()
    cutoff = now + timedelta(days=within_days) if within_days is not None else None
    entries = []
    seen: set[tuple[str, datetime]] = set()
    for field_name, label in DEADLINE_FIELDS:
        deadline_at = getattr(project, field_name, None)
        if deadline_at is None:
            continue
        seen.add((label, deadline_at))
        if (not include_past and deadline_at < now) or (cutoff and deadline_at > cutoff):
            continue
        seconds_left = (deadline_at - now).total_seconds()
        days_left = max(0, math.ceil(seconds_left / 86400))
        entries.append(
            {
                "label": label,
                "deadline_at": deadline_at,
                "project_id": project.id,
                "project_title": project.name,
                "days_left": days_left,
                "tone": "danger" if seconds_left <= 3 * 86400 else "warning",
            }
        )
    for dynamic_entry in dynamic_timeline_entries(project):
        label = dynamic_entry["label"]
        deadline_at = dynamic_entry["deadline_at"]
        if (label, deadline_at) in seen or (not include_past and deadline_at < now) or (cutoff and deadline_at > cutoff):
            continue
        seconds_left = (deadline_at - now).total_seconds()
        entries.append(
            {
                "label": label,
                "deadline_at": deadline_at,
                "project_id": project.id,
                "project_title": project.name,
                "days_left": max(0, math.ceil(seconds_left / 86400)),
                "tone": "danger" if seconds_left <= 3 * 86400 else "warning",
            }
        )
    return sorted(entries, key=lambda item: item["deadline_at"])


def project_next_deadline(project: Project, now: datetime | None = None) -> tuple[str, datetime] | None:
    entries = project_deadline_entries(project, now)
    if not entries:
        return None
    return entries[0]["label"], entries[0]["deadline_at"]


def summary_blocks(content: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    sections = content.get("sections", [])
    preferred = [section for section in sections if section.get("visibility") == "summary"] or sections
    for section in preferred:
        for block in section.get("blocks", []):
            if block.get("type") in {"field", "status"}:
                blocks.append(block)
                if len(blocks) >= limit:
                    return blocks
    return blocks


def serialize_project_card(project: Project, now: datetime | None = None) -> dict[str, Any]:
    content = load_dynamic_content(project)
    deadline = project_next_deadline(project, now)
    return {
        "id": project.id,
        "title": project.name,
        "code": project.tender_code or f"PROJECT-{project.id}",
        "status": project.status,
        "status_label": STATUS_LABELS.get(project.status, project.status),
        "status_tone": STATUS_TONES.get(project.status, "neutral"),
        "owner": project.owner_name or "未指定",
        "summary": project.summary or project.notes or "等待补充项目摘要。",
        "updated_at": project.updated_at,
        "version": project.content_version or 1,
        "tab_count": len(content.get("sections", [])),
        "summary_blocks": summary_blocks(content),
        "deadline_label": deadline[0] if deadline else "暂无近期节点",
        "deadline_at": deadline[1] if deadline else None,
        "content": content,
        "workflow_items": workflow_status_items(content, project.status, project.bid_datetime),
        "agency": project.agency or "未登记",
        "contact_name": project.contact_name or "未登记",
        "contact_phone": project.contact_phone or "未登记",
    }


def build_workspace_data(session, *, keyword: str = "", status: str = "", owner: str = "") -> dict[str, Any]:
    now = datetime.now()
    query = session.query(Project).filter(Project.status.in_(sorted(ACTIVE_STATUSES)))
    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter((Project.name.ilike(pattern)) | (Project.tender_code.ilike(pattern)) | (Project.dynamic_content.ilike(pattern)))
    if status:
        query = query.filter(Project.status == status)
    if owner:
        query = query.filter(Project.owner_name == owner)
    projects = query.order_by(Project.updated_at.desc()).all()
    cards = [serialize_project_card(project, now) for project in projects]
    upcoming = [entry for project in projects for entry in project_deadline_entries(project, now, within_days=7)]
    upcoming.sort(key=lambda item: item["deadline_at"])
    owners = sorted({project.owner_name for project in projects if project.owner_name})
    return {
        "projects": cards,
        "upcoming": upcoming[:6],
        "owners": owners,
        "metrics": {
            "active": len(cards),
            "due_seven_days": len(upcoming),
            "urgent": sum(1 for entry in upcoming if entry["tone"] == "danger"),
            "pending_result": sum(1 for card in cards if card["status"] == "result_pending"),
        },
        "filters": {"keyword": keyword, "status": status, "owner": owner},
        "status_labels": STATUS_LABELS,
        "status_tones": STATUS_TONES,
    }


def build_calendar_data(session, year: int, month: int) -> dict[str, Any]:
    projects = session.query(Project).filter(Project.status.in_(sorted(ACTIVE_STATUSES))).all()
    events: list[dict[str, Any]] = []
    for project in projects:
        for entry in project_deadline_entries(project, include_past=True):
            if entry["deadline_at"].year == year and entry["deadline_at"].month == month:
                events.append({"project_id": project.id, "project_name": project.name, "label": entry["label"], "at": entry["deadline_at"], "tone": entry["tone"]})
    events.sort(key=lambda item: item["at"])
    event_map: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        event_map.setdefault(event["at"].date().isoformat(), []).append(event)
    weeks = calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month)
    today = datetime.now().date()
    days = [
        {
            "day": day.day,
            "date": day,
            "current_month": day.month == month,
            "today": day == today,
            "events": event_map.get(day.isoformat(), []),
        }
        for week in weeks
        for day in week
    ]
    previous = datetime(year, month, 1) - timedelta(days=1)
    next_month = (datetime(year, month, 28) + timedelta(days=4)).replace(day=1)
    return {
        "year": year,
        "month": month,
        "events": events,
        "days": days,
        "prev_year": previous.year,
        "prev_month": previous.month,
        "next_year": next_month.year,
        "next_month": next_month.month,
    }


def build_archive_data(session, keyword: str = "", status: str = "") -> dict[str, Any]:
    query = session.query(ArchivedProject)
    if keyword:
        query = query.filter(ArchivedProject.project_name.ilike(f"%{keyword}%"))
    if status:
        query = query.filter(ArchivedProject.final_status == status)
    rows = query.order_by(ArchivedProject.archived_at.desc()).all()
    return {
        "rows": rows,
        "total": len(rows),
        "won": sum(1 for row in rows if row.final_status == "won"),
        "lost": sum(1 for row in rows if row.final_status == "lost"),
        "deleted_files": sum(row.deleted_source_files_count for row in rows),
        "keyword": keyword,
        "status": status,
        "status_labels": STATUS_LABELS,
    }


def serialize_project_detail(project: Project) -> dict[str, Any]:
    card = serialize_project_card(project)
    return {**card, "schema_version": project.schema_version or SCHEMA_VERSION}
