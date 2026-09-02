from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.errors import AppError
from app.domain.models import AuditEventRecord
from app.domain.policies import sha256_text
from app.domain.states import State, transition
from app.hermes.adapter import HermesPort
from app.hermes.tool_registry import ToolContext, execute_tool
from app.infrastructure.logging import hash_id
from app.infrastructure.repositories import CaseRepository, EventRepository, FactRepository
from app.services.cases import CaseService, now_utc
from app.services.ids import new_id


class Orchestrator:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        cases: CaseService,
        hermes: HermesPort,
        ctx: ToolContext,
    ) -> None:
        self.session = session
        self.settings = settings
        self.cases = cases
        self.hermes = hermes
        self.ctx = ctx
        self.case_repo = CaseRepository(session)
        self.events = EventRepository(session)
        self.facts = FactRepository(session)

    def run_until_pause(self, case_id: str, run_id: str) -> dict[str, object]:
        trace: list[str] = []
        for _ in range(12):
            case = self.case_repo.get(case_id)
            if case.state in {
                State.REVIEW_REQUIRED,
                State.WAITING_APPROVAL,
                State.HANDOFF_READY,
                State.RECEIPT_RECORDED,
                State.COMPLETE,
                State.PURGED,
            }:
                if case.state == State.REVIEW_REQUIRED and not self.facts.list_for_case(case_id):
                    pass
                elif case.state == State.REVIEW_REQUIRED and "validate_case_facts" in trace:
                    break
                elif case.state != State.REVIEW_REQUIRED:
                    break
            summary = {
                "route": case.route.value,
                "candidates_done": bool(self.facts.list_for_case(case_id)),
            }
            tool = self.hermes.propose_tool(case.state.value, summary)
            if tool is None:
                break
            if tool == "validate_case_facts" and case.state == State.REVIEW_REQUIRED and tool in trace:
                break
            try:
                result = execute_tool(
                    tool,
                    case.state,
                    {
                        "case_id": case_id,
                        "approved_snapshot_hash": case.approved_snapshot_hash,
                        "session_id": case.owner_session_id,
                    },
                    self.ctx,
                )
            except AppError as exc:
                case = self.case_repo.get(case_id)
                case.state = transition(case.state, State.FAILED_SAFE)
                self.cases.touch(case)
                self._trace(case_id, run_id, tool, case.state.value, "ERROR", exc.code, None)
                return {"status": "FAILED_SAFE", "trace": trace, "error": exc.code}
            trace.append(tool)
            case = self.case_repo.get(case_id)
            if tool == "extract_candidate_facts" and case.state == State.EXTRACTING:
                pass
            if tool == "build_postincident_plan" or tool == "build_preincident_brief":
                if case.state == State.READY_FOR_ACTION:
                    self.cases.set_state(case, State.WAITING_APPROVAL, event_type="WAITING_APPROVAL", run_id=run_id)
            if tool == "compile_artifacts" and case.state == State.GENERATING:
                self.cases.set_state(case, State.VERIFYING, event_type="GENERATING_DONE", run_id=run_id)
            if tool == "verify_artifacts" and case.state == State.VERIFYING:
                self.cases.set_state(case, State.HANDOFF_READY, event_type="HANDOFF_READY", run_id=run_id)
            self._trace(
                case_id,
                run_id,
                tool,
                case.state.value,
                "OK",
                None,
                int(result.get("duration_ms") or 0),
            )
            if case.state in {State.REVIEW_REQUIRED, State.WAITING_APPROVAL, State.HANDOFF_READY}:
                break
        return {"status": "OK", "trace": trace}

    def _trace(
        self,
        case_id: str,
        run_id: str,
        tool: str,
        state_after: str,
        result: str,
        error: str | None,
        duration: int | None,
    ) -> None:
        self.events.add(
            AuditEventRecord(
                event_id=new_id("evt"),
                case_id=hash_id(case_id),
                run_id=run_id,
                event_type="TOOL_CALLED",
                state_before=None,
                state_after=state_after,
                tool_name=tool,
                tool_version="2.0.0",
                duration_ms=duration,
                result_code=result,
                error_code=error,
                payload_hash=sha256_text(tool),
                created_at=now_utc(),
            )
        )
