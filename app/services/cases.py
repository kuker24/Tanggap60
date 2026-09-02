from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.errors import CaseExpired, Forbidden, NotFound
from app.domain.models import AuditEventRecord, CaseRecord
from app.domain.policies import route_from_condition
from app.domain.states import DeclaredCondition, Mode, State, transition
from app.infrastructure.logging import hash_id
from app.infrastructure.repositories import CaseRepository, EventRepository
from app.infrastructure.storage import CaseStorage
from app.services.ids import new_id


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CaseService:
    def __init__(self, session: Session, settings: Settings, storage: CaseStorage) -> None:
        self.session = session
        self.settings = settings
        self.storage = storage
        self.cases = CaseRepository(session)
        self.events = EventRepository(session)

    def create(
        self,
        *,
        mode: Mode,
        condition: DeclaredCondition,
        session_id: str,
    ) -> tuple[CaseRecord, str]:
        token = secrets.token_urlsafe(24)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        ttl = self.settings.demo_ttl_seconds if mode == Mode.DEMO else self.settings.case_ttl_seconds
        created = now_utc()
        route, reason, confidence, ask = route_from_condition(condition, has_loss_facts=False)
        case = CaseRecord(
            case_id=new_id("case"),
            mode=mode,
            route=route,
            state=State.NEW,
            declared_condition=condition,
            owner_session_id=session_id,
            case_token_hash=token_hash,
            created_at=created,
            updated_at=created,
            expires_at=created + timedelta(seconds=ttl),
            route_reason=reason,
            route_confidence=confidence,
            ask_loss_question=ask,
        )
        self.cases.add(case)
        self.storage.ensure_case_dir(case.case_id)
        self._event(case, "CASE_CREATED", None, State.NEW.value)
        return case, token

    def get_owned(self, case_id: str, session_id: str, token: str | None = None) -> CaseRecord:
        case = self.cases.get(case_id)
        if case.state == State.PURGED:
            raise NotFound("case not found")
        if now_utc() > case.expires_at:
            raise CaseExpired("case expired")
        token_ok = False
        if token:
            digest = hashlib.sha256(token.encode()).hexdigest()
            token_ok = digest == case.case_token_hash
        if case.owner_session_id != session_id and not token_ok:
            raise Forbidden("bukan pemilik kasus")
        return case

    def set_state(self, case: CaseRecord, target: State, *, event_type: str, run_id: str | None = None) -> CaseRecord:
        before = case.state
        case.state = transition(before, target)
        case.updated_at = now_utc()
        self.cases.bump(case, expected_version=case.version)
        self._event(case, event_type, before.value, case.state.value, run_id=run_id)
        return case

    def touch(self, case: CaseRecord) -> CaseRecord:
        case.updated_at = now_utc()
        return self.cases.bump(case, expected_version=case.version)

    def _event(
        self,
        case: CaseRecord,
        event_type: str,
        state_before: str | None,
        state_after: str | None,
        *,
        run_id: str | None = None,
        tool_name: str | None = None,
        duration_ms: int | None = None,
        result_code: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.events.add(
            AuditEventRecord(
                event_id=new_id("evt"),
                case_id=hash_id(case.case_id),
                run_id=run_id,
                event_type=event_type,
                state_before=state_before,
                state_after=state_after,
                tool_name=tool_name,
                tool_version=None,
                duration_ms=duration_ms,
                result_code=result_code,
                error_code=error_code,
                payload_hash=None,
                created_at=now_utc(),
            )
        )
