#!/usr/bin/env bash
set -euo pipefail
cd /opt/tanggap60/app
.venv/bin/python scripts/purge_expired.py
