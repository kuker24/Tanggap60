# Design — SatuAman Tanggap60

<!-- impeccable:design-schema 1 -->

## World
Satu dunia terang-tenang dari beranda sampai paket: **WASPADA TENANG**. Warm paper, tinta gelap, amber untuk satu aksi komitmen. Panel gelap hanya untuk jejak teknis (collapsed by default). Landing: CTA kondisi di viewport pertama, lalu satu cerita bukti berantakan yang tersusun tanpa menebak.

## Palette
- Kanvas `#faf7f1`, teks `#1c1917`, muted `#6f675c`, garis `#e6ddcb`, kartu `#ffffff`
- Primer amber `#92400e` (hover `#78350f`), teks tombol putih
- Sinyal/fokus/link biru `#1d4ed8`; OK `#15803d`; danger `#b91c1c`
- Varian lembut untuk latar status: amber `#f7e8d3`, hijau `#e2f2e7`, merah `#fbe4e1`, biru `#e3ecfd`
- Panel teknis: `#1c1917` + teks `#f5efe2`, mono untuk data

## Type
- Inter 400/500/600 + system fallback. H1 task 26–32px tracking −1.2px, balance wrap.
- Input mobile 16px (anti zoom Safari). Target sentuh ≥44px.

## Materials
- Kartu 20px + bayangan tipis `0 1px 2px`; tombol 9px; tanpa dekorasi berat.
- Sticky bottom CTA di alur konfirmasi/persetujuan + safe-area.
- Gerak: satu momen (cincin tunggu 0.85s); `prefers-reduced-motion` mematikan gerak.

## Layout
- Task flow max 40rem; dashboard max 60rem; landing max 40/68rem.
- Stepper Bukti → Periksa → Konfirmasi → Bertindak; kompak “N / 4 · Nama” di HP; `aria-current="step"`.
- Satu keputusan per kartu; radio cards untuk pilihan; ringkasan transaksi relasional (Rp → rekening → waktu).
- Bukti teknis di disclosure gelap / ZIP, bukan primary UI.

## Voice
- Indonesia sederhana. “Transaksi” bukan reporting unit; “rekening penerima” tanpa tuduhan; “kami tidak akan menebak”.
- Error menjawab: apa yang gagal, dampaknya, apa yang bisa dilakukan + Retry.
- Pendamping AI: ringkas (1–2 kalimat), menunjuk bukan menggurui; “Saya tandai bagian yang perlu Anda cek”, “Sebelum saya menyimpan perubahan ini, pastikan datanya benar”, “Bagian ini perlu Anda isi sendiri”, “Dokumen belum dikirim ke mana pun”. Panel chat drawer desktop / bottom-sheet HP; pointer ring biru + tooltip, hormati prefers-reduced-motion; aria-live polite.

## Surface — Persuade + Operate
Landing meyakinkan lalu memulai (4 langkah + satu mock “tidak menebak” + FAQ + TTL). Kasus menyelesaikan tugas: bukti → susun → konfirmasi → lakukan sekarang → paket → bawa ke situs resmi.
