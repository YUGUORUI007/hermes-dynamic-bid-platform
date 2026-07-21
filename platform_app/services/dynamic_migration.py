from __future__ import annotations

import json

from ..dynamic_schema import SCHEMA_VERSION, validate_project_payload
from ..models import Project, ProjectContentVersion
from .dynamic_ui import build_legacy_content


def migrate_projects_to_dynamic_content(session, *, apply: bool = False, actor_name: str = "migration") -> dict[str, object]:
    projects = session.query(Project).order_by(Project.id.asc()).all()
    migrated: list[int] = []
    skipped: list[int] = []
    failures: list[dict[str, object]] = []
    for project in projects:
        if project.dynamic_content:
            skipped.append(project.id)
            continue
        try:
            content = build_legacy_content(project)
            normalized = validate_project_payload(
                {
                    "title": project.name,
                    "status": project.status,
                    "owner": project.owner_name or "",
                    "summary": project.summary or project.notes or "",
                    "schema_version": SCHEMA_VERSION,
                    "content": content,
                }
            )
            if apply:
                project.summary = normalized.get("summary") or None
                project.dynamic_content = json.dumps(normalized["content"], ensure_ascii=False)
                project.schema_version = SCHEMA_VERSION
                project.content_version = max(project.content_version or 1, 1)
                session.add(
                    ProjectContentVersion(
                        project_id=project.id,
                        version=project.content_version,
                        schema_version=SCHEMA_VERSION,
                        title=project.name,
                        summary=project.summary,
                        content_json=project.dynamic_content,
                        change_summary="旧固定字段迁移为动态标签",
                        actor_name=actor_name,
                        request_id=f"migration-project-{project.id}",
                    )
                )
            migrated.append(project.id)
        except Exception as exc:  # Preserve per-project failure evidence for migration audits.
            failures.append({"project_id": project.id, "error": str(exc)})
    if apply:
        session.flush()
    return {
        "mode": "apply" if apply else "dry-run",
        "total": len(projects),
        "migrated_count": len(migrated),
        "skipped_count": len(skipped),
        "failed_count": len(failures),
        "migrated_project_ids": migrated,
        "skipped_project_ids": skipped,
        "failures": failures,
    }
