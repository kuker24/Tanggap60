from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.config import Settings


class HermesPort(Protocol):
    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        ...


class DeterministicHermes:
    ORDER = {
        "INGESTING": "inspect_evidence",
        "EXTRACTING": "extract_candidate_facts",
        "REVIEW_REQUIRED": "validate_case_facts",
        "READY_FOR_ACTION": None,
        "WAITING_APPROVAL": None,
        "GENERATING": "compile_artifacts",
        "VERIFYING": "verify_artifacts",
        "HANDOFF_READY": "prepare_official_handoff",
        "FAILED_SAFE": None,
    }

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        if state == "EXTRACTING" and summary.get("candidates_done"):
            return "validate_case_facts"
        if state == "READY_FOR_ACTION":
            if summary.get("route") == "PRE_INCIDENT_CHECK":
                return "build_preincident_brief"
            return "build_postincident_plan"
        return self.ORDER.get(state)


class HttpHermes:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.rstrip("/")

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                f"{self.endpoint}/next-tool",
                json={"state": state, "summary": summary},
            )
            response.raise_for_status()
            name = response.json().get("tool")
            return str(name) if name else None


def build_hermes(settings: Settings) -> HermesPort:
    if settings.hermes_endpoint:
        return HttpHermes(settings.hermes_endpoint)
    return DeterministicHermes()
