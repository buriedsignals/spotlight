# Spotlight — design system (proto-session-5)

Extrait du landing `proto-session-5/index.html`. Référence pour porter les autres pages du site dans la même DA.

## Palette

```
--ink              #07070a   — fond principal (sections "dark")
--ink-2            #0c0c10   — fond secondaire dark
--paper            #ede8dc   — fond principal "light" (chaud, papier)
--paper-2          #e3ddce   — fond secondaire light (cards)
--paper-fg         #17140e   — texte sur paper
--warm             #fff5d9   — texte/surface clair·e sur dark (CTA neutres)
--accent-warm      #c16a34   — accent éditorial primaire (terracotta)
--accent-cool      #346fa8   — accent secondaire (steel blue, scène 3D)
--muted-dark       rgba(237,232,220,0.55)   — texte secondaire sur ink
--muted-faint-dark rgba(237,232,220,0.18)   — bordures sur ink
--muted-light      rgba(23,20,14,0.55)      — texte secondaire sur paper
--muted-faint-light rgba(23,20,14,0.15)     — bordures sur paper
```

Règle d'usage :
- Alterner sections **ink ↔ paper** pour rythmer la page.
- **`--accent-warm`** (terracotta `#c16a34`) est l'accent éditorial primaire — fonctionne sur paper ET sur ink. Réservé à : italiques d'emphase dans titres et pull quotes (`em`, `.accent`), hover states (nav, footer, credits, marquee count), barre de progression / fill, numérotation Fraunces (counter du split-scroll, idx des cards, n des stats). Tient lieu de "couleur de marque".
- **`--accent-cool`** (steel blue `#346fa8`) — utilisé en pair avec `accent-warm` dans la scène 3D (tints de cubes). Sur les pages statiques : disponible mais à utiliser avec parcimonie (ex. data-viz secondaire, badges).
- **`--warm`** (cream `#fff5d9`) n'est plus un accent éditorial — c'est juste une couleur de texte clair sur fond sombre. Utilisé pour le texte des CTA neutres (ex. `.hero-side .cta` qui a `color: var(--warm)` + `border: 1px solid rgba(255,245,217,0.35)`), avec le hover qui flip vers `--accent-warm`.
- **Fades / glows** : utiliser `rgba(193, 106, 52, X)` pour les fades terracotta (ex. background CTA hover à 0.14) et `rgba(255, 245, 217, X)` pour les fades cream neutres (bordures CTA, surfaces très claires sur dark).

## Typographie

- **Display / titres** : `Fraunces` (variable, opsz 9-144). Toujours `font-variation-settings: "opsz" <taille>` pour calibrer l'optical size.
  - Hero : `clamp(44px, 6.5vw, 96px)`, weight 500, letter-spacing `-0.025em`, line-height 0.98
  - H2 section : `clamp(32px, 4.5vw, 60px)`, weight 500, letter-spacing `-0.02em`
  - Pull quote / lede italique : `clamp(22px, 2.6vw, 34px)`, weight 400 italic
  - Footer huge : `clamp(80px, 18vw, 280px)`, weight 500, letter-spacing `-0.04em`
- **UI / body** : `Geist Mono` (mono variable). Tout le reste — body, eyebrows, labels, boutons.
  - Body : 14px, line-height 1.6
  - Eyebrow / chap label : 11px, letter-spacing `0.18em-0.22em`, uppercase, opacity 0.55
  - Bouton : 11px, letter-spacing 0.2em, uppercase
- **Italique d'accent** : `font-style: italic` sur Fraunces avec `color: var(--warm)`. Réservé aux mots clés dans les titres dark.

## Composants récurrents

### Nav
Fixe top, `mix-blend-mode: difference`, padding 20px 28px. Brand = pictogramme Spotlight + wordmark Fraunces 17px.

### Section header
```html
<div class="header">
  <span class="chap">Ch. 0X — Topic</span>
  <h2 class="title">Phrase courte.</h2>
</div>
```
Padding `80px 48px 40px`, flex justify-between baseline, border-bottom muted-faint.

### Card (paper bg)
```css
background: var(--paper-2);
border: 1px solid var(--muted-faint-light);
padding: 32px 28px;
```
Hover possible : invert vers ink/paper.

### CTA primaire
- Sur dark (CTA neutre type "Install Spotlight" du hero) : bordure `rgba(255,245,217,0.35)`, texte `--warm`, hover → background `rgba(193,106,52,0.14)` + border + color = `--accent-warm`. Le bouton commence "neutre cream" et s'allume terracotta au hover.
- Sur paper (CTA primaire type offers) : background `--ink`, texte `--paper`, padding `14px 18px`, mono uppercase 11px / 0.2em. Hover : background `--paper-fg`.
- Variante ghost : transparent + bordure `--paper-fg`. Hover : remplit en `--paper-fg`.
- Lien secondaire : opacity 0.55, hover → `--accent-warm` + border-bottom solide.

### Inputs (à porter)
Dans la nouvelle DA, les inputs doivent abandonner le look "rounded glass" pour un look papier strict :
- `background: var(--paper)` (sur card paper-2) ou `paper-2` (sur card paper)
- `border: 1px solid var(--muted-faint-light)` → focus `border-color: var(--paper-fg)`
- `border-radius: 0` (carré) ou max 2px — pas de pilules
- Padding 12px 14px
- Font Geist Mono 13px
- `outline: none`, focus = bordure pleine ink

### Radio / checkbox cards
Garder le pattern label-as-card mais en monochrome :
- État neutre : background paper, bordure muted-faint-light
- Hover : background paper-2
- Sélectionné : background ink, color paper (inversion totale) — c'est plus radical et plus dans la DA que juste un changement de bordure
- Pas de border-radius

### Pictogramme Spotlight (mark)
SVG 24×24 simple stroke, deux arcs + cercle central rempli :
```svg
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
  <path d="M 20 9.5 A 8 8 0 0 1 4 9.5"/>
  <path d="M 4 14.5 A 8 8 0 0 0 20 14.5"/>
  <circle cx="12" cy="12" r="2" fill="currentColor"/>
</svg>
```

## Layout / spacing

- Container max-width : pas de max-width global — sections full-bleed avec padding latéral 48px desktop / 20px mobile.
- Pour pages "app" ou "form" type setup.html : utiliser un wrap centré `max-width: 880px` + padding `80px 48px` pour garder la lisibilité tout en restant cohérent.
- Verticales : sections 80-140px de padding-block, transitions inter-sections via border-top muted-faint.

## Animations / reveal

Système `[data-reveal="<kind>"]` avec IntersectionObserver qui ajoute `.in`. Kinds disponibles : `title` (translateY + fade), `serif-line`, `body`, `meta` (letter-spacing collapse), `card`, `line` (scaleX), `credit-row`, `huge-word`, `hero-meta`, `stat`, `scroll-cue`.

Pour pages secondaires (setup, etc.), suffit d'utiliser `title`, `body`, `meta`, `card`. Reduced-motion : tout collapse en un fade opacity 300ms.

## Footer (mini variante pour pages secondaires)

Pour une page non-narrative, omettre le `footer-huge`. Garder seulement le `footer-grid` 4 colonnes + `footer-meta` (brand-mini + copyright). Padding réduit à `80px 48px 40px`.

## Anti-patterns à proscrire (héritage v1)

- Border-radius arrondi (8-10px) sur cards et inputs
- Palette froide (`#0f1115`, bleu `#7cb7ff`) ou bleu indigo générique
- `Geist Mono` comme display
- Fraunces weight 600+ (trop gras, perd l'élégance — rester 400-500)
- `box-shadow` (pas dans la DA — tout est plat, contraste pur)
- Émojis dans le contenu (✓ ⚠ etc.) — utiliser caractères texte ou les retirer
- Utiliser `--warm` (cream) comme accent éditorial — c'est `--accent-warm` (terracotta) qui joue ce rôle. `--warm` ne sert qu'aux surfaces/textes clairs neutres sur dark.
- Utiliser `--accent-warm` pour des warnings/erreurs — c'est un accent éditorial, pas un signal sémantique. Pour les warnings garder une amber dédiée (`--amber: #8a6212` ou équivalent).
