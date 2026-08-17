# Interactive diagrams — zoomable/draggable canvas, hyperlinked nodes, no font cropping

## Deterministic report diagrams

For report diagrams selected in `report-draft.json`, the renderer—not the
model—emits all Mermaid source. Follow `diagram-design.md` for type selection
and visual grammar. The renderer pins Mermaid to `11.16.1` and
`@mermaid-js/layout-elk` to `0.2.2`, uses strict Mermaid security, and emits no
`click` directives or node URLs. Keep the font-ready initialization, natural
SVG sizing, overflow fixes, and canvas controls below.

Default capability for `report.html`. When the case involves an **entity network, money flow, funnel, or pipeline** (≥3 actors with directed relationships), the report SHOULD carry one or more interactive diagrams built with this recipe. Battle-tested in the health-insider investigation (June 2026); every fix below was learned against a real rendering failure — do not "simplify" them away.

## What the reader gets

- Drag to pan, ctrl/⌘-scroll to zoom (0.25×–10×), zoom buttons, reset, open-full-size-in-new-tab
- Crisp vector text at any zoom level (no pixelation)
- Click a node → opens its primary source (registry entry, Ad Library page, archived filing) in a new tab, with a hover affordance (glow + underline + ↗)
- Color-coded role classes + a floating legend

## Hard-won fixes — the three that matter

1. **Font-measurement cropping.** Mermaid measures node label widths at render time. If it renders before the page's webfonts load, every label is measured in the fallback font and then re-rendered wider → text clipped mid-character. Fix: `startOnLoad: false`, then `await document.fonts.ready; await mermaid.run();`.
2. **foreignObject clipping.** Even with fonts loaded, late-applied CSS (letter-spacing on cluster titles, custom `<small>` styling) widens labels past their measured `foreignObject` boxes. Fix: let labels overflow visibly (CSS below). Without this, edge labels and cluster titles clip.
3. **Pixelation at zoom.** Two causes. (a) Mermaid clamps SVGs with `max-width: 100%` so the diagram renders small and zoom magnifies a shrunken layout — fix with `flowchart: { useMaxWidth: false }`. (b) Pan/zoom must use the CSS `zoom` property (triggers re-layout, vectors stay crisp) — NOT `transform: scale()`, which rasterizes and blurs.

## Dependencies (CDN — viewer must be online; data/tables degrade gracefully offline, diagrams need the CDN)

```html
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11.16.1/dist/mermaid.esm.min.mjs';
  import elkLayouts from 'https://cdn.jsdelivr.net/npm/@mermaid-js/layout-elk@0.2.2/dist/mermaid-layout-elk.esm.min.mjs';

  const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  mermaid.registerLayoutLoaders(elkLayouts);
  mermaid.initialize({
    startOnLoad: false,            // render AFTER fonts load — see fix #1
    theme: 'base',
    layout: 'elk',                 // layered networks; see "layout choice" below
    securityLevel: 'loose',        // REQUIRED for click-node hyperlinks
    flowchart: { useMaxWidth: false },  // natural-size SVG — see fix #3a
    themeVariables: {
      // match these to the page's CSS so measurement == final render
      fontSize: '15px',
      fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
      primaryColor: isDark ? '#222b36' : '#ffffff',
      primaryBorderColor: isDark ? '#98a1ad' : '#6b7280',
      primaryTextColor: isDark ? '#e8e4da' : '#1f2a37',
      lineColor: isDark ? '#98a1ad' : '#9aa1ab',
      clusterBkg: isDark ? '#1b222b88' : '#f3eee466',
      clusterBorder: isDark ? '#d9b35a' : '#b8860b',
      edgeLabelBackground: isDark ? '#1b222b' : '#ffffff',
    }
  });

  await document.fonts.ready;      // fix #1 — never render before webfonts
  await mermaid.run();
  // re-apply initial zoom/pan now that SVGs exist
  document.querySelectorAll('.mermaid-wrap').forEach(function(wrap) {
    var m = wrap.querySelector('.mermaid');
    if (m && m.dataset.zoom) {
      m.style.zoom = m.dataset.zoom;
      m.style.transform = 'translate(' + (m.dataset.tx || 0) + 'px,' + (m.dataset.ty || 0) + 'px)';
    }
  });
</script>
```

## CSS (drop into the report stylesheet)

```css
/* ---------- diagram canvas ---------- */
.mermaid-wrap {
  position: relative;
  background: var(--surface, #fff);
  border: 1px solid var(--rule, rgba(30,41,59,.1));
  border-radius: 14px;
  padding: 34px 22px;
  overflow: hidden;
  display: flex; justify-content: center; align-items: center;
  min-height: 420px;
  cursor: grab;
}
.mermaid-wrap--tall { min-height: min(80vh, 860px); }
.mermaid-wrap.is-panning { cursor: grabbing; user-select: none; }

.zoom-controls {
  position: absolute; top: 10px; right: 10px; z-index: 10;
  display: flex; gap: 2px;
  background: var(--surface, #fff); border: 1px solid var(--rule, rgba(30,41,59,.1));
  border-radius: 7px; padding: 2px;
}
.zoom-controls button {
  width: 28px; height: 28px; border: none; background: transparent;
  color: var(--ink-dim, #6b7280); font-family: var(--mono, monospace); font-size: 14px;
  cursor: pointer; border-radius: 5px;
  display: flex; align-items: center; justify-content: center;
}
.zoom-controls button:hover { background: var(--bg-soft, #f3eee4); color: var(--ink, #1f2a37); }

.canvas-legend {
  position: absolute; bottom: 10px; left: 12px; z-index: 10;
  display: flex; gap: 16px; flex-wrap: wrap;
  background: color-mix(in srgb, var(--surface, #fff) 88%, transparent);
  backdrop-filter: blur(3px);
  border: 1px solid var(--rule, rgba(30,41,59,.1)); border-radius: 8px; padding: 7px 12px;
  pointer-events: none;
}
.legend-item { display: flex; align-items: center; gap: 7px; font-family: var(--mono, monospace); font-size: 11px; }
.legend-swatch { width: 12px; height: 12px; border-radius: 3px; }

/* ---------- label rendering: fixes #2 ---------- */
.mermaid .nodeLabel { line-height: 1.35 !important; }
.mermaid .nodeLabel small { font-family: var(--mono, monospace); font-size: 10.5px; opacity: .75; font-weight: 400; }
.mermaid foreignObject { overflow: visible !important; }
.mermaid foreignObject > div { overflow: visible !important; white-space: nowrap !important; }

/* ---------- clickable nodes: hover affordance ---------- */
.mermaid a { cursor: pointer; }
.mermaid a rect, .mermaid a .nodeLabel { transition: all 0.15s ease; }
.mermaid a:hover rect { stroke-width: 3px !important; filter: brightness(1.12) drop-shadow(0 2px 8px rgba(194,65,12,.35)); }
.mermaid a:hover .nodeLabel { text-decoration: underline; text-underline-offset: 3px; }
.mermaid a:hover .nodeLabel::after { content: ' ↗'; font-size: 11px; }
```

## Pan/zoom/fullscreen JS (plain script tag, after the module script)

```html
<script>
  var DEFAULT_ZOOM = 1.15;
  function applyView(t) { t.style.zoom = t.dataset.zoom; t.style.transform = 'translate(' + (t.dataset.tx || 0) + 'px,' + (t.dataset.ty || 0) + 'px)'; }
  function initialZoomFor(w) { return parseFloat(w.dataset.initialZoom || DEFAULT_ZOOM); }
  function zoomDiagram(btn, f) {
    var w = btn.closest('.mermaid-wrap'), t = w.querySelector('.mermaid');
    t.dataset.zoom = Math.min(Math.max((parseFloat(t.dataset.zoom || initialZoomFor(w))) * f, 0.25), 10);
    applyView(t);
  }
  function resetZoom(btn) {
    var w = btn.closest('.mermaid-wrap'), t = w.querySelector('.mermaid');
    t.dataset.zoom = initialZoomFor(w); t.dataset.tx = 0; t.dataset.ty = 0; applyView(t);
  }
  function openDiagramFullscreen(btn) { openMermaidInNewTab(btn.closest('.mermaid-wrap')); }
  function openMermaidInNewTab(wrap) {
    var svg = wrap.querySelector('.mermaid svg'); if (!svg) return;
    var c = svg.cloneNode(true); c.style.zoom = ''; c.style.transform = '';
    var bg = getComputedStyle(document.documentElement).getPropertyValue('--paper').trim() || '#ffffff';
    var html = '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Diagram</title><style>' +
      'body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:' + bg + ';padding:40px;box-sizing:border-box}' +
      'svg{max-width:100%;max-height:92vh;height:auto}</style></head><body>' + c.outerHTML + '</body></html>';
    window.open(URL.createObjectURL(new Blob([html], { type: 'text/html' })), '_blank');
  }
  document.querySelectorAll('.mermaid-wrap').forEach(function(wrap) {
    var m = wrap.querySelector('.mermaid');
    if (m) { m.dataset.zoom = initialZoomFor(wrap); m.dataset.tx = 0; m.dataset.ty = 0; applyView(m); }
    wrap.addEventListener('wheel', function(e) {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      var t = wrap.querySelector('.mermaid');
      t.dataset.zoom = Math.min(Math.max((parseFloat(t.dataset.zoom || initialZoomFor(wrap))) * (e.deltaY < 0 ? 1.1 : 0.9), 0.25), 10);
      applyView(t);
    }, { passive: false });
    var sx, sy, stx, sty, st, didPan, onLink;
    wrap.addEventListener('mousedown', function(e) {
      if (e.target.closest('.zoom-controls')) return;
      onLink = !!e.target.closest('a');   // guard: node-link clicks must not ALSO open fullscreen
      var t = wrap.querySelector('.mermaid');
      wrap.classList.add('is-panning');
      sx = e.clientX; sy = e.clientY;
      stx = parseFloat(t.dataset.tx || 0); sty = parseFloat(t.dataset.ty || 0);
      st = Date.now(); didPan = false;
      e.preventDefault();
    });
    window.addEventListener('mousemove', function(e) {
      if (!wrap.classList.contains('is-panning')) return;
      var t = wrap.querySelector('.mermaid'), z = parseFloat(t.dataset.zoom || 1);
      var dx = e.clientX - sx, dy = e.clientY - sy;
      if (Math.abs(dx) > 5 || Math.abs(dy) > 5) didPan = true;
      t.dataset.tx = stx + dx / z; t.dataset.ty = sty + dy / z;
      applyView(t);
    });
    window.addEventListener('mouseup', function() {
      if (!wrap.classList.contains('is-panning')) return;
      wrap.classList.remove('is-panning');
      if (!didPan && !onLink && (Date.now() - st) < 300) openMermaidInNewTab(wrap);
    });
  });
</script>
```

## Per-diagram markup

```html
<div class="mermaid-wrap mermaid-wrap--tall" data-initial-zoom="0.85">
  <div class="zoom-controls">
    <button onclick="zoomDiagram(this, 1.2)" title="Zoom in">+</button>
    <button onclick="zoomDiagram(this, 0.8)" title="Zoom out">&minus;</button>
    <button onclick="resetZoom(this)" title="Reset zoom">&#8634;</button>
    <button onclick="openDiagramFullscreen(this)" title="Open full size">&#x26F6;</button>
  </div>
  <div class="canvas-legend">
    <div class="legend-item"><div class="legend-swatch" style="background:#c2410c"></div> payers</div>
    <!-- one item per role class -->
  </div>
  <pre class="mermaid">
flowchart LR
  ...
  </pre>
</div>
```

Always tell the reader how to use it, in the section note above the canvas:
**Drag to pan · ctrl/⌘-scroll to zoom · click a node to open its source.**

## Diagram authoring rules

- **≤14 nodes per diagram.** Split by concern instead of cramming: one diagram per question (e.g. "who pays → who fronts → where it lands" and a separate "money loop"). A diagram that needs scrolling at default zoom is two diagrams.
- **Evidence-labeled edges.** Edge labels carry the actual evidence figure, not vibes: `==>|"named payer — 99/100 ads"|`, `==>|"2,323 / 2,409 active ads link here"|`.
- **Totals in cluster titles**: `subgraph PAGES["  AD PAGES — 127,381+ ads  "]`.
- **Role color classes** via `classDef` (payers/corporate = terra, pages = navy, fabricated entities = red, domains = gold, consumers/products = sage) + matching legend.
- **Hyperlink every node that has a primary source**: `click NODE "https://..." _blank`. Requires `securityLevel: 'loose'`. Inside the `<pre>`, escape ampersands in URLs as `&amp;`. Citation discipline applies — node URLs come from the case ground-truth files, same as any other citation.
- **`data-initial-zoom`** per canvas: dense layered diagrams ~0.85, simple flows ~1.0–1.15.
- **`<small>` sublabels** for counts/metadata inside node labels: `NODE["Name<br/><small>50,001+ ads · capped</small>"]`.

## Layout choice: ELK vs dagre

- **ELK** (the global default above): best for layered LR network/attribution diagrams.
- **dagre per-diagram override**: when a diagram contains a **cycle** (e.g. money loop: corporate → ads → consumers → revenue → corporate) and must visually START from a specific node, ELK may break the cycle at the wrong edge and float the wrong node to the top. Override per diagram with frontmatter inside the `<pre>`:

```
---
config:
  layout: dagre
---
flowchart TD
  ...
```

dagre breaks cycles starting from the first-declared node — declare the intended root first, and draw the return edge dotted (`-.->`) so it reads as a return.

## Verification before shipping (mandatory smoke test)

```bash
# 1) no mermaid parse errors, diagrams actually rendered
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --dump-dom --virtual-time-budget=15000 "file://$PWD/report.html" 2>/dev/null | python3 -c "
import sys, re
dom = sys.stdin.read()
errs = len(re.findall(r'class=\"[^\"]*error-(icon|text)', dom))   # rendered error ELEMENTS (the .error-icon CSS rule alone is normal)
print('error elements:', errs)
print('svg diagrams:', dom.count('<svg'))
assert errs == 0 and dom.count('<svg') >= 1
"
# 2) screenshot and LOOK at it — check for clipped labels at default zoom
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --screenshot=/tmp/report-check.png --window-size=1500,2400 --virtual-time-budget=15000 "file://$PWD/report.html"
```

If any label renders clipped mid-character: fonts.ready isn't firing before `mermaid.run()`, or the foreignObject overflow CSS is missing — both are in this file. Token cost of re-checking is trivial against shipping a clipped diagram.
