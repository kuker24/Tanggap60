# Deploy VPS

Jangan `make run` di laptop. Sumber kebenaran: `https://github.com/kuker24/Tanggap60`.

## Layout

- `/opt/tanggap60/app` source + venv
- `/var/lib/tanggap60/cases` mode 0700
- `/var/lib/tanggap60/db`
- `/var/log/tanggap60`
- `/etc/tanggap60/tanggap60.env` mode 0640, group `tanggap60`

Wajib di env: `SECRET_KEY` (>=16), `DATABASE_URL=sqlite:////var/lib/tanggap60/db/tanggap60.db`, `CASE_STORAGE_DIR=/var/lib/tanggap60/cases`, `OFFICIAL_IASC_URL=https://iasc.ojk.go.id/`. `SYNC_JOBS` kosong; worker terpisah. Guard: `MIN_AVAILABLE_RAM_MB=1024`, `MIN_FREE_DISK_MB=2048`. `HERMES_ENDPOINT` opsional (HTTP panitia). VPS uji memakai `HERMES_BIN=/home/hermes/.local/bin/hermes` — Hermes Agent CLI memilih tool; gagal = deterministic fallback. `MODEL_API_KEY` opsional untuk ekstraksi narasi.

## VPS lomba (satu kali)

Jangan commit secret. Isi `HERMES_ENDPOINT` hanya setelah panitia kasih.

```bash
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
apt-get install -y --no-install-recommends git ca-certificates
git clone https://github.com/kuker24/Tanggap60.git /opt/tanggap60/app
cd /opt/tanggap60/app
sudo RELEASE_SHA=<40-hex-commit> ./scripts/bootstrap_vps.sh
./scripts/verify_vps.sh
```

Kalau tidak ada IP publik: `sudo ENABLE_TUNNEL=1 ./scripts/bootstrap_vps.sh` lalu `./scripts/tunnel_url.sh`.

TLS/certbot hanya di VPS lomba jika panitia/IP publik mengizinkan. Jangan ubah `sshd_config`, password, atau `ufw enable`.

## VPS uji

- Jangan ubah password SSH / root
- Jangan pasang certbot
- Jangan ubah `sshd_config`, jangan `ufw enable`, jangan ganti IP/network
- Nginx HTTP :80 → `127.0.0.1:8000`
- Jangan reboot kecuali diminta
- Tunnel: `ENABLE_TUNNEL=1`

```bash
cd /opt/tanggap60/app
sudo ENABLE_TUNNEL=1 ./scripts/bootstrap_vps.sh
./scripts/verify_vps.sh
sudo ./scripts/install_hermes_dashboard.sh
```

Hermes GUI dari laptop (tanpa IP publik):

```bash
ssh -L 9119:127.0.0.1:9119 tanggap60-uji
```

Lalu `http://127.0.0.1:9119` — isi API key / model di tab API Keys + Models. Tanpa itu `smoke_hermes.sh` keluar `HERMES_NEEDS_MODEL` dan Tanggap60 fallback deterministic. Origin dashboard `127.0.0.1:9119` saja. `hermes-tunnel` harus tetap masked. Jangan reboot VPS uji.

Setelah web hidup: `./scripts/smoke_hermes.sh`, `./scripts/smoke_hero.sh`, `./scripts/benchmark.sh`. Fixture demo: `python scripts/make_demo_fixtures.py`.

## Upgrade Preflight (tanpa tabel baru)

Preflight tidak menambah database, Redis, atau unit systemd. Setelah tes lokal lulus:

1. Catat SHA commit aktif di VPS: `git -C /opt/tanggap60/app rev-parse HEAD`
2. `sudo ./scripts/backup_competition.sh /tmp/tanggap60-backup-$(date -u +%Y%m%d)`
3. Pastikan worktree VPS bersih. Jangan `git reset --hard`. Jangan menimpa `/etc/tanggap60/tanggap60.env`.
4. Deploy source yang sudah lulus CI (fast-forward atau rsync tanpa `.venv`).
5. Restart hanya `tanggap60-web` dan `tanggap60-worker`. Jangan restart tunnel.
6. `./scripts/verify_vps.sh && ./scripts/smoke_hermes.sh && ./scripts/smoke_hero.sh`
7. Cek halaman `/cases/{id}/readiness`, ZIP berisi sembilan berkas pascainsiden, `official_status=NOT_VERIFIED`.

Rollback: kembalikan source ke SHA cadangan, restart web/worker, ulangi health check. Jangan restore DB kecuali ada migrasi schema (Preflight tidak memerlukannya).
