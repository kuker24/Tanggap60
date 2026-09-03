# Tanggap60 — AI Digital Incident Rescue

Mengubah bukti digital berantakan menjadi **unit kasus yang dapat ditelusuri**, tindakan berikutnya, dan paket handoff terverifikasi.

> Tanggap60 bukan scam detector. Ia membantu korban mengubah satu insiden digital menjadi unit kasus yang dapat ditelusuri, menunjukkan apa yang masih salah, lalu menentukan tindakan paling berguna berikutnya sebelum korban berpindah ke kanal resmi.

Benang merah: `ONE INCIDENT → EVIDENCE → VERIFIED FACTS → REPORTING UNITS → GAPS → NEXT BEST ACTION → UNIT READINESS → HUMAN APPROVAL → VERIFIED HANDOFF PACK`.

**Rescue Agent (chat + pointer + ruang persiapan):** pendamping state-aware — membaca kondisi kasus, menjalankan tool Tanggap60 via Hermes, menyorot UI yang perlu diklik, meminta persetujuan untuk perubahan, menyiapkan workspace simulasi, berhenti sebelum OTP/kredensial/submit final. Lihat `AGENT_ARCHITECTURE.md`.

Jangan andalkan laptop sebagai lingkungan uji. Jalankan di VPS (systemd + Nginx + venv).

## Stack

Hermes adapter + FastAPI + Jinja + SQLite WAL + Pillow/Tesseract + ReportLab. Satu web worker, satu heavy worker.

Alur pascainsiden: unggah bukti → ekstraksi → tinjau fakta/konflik → preflight kanal → approval → paket terverifikasi → handoff manual → receipt. Artefak ZIP: Action Plan, Evidence Pack, readiness report, tiga paket kanal, `case.json`, checklist, manifest SHA-256. Lihat `docs/PREFLIGHT.md`.

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

