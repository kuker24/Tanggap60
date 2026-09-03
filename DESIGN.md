# Design — SatuAman Tanggap60

<!-- impeccable:design-schema 1 -->

## World
**Superwhisper aurora.** Midnight glass, gradien hitam→navy→ungu→lavender. Bukan walnut ORYZO, bukan kertas bone, bukan UI crypto.

## Palette
- Canvas `#000000`, surface `#0f0f10` / `#1c1d1f` / `#001b33`
- Teks `#ffffff`, muted `#888b91`
- CTA putih isi, teks hitam. Sinyal `#0088ff` hanya ikon, link, ring fokus
- OK `#27c93f`, danger `#e6714f`

## Type
- Inter 400/500/600. H1 32px tracking −1.2px, bukan uppercase paksa.
- Label 12px muted. Sentuh 44px.

## Materials
- Kartu 24px, tombol 9px, nav pill. Tanpa bayangan.
- Aurora hanya di beranda dan layar tunggu. PDF kop midnight+putih, tanpa aurora.

## Layout
- Full-bleed midnight, isi max 40rem. Satu aksi primer putih penuh per layar. Sekunder charcoal. Skip tautan teks.
- Coach: intake unggah→cerita→tautan→kirim. Tinjau satu fakta. Field tetap di DOM.

## Voice
- Indonesia sederhana. CTA menamai aksi.

## Motion
- Cincin wait: busur sinyal berputar linear (GPU). Aurora di void. `prefers-reduced-motion` mematikan gerak.

## Surface — Operate
Visitor finishes a task. Technical proof lives in the ZIP, not the UI.
