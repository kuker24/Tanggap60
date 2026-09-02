from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from app.config import Settings
from app.hermes.adapter import (
    CliHermes,
    DeterministicHermes,
    FallbackHermes,
    MechanicalPlan,
    build_hermes,
    parse_tool_reply,
    parse_tools_reply,
)
from app.hermes.telemetry import planner_for
from app.hermes.tools.catalog import TOOL_SPECS, tool_names


def test_tool_catalog_covers_hero_loop() -> None:
    names = set(tool_names())
    for required in {
        "inspect_evidence",
        "extract_candidate_facts",
        "validate_case_facts",
        "build_postincident_plan",
        "compile_artifacts",
        "verify_artifacts",
        "prepare_official_handoff",
        "record_handoff_receipt",
        "purge_case",
    }:
        assert required in names
    assert len(TOOL_SPECS) == 10


def test_deterministic_handoff_after_verify() -> None:
    hermes = DeterministicHermes()
    assert hermes.propose_tool("HANDOFF_READY", {}) == "prepare_official_handoff"
    assert hermes.propose_tool("HANDOFF_READY", {"handoff_prepared": True}) is None
    assert hermes.propose_tool("VERIFYING", {}) == "verify_artifacts"


def test_parse_tool_reply_json_and_fence() -> None:
    allowed = {"inspect_evidence", "extract_candidate_facts"}
    assert parse_tool_reply('{"tool": "inspect_evidence"}', allowed) == "inspect_evidence"
    assert parse_tool_reply("```json\n{\"tool\": null}\n```", allowed) is None


def test_parse_tools_reply_sequence() -> None:
    assert parse_tools_reply('{"tools": ["inspect_evidence", "extract_candidate_facts"]}') == [
        "inspect_evidence",
        "extract_candidate_facts",
    ]
    assert parse_tools_reply('{"tool": null}') == []


def test_cli_hermes_sequence() -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["hermes"],
            returncode=0,
            stdout='{"tools": ["inspect_evidence", "extract_candidate_facts"]}',
            stderr="",
        )

    hermes = CliHermes(command=["hermes"], runner=fake_run)
    assert hermes.propose_sequence("INGESTING", {}) == ["inspect_evidence", "extract_candidate_facts"]
    assert hermes.last_mode == "cli"


def test_parse_tool_reply_rejects_outside_allowlist() -> None:
    try:
        parse_tool_reply('{"tool": "purge_case"}', {"inspect_evidence"})
    except ValueError:
        return
    raise AssertionError("expected allowlist error")


def test_cli_hermes_reads_stdout(monkeypatch: Any) -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["hermes"],
            returncode=0,
            stdout='noise\n{"tool": "inspect_evidence"}\n',
            stderr="",
        )

    hermes = CliHermes(command=["hermes"], runner=fake_run)
    tool = hermes.propose_tool("INGESTING", {"allowed_tools": ["inspect_evidence"]})
    assert tool == "inspect_evidence"
    assert hermes.last_mode == "cli"


def test_fallback_uses_deterministic_when_cli_fails() -> None:
    def boom(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["hermes"], returncode=1, stdout="", stderr="fail")

    hermes = FallbackHermes(CliHermes(command=["hermes"], runner=boom), DeterministicHermes())
    assert hermes.propose_tool("INGESTING", {}) == "inspect_evidence"
    assert hermes.last_mode == "deterministic"


def test_planner_labels() -> None:
    assert planner_for("inspect_evidence", "cli") == "HERMES_CLI"
    assert planner_for("compile_artifacts", "cli") == "DETERMINISTIC_SAFE"
    assert planner_for("record_handoff_receipt", "cli") == "USER"


def test_cli_mechanical_plan() -> None:
    hermes = CliHermes(command=["hermes"])
    try:
        hermes.propose_tool("GENERATING", {})
    except MechanicalPlan:
        return
    raise AssertionError("expected MechanicalPlan")


def test_mechanical_keeps_cli_used() -> None:
    def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["hermes"],
            returncode=0,
            stdout='{"tool": "inspect_evidence"}',
            stderr="",
        )

    wrapped = FallbackHermes(CliHermes(command=["hermes"], runner=fake_run), DeterministicHermes())
    assert wrapped.propose_tool("INGESTING", {"allowed_tools": ["inspect_evidence"]}) == "inspect_evidence"
    assert wrapped.cli_used is True
    assert wrapped.propose_tool("GENERATING", {}) == "compile_artifacts"
    assert wrapped.last_mode == "cli"
    assert wrapped.last_planner_mode == "deterministic"


def test_build_hermes_cli_when_bin_set() -> None:
    settings = Settings(
        secret_key="xxxxxxxxxxxxxxxx",
        case_storage_dir=Path("/tmp/cases"),
        hermes_bin="/home/hermes/.local/bin/hermes",
    )
    built = build_hermes(settings)
    assert isinstance(built, FallbackHermes)
    assert isinstance(built.primary, CliHermes)
