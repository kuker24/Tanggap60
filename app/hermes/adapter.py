from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.hermes.tools.catalog import allowed_tools

Runner = Callable[..., subprocess.CompletedProcess[str]]


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


def parse_tool_reply(text: str, allowed: set[str]) -> str | None:
    blob = text.strip()
    if "```" in blob:
        parts = blob.split("```")
        blob = max(parts, key=lambda p: p.count("{"))
        if blob.lstrip().startswith("json"):
            blob = blob.lstrip()[4:]
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("hermes reply is not JSON")
    data = json.loads(blob[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("hermes reply is not an object")
    name = data.get("tool")
    if name in (None, "", "null"):
        return None
    tool = str(name)
    if tool not in allowed:
        raise ValueError("hermes proposed a tool outside the allowlist")
    return tool


def picker_prompt(state: str, summary: dict[str, Any], allowed: list[str]) -> str:
    return (
        "Tanggap60 tool picker. Reply with JSON only: {\"tool\": \"<name>\"} or {\"tool\": null}.\n"
        f"state={state}\n"
        f"allowed={allowed}\n"
        f"route={summary.get('route')}\n"
        f"candidates_done={bool(summary.get('candidates_done'))}\n"
        f"handoff_prepared={bool(summary.get('handoff_prepared'))}\n"
        "Pick exactly one allowed tool to advance the case, or null to pause for the human.\n"
    )


class HttpHermes:
    last_mode = "http"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.rstrip("/")

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        allowed = list(allowed_tools(state))
        with httpx.Client(timeout=8.0) as client:
            response = client.post(
                f"{self.endpoint}/next-tool",
                json={"state": state, "summary": summary, "allowed_tools": allowed},
            )
            response.raise_for_status()
            payload = response.json()
        raw = payload.get("tool")
        if raw in (None, "", "null"):
            self.last_mode = "http"
            return None
        tool = str(raw)
        if tool not in set(allowed):
            return None
        self.last_mode = "http"
        return tool


class CliHermes:
    last_mode = "cli"

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        timeout: float = 25.0,
        runner: Runner = subprocess.run,
    ) -> None:
        self.command = command
        self.env = env
        self.timeout = timeout
        self.runner = runner

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        allowed = list(summary.get("allowed_tools") or allowed_tools(state))
        prompt = picker_prompt(state, summary, allowed)
        completed = self.runner(
            self.command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=self.env,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("hermes cli failed")
        tool = parse_tool_reply(completed.stdout, set(allowed))
        self.last_mode = "cli"
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


def _cli_command(binary: str) -> list[str]:
    return [
        binary,
        "chat",
        "--oneshot",
        "--quiet",
        "--max-turns",
        "1",
        "--ignore-rules",
        "--source",
        "tool",
        "--query-file",
        "-",
    ]


def _cli_env(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    home = settings.hermes_home or "/home/hermes/.hermes"
    env["HERMES_HOME"] = home
    env["HOME"] = "/home/hermes" if home.startswith("/home/hermes") else env.get("HOME", home)
    env["PATH"] = f"/home/hermes/.local/bin:/home/hermes/.hermes/bin:{env.get('PATH', '')}"
    return env


def build_hermes(settings: Settings) -> HermesPort:
    fallback = DeterministicHermes()
    if settings.hermes_endpoint:
        return FallbackHermes(HttpHermes(settings.hermes_endpoint), fallback)
    if settings.hermes_bin:
        return FallbackHermes(
            CliHermes(_cli_command(settings.hermes_bin), env=_cli_env(settings)),
            fallback,
        )
    return fallback
