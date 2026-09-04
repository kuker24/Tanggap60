# AGENT_ARCHITECTURE — Tanggap60 AI Rescue Agent

> Tanggap60 adalah AI Rescue Agent yang mendampingi korban insiden digital
> dari bukti berantakan sampai siap bertindak dan handoff ke kanal resmi.

Prinsip: **AI mengurangi kekacauan sampai korban mampu bertindak benar.
AI tidak mengambil keputusan hukum, tidak menyentuh kredensial,
tidak mengirim laporan.**

## Bukan chatbot prompt-panjang

Bukti agentic bersifat mekanis, bukan klaim:

1. `POST .../agent/messages` → `ConversationService.handle_message`.
2. Klasifikasi intent **deterministik** (`app/agent/intents.py`, pola kecil
   bahasa Indonesia yang bisa diaudit — tanpa system prompt panjang).
3. Intent → kandidat read tool Tanggap60 → dijalankan lewat Hermes
   `execute_tool()` (allowlist per state) atau fungsi service yang SAMA
   bila state tidak mengizinkan tool itu (dicatat jujur di `tools_used`).
4. Kalimat jawaban berasal dari **template tetap** + hasil tool.
   Hermes/LLM hanya memilih tool — tidak pernah menulis kalimat pengguna.
5. Setiap langkah dicatat sebagai audit event `AGENT_*` (baca
   `GET .../agent/trail`), tampil di panel chat "Detail teknis".

Jika `hermes_bin`/`hermes_endpoint` terkonfigurasi, Hermes CLI/HTTP boleh
memilih tool kandidat (`_Runner.hermes_preference`, hasil divalidasi
allowlist). Tanpa itu, router deterministik + tool yang sama dipakai.

## Alur

```
USER
 ↓  chat (teks / suara push-to-talk → teks, API sama)
ConversationService (app/agent/service.py)
 ↓  AgentContextBuilder (app/agent/context.py) — terstruktur, tanpa PII mentah
Intent router (deterministik) → Hermes execute_tool / fungsi service sama
 ↓
ActionBroker (app/agent/broker.py): GREEN / YELLOW / RED
 ↓
respons terstruktur {message, quick_actions, guidance, proposed_action,
                    tools_used, agent_response_ms, technical}
 ↓
frontend: panel chat + pointer (data-guide-id) + modal Simpan/Batal
 ↓
HUMAN APPROVAL → eksekusi reuse endpoint existing (mapping/approval)
 ↓
Safe Workspace → panduan handoff manual ke portal resmi (allowlist)
```

## Action Broker

- **GREEN** (otomatis): baca state/readiness/konflik/tindakan; scroll,
  highlight, pindah halaman internal; pratinjau workspace; tool read-only.
- **YELLOW** (butuh konfirmasi eksplisit): `SET_DRAFT` / `SET_UNIT_MAPPING`,
  `OPEN_OFFICIAL`. Proposal = `action_id` HMAC-SHA256
  `HMAC(secret, case|type|payload|version)` 32 hex — tanpa secret
  server menolak. Stateless, anti-tamper (diubah sedikit → 400),
  idempoten, tolak basi (versi berubah → 409). Prefill DOM ≠ commit
  server sampai pengguna/voice `iya`.
- **RED** (selalu tolak + jelaskan): password/PIN/login, OTP, CAPTCHA,
  transaksi bank, auto-submit, vonis hukum, scrape situs lain, bypass,
  unggah KTP otomatis. Event `SENSITIVE_STOP`.

## Registry (allowlist, bukan output model)

- `GUIDE_TARGETS`: target `data-guide-id` statis + pola dinamis
  `transaction-<ru_12hex>[-amount|-destination|-datetime]` yang
  divalidasi terhadap unit nyata di konteks. Elemen boleh membawa
  `data-guide-alt` sebagai jangkar cadangan; JS mundur ke target dasar
  bila target field tidak ada di halaman. Selector arbitrer ditolak.
- `WORKSPACE_FIELDS`: field yang boleh diisi. Korban/identitas tidak
  pernah diisi AI. Yang belum diketahui = "Belum tersedia", bukan tebakan.
- URL eksternal: hanya `HANDOFF_ALLOWLIST`.

## Safe Workspace

Halaman `/cases/<id>/workspace`, stateless (derivasi dari fakta
CONFIRMED/CORRECTED + unit COMPLETE/INCOMPLETE). Label eksplisit
"SIMULASI PERSIAPAN FORM — BUKAN PORTAL RESMI". Action log visual
"Yang Tanggap60 siapkan", checklist, CTA portal resmi. Tanpa auto-submit — pengiriman tetap manual.
Bukan isolated computer / sandbox browser — ini adalah formulir persiapan terpandu
di mana nomor rekening tujuan ditampilkan lengkap khusus di sesi browser korban
agar siap dicopy ke formulir resmi, sementara konteks Agent, audit log,
dan telemetry ke model tetap tersamar (masked).

## Batas yang dijaga

- Koreksi mapping hanya diproposal bila **tepat 1 amount × 1 rekening**
  (rekening tunggal atau hint eksplisit pengguna); konflik BLOCKING
  menyentuh unit → arahkan selesaikan konflik dulu ( intuition = UI web:
  pairing disembunyikan saat blocking).
- Chat/voice = assistive layer. Core flow jalan tanpa chat
  (progressive enhancement; fallback "lanjutkan manual").
- Riwayat chat di `sessionStorage` per kasus (TTL 60 mnt, maks 30).
  Server hanya menyimpan audit event (ringkasan tersanitasi, tanpa teks
  mentah/rahasia) — selaras TTL kasus.
- Pesan dirender via `textContent` (XSS-safe). Mikrofon hanya setelah
  tap eksplisit; TTS default mati.

## Voice (bonus, cuttable)

`agent.js`: push-to-talk Web Speech API → transkrip final auto-send ke
`POST .../agent/messages` (dijaga `inFlight`). TTS `speechSynthesis`
default mati, nyala hanya dari toggle. NativeActionBus mengeksekusi
langkah GREEN di DOM; YELLOW prefill + badge; RED tidak dijalankan.
Tanpa dependensi server/API berbayar. Chat teks tetap jalan jika
SpeechRecognition tidak ada.

## Endpoint

- `POST /api/v1/cases/{id}/agent/messages` `{text, ui_state}`
- `POST /api/v1/cases/{id}/agent/actions/{aid}/approve` `{action_type, payload, expected_version}`
- `POST .../deny` `{action_type}`
- `GET .../agent/context`, `GET .../agent/trail`, `GET .../workspace`

## Tes

`tests/unit/test_agent_policy.py` (parser, intent, registry, allowlist,
action_id) dan `tests/integration/test_agent_conversation.py` (17 E2E:
paham state + tool use, pointer, koreksi→proposal→approve→idempoten,
stale-409, tamper-400, OTP/auto-submit/URL-asing ditolak, XSS, workspace
hanya fakta confirmed, isolasi sesi, UI inti tanpa chat). QA visual:
`/tmp/opencode/qa_agent.py` (chat, ring pointer, kartu proposal,
workspace; desk 1440 + HP 390, cek overflow).
