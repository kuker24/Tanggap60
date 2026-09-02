# Deploy VPS

Jangan `make run` di laptop. Sumber kebenaran: `https://github.com/kuker24/Tanggap60`.

## Layout

- `/opt/tanggap60/app` source + venv
- `/var/lib/tanggap60/cases` mode 0700
- `/var/lib/tanggap60/db`
- `/var/log/tanggap60`
- `/etc/tanggap60/tanggap60.env` mode 0640, group `tanggap60`

Wajib di env: `SECRET_KEY` (>=16), `DATABASE_URL=sqlite:////var/lib/tanggap60/db/tanggap60.db`, `CASE_STORAGE_DIR=/var/lib/tanggap60/cases`, `OFFICIAL_IASC_URL=https://iasc.ojk.go.id/`. `SYNC_JOBS` kosong; worker terpisah.

## VPS lomba (satu kali)

Jangan commit secret. Isi `HERMES_ENDPOINT` hanya setelah panitia kasih.

```bash
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
apt-get install -y --no-install-recommends git ca-certificates
git clone https://github.com/kuker24/Tanggap60.git /opt/tanggap60/app
cd /opt/tanggap60/app
sudo ./scripts/bootstrap_vps.sh
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
```
