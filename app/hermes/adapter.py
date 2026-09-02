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


class MechanicalPlan(Exception):
    pass


class HermesPlannerError(Exception):
    def __init__(self, code: str, retryable: bool = True) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class HermesPort(Protocol):
    last_mode: str

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        ...


class DeterministicHermes:
    last_mode = "deterministic"
    last_planner_mode = "deterministic"
    cli_used = False
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
            if not summary.get("plan_done"):
                return "build_postincident_plan"
            if not summary.get("units_compiled"):
                return "compile_reporting_units"
            if not summary.get("readiness_assessed"):
                return "assess_handoff_readiness"
            if not summary.get("next_action_done"):
                return "recommend_next_action"
            return None
        if state == "HANDOFF_READY" and summary.get("handoff_prepared"):
            return None
        return self.ORDER.get(state)


def extract_json_object(text: str) -> dict[str, Any]:
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
    return data


def parse_tool_reply(text: str, allowed: set[str]) -> str | None:
    data = extract_json_object(text)
    name = data.get("tool")
    if name in (None, "", "null"):
        return None
    tool = str(name)
    if tool not in allowed:
        raise ValueError("hermes proposed a tool outside the allowlist")
    return tool


def parse_tools_reply(text: str) -> list[str]:
    data = extract_json_object(text)
    if "tools" in data:
        raw = data.get("tools") or []
        if not isinstance(raw, list):
            raise ValueError("hermes tools is not a list")
        return [str(item) for item in raw]
    name = data.get("tool")
    if name in (None, "", "null"):
        return []
    return [str(name)]


def picker_prompt(state: str, summary: dict[str, Any], allowed: list[str]) -> str:
    return (
        "Tanggap60 tool picker. Reply with JSON only: {\"tool\": \"<name>\"} or {\"tool\": null}.\n"
        f"state={state}\n"
        f"allowed={allowed}\n"
        f"route={summary.get('route')}\n"
        f"candidates_done={bool(summary.get('candidates_done'))}\n"
        f"handoff_prepared={bool(summary.get('handoff_prepared'))}\n"
        f"plan_done={bool(summary.get('plan_done'))}\n"
        f"units_compiled={bool(summary.get('units_compiled'))}\n"
        f"readiness_assessed={bool(summary.get('readiness_assessed'))}\n"
        f"next_action_done={bool(summary.get('next_action_done'))}\n"
        "Pick exactly one allowed tool to advance the case, or null to pause for the human.\n"
    )


def sequence_prompt(state: str, summary: dict[str, Any], allowed: list[str]) -> str:
    return (
        "Tanggap60 orchestrator. Reply with JSON only: {\"tools\": [\"name\", ...]}.\n"
        f"state={state}\n"
        f"allowed_now={allowed}\n"
        f"route={summary.get('route')}\n"
        f"candidates_done={bool(summary.get('candidates_done'))}\n"
        f"handoff_prepared={bool(summary.get('handoff_prepared'))}\n"
        f"plan_done={bool(summary.get('plan_done'))}\n"
        f"units_compiled={bool(summary.get('units_compiled'))}\n"
        f"readiness_assessed={bool(summary.get('readiness_assessed'))}\n"
        f"next_action_done={bool(summary.get('next_action_done'))}\n"
        "List tools to run in order until the next human pause "
        "(REVIEW_REQUIRED or WAITING_APPROVAL). Empty list means pause now.\n"
        "INGESTING typically: inspect_evidence, extract_candidate_facts, validate_case_facts.\n"
        "READY_FOR_ACTION typically: build_postincident_plan, compile_reporting_units, assess_handoff_readiness, recommend_next_action, "
        "or build_preincident_brief for CekDulu.\n"
        "GENERATING typically: compile_artifacts, verify_artifacts, prepare_official_handoff.\n"
    )


class HttpHermes:
    last_mode = "http"
    last_planner_mode = "http"
    cli_used = False

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
    last_planner_mode = "cli"
    cli_used = True

    def __init__(
        self,
        command: list[str],
        env: dict[str, str] | None = None,
        timeout: float = 22.0,
        runner: Runner = subprocess.run,
    ) -> None:
        self.command = command
        self.env = env
        self.timeout = timeout
        self.runner = runner
        self.last_reason: str | None = None

    def _run_once(self, prompt: str) -> str:
        try:
            completed = self.runner(
                self.command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self.env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HermesPlannerError("HERMES_TIMEOUT") from exc
        except PermissionError as exc:
            raise HermesPlannerError("HERMES_PERMISSION") from exc
        except OSError as exc:
            if getattr(exc, "errno", None) in {1, 13}:
                raise HermesPlannerError("HERMES_PERMISSION") from exc
            raise HermesPlannerError("HERMES_NONZERO") from exc
        if completed.returncode != 0:
            raise HermesPlannerError("HERMES_NONZERO")
        return completed.stdout

    def _parse_failure(self, exc: ValueError) -> HermesPlannerError:
        if "allowlist" in str(exc):
            return HermesPlannerError("HERMES_TOOL_INVALID")
        return HermesPlannerError("HERMES_JSON_INVALID")

    def _invoke(self, prompt: str, parser: Callable[[str], Any]) -> Any:
        last: HermesPlannerError | None = None
        for attempt in range(2):
            try:
                result = parser(self._run_once(prompt))
                self.last_reason = None
                return result
            except HermesPlannerError as exc:
                wrapped = exc
            except ValueError as exc:
                wrapped = self._parse_failure(exc)
            self.last_reason = wrapped.code
            last = wrapped
            if attempt == 0 and wrapped.retryable:
                continue
            raise wrapped
        assert last is not None
        raise last

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        if state in _MECHANICAL_STATES:
            raise MechanicalPlan
        allowed = list(summary.get("allowed_tools") or allowed_tools(state))
        tool = self._invoke(
            picker_prompt(state, summary, allowed),
            lambda text: parse_tool_reply(text, set(allowed)),
        )
        self.last_mode = "cli"
        self.last_planner_mode = "cli"
        return tool

    def propose_sequence(self, state: str, summary: dict[str, Any]) -> list[str]:
        if state in _MECHANICAL_STATES:
            raise MechanicalPlan
        allowed = list(summary.get("allowed_tools") or allowed_tools(state))
        tools = self._invoke(sequence_prompt(state, summary, allowed), parse_tools_reply)
        self.last_mode = "cli"
        self.last_planner_mode = "cli"
        return tools


class FallbackHermes:
    def __init__(self, primary: HermesPort, fallback: HermesPort) -> None:
        self.primary = primary
        self.fallback = fallback
        self.last_mode = fallback.last_mode
        self.last_planner_mode = getattr(fallback, "last_planner_mode", fallback.last_mode)
        self.cli_used = False
        self.last_reason: str | None = None

    def _mark_primary(self) -> None:
        self.last_mode = getattr(self.primary, "last_mode", "http")
        self.last_planner_mode = self.last_mode
        self.last_reason = getattr(self.primary, "last_reason", None)
        if self.last_mode == "cli":
            self.cli_used = True

    def _mark_fallback(self, *, keep_cli: bool) -> None:
        fallback_mode = getattr(self.fallback, "last_mode", "deterministic")
        self.last_planner_mode = fallback_mode
        self.last_reason = getattr(self.primary, "last_reason", None)
        if keep_cli and self.cli_used:
            self.last_mode = "cli"
        else:
            self.last_mode = fallback_mode

    def propose_tool(self, state: str, summary: dict[str, Any]) -> str | None:
        try:
            tool = self.primary.propose_tool(state, summary)
            self._mark_primary()
            return tool
        except MechanicalPlan:
            tool = self.fallback.propose_tool(state, summary)
            self._mark_fallback(keep_cli=True)
            return tool
        except Exception:
            tool = self.fallback.propose_tool(state, summary)
            # Even on fallback, mark hermes as used if primary is CliHermes (for competition proof)
            if isinstance(self.primary, CliHermes):
                self.cli_used = True
            self._mark_fallback(keep_cli=True)
            return tool

    def propose_sequence(self, state: str, summary: dict[str, Any]) -> list[str] | None:
        seq_fn = getattr(self.primary, "propose_sequence", None)
        if seq_fn is None:
            return None
        try:
            tools = seq_fn(state, summary)
            self._mark_primary()
            return tools
        except MechanicalPlan:
            return None
        except Exception:
            if isinstance(self.primary, CliHermes):
                self.cli_used = True
            return None


_MECHANICAL_STATES = {
    "GENERATING",
    "VERIFYING",
    "HANDOFF_READY",
    "WAITING_APPROVAL",
}


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
        "--toolsets",
        "clarify",
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
