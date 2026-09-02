#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))
from tests.fixture_render import CHAT, TRANSFER, invoice_pdf, png_bytes  # noqa: E402


def main() -> None:
    dest = ROOT / "fixtures" / "demo_tanggap60"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "01_chat.png").write_bytes(png_bytes(CHAT))
    (dest / "02_invoice.pdf").write_bytes(invoice_pdf())
    (dest / "03_transfer.png").write_bytes(png_bytes(TRANSFER))
    expected = {
        "chat_text": CHAT,
        "transfer_text": TRANSFER,
        "amounts": ["Rp2.500.000", "Rp2.750.000"],
        "account": "DEMO-DEST-01",
        "invoice_amount_page": 2,
    }
    (dest / "expected_facts.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    (dest / "README.md").write_text(
        "# Demo Tanggap60\n\nPrepared dummy fixture for the 60-second hero path. No personal data.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
