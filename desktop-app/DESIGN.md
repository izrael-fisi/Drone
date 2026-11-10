# Proxigo desktop design system

Operator software used on a laptop in the field, often in daylight glare or a dim
vehicle. Every screen is a working tool (Impeccable "Operate" mode): scanability
and consistency come before expression. The map is the ground; panels float over
it in dark glass.

## Type

- **IBM Plex Sans** for all UI (400, 500, 600). **IBM Plex Mono** only for code,
  file paths, serial numbers, console output and raw reports (METAR). Both are
  bundled with `@fontsource` (latin subset) so the app renders offline.
- Base 14px. Floor 11px; nothing smaller.
- Roles: dock header 15px/600 · verdict or key value 18–24px/600 · row label 13px/400
  `slate-300/400` · row value 13.5px/500 `slate-100` · meta 12px `slate-500` ·
  section heading 12px/600 `slate-300`.
- Sentence case everywhere. No uppercase letter-spaced labels and no eyebrow labels.
- Numbers that change or line up use tabular figures (`tabular-nums`, or
  `.font-data-mono`, which now means tabular Plex Sans, not monospace).

## Icons

- **Phosphor** (`@phosphor-icons/react`, `*Icon` exports). Regular weight by
  default. Active rail items use `duotone`; the active product badge uses `fill`.
- Rail icons 20px, inline icons 14–16px. No icon tiles (rounded squares behind
  icons) above headings or list items.

## Color

- Ground `#0b0c0d`; text from `slate-100` (values) to `slate-500` (meta).
- One accent per context: Macula VPS `#FF6600`, Airspace & Weather `#A78BFA`,
  platform views (Overview, Settings, Account) `#38BDF8`. The accent marks the
  title-bar strip, active rail item, product badge and breadcrumb.
- State colors carry meaning only: ready/go `#22C55E`/`#63D706`, caution
  `#EAB308`, critical/no-go `#EF4444`.

## Surfaces

- The dock, HUDs and search sit on dark glass (`bg-bg-base/75` with
  `backdrop-blur-xl`) over the map. Glass is used only over the map.
- Inside panels, rows are quiet filled bands (`bg-white/[0.035]`, `rounded-md`),
  grouped in one container with hairline dividers (`divide-white/[0.06]`) when
  they belong together. No bordered card per row, and no nested cards.
- Status is text plus a 6–8px dot, or a soft tinted pill. Never an outlined pill.

## Sidebar

One glass sheet (`rgba(13,14,16,0.62)`, blur 2xl, 1px white/9% border, top
inner highlight), 224px labelled, or a 60px icon rail with tooltips. Active entries
take the product accent as a tint; icons switch to duotone. Panels open beside
the rail at 384px on a darker glass for text contrast.

## Components

Use the primitives in `src/platform/ui.tsx` (`PanelStack`, `PanelAction`,
`SettingLine`, `StatusRow`, `StateCard`, `SectionLabel`, `EmptyLine`). The dock
header shows `Product / Panel`, so `PanelStack` shows only the subtitle and action.

## Signature

Airspace & Weather's 12-hour wind strip: bars against the operator's gust limit
(a dashed line). Data drawn honestly against the limit that matters, instead of a
grid of boxed numbers.

## Checks

- `npx impeccable detect src/` after UI edits. Known false positive:
  `gray-on-color` on `.btn-secondary` in `index.css`.
- Verify in the desktop window, not a browser.
