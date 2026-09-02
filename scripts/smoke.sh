#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE_URL:-http://127.0.0.1:8000}"
curl -fsS "$BASE/health/live" >/dev/null
curl -fsS "$BASE/health/ready" >/dev/null
echo "SMOKE PASS"
