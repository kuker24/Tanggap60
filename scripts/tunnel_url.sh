#!/usr/bin/env bash
set -euo pipefail
journalctl -u tanggap60-tunnel -n 200 --no-pager \
  | grep -Eo 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' \
  | tail -n 1
