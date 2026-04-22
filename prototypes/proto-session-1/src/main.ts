import "./style.css";
import { Scenography } from "./scene";

const canvas = document.getElementById("stage") as HTMLCanvasElement;
const scenographyEl = document.getElementById("scenography") as HTMLElement;
const acts = Array.from(document.querySelectorAll<HTMLElement>(".act"));
const scrubberFill = document.getElementById("scrubber-fill") as HTMLElement;
const scrubberLabel = document.getElementById("scrubber-label") as HTMLElement;
const stage = document.getElementById("stage") as HTMLElement;
const topHeader = document.querySelector<HTMLElement>("header.top")!;

const scene = new Scenography(canvas);
scene.mount();

const actLabels: Record<string, string> = {
  "1": "Plan 01 · Sous la surface — les traces",
  "2": "Plan 02 · Les filaments se propagent",
  "3": "Plan 03 · Le terrain se forme",
  "4": "Plan 04 · La source s'agrège",
  "5": "Plan 05 · Vu d'en haut — le territoire",
};

function computeProgress(): number {
  const rect = scenographyEl.getBoundingClientRect();
  const scrollableHeight = rect.height - window.innerHeight;
  if (scrollableHeight <= 0) return 0;
  const raw = -rect.top / scrollableHeight;
  return Math.min(1, Math.max(0, raw));
}

/** Five equal segments: 0-0.2 / 0.2-0.4 / 0.4-0.6 / 0.6-0.8 / 0.8-1 */
function activeActIndex(p: number): number {
  if (p >= 0.8) return 4;
  if (p >= 0.6) return 3;
  if (p >= 0.4) return 2;
  if (p >= 0.2) return 1;
  return 0;
}

function update() {
  const p = computeProgress();
  scene.setProgress(p);

  const idx = activeActIndex(p);
  acts.forEach((el, i) => el.classList.toggle("active", i === idx));

  const act = acts[idx];
  const key = act?.dataset.act ?? "1";
  scrubberFill.style.width = `${(p * 100).toFixed(1)}%`;
  scrubberLabel.textContent = actLabels[key] ?? "Plan";

  const sgRect = scenographyEl.getBoundingClientRect();
  const pastScenography = sgRect.bottom < window.innerHeight * 0.55;
  stage.classList.toggle("faded", pastScenography);
  topHeader.classList.toggle("on-paper", pastScenography);
  document.body.classList.toggle("on-paper", pastScenography);
}

window.addEventListener("scroll", update, { passive: true });
window.addEventListener("resize", () => {
  scene.resize();
  update();
});

// Mouse tracking — normalized 0..1 position drives scenographic interactivity.
let mouseX = 0.5;
let mouseY = 0.5;
let mouseActive = false;
let lastMoveT = 0;
window.addEventListener("mousemove", (e) => {
  mouseX = e.clientX / window.innerWidth;
  mouseY = e.clientY / window.innerHeight;
  mouseActive = true;
  lastMoveT = performance.now();
  scene.setMouse(mouseX, mouseY, true);
});
window.addEventListener("mouseleave", () => {
  mouseActive = false;
  scene.setMouse(mouseX, mouseY, false);
});

// Fade mouse influence after a period of stillness (keeps last-known position)
setInterval(() => {
  if (!mouseActive) return;
  if (performance.now() - lastMoveT > 1500) {
    scene.setMouse(mouseX, mouseY, false);
    mouseActive = false;
  }
}, 400);

update();

// Newsletter signup stub
const form = document.getElementById("signup-form") as HTMLFormElement | null;
if (form) {
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const btn = document.getElementById("signup-submit") as HTMLButtonElement;
    const orig = btn.textContent ?? "Subscribe";
    btn.disabled = true;
    btn.textContent = "Demo — not wired";
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = orig;
    }, 1600);
  });
}
