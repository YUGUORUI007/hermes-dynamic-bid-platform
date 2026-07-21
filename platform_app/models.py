from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now_utc() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    short_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tender_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    buyer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bid_mode: Mapped[str] = mapped_column(String(32), default="self")
    status: Mapped[str] = mapped_column(String(32), default="tracking", index=True)
    owner_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agency: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(128), nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract_term: Mapped[str | None] = mapped_column(String(128), nullable=True)
    budget_amount: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deposit_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    signup_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    document_sale_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    clarification_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    site_visit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deposit_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bid_datetime: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submission_datetime: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    bid_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_fee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    bid_document_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    has_defense: Mapped[str | None] = mapped_column(String(32), nullable=True)
    defense_presenter: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invalidation_risks: Mapped[str | None] = mapped_column(Text, nullable=True)
    submission_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    seal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    dynamic_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    content_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    files: Mapped[list["ProjectFile"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    extractions: Mapped[list["ExtractionJob"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    messages: Mapped[list["ProjectMessage"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    milestones: Mapped[list["ProjectMilestone"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    followups: Mapped[list["ProjectFollowup"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    requirements: Mapped[list["ProjectRequirement"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectFile(Base):
    __tablename__ = "project_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    original_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_path: Mapped[str] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(String(128))
    file_size: Mapped[int] = mapped_column(Integer)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(32), default="upload")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    project: Mapped[Project | None] = relationship(back_populates="files")
    extraction_jobs: Mapped[list["ExtractionJob"]] = relationship(back_populates="project_file", cascade="all, delete-orphan")


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    project_file_id: Mapped[int] = mapped_column(ForeignKey("project_files.id"))
    status: Mapped[str] = mapped_column(String(32), default="pending_review", index=True)
    matched_project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

    project: Mapped[Project | None] = relationship(back_populates="extractions")
    project_file: Mapped[ProjectFile] = relationship(back_populates="extraction_jobs")
    fields: Mapped[list["ExtractionField"]] = relationship(back_populates="extraction_job", cascade="all, delete-orphan")


class ExtractionField(Base):
    __tablename__ = "extraction_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    extraction_job_id: Mapped[int] = mapped_column(ForeignKey("extraction_jobs.id"))
    field_key: Mapped[str] = mapped_column(String(64))
    field_label: Mapped[str] = mapped_column(String(128))
    extracted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_review")
    final_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    extraction_job: Mapped[ExtractionJob] = relationship(back_populates="fields")


class ProjectMessage(Base):
    __tablename__ = "project_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    citations: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

    project: Mapped[Project] = relationship(back_populates="messages")


class ProjectMilestone(Base):
    __tablename__ = "project_milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    milestone_type: Mapped[str] = mapped_column(String(64), default="custom")
    title: Mapped[str] = mapped_column(String(255))
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    project: Mapped[Project] = relationship(back_populates="milestones")


class ProjectFollowup(Base):
    __tablename__ = "project_followups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

    project: Mapped[Project] = relationship(back_populates="followups")


class ProjectRequirement(Base):
    __tablename__ = "project_requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    category: Mapped[str] = mapped_column(String(64), default="other", index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[str] = mapped_column(String(32), default="medium")
    source_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    project: Mapped[Project] = relationship(back_populates="requirements")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class ReminderState(Base):
    __tablename__ = "reminder_states"

    reminder_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    done_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    done_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class ArchivedProject(Base):
    __tablename__ = "archived_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_project_id: Mapped[int] = mapped_column(Integer, index=True)
    project_name: Mapped[str] = mapped_column(String(255))
    bid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    final_status: Mapped[str] = mapped_column(String(32))
    deleted_source_files_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted_text_cache_count: Mapped[int] = mapped_column(Integer, default=0)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)


class ProjectContentVersion(Base):
    __tablename__ = "project_content_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[str] = mapped_column(Text)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    token_prefix: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("token_id", "idempotency_key", name="uq_idempotency_token_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("api_tokens.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
