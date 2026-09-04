# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
P1 Korban panik (baru transfer, bukti berantakan), P2 Pengguna ragu (belum rugi, ingin cek URL/rekening), P3 Pendamping (keluarga/teman yang membantu). Semua di Indonesia, mobile-first, butuh langkah jelas tanpa jargon hukum.

## Product Purpose
SatuAman Tanggap60 mengubah bukti berantakan menjadi kasus siap handoff resmi. Intake → ekstraksi → review fakta/konflik → preflight kesiapan per kanal (BANK_PJP, IASC, POLICE) → approval snapshot-bound → artefak ZIP terverifikasi → handoff manual → receipt. Tidak mengirim laporan, tidak menjamin dana kembali, status resmi selalu NOT_VERIFIED.

## Positioning
SatuAman Tanggap60 — AI Rescue Agent / Native Co-pilot: AI pendamping insiden digital yang menemani korban dari bukti berantakan sampai siap handoff ke kanal resmi. Setiap klaim terikat sumber bukti; konflik ditampilkan sebelum laporan; paket Bank/IASC hanya bila channel READY, dengan manifest SHA-256. Bukan chatbot jawaban atau daftar tautan. Native Action (lihat AGENT_ARCHITECTURE.md): bicara atau ketik, AI bertindak di halaman, prefill draf, minta persetujuan, berhenti sebelum kredensial/OTP/submit final.

## Operating Context
VPS 4 vCPU / 4 GB / 20 GB, 1 web + 1 heavy worker, Nginx, SQLite WAL, Hermes Agent CLI sebagai orkestrator tool (allowlist per state). Fallback deterministik jika model tidak tersedia. Klaim durasi demo diukur lokal, bukan jaminan.

## Capabilities and Constraints
Must: case anonim, upload JPG/PNG/PDF maksimal 8 berkas / 25 MB total plus teks/URL dengan kuota yang sama, SHA-256 + provenance, OCR + fact extraction, routing PRE/POST/OUT_OF_SCOPE, conflict detection, fact review, readiness 2026-09-02.mvp2, action plan, artifact ZIP terverifikasi (paket kanal sesuai kesiapan), verification, handoff manual, receipt, purge, events. Constraints: no auto-submit, no raw OCR/PII di trace, approval hash-bound, profile fail-safe, guard RAM 1024 / disk 2048.

## Brand Commitments
Nama SatuAman Tanggap60. Bahasa Indonesia sederhana. Disclaimer tetap: keputusan resmi di lembaga berwenang. Dunia visual: satu dunia terang-tenang (warm paper) dari beranda sampai paket; panel gelap hanya untuk jejak teknis yang collapsed. Alur langkah: Bukti → Periksa → Konfirmasi → Bertindak.

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
