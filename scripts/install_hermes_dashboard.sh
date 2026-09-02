#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE="${NEEDRESTART_MODE:-l}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

apt-get install -y --no-install-recommends git curl ca-certificates xz-utils build-essential libatomic1

id hermes >/dev/null 2>&1 || useradd --create-home --shell /bin/bash --home-dir /home/hermes hermes
install -d -o hermes -g hermes /home/hermes

as_hermes() {
  sudo -u hermes -H env -i \
    HOME=/home/hermes USER=hermes LOGNAME=hermes SHELL=/bin/bash \
    PATH=/home/hermes/.local/bin:/home/hermes/.hermes/bin:/usr/local/bin:/usr/bin:/bin \
    HERMES_HOME=/home/hermes/.hermes \
    UV_NO_CONFIG=1 \
    bash -lc "cd /home/hermes && $*"
}

if [[ ! -x /home/hermes/.local/bin/hermes ]]; then
  as_hermes 'curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup --skip-browser --skip-computer-use --non-interactive --no-skills'
fi

as_hermes 'cd "$HOME/.hermes/hermes-agent" && "$HOME/.hermes/bin/uv" pip install --python "$HOME/.hermes/hermes-agent/venv/bin/python" -e ".[web,pty]"'

APP_DIR="${APP_DIR:-/opt/tanggap60/app}"
cp "${APP_DIR}/deploy/hermes-dashboard.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/hermes-tunnel.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable hermes-dashboard hermes-tunnel
systemctl restart hermes-dashboard
sleep 3
systemctl restart hermes-tunnel
echo "HERMES_DASHBOARD_OK"
