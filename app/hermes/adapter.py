from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.config import Settings
from app.hermes.tools.catalog import allowed_tools


class HermesPort(Protocol):
    last_mode: str

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        ...


class DeterministicHermes:
    last_mode = "deterministic"
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
        self.last_mode = "deterministic"
        if state == "EXTRACTING" and summary.get("candidates_done"):
            return "validate_case_facts"
        if state == "READY_FOR_ACTION":
            if summary.get("route") == "PRE_INCIDENT_CHECK":
                return "build_preincident_brief"
            return "build_postincident_plan"
        if state == "HANDOFF_READY" and summary.get("handoff_prepared"):
            return None
        return self.ORDER.get(state)


class HttpHermes:
    last_mode = "http"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.rstrip("/")

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                f"{self.endpoint}/next-tool",
                json={"state": state, "summary": summary, "allowed_tools": list(allowed_tools(state))},
            )
            response.raise_for_status()
            name = response.json().get("tool")
            tool = str(name) if name else None
        allowed = allowed_tools(state)
        if tool and tool not in allowed:
            return None
        self.last_mode = "http"
        return tool


class FallbackHermes:
    def __init__(self, primary: HermesPort, fallback: HermesPort) -> None:
        self.primary = primary
        self.fallback = fallback
        self.last_mode = fallback.last_mode

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        try:
            tool = self.primary.propose_tool(state, summary)
            self.last_mode = getattr(self.primary, "last_mode", "http")
            return tool
        except Exception:
            tool = self.fallback.propose_tool(state, summary)
            self.last_mode = getattr(self.fallback, "last_mode", "deterministic")
            return tool


def build_hermes(settings: Settings) -> HermesPort:
    fallback = DeterministicHermes()
    if settings.hermes_endpoint:
        return FallbackHermes(HttpHermes(settings.hermes_endpoint), fallback)
    return fallback
