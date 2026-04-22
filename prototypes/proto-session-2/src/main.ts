import "./styles.css";
import * as THREE from "three";

// ─── Hero 3D — signal from noise ─────────────────────────

function initHero(): (() => void) | void {
  const canvas = document.getElementById(
    "hero-canvas",
  ) as HTMLCanvasElement | null;
  if (!canvas) return;

  const isMobile = window.matchMedia("(max-width: 900px)").matches;
  const reduceMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;
  if (isMobile) {
    canvas.style.display = "none";
    return;
  }

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
  camera.position.z = 7;

  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0);

  const resize = () => {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (w === 0 || h === 0) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };

  const COUNT = 5000;
  const positions = new Float32Array(COUNT * 3);
  const seeds = new Float32Array(COUNT);
  const sizes = new Float32Array(COUNT);
  const phases = new Float32Array(COUNT);

  // Arrange on a torus knot with per-particle jitter
  for (let i = 0; i < COUNT; i++) {
    const t = i / COUNT;
    const p = 2;
    const q = 3;
    const R = 2.2;
    const r = 0.75;
    const phi = t * Math.PI * 2;
    const cq = Math.cos(q * phi);
    const x = (R + r * cq) * Math.cos(p * phi);
    const y = r * Math.sin(q * phi);
    const z = (R + r * cq) * Math.sin(p * phi);
    const jitter = 0.38;
    positions[i * 3 + 0] = x + (Math.random() - 0.5) * jitter;
    positions[i * 3 + 1] = y + (Math.random() - 0.5) * jitter;
    positions[i * 3 + 2] = z + (Math.random() - 0.5) * jitter;
    seeds[i] = Math.random();
    sizes[i] = 0.8 + Math.random() * 2.4;
    phases[i] = Math.random() * Math.PI * 2;
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geom.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
  geom.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));
  geom.setAttribute("aPhase", new THREE.BufferAttribute(phases, 1));

  const uniforms = {
    uTime: { value: 0 },
    uMouse: { value: new THREE.Vector2(0, 0) },
    uColorA: { value: new THREE.Color(0x5a8dff) },
    uColorB: { value: new THREE.Color(0xa89bff) },
  };

  const mat = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: /* glsl */ `
      uniform float uTime;
      uniform vec2 uMouse;
      attribute float aSeed;
      attribute float aSize;
      attribute float aPhase;
      varying float vMix;
      varying float vAlpha;

      void main() {
        vec3 pos = position;
        // Breathing displacement — signal emerging from noise
        float n = sin(uTime * 0.6 + aPhase) * 0.35 + sin(uTime * 0.3 + aPhase * 1.7) * 0.22;
        pos += normalize(pos + 0.0001) * n * 0.18;
        // Mouse parallax (per-particle depth)
        pos.xy += uMouse * 0.5 * aSeed;

        vec4 mv = modelViewMatrix * vec4(pos, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = aSize * (180.0 / -mv.z);
        vMix = aSeed;
        vAlpha = 0.55 + aSeed * 0.4;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 uColorA;
      uniform vec3 uColorB;
      varying float vMix;
      varying float vAlpha;

      void main() {
        vec2 uv = gl_PointCoord - 0.5;
        float d = length(uv);
        float a = smoothstep(0.5, 0.0, d);
        vec3 col = mix(uColorA, uColorB, vMix);
        gl_FragColor = vec4(col, a * vAlpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  const points = new THREE.Points(geom, mat);
  scene.add(points);

  const mouseTarget = new THREE.Vector2();
  const onMove = (e: PointerEvent) => {
    mouseTarget.x = (e.clientX / window.innerWidth) * 2 - 1;
    mouseTarget.y = -(e.clientY / window.innerHeight) * 2 + 1;
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("resize", resize);
  resize();

  const clock = new THREE.Clock();
  let running = true;

  const tick = () => {
    if (!running) return;
    uniforms.uTime.value = clock.getElapsedTime();
    uniforms.uMouse.value.lerp(mouseTarget, 0.04);
    if (!reduceMotion) {
      points.rotation.y += 0.0025;
      points.rotation.x = Math.sin(clock.getElapsedTime() * 0.1) * 0.15;
    }
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  };
  tick();

  return () => {
    running = false;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("resize", resize);
    geom.dispose();
    mat.dispose();
    renderer.dispose();
  };
}

// ─── Newsletter ────────────────────────────────────────────

function initNewsletter(): void {
  const form = document.getElementById("signup-form") as HTMLFormElement | null;
  const emailInput = document.getElementById(
    "signup-email",
  ) as HTMLInputElement | null;
  const submitBtn = document.getElementById(
    "signup-submit",
  ) as HTMLButtonElement | null;
  const errorEl = document.getElementById("signup-error");
  const successEl = document.getElementById("signup-success");
  if (!form || !emailInput || !submitBtn || !errorEl || !successEl) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = emailInput.value.trim();
    if (!email) return;
    submitBtn.disabled = true;
    const orig = submitBtn.textContent;
    submitBtn.textContent = "Subscribing…";
    errorEl.hidden = true;

    try {
      const res = await fetch("/api/newsletter/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, newsletters: ["buried_signals"] }),
      });
      if (res.ok) {
        form.hidden = true;
        successEl.hidden = false;
        return;
      }
      let msg = "Something went wrong. Please try again.";
      try {
        const d = (await res.json()) as { detail?: string };
        if (d?.detail) msg = d.detail;
      } catch {
        /* non-json body */
      }
      errorEl.textContent = msg;
      errorEl.hidden = false;
    } catch {
      errorEl.textContent = "Something went wrong. Please try again.";
      errorEl.hidden = false;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = orig;
    }
  });
}

// ─── Boot ──────────────────────────────────────────────────

const ready = (fn: () => void) => {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", fn, { once: true });
  } else {
    fn();
  }
};

ready(() => {
  initHero();
  initNewsletter();
});
