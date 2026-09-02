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

Lihat `deploy/` dan `docs/DEPLOY.md`. Path default:

- `/opt/tanggap60/app`
- `/var/lib/tanggap60/cases`
- `/var/lib/tanggap60/db`
- `/etc/tanggap60/tanggap60.env` (mode 0600)

Salin `.env.example` ke env file. Jangan mengisi secret di git.
