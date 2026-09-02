#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE="${NEEDRESTART_MODE:-l}"
export PIP_DISABLE_PIP_VERSION_CHECK=1

REPO_URL="${REPO_URL:-https://github.com/kuker24/Tanggap60.git}"
APP_DIR="${APP_DIR:-/opt/tanggap60/app}"
ENABLE_TUNNEL="${ENABLE_TUNNEL:-0}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

apt_install() {
  apt-get install -y --no-install-recommends "$@"
}

if ! command -v git >/dev/null 2>&1; then
  apt_install git ca-certificates curl
fi

mkdir -p /opt/tanggap60 /var/lib/tanggap60/cases /var/lib/tanggap60/db /var/log/tanggap60 /etc/tanggap60

if [[ ! -f "${APP_DIR}/pyproject.toml" ]]; then
  git clone "${REPO_URL}" "${APP_DIR}"
elif [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" pull --ff-only
fi

id tanggap60 >/dev/null 2>&1 || useradd --system --home /opt/tanggap60 --shell /usr/sbin/nologin tanggap60

if command -v python3.11 >/dev/null 2>&1; then
  PY=python3.11
else
  apt_install python3.11 python3.11-venv python3.11-dev || apt_install python3 python3-venv python3-dev
  if command -v python3.11 >/dev/null 2>&1; then
    PY=python3.11
  else
    PY=python3
  fi
fi

apt_install tesseract-ocr nginx ca-certificates curl

ver="$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
min_ok="$($PY -c 'import sys; print(int(sys.version_info[:2] >= (3, 11)))')"
if [[ "${min_ok}" != "1" ]]; then
  echo "need Python >= 3.11, got ${ver}" >&2
  exit 1
fi

cd "${APP_DIR}"
if [[ ! -x .venv/bin/pip ]]; then
  "${PY}" -m venv .venv
fi
.venv/bin/pip install -U pip
if [[ -f requirements.lock ]]; then
  .venv/bin/pip install -r requirements.lock
fi
.venv/bin/pip install -e ".[dev,ocr]"

if [[ ! -f /etc/tanggap60/tanggap60.env ]]; then
  key="$("${PY}" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  cat >/etc/tanggap60/tanggap60.env <<EOF
APP_ENV=competition
BASE_URL=${BASE_URL}
SECRET_KEY=${key}
DATABASE_URL=sqlite:////var/lib/tanggap60/db/tanggap60.db
CASE_STORAGE_DIR=/var/lib/tanggap60/cases
OFFICIAL_IASC_URL=https://iasc.ojk.go.id/
RESOURCE_GUARD_ENABLED=true
MIN_AVAILABLE_RAM_MB=1024
MIN_FREE_DISK_MB=2048
EOF
fi
chmod 0640 /etc/tanggap60/tanggap60.env
chown root:tanggap60 /etc/tanggap60/tanggap60.env

cp "${APP_DIR}/deploy/tanggap60-web.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/tanggap60-worker.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/tanggap60-purge.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/tanggap60-purge.timer" /etc/systemd/system/
cp "${APP_DIR}/deploy/nginx.conf" /etc/nginx/conf.d/tanggap60.conf
rm -f /etc/nginx/sites-enabled/default

chown -R tanggap60:tanggap60 /opt/tanggap60 /var/lib/tanggap60 /var/log/tanggap60
chmod 0700 /var/lib/tanggap60/cases
chmod +x "${APP_DIR}/scripts/"*.sh

nginx -t
systemctl daemon-reload
systemctl enable tanggap60-web tanggap60-worker tanggap60-purge.timer
systemctl restart tanggap60-web tanggap60-worker
systemctl reload nginx || systemctl restart nginx
systemctl start tanggap60-purge.timer

if [[ "${ENABLE_TUNNEL}" == "1" ]]; then
  if ! command -v cloudflared >/dev/null 2>&1; then
    curl -fsSL -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    dpkg -i /tmp/cloudflared.deb
    rm -f /tmp/cloudflared.deb
  fi
  cp "${APP_DIR}/deploy/tanggap60-tunnel.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable tanggap60-tunnel
  if systemctl is-active --quiet tanggap60-tunnel; then
    echo "TUNNEL_ALREADY_ACTIVE"
  else
    systemctl start tanggap60-tunnel
  fi
fi

echo "BOOTSTRAP_OK"
