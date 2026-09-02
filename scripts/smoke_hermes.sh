#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/tanggap60/app}"
cd "${APP_DIR}"
if [[ -f /etc/tanggap60/tanggap60.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source /etc/tanggap60/tanggap60.env
  set +a
fi
if [[ -x /home/hermes/.local/bin/hermes ]]; then
  sudo -u hermes -H env HOME=/home/hermes HERMES_HOME=/home/hermes/.hermes \
    PATH=/home/hermes/.local/bin:/usr/bin:/bin \
    /home/hermes/.local/bin/hermes --version
fi
.venv/bin/python scripts/smoke_hermes.py
