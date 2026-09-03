# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
P1 Korban panik (baru transfer, bukti berantakan), P2 Pengguna ragu (belum rugi, ingin cek URL/rekening), P3 Pendamping (keluarga/teman yang membantu). Semua di Indonesia, mobile-first, butuh langkah jelas tanpa jargon hukum.

## Product Purpose
SatuAman Tanggap60 mengubah bukti berantakan menjadi kasus siap handoff resmi. Intake → ekstraksi → review fakta/konflik → preflight kesiapan per kanal (BANK_PJP, IASC, POLICE) → approval snapshot-bound → artefak ZIP terverifikasi → handoff manual → receipt. Tidak mengirim laporan, tidak menjamin dana kembali, status resmi selalu NOT_VERIFIED.

## Positioning
Satu-satunya companion pasca-insiden yang membuktikan setiap klaim dengan sumber bukti, mendeteksi konflik sebelum laporan, dan menghasilkan paket siap-ajar untuk bank/IASC/polisi dengan manifest SHA-256 — bukan chatbot jawaban atau link list.

## Operating Context
VPS 4 vCPU / 4 GB / 20 GB, 1 web + 1 heavy worker, Nginx, SQLite WAL, Hermes Agent CLI sebagai orkestrator tool (allowlist 9 tool). Flow demo 60 detik tanpa waktu manusia. Offline fallback deterministik jika model tidak tersedia.

## Capabilities and Constraints
Must: case anonim, upload JPG/PNG/PDF 8×25MB, SHA-256 + provenance, OCR + fact extraction, routing PRE/POST/OUT_OF_SCOPE, conflict detection, fact review, readiness 2026-09-02.mvp2, action plan, artifact ZIP 9 file, verification, handoff manual, receipt, purge, events. Constraints: no auto-submit, no raw OCR/PII di trace, approval hash-bound, profile fail-safe, guard RAM 1024 / disk 2048.

## Brand Commitments
Nama SatuAman Tanggap60. Bahasa Indonesia sederhana. Disclaimer tetap: keputusan resmi di lembaga berwenang. Dunia visual: darkroom ORYZO — walnut void, cream type, ember hanya aksen pada segel. Bukan kertas bone.

## Evidence on Hand
Fixtures demo_tanggap60, PRD/UX spec, PREFLIGHT.md, DEPLOY.md. Tidak ada testimoni/customer palsu untuk diinventarisir.

## Product Principles
1. Fakta punya sumber, uncertainty ditampilkan.
2. Luas di pintu masuk, sempit dalam di eksekusi.
3. Manusia menyetujui risiko, bukan LLM.
4. Output harus bisa dibuka dan diverifikasi.
5. Gagal aman dan dapat dipulihkan.

## Accessibility & Inclusion
Keyboard usable, fokus terlihat, kontras 4.5:1, target 44px, status via teks+ikon bukan warna saja, bahasa Indonesia sederhana, aria-live untuk perubahan state.
