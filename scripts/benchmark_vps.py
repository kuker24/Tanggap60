from __future__ import annotations

import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_hero import run_hero  # noqa: E402


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BASE_URL", "http://127.0.0.1:8000")
    runs = int(os.environ.get("TANGGAP60_SOAK", "3"))
    times: list[float] = []
    modes: list[str] = []
    for index in range(runs):
        result = run_hero(base)
        times.append(float(result["elapsed_s"]))
        modes.append(str(result.get("hermes_mode") or ""))
        print(f"run={index + 1}/{runs} elapsed_s={result['elapsed_s']} hermes={result['hermes_mode']}")
    times.sort()
    p95 = times[max(0, int(round(0.95 * (len(times) - 1))))]
    p50 = statistics.median(times)
    print(f"VPS_BENCHMARK n={runs} p50={p50:.3f} p95={p95:.3f} max={max(times):.3f} hermes={modes}")
    if p50 >= 60 or p95 >= 60 or max(times) >= 60:
        raise SystemExit("VPS_BENCHMARK_FAIL over 60s")
    print("VPS_BENCHMARK_PASS")


if __name__ == "__main__":
    main()
