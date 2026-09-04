from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import sqlite_file_from_url


class Base(DeclarativeBase):
    pass


class CaseRow(Base):
    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20))
    route: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(40))
    declared_condition: Mapped[str] = mapped_column(String(20))
    owner_session_id: Mapped[str] = mapped_column(String(80), index=True)
    case_token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(DateTime)
    updated_at: Mapped[str] = mapped_column(DateTime)
    expires_at: Mapped[str] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)
    route_reason: Mapped[str] = mapped_column(Text, default="")
    route_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    approved_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ask_loss_question: Mapped[bool] = mapped_column(Boolean, default=False)


class EvidenceRow(Base):
    __tablename__ = "evidence"

    evidence_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    original_name_display: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(80))
    mime: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20))
    retention_until: Mapped[str] = mapped_column(DateTime)
    extracted_text_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)


class FactRow(Base):
    __tablename__ = "facts"

    fact_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    raw_value: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    criticality: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(20))
    source_evidence_id: Mapped[str] = mapped_column(String(80))
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_bbox: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_excerpt_hash: Mapped[str] = mapped_column(String(64))
    corrected_from_fact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ConflictRow(Base):
    __tablename__ = "conflicts"

    conflict_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    fact_ids_json: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    resolution_fact_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resolved_at: Mapped[str | None] = mapped_column(DateTime, nullable=True)


class TransactionRow(Base):
    __tablename__ = "transaction_groups"

    transaction_group_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    victim_account: Mapped[str | None] = mapped_column(String(80), nullable=True)
    destination_account: Mapped[str] = mapped_column(String(80))
    amount: Mapped[float] = mapped_column(Float)
    transferred_at: Mapped[str] = mapped_column(String(40))
    evidence_ids_json: Mapped[str] = mapped_column(Text)
    readiness: Mapped[str] = mapped_column(String(40))


class ActionRow(Base):
    __tablename__ = "actions"

    action_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    priority: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(30))
    instruction: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30))
    official_url_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    requires_external_user_action: Mapped[bool] = mapped_column(Boolean, default=True)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    actor: Mapped[str] = mapped_column(String(20))
    scope: Mapped[str] = mapped_column(String(30))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[str] = mapped_column(DateTime)
    notice_version: Mapped[str] = mapped_column(String(40))
    revoked_at: Mapped[str | None] = mapped_column(DateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    profile_version: Mapped[str | None] = mapped_column(String(40), nullable=True)


class UnitMappingRow(Base):
    __tablename__ = "unit_mappings"

    decision_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    unit_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_evidence_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pairings_json: Mapped[str] = mapped_column(Text, default="[]")
    actor: Mapped[str] = mapped_column(String(20), default="USER")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    storage_key: Mapped[str] = mapped_column(String(80))
    mime: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    source_snapshot_hash: Mapped[str] = mapped_column(String(64))
    verify_status: Mapped[str] = mapped_column(String(20))
    verify_details_json: Mapped[str] = mapped_column(Text, default="{}")


class ReceiptRow(Base):
    __tablename__ = "receipts"

    receipt_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    ticket_value_masked: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(20))
    format_status: Mapped[str] = mapped_column(String(20))
    local_match_status: Mapped[str] = mapped_column(String(20))
    official_status: Mapped[str] = mapped_column(String(20), default="NOT_VERIFIED")
    recorded_at: Mapped[str] = mapped_column(DateTime)
    receipt_evidence_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ticket_normalized: Mapped[str] = mapped_column(String(80), default="")


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(80), index=True)
    run_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    event_type: Mapped[str] = mapped_column(String(60))
    state_before: Mapped[str | None] = mapped_column(String(40), nullable=True)
    state_after: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    tool_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[str] = mapped_column(DateTime)
    planner: Mapped[str | None] = mapped_column(String(40), nullable=True)
    execution: Mapped[str | None] = mapped_column(String(40), nullable=True)
    planner_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    handler_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hermes_attempt_1_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hermes_attempt_2_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hermes_sequence_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class JobRow(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(80), index=True)
    run_id: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(DateTime)
    started_at: Mapped[str | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(DateTime, nullable=True)


class IdempotencyRow(Base):
    __tablename__ = "idempotency"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(80))
    payload_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(DateTime)


class DerivedTextRow(Base):
    __tablename__ = "derived_text"

    ref: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(80), index=True)
    evidence_id: Mapped[str] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(80))


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def make_engine(database_url: str) -> Engine:
    path = sqlite_file_from_url(database_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    _configure_sqlite(engine)
    return engine


def _migrate_audit_events(engine: Engine) -> None:
    with engine.begin() as conn:
        names = {row[1] for row in conn.execute(text("PRAGMA table_info(audit_events)"))}
        if "planner" not in names:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN planner VARCHAR(40)"))
        if "execution" not in names:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN execution VARCHAR(40)"))
        if "planner_ms" not in names:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN planner_ms INTEGER"))
        if "handler_ms" not in names:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN handler_ms INTEGER"))
        if "hermes_attempt_1_ms" not in names:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN hermes_attempt_1_ms INTEGER"))
        if "hermes_attempt_2_ms" not in names:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN hermes_attempt_2_ms INTEGER"))
        if "hermes_sequence_ms" not in names:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN hermes_sequence_ms INTEGER"))
        if "ocr_total_ms" not in names:
            conn.execute(text("ALTER TABLE audit_events ADD COLUMN ocr_total_ms INTEGER"))


def _migrate_approvals(engine: Engine) -> None:
    with engine.begin() as conn:
        try:
            names = {row[1] for row in conn.execute(text("PRAGMA table_info(approvals)"))}
        except Exception:
            return
        if "target_id" not in names:
            conn.execute(text("ALTER TABLE approvals ADD COLUMN target_id VARCHAR(80)"))
        if "profile_version" not in names:
            conn.execute(text("ALTER TABLE approvals ADD COLUMN profile_version VARCHAR(40)"))


def _migrate_receipts(engine: Engine) -> None:
    with engine.begin() as conn:
        try:
            names = {row[1] for row in conn.execute(text("PRAGMA table_info(receipts)"))}
        except Exception:
            return
        if "ticket_normalized" in names:
            conn.execute(text("UPDATE receipts SET ticket_normalized = ''"))


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _migrate_audit_events(engine)
    _migrate_approvals(engine)
    _migrate_receipts(engine)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.commit()


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


def get_session(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
