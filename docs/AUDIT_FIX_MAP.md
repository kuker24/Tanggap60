# Pemetaan temuan audit → perbaikan

Target: `kuker24/Tanggap60` branch `fix/audit-integrity-and-ux` (bukan Tanggap60Q / SHA `611fc73`).

| ID | Status | Bukti |
|---|---|---|
| F01 500 + copy expiry | fixed | `error.html` untuk unhandled 500; FORBIDDEN ≠ “data demo dihapus”; `test_foreign_case_html_does_not_claim_deleted` |
| F02 BEFORE+nominal → POST | fixed | `route_from_condition` tetap PRE + `ask_loss_question` |
| F03 demo datetime / amount 100x | fixed | `normalize_amount`/`normalize_datetime`; `test_audit_parsers` |
| F04 dedup UI | fixed | review tanpa skip type+nilai; semua kandidat tampil |
| F05 Lembar Bank+IASC statis | fixed | `tx.docs` dari readiness per kanal |
| F06 download tanpa approval aktif | fixed | `download_artifact` cek snapshot; revoke |
| F07 fallback 2.1 | fixed | unit path dipakai bila ada unit; `_case_json` gagal tertutup |
| F08 OCR cache | fixed | max 32 + TTL 600s; `clear_cache` inspect, DELETE, dan `_wipe` |
| URL port crash | fixed | `analyze_url` ValueError |
| Telepon = rekening | fixed | PHONE tidak jadi ACCOUNT |
| ZIP tanpa bukti | fixed | `bukti/` + manifest |
| GET mutasi job | fixed | readiness/result GET hanya render |
| Job recover unbounded | fixed | lease 600s + attempt 3; `test_recover_stale_fails_after_attempt_budget` |
| PDF campuran | fixed | OCR halaman tanpa teks; `test_mixed_pdf_ocrs_scan_page` |
| Overlay AI | fixed | `GUIDANCE_OFF`, AbortController; Bantu saya di header |
| Landing/intake/CTA | fixed | Tanggap60, 4 langkah, tab default chat untuk BEFORE_LOSS |
| Workspace Tanggal: 23 | fixed | `format_when` tanpa split spasi |
| Manual OCR nol | fixed | `add_manual_fact` USER_ENTERED |
| Lihat bukti / Ubah | fixed | preview + Ubah setelah konfirmasi |
| Upload decode dulu | fixed | `read_upload_limited`; dimensi sebelum `verify` |
| Receipt koreksi | fixed | `replace=true` + try/catch fetch |
| Poll macet | fixed | batas 180s → muat ulang / isi manual |
| Bantuan IASC awal | fixed | intake, processing, review, readiness |

**NOT_RUN (bukan kode):** uji browser 360/zoom/200% interaktif, benchmark VPS 4 GB, uji 5 orang.
**Tidak production-ready** sampai tiga item di atas dijalankan.
