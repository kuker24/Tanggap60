# Product

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
P1 Korban panik (baru transfer, bukti berantakan), P2 Pengguna ragu (belum rugi, ingin cek URL/rekening), P3 Pendamping (keluarga/teman yang membantu). Semua di Indonesia, mobile-first, butuh langkah jelas tanpa jargon hukum.

## Product Purpose
SatuAman Tanggap60 adalah AI Golden Window Rescue Engine yang mengubah bukti berantakan menjadi satu tindakan paling bernilai dan kasus siap handoff resmi. Intake → ekstraksi → review fakta/konflik → prioritas containment → stress-test kesiapan per kanal (BANK_PJP, IASC, POLICE) → approval snapshot-bound → artefak ZIP terverifikasi → handoff manual → receipt. Tidak mengirim laporan, tidak memblokir akun, tidak menjamin dana kembali, status resmi selalu NOT_VERIFIED.

## Positioning
SatuAman Tanggap60 — AI Golden Window Rescue Engine / Native Co-pilot: AI pendamping insiden digital yang membantu korban bertindak benar ketika waktu, bukti, dan kondisi mental sedang melawan mereka. Fitur "Belum Yakin, Tetap Amankan" memberi containment tanpa memaksa vonis; Golden Window menghitung ulang prioritas dari fakta yang ditinjau; stress-test paket memperlihatkan pertanyaan yang mungkin muncul saat intake tanpa mengatasnamakan keputusan lembaga. Setiap klaim terikat sumber bukti; konflik ditampilkan sebelum laporan; paket Bank/IASC hanya bila channel READY, dengan manifest SHA-256. Native Action berhenti sebelum kredensial/OTP/submit final.

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
