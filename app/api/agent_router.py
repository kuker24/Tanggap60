"""Endpoint Conversational Rescue Agent.

Kontrak respons POST messages:
{
  "message": str,            # template tetap dari hasil tool
  "quick_actions": [str],
  "guidance": {"target": str, "label": str} | None,
  "proposed_action": {"action_id","action_type","risk","summary","payload","expected_version"} | None,
  "tools_used": [{"tool","planner","duration_ms"}],
  "agent_response_ms": int, "agent_tool_ms": int,
  "state": str, "case_version": int,
  "technical": {"planner_modes": [...], "fallback_note": str},
}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.agent.context import build_agent_context
from app.agent.service import approve_action, deny_action, handle_message
from app.agent.workspace import prepare_workspace
from app.api.router import sid, svc
from app.domain.models import AuditEventRecord
from app.infrastructure.logging import hash_id
from app.infrastructure.repositories import EventRepository
from app.services.cases import now_utc
from app.services.ids import new_id

agent_api = APIRouter(prefix="/api/v1")

_AGENT_EVENT_TYPES = frozenset(
    {
        "AGENT_MESSAGE",
        "AGENT_PLANNER_DECISION",
        "AGENT_TOOL_REQUEST",
        "AGENT_TOOL_RESULT",
        "GUIDANCE_SHOWN",
        "ACTION_PROPOSED",
        "ACTION_APPROVED",
        "ACTION_DENIED",
        "WORKSPACE_ACTION",
        "SENSITIVE_STOP",
    }
)


@agent_api.post("/cases/{case_id}/agent/messages")
def post_agent_message(case_id: str, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text") or "")[:2000]
    ui_state = payload.get("ui_state") if isinstance(payload.get("ui_state"), dict) else {}
    return handle_message(request.state.db, request.app.state.container, case_id, sid(request), text, ui_state)


@agent_api.post("/cases/{case_id}/agent/actions/{action_id}/approve")
def approve_agent_action(
    case_id: str, action_id: str, request: Request, payload: dict[str, Any]
) -> dict[str, Any]:
    action_type = str(payload.get("action_type") or "")
    raw_payload = payload.get("payload")
    action_payload: dict[str, Any] = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    try:
        expected_version = int(str(payload.get("expected_version")))
    except (TypeError, ValueError):
        from app.domain.errors import ValidationFailed

        raise ValidationFailed("versi data tidak dikenal") from None
    return approve_action(
        request.state.db, request, case_id, sid(request), action_id, action_type, action_payload, expected_version
    )


@agent_api.post("/cases/{case_id}/agent/actions/{action_id}/deny")
def deny_agent_action(case_id: str, action_id: str, request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    action_type = str(payload.get("action_type") or "")
    return deny_action(request.state.db, request.app.state.container, case_id, sid(request), action_type)


@agent_api.get("/cases/{case_id}/agent/context")
def get_agent_context(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    return build_agent_context(request.state.db, case_id)


@agent_api.get("/cases/{case_id}/agent/trail")
def get_agent_trail(case_id: str, request: Request) -> dict[str, Any]:
    svc(request)["cases"].get_owned(case_id, sid(request))
    events = EventRepository(request.state.db).list_for_case(hash_id(case_id))
    return {
        "trail": [
            {
                "event_type": e.event_type,
                "tool_name": e.tool_name,
                "planner": e.planner,
                "duration_ms": e.duration_ms,
                "result_code": e.result_code,
                "error_code": e.error_code,
            }
            for e in events
            if e.event_type in _AGENT_EVENT_TYPES
        ]
    }


@agent_api.get("/cases/{case_id}/workspace")
def get_workspace(case_id: str, request: Request) -> dict[str, Any]:
    case = svc(request)["cases"].get_owned(case_id, sid(request))
    body = prepare_workspace(request.state.db, case_id)
    EventRepository(request.state.db).add(
        AuditEventRecord(
            event_id=new_id("evt"),
            case_id=hash_id(case_id),
            run_id=None,
            event_type="WORKSPACE_ACTION",
            state_before=case.state.value,
            state_after=case.state.value,
            tool_name="prepare_workspace",
            tool_version="2.0.0",
            duration_ms=0,
            result_code="OK",
            error_code=None,
            payload_hash=None,
            created_at=now_utc(),
            planner="DETERMINISTIC_SAFE",
            execution="AGENT_TOOL",
        )
    )
    return body
