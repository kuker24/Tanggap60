# Usability Test — Tanggap60 Rescue

Protocol: synthetic fixture (2 transfers + 1 chat), 5 peserta non-developer, tanpa PII.

## Tugas
- T1: Temukan "Lakukan Sekarang"
- T2: Ketahui ada 2 transaksi/unit
- T3: Ketahui unit mana yang belum siap
- T4: Memperbaiki ambiguity (pilih pasangan)
- T5: Mengerti READY bukan laporan diterima
- T6: Mengerti Tanggap60 tidak mengirim laporan

## Metrik Target
- >=4/5 menemukan tindakan pertama tanpa bantuan
- >=4/5 memahami reporting units
- >=4/5 menyelesaikan correction/mapping
- 5/5 memahami NOT_VERIFIED
- 5/5 memahami no auto-submit

## Hasil Status
`USABILITY_STUDY = READY_FOR_HUMAN_TEST` — studi belum dilakukan dengan peserta manusia, template siap, jangan mengarang hasil.

Laporan setelah 5 peserta: catat `docs/usability_results.csv` dengan fields `participant_code,task,success,time_seconds,assistance,comment` tanpa nama/email/NIK.

## Hasil Synthetic (internal)
- Navigasi ke /next-action terlihat sebagai card besar di readiness.
- Unit cards menampilkan Rp, rekening, waktu, status READY/NEEDS_ACTION/BLOCKED.
- Evidence map menampilkan provenance per unit.
