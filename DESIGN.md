---
status: active
date: 2026-04-22
role: Canonical design system for Spotlight marketing + installer. Source of truth for typography, palette, components, layout, motion. Follow this when adding UI or modifying existing sections.
---

# Spotlight — design system

This document is **the** reference. If the code deviates from what's written here, fix the code. If a new pattern emerges, add it here first, then use it.

Scope: `index.html` (landing) and `setup.html` (installer). Both pages share the same DA — most patterns apply to both; a few are landing-only and marked as such.

---

## 1. Palette

```css
:root {
  /* Base surfaces */
  --ink              : #07070a;   /* dark sections primary bg */
  --ink-2            : #0c0c10;   /* dark sections secondary (setup script-box, selected card code) */
  --paper            : #ede8dc;   /* paper sections primary bg, inputs */
  --paper-2          : #e3ddce;   /* paper cards, code inline bg */
  --paper-fg         : #17140e;   /* text on paper, ghost-button border/color */

  /* Neutral text on dark */
  --warm             : #fff5d9;   /* light text/surface on dark (hero CTA cream) — NOT an editorial accent */

  /* Editorial accents */
  --accent-warm      : #c16a34;   /* terracotta — primary editorial accent */
  --accent-cool      : #346fa8;   /* steel blue — 3D scene tint, reserved */

  /* Muted text + borders */
  --muted-dark        : rgba(237, 232, 220, 0.55);  /* secondary text on ink */
  --muted-faint-dark  : rgba(237, 232, 220, 0.18);  /* borders on ink */
  --muted-light       : rgba(23, 20, 14, 0.55);     /* secondary text on paper */
  --muted-faint-light : rgba(23, 20, 14, 0.15);     /* borders on paper */

  /* Status (semantic — sparingly) */
  --green            : #4a7d3f;   /* positive verdict, success state */
  --amber            : #8a6212;   /* warning, advisory note */
  --red              : #a83838;   /* error, required marker, negative verdict */

  /* Pre-mixed accent-warm fades — add new tiers here when a new opacity is needed */
  --accent-warm-14   : rgba(193, 106, 52, 0.14);   /* CTA hover bg, selected tint */

  /* Motion defaults */
  --rd               : 0ms;       /* reveal delay (per-element via inline style) */
  --rs               : 0;         /* reveal stagger index */
  --stagger          : 180ms;     /* per-step stagger unit — used by data-reveal[--rs] */
}
```

### Usage rules

- **Alternate ink ↔ paper sections** to rhythm the page. No third background colour.
- **`--accent-warm`** is the only editorial accent. Use for:
  - Italic emphasis in titles + pull quotes (via `<em>` or `.accent`)
  - Hover states (nav links, footer links, CTAs, int-card hover fill via `rgba(193,106,52,0.14)`)
  - Terracotta fills (progress bars, stat numbers, input focus underline)
  - Numeric accents (Fraunces numerals in counters, card indexes, stat numbers)
- **`--accent-cool`** is reserved: 3D scene (cube tints) + sparing secondary data-viz. Never on copy/CTAs.
- **`--warm`** is *not* an accent — it's just the cream text colour on dark (hero CTA text, CTA border at 35%).
- **Status colours** (`--green/--amber/--red`): only for semantic states (verdicts, warnings, errors, required markers). Never for decoration. Never as editorial accent.
- **Token aliases for transparent accents**: when fading accent-warm, use `rgba(193, 106, 52, X)` with X in `{0.08, 0.14, 0.24}` — the three tiers in use. When fading cream on dark, use `rgba(255, 245, 217, X)` with X in `{0.18, 0.35}`.

### Anti-pattern

- Do not introduce new semantic colours (no blue info, no purple, no teal). Stay on the 12 tokens above.
- Do not use `--accent-warm` for warnings — that's what `--amber` is for.
- Do not use `--warm` as an editorial accent — that's `--accent-warm`'s job.

---

## 2. Typography

Two families. No third font.

### Families

- **Fraunces** — display, titles, editorial italics, numeric accents. Variable axis `opsz` (9 → 144) + `wght` (400 / 500). Always set `font-variation-settings` explicitly:
  ```css
  font-family: "Fraunces", serif;
  font-variation-settings: "opsz" <calibrated>, "wght" <400|500>;
  ```
  Weights 400 (italic accent) and 500 (all display). **Never 600+** — it loses the elegance.
- **Geist Mono** — UI text, body, eyebrows, labels, buttons, code, inputs. Variable weight 400 / 500. Fallback stack: `"Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace` — use this full stack everywhere (inputs, labels, body, buttons) for consistency.

### Display tiers (Fraunces)

| Tier | Role | Size | Weight | `opsz` | Letter-spacing | Line-height |
|---|---|---|---|---|---|---|
| **D1** | Hero title | `clamp(44px, 6.5vw, 96px)` | 500 | 144 | `-0.025em` | `0.98` |
| **D2** | Footer huge | `clamp(80px, 18vw, 280px)` | 500 | 144 | `-0.04em` | `0.82` |
| **D3** | Stat mega (first cell) | `clamp(100px, 15vw, 220px)` | 500 | 144 | `-0.04em` | `0.82` |
| **D4** | Stat standard | `clamp(72px, 11vw, 160px)` | 500 | 144 | `-0.04em` | `0.82` |
| **H1** | Section title (landing) | `clamp(32px, 4.5vw, 60px)` | 500 | 96 | `-0.02em` | `1.02` |
| **H2** | Section title (setup) | `clamp(28px, 3.6vw, 44px)` | 500 | 96 | `-0.02em` | `1.05` |
| **H3** | Card / offer heading | `clamp(28px, 3.8vw, 48px)` | 500 | 72 | `-0.015em` | `1.08` |
| **H4** | Int-card name, credits entity | `clamp(22px, 2.4vw, 34px)` | 500 | 72 | `-0.02em` | `1.1` |
| **PQ** | Pull quote (chapter-break) | `clamp(24px, 3.2vw, 42px)` italic | 400 | 72 | `0` | `1.25` |
| **L1** | Attribution lede, generate-bar label | `clamp(22px, 2.6vw, 34px)` italic | 400 | 72 | `0` | `1.3` |

**Italic accent rule**: inside a Fraunces display element, use `<em>` (or `<span class="accent">`) to emphasise 1–3 words with:
```css
font-style: italic;
font-variation-settings: "opsz" <same>, "wght" 400;
color: var(--accent-warm);
```
Reserved slots: `.hero-title .accent`, `.attr-lede em`, `.chapter-break .pull em`, `.footer-huge em`, `.generate-bar .lbl em`. Nothing else gets italic terracotta.

### UI tiers (Geist Mono)

All UI text is uppercase OR mixed-case per role; never small-caps. Letter-spacing tiers below are **canonical** — don't improvise.

| Tier | Role | Size | Weight | Letter-spacing | Transform | Opacity |
|---|---|---|---|---|---|---|
| **M-body** | Body text | 14px | 400 | `0` | none | 1 |
| **M-body-sm** | Card desc, small body, input text | 13px | 400 | `0` | none | `0.85` on dark |
| **M-label** | Form label, nav link | 11px | 500 | `0.15em` | UPPERCASE | 1 |
| **M-eyebrow** | Chapter label (`.chap`), eyebrow, section meta | 11px | 500 | `0.2em` | UPPERCASE | `0.55` |
| **M-pill** | Install-pill CTA, footer col title (`h4`), credits role | 11px | 500 | `0.22em` | UPPERCASE | 1 |
| **M-btn** | Primary/ghost button | 11px | 500 | `0.18em` | UPPERCASE | 1 |
| **M-meta** | Stat label, footer-meta, offer kicker, card idx | 10px | 500 | `0.2em` | UPPERCASE | `0.55–0.6` |
| **M-ext** | External link marker (↗ TYPE) in credits | 10px | 500 | `0.18em` | UPPERCASE | `0.6` |
| **M-cue** | Hero `.scroll-cue` overlay on WebGL | 10px | 500 | `0.25em` | UPPERCASE | 0.5 |
| **M-code** | Inline `<code>`, script box | 11–12px | 400 | `0` | none | 1 |

Don't introduce new letter-spacing values between these tiers. If something needs a different `ls`, it's probably a new tier that should be added here first.

### Anti-patterns

- Fraunces weight 600 or 700 — never. Stay 400/500.
- Geist Mono as display (hero title, section title) — never. Display is always Fraunces.
- Letter-spacing other than `{0, 0.15em, 0.18em, 0.2em, 0.22em}` for uppercase mono. Other values are drift.
- Mixing `"Fraunces", serif` and `"Fraunces", Georgia, "Times New Roman", serif` — use `"Fraunces", serif` everywhere.
- Lowercase-with-underline for links. We use opacity shift + terracotta hover.

---

## 3. Components

### 3.1 Nav — `nav.topnav`

Fixed top. Three-state (hero / dark / paper). JS observes `data-nav-theme` on sections + toggles `.on-hero`, `.on-dark`, or the default class.

```html
<nav class="topnav" id="topnav">
  <a href="#" class="brand">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true">
      <path d="M 20 9.5 A 8 8 0 0 1 4 9.5"/>
      <path d="M 4 14.5 A 8 8 0 0 0 20 14.5"/>
      <circle cx="12" cy="12" r="2" fill="currentColor"/>
    </svg>
    Spotlight
  </a>
  <div class="links">
    <a href="#link-1">Link</a>
    <a href="setup.html">Install</a>  <!-- auto-promotes to pill CTA -->
  </div>
</nav>
```

| State | Class | Trigger (section attr) | Style |
|---|---|---|---|
| **paper** (default) | none | no `data-nav-theme` | bg `--paper`, text `--paper-fg`, border-bottom `--muted-faint-light` |
| **hero** | `.on-hero` | `data-nav-theme="hero"` | bg transparent, text `#fff`, `mix-blend-mode: difference` |
| **dark** | `.on-dark` | `data-nav-theme="dark"` | bg `--ink`, text `--paper`, border-bottom `--muted-faint-dark` |

**Transition rule:** only `color` + `border-color` animate (220ms ease). `background` snaps instantly — otherwise a semi-transparent "bleed" frame appears during scroll.

**Install pill (CTA):**
```css
nav.topnav .links a[href="setup.html"] {
  padding: 9px 14px;
  border: 1px solid currentColor;
  letter-spacing: 0.22em;  /* M-pill tier */
  opacity: 1;
}
nav.topnav .links a[href="setup.html"]:hover {
  background: rgba(193, 106, 52, 0.14);
  border-color: var(--accent-warm);
  color: var(--accent-warm);
}
```
`border-color: currentColor` makes the pill auto-follow nav state. On mobile (≤760px): all text links `display:none`, only Install pill survives.

### 3.2 Chapter-break (landing only)

Ink band with italic Fraunces pull quote. Use once between act changes (not more).

```html
<section class="chapter-break" data-nav-theme="dark">
  <p class="pull" data-reveal="serif-line">
    <em>Accent phrase</em> rest of the line.
  </p>
</section>
```
Padding `120px 48px`, text-align center, `border-top: 1px solid var(--muted-faint-dark)`, max-width `22ch`.

### 3.3 Section header — `.header`

Canonical section entry point. Two variants: chap+title (landing), eyebrow-only (setup uses `.sec-header`).

```html
<div class="header">
  <span class="chap" data-reveal="meta">Ch. 0X — Topic</span>
  <h2 class="title" data-reveal="title" style="--rd: 120ms">Short sentence.</h2>
</div>
```
```css
.header {
  display: flex; justify-content: space-between; align-items: baseline;
  padding: 80px 48px 40px;
  border-bottom: 1px solid var(--muted-faint-light);
}
.header .chap { /* M-eyebrow tier */ }
.header .title { /* H1 tier */ }
```
Mobile (≤760px): stacks vertically, `gap: 12px`, `.title` reflows to `clamp(26px, 7.5vw, 42px)`.

### 3.4 Cards

Four variants. All squared (no border-radius). All use `var(--paper-2)` or `var(--paper)` bg on paper sections.

**Int-card** (integration slot, landing):
```css
.int-card {
  flex: 0 0 380px; height: 440px;
  background: var(--paper-2);
  border: 1px solid var(--muted-faint-light);
  padding: 32px 28px 28px;
  display: flex; flex-direction: column; gap: 18px;
  transition: background 300ms ease, color 300ms ease, border-color 300ms ease;
}
.int-card:hover { background: var(--ink); color: var(--paper); border-color: var(--ink); }
```
Contains `.idx` (M-meta), `.card-scene-slot` (200×200 square, WebGL), `.name` (H4), `.desc` (M-body-sm).

**Offer** (pricing):
```css
.offer {
  padding: 48px 40px 44px;
  border-right: 1px solid var(--muted-faint-light);  /* last: no border */
  min-height: 520px;
  transition: background 300ms ease;
}
.offer:hover { background: var(--paper-2); }
.offer ul li::before {
  content: ""; display: inline-block; width: 10px; height: 1px;
  background: var(--paper-fg); opacity: 0.5; margin-right: 10px;
  transform: translateY(-4px);
}
```

**Form card** (`section.card`, setup): `background: var(--paper-2); border: 1px solid var(--muted-faint-light); padding: 28px; margin-top: 28px;`

**Radio/checkbox card** (setup):
```css
.radio-group label {
  cursor: pointer; padding: 16px 18px;
  border: 1px solid var(--muted-faint-light);
  background: var(--paper);
  display: flex; gap: 14px; align-items: flex-start;
  transition: background 150ms, color 150ms, border-color 150ms;
}
.radio-group label:has(input:checked) {
  background: var(--ink); color: var(--paper); border-color: var(--ink);
}
.radio-group label:has(input:checked) .name { color: var(--accent-warm); }
.radio-group input { accent-color: var(--accent-warm); }
```
Hover: `border-color: var(--paper-fg)` (no fill). Selected: full inversion, name flips to terracotta.

### 3.5 CTAs

Three tiers. Shape = no border-radius (flat/bordered). The nav Install CTA uses `border-color: currentColor` so the border auto-follows the nav state (paper-fg on paper, paper on dark, #fff on hero) — reuse this pattern if you need a colour-adaptive bordered button, but keep the shape squared.

**Hero CTA (neutral cream on dark)**:
```css
.hero-side .cta {
  padding: 12px 16px 12px 18px;
  background: transparent;
  color: var(--warm);
  border: 1px solid rgba(255, 245, 217, 0.35);
  font-family: "Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px; letter-spacing: 0.2em; text-transform: uppercase; font-weight: 500;
  text-decoration: none; display: inline-flex; align-items: center; gap: 8px;
}
.hero-side .cta:hover {
  background: rgba(193, 106, 52, 0.14);
  border-color: var(--accent-warm);
  color: var(--accent-warm);
}
```

**Hero body copy**:
```css
.hero-description {
  margin: 0;
  color: rgba(237, 232, 220, 0.92);
  font-size: 14px;
  font-weight: 500;
  line-height: 1.6;
}
.hero-helper {
  color: rgba(237, 232, 220, 0.72);
}
/* If the hero side uses a backdrop panel over the scene, keep it square. */
.hero-side::before {
  border-radius: 0;
}
```

**Primary button (solid ink on paper)**:
```css
.btn-primary {
  padding: 14px 18px;
  background: var(--ink); color: var(--paper);
  border: 1px solid var(--ink);
  /* M-btn tier: 11px, 0.18em, uppercase */
}
.btn-primary:hover { background: var(--paper-fg); border-color: var(--paper-fg); }
```

**Ghost button (outline on paper)**:
```css
.btn-ghost {
  padding: 14px 18px;
  background: transparent; color: var(--paper-fg);
  border: 1px solid var(--paper-fg);
}
.btn-ghost:hover { background: var(--paper-fg); color: var(--paper); }
```

**Hero secondary button** (hero "GitHub ↗"): transparent ghost button on dark. Keep the background transparent at rest and on hover so the scene remains visible through it.
```css
.hero-side .secondary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px 18px;
  background: transparent;
  color: rgba(237, 232, 220, 0.92);
  border: 1px solid rgba(255, 245, 217, 0.68);
  font-size: 11px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  text-decoration: none;
  white-space: nowrap;
}
.hero-side .secondary:hover {
  background: transparent;
  color: var(--accent-warm);
  border-color: var(--accent-warm);
}
```

**Secondary text link** (card "See cases"): `opacity: 0.55; font-size: 11px; letter-spacing: 0.18em; text-transform: uppercase;` — hover `opacity: 1; color: var(--accent-warm); border-bottom: 1px solid var(--accent-warm);`.

### 3.6 Inputs (setup)

Squared, paper-on-paper, terracotta focus signal.

```css
input[type="text"], input[type="password"], select {
  width: 100%;
  background: var(--paper);  /* or --paper-2 if on paper card */
  border: 1px solid var(--muted-faint-light);
  border-radius: 0;  /* squared */
  padding: 12px 14px;
  color: var(--paper-fg);
  font-family: "Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;  /* M-body-sm */
  transition: border-color 150ms ease, box-shadow 150ms ease;
}
input:focus, select:focus {
  outline: none;
  border-color: var(--accent-warm);
  box-shadow: inset 0 -2px 0 0 var(--accent-warm);  /* underline signal — this is the ONE exception to no-box-shadow */
}
```
Focus underline is the signature — keep it. No rounded inputs. No pill inputs.

Label pattern:
```html
<label class="block">Field name <span class="hint">— <a href="...">help text</a></span></label>
<input type="text" ...>
```
```css
label.block {
  display: block; margin-bottom: 8px;
  /* M-label tier: 11px, 0.15em, uppercase */
  opacity: 0.7;
}
label.block .hint { opacity: 0.6; text-transform: none; letter-spacing: 0; font-size: 11px; }
```

### 3.7 Stats row (landing)

Four-cell grid, first cell is mega.

```html
<div class="stats-row">
  <div class="stat-cell" data-reveal="body" data-stagger-idx="0">
    <div class="n" data-count="11">11</div>
    <div class="l">Skills</div>
  </div>
  <!-- + 3 more, last-of-type lose right border -->
</div>
```
```css
.stats-row { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; }
.stat-cell { padding: 60px 40px; border-right: 1px solid var(--muted-faint-light); }
.stat-cell:last-child { border-right: none; }
.stat-cell .n { /* D4 tier. First cell: D3 */ color: var(--accent-warm); }
.stat-cell .l { /* M-meta tier, margin-top: 16px */ }
```
`data-count="N"` drives a JS count-up animation. Mobile: `2×2` grid.

### 3.8 Credits row (landing)

Used in Built-on section. Two-column: role label + entity block.

```html
<div class="credits" data-reveal="credit-row" data-stagger-idx="0">
  <div class="role">Originated by</div>
  <div class="entities">
    <div class="entity">
      <a href="...">Name</a> <span class="ext">↗ EXT</span>
    </div>
    <p class="sub">Short description.</p>
  </div>
</div>
```
```css
.credits {
  display: grid; grid-template-columns: 200px 1fr; gap: 40px;
  padding: 28px 0;
  border-top: 1px solid var(--muted-faint-dark);
}
.credits:last-child { border-bottom: 1px solid var(--muted-faint-dark); }
.credits .role { /* M-pill tier, color: --muted-dark */ }
.credits .entity a { /* H4 tier */ }
.credits .ext { /* M-ext tier */ }
.credits .sub { /* M-body-sm, max-width: 58ch, opacity: 0.7 */ }
```

### 3.9 Footer (both)

Canonical summary-left / actions-right footer shared by `index.html` and `setup.html`.

```html
<section id="footer" data-nav-theme="dark">
  <div class="footer-simple">
    <p class="footer-summary">
      An open-source investigative system for AI agents, built for journalists.
      One agent reports, one agent checks, and you stay the editor.
      Local when the case is too sensitive for the cloud.
    </p>
    <div class="footer-links">
      <a href="setup.html">Install</a>
      <a href="https://buriedsignals.com/consulting" target="_blank" rel="noopener">Work with me ↗</a>
      <a href="https://buriedsignals.com" target="_blank" rel="noopener">Buried Signals ↗</a>
    </div>
  </div>

  <div class="footer-meta" data-reveal="meta">
    <div class="brand-mini">
      <svg>…</svg> <span>Spotlight · v1.0</span>
    </div>
    <span>© 2026 Buried Signals — MIT licensed</span>
  </div>
</section>
```

```css
.footer-simple {
  display: grid;
  grid-template-columns: minmax(0, 56ch) auto;
  align-items: start;
  gap: 32px;
  padding-top: 28px;
  border-top: 1px solid var(--muted-faint-dark);
}
.footer-links {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  justify-content: flex-end;
  align-content: flex-start;
}
```

Key:
- Shared on landing and setup. No alternate footer variants.
- Summary stays on the left, action buttons live on the right.
- Footer links use the bordered button treatment, not text lists.
- Mobile collapses to one column and left-aligns the action row.
- `footer-meta` stays as the only secondary row beneath the component.

### 3.10 Scene slot system (landing only)

One fixed `<div id="global-canvas">` at `z-index: 5, pointer-events: none`. Three.js renders into it once. Each visual slot in the DOM is a `<div class="scene-slot">` (or specialized `#hero-canvas`, `.card-scene-slot`, `#five-things-slot`) — measured at runtime, rendered into via scissor + viewport. Outside slots, canvas is transparent.

```html
<div id="global-canvas" aria-hidden="true"></div>
<!-- later, in content: -->
<div class="card-scene-slot" data-card-idx="0"></div>
```
```css
#global-canvas { position: fixed; inset: 0; z-index: 5; pointer-events: none; }
.card-scene-slot { flex: 0 0 200px; aspect-ratio: 1/1; align-self: center; }
```
Setup does not use this system — no 3D on form pages.

### 3.11 Spotlight mark (brand)

SVG 24×24. Two arcs + central filled circle. Stroke = `currentColor` so it follows nav state.

```svg
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
  <path d="M 20 9.5 A 8 8 0 0 1 4 9.5"/>
  <path d="M 4 14.5 A 8 8 0 0 0 20 14.5"/>
  <circle cx="12" cy="12" r="2" fill="currentColor"/>
</svg>
```
Sizes: 18×18 in nav brand, 14×14 in footer-meta brand-mini. Favicon: same SVG with stroke `#c16a34` (terracotta), fill `#c16a34` on the inner circle. Embed as inline `data:image/svg+xml;utf8,...` in a `<link rel="icon" type="image/svg+xml">` in `<head>`.

---

## 4. Layout & spacing

### Rhythm

- No global container — sections are full-bleed with inline padding (48px desktop, 20px mobile).
- Section vertical padding: `80–140px` block depending on section weight. Hero can go larger.
- Section transitions: `border-top: 1px solid var(--muted-faint-dark)` between dark sections, `--muted-faint-light` between paper sections.
- Max-widths live on *content* (titles, ledes, notes, footer colums), never on the section itself.

### Canonical paddings

| Context | Desktop | Mobile (≤760px) |
|---|---|---|
| Nav | `20px 28px` | `14px 20px` |
| Hero (landing) | `92px 48px 40px` | `72px 20px 32px` |
| Hero (setup) | `140px 48px 100px` | `110px 20px 60px` |
| Section header `.header` | `80px 48px 40px` | `84px 20px 22px` |
| Chapter-break | `120px 48px` | `80px 20px` |
| Integrations / Offers / Box | `120px 48px` | `60px 20px` |
| Attribution | `140px 48px 120px` | `80px 20px` |
| Footer | `140px 48px 40px` | `80px 20px 28px` |
| Form card | `28px 28px` | `24px 20px` |

### Canonical max-widths

- Titles: `14ch` (hero), `22ch` (sec-header setup).
- Pull quote: `22ch`. Attribution lede: `30ch`.
- Body copy: `54ch` (step-body), `58ch` (credits sub), `60ch` (box note).
- Form wrap: `880px`. Install-output wrap: `1080px`. Hero-grid (setup): `1280px`.
- Card headings: `14ch` (offer h3), `18ch` (step title). Leads: `44ch`.
- Footer about col p: `38ch`.

### Canonical grids

| Grid | Columns | Gap | Mobile |
|---|---|---|---|
| Hero bottom | `1fr auto` | `48px` | `1fr`, gap `32px` |
| Stats | `2fr 1fr 1fr 1fr` | `0` | `1fr 1fr` |
| Credits | `200px 1fr` | `40px` | `1fr` |
| Offers | `1fr 1fr` | `0` | `1fr` |
| Install-options (setup) | `minmax(0, 2fr) minmax(0, 1fr)` | `0` | `1fr` |
| Footer | `2fr 1fr 1fr 1fr` | `40px` | `1fr 1fr`, `about` spans 2, gap `28px` |

---

## 5. Motion & reveal

Reveal system: set `data-reveal="<kind>"` on any element. An `IntersectionObserver` adds `.in`. For stagger, add `data-stagger-idx="N"` — CSS reads `--rs: N` and computes delay = `--rd + --rs * --stagger`. Per-element delay via inline `style="--rd: 400ms"`.

### Kinds

| Kind | Default | `.in` | Curve | Duration | Used on |
|---|---|---|---|---|---|
| `title` | `opacity:0; translateY(28px)` | 0, 0 | `cubic-bezier(0.2, 0.75, 0.2, 1)` | 1000ms | All section titles + hero title |
| `serif-line` | `opacity:0; translateY(18px)` | 0, 0 | same | 1200ms | Chapter-break pull, attribution lede |
| `body` | `opacity:0; translateY(12px)` | 0, 0 | `cubic-bezier(0.25, 0.6, 0.3, 1)` | 700ms | Body copy, descriptions, stat rows |
| `meta` | `opacity:0; letter-spacing:0.4em` | 1 (or 0.55 on eyebrows), 0.2em | same | 600ms op / 800ms ls | Eyebrows, chap labels, footer h4, section meta |
| `card` | `opacity:0; translateY(32px) scale(0.985)` | 1, 0 scale 1 | `cubic-bezier(0.2, 0.7, 0.25, 1)` | 900ms | `.int-card`, `.offer` |
| `credit-row` | `opacity:0; translateY(18px)` | 1, 0 | same as `body` | 800ms | `.credits` rows |
| `huge-word` | child spans `translateY(108%)`, parent `overflow:hidden` | `translateY(0)` | `cubic-bezier(0.16, 0.78, 0.22, 1)` | 1500ms, stagger 240ms | `.footer-huge` (JS word-wraps) |
| `scroll-cue` | `opacity: 0` | 0.5 | ease | 1500ms delay 1200ms | `.scroll-cue` |

**Stagger default: `--stagger: 180ms`** (page-level). Override per-context if needed (huge-word uses 240ms for slower cascade).

### Load-fire vs scroll-fire

- **Hero elements** (title, hero-side body) fire immediately on load via `requestAnimationFrame(() => el.classList.add('in'))`. Don't wait for IO.
- **Everything else** fires on IO entry with `rootMargin: '0px 0px -40px 0px'` (standard) or `-80px` (for slow reveals like huge-word).
- **Nav** fires on load with its own transition (`opacity:0; translateY(-8px) → .in`, 800ms delay 400ms).

### Reduced motion

`@media (prefers-reduced-motion: reduce)` collapses every reveal to a single `opacity: 0 → 1` fade over 300ms. Clears `transform`, `clip-path`, `letter-spacing`, `font-variation-settings`. Always keep the reveal initial state (`opacity: 0`) behind the reduced-motion rule so the content still animates in, just more subtly.

### Other motion

- `.scroll-cue .tick` — 2.2s infinite `scaleX 0.4 → 1` (keyframe `@scrollTick`).
- WebGL scene (landing): own RAF loop. Drive via `data-scene-mood` attributes if you need per-section variation — don't hard-code per-section scene logic inside Three.js.

### Anti-pattern

- Don't add `!important` to reveal rules unless specifically overriding a third-party style — current `[data-reveal="card"]` uses `!important` which we should clean up.
- Don't create new reveal kinds ad-hoc — check if one of the eight above fits first. New kinds get added to this table.

---

## 6. Breakpoints

Single breakpoint: **`760px`**. Above → desktop/tablet. Below → mobile. One intermediate at **`1100px`** on landing only (tightens `.split-scroll` grid). Do not add new breakpoints without discussion.

```css
@media (max-width: 1100px) { /* landing tablet only — split-scroll tightening */ }
@media (max-width: 760px)  { /* mobile — everything else collapses */ }
@media (prefers-reduced-motion: reduce) { /* see §5 */ }
```

---

## 7. Z-index layers

| z-index | Element | Notes |
|---|---|---|
| `50` | `nav.topnav` | Fixed top |
| `15` | `.split-left` (landing, sticky counter) | mobile |
| `10` | `#hero` | Above global canvas |
| `5` | `#global-canvas` | Single shared WebGL canvas |
| `3` | `.hero-content`, `.scroll-cue` | Above hero gradient |
| `2` | `.hero-gradient` | Dark overlay over canvas |
| `9999` | `#dev-panel` | Hidden by default, flag for ship |

Stay in this range. Don't introduce arbitrary z-indexes. If you need a new layer, pick a value from inside the existing band and document it here.

---

## 8. Anti-patterns (do not do)

- **Border-radius** > 2px on anything. The DA is flat & squared. Inputs: 0. Cards: 0. CTAs: 0. No exceptions.
- **Pill shapes** (`border-radius: 999px`). Never.
- **`box-shadow`**. One exception only: the input `focus` underline trick `box-shadow: inset 0 -2px 0 0 var(--accent-warm)`. Nothing else.
- **Cool palette** (`#0f1115`, blue `#7cb7ff`, indigo) or any colour outside the 12 tokens above.
- **Fraunces weight 600+**. 400 italic (accent) or 500 (all display). Nothing else.
- **Geist Mono as display**. Display is Fraunces, full stop.
- **Letter-spacing outside the uppercase mono scale** (`{0, 0.15em, 0.18em, 0.2em, 0.22em, 0.25em}` — the `0.25em` is reserved for **M-cue only**; dev-panel uses `0.14em`, which is acceptable because it's behind `display: none` in production).
- **Emoji in content** (✓ ⚠ 🚀). Use text ("OK", "Warning") or rely on terracotta/amber/red colour tokens.
- **Inline `style=""` for visual properties**. Acceptable for per-element reveal delays (`style="--rd: 400ms"`) and `data-count="N"`, not for margins, opacities, fonts.
- **`--warm` as editorial accent**. Cream is neutral surface. Terracotta (`--accent-warm`) is the accent.
- **`--accent-warm` for warnings**. Use `--amber` for warnings, `--red` for errors, `--green` for positive states.
- **New utility classes that duplicate existing tiers**. If you reach for `margin-top: 16px`, check if a card/section padding tier already handles it.

---

## 9. Alignment (code vs this doc)

Deviations observed on 2026-04-22 and their resolution status. 13/14 complete.

| # | Deviation | Status |
|---|---|---|
| 1 | `--stagger` divergence (80/240ms) | ✅ unified to 180ms |
| 2 | Status tokens missing from index | ✅ added |
| 3 | Dead CSS (`.eyebrow`, `line`/`hero-meta` kinds, empty `.hero-top`) | ✅ removed + stat kind reconnected to `.n` |
| 4 | Font stack strings | ✅ normalised to canonical |
| 5 | Letter-spacing drift (0.14em, 0.25em) | ✅ doc updated (M-cue tier + dev-panel exception) |
| 6 | Font-size drift (12.5/13.5px) | ✅ aligned to 12/13 |
| 7a | Inline `<code>` styles | ✅ factored into `code {}` rule |
| 7b | Other inline `style=""` (margins, font-sizes, opacities) | ⏳ pending — needs utility classes |
| 8 | `.header` rule duplicated 5× in index | ✅ factored (-46 lines) |
| 9 | `.hero-bottom` → `.hero-grid` rename + bottom alignment fix | ✅ unified |
| 10 | Install pill duplicated in mobile | ✅ slimmed to overrides only |
| 11 | `.note-amber` misleading name | ✅ renamed to `.note-advisory` |
| 12 | `rgba(193, 106, 52, 0.14)` scattered | ✅ extracted to `--accent-warm-14` token |
| 13 | `!important` on `[data-reveal="card"]` | ✅ removed |
| 14 | `.footer-col ul li` bullet mismatch | ✅ false positive — rules are identical |

---

## 10. Workflow — adding or modifying UI

1. **Find the nearest existing pattern** in §3. Reuse it. 80% of new UI needs will fit an existing component.
2. **If nothing fits**, check §2 tiers and §1 tokens — compose from those first.
3. **If you truly need a new component**, add it to §3 of this doc *before* writing the CSS. Propose the pattern in a PR comment or chat; once agreed, codify.
4. **If you need a new token** (colour, font size, letter-spacing, spacing value), stop. Those are rare. Propose and justify before adding.
5. **Always test** both landing and setup if the change touches shared components (nav, footer, buttons, inputs, typography).
6. **Reduced motion** — if you add motion, extend the rule in §5 to collapse it.
7. **Run** `bun tests/setup-generator-check.js` and `bash tests/smoke.sh` after any change to `setup.html`.

---

*Last updated: 2026-04-22. When you change the DA, update this doc first.*
