from __future__ import annotations

import subprocess

from app.hermes.adapter import CliHermes, DeterministicHermes, FallbackHermes


def _fake_runner_ok(prompt=""):
    # returns valid JSON tool
    class R:
        returncode = 0
        stdout = '{"tool": "inspect_evidence"}'
    return R()


def _fake_runner_invalid_json(prompt=""):
    class R:
        returncode = 0
        stdout = 'not json'
    return R()


def _fake_runner_timeout(*a, **kw):
    raise subprocess.TimeoutExpired(cmd="hermes", timeout=1)


def test_cli_exception_marks_attempted_not_succeeded():
    # CliHermes with timeout -> should set failure
    cli = CliHermes(command=["hermes"], runner=lambda *a, **kw: _fake_runner_timeout())
    fallback = DeterministicHermes()
    fb = FallbackHermes(cli, fallback)
    # propose_tool should fallback
    _tool = fb.propose_tool("INGESTING", {"allowed_tools": ["inspect_evidence"]})
    assert fb.hermes_cli_attempted is True
    assert fb.hermes_cli_succeeded is False
    assert fb.hermes_fallback_used is True
    assert fb.hermes_failure_reason == "HERMES_TIMEOUT"
    assert fb.cli_used is False  # cli_used true only on success
    assert fb.last_mode == "deterministic"


def test_cli_success_marks_succeeded():
    cli = CliHermes(command=["hermes"], runner=lambda *a, **kw: _fake_runner_ok())
    fallback = DeterministicHermes()
    fb = FallbackHermes(cli, fallback)
    tool = fb.propose_tool("INGESTING", {"allowed_tools": ["inspect_evidence"]})
    assert tool == "inspect_evidence"
    assert fb.hermes_cli_attempted is True
    assert fb.hermes_cli_succeeded is True
    assert fb.hermes_fallback_used is False
    assert fb.cli_used is True
    assert fb.last_mode == "cli"


def test_mechanical_does_not_count_as_fallback():
    cli = CliHermes(command=["hermes"], runner=lambda *a, **kw: _fake_runner_invalid_json())
    fallback = DeterministicHermes()
    fb = FallbackHermes(cli, fallback)
    # GENERATING is mechanical
    tool = fb.propose_tool("GENERATING", {"allowed_tools": ["compile_artifacts"]})
    # Should go to fallback but not count as hermes fallback for reasoning
    assert tool == "compile_artifacts"
    # mechanical should not set attempted/fallback
    assert fb.hermes_cli_attempted is False
    assert fb.hermes_fallback_used is False


def test_fallback_does_not_falsely_mark_cli_used():
    cli = CliHermes(command=["hermes"], runner=lambda *a, **kw: _fake_runner_timeout())
    fallback = DeterministicHermes()
    fb = FallbackHermes(cli, fallback)
    fb.propose_tool("INGESTING", {"allowed_tools": ["inspect_evidence"]})
    assert fb.cli_used is False
    # ensure hermes_cli_succeeded still false
    assert fb.hermes_cli_succeeded is False


def test_sequence_failure_marks_fallback():
    cli = CliHermes(command=["hermes"], runner=lambda *a, **kw: _fake_runner_timeout())
    fallback = DeterministicHermes()
    fb = FallbackHermes(cli, fallback)
    res = fb.propose_sequence("INGESTING", {"allowed_tools": ["inspect_evidence"]})
    assert res is None
    assert fb.hermes_cli_attempted is True
    assert fb.hermes_fallback_used is True


def test_deterministic_has_no_hermes_config():
    d = DeterministicHermes()
    assert d.hermes_cli_configured is False
    assert d.hermes_cli_succeeded is False
