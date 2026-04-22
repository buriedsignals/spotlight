# Proto 04 · Scenography

Scenographic WebGL landing. One unified direction absorbing what worked from protos 1/2/3 — carried by a play of lights, forms, and lighting rather than diagrams, marginalia, or typographic contrast.

## Stack

- Bun (runtime)
- Vite (dev server + HMR)
- Three.js vanilla (no React)
- TypeScript

## Run

```bash
cd prototypes/proto-4-scenography
bun install
bun run dev
```

Vite opens `http://localhost:5173` automatically.

## Structure

```
proto-4-scenography/
├── index.html        # shell + HTML overlay for landing content
├── package.json
├── tsconfig.json
├── vite.config.ts
└── src/
    ├── main.ts       # entry: scroll wiring + overlay state
    ├── scene.ts      # Three.js: renderer, camera, lights, forms, timeline
    └── style.css     # layout + paper/ink typography
```

## What the prototype demonstrates

**Scenography**, not diagramming. Three coexisting statuses (shipped / won't do / coming) expressed by **material + light + form** — no labels, no boxes, no footnotes. The content (hero, what it does, integrations, stats) lives in HTML overlay above the canvas; the canvas carries the atmosphere.

### Scroll timeline (3 scenes → 340vh)

1. **Intake** (0 → ~0.33) — dark volume, a single central form lit from one direction. Camera close, contemplative.
2. **Exploration** (~0.33 → ~0.66) — verified satellites emerge in rich PBR material. Camera pulls back. Fill light grows.
3. **Anchoring** (~0.66 → 1) — impasse satellites (darker, smouldering) and fog satellites (translucent, distant) enter. Rim light rises. The composed field reads at a glance.

After the scenography container, the canvas fades and content enters normal HTML flow on a paper background: Attribution, Pro Membership (dashed border — "coming"), Consulting, closing CTA, footer.

## What to look at while reviewing

- Quality of **lighting** (Lusion benchmark): does the central form *feel* lit, not just shaded?
- Quality of **material differentiation**: do verified / impasse / fog forms read as distinct statuses without text?
- **Scroll-to-scene** rhythm: does each overlay section land with its scenographic moment?
- **Transition** into post-scenography paper content: is the handoff clean?
- Performance: smooth 60fps on a mid-range laptop?

## What's deliberately minimal for v1

- No post-processing (bloom, DOF, chromatic). Lighting + fog + materials do the work. We add post only if the base isn't expressive enough.
- No custom shaders. MeshPhysicalMaterial + MeshStandardMaterial + MeshBasicMaterial. Shaders join later if a specific effect demands it.
- No audio. No gesture interactions beyond scroll. Focus on the visual direction first.

## Knobs to tweak during review

Most scenographic parameters live in `src/scene.ts`:

- **Light intensities / colors** → `setupLights()`
- **Form materials** → `setupForms()` (verifiedMat, impasseMat, fogMat)
- **Camera trajectory** → `tick()` camera lerp block
- **Fog density** → `tick()` fog.density lerp
- **Satellite scale windows** → `scaleRange` per satellite factory
