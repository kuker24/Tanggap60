from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.hermes.adapter import build_hermes  # noqa: E402


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    hermes = build_hermes(settings)
    tool = hermes.propose_tool(
        "INGESTING",
        {"allowed_tools": ["inspect_evidence"], "route": "POST_INCIDENT"},
    )
    mode = getattr(hermes, "last_mode", "unknown")
    print(f"HERMES_SMOKE mode={mode} tool={tool} bin={settings.hermes_bin or '-'}")
    if settings.hermes_bin and mode != "cli":
        raise SystemExit("HERMES_NEEDS_MODEL")
    if tool != "inspect_evidence":
        raise SystemExit(f"unexpected tool {tool}")
    print("HERMES_SMOKE_PASS")


if __name__ == "__main__":
    main()
