# Design — SatuAman Tanggap60

<!-- impeccable:design-schema 1 -->

## World
Dua permukaan. **Landing:** Motionsites AI Image Generator UI — kanvas putih, tiga kartu radial kuning–pink–ungu. **Kasus:** aurora wizard seperti layar Periksa — indigo bukan void Superwhisper, bukan walnut ORYZO.

## Palette
- Landing: canvas `#ffffff`, teks `#0f172a`, muted `#64748b`, kartu `#F4F8F9`, aksen `#F5C344` / `#F28482` / `#B567C2`
- Kasus: canvas `#0c1224`, surface `#030719` / `#001b33`, teks `#ffffff`, muted `#888b91`
- Kasus CTA putih isi, teks hitam. Sinyal `#0088ff` ikon, tautan, cincin. OK `#27c93f`, danger `#e6714f`

## Type
- Inter 400/500/600. Landing H1 2.75rem tracking −0.02em. Kasus H1 32px tracking −1.2px.
- Label 12px muted. Sentuh 44px.

## Materials
- Landing kartu 20px, bayangan `0 10px 30px -10px`. Kasus kartu 24px, tombol 9px, tanpa bayangan.
- Aurora penuh di semua layar kasus. PDF kop midnight+putih, tanpa aurora.

## Layout
- Landing max 1100px pada seksi fitur; hero/FAQ max 40rem. Dua form `POST /start`.
- Kasus isi max 40rem. Stepper tengah: Bukti → Periksa → Tinjau → Paket. Satu aksi primer putih penuh. Skip tautan teks.
- Coach: intake unggah→cerita→tautan→kirim. Tinjau satu fakta.

## Voice
- Indonesia sederhana. CTA menamai aksi. Tanpa copy generator gambar.

## Motion
- Kartu landing statis (sesuai bank). Kasus: cincin wait GPU 0.85s linear; stepper 180ms ease-out; `:active scale(0.97)`. `prefers-reduced-motion` mematikan gerak.

## Surface — Persuade + Operate
Landing meyakinkan lalu memulai. Kasus menyelesaikan tugas. Bukti teknis di ZIP, bukan di UI.
