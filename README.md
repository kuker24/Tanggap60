# Tanggap60 — AI Digital Incident Rescue

Mengubah bukti digital berantakan menjadi **unit kasus yang dapat ditelusuri**, tindakan berikutnya, dan paket handoff terverifikasi.

> Tanggap60 bukan scam detector. Ia membantu korban mengubah satu insiden digital menjadi unit kasus yang dapat ditelusuri, menunjukkan apa yang masih salah, lalu menentukan tindakan paling berguna berikutnya sebelum korban berpindah ke kanal resmi.

Benang merah: `ONE INCIDENT → EVIDENCE → VERIFIED FACTS → REPORTING UNITS → GAPS → NEXT BEST ACTION → UNIT READINESS → HUMAN APPROVAL → VERIFIED HANDOFF PACK`.

**Native Co-pilot:** suara/teks → aksi native di halaman (buka transaksi, prefill draf, minta persetujuan). GREEN dieksekusi, YELLOW prefill, RED ditolak. TTS default mati. Lihat `AGENT_ARCHITECTURE.md`.

Jangan andalkan laptop sebagai lingkungan uji. Jalankan di VPS (systemd + Nginx + venv).

## Stack

Hermes adapter + FastAPI + Jinja + SQLite WAL + Pillow/Tesseract + ReportLab. Satu web worker, satu heavy worker.

Alur pascainsiden: unggah bukti → ekstraksi → tinjau fakta/konflik → preflight kanal → approval → paket terverifikasi → handoff manual → receipt. Artefak ZIP terverifikasi (paket kanal Bank/IASC hanya jika channel READY). Lihat `docs/PREFLIGHT.md`.

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
sudo RELEASE_SHA=<40-hex-commit> ./scripts/bootstrap_vps.sh
./scripts/verify_vps.sh
```

Jangan clone tanpa pin SHA. Default `main` hanya valid setelah tag `competition-final`.

Tanpa IP publik: `sudo ENABLE_TUNNEL=1 ./scripts/bootstrap_vps.sh`.

