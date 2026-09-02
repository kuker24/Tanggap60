from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_hero import run_hero, run_rescue_hero  # noqa: E402


def _metric(row: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def main() -> None:
    # args: base_url, --scenario legacy|rescue_multi
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    base = args[0] if args else os.environ.get("BASE_URL", "http://127.0.0.1:8000")
    scenario = "rescue_multi"
    # check env or argv for scenario
    if "--scenario" in sys.argv:
        idx = sys.argv.index("--scenario")
        if idx + 1 < len(sys.argv):
            scenario = sys.argv[idx + 1]
    elif os.environ.get("SCENARIO"):
        scenario = os.environ.get("SCENARIO", "rescue_multi")
    else:
        # if first arg was scenario name
        if args and args[0] in ("legacy", "rescue_multi"):
            scenario = args[0]
            base = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
    runs = int(os.environ.get("TANGGAP60_SOAK", "10"))
    times: list[float] = []
    failed = 0
    peak_rss = 0
    min_ram = 10**9
    min_disk = 10**9
    max_queue = 0
    tool_ms: dict[str, list[int]] = {}
    tool_handler_ms: dict[str, list[int]] = {}
    tool_planner_ms: dict[str, list[int]] = {}
    ocr_ms_list: list[int] = []
    hermes_seq_ms_list: list[int] = []
    hermes_attempt_1_list: list[int] = []
    hermes_attempt_2_list: list[int] = []
    local_ms_list: list[int] = []
    hermes_configured_count = 0
    hermes_attempted_count = 0
    hermes_reasoning_success = 0
    reasoning_fallback_count = 0
    fallback_reasons: list[str] = []
    hermes_cli_succeeded_count = 0
    for index in range(runs):
        if index > 0:
            time.sleep(3.0)
        try:
            if scenario == "legacy":
                result = run_hero(base, scenario="legacy")
            else:
                result = run_rescue_hero(base)
        except SystemExit as exc:
            failed += 1
            print(f"run={index + 1}/{runs} FAIL {exc}")
            # try to capture fallback reason from exception message
            fallback_reasons.append(str(exc)[:120])
            continue
        times.append(float(result["elapsed_s"]))
        metrics = result.get("metrics") or {}
        peak_rss = max(peak_rss, _metric(metrics, "process_rss_mb"))
        ram = _metric(metrics, "available_ram_mb", 0)
        if ram:
            min_ram = min(min_ram, ram)
        disk = _metric(metrics, "disk_free_mb", 0)
        if disk:
            min_disk = min(min_disk, disk)
        max_queue = max(max_queue, _metric(metrics, "job_queue_depth"))
        # separated metrics per run
        ocr_sum = 0
        seq_sum = 0
        att1_sum = 0
        att2_sum = 0
        local_sum = 0
        for step in result.get("trace_steps") or []:
            name = str(step.get("tool_name") or "")
            if not name:
                continue
            tool_ms.setdefault(name, []).append(int(step.get("duration_ms") or 0))
            tool_handler_ms.setdefault(name, []).append(int(step.get("handler_ms") or step.get("local_processing_ms") or step.get("duration_ms") or 0))
            tool_planner_ms.setdefault(name, []).append(int(step.get("planner_ms") or 0))
            ocr_sum += int(step.get("ocr_total_ms") or 0)
            seq_sum += int(step.get("hermes_sequence_ms") or 0)
            att1_sum += int(step.get("hermes_attempt_1_ms") or 0)
            att2_sum += int(step.get("hermes_attempt_2_ms") or 0)
            local_sum += int(step.get("handler_ms") or step.get("local_processing_ms") or 0)
        ocr_ms_list.append(ocr_sum)
        hermes_seq_ms_list.append(seq_sum)
        hermes_attempt_1_list.append(att1_sum)
        hermes_attempt_2_list.append(att2_sum)
        local_ms_list.append(local_sum)
        # Hermes telemetry
        configured = bool(result.get("hermes_cli_configured") or result.get("hermes_cli_used"))
        reasoning_fb = int(result.get("hermes_reasoning_fallback", 0))
        if configured:
            hermes_configured_count += 1
        if configured:
            hermes_attempted_count += 1  # each configured run attempts
        if configured and reasoning_fb == 0 and result.get("hermes_cli_used"):
            hermes_reasoning_success += 1
            hermes_cli_succeeded_count += 1
        else:
            if reasoning_fb > 0:
                reasoning_fallback_count += 1
                fallback_reasons.append(f"run{index+1} fallback={reasoning_fb}")
            elif not result.get("hermes_cli_used") and configured:
                reasoning_fallback_count += 1
                fallback_reasons.append(f"run{index+1} no_cli")
        print(
            f"run={index + 1}/{runs} elapsed_s={result['elapsed_s']} "
            f"hermes_cli_used={result.get('hermes_cli_used')} configured={configured} reason_fb={reasoning_fb} "
            f"rss={metrics.get('process_rss_mb')} ram={metrics.get('available_ram_mb')} disk={metrics.get('disk_free_mb')} "
            f"ocr_ms={ocr_sum} seq_ms={seq_sum} att1={att1_sum} att2={att2_sum} local_ms={local_sum}"
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
    print(f"scenario          {scenario}")
    print(f"runs              {runs}")
    print(f"success           {success}/{runs}")
    print(f"Hermes CLI configured {hermes_configured_count}/{runs}")
    print(f"Hermes CLI attempted  {hermes_attempted_count}/{runs}")
    print(f"Hermes reasoning success {hermes_reasoning_success}/{runs}")
    print(f"reasoning fallback count {reasoning_fallback_count}")
    if fallback_reasons:
        print(f"fallback reasons  {'; '.join(fallback_reasons[:5])}")
    else:
        print("fallback reasons  none")
    print(f"p50               {p50:.2f} s")
    print(f"p95               {p95:.2f} s")
    print(f"max               {max_t:.2f} s")
    print(f"peak process RSS  {peak_rss} MB")
    print(f"min RAM available {min_ram} MB")
    print(f"min disk free     {min_disk} MB")
    print(f"max queue depth   {max_queue}")
    cli_count = hermes_cli_succeeded_count
    print(f"Hermes CLI used   {'YES' if cli_count == runs and success == runs else 'NO'} ({cli_count}/{runs} cli)")
    print(f"Hermes reasoning  {hermes_reasoning_success}/{runs}")
    print(f"fallback          {reasoning_fallback_count}")
    # separated aggregates
    if ocr_ms_list:
        print(f"ocr_total_ms      p50={int(statistics.median(ocr_ms_list))} max={max(ocr_ms_list)}")
        print(f"hermes_sequence_ms p50={int(statistics.median(hermes_seq_ms_list))} max={max(hermes_seq_ms_list)}")
        print(f"hermes_attempt_1_ms p50={int(statistics.median(hermes_attempt_1_list))} max={max(hermes_attempt_1_list)}")
        print(f"hermes_attempt_2_ms p50={int(statistics.median(hermes_attempt_2_list))} max={max(hermes_attempt_2_list)}")
        print(f"local_processing_ms p50={int(statistics.median(local_ms_list))} max={max(local_ms_list)}")
    for name, samples in sorted(tool_ms.items()):
        h_samples = tool_handler_ms.get(name, [])
        p_samples = tool_planner_ms.get(name, [])
        print(f"tool {name} p50_total={int(statistics.median(samples))} max_total={max(samples)} p50_handler={int(statistics.median(h_samples)) if h_samples else 0} p50_planner={int(statistics.median(p_samples)) if p_samples else 0}")
    # Strict acceptance when hermes configured
    hermes_strict_required = hermes_configured_count > 0
    if hermes_strict_required:
        ok = (
            failed == 0
            and success == runs
            and hermes_reasoning_success == runs
            and reasoning_fallback_count == 0
            and p95 < 60
            and max_t < 60
            and min_ram >= 1024
            and min_disk >= 2048
        )
    else:
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
