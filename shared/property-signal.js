// Radar carried from bureau/slides/classes/indicator-workshop/closing-signal.js.
// The drawing and detection loop are retained; a custom element owns website lifecycle.
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const VW = 400, VH = 200, OX = 200, OY = 200, AMBER = '#D4A853';
  const DOT_COUNT = 80, DOT_MIN_DIST = 15, CURSOR_RADIUS = 60;
  const WAVE_INTERVAL = 1800, WAVE_SPEED = 0.1, WAVE_MAX_R = 450, WAVE_BASE_OP = 0.15;
  const DET_CHANCE = 0.7, DET_DURATION = 1000, DET_MIN_DIST = 35, DET_MAX_DIST = 180;
  const PING_DURATION = 900, LINE_DURATION = 1400;
  // The deck draws the field about twice as large as the site does; thin the strokes to match.
  const K = 0.5;

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  function build(host) {
    const root = host.shadowRoot || host.attachShadow({ mode: 'open' });
    root.replaceChildren();
    const style = document.createElement('style');
    style.textContent = `
      svg { display:block; width:100%; height:100%; overflow:visible; }
      .scan-field__dots { opacity:1; }
      .scan-emitter > * { opacity:0; transform-origin:200px 200px; transform-box:view-box;
        animation:emitter-in .5s cubic-bezier(.34,1.56,.64,1) forwards; }
      .scan-emitter__glow { animation-delay:.05s; }
      .scan-emitter__ring { animation-delay:.22s; }
      .scan-emitter__core-hot { animation-delay:.3s; }
      .scan-emitter .scan-emitter__core {
        animation:emitter-in .5s cubic-bezier(.34,1.56,.64,1) .3s forwards,
          emitter-pulse 1800ms ease-out .8s infinite; }
      .paused * { animation-play-state:paused; }
      @keyframes emitter-in { from { opacity:0; transform:scale(.2); } to { opacity:1; transform:scale(1); } }
      @keyframes emitter-pulse { 0% { opacity:1; transform:scale(1.3); } 35%,100% { opacity:1; transform:scale(1); } }
      @media (prefers-reduced-motion:reduce) { .scan-emitter > * { opacity:1; animation:none; } }
    `;
    root.appendChild(style);
    const svg = el('svg', { viewBox: `0 0 ${VW} ${VH}`, fill: 'none' }, root);
    svg.classList.add('paused');
    const defs = el('defs', {}, svg);
    const lg = el('linearGradient', { id: 'cs-fade', gradientUnits: 'userSpaceOnUse', x1: 0, y1: OY, x2: 0, y2: OY + 250 }, defs);
    el('stop', { offset: '0%', 'stop-color': 'white' }, lg);
    el('stop', { offset: '100%', 'stop-color': 'white', 'stop-opacity': '0.1' }, lg);
    const rg = el('radialGradient', { id: 'cs-glow' }, defs);
    el('stop', { offset: '0%', 'stop-color': AMBER, 'stop-opacity': '0.32' }, rg);
    el('stop', { offset: '55%', 'stop-color': AMBER, 'stop-opacity': '0.08' }, rg);
    el('stop', { offset: '100%', 'stop-color': AMBER, 'stop-opacity': '0' }, rg);
    const mask = el('mask', { id: 'cs-mask' }, defs);
    el('rect', { x: -500, y: -500, width: 1400, height: 1500, fill: 'url(#cs-fade)' }, mask);

    const emitter = el('g', { class: 'scan-emitter' }, svg);
    el('circle', { cx: OX, cy: OY, r: 16, fill: 'url(#cs-glow)', class: 'scan-emitter__glow' }, emitter);
    el('circle', { cx: OX, cy: OY, r: 6.5, stroke: AMBER, 'stroke-width': 0.8, opacity: 0.55, class: 'scan-emitter__ring' }, emitter);
    el('circle', { cx: OX, cy: OY, r: 2.8, fill: AMBER, class: 'scan-emitter__core' }, emitter);
    el('circle', { cx: OX, cy: OY, r: 1.1, fill: '#FFF6E0', class: 'scan-emitter__core-hot' }, emitter);

    const waveG = el('g', { mask: 'url(#cs-mask)' }, svg);
    const sigG = el('g', {}, svg);
    const dotG = el('g', { class: 'scan-field__dots' }, svg);

    const dots = [];
    let tries = 0;
    while (dots.length < DOT_COUNT && tries < 800) {
      tries++;
      const x = -150 + Math.random() * 700, y = -250 + Math.random() * 430;
      const dist = Math.hypot(x - OX, y - OY);
      if (dist < 30 || dots.some(p => Math.hypot(x - p.x, y - p.y) < DOT_MIN_DIST)) continue;
      const node = el('circle', { cx: x, cy: y, r: 1.5, fill: 'currentColor', opacity: 0.03 }, dotG);
      dots.push({ x, y, dist, op: 0.03, r: 1.5, amberUntil: 0, node });
    }
    const reachable = dots.map((d, i) => [d, i]).filter(([d]) => d.dist >= DET_MIN_DIST && d.dist <= DET_MAX_DIST).map(([, i]) => i);
    return { svg, waveG, sigG, dotG, dots, reachable, waves: [], pings: [], lastSpawn: 0 };
  }

  function spawn(s, t) {
    const hot = s.reachable.length > 0 && Math.random() < DET_CHANCE;
    const node = el('circle', { cx: OX, cy: OY, r: 15, fill: 'none', stroke: 'currentColor' }, s.waveG);
    s.waves.push({ birth: t, r: 15, hot, target: hot ? s.reachable[Math.floor(Math.random() * s.reachable.length)] : -1, fired: false, amberUntil: 0, node });
  }

  function mount(host) {
    let state = build(host), raf = null, active = false, mouse = { x: -9999, y: -9999 };
    const onMouse = e => { mouse.x = e.clientX; mouse.y = e.clientY; };
    document.addEventListener('mousemove', onMouse);

    function tick(t) {
      if (!active || !state) { raf = null; return; }
      const s = state;
      if (t - s.lastSpawn > WAVE_INTERVAL) { spawn(s, t); s.lastSpawn = t; }
      const life = (WAVE_MAX_R - 15) / WAVE_SPEED;
      for (let i = s.waves.length - 1; i >= 0; i--) {
        const w = s.waves[i];
        const p = (t - w.birth) / life;
        if (p >= 1) { w.node.remove(); s.waves.splice(i, 1); continue; }
        const eased = 1 - (1 - p) * (1 - p);
        w.r = 15 + (WAVE_MAX_R - 15) * eased;
        let op = WAVE_BASE_OP * Math.pow(1 - p, 1.4), sw = 0.5 + 1.1 * (1 - p), col = 'currentColor';
        if (w.hot && !w.fired && w.target >= 0 && w.r >= s.dots[w.target].dist - 6) {
          w.fired = true; w.amberUntil = t + DET_DURATION;
          const d = s.dots[w.target]; d.amberUntil = t + DET_DURATION;
          const line = el('line', { x1: d.x, y1: d.y, x2: OX, y2: OY, stroke: AMBER, 'stroke-width': 0.6 * K, 'stroke-dasharray': d.dist }, s.sigG);
          const ring = el('circle', { cx: d.x, cy: d.y, r: 2, stroke: AMBER, 'stroke-width': 0.8 * K, fill: 'none' }, s.sigG);
          s.pings.push({ birth: t, len: d.dist, line, ring });
        }
        if (t < w.amberUntil) {
          const rem = (w.amberUntil - t) / DET_DURATION;
          col = AMBER; op = Math.max(op, 0.45 * rem); sw = 1.2 + rem;
        }
        w.node.setAttribute('r', w.r); w.node.setAttribute('opacity', op);
        w.node.setAttribute('stroke-width', sw * K); w.node.setAttribute('stroke', col);
      }
      for (let i = s.pings.length - 1; i >= 0; i--) {
        const p = s.pings[i], age = t - p.birth;
        if (age > LINE_DURATION) { p.line.remove(); p.ring.remove(); s.pings.splice(i, 1); continue; }
        const pp = Math.min(1, age / PING_DURATION), lp = age / LINE_DURATION;
        p.ring.setAttribute('r', 2 + 14 * (1 - (1 - pp) * (1 - pp)));
        p.ring.setAttribute('opacity', 0.7 * (1 - pp));
        p.line.setAttribute('stroke-dashoffset', p.len * Math.max(0, 1 - lp * 3));
        p.line.setAttribute('opacity', 0.35 * (1 - lp));
      }
      let m = { x: -9999, y: -9999 };
      try { const c = s.svg.getScreenCTM(); if (c) m = new DOMPoint(mouse.x, mouse.y).matrixTransform(c.inverse()); } catch (e) { /* off screen */ }
      for (const d of s.dots) {
        const dist = Math.hypot(d.x - m.x, d.y - m.y);
        let target = dist < CURSOR_RADIUS ? 0.03 + (1 - dist / CURSOR_RADIUS) * 0.32 : 0.03, targetR = 1.5;
        if (t < d.amberUntil) {
          const rem = (d.amberUntil - t) / DET_DURATION;
          target = Math.max(target, 0.9 * rem); targetR = 1.5 + 2 * rem;
          d.node.setAttribute('fill', AMBER);
        } else d.node.setAttribute('fill', 'currentColor');
        d.op += (target - d.op) * 0.1; d.r += (targetR - d.r) * 0.15;
        d.node.setAttribute('opacity', d.op); d.node.setAttribute('r', d.r);
      }
      raf = requestAnimationFrame(tick);
    }


    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reducedMotion) {
      for (const radius of [60, 120, 180, 240]) {
        el('circle', { cx: OX, cy: OY, r: radius, fill: 'none', stroke: 'currentColor', 'stroke-width': K, opacity: 0.12 }, state.waveG);
      }
      for (const dot of state.dots) dot.node.setAttribute('opacity', 0.12);
      document.removeEventListener('mousemove', onMouse);
      return () => { state = null; };
    }
    let pausedAt = performance.now();
    spawn(state, pausedAt); spawn(state, pausedAt - 600); spawn(state, pausedAt - 1200);
    state.lastSpawn = pausedAt;
    let onScreen = false;
    function updateVisibility() {
      const next = onScreen && !document.hidden;
      if (next === active || !state) return;
      const now = performance.now();
      active = next;
      state.svg.classList.toggle('paused', !active);
      if (!active) {
        pausedAt = now;
        if (raf) cancelAnimationFrame(raf);
        raf = null;
        return;
      }
      const gap = now - pausedAt;
      for (const w of state.waves) { w.birth += gap; w.amberUntil += gap; }
      for (const p of state.pings) p.birth += gap;
      for (const d of state.dots) d.amberUntil += gap;
      state.lastSpawn += gap;
      raf = requestAnimationFrame(tick);
    }
    const observer = new IntersectionObserver(([entry]) => {
      onScreen = entry.isIntersecting;
      updateVisibility();
    });
    observer.observe(host.closest('#page-3d') ? host : host.closest('.bs-property-signal') || host);
    document.addEventListener('visibilitychange', updateVisibility);
    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', updateVisibility);
      document.removeEventListener('mousemove', onMouse);
      if (raf) cancelAnimationFrame(raf);
      active = false;
      state = null;
    };
  }

  function reveal(host) {
    const band = host.closest('.bs-property-signal');
    const content = band?.querySelector('.bs-property-signal__content');
    if (!content) return () => {};
    // Mycroft's transformed page is its own fixed-position containing block.
    const fixed = !host.closest('#page-3d');
    if (!fixed && matchMedia('(prefers-reduced-motion: reduce)').matches) return () => {};
    if (fixed) band.classList.add('bs-property-signal--fixed');
    let frame = null;
    let lastOffset;
    function update() {
      frame = null;
      const radius = parseFloat(getComputedStyle(band).paddingTop);
      const top = band.getBoundingClientRect().top + radius;
      const height = content.offsetHeight;
      const offset = fixed
        ? Math.max(0, Math.min(height + radius, top - innerHeight + height))
        : Math.max(-height, Math.min(0, innerHeight - height - top));
      if (offset !== lastOffset) {
        band.style.setProperty(fixed ? '--bs-reveal-cut' : '--bs-reveal-offset', offset + 'px');
        lastOffset = offset;
      }
    }
    function schedule() { if (frame === null) frame = requestAnimationFrame(update); }
    function onFocus(event) {
      if (event.target.matches(':focus-visible')) band.scrollIntoView({ block: 'end', behavior: 'instant' });
    }
    update();
    addEventListener('scroll', schedule, { passive: true });
    addEventListener('resize', schedule);
    band.addEventListener('focusin', onFocus);
    return () => {
      removeEventListener('scroll', schedule);
      removeEventListener('resize', schedule);
      band.removeEventListener('focusin', onFocus);
      if (frame !== null) cancelAnimationFrame(frame);
      band.style.removeProperty('--bs-reveal-offset');
      band.style.removeProperty('--bs-reveal-cut');
      band.classList.remove('bs-property-signal--fixed');
    };
  }

  if (!customElements.get('bs-property-radar')) {
    customElements.define('bs-property-radar', class extends HTMLElement {
      connectedCallback() { this.cleanup = mount(this); this.cleanupReveal = reveal(this); }
      disconnectedCallback() { this.cleanup?.(); this.cleanupReveal?.(); }
    });
  }
})();
