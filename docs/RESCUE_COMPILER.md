# Tanggap60 Rescue Compiler

## Problem
Transaksi berulang dipasangkan secara naif: amount pertama + waktu pertama + setiap rekening. Untuk kasus Rp2.000.000 → A 09:13 dan Rp750.000 → B 09:47, pasangan harus dibuktikan via provenance, bukan urutan array.

## Reporting Unit Model
`ReportingUnit` = satu pasangan finansial yang dapat dipersiapkan independen untuk handoff.

Fields: `unit_id` (stable `ru_<12hex>` dari hash canonical `evidence_id`+`fact_ids`), `source_account`, `destination_account`, `amount`, `transferred_at`, `fact_ids[]`, `evidence_ids[]`, `mapping_status` (COMPLETE/INCOMPLETE/AMBIGUOUS), `mapping_reason`, `mapping_provenance`.

Stable ID: hash canonical tanpa runtime timestamp/random/row order.

## Mapping Rules
- **Strong mapping**: dest+amount+datetime dari `source_evidence_id` yang sama dan tidak ada ambiguitas internal.
- **Do not guess**: Jika 2 akun, 2 nominal, 2 waktu tanpa bukti pasangan (contoh semua fakta di satu evidence tanpa locator yang membedakan), buat `AMBIGUOUS_MAPPING`, bukan `first->first`.
- **Shared evidence**: chat/iklan/URL adalah `SHARED_EVIDENCE` incident-level, tidak otomatis terikat ke satu unit kecuali provenance/decision.

## Evidence Semantics
Klasifikasi deterministik dari hasil ekstraksi, bukan primarily dari filename (IMG_7821.jpg, Screenshot_*.png harus tetap dikenali).

- **Transaction candidate**: kombinasi AMOUNT + ACCOUNT/PJP + DATETIME.
- **Communication candidate**: CLAIM + CHANNEL + PHONE atau conversation-like.
- Filename hanya secondary hint.

## Human Mapping Resolution
Jika `AMBIGUOUS_MAPPING`, UI menampilkan:

> Kami menemukan dua kemungkinan transaksi, tetapi belum dapat memastikan fakta mana yang berpasangan.

Pengguna memilih pasangan yang benar. Decision disimpan sebagai `UnitMappingDecision` dengan `actor=USER`, audited, snapshot-bound, tidak diizinkan Hermes membuat final mapping saat ambiguous.

## No Global Blocking When Not Necessary
Conflict dibedakan:
- `UNIT_SCOPED` — hanya memblokir unit yang terkait (fact_ids subset unit).
- `INCIDENT_GLOBAL` — memblokir semua unit.

Contoh: Unit A READY, Unit B AMBIGUOUS → Unit A tetap dapat ditindaklanjuti.

## Readiness V2 Per Unit
```
INCIDENT
├─ Unit A → BANK/PJP readiness, IASC readiness
├─ Unit B → BANK/PJP readiness, IASC readiness
└─ Incident → POLICE readiness (incident-level)
```
Status: `READY`, `NEEDS_ACTION`, `BLOCKED`, plus `AMBIGUOUS` sebagai blocker eksplisit. Ditampilkan sebagai `5/5 pemeriksaan internal selesai + 1 diisi di portal resmi`.

## Approval Granularity
- `REPORTING_UNIT_HANDOFF` dengan `target_id = unit_id`, snapshot mengikat `unit identity, reviewed facts, provenance, mapping decisions, relevant conflicts, readiness_profile_version`.
- `INCIDENT_HANDOFF` untuk police/incident pack.
- Perubahan fakta yang relevan → revoke unit tersebut; perubahan unit lain yang tidak terkait → tidak membatalkan approval unit yang masih valid.

## Artifact Contract V2
Schema `2.2` (additive, 2.0/2.1 tetap valid).

`case.json` 2.2 berisi `reporting_units[]`, `next_best_action`, `readiness_units`.

Layout ZIP (contoh):
```
action_plan.pdf  (mencerminkan next_best_action)
evidence_pack.pdf
readiness_report.pdf
police_handoff_pack.pdf
units/ru_x/unit.json
units/ru_x/bank_handoff_pack.pdf
units/ru_x/iasc_handoff_pack.pdf
case.json (2.2)
handoff.md
manifest.sha256
case-pack.zip
```
Draft untuk unit belum READY diberi `BELUM LENGKAP — PERLU TINDAKAN` dan `DRAF PENGGUNA — BUKAN DOKUMEN RESMI`, handoff tetap terkunci.

## Next Best Action Engine
Deterministik, testable, dipanggil Hermes orchestration tetapi bukan opini LLM.

Codes: `RESOLVE_CONFLICT`, `RESOLVE_UNIT_MAPPING`, `CONFIRM_TRANSACTION_*`, `CONTACT_BANK_PJP`, `PREPARE_IASC_UNIT`, `PREPARE_POLICE_INCIDENT`, dll.

Prioritas:
1. Blocking conflict → RESOLVE_CONFLICT
2. Ambiguous → RESOLVE_UNIT_MAPPING
3. Ready financial unit → CONTACT_BANK/PREPARE_IASC (tanpa menunggu unit lain)
4. Missing critical fact → CONFIRM_*
5. Police incident

Hanya satu primary action ditampilkan sebagai **LAKUKAN SEKARANG**.

## Privacy & Safety
- No raw OCR di trace, no PII, no API keys.
- `official_status=NOT_VERIFIED` invariant.
- No auto-submit, no fetch URL, no scraping.

## Limitations
- Tidak menjamin format pasti diterima IASC/Polri (internal preparation check).
- Tidak ada fraud scoring, blacklist, graph DB, blockchain, multi-agent, Redis, vector DB.
