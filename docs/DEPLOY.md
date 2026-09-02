# Deploy VPS

Uji coba hanya di VPS. Jangan `make run` di laptop.

## Rules uji (wajib)

- Jangan ubah password SSH / root
- Jangan pasang TLS, certbot, atau enkripsi tambahan
- Jangan ubah `sshd_config`, jangan `ufw enable`, jangan ganti IP/network
- Nginx HTTP port 80 saja (proxy ke `127.0.0.1:8000`)
- VPS sekali pakai: kehilangan akses tidak bisa dipulihkan

Koneksi lokal (bukan git): `ssh tanggap60-uji`. Password hanya di `VPSujicoba.txt`.

## Layout

- `/opt/tanggap60/app` source + venv
- `/var/lib/tanggap60/cases` mode 0700
- `/var/lib/tanggap60/db`
- `/var/log/tanggap60`
- `/etc/tanggap60/tanggap60.env` mode 0600

## Langkah

1. Salin repo ke `/opt/tanggap60/app`
2. `python3 -m venv .venv && .venv/bin/pip install -e ".[dev,ocr]"` (Python >= 3.11)
3. Pasang tesseract jika belum: `apt install tesseract-ocr` (jangan dist-upgrade)
4. Isi `/etc/tanggap60/tanggap60.env` dari `.env.example` (tanpa commit nilai)
5. `SYNC_JOBS=` kosong; worker terpisah
6. Salin unit systemd di `deploy/`
7. Nginx: `deploy/nginx.conf` — listen 80, tanpa SSL
8. `systemctl enable --now tanggap60-web tanggap60-worker tanggap60-purge.timer`
9. `make smoke` dari VPS (`http://127.0.0.1:8000`)
10. `make test && make test-security`
11. Tanpa IP publik: `systemctl enable --now tanggap60-tunnel` (Cloudflare quick tunnel ke `127.0.0.1:80`). URL: `scripts/tunnel_url.sh`. SSH/password/NAT tidak diubah. Origin tetap HTTP lokal.

Wajib di env: `SECRET_KEY` (>=16), `DATABASE_URL=sqlite:////var/lib/tanggap60/db/tanggap60.db`, `CASE_STORAGE_DIR=/var/lib/tanggap60/cases`, `OFFICIAL_IASC_URL=https://iasc.ojk.go.id/`
