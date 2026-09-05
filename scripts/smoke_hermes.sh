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
  if [[ "$(id -u)" -eq 0 ]] && id tanggap60 >/dev/null 2>&1; then
    ./scripts/fix_hermes_acl.sh
    sudo -u tanggap60 -H env HOME=/home/hermes HERMES_HOME=/home/hermes/.hermes \
      PATH=/home/hermes/.local/bin:/usr/bin:/bin \
      /home/hermes/.local/bin/hermes --version
  else
    /home/hermes/.local/bin/hermes --version
  fi
fi
if id tanggap60 >/dev/null 2>&1 && [[ -n "${HERMES_BIN:-}" ]]; then
  sudo -u tanggap60 -H env \
    HOME=/home/hermes \
    HERMES_HOME="${HERMES_HOME:-/home/hermes/.hermes}" \
    HERMES_BIN="${HERMES_BIN}" \
    PATH=/home/hermes/.local/bin:/home/hermes/.hermes/bin:/usr/bin:/bin \
    SECRET_KEY="$(printf 'smoke-%032d' 0)" \
    .venv/bin/python scripts/smoke_hermes.py
else
  .venv/bin/python scripts/smoke_hermes.py
fi
