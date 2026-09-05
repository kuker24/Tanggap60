from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.errors import AppError, NotFound
from app.domain.models import AuditEventRecord
from app.domain.policies import sha256_text
from app.domain.states import State, transition
from app.hermes.adapter import HermesPort
from app.hermes.telemetry import execution_for, mode_from_hermes, planner_for
from app.hermes.tool_registry import ToolContext, execute_tool
from app.hermes.tools.catalog import allowed_tools
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
        case = self.case_repo.get(case_id)
        summary0 = {
            "route": case.route.value,
            "candidates_done": bool(self.facts.list_for_case(case_id)),
            "allowed_tools": list(allowed_tools(case.state.value)),
                "handoff_prepared": False,
                "plan_done": False,
                "readiness_assessed": False,
            }
        planned: list[str] | None = None
        planned_mode = "deterministic"
        seq_ms = 0
        seq_attempt_1_ms = 0
        seq_attempt_2_ms = 0
        seq_total_ms = 0
        seq_fn = getattr(self.hermes, "propose_sequence", None)
        if seq_fn is not None:
            started = time.perf_counter()
            try:
                raw = seq_fn(case.state.value, summary0)
                if raw is not None:
                    planned = [str(item) for item in raw]
                    planned_mode = mode_from_hermes(self.hermes)
            except Exception:
                planned = None
            seq_ms = int((time.perf_counter() - started) * 1000)
            # capture separated hermes attempt timings (no cross-case cache)
            seq_attempt_1_ms = int(getattr(self.hermes, "last_attempt_1_ms", 0) or 0)
            seq_attempt_2_ms = int(getattr(self.hermes, "last_attempt_2_ms", 0) or 0)
            seq_total_ms = int(getattr(self.hermes, "last_total_ms", seq_ms) or seq_ms)
        for _ in range(16):
            case = self.case_repo.get(case_id)
            if self._should_pause(case.state, trace):
                break
            summary = {
                "route": case.route.value,
                "candidates_done": bool(self.facts.list_for_case(case_id)),
                "extract_done": "extract_candidate_facts" in trace,
                "allowed_tools": list(allowed_tools(case.state.value)),
                "handoff_prepared": "prepare_official_handoff" in trace,
                "plan_done": "build_postincident_plan" in trace or "build_preincident_brief" in trace,
                "units_compiled": "compile_reporting_units" in trace,
                "readiness_assessed": "assess_handoff_readiness" in trace,
                "next_action_done": "recommend_next_action" in trace,
            }
            # Mechanical tools forced deterministic; reasoning via Hermes (build_postincident_plan, assess_handoff_readiness must be HERMES_CLI)
            forced = None
            if case.state == State.READY_FOR_ACTION and case.route.value == "POST_INCIDENT_RESPONSE":
                # Only force mechanical steps deterministically, allow Hermes to orchestrate reasoning
                if "build_postincident_plan" in trace and "compile_reporting_units" not in trace:
                    forced = "compile_reporting_units"
                elif "assess_handoff_readiness" in trace and "recommend_next_action" not in trace:
                    forced = "recommend_next_action"
            source_mode = planned_mode
            pick_ms = 0
            pick_attempt_1_ms = 0
            pick_attempt_2_ms = 0
            tool: str | None = None
            if forced and forced in allowed_tools(case.state.value):
                tool = forced
                source_mode = "deterministic"
                # keep planned sequence in sync to avoid duplicate/redundant Hermes picker after forced deterministic step
                if planned and planned[0] == forced:
                    planned.pop(0)
            elif planned:
                tool = planned.pop(0)
                if tool not in allowed_tools(case.state.value):
                    planned = None
                    started = time.perf_counter()
                    tool = self.hermes.propose_tool(case.state.value, summary)
                    pick_ms = int((time.perf_counter() - started) * 1000)
                    source_mode = mode_from_hermes(self.hermes)
                    pick_attempt_1_ms = int(getattr(self.hermes, "last_attempt_1_ms", 0) or 0)
                    pick_attempt_2_ms = int(getattr(self.hermes, "last_attempt_2_ms", 0) or 0)
            else:
                started = time.perf_counter()
                tool = self.hermes.propose_tool(case.state.value, summary)
                pick_ms = int((time.perf_counter() - started) * 1000)
                source_mode = mode_from_hermes(self.hermes)
                pick_attempt_1_ms = int(getattr(self.hermes, "last_attempt_1_ms", 0) or 0)
                pick_attempt_2_ms = int(getattr(self.hermes, "last_attempt_2_ms", 0) or 0)
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
                self._trace(case_id, run_id, tool, case.state.value, "ERROR", exc.code, None, source_mode)
                return {
                    "status": "FAILED_SAFE",
                    "trace": trace,
                    "error": exc.code,
                    "hermes_mode": getattr(self.hermes, "last_mode", "deterministic"),
                    "hermes_cli_used": bool(getattr(self.hermes, "cli_used", False)),
                    "hermes_cli_configured": bool(getattr(self.hermes, "hermes_cli_configured", False)),
                    "hermes_cli_attempted": bool(getattr(self.hermes, "hermes_cli_attempted", False)),
                    "hermes_cli_succeeded": bool(getattr(self.hermes, "hermes_cli_succeeded", False)),
                    "hermes_fallback_used": bool(getattr(self.hermes, "hermes_fallback_used", False)),
                    "hermes_failure_reason": getattr(self.hermes, "hermes_failure_reason", None),
                }
            trace.append(tool)
            case = self.case_repo.get(case_id)
            if tool == "build_preincident_brief" and case.state == State.READY_FOR_ACTION:
                self.cases.set_state(case, State.WAITING_APPROVAL, event_type="WAITING_APPROVAL", run_id=run_id)
            if tool == "recommend_next_action" and case.state == State.READY_FOR_ACTION:
                self.cases.set_state(case, State.WAITING_APPROVAL, event_type="WAITING_APPROVAL", run_id=run_id)
            elif tool == "assess_handoff_readiness" and case.state == State.READY_FOR_ACTION and "recommend_next_action" not in trace:
                # fallback if recommend not yet - allow assess to also transition when recommend not present (legacy)
                if "recommend_next_action" not in allowed_tools(case.state.value):
                    self.cases.set_state(case, State.WAITING_APPROVAL, event_type="WAITING_APPROVAL", run_id=run_id)
            if tool == "compile_artifacts" and case.state == State.GENERATING:
                self.cases.set_state(case, State.VERIFYING, event_type="GENERATING_DONE", run_id=run_id)
            if tool == "verify_artifacts" and case.state == State.VERIFYING:
                self.cases.set_state(case, State.HANDOFF_READY, event_type="HANDOFF_READY", run_id=run_id)
            handler_ms = int(result.get("duration_ms") or 0)
            # ocr vs model latency (both external, not local)
            ocr_ms = 0
            if tool == "inspect_evidence":
                ocr_ms = int(result.get("ocr_total_ms") or 0)
            elif tool == "extract_candidate_facts":
                ocr_ms = int(result.get("model_total_ms") or 0)
            # separated planner vs handler (do not misattribute Hermes to build_postincident_plan)
            is_first = (seq_ms != 0)
            planner_ms = pick_ms + (seq_ms if is_first else 0)
            # attempt breakdown: sequence attempts for first tool, picker attempts otherwise
            if is_first and seq_total_ms:
                hermes_attempt_1_ms = seq_attempt_1_ms if seq_attempt_1_ms else pick_attempt_1_ms
                hermes_attempt_2_ms = seq_attempt_2_ms if seq_attempt_2_ms else pick_attempt_2_ms
                hermes_sequence_ms = seq_ms
            else:
                hermes_attempt_1_ms = pick_attempt_1_ms
                hermes_attempt_2_ms = pick_attempt_2_ms
                hermes_sequence_ms = 0
            # keep total duration for backward compat but store separated
            duration = handler_ms + planner_ms
            # consume sequence only once
            if is_first:
                seq_ms = 0
                seq_attempt_1_ms = 0
                seq_attempt_2_ms = 0
                seq_total_ms = 0
            self._trace(
                case_id,
                run_id,
                tool,
                case.state.value,
                "OK",
                None,
                duration,
                source_mode,
                planner_ms=planner_ms,
                handler_ms=handler_ms,
                hermes_attempt_1_ms=hermes_attempt_1_ms,
                hermes_attempt_2_ms=hermes_attempt_2_ms,
                hermes_sequence_ms=hermes_sequence_ms,
                ocr_total_ms=ocr_ms if ocr_ms else None,
            )
            self.session.commit()
            if self._should_pause(self.case_repo.get(case_id).state, trace):
                break
        return {
            "status": "OK",
            "trace": trace,
            "hermes_mode": getattr(self.hermes, "last_mode", "deterministic"),
            "hermes_cli_used": bool(getattr(self.hermes, "cli_used", False)),
            "hermes_cli_configured": bool(getattr(self.hermes, "hermes_cli_configured", False)),
            "hermes_cli_attempted": bool(getattr(self.hermes, "hermes_cli_attempted", False)),
            "hermes_cli_succeeded": bool(getattr(self.hermes, "hermes_cli_succeeded", False)),
            "hermes_fallback_used": bool(getattr(self.hermes, "hermes_fallback_used", False)),
            "hermes_failure_reason": getattr(self.hermes, "hermes_failure_reason", None),
        }

    def run_tool(self, case_id: str, run_id: str, tool: str, extra: dict[str, object] | None = None) -> dict[str, object]:
        case = self.case_repo.get(case_id)
        args: dict[str, object] = {
            "case_id": case_id,
            "approved_snapshot_hash": case.approved_snapshot_hash,
            "session_id": case.owner_session_id,
        }
        if extra:
            args.update(extra)
        result = execute_tool(tool, case.state, args, self.ctx)
        if tool == "purge_case":
            return result
        try:
            case = self.case_repo.get(case_id)
            state_after = case.state.value
        except NotFound:
            state_after = "PURGED"
        self._trace(case_id, run_id, tool, state_after, "OK", None, int(result.get("duration_ms") or 0), "user")
        return result

    def _should_pause(self, state: State, trace: list[str]) -> bool:
        if state in {State.WAITING_APPROVAL, State.RECEIPT_RECORDED, State.COMPLETE, State.PURGED}:
            return True
        if state == State.REVIEW_REQUIRED and "validate_case_facts" in trace:
            return True
        if state == State.HANDOFF_READY and "prepare_official_handoff" in trace:
            return True
        return False

    def _trace(
        self,
        case_id: str,
        run_id: str,
        tool: str,
        state_after: str,
        result: str,
        error: str | None,
        duration: int | None,
        source_mode: str = "deterministic",
        planner_ms: int | None = None,
        handler_ms: int | None = None,
        hermes_attempt_1_ms: int | None = None,
        hermes_attempt_2_ms: int | None = None,
        hermes_sequence_ms: int | None = None,
        ocr_total_ms: int | None = None,
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
                planner=planner_for(tool, source_mode),
                execution=execution_for(tool),
                planner_ms=planner_ms,
                handler_ms=handler_ms,
                hermes_attempt_1_ms=hermes_attempt_1_ms,
                hermes_attempt_2_ms=hermes_attempt_2_ms,
                hermes_sequence_ms=hermes_sequence_ms,
                ocr_total_ms=ocr_total_ms,
            )
        )
