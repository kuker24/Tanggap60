from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_hero import run_hero  # noqa: E402


def _metric(row: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BASE_URL", "http://127.0.0.1:8000")
    runs = int(os.environ.get("TANGGAP60_SOAK", "10"))
    times: list[float] = []
    modes: list[str] = []
    failed = 0
    peak_rss = 0
    min_ram = 10**9
    min_disk = 10**9
    max_queue = 0
    tool_ms: dict[str, list[int]] = {}
    for index in range(runs):
        if index > 0:
            time.sleep(3.0)
        try:
            result = run_hero(base)
        except SystemExit as exc:
            failed += 1
            print(f"run={index + 1}/{runs} FAIL {exc}")
            continue
        times.append(float(result["elapsed_s"]))
        modes.append("cli" if result.get("hermes_cli_used") else str(result.get("hermes_mode") or ""))
        metrics = result.get("metrics") or {}
        peak_rss = max(peak_rss, _metric(metrics, "process_rss_mb"))
        ram = _metric(metrics, "available_ram_mb", 0)
        if ram:
            min_ram = min(min_ram, ram)
        disk = _metric(metrics, "disk_free_mb", 0)
        if disk:
            min_disk = min(min_disk, disk)
        max_queue = max(max_queue, _metric(metrics, "job_queue_depth"))
        for step in result.get("trace_steps") or []:
            name = str(step.get("tool_name") or "")
            if not name:
                continue
            tool_ms.setdefault(name, []).append(int(step.get("duration_ms") or 0))
        print(
            f"run={index + 1}/{runs} elapsed_s={result['elapsed_s']} "
            f"hermes_cli_used={result['hermes_cli_used']} rss={metrics.get('process_rss_mb')} "
            f"ram={metrics.get('available_ram_mb')} disk={metrics.get('disk_free_mb')}"
        )
    success = runs - failed
    if not times:
        raise SystemExit("VPS_BENCHMARK_FAIL no successful runs")
    times.sort()
    p95 = times[max(0, int(round(0.95 * (len(times) - 1))))]
    p50 = statistics.median(times)
    max_t = max(times)
    if min_ram == 10**9:
        min_ram = 0
    if min_disk == 10**9:
        min_disk = 0
    print()
    print("TANGGAP60 VPS BENCHMARK")
    print(f"runs              {runs}")
    print(f"success           {success}/{runs}")
    print(f"p50               {p50:.2f} s")
    print(f"p95               {p95:.2f} s")
    print(f"max               {max_t:.2f} s")
    print(f"peak process RSS  {peak_rss} MB")
    print(f"min RAM available {min_ram} MB")
    print(f"min disk free     {min_disk} MB")
    print(f"max queue depth   {max_queue}")
    cli_count = sum(1 for m in modes if m == "cli")
    # For benchmark, hermes is best-effort; allow fallback but report
    print(f"Hermes CLI used   {'YES' if cli_count >= 1 and success == runs else 'NO'} ({cli_count}/{runs} cli) - rescue allows fallback for p95")
    for name, samples in sorted(tool_ms.items()):
        print(f"tool {name} p50_ms={int(statistics.median(samples))} max_ms={max(samples)}")
    ok = (
        failed == 0
        and success == runs
        and p95 < 60
        and max_t < 60
        and min_ram >= 1024
        and min_disk >= 2048
    )
    print(f"RESULT            {'PASS' if ok else 'FAIL'}")
    if not ok:
        raise SystemExit("VPS_BENCHMARK_FAIL")
    print("VPS_BENCHMARK_PASS")


if __name__ == "__main__":
    main()
