#!/usr/bin/env bash
set -euo pipefail
if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi
chmod 0750 /home/hermes
chmod 0770 /home/hermes/.hermes
setfacl -m u:tanggap60:--x /home/hermes
setfacl -m u:tanggap60:rwx -m m::rwx /home/hermes/.hermes
setfacl -d -m u:tanggap60:rwx -m m::rwx /home/hermes/.hermes

if [[ -d /home/hermes/.hermes ]]; then
  echo "systemd" > /home/hermes/.hermes/.managed
  chown hermes:hermes /home/hermes/.hermes/.managed
  chmod 0644 /home/hermes/.hermes/.managed
fi

# Hermes updates both files and lockfiles while the worker invokes the CLI.
for file in state.db auth.json auth.lock; do
  path="/home/hermes/.hermes/${file}"
  if [[ -f "${path}" ]]; then
    chown hermes:hermes "${path}" || true
    chmod 0660 "${path}" || true
    setfacl -m u:tanggap60:rw- -m m::rw- "${path}" || true
  fi
done
for f in /home/hermes/.hermes/*.lock /home/hermes/.hermes/state.db*; do
  if [[ -e "$f" ]]; then
    chmod 0660 "$f" || true
    setfacl -m u:tanggap60:rw- -m m::rw- "$f" || true
  fi
done
if [[ -d /home/hermes/.local ]]; then
  setfacl -R -m u:tanggap60:r-x -m m::rx /home/hermes/.local
fi
if [[ -d /home/hermes/.hermes/hermes-agent ]]; then
  setfacl -R -m u:tanggap60:r-x -m m::rx /home/hermes/.hermes/hermes-agent
fi
if [[ -f /home/hermes/.hermes/config.yaml ]]; then
  chmod 0640 /home/hermes/.hermes/config.yaml || true
  setfacl -m u:tanggap60:r-- -m m::r-- /home/hermes/.hermes/config.yaml || true
fi
if [[ -f /home/hermes/.hermes/.env ]]; then
  chmod 0640 /home/hermes/.hermes/.env || true
  setfacl -m u:tanggap60:r-- -m m::r-- /home/hermes/.hermes/.env || true
fi
for d in logs sessions tmp cache; do
  install -d -o hermes -g hermes "/home/hermes/.hermes/${d}"
  chmod 0770 "/home/hermes/.hermes/${d}" || true
  chmod -R g+rwX "/home/hermes/.hermes/${d}" || true
  setfacl -R -m u:tanggap60:rwx -m m::rwx "/home/hermes/.hermes/${d}"
  setfacl -d -m u:tanggap60:rwx -m m::rwx "/home/hermes/.hermes/${d}"
done
