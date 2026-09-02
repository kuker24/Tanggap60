#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
from tests.fixture_render import CHAT, TRANSFER, invoice_pdf, png_bytes  # noqa: E402


TRANSFER_A = "Transfer Berhasil Rp2.000.000 Ke: DEMO-DEST-A 23 September 2026 09:13 WIB Dari: DEMO-VICTIM-MASKED"
TRANSFER_B = "Transfer Berhasil Rp750.000 Ke: DEMO-DEST-B"
TRANSFER_B_COMPLETE = "Transfer Berhasil Rp750.000 Ke: DEMO-DEST-B 23 September 2026 09:47 WIB Dari: DEMO-VICTIM-MASKED"
AMBIGUOUS_TEXT = "Transfer Berhasil Rp2.000.000 Ke: DEMO-DEST-A 23 September 2026 09:13 WIB Transfer Berhasil Rp750.000 Ke: DEMO-DEST-B 23 September 2026 09:47 WIB"


def main() -> None:
    dest = ROOT / "fixtures" / "demo_tanggap60"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "01_chat.png").write_bytes(png_bytes(CHAT))
    (dest / "02_invoice.pdf").write_bytes(invoice_pdf())
    (dest / "03_transfer.png").write_bytes(png_bytes(TRANSFER))
    # Rescue compiler multi-unit fixtures
    (dest / "04_transfer_a.png").write_bytes(png_bytes(TRANSFER_A))
    (dest / "05_transfer_b.png").write_bytes(png_bytes(TRANSFER_B))
    (dest / "06_transfer_b_complete.png").write_bytes(png_bytes(TRANSFER_B_COMPLETE))
    (dest / "07_ambiguous.png").write_bytes(png_bytes(AMBIGUOUS_TEXT))
    expected = {
        "chat_text": CHAT,
        "transfer_text": TRANSFER,
        "amounts": ["Rp2.500.000", "Rp2.750.000"],
        "account": "DEMO-DEST-01",
        "invoice_amount_page": 2,
        "transfer_a": TRANSFER_A,
        "transfer_b": TRANSFER_B,
        "transfer_b_complete": TRANSFER_B_COMPLETE,
        "ambiguous": AMBIGUOUS_TEXT,
        "rescue_units": 2,
    }
    (dest / "expected_facts.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
    (dest / "README.md").write_text(
        "# Demo Tanggap60\n\nPrepared dummy fixture for the 60-second hero path. No personal data.\n\nRescue compiler fixtures: 04_transfer_a (2M), 05_transfer_b (750k missing time), 07_ambiguous (2 dests/2 amounts same evidence).\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
