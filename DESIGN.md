# Design — SatuAman Tanggap60

<!-- impeccable:design-schema 1 -->

## World
**Waspada Tenang.** Bukti berantakan → map forensik yang bisa diajar. Light paper #FFFBF7 + ink #0F172A + slate, hairline E7E0D3, amber #B45309 komitmen 35% (CTAs, step, trust). Bukan dark neon, bukan cream generik. Scene: siang di meja pendamping — kertas, stabilo, cap verifikasi.

## Palette
- paper #FFFBF7, paper-2 #F6EFE0, panel #FFFFFF, line #E7E0D3, line-2 #EAE2D1
- ink #0F172A, slate #475569, muted #64748B
- amber #B45309, amber-2 #92400E, amber-soft #FFF1D6, ring #F59E0B
- red #991B1B / soft #FFF1F2, green #065F46 / soft #ECFDF5, focus #1D4ED8
- Strategy: Restrained + committed amber. Fields that own regions, not scattered accents.

## Type
- UI: Inter 500/600/700/800 — operate workhorse, 16px body, 44px touch.
- Display: Source Serif 4 600/700/800, -0.035em tracking, clamp 28–40px.
- Mono: ui-monospace for hash/IDs. Measure 52–65ch. No gradient text.

## Materials
- Card: 14px radius, 1px hairline, soft shadow offset+blur (0 10 28 rgba, 0 2 8). Never colored left border >1px.
- Header: sticky, backdrop blur, hairline bottom.
- Buttons: pill 999px, ink primary with shadow, ghost with hairline. Hover lift -1px.
- Timeline: pill dots (ok/run/neutral), not uniform kicker.
- Progress: 8px track paper-2, fill amber gradient.

## Layout
- Shell 1.75fr + 320px rail, max 1120px. Rail sticky top 84px.
- Hero split 1.05 / 0.95 → 1 col <900px. Preview card stacked mini-facts.
- Stepper pill 22px. Cards grid 3 → 1 col <980px.

## Components
- choice (plain + primary amber-soft), dropzone dashed, fact card, status-card (READY/NEEDS_ACTION/BLOCKED top 3px), badge (ok/err/neutral dot), table pill, notice amber-soft.

## Motion
- One authored moment: rise 0.5s ease for hero preview; pulse for running dot; hover lift. No scattered bounces. Reduced-motion respected.

## Voice
- Indonesia sederhana, tegas, tidak hype. Setiap klaim punya sumber. CTA menamai aksi.

## Constraints
- No kicker eyebrow above heading, no hard offset shadows, no glass decoration, no monospace costume, no emoji icons. Contrast ≥4.5:1.
- Keep 1 web worker + 1 heavy, no framework, bundle <20KB CSS.

## Surface — Operate (intake→receipt)
Visitor completes task. Scanability > expression. Brand lives in precise details: step, hash, badge, timeline.
