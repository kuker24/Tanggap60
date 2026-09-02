from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.domain.states import DeclaredCondition, Mode, Route, State


class EvidenceKind(StrEnum):
    IMAGE = "IMAGE"
    PDF = "PDF"
    TEXT = "TEXT"
    URL = "URL"
    RECEIPT = "RECEIPT"


class EvidenceStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXTRACTED = "EXTRACTED"
    PURGED = "PURGED"


class FactType(StrEnum):
    PERSON_NAME = "PERSON_NAME"
    PHONE = "PHONE"
    ACCOUNT = "ACCOUNT"
    PJP = "PJP"
    AMOUNT = "AMOUNT"
    DATETIME = "DATETIME"
    CHANNEL = "CHANNEL"
    URL = "URL"
    CLAIM = "CLAIM"
    EVENT = "EVENT"


class Criticality(StrEnum):
    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    OPTIONAL = "OPTIONAL"


class ReviewStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"


class ConflictType(StrEnum):
    VALUE_MISMATCH = "VALUE_MISMATCH"
    TIME_ORDER = "TIME_ORDER"
    DUPLICATE = "DUPLICATE"
    SOURCE_DISAGREEMENT = "SOURCE_DISAGREEMENT"


class ConflictSeverity(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


class ConflictStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ActionPriority(StrEnum):
    NOW = "NOW"
    NEXT = "NEXT"
    LATER = "LATER"


class ActionChannel(StrEnum):
    BANK_PJP = "BANK_PJP"
    IASC = "IASC"
    POLICE = "POLICE"
    ACCOUNT_SECURITY = "ACCOUNT_SECURITY"
    MANUAL_VERIFY = "MANUAL_VERIFY"


class ActionStatus(StrEnum):
    TODO = "TODO"
    USER_CONFIRMED_DONE = "USER_CONFIRMED_DONE"
    SKIPPED = "SKIPPED"


class ApprovalScope(StrEnum):
    PRE_BRIEF = "PRE_BRIEF"
    POST_CASE_PACK = "POST_CASE_PACK"


class ArtifactType(StrEnum):
    VERIFICATION_BRIEF = "VERIFICATION_BRIEF"
    ACTION_PLAN = "ACTION_PLAN"
    EVIDENCE_PACK = "EVIDENCE_PACK"
    CASE_JSON = "CASE_JSON"
    CHECKLIST = "CHECKLIST"
    MANIFEST = "MANIFEST"
    CASE_ZIP = "CASE_ZIP"


class VerifyStatus(StrEnum):
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"


class ReceiptSource(StrEnum):
    USER_INPUT = "USER_INPUT"
    RECEIPT_OCR = "RECEIPT_OCR"
    BOTH = "BOTH"


class FormatStatus(StrEnum):
    NOT_CHECKED = "NOT_CHECKED"
    PLAUSIBLE = "PLAUSIBLE"
    UNRECOGNIZED = "UNRECOGNIZED"


class LocalMatchStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


class UserDecision(StrEnum):
    CANCELLED_ACTION = "CANCELLED_ACTION"
    VERIFY_VIA_OFFICIAL_CHANNEL = "VERIFY_VIA_OFFICIAL_CHANNEL"
    PROCEED_BY_USER = "PROCEED_BY_USER"


OFFICIAL_STATUS = "NOT_VERIFIED"
REVIEWED_FACT_STATUSES = frozenset(
    {ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED, ReviewStatus.UNAVAILABLE}
)


@dataclass
class CaseRecord:
    case_id: str
    mode: Mode
    route: Route
    state: State
    declared_condition: DeclaredCondition
    owner_session_id: str
    case_token_hash: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    version: int = 1
    route_reason: str = ""
    route_confidence: float = 0.0
    approved_snapshot_hash: str | None = None
    user_decision: str | None = None
    ask_loss_question: bool = False


@dataclass
class EvidenceRecord:
    evidence_id: str
    case_id: str
    kind: EvidenceKind
    original_name_display: str
    storage_key: str
    mime: str
    size_bytes: int
    sha256: str
    page_count: int
    status: EvidenceStatus
    retention_until: datetime
    extracted_text_ref: str | None = None
    warning: str | None = None


@dataclass
class FactRecord:
    fact_id: str
    case_id: str
    type: FactType
    raw_value: str
    normalized_value: str | None
    criticality: Criticality
    confidence: float
    review_status: ReviewStatus
    source_evidence_id: str
    source_page: int | None
    source_bbox: str | None
    source_excerpt_hash: str
    corrected_from_fact_id: str | None = None


@dataclass
class ConflictRecord:
    conflict_id: str
    case_id: str
    type: ConflictType
    fact_ids: list[str]
    severity: ConflictSeverity
    status: ConflictStatus
    resolution_fact_id: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None


@dataclass
class ActionRecord:
    action_id: str
    case_id: str
    priority: ActionPriority
    channel: ActionChannel
    instruction: str
    status: ActionStatus
    official_url_key: str | None = None
    requires_external_user_action: bool = True


@dataclass
class TransactionGroupRecord:
    transaction_group_id: str
    case_id: str
    victim_account: str | None
    destination_account: str
    amount: float
    transferred_at: str
    evidence_ids: list[str]
    readiness: str


@dataclass
class ApprovalRecord:
    approval_id: str
    case_id: str
    actor: str
    scope: ApprovalScope
    snapshot_hash: str
    approved_at: datetime
    notice_version: str
    revoked_at: datetime | None = None
    revoke_reason: str | None = None


@dataclass
class ArtifactRecord:
    artifact_id: str
    case_id: str
    type: ArtifactType
    storage_key: str
    mime: str
    size_bytes: int
    sha256: str
    source_snapshot_hash: str
    verify_status: VerifyStatus
    verify_details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReceiptRecord:
    receipt_id: str
    case_id: str
    ticket_value_masked: str
    source: ReceiptSource
    format_status: FormatStatus
    local_match_status: LocalMatchStatus
    official_status: str
    recorded_at: datetime
    receipt_evidence_id: str | None = None


@dataclass
class AuditEventRecord:
    event_id: str
    case_id: str
    run_id: str | None
    event_type: str
    state_before: str | None
    state_after: str | None
    tool_name: str | None
    tool_version: str | None
    duration_ms: int | None
    result_code: str | None
    error_code: str | None
    payload_hash: str | None
    created_at: datetime
    planner: str | None = None
    execution: str | None = None
