# SatuAman Tanggap60

Competition MVP: bukti digital berantakan menjadi kasus siap ditindaklanjuti. Tidak mengirim laporan, tidak menjamin dana kembali, status tiket resmi selalu `NOT_VERIFIED`.

Jalankan di VPS (systemd + Nginx + venv). Jangan andalkan laptop sebagai lingkungan uji.

## Stack

Hermes adapter + FastAPI + Jinja + SQLite WAL + Pillow/Tesseract + ReportLab. Satu web worker, satu heavy worker.

## Perintah

- `make setup`
- `make test`
- `make test-security`
- `make lint`
- `make typecheck`
- `make smoke` (butuh layanan hidup di VPS)
- `make benchmark`
- `make run` / `make worker` (hanya di VPS)

## Deploy VPS

Lihat `docs/DEPLOY.md`. VPS lomba:

```bash
git clone https://github.com/kuker24/Tanggap60.git /opt/tanggap60/app
cd /opt/tanggap60/app
sudo ./scripts/bootstrap_vps.sh
./scripts/verify_vps.sh
```

Tanpa IP publik: `sudo ENABLE_TUNNEL=1 ./scripts/bootstrap_vps.sh`.

