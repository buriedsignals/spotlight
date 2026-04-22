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
- **Italique d'accent** : `font-style: italic` sur Fraunces avec `color: var(--accent-warm)`. Réservé aux mots clés dans les titres et pull quotes (`.hero-title .accent`, `.attr-lede em`, `.chapter-break .pull em`, `.footer-huge em`, `.generate-bar .lbl em`).

## Composants récurrents

### Nav — système 3 états
Fixe top, padding 20px 28px. Brand = pictogramme Spotlight + wordmark Fraunces 17px. Le nav adopte automatiquement l'un de 3 états selon la section visible sous le bord supérieur — JS observe `scrollY`, lit `data-nav-theme` sur les sections, ajoute la classe.

| État | Classe | Quand | Style |
|---|---|---|---|
| **paper** (défaut) | aucune | Sur sections paper / pas de `data-nav-theme` | `background: var(--paper)`, texte `--paper-fg`, border-bottom `muted-faint-light` |
| **hero** | `.on-hero` | Sur `[data-nav-theme="hero"]` | `background: transparent`, texte `#fff`, `mix-blend-mode: difference` (pour lire sur scène WebGL ou fond dark) |
| **dark** | `.on-dark` | Sur `[data-nav-theme="dark"]` | `background: var(--ink)`, texte `--paper`, border-bottom `muted-faint-dark` |

**Pattern HTML** : déclarer `data-nav-theme="hero"` sur le `#hero` et `data-nav-theme="dark"` sur toute section ink (chapter-break, attribution, footer, install-output, etc.). Les sections paper ne déclarent rien — elles tombent dans le défaut.

**Transitions** : seules `color` + `border-color` animent (220ms ease). Le `background` snap instantanément pour éviter un état "demi-transparent" pendant la transition.

**Liens nav** : `opacity: 0.85` au repos, hover → `--accent-warm` + `opacity: 1`. `align-items: center` sur le conteneur `.links` (pour aligner le texte régulier avec le CTA pill ci-dessous).

**Install — CTA pill** : le lien `a[href="setup.html"]` dans le topnav est promu en CTA bordé (pas un lien plat). Pattern :
```css
nav.topnav .links a[href="setup.html"] {
  padding: 9px 14px;
  border: 1px solid currentColor;
  letter-spacing: 0.22em;
  opacity: 1;
}
nav.topnav .links a[href="setup.html"]:hover {
  background: rgba(193, 106, 52, 0.14);
  border-color: var(--accent-warm);
  color: var(--accent-warm);
}
```
Le `border-color: currentColor` fait que le pill suit automatiquement la couleur du nav (paper-fg / paper / #fff selon l'état). Sur mobile (≤760px) : tous les liens texte se masquent, seul le pill Install reste visible — devient le CTA principal de la nav mobile.

### Global canvas + scene-slot system (landing only)
Le landing utilise **un seul** canvas WebGL fixed (`#global-canvas`, z-index 5) qui rend ses scènes Three.js dans des `<div class="scene-slot">` placés dans le DOM (hero, `.split-graph`, chaque `.int-card .card-scene-slot`, etc.). Le rendu utilise scissor + viewport pour découper chaque slot. Hors slots, le canvas est transparent.

Pages secondaires (setup, etc.) **ne reprennent pas** ce système — pas besoin de scène 3D sur un formulaire.

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

### Inputs
Look papier strict — pas de "rounded glass" :
- `background: var(--paper)` (sur card paper-2) ou `paper-2` (sur card paper)
- `border: 1px solid var(--muted-faint-light)`
- `border-radius: 0` (carré) ou max 2px — pas de pilules
- Padding 12px 14px
- Font Geist Mono 13px
- `outline: none`, focus → `border-color: var(--accent-warm)` + soulignement intérieur `box-shadow: inset 0 -2px 0 0 var(--accent-warm)` (signal terracotta)

### Radio / checkbox cards
Pattern label-as-card monochrome :
- État neutre : background paper, bordure muted-faint-light
- Hover : `border-color: var(--paper-fg)` (pas de remplissage — juste la bordure se durcit)
- Sélectionné : background ink, color paper (inversion totale), nom Fraunces flippe en `--accent-warm` (titre s'allume terracotta), `accent-color` du dot natif passe en `--accent-warm`
- Pas de border-radius

### Footer (4 colonnes canoniques)
Structure standard reprise par toutes les pages :
1. **About** (col 2x) : `<h4>Spotlight</h4>` + paragraphe descriptif
2. **Product** : What it does, Integrations, In the box, Install
3. **Project** : Built on, Work with us, GitHub ↗ — *(et NON "Resources" — c'est un héritage v1 à éviter)*
4. **Contact** : email, buriedsignals.com ↗, indicator.media ↗

`footer-meta` en bas : brand-mini (SVG + "Spotlight · v1.0") à gauche, copyright "© 2025 Buried Signals — MIT licensed" à droite. Hover sur les liens du footer → `--accent-warm`.

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

## Footer — variante mini (pages secondaires)

Pour une page non-narrative (setup, etc.), omettre le `footer-huge`. Garder seulement le `footer-grid` 4 colonnes (cf. structure ci-dessus) + `footer-meta`. Padding réduit à `80px 48px 40px`. Pour une page primaire (landing), conserver le `footer-huge` "Lead the **story.**" avec le `<em>` italique terracotta.

## Anti-patterns à proscrire (héritage v1)

- Border-radius arrondi (8-10px) sur cards et inputs
- Palette froide (`#0f1115`, bleu `#7cb7ff`) ou bleu indigo générique
- `Geist Mono` comme display
- Fraunces weight 600+ (trop gras, perd l'élégance — rester 400-500)
- `box-shadow` (pas dans la DA — tout est plat, contraste pur)
- Émojis dans le contenu (✓ ⚠ etc.) — utiliser caractères texte ou les retirer
- Utiliser `--warm` (cream) comme accent éditorial — c'est `--accent-warm` (terracotta) qui joue ce rôle. `--warm` ne sert qu'aux surfaces/textes clairs neutres sur dark.
- Utiliser `--accent-warm` pour des warnings/erreurs — c'est un accent éditorial, pas un signal sémantique. Pour les warnings garder une amber dédiée (`--amber: #8a6212` ou équivalent).
