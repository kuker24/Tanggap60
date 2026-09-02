#!/usr/bin/env bash
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi
chmod 0750 /home/hermes /home/hermes/.hermes
setfacl -m u:tanggap60:--x /home/hermes
setfacl -m m::rx -m u:tanggap60:r-x /home/hermes/.hermes
if [[ -d /home/hermes/.local ]]; then
  setfacl -R -m u:tanggap60:r-x -m m::rx /home/hermes/.local
fi
if [[ -d /home/hermes/.hermes/hermes-agent ]]; then
  setfacl -R -m u:tanggap60:r-x -m m::rx /home/hermes/.hermes/hermes-agent
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
done
