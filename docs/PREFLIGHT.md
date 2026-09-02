# Tanggap60 Preflight

Tanggap60 bukan penentu siapa penipu dan bukan portal pelaporan. Preflight menguji apakah kasus sudah konsisten dan cukup lengkap untuk dibawa ke kanal yang dipilih, lalu menghasilkan draf paket handoff setelah persetujuan manusia.

## Model

Hasil kesiapan dihitung ulang secara deterministik dari fakta, bukti, konflik, dan transaksi yang sudah ada. Hermes boleh memilih tool `assess_handoff_readiness`; angka dan status tidak berasal dari opini LLM.

Status kanal: `READY`, `NEEDS_ACTION`, `BLOCKED`. Bukan skor peluang laporan diterima.

Label awam:

- Siap dibuatkan draf
- Masih perlu diperbaiki
- Terblokir oleh konflik bukti
- Siapkan langsung di kanal resmi (`PREPARE_EXTERNALLY`)

## Profile

File: `app/data/readiness_profiles.json`

- `profile_version` (saat ini `2026-09-02.mvp2`)
- `last_reviewed_at`
- `source_urls` publik
- `disclaimer`
- checks per kanal: `REQUIRED`, `RECOMMENDED`, `PREPARE_EXTERNALLY`

Profile adalah rule internal Tanggap60. Bukan reproduksi resmi requirement Bank, IASC, atau Kepolisian. Profile invalid membuat penilaian gagal terkontrol.

## Kanal MVP

- `BANK_PJP`
- `IASC`
- `POLICE`

CekDulu (sebelum rugi) tidak dipaksa menghasilkan paket pascainsiden.

## API

`GET /api/v1/cases/{case_id}/readiness` — owner session saja. Respons memuat `checks_met`/`checks_total`, `official_status=NOT_VERIFIED`, dan disclaimer. Tidak ada raw OCR.

## Approval

Snapshot hash mencakup `readiness_profile_version` dan status check canonical tanpa timestamp. Perubahan fakta, konflik, bukti, atau hasil check membatalkan approval lama.

## Artefak

Untuk pascainsiden, `case-pack.zip` berisi sembilan berkas: action plan, evidence pack, readiness report, tiga paket kanal, `case.json` (`schema_version` 2.1), `handoff.md`, `manifest.sha256`.

Setiap PDF kanal berlabel `DRAF PENGGUNA — BUKAN DOKUMEN RESMI` dan `STATUS RESMI: NOT_VERIFIED`. Kanal yang belum READY diberi `BELUM LENGKAP — PERLU TINDAKAN`.

Tidak ada submit otomatis.
