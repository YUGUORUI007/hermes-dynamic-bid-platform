from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from ..config import PROJECT_STORAGE_DIR
from ..models import ArchivedProject, Project, ProjectContentVersion, ProjectFile, ReminderState


def archive_project_data(session, project: Project, final_status: str) -> ArchivedProject:
    files = session.query(ProjectFile).filter(ProjectFile.project_id == project.id, ProjectFile.deleted_at.is_(None)).all()
    deleted_source_files_count = 0
    deleted_text_cache_count = 0
    for file_record in files:
        path = Path(file_record.storage_path)
        if path.exists():
            path.unlink(missing_ok=True)
        if file_record.extracted_text:
            deleted_text_cache_count += 1
        file_record.extracted_text = None
        file_record.extracted_summary = None
        file_record.deleted_at = datetime.utcnow()
        file_record.deleted_reason = "项目归档自动清理"
        deleted_source_files_count += 1
        text_path = path.with_suffix(".txt")
        if text_path.exists():
            text_path.unlink(missing_ok=True)

    project_root = (PROJECT_STORAGE_DIR / str(project.id)).resolve()
    storage_root = PROJECT_STORAGE_DIR.resolve()
    if project_root != storage_root and storage_root in project_root.parents and project_root.is_dir():
        shutil.rmtree(project_root, ignore_errors=True)

    session.query(ReminderState).filter(ReminderState.project_id == project.id).delete()
    session.query(ProjectContentVersion).filter(ProjectContentVersion.project_id == project.id).delete()

    archived = ArchivedProject(
        original_project_id=project.id,
        project_name=project.name,
        bid_at=project.bid_datetime or project.submission_datetime,
        final_status=final_status,
        deleted_source_files_count=deleted_source_files_count,
        deleted_text_cache_count=deleted_text_cache_count,
    )
    session.add(archived)
    session.flush()
    session.delete(project)
    session.flush()
    return archived
