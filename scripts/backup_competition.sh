#!/usr/bin/env bash
set -euo pipefail
DEST="${1:-/tmp/tanggap60-backup}"
mkdir -p "$DEST"
tar -C /opt/tanggap60 -czf "$DEST/app.tgz" app
cp /etc/tanggap60/tanggap60.env.example "$DEST/" 2>/dev/null || true
sha256sum "$DEST"/* > "$DEST/manifest.sha256"
echo "backup at $DEST"
