#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "${APP_DIR}"
export TANGGAP60_SOAK="${TANGGAP60_SOAK:-10}"
if [[ -x .venv/bin/pytest ]]; then
  PYTEST=.venv/bin/pytest
else
  PYTEST=pytest
fi
"${PYTEST}" -q tests/performance/test_hero_budget.py
echo "BENCHMARK_OK soak=${TANGGAP60_SOAK}"
