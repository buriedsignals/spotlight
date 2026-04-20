---
status: active
date: 2026-04-20
role: Spec for the Resend newsletter signup integration on the Spotlight landing page — form UX, endpoint contract, and the membership/consulting block it lives in.
---

# Resend Integration — Spotlight Landing

## Goal

Add a two-card block to `landing.html` (before `<footer>`) that promotes Buried Signals Pro Membership and Consulting. Membership card includes a direct Resend signup form (same UX pattern as `osint-navigator`). Consulting card is a `mailto:` CTA plus a plain-text link to case studies.

## Source of copy

- `https://buriedsignals.com/newsletter` — Pro Membership block (launching May 2026, price line).
- `https://buriedsignals.com/consulting` — service categories and case studies (MediaStorm, The New Humanitarian, Republik, MAZ Journalistenschule) plus 20 Minuten and Le Temps per user.
- All final copy rewritten to Tom's voice per `kit/shared/voice-copy/SKILL.md` — "I" voice, no rhetorical questions, no generic "we", named clients, no corporate jargon.

## Structure

One `<section>` containing:
- Eyebrow `<h2>`: `From the team behind Spotlight`
- Two cards in a responsive grid (stacks on mobile). **Membership first, consulting second.**

```
┌─ section eyebrow: "From the team behind Spotlight" ───┐
│                                                        │
│  ┌─ CARD 1: Pro Membership ──┐  ┌─ CARD 2: Consulting ─┐
│  │ eyebrow + title           │  │ title + subtitle     │
│  │ subtitle                  │  │ 3 service lines      │
│  │ 4 bullets                 │  │ (client case names)  │
│  │ price line                │  │ [Get in Touch →]     │
│  │ [email input][Subscribe]  │  │ see case studies →   │
│  │ (success state)           │  │                      │
│  └───────────────────────────┘  └──────────────────────┘
└────────────────────────────────────────────────────────┘
```

## Copy

### Card 1 — Pro Membership

**Eyebrow tag:** `LAUNCHING MAY 2026`

**Title:** `Pro Membership`

**Subtitle:** `Investigations with AI. The tools to run your own.`

**Bullets (4 — voice-rewritten, tool-suite line names all four tools, no duplicate AI-tools line):**

- Collaborative investigations — shared leads, data, and methodology
- Live bootcamps, workshops, and events
- Hosted Pro tier of the agent extensions — coJournalist, Navigator, Spotlight, DataHound
- Investigation methodologies and AI techniques, in depth

**Price line:** `From $25/mo · 400+ journalists already reading`

**Form** (same pattern as osint-navigator `newsletter-box`):
- Email input (`type=email`, placeholder `you@example.com`, required)
- Submit button: `Subscribe`
- Success state: checkmark icon + `You're in. I'll ping you at launch.`
- Error state: inline `<div>` below input

**Endpoint:** `POST {NEWSLETTER_ENDPOINT}` with JSON `{ email, newsletters: ["buried_signals"] }`.
Leave `const NEWSLETTER_ENDPOINT = '/api/newsletter/subscribe';` as a placeholder at the top of the inline `<script>`. Tom wires the real URL + CORS later. Response contract mirrors osint-navigator: 2xx = success, non-2xx with `{ detail }` = error message to show.

### Card 2 — Consulting

**Title:** `Consulting`

**Subtitle:** `I train newsrooms to investigate with AI.`

**Three service lines (I-voice, case-study-backed clients):**

- **Workshops & training** — I run hands-on sessions where journalists investigate live stories with AI. *Le Temps · MAZ Journalistenschule · Republik · 20 Minuten.*
- **Custom AI tooling** — I build AI systems into your editorial workflow — archive search, source monitoring, research agents, whatever the newsroom needs. *MediaStorm.*
- **Investigation collaborations** — Visual production and AI pipelines paired with your field reporting. *The New Humanitarian.*

**CTA row:**
- Button: `Get in Touch →` → `mailto:tom@buriedsignals.com?subject=Spotlight%20%E2%80%94%20Consulting`
- Plain text link next to button: `See case studies →` → `https://buriedsignals.com/consulting`

## Visual styling

- Reuse existing `landing.html` CSS variables (`--bg-2`, `--border`, `--accent`, `--muted`) and the `.box-card` radius/border treatment. No new palette.
- Cards use the same card surface as the existing "What's in the box" block for consistency.
- Grid: `grid-template-columns: repeat(auto-fit, minmax(300px, 1fr))` with `gap: 16px`. Single-column under 640px.
- Bullets: reuse `ul.clean` styling or a compact variant.
- Form styling: mirror osint-navigator's `.newsletter-input-row` — input flex:1, button inline, accent-colored submit.
- `See case studies →` rendered as plain underlined link in `--muted` color, no button styling — matches the `.cta-row a.secondary` pattern already on the page.

## Behavior / JS

- Vanilla JS in a `<script>` tag at the bottom of the page (no build step — landing page is static).
- Form submit: prevent default → disable button → fetch POST → on 2xx, hide form and show success state → on error, show inline error and re-enable button.
- No analytics, no tracking, no email pre-fill.

## Out of scope (user handles later)

- Actual Resend endpoint URL + CORS configuration.
- Backend audience routing (which Resend audience ID "buried_signals" maps to).
- Double opt-in flow (handled by Resend itself if configured).
