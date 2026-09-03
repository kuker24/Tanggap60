# Design — SatuAman Tanggap60

<!-- impeccable:design-schema 1 -->

## World
**ORYZO darkroom.** Paket bukti sebagai objek museum di kamar gelap: walnut void, huruf cream, satu segel 60. Bukan jurnal kertas, bukan tombol oranye SaaS.

## Palette
- Canvas Walnut #100904, Bark #382416, Cork #40372e, Cream #ffedd7
- Ember #dc5000 hanya pada rim segel dan aksen PDF, bukan CTA
- Drift #cbbba6 muted. OK #b7d3a8, danger #e07050. Tanpa #fff/#000 murni.

## Type
- Satu keluarga: Archivo 400/500. Label, nav, H1, tombol: 500 uppercase.
- Lede dan nilai fakta: 400 mixed-case. Body 16px, H1 32px/.9, sentuh 44px.
- Tanpa mono di layar korban.

## Materials
- Tanpa bayangan. Kedalaman dari walnut → bark.
- Kartu 12px, CTA isi 36px pill, ghost 22.5px. Input garis bawah saja.
- Divider putus 1px cork. Header sticky walnut. Footer legal uppercase 10px.
- Museum hidup: grain film, objek mengambang, cincin wait bernapas. Bukan video. `prefers-reduced-motion` mematikan gerak.

## Layout
- Full-bleed walnut, isi max 40rem. Satu aksi primer per layar.
- Home: H1 + dua slab (bark vs ghost). Alur: Bukti → Periksa → Tinjau → Paket.

## Voice
- Indonesia sederhana. Nilai, nama file, langkah manusia.
- CTA menamai aksi. CSS mengubah label menjadi uppercase.

## Surface — Operate
Visitor finishes a task. Technical proof lives in the ZIP, not the UI.
