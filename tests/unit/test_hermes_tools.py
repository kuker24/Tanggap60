from __future__ import annotations

from app.hermes.adapter import DeterministicHermes
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
