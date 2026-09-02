from __future__ import annotations

REASONING_TOOLS = frozenset(
    {
        "inspect_evidence",
        "extract_candidate_facts",
        "validate_case_facts",
        "build_preincident_brief",
        "build_postincident_plan",
        "assess_handoff_readiness",
    }
)
MECHANICAL_TOOLS = frozenset(
    {
        "compile_artifacts",
        "verify_artifacts",
        "prepare_official_handoff",
        "compile_reporting_units",
        "recommend_next_action",
    }
)
USER_TOOLS = frozenset({"record_handoff_receipt", "purge_case"})


def planner_for(tool: str, source_mode: str) -> str:
    if tool in USER_TOOLS:
        return "USER"
    if tool in MECHANICAL_TOOLS:
        return "DETERMINISTIC_SAFE"
    if source_mode == "cli":
        return "HERMES_CLI"
    if source_mode == "http":
        return "HERMES_HTTP"
    return "DETERMINISTIC_SAFE"


def execution_for(tool: str) -> str:
    if tool in USER_TOOLS:
        return "USER+LOCAL_TOOL"
    return "LOCAL_TOOL"


def mode_from_hermes(hermes: object) -> str:
    return str(getattr(hermes, "last_planner_mode", getattr(hermes, "last_mode", "deterministic")))
