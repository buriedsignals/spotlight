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

// =============================================================================
// GLSL helpers — hash + noise + fbm
// =============================================================================
const NOISE_GLSL = /* glsl */ `
  float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
  }
  float noise(vec2 p) {
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
      v += a * noise(p);
      p *= 2.0;
      a *= 0.5;
    }
    return v;
  }
`;

// =============================================================================
// GROUND — propagation of filaments outward from the figure
// =============================================================================
const GROUND_VERT = /* glsl */ `
  varying vec3 vWorldPos;
  void main() {
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vWorldPos = wp.xyz;
    gl_Position = projectionMatrix * viewMatrix * wp;
  }
`;

const GROUND_FRAG = /* glsl */ `
  uniform float uTime;
  uniform float uProgress;
  uniform vec2  uMouse;
  uniform float uMouseActive;
  uniform float uP05Boost;

  varying vec3 vWorldPos;

  ${NOISE_GLSL}

  void main() {
    vec2 pos = vWorldPos.xz;
    float d = length(pos);
    float theta = atan(pos.y, pos.x);

    // --- Growth front (propagation wave) --------------------------------
    // The filaments propagate outward from the figure as we scroll through P2/P3.
    float growthP = smoothstep(0.14, 0.58, uProgress);
    float growthFront = growthP * 26.0;

    // Mouse locally pushes the front further
    float mouseDist = length(pos - uMouse);
    float mouseInfluence = smoothstep(3.2, 0.0, mouseDist) * uMouseActive;
    growthFront += mouseInfluence * 4.5;

    // Coverage: 1 inside the front, 0 outside
    float covered = smoothstep(growthFront + 0.8, growthFront - 0.35, d);

    // --- Path structure (discrete radial stripes with branching) --------
    // Break the circle into angular stripes. Each stripe either carries paths or not.
    float stripeCount = 36.0;
    float stripeId = floor(theta * stripeCount / 6.2831853);
    float withinStripe = fract(theta * stripeCount / 6.2831853);
    // Random activation per stripe
    float stripeActive = step(0.42, hash(vec2(stripeId, 2.1)));
    // Random offset inside each stripe (so the line isn't always centered)
    float stripeOffset = hash(vec2(stripeId, 7.3)) * 0.4;

    // Primary radial path inside this stripe — line at offset ± wobble(d)
    float wobble = (noise(vec2(d * 0.22, stripeId)) - 0.5) * 0.25;
    float primaryLineDist = abs(withinStripe - 0.5 - stripeOffset + wobble);
    float primary = smoothstep(0.035, 0.0, primaryLineDist) * stripeActive;

    // Radial segmentation — the line isn't continuous, it's a series of dashes
    float dashPattern = smoothstep(0.12, 0.0, abs(fract(d * 0.35 + hash(vec2(stripeId, 11.0)) * 5.0) - 0.45));
    primary *= mix(0.55, 1.0, dashPattern);

    // Branch paths — weaker secondary offshoots at selected angles
    float branchSeed = hash(vec2(stripeId, 17.0));
    float branchStripe = floor(theta * stripeCount * 2.0 / 6.2831853);
    float branchWithin = fract(theta * stripeCount * 2.0 / 6.2831853);
    float branchActive = step(0.72, hash(vec2(branchStripe, 23.0))) * step(0.4, branchSeed);
    float branchLineDist = abs(branchWithin - 0.5);
    float branch = smoothstep(0.025, 0.0, branchLineDist) * branchActive * 0.55;
    // Branches only exist in mid-range
    branch *= smoothstep(2.0, 4.0, d) * smoothstep(22.0, 16.0, d);

    float filament = max(primary, branch);

    // --- Coverage masking ------------------------------------------------
    filament *= covered;

    // --- Travelling pulse — light shimmer along the filaments -----------
    // Creates the sense of something propagating, not just revealed.
    float flowSpeed = 4.0;
    float flowPhase = d * 0.8 - uTime * flowSpeed + hash(vec2(stripeId, 0.3)) * 6.28;
    float pulse = smoothstep(-0.2, 0.2, sin(flowPhase)) * 0.5 + 0.5;
    filament *= (0.55 + 0.7 * pulse);

    // --- Fog zones (honest gaps, un-mapped territory) -------------------
    float fogZone = smoothstep(0.28, 0.50, fbm(pos * 0.11 + vec2(5.0, 13.0)));
    filament *= fogZone;

    // --- Impasse tint — stripes with impasse signature shift to rust -----
    float stripeImpasse = step(0.78, hash(vec2(stripeId, 41.0)));
    // Impasses end early (smaller max radius)
    float impasseEnd = 6.0 + hash(vec2(stripeId, 31.0)) * 5.0;
    float impasseCut = smoothstep(impasseEnd + 0.3, impasseEnd - 0.3, d);
    float impasseMask = stripeImpasse * impasseCut * stripeActive;

    // --- Colors ---------------------------------------------------------
    vec3 base = vec3(0.014, 0.017, 0.024); // cool near-black ground
    vec3 amber = vec3(1.00, 0.74, 0.42);
    vec3 rust  = vec3(0.72, 0.20, 0.09);
    vec3 emission = mix(amber, rust, impasseMask * 0.85);

    vec3 color = base + emission * filament * (1.85 + uP05Boost * 0.7);

    // Bright leading edge at the wavefront
    float front = exp(-pow(d - growthFront, 2.0) * 2.5) * growthP * 0.42;
    color += amber * front;

    gl_FragColor = vec4(color, 1.0);
  }
`;

// =============================================================================
// BEAM — Lusion-accurate narrow volumetric column
// =============================================================================
const BEAM_VERT = /* glsl */ `
  varying vec3 vPos;
  void main() {
    vPos = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const BEAM_FRAG = /* glsl */ `
  uniform float uIntensity;
  uniform float uTime;
  varying vec3 vPos;

  ${NOISE_GLSL}

  void main() {
    // Cone local y: -6 (wide, bottom) → +6 (narrow, top)
    float t = clamp((vPos.y + 6.0) / 12.0, 0.0, 1.0);
    float r = length(vPos.xz);

    // Tight radial fall-off — the beam is NARROW
    float radial = 1.0 - smoothstep(0.0, 1.2, r);
    radial = pow(radial, 2.4);

    // Strong vertical gradient — bright at top, fades toward ground
    float vertical = pow(t, 0.85);

    // Organic shimmer — multiple octaves for depth
    float n1 = noise(vec2(vPos.xz.x * 4.0 + uTime * 0.3, vPos.y * 1.2 + uTime * 0.18));
    float n2 = noise(vec2(vPos.xz.y * 3.2 - uTime * 0.22, vPos.y * 0.8));
    float shimmer = 0.55 + 0.3 * n1 + 0.3 * n2;

    // Base alpha of the cone body
    float alpha = uIntensity * radial * vertical * shimmer * 0.78;

    // Extra bright "core" at the top source
    float coreT = pow(t, 4.0);
    float coreR = 1.0 - smoothstep(0.0, 0.35, r);
    alpha += uIntensity * coreT * pow(coreR, 3.0) * 1.2;

    // Cool white, slight warm at the core
    vec3 color = mix(vec3(0.84, 0.91, 1.0), vec3(1.0, 0.98, 0.92), coreT);

    gl_FragColor = vec4(color, alpha);
  }
`;

// =============================================================================
// STREAMS — amber columnar particles rising from the ground
// =============================================================================
const STREAM_VERT = /* glsl */ `
  attribute float aLife;
  attribute float aColumn;
  uniform float uPixelRatio;
  uniform float uTime;
  varying float vLife;
  varying float vColumn;
  void main() {
    vLife = aLife;
    vColumn = aColumn;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    float sizeBase = 3.2 + sin(uTime * 0.6 + aColumn * 1.7) * 0.6;
    gl_PointSize = sizeBase * uPixelRatio * (200.0 / max(-mv.z, 0.1));
    gl_Position = projectionMatrix * mv;
  }
`;

const STREAM_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uIntensity;
  varying float vLife;
  varying float vColumn;
  void main() {
    vec2 c = gl_PointCoord - vec2(0.5);
    // Point reads as a thin vertical streak
    float vertical = smoothstep(0.06, 0.0, abs(c.x));
    float tail = smoothstep(0.5, 0.0, length(c));
    float alpha = vertical * tail * (0.45 + 0.55 * sin(vLife * 3.1415));
    gl_FragColor = vec4(uColor * uIntensity, alpha * uIntensity);
  }
`;

// =============================================================================
// SOURCE PARTICLES — luminous cluster at the top that grows by accretion
// =============================================================================
const SOURCE_VERT = /* glsl */ `
  attribute float aSize;
  attribute float aOffset;
  uniform float uPixelRatio;
  uniform float uIntensity;
  uniform float uTime;
  varying float vGlow;
  void main() {
    // Subtle Brownian drift — position is the rest pose, we offset in shader
    vec3 driftedPos = position + vec3(
      sin(uTime * 0.4 + aOffset * 3.0) * 0.04,
      cos(uTime * 0.5 + aOffset * 2.1) * 0.04,
      sin(uTime * 0.3 + aOffset * 4.2) * 0.04
    );
    vec4 mv = modelViewMatrix * vec4(driftedPos, 1.0);
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
// Mountain silhouettes — canvas-drawn, painterly, three ridges for parallax
// =============================================================================
function makeMountainTexture(
  seed: number,
  mirrored: boolean,
): THREE.CanvasTexture {
  const w = 1200;
  const h = 800;
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d")!;
  ctx.clearRect(0, 0, w, h);

  const ridges = [
    {
      peaks: 8,
      color: "#14161c",
      baseY: h * 0.62,
      peakRange: [h * 0.25, h * 0.55],
    },
    {
      peaks: 10,
      color: "#1c2028",
      baseY: h * 0.72,
      peakRange: [h * 0.15, h * 0.45],
    },
    {
      peaks: 14,
      color: "#24282f",
      baseY: h * 0.82,
      peakRange: [h * 0.1, h * 0.35],
    },
  ];

  let s = seed;
  const rnd = () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };

  for (const r of ridges) {
    ctx.fillStyle = r.color;
    ctx.beginPath();
    ctx.moveTo(0, h);
    const [lo, hi] = r.peakRange;
    for (let i = 0; i <= r.peaks; i++) {
      const x = (i / r.peaks) * w + (rnd() - 0.5) * (w / r.peaks) * 0.35;
      const peakY = r.baseY - (lo + rnd() * (hi - lo));
      if (i === 0) ctx.lineTo(x, peakY);
      else {
        const prevX = ((i - 1) / r.peaks) * w;
        const midX = (prevX + x) / 2;
        const midY = r.baseY - (lo + rnd() * (hi - lo) * 0.7);
        ctx.quadraticCurveTo(midX, midY, x, peakY);
      }
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fill();
  }

  if (mirrored) {
    ctx.globalCompositeOperation = "copy";
    ctx.save();
    ctx.scale(-1, 1);
    ctx.drawImage(c, -w, 0);
    ctx.restore();
  }

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  return tex;
}

// =============================================================================
// Scenography — main class
// =============================================================================
export class Scenography {
  private canvas: HTMLCanvasElement;
  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;

  // State
  private progress = 0;
  private mouseNorm = new THREE.Vector2(0.5, 0.5);
  private mouseWorld = new THREE.Vector3();
  private mouseActive = 0;
  private time = 0;
  private lastT = 0;
  private rafId: number | null = null;

  // Lights
  private ambient!: THREE.AmbientLight;
  private keyLight!: THREE.DirectionalLight;
  private fillLight!: THREE.DirectionalLight;
  private sourceLight!: THREE.PointLight; // drives real illumination from the source in P4/P5
  private sceneFog!: THREE.FogExp2;

  // Objects
  private ground!: THREE.Mesh;
  private groundMat!: THREE.ShaderMaterial;

  private beam!: THREE.Mesh;
  private beamMat!: THREE.ShaderMaterial;

  private mountains: THREE.Mesh[] = [];

  private silhouette!: THREE.Group;
  private silMat!: THREE.MeshStandardMaterial;

  // Streams — columnar rising particles (P3-P5)
  private streams!: THREE.Points;
  private streamMat!: THREE.ShaderMaterial;
  private streamGeom!: THREE.BufferGeometry;
  private streamPositions!: Float32Array;
  private streamVelocities!: Float32Array;
  private streamLives!: Float32Array;
  private streamColumns!: Float32Array;
  private columnXZ: { x: number; z: number }[] = [];
  private readonly COLUMN_COUNT = 70;
  private readonly PARTICLES_PER_COLUMN = 22;

  // Source — particle cluster (P4/P5)
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
    this.scene.background = new THREE.Color(0x05060a);
    this.sceneFog = new THREE.FogExp2(0x0a0d14, 0.055);
    this.scene.fog = this.sceneFog;

    this.camera = new THREE.PerspectiveCamera(
      38,
      window.innerWidth / window.innerHeight,
      0.1,
      120,
    );
    this.camera.position.set(0, 1.8, 6.2);
    this.camera.lookAt(0, 1.0, 0);

    this.setupLights();
    this.buildGround();
    this.buildMountains();
    this.buildSilhouette();
    this.buildBeam();
    this.buildStreams();
    this.buildSource();
  }

  private setupLights() {
    this.ambient = new THREE.AmbientLight(0x8f99ad, 0.1);
    this.scene.add(this.ambient);

    this.keyLight = new THREE.DirectionalLight(0xf0f4fb, 0.9);
    this.keyLight.position.set(0, 10, 0.5);
    this.scene.add(this.keyLight);

    this.fillLight = new THREE.DirectionalLight(0x3a4558, 0.25);
    this.fillLight.position.set(-4, 3, -6);
    this.scene.add(this.fillLight);

    // Real illumination from the source — positioned inside the particle cluster.
    // Intensity drives up during P4/P5 as the cluster forms.
    this.sourceLight = new THREE.PointLight(0xffc890, 0.0, 22, 1.6);
    this.sourceLight.position.set(0, 7.5, -0.4);
    this.scene.add(this.sourceLight);
  }

  // ----- GROUND --------------------------------------------------------------
  private buildGround() {
    const geo = new THREE.PlaneGeometry(70, 70, 1, 1);
    geo.rotateX(-Math.PI / 2);
    this.groundMat = new THREE.ShaderMaterial({
      vertexShader: GROUND_VERT,
      fragmentShader: GROUND_FRAG,
      uniforms: {
        uTime: { value: 0 },
        uProgress: { value: 0 },
        uMouse: { value: new THREE.Vector2(0, 0) },
        uMouseActive: { value: 0 },
        uP05Boost: { value: 0 },
      },
      fog: false,
    });
    this.ground = new THREE.Mesh(geo, this.groundMat);
    this.scene.add(this.ground);
  }

  // ----- MOUNTAINS -----------------------------------------------------------
  private buildMountains() {
    const left = new THREE.Mesh(
      new THREE.PlaneGeometry(14, 9),
      new THREE.MeshBasicMaterial({
        map: makeMountainTexture(17, false),
        transparent: true,
        depthWrite: false,
        fog: true,
      }),
    );
    left.position.set(-8.5, 3.2, -4);
    this.scene.add(left);
    this.mountains.push(left);

    const right = new THREE.Mesh(
      new THREE.PlaneGeometry(14, 9),
      new THREE.MeshBasicMaterial({
        map: makeMountainTexture(53, true),
        transparent: true,
        depthWrite: false,
        fog: true,
      }),
    );
    right.position.set(8.5, 3.2, -4);
    this.scene.add(right);
    this.mountains.push(right);

    const farRidge = new THREE.Mesh(
      new THREE.PlaneGeometry(28, 7),
      new THREE.MeshBasicMaterial({
        map: makeMountainTexture(91, false),
        transparent: true,
        depthWrite: false,
        opacity: 0.72,
        fog: true,
      }),
    );
    farRidge.position.set(0, 2.4, -14);
    this.scene.add(farRidge);
    this.mountains.push(farRidge);
  }

  // ----- SILHOUETTE ----------------------------------------------------------
  private buildSilhouette() {
    this.silhouette = new THREE.Group();

    this.silMat = new THREE.MeshStandardMaterial({
      color: 0x0a0b0f,
      roughness: 0.95,
      metalness: 0.0,
      emissive: 0x1c2230,
      emissiveIntensity: 0.04,
      fog: true,
    });

    const head = new THREE.Mesh(
      new THREE.SphereGeometry(0.15, 22, 16),
      this.silMat,
    );
    head.position.set(0, 1.52, 0);
    this.silhouette.add(head);

    const body = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.19, 0.85, 6, 14),
      this.silMat,
    );
    body.position.set(0, 0.95, 0);
    this.silhouette.add(body);

    const legL = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.09, 0.72, 4, 8),
      this.silMat,
    );
    legL.position.set(-0.07, 0.38, 0);
    this.silhouette.add(legL);

    const legR = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.09, 0.72, 4, 8),
      this.silMat,
    );
    legR.position.set(0.07, 0.38, 0);
    this.silhouette.add(legR);

    this.silhouette.position.set(0, 0, -0.6);
    this.silhouette.rotation.y = 0.35;
    this.scene.add(this.silhouette);
  }

  // ----- BEAM ----------------------------------------------------------------
  private buildBeam() {
    // Narrower cone: tighter radius top, tighter radius bottom.
    const geo = new THREE.CylinderGeometry(0.08, 1.2, 12, 36, 1, true);
    this.beamMat = new THREE.ShaderMaterial({
      vertexShader: BEAM_VERT,
      fragmentShader: BEAM_FRAG,
      uniforms: {
        uIntensity: { value: 1.0 },
        uTime: { value: 0 },
      },
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    });
    this.beam = new THREE.Mesh(geo, this.beamMat);
    this.beam.position.set(0, 5.5, -0.4);
    this.scene.add(this.beam);
  }

  // ----- STREAMS -------------------------------------------------------------
  private buildStreams() {
    for (let i = 0; i < this.COLUMN_COUNT; i++) {
      const theta = Math.random() * Math.PI * 2;
      const r = 1.2 + Math.sqrt(Math.random()) * 16.0;
      this.columnXZ.push({
        x: Math.cos(theta) * r,
        z: Math.sin(theta) * r - 0.4,
      });
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
        const jitter = 0.05;
        this.streamPositions[i * 3 + 0] =
          col.x + (Math.random() - 0.5) * jitter;
        this.streamPositions[i * 3 + 1] =
          (p / this.PARTICLES_PER_COLUMN) * 7 + Math.random() * 0.2;
        this.streamPositions[i * 3 + 2] =
          col.z + (Math.random() - 0.5) * jitter;

        this.streamVelocities[i * 3 + 0] = (Math.random() - 0.5) * 0.02;
        this.streamVelocities[i * 3 + 1] = 0.45 + Math.random() * 0.25;
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

  // ----- SOURCE (luminous particle cluster) ----------------------------------
  private buildSource() {
    const n = this.SOURCE_COUNT;
    this.sourcePositions = new Float32Array(n * 3);
    this.sourceSizes = new Float32Array(n);
    this.sourceOffsets = new Float32Array(n);

    // Gaussian-like distribution within ~1.3 unit radius sphere, denser at centre
    for (let i = 0; i < n; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      // Pow < 1 biases toward centre
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
    this.source.position.set(0, 7.5, -0.4);
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
    this.scene.traverse((obj) => {
      if (obj instanceof THREE.Mesh || obj instanceof THREE.Points) {
        (obj as THREE.Mesh).geometry?.dispose();
        const mat = (obj as THREE.Mesh).material;
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
        else mat?.dispose();
      }
    });
    this.renderer.dispose();
  }

  // =========================================================================
  // Frame tick
  // =========================================================================
  private tick(dt: number) {
    const p = this.progress;

    // ----- Plan weights ----------------------------------------------------
    const wP1 = plateau(p, -0.05, 0.02, 0.15, 0.22);
    const wP2 = plateau(p, 0.15, 0.22, 0.34, 0.44);
    const wP3 = plateau(p, 0.34, 0.44, 0.58, 0.66);
    const wP4 = plateau(p, 0.58, 0.66, 0.78, 0.84);
    const wP5 = plateau(p, 0.78, 0.86, 1.05, 1.1);

    // ----- Camera trajectory ----------------------------------------------
    const cam = new THREE.Vector3();
    const look = new THREE.Vector3();
    if (p < 0.15) {
      const t = easeInOutCubic(p / 0.15);
      cam.set(0, lerp(1.85, 1.8, t), lerp(6.4, 6.2, t));
      look.set(0, lerp(1.1, 1.0, t), 0);
    } else if (p < 0.35) {
      const t = easeInOutCubic((p - 0.15) / 0.2);
      cam.set(0, lerp(1.8, 0.7, t), lerp(6.2, 4.2, t));
      look.set(0, lerp(1.0, 0.1, t), lerp(0, -0.3, t));
    } else if (p < 0.6) {
      const t = easeInOutCubic((p - 0.35) / 0.25);
      cam.set(lerp(0, 0.2, t), lerp(0.7, 2.6, t), lerp(4.2, 7.0, t));
      look.set(0, lerp(0.1, 2.0, t), lerp(-0.3, -0.5, t));
    } else if (p < 0.8) {
      const t = easeInOutCubic((p - 0.6) / 0.2);
      cam.set(lerp(0.2, 0.05, t), lerp(2.6, 5.8, t), lerp(7.0, 5.5, t));
      look.set(0, lerp(2.0, 7.0, t), lerp(-0.5, -0.4, t));
    } else {
      const t = easeInOutCubic((p - 0.8) / 0.2);
      cam.set(lerp(0.05, 0, t), lerp(5.8, 1.8, t), lerp(5.5, 6.2, t));
      look.set(0, lerp(7.0, 1.0, t), lerp(-0.4, 0, t));
    }
    cam.x += Math.sin(this.time * 0.17) * 0.018;
    cam.y += Math.cos(this.time * 0.22) * 0.012;
    this.camera.position.copy(cam);
    this.camera.lookAt(look);

    // ----- Mouse projection onto ground plane ------------------------------
    const mx = this.mouseNorm.x * 2 - 1;
    const my = -(this.mouseNorm.y * 2 - 1);
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(new THREE.Vector2(mx, my), this.camera);
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    const hit = new THREE.Vector3();
    if (raycaster.ray.intersectPlane(plane, hit)) {
      this.mouseWorld.copy(hit);
    }

    // ----- Ground uniforms ------------------------------------------------
    this.groundMat.uniforms.uTime.value = this.time;
    this.groundMat.uniforms.uProgress.value = p;
    this.groundMat.uniforms.uMouse.value.set(
      this.mouseWorld.x,
      this.mouseWorld.z,
    );
    this.groundMat.uniforms.uMouseActive.value =
      this.mouseActive * (wP2 + wP3 * 0.7 + wP5 * 0.3);
    this.groundMat.uniforms.uP05Boost.value = wP5;

    // ----- Scene fog ------------------------------------------------------
    this.sceneFog.density =
      lerp(0.055, 0.03, Math.max(wP2, wP3, wP4) * 0.6) * (1.0 + 0.1 * wP5);

    // ----- Beam -----------------------------------------------------------
    const beamIntensity =
      wP1 * 0.95 + wP2 * 0.6 + wP3 * 0.4 + wP4 * 0.3 + wP5 * 1.1;
    this.beamMat.uniforms.uIntensity.value = beamIntensity;
    this.beamMat.uniforms.uTime.value = this.time;
    this.beam.visible = beamIntensity > 0.02;
    const breathe = 1 + Math.sin(this.time * 0.55) * 0.025;
    this.beam.scale.set(breathe, 1, breathe);

    // ----- Silhouette -----------------------------------------------------
    const silVis = Math.max(wP1, wP2 * 0.6, wP5);
    this.silhouette.visible = silVis > 0.02;
    this.silMat.emissiveIntensity = 0.04 * wP1 + 0.12 * wP5;
    this.silhouette.rotation.y = 0.35 + Math.sin(this.time * 0.25) * 0.015;

    // Key light brightens during P5 (source illuminates the whole scene via sourceLight too)
    this.keyLight.intensity = 0.75 + 0.3 * wP5;
    this.fillLight.intensity = 0.22 + 0.1 * wP5;

    // ----- Streams --------------------------------------------------------
    const streamIntensity = Math.max(wP3, wP4 * 0.9, wP5 * 0.75);
    this.streamMat.uniforms.uIntensity.value = streamIntensity;
    this.streamMat.uniforms.uTime.value = this.time;
    this.streams.visible = streamIntensity > 0.02;

    const sp = this.streamPositions;
    const sl = this.streamLives;
    const total = this.COLUMN_COUNT * this.PARTICLES_PER_COLUMN;
    const reachTargetY = 7.5;
    for (let i = 0; i < total; i++) {
      sl[i] += dt * 0.22;
      if (sl[i] > 1) sl[i] = 0;

      sp[i * 3 + 0] += this.streamVelocities[i * 3 + 0] * dt;
      sp[i * 3 + 1] += this.streamVelocities[i * 3 + 1] * dt;
      sp[i * 3 + 2] += this.streamVelocities[i * 3 + 2] * dt;

      // Attract toward the source as they rise
      const ySoft = clamp(sp[i * 3 + 1] / 6.5, 0, 1);
      const attract = lerp(0.0, 1.6, ySoft) * (0.3 + 0.7 * wP4);
      const dx = 0 - sp[i * 3 + 0];
      const dz = -0.4 - sp[i * 3 + 2];
      const dsq = dx * dx + dz * dz + 0.0001;
      const inv = 1 / Math.sqrt(dsq);
      sp[i * 3 + 0] += dx * inv * attract * dt * 0.25;
      sp[i * 3 + 2] += dz * inv * attract * dt * 0.25;

      if (sp[i * 3 + 1] > reachTargetY + 0.2) {
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

    // ----- Source particle cluster ----------------------------------------
    this.sourceMat.uniforms.uTime.value = this.time;
    const sourceIntensity = wP4 * 0.95 + wP5 * 1.15;
    this.sourceMat.uniforms.uIntensity.value = sourceIntensity;
    this.source.visible = sourceIntensity > 0.02;
    const srcScale = lerp(0.3, 1.35, Math.max(wP4, wP5));
    this.source.scale.setScalar(srcScale);
    this.source.rotation.y += dt * 0.06;

    // Real illumination from the source — drives the scene light in P4/P5.
    this.sourceLight.intensity = wP4 * 1.8 + wP5 * 3.4;
    this.sourceLight.distance = 14 + wP5 * 10;
  }
}
