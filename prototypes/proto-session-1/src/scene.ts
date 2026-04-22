import * as THREE from "three";

// =============================================================================
// Helpers
// =============================================================================
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const clamp = (v: number, min: number, max: number) =>
  Math.min(max, Math.max(min, v));
const easeInOutCubic = (t: number) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

function plateau(
  p: number,
  a: number,
  b: number,
  c: number,
  d: number,
): number {
  if (p <= a) return 0;
  if (p >= d) return 0;
  if (p < b) return easeOutCubic((p - a) / (b - a));
  if (p > c) return 1 - easeOutCubic((p - c) / (d - c));
  return 1;
}

// Footprints — plural traces (multiple journalists / cases have walked here)
const FOOTPRINTS: { x: number; z: number; rot: number; size: number }[] = [
  { x: -3.6, z: 2.3, rot: 0.4, size: 1.0 },
  { x: -3.1, z: 1.2, rot: 0.5, size: 0.95 },
  { x: 3.0, z: -1.7, rot: -0.8, size: 1.0 },
  { x: 2.4, z: -2.8, rot: -0.75, size: 0.95 },
  { x: 1.3, z: 3.2, rot: 1.2, size: 0.9 },
  { x: 0.5, z: 4.1, rot: 1.25, size: 0.9 },
  { x: -1.8, z: -3.2, rot: -2.1, size: 1.0 },
  { x: -2.4, z: -4.0, rot: -2.0, size: 0.95 },
];

const CROSSHAIRS: { x: number; z: number; size: number }[] = [
  { x: 0.0, z: 0.0, size: 1.15 },
  { x: -4.8, z: -1.2, size: 0.85 },
  { x: 4.5, z: 2.8, size: 0.95 },
  { x: -2.2, z: 4.5, size: 0.7 },
  { x: 3.6, z: -4.1, size: 0.8 },
];

// =============================================================================
// GLSL — noise helpers
// =============================================================================
const NOISE_GLSL = /* glsl */ `
  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
  }
  float vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash(i);
    float b = hash(i + vec2(1.0, 0.0));
    float c = hash(i + vec2(0.0, 1.0));
    float d = hash(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
  }
  float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 5; i++) {
      v += a * vnoise(p);
      p *= 2.0;
      a *= 0.5;
    }
    return v;
  }
`;

// =============================================================================
// GROUND — vertex displacement + dual view (below/above) fragment blend
// =============================================================================
const GROUND_VERT = /* glsl */ `
  uniform float uTime;
  uniform float uDisplace;

  varying vec2 vPlanePos;
  varying float vElev;

  ${NOISE_GLSL}

  void main() {
    vec3 pos = position;
    // The plane lives in the XY plane (its normal is +Z in local space).
    // Its 2D coordinate is simply position.xy — stable regardless of the mesh
    // rotation applied by the scroll timeline.
    vec2 planePos = pos.xy;
    float e = fbm(planePos * 0.22 + vec2(uTime * 0.005, 0.0)) * 3.8;
    e += fbm(planePos * 0.8) * 0.85;
    float centerDamp = smoothstep(0.5, 5.0, length(planePos));
    e *= centerDamp;
    vElev = e;
    // Displace along the plane's local normal (+Z). After mesh rotation -π/2
    // around X, this becomes +Y in world space — i.e. terrain relief.
    pos.z += e * uDisplace;
    vPlanePos = planePos;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
  }
`;

const GROUND_FRAG = /* glsl */ `
  uniform float uTime;
  uniform float uProgress;
  uniform float uUpFactor;
  uniform vec2  uMouse;
  uniform float uMouseActive;

  varying vec2 vPlanePos;
  varying float vElev;

  ${NOISE_GLSL}

  // Regular dot grid — the "halftone" / coordinate sampling.
  float halftone(vec2 uv, float scale) {
    vec2 grid = uv * scale;
    vec2 cell = fract(grid) - 0.5;
    float d = length(cell);
    float size = 0.22 + 0.03 * vnoise(floor(grid) * 0.5);
    return 1.0 - smoothstep(size, size + 0.06, d);
  }

  // Shoe-print shape: sole ellipse + toe cluster offset.
  float footprintShape(vec2 pos, vec2 centre, float rot, float size) {
    vec2 local = (pos - centre) / size;
    float cR = cos(rot);
    float sR = sin(rot);
    vec2 rp = vec2(local.x * cR - local.y * sR, local.x * sR + local.y * cR);
    float sole = 1.0 - smoothstep(0.45, 0.55, length(rp * vec2(1.6, 0.95)));
    vec2 toePos = rp - vec2(0.0, -0.55);
    float toe = 1.0 - smoothstep(0.22, 0.30, length(toePos * vec2(1.4, 1.0)));
    return max(sole, toe * 0.95);
  }

  // Hardcoded pluralized footprints — 8 positions, rotations, sizes.
  // Inlined rather than passed as uniform arrays to avoid any GLSL portability surprises.
  float allFootprints(vec2 pos) {
    float fp = 0.0;
    fp = max(fp, footprintShape(pos, vec2(-3.6,  2.3),  0.40, 1.00));
    fp = max(fp, footprintShape(pos, vec2(-3.1,  1.2),  0.50, 0.95));
    fp = max(fp, footprintShape(pos, vec2( 3.0, -1.7), -0.80, 1.00));
    fp = max(fp, footprintShape(pos, vec2( 2.4, -2.8), -0.75, 0.95));
    fp = max(fp, footprintShape(pos, vec2( 1.3,  3.2),  1.20, 0.90));
    fp = max(fp, footprintShape(pos, vec2( 0.5,  4.1),  1.25, 0.90));
    fp = max(fp, footprintShape(pos, vec2(-1.8, -3.2), -2.10, 1.00));
    fp = max(fp, footprintShape(pos, vec2(-2.4, -4.0), -2.00, 0.95));
    return fp;
  }

  // Orange plus marker — a horizontal + vertical soft cross.
  float crosshairMark(vec2 pos, vec2 centre, float size) {
    vec2 local = pos - centre;
    float thick = 0.025 * size;
    float reach = 0.55 * size;
    float h = (1.0 - smoothstep(thick, thick * 1.6, abs(local.y))) *
              (1.0 - smoothstep(reach, reach + 0.03, abs(local.x)));
    float v = (1.0 - smoothstep(thick, thick * 1.6, abs(local.x))) *
              (1.0 - smoothstep(reach, reach + 0.03, abs(local.y)));
    float glow = (1.0 - smoothstep(0.05 * size, 0.18 * size, length(local))) * 0.35;
    return max(max(h, v), glow);
  }

  // Hardcoded crosshair markers at coordinate intersections.
  float allCrosshairs(vec2 pos) {
    float c = 0.0;
    c = max(c, crosshairMark(pos, vec2( 0.0,  0.0), 1.15));
    c = max(c, crosshairMark(pos, vec2(-4.8, -1.2), 0.85));
    c = max(c, crosshairMark(pos, vec2( 4.5,  2.8), 0.95));
    c = max(c, crosshairMark(pos, vec2(-2.2,  4.5), 0.70));
    c = max(c, crosshairMark(pos, vec2( 3.6, -4.1), 0.80));
    return c;
  }

  // Filament propagation (above view): radial stripes with travelling pulse.
  float filaments(vec2 pos) {
    float d = length(pos);
    float theta = atan(pos.y, pos.x);

    float growthP = smoothstep(0.14, 0.58, uProgress);
    float growthFront = growthP * 22.0;
    float mouseInfluence = smoothstep(3.0, 0.0, length(pos - uMouse)) * uMouseActive;
    growthFront += mouseInfluence * 4.0;
    float covered = smoothstep(growthFront + 0.7, growthFront - 0.3, d);

    float stripeCount = 36.0;
    float stripeId = floor(theta * stripeCount / 6.2831853);
    float within = fract(theta * stripeCount / 6.2831853);
    float act = step(0.42, hash(vec2(stripeId, 2.1)));
    float sOff = hash(vec2(stripeId, 7.3)) * 0.4;
    float wobble = (vnoise(vec2(d * 0.22, stripeId)) - 0.5) * 0.25;
    float lineDist = abs(within - 0.5 - sOff + wobble);
    float prim = smoothstep(0.032, 0.0, lineDist) * act;
    float dashes = smoothstep(0.12, 0.0, abs(fract(d * 0.35 + hash(vec2(stripeId, 11.0)) * 5.0) - 0.45));
    prim *= mix(0.55, 1.0, dashes);

    float flowPhase = d * 0.85 - uTime * 4.0 + hash(vec2(stripeId, 0.3)) * 6.28;
    float pulse = smoothstep(-0.2, 0.2, sin(flowPhase)) * 0.5 + 0.5;

    return prim * covered * (0.55 + 0.7 * pulse);
  }

  // Impasse mask — some stripes are impasses (shorter, rust tint).
  float impasseMask(vec2 pos) {
    float theta = atan(pos.y, pos.x);
    float stripeId = floor(theta * 36.0 / 6.2831853);
    float act = step(0.42, hash(vec2(stripeId, 2.1)));
    float isImp = step(0.78, hash(vec2(stripeId, 41.0)));
    float end = 6.0 + hash(vec2(stripeId, 31.0)) * 5.0;
    float cut = smoothstep(end + 0.3, end - 0.3, length(pos));
    return act * isImp * cut;
  }

  void main() {
    vec2 pos = vPlanePos;
    float dots = halftone(pos, 2.4);

    // ---------- BELOW VIEW (uUpFactor = 0) ----------
    vec3 belowBg  = vec3(0.42, 0.48, 0.56);
    vec3 belowDot = vec3(0.16, 0.20, 0.26);
    vec3 below = mix(belowBg, belowDot, dots);

    float fp = allFootprints(pos);
    vec3 fpColor = vec3(0.05, 0.06, 0.08);
    below = mix(below, fpColor, fp * 0.78);
    below = mix(below, fpColor * 0.7, fp * dots * 0.6);

    // ---------- ABOVE VIEW (uUpFactor = 1) ----------
    vec3 aboveBase = vec3(0.018, 0.022, 0.028);
    float fil = filaments(pos);
    float imp = impasseMask(pos);
    vec3 amber = vec3(1.00, 0.74, 0.42);
    vec3 rust  = vec3(0.72, 0.20, 0.09);
    vec3 emission = mix(amber, rust, imp * 0.85);
    vec3 above = aboveBase + emission * fil * 1.8;
    above = mix(above, above * 0.55, dots * 0.22);

    float d = length(pos);
    float growthP = smoothstep(0.14, 0.58, uProgress);
    float growthFront = growthP * 22.0;
    float front = exp(-pow(d - growthFront, 2.0) * 2.5) * growthP * 0.32;
    above += amber * front;

    vec3 color = mix(below, above, uUpFactor);

    // ---------- CROSSHAIRS (both views) ----------
    float ch = allCrosshairs(pos);
    vec3 crossColBelow = vec3(1.0, 0.42, 0.14);
    vec3 crossColAbove = vec3(1.0, 0.56, 0.22);
    vec3 crossCol = mix(crossColBelow, crossColAbove, uUpFactor);
    color = mix(color, crossCol, ch * 0.95);

    gl_FragColor = vec4(color, 1.0);
  }
`;

// =============================================================================
// STREAMS — columnar amber particles rising
// =============================================================================
const STREAM_VERT = /* glsl */ `
  attribute float aLife;
  attribute float aColumn;
  uniform float uPixelRatio;
  uniform float uTime;
  varying float vLife;
  void main() {
    vLife = aLife;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    float sizeBase = 3.0 + sin(uTime * 0.6 + aColumn * 1.7) * 0.5;
    gl_PointSize = sizeBase * uPixelRatio * (200.0 / max(-mv.z, 0.1));
    gl_Position = projectionMatrix * mv;
  }
`;

const STREAM_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uIntensity;
  varying float vLife;
  void main() {
    vec2 c = gl_PointCoord - vec2(0.5);
    float vertical = smoothstep(0.06, 0.0, abs(c.x));
    float tail = smoothstep(0.5, 0.0, length(c));
    float alpha = vertical * tail * (0.45 + 0.55 * sin(vLife * 3.1415));
    gl_FragColor = vec4(uColor * uIntensity, alpha * uIntensity);
  }
`;

// =============================================================================
// SOURCE — luminous particle cluster
// =============================================================================
const SOURCE_VERT = /* glsl */ `
  attribute float aSize;
  attribute float aOffset;
  uniform float uPixelRatio;
  uniform float uIntensity;
  uniform float uTime;
  varying float vGlow;
  void main() {
    vec3 d = vec3(
      sin(uTime * 0.4 + aOffset * 3.0) * 0.04,
      cos(uTime * 0.5 + aOffset * 2.1) * 0.04,
      sin(uTime * 0.3 + aOffset * 4.2) * 0.04
    );
    vec4 mv = modelViewMatrix * vec4(position + d, 1.0);
    float sizeBase = aSize * (0.6 + 0.4 * sin(uTime * 0.7 + aOffset * 5.0));
    gl_PointSize = sizeBase * uPixelRatio * (260.0 / max(-mv.z, 0.1)) * uIntensity;
    vGlow = 0.7 + 0.3 * sin(uTime * 1.2 + aOffset * 7.3);
    gl_Position = projectionMatrix * mv;
  }
`;

const SOURCE_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uIntensity;
  varying float vGlow;
  void main() {
    vec2 c = gl_PointCoord - vec2(0.5);
    float dist = length(c);
    if (dist > 0.5) discard;
    float soft = smoothstep(0.5, 0.0, dist);
    float core = smoothstep(0.2, 0.0, dist);
    float alpha = (soft * 0.6 + core * 0.4) * vGlow * uIntensity;
    gl_FragColor = vec4(uColor * (0.8 + 0.5 * core), alpha);
  }
`;

// =============================================================================
// Scenography
// =============================================================================
export class Scenography {
  private canvas: HTMLCanvasElement;
  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;

  private progress = 0;
  private mouseNorm = new THREE.Vector2(0.5, 0.5);
  private mouseWorld = new THREE.Vector3();
  private mouseActive = 0;
  private time = 0;
  private lastT = 0;
  private rafId: number | null = null;

  private ambient!: THREE.AmbientLight;
  private sourceLight!: THREE.PointLight;
  private sceneFog!: THREE.FogExp2;

  private ground!: THREE.Mesh;
  private groundMat!: THREE.ShaderMaterial;

  private streams!: THREE.Points;
  private streamMat!: THREE.ShaderMaterial;
  private streamGeom!: THREE.BufferGeometry;
  private streamPositions!: Float32Array;
  private streamVelocities!: Float32Array;
  private streamLives!: Float32Array;
  private streamColumns!: Float32Array;
  private columnXZ: { x: number; z: number }[] = [];
  private readonly COLUMN_COUNT = 60;
  private readonly PARTICLES_PER_COLUMN = 20;

  private source!: THREE.Points;
  private sourceMat!: THREE.ShaderMaterial;
  private sourceGeom!: THREE.BufferGeometry;
  private sourcePositions!: Float32Array;
  private sourceSizes!: Float32Array;
  private sourceOffsets!: Float32Array;
  private readonly SOURCE_COUNT = 480;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;

    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(window.innerWidth, window.innerHeight, false);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x07090d);
    this.sceneFog = new THREE.FogExp2(0x0b0f16, 0.04);
    this.scene.fog = this.sceneFog;

    this.camera = new THREE.PerspectiveCamera(
      42,
      window.innerWidth / window.innerHeight,
      0.1,
      120,
    );

    this.setupLights();
    this.buildGround();
    this.buildStreams();
    this.buildSource();

    // Diagnostic
    console.log(
      "[Scenography] renderer WebGL2?",
      this.renderer.capabilities.isWebGL2,
    );
    console.log(
      "[Scenography] canvas size:",
      this.renderer.domElement.width,
      "x",
      this.renderer.domElement.height,
    );
    console.log("[Scenography] scene children:", this.scene.children.length);
    console.log(
      "[Scenography] ground material?",
      this.groundMat !== undefined,
      "program compiled?",
      this.groundMat,
    );
    console.log(
      "[Scenography] camera:",
      this.camera.position.toArray(),
      "fov:",
      this.camera.fov,
    );
  }

  private setupLights() {
    this.ambient = new THREE.AmbientLight(0x7d879b, 0.22);
    this.scene.add(this.ambient);

    this.sourceLight = new THREE.PointLight(0xffc890, 0.0, 24, 1.6);
    this.sourceLight.position.set(0, 6.5, 0);
    this.scene.add(this.sourceLight);
  }

  // ----- GROUND -------------------------------------------------------------
  private buildGround() {
    // Plane geometry stays in XY (normal +Z). Mesh rotation is applied
    // per-frame via this.ground.rotation.x during the P1→P2 transition.
    const geo = new THREE.PlaneGeometry(50, 50, 180, 180);

    this.groundMat = new THREE.ShaderMaterial({
      vertexShader: GROUND_VERT,
      fragmentShader: GROUND_FRAG,
      uniforms: {
        uTime: { value: 0 },
        uProgress: { value: 0 },
        uUpFactor: { value: 0 },
        uDisplace: { value: 0 },
        uMouse: { value: new THREE.Vector2(0, 0) },
        uMouseActive: { value: 0 },
      },
      side: THREE.DoubleSide,
      fog: false,
    });

    this.ground = new THREE.Mesh(geo, this.groundMat);
    this.scene.add(this.ground);
  }

  // ----- STREAMS ------------------------------------------------------------
  private buildStreams() {
    for (let i = 0; i < this.COLUMN_COUNT; i++) {
      const theta = Math.random() * Math.PI * 2;
      const r = 1.5 + Math.sqrt(Math.random()) * 14.0;
      this.columnXZ.push({ x: Math.cos(theta) * r, z: Math.sin(theta) * r });
    }

    const total = this.COLUMN_COUNT * this.PARTICLES_PER_COLUMN;
    this.streamPositions = new Float32Array(total * 3);
    this.streamVelocities = new Float32Array(total * 3);
    this.streamLives = new Float32Array(total);
    this.streamColumns = new Float32Array(total);

    for (let c = 0; c < this.COLUMN_COUNT; c++) {
      for (let p = 0; p < this.PARTICLES_PER_COLUMN; p++) {
        const i = c * this.PARTICLES_PER_COLUMN + p;
        const col = this.columnXZ[c];
        const j = 0.05;
        this.streamPositions[i * 3 + 0] = col.x + (Math.random() - 0.5) * j;
        this.streamPositions[i * 3 + 1] =
          (p / this.PARTICLES_PER_COLUMN) * 6 + Math.random() * 0.2;
        this.streamPositions[i * 3 + 2] = col.z + (Math.random() - 0.5) * j;
        this.streamVelocities[i * 3 + 0] = (Math.random() - 0.5) * 0.02;
        this.streamVelocities[i * 3 + 1] = 0.4 + Math.random() * 0.25;
        this.streamVelocities[i * 3 + 2] = (Math.random() - 0.5) * 0.02;
        this.streamLives[i] = Math.random();
        this.streamColumns[i] = c;
      }
    }

    this.streamGeom = new THREE.BufferGeometry();
    this.streamGeom.setAttribute(
      "position",
      new THREE.BufferAttribute(this.streamPositions, 3),
    );
    this.streamGeom.setAttribute(
      "aLife",
      new THREE.BufferAttribute(this.streamLives, 1),
    );
    this.streamGeom.setAttribute(
      "aColumn",
      new THREE.BufferAttribute(this.streamColumns, 1),
    );

    this.streamMat = new THREE.ShaderMaterial({
      vertexShader: STREAM_VERT,
      fragmentShader: STREAM_FRAG,
      uniforms: {
        uColor: { value: new THREE.Color(0xffc890) },
        uIntensity: { value: 0.0 },
        uTime: { value: 0 },
        uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
      },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    this.streams = new THREE.Points(this.streamGeom, this.streamMat);
    this.scene.add(this.streams);
  }

  // ----- SOURCE -------------------------------------------------------------
  private buildSource() {
    const n = this.SOURCE_COUNT;
    this.sourcePositions = new Float32Array(n * 3);
    this.sourceSizes = new Float32Array(n);
    this.sourceOffsets = new Float32Array(n);

    for (let i = 0; i < n; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = Math.pow(Math.random(), 0.55) * 1.4;
      this.sourcePositions[i * 3 + 0] = Math.sin(phi) * Math.cos(theta) * r;
      this.sourcePositions[i * 3 + 1] = Math.cos(phi) * r;
      this.sourcePositions[i * 3 + 2] = Math.sin(phi) * Math.sin(theta) * r;
      this.sourceSizes[i] = 2.0 + Math.random() * 4.5;
      this.sourceOffsets[i] = Math.random();
    }

    this.sourceGeom = new THREE.BufferGeometry();
    this.sourceGeom.setAttribute(
      "position",
      new THREE.BufferAttribute(this.sourcePositions, 3),
    );
    this.sourceGeom.setAttribute(
      "aSize",
      new THREE.BufferAttribute(this.sourceSizes, 1),
    );
    this.sourceGeom.setAttribute(
      "aOffset",
      new THREE.BufferAttribute(this.sourceOffsets, 1),
    );

    this.sourceMat = new THREE.ShaderMaterial({
      vertexShader: SOURCE_VERT,
      fragmentShader: SOURCE_FRAG,
      uniforms: {
        uColor: { value: new THREE.Color(0xffd9a8) },
        uIntensity: { value: 0.0 },
        uTime: { value: 0 },
        uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
      },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    this.source = new THREE.Points(this.sourceGeom, this.sourceMat);
    this.source.position.set(0, 6.5, 0);
    this.scene.add(this.source);
  }

  // =========================================================================
  // Public API
  // =========================================================================
  setProgress(p: number) {
    this.progress = clamp(p, 0, 1);
  }
  setMouse(x: number, y: number, active = true) {
    this.mouseNorm.set(x, y);
    this.mouseActive = active ? 1 : 0;
  }
  mount() {
    this.lastT = performance.now();
    const loop = (t: number) => {
      const dt = Math.min(0.05, (t - this.lastT) / 1000);
      this.lastT = t;
      this.time += dt;
      this.tick(dt);
      this.renderer.render(this.scene, this.camera);
      this.rafId = requestAnimationFrame(loop);
    };
    this.rafId = requestAnimationFrame(loop);
  }
  resize() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.streamMat.uniforms.uPixelRatio.value = Math.min(
      window.devicePixelRatio,
      2,
    );
    this.sourceMat.uniforms.uPixelRatio.value = Math.min(
      window.devicePixelRatio,
      2,
    );
  }
  dispose() {
    if (this.rafId !== null) cancelAnimationFrame(this.rafId);
    this.renderer.dispose();
  }

  // =========================================================================
  // Tick — 180° camera arc + shader updates
  // =========================================================================
  private tick(dt: number) {
    const p = this.progress;

    const wP3 = plateau(p, 0.34, 0.44, 0.58, 0.66);
    const wP4 = plateau(p, 0.58, 0.66, 0.78, 0.84);
    const wP5 = plateau(p, 0.78, 0.86, 1.05, 1.1);
    const wP2 = plateau(p, 0.15, 0.22, 0.34, 0.44);

    // -------- Plane rotation 0 → -π/2 during P1 → end of P2 --------
    // Starts just after P1 begins, completes by the end of P2 so the plane
    // is fully horizontal as we enter P3 (terrain phase).
    const rotP = easeInOutCubic(clamp((p - 0.05) / 0.25, 0, 1));
    this.ground.rotation.x = lerp(0, -Math.PI / 2, rotP);

    // -------- Camera trajectory (plane rotates, camera moves alongside) --------
    const breathX = Math.sin(this.time * 0.16) * 0.02;
    const breathY = Math.cos(this.time * 0.21) * 0.015;

    const camPos = new THREE.Vector3();
    const lookAt = new THREE.Vector3();

    if (p < 0.15) {
      // P1 — camera straight in front of the vertical plane (poster view).
      const t = easeInOutCubic(p / 0.15);
      camPos.set(0, 0, lerp(13, 12, t));
      lookAt.set(0, 0, 0);
    } else if (p < 0.35) {
      // P2 — plane tilts back; camera rises and comes in a bit, tracks the
      // pivoting top edge while keeping the flat surface visible.
      const t = easeInOutCubic((p - 0.15) / 0.2);
      camPos.set(0, lerp(0, 2.4, t), lerp(12, 8, t));
      lookAt.set(0, lerp(0, 0.4, t), 0);
    } else if (p < 0.6) {
      // P3 — plane is now horizontal; camera gains altitude for landscape view.
      const t = easeInOutCubic((p - 0.35) / 0.25);
      camPos.set(lerp(0, 0.5, t), lerp(2.4, 3.6, t), lerp(8, 6.5, t));
      lookAt.set(0, lerp(0.4, 1.2, t), 0);
    } else if (p < 0.8) {
      // P4 — rise toward the source at world (0, 6.5, 0).
      const t = easeInOutCubic((p - 0.6) / 0.2);
      camPos.set(lerp(0.5, 0.2, t), lerp(3.6, 5.8, t), lerp(6.5, 4.5, t));
      lookAt.set(0, lerp(1.2, 5.0, t), 0);
    } else {
      // P5 — near top-down aerial view of the whole territory.
      const t = easeInOutCubic((p - 0.8) / 0.2);
      camPos.set(lerp(0.2, 0, t), lerp(5.8, 8.5, t), lerp(4.5, 2.8, t));
      lookAt.set(0, lerp(5.0, 0.3, t), 0);
    }

    this.camera.position.set(camPos.x + breathX, camPos.y + breathY, camPos.z);
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(lookAt);

    // uUpFactor is tied to the plane's rotation: 0 when vertical (poster view),
    // 1 when horizontal (terrain view). Fragment shader blends both renderings.
    const upFactor = rotP;

    // -------- Mouse projection: raycast against the ground mesh directly.
    // The mesh is rotating over time, so we intersect with it (not with a
    // fixed world plane) and convert the hit into plane-local xy coords
    // — that's what the shader's halftone / footprints / mouse boost use.
    const mx = this.mouseNorm.x * 2 - 1;
    const my = -(this.mouseNorm.y * 2 - 1);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(new THREE.Vector2(mx, my), this.camera);
    const hits = raycaster.intersectObject(this.ground, false);
    if (hits.length > 0) {
      const local = this.ground.worldToLocal(hits[0].point.clone());
      this.mouseWorld.set(local.x, local.y, 0);
    }

    // -------- Ground uniforms --------
    this.groundMat.uniforms.uTime.value = this.time;
    this.groundMat.uniforms.uProgress.value = p;
    this.groundMat.uniforms.uUpFactor.value = upFactor;
    const displace = easeInOutCubic(clamp((p - 0.2) / 0.25, 0, 1));
    this.groundMat.uniforms.uDisplace.value = displace;
    this.groundMat.uniforms.uMouse.value.set(
      this.mouseWorld.x,
      this.mouseWorld.y,
    );
    this.groundMat.uniforms.uMouseActive.value =
      this.mouseActive * (wP2 + wP3 * 0.7);

    // -------- Fog --------
    this.sceneFog.density = lerp(0.055, 0.025, upFactor);

    // -------- Streams --------
    const streamIntensity = Math.max(wP3, wP4 * 0.9, wP5 * 0.75);
    this.streamMat.uniforms.uIntensity.value = streamIntensity;
    this.streamMat.uniforms.uTime.value = this.time;
    this.streams.visible = streamIntensity > 0.02;

    const sp = this.streamPositions;
    const sl = this.streamLives;
    const total = this.COLUMN_COUNT * this.PARTICLES_PER_COLUMN;
    const sourceY = 6.5;
    for (let i = 0; i < total; i++) {
      sl[i] += dt * 0.22;
      if (sl[i] > 1) sl[i] = 0;
      sp[i * 3 + 0] += this.streamVelocities[i * 3 + 0] * dt;
      sp[i * 3 + 1] += this.streamVelocities[i * 3 + 1] * dt;
      sp[i * 3 + 2] += this.streamVelocities[i * 3 + 2] * dt;

      const ySoft = clamp(sp[i * 3 + 1] / 5.5, 0, 1);
      const attract = lerp(0.0, 1.6, ySoft) * (0.3 + 0.7 * wP4);
      const dx = 0 - sp[i * 3 + 0];
      const dz = 0 - sp[i * 3 + 2];
      const dsq = dx * dx + dz * dz + 0.0001;
      const inv = 1 / Math.sqrt(dsq);
      sp[i * 3 + 0] += dx * inv * attract * dt * 0.25;
      sp[i * 3 + 2] += dz * inv * attract * dt * 0.25;

      if (sp[i * 3 + 1] > sourceY + 0.2) {
        const c = this.streamColumns[i];
        const col = this.columnXZ[c];
        sp[i * 3 + 0] = col.x + (Math.random() - 0.5) * 0.05;
        sp[i * 3 + 1] = Math.random() * 0.4;
        sp[i * 3 + 2] = col.z + (Math.random() - 0.5) * 0.05;
        sl[i] = 0;
      }
    }
    this.streamGeom.attributes.position.needsUpdate = true;
    this.streamGeom.attributes.aLife.needsUpdate = true;

    // -------- Source --------
    this.sourceMat.uniforms.uTime.value = this.time;
    const sourceIntensity = wP4 * 0.95 + wP5 * 1.15;
    this.sourceMat.uniforms.uIntensity.value = sourceIntensity;
    this.source.visible = sourceIntensity > 0.02;
    this.source.scale.setScalar(lerp(0.3, 1.35, Math.max(wP4, wP5)));
    this.source.rotation.y += dt * 0.06;

    this.sourceLight.intensity = wP4 * 1.8 + wP5 * 3.4;
    this.sourceLight.distance = 14 + wP5 * 10;

    this.ambient.intensity = lerp(0.22, 0.1, upFactor) + wP5 * 0.08;
  }
}
