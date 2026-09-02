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
id tanggap60 >/dev/null 2>&1 && usermod -aG hermes tanggap60
apt-get install -y --no-install-recommends acl
chmod 0750 /home/hermes
chmod 0750 /home/hermes/.hermes
setfacl -m u:tanggap60:--x /home/hermes
if [[ -d /home/hermes/.local ]]; then
  setfacl -R -m u:tanggap60:r-x -m m::rx /home/hermes/.local
  setfacl -d -m u:tanggap60:r-x -d -m m::rx /home/hermes/.local
fi
setfacl -m m::rx /home/hermes/.hermes
setfacl -m u:tanggap60:r-x /home/hermes/.hermes
setfacl -d -m u:tanggap60:r-x -d -m m::rx /home/hermes/.hermes
if [[ -d /home/hermes/.hermes/hermes-agent ]]; then
  setfacl -R -m u:tanggap60:r-x -m m::rx /home/hermes/.hermes/hermes-agent
  setfacl -R -d -m u:tanggap60:r-x -d -m m::rx /home/hermes/.hermes/hermes-agent
fi
if [[ -f /home/hermes/.hermes/config.yaml ]]; then
  setfacl -m u:tanggap60:r-- /home/hermes/.hermes/config.yaml
fi
if [[ -f /home/hermes/.hermes/.env ]]; then
  setfacl -m u:tanggap60:r-- /home/hermes/.hermes/.env
fi
for d in logs sessions tmp cache; do
  install -d -o hermes -g hermes "/home/hermes/.hermes/${d}"
  setfacl -m u:tanggap60:rwx -m m::rwx "/home/hermes/.hermes/${d}"
  setfacl -d -m u:tanggap60:rwx "/home/hermes/.hermes/${d}"
done
ENV_FILE=/etc/tanggap60/tanggap60.env
if [[ -f "${ENV_FILE}" ]]; then
  grep -q '^HERMES_BIN=' "${ENV_FILE}" || printf '\nHERMES_BIN=/home/hermes/.local/bin/hermes\n' >> "${ENV_FILE}"
  grep -q '^HERMES_HOME=' "${ENV_FILE}" || printf 'HERMES_HOME=/home/hermes/.hermes\n' >> "${ENV_FILE}"
fi
install -d /etc/systemd/system/tanggap60-web.service.d /etc/systemd/system/tanggap60-worker.service.d
printf '[Service]\nSupplementaryGroups=hermes\n' >/etc/systemd/system/tanggap60-web.service.d/hermes.conf
printf '[Service]\nSupplementaryGroups=hermes\n' >/etc/systemd/system/tanggap60-worker.service.d/hermes.conf
cp "${APP_DIR}/deploy/hermes-dashboard.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/hermes-tunnel.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/tanggap60-hermes-acl.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/tanggap60-hermes-acl.timer" /etc/systemd/system/
chmod +x "${APP_DIR}/scripts/fix_hermes_acl.sh"
"${APP_DIR}/scripts/fix_hermes_acl.sh"
systemctl daemon-reload
systemctl enable --now tanggap60-hermes-acl.service
systemctl enable hermes-dashboard hermes-tunnel
systemctl restart hermes-dashboard
sleep 3
if systemctl is-active --quiet hermes-tunnel; then
  echo "HERMES_TUNNEL_ALREADY_ACTIVE"
else
  systemctl start hermes-tunnel || echo "HERMES_TUNNEL_SKIP"
fi
systemctl restart tanggap60-web tanggap60-worker
echo "HERMES_DASHBOARD_OK"
