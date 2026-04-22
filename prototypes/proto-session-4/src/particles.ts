import * as THREE from "three";
import { GPUComputationRenderer } from "three/examples/jsm/misc/GPUComputationRenderer.js";

// GPU-compute particle system.
// Each particle = one texel in a pair of float textures (position + velocity).
// Two compute shaders run per frame to integrate + respawn particles, and a
// render shader samples the position texture to draw points. Scales to tens
// of thousands of particles at ~zero CPU cost.

const W = 512;
const H = 256;
export const SPARK_COUNT = W * H; // 131 072

export interface ParticleSystem {
  points: THREE.Points;
  tick(dt: number, t: number): void;
}

export function createSparks(
  renderer: THREE.WebGLRenderer,
  radius: number,
  revealSpeed = 1.8,
  revealStart = 0,
  revealOrigin: [number, number, number] = [0, radius, 0],
): ParticleSystem {
  const gpu = new GPUComputationRenderer(W, H, renderer);

  const posTex = gpu.createTexture();
  const velTex = gpu.createTexture();
  const seedTex = gpu.createTexture();

  const pData = posTex.image.data as unknown as Float32Array;
  const vData = velTex.image.data as unknown as Float32Array;
  const sData = seedTex.image.data as unknown as Float32Array;

  for (let i = 0; i < SPARK_COUNT; i++) {
    pData[i * 4 + 0] = 0;
    pData[i * 4 + 1] = 0;
    pData[i * 4 + 2] = 0;
    pData[i * 4 + 3] = 999; // age — starts expired so all will respawn
    vData[i * 4 + 0] = 0;
    vData[i * 4 + 1] = 0;
    vData[i * 4 + 2] = 0;
    vData[i * 4 + 3] = 1; // lifespan placeholder
    sData[i * 4 + 0] = Math.random();
    sData[i * 4 + 1] = Math.random();
    sData[i * 4 + 2] = Math.random();
    sData[i * 4 + 3] = Math.random();
  }
  seedTex.needsUpdate = true;

  // Shared GLSL: Voronoi edge distance — identical to the monolith shader
  // so particle spawns align pixel-perfect with the visible fractures.
  const crackGLSL = /* glsl */ `
    float hash2(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
    float vnoise2(vec2 p){
      vec2 i = floor(p), f = fract(p);
      vec2 u = f * f * (3.0 - 2.0 * f);
      float a = hash2(i);
      float b = hash2(i + vec2(1.0, 0.0));
      float c = hash2(i + vec2(0.0, 1.0));
      float d = hash2(i + vec2(1.0, 1.0));
      return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
    }
    float fbm2(vec2 p){
      float f = 0.0, amp = 0.5;
      for (int i = 0; i < 5; i++){ f += amp * vnoise2(p); p *= 2.03; amp *= 0.5; }
      return f;
    }
    vec3 hash3(vec3 p){
      p = vec3(
        dot(p, vec3(127.1, 311.7, 74.7)),
        dot(p, vec3(269.5, 183.3, 246.1)),
        dot(p, vec3(113.5, 271.9, 124.6))
      );
      return fract(sin(p) * 43758.5453);
    }
    float worleyEdges(vec3 p){
      vec3 ip = floor(p);
      vec3 fp = fract(p);
      float d1 = 8.0, d2 = 8.0;
      for (int i = -1; i <= 1; i++){
        for (int j = -1; j <= 1; j++){
          for (int k = -1; k <= 1; k++){
            vec3 g = vec3(float(i), float(j), float(k));
            vec3 o = hash3(ip + g);
            vec3 r = g + o - fp;
            float d = dot(r, r);
            if (d < d1){ d2 = d1; d1 = d; }
            else if (d < d2){ d2 = d; }
          }
        }
      }
      return sqrt(d2) - sqrt(d1);
    }
    float crackField(vec3 p){
      vec3 warp = vec3(
        fbm2(vec2(p.y * 0.9, p.z * 0.9)),
        fbm2(vec2(p.x * 0.9 + 4.3, p.z * 0.9 + 1.7)),
        fbm2(vec2(p.x * 0.9 + 9.1, p.y * 0.9 + 7.2))
      ) - 0.5;
      vec3 vp = p * 0.55 + warp * 0.35;
      return worleyEdges(vp);
    }
  `;

  const posShader = /* glsl */ `
    uniform float uTime;
    uniform float uDT;
    uniform float uRadius;
    uniform float uRevealSpeed;
    uniform float uRevealStart;
    uniform vec3 uRevealOrigin;
    uniform float uCrackThreshold;
    uniform sampler2D textureSeeds;

    ${crackGLSL}

    void main() {
      vec2 uv = gl_FragCoord.xy / resolution.xy;
      vec4 pos = texture2D(texturePosition, uv);
      vec4 vel = texture2D(textureVelocity, uv);
      vec4 seeds = texture2D(textureSeeds, uv);
      float age = pos.w;
      float life = vel.w;

      vec4 outPos = pos;
      if (age > life) {
        float s1 = fract(seeds.x + uTime * 0.137);
        float s2 = fract(seeds.y + uTime * 0.211);
        float theta = s1 * 6.28318530718;
        float phi = acos(2.0 * s2 - 1.0);
        float sp = sin(phi);
        vec3 n = vec3(sp * cos(theta), cos(phi), sp * sin(theta));
        vec3 candidate = n * uRadius;
        float distFromStart = length(candidate - uRevealOrigin);
        float front = max(uTime - uRevealStart, 0.0) * uRevealSpeed;
        float edgeDist = crackField(candidate);
        // spawn only where the candidate sits on a crack AND is revealed
        if (distFromStart < front + 0.4 && edgeDist < uCrackThreshold) {
          outPos.xyz = candidate;
          outPos.w = 0.0;
        } else {
          outPos.w = life + 0.1;
        }
      } else {
        outPos.xyz = pos.xyz + vel.xyz * uDT;
        outPos.w = age + uDT;
      }
      gl_FragColor = outPos;
    }
  `;

  const velShader = /* glsl */ `
    uniform float uTime;
    uniform float uDT;
    uniform float uRadius;
    uniform float uRevealSpeed;
    uniform float uRevealStart;
    uniform vec3 uRevealOrigin;
    uniform float uCrackThreshold;
    uniform sampler2D textureSeeds;

    ${crackGLSL}

    void main() {
      vec2 uv = gl_FragCoord.xy / resolution.xy;
      vec4 pos = texture2D(texturePosition, uv);
      vec4 vel = texture2D(textureVelocity, uv);
      vec4 seeds = texture2D(textureSeeds, uv);
      float age = pos.w;
      float life = vel.w;

      vec4 outVel = vel;
      if (age > life) {
        float s1 = fract(seeds.x + uTime * 0.137);
        float s2 = fract(seeds.y + uTime * 0.211);
        float theta = s1 * 6.28318530718;
        float phi = acos(2.0 * s2 - 1.0);
        float sp = sin(phi);
        vec3 n = vec3(sp * cos(theta), cos(phi), sp * sin(theta));
        vec3 candidate = n * uRadius;
        float distFromStart = length(candidate - uRevealOrigin);
        float front = max(uTime - uRevealStart, 0.0) * uRevealSpeed;
        float edgeDist = crackField(candidate);
        if (distFromStart < front + 0.4 && edgeDist < uCrackThreshold) {
          // projected outward with enough speed and life to travel far
          float speed = 0.5 + seeds.z * 1.0;
          outVel.xyz = n * speed;
          outVel.w = 6.0 + seeds.w * 5.0;
        }
      } else {
        // no wind — very light drag so particles carry far
        float DRAG = 0.999;
        outVel.xyz *= DRAG;
      }
      gl_FragColor = outVel;
    }
  `;

  const posVar = gpu.addVariable("texturePosition", posShader, posTex);
  const velVar = gpu.addVariable("textureVelocity", velShader, velTex);
  gpu.setVariableDependencies(posVar, [posVar, velVar]);
  gpu.setVariableDependencies(velVar, [posVar, velVar]);

  const shared = {
    uTime: { value: 0 },
    uDT: { value: 1 / 60 },
    uRadius: { value: radius },
    uRevealSpeed: { value: revealSpeed },
    uRevealStart: { value: revealStart },
    uRevealOrigin: {
      value: new THREE.Vector3(
        revealOrigin[0],
        revealOrigin[1],
        revealOrigin[2],
      ),
    },
    uCrackThreshold: { value: 0.05 },
    textureSeeds: { value: seedTex },
  };
  for (const v of [posVar, velVar]) {
    v.material.uniforms.uTime = shared.uTime;
    v.material.uniforms.uDT = shared.uDT;
    v.material.uniforms.uRadius = shared.uRadius;
    v.material.uniforms.uRevealSpeed = shared.uRevealSpeed;
    v.material.uniforms.uRevealStart = shared.uRevealStart;
    v.material.uniforms.uRevealOrigin = shared.uRevealOrigin;
    v.material.uniforms.uCrackThreshold = shared.uCrackThreshold;
    v.material.uniforms.textureSeeds = shared.textureSeeds;
  }

  const err = gpu.init();
  if (err !== null) {
    console.error("GPUComputationRenderer init error:", err);
  }

  // ---------------------------------------------------------- rendering
  const refs = new Float32Array(SPARK_COUNT * 2);
  const dummyPos = new Float32Array(SPARK_COUNT * 3);
  for (let i = 0; i < SPARK_COUNT; i++) {
    refs[i * 2 + 0] = ((i % W) + 0.5) / W;
    refs[i * 2 + 1] = (Math.floor(i / W) + 0.5) / H;
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(dummyPos, 3));
  geom.setAttribute("aRef", new THREE.BufferAttribute(refs, 2));

  const material = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uPosTex: { value: null as THREE.Texture | null },
      uVelTex: { value: null as THREE.Texture | null },
      uSize: { value: 0.25 },
      uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
      uCold: { value: new THREE.Color(0x707070) },
      uHot: { value: new THREE.Color(0xb8b8b8) },
    },
    vertexShader: /* glsl */ `
      uniform sampler2D uPosTex;
      uniform sampler2D uVelTex;
      uniform float uSize;
      uniform float uPixelRatio;
      attribute vec2 aRef;
      varying float vAlpha;
      void main() {
        vec4 pos = texture2D(uPosTex, aRef);
        vec4 vel = texture2D(uVelTex, aRef);
        float age = pos.w;
        float life = vel.w;
        float t = clamp(age / life, 0.0, 1.0);
        vec4 mv = modelViewMatrix * vec4(pos.xyz, 1.0);
        gl_Position = projectionMatrix * mv;
        // invisible when parked (age > life)
        float visible = step(age, life);
        vAlpha = visible * smoothstep(0.0, 0.05, t) * (1.0 - t) * (1.0 - t);
        float sizeT = 0.4 + 0.6 * (1.0 - t);
        gl_PointSize = uSize * uPixelRatio * sizeT * (320.0 / max(-mv.z, 0.1));
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      varying float vAlpha;
      uniform vec3 uCold;
      uniform vec3 uHot;
      void main() {
        vec2 p = gl_PointCoord - 0.5;
        float d = length(p);
        if (d > 0.5) discard;
        float core = exp(-d * d * 90.0);
        float halo = exp(-d * d * 20.0);
        vec3 col = mix(uCold, uHot, core);
        float a = (core + halo * 0.2) * vAlpha;
        gl_FragColor = vec4(col, a);
      }
    `,
  });

  const points = new THREE.Points(geom, material);
  points.frustumCulled = false;

  return {
    points,
    tick(dt, t) {
      shared.uTime.value = t;
      shared.uDT.value = dt;
      gpu.compute();
      material.uniforms.uPosTex.value =
        gpu.getCurrentRenderTarget(posVar).texture;
      material.uniforms.uVelTex.value =
        gpu.getCurrentRenderTarget(velVar).texture;
    },
  };
}

// Ambient dust filling the void around the sphere. Fine cool points, mostly
// still, with very slow drift. No flow, no swirl — just suspended dust to
// give the matte black volume some presence.
export function createDust(): ParticleSystem {
  const COUNT = 40000;
  const INNER = 10;
  const OUTER = 50;
  const positions = new Float32Array(COUNT * 3);
  const velocities = new Float32Array(COUNT * 3);
  const seeds = new Float32Array(COUNT);

  for (let i = 0; i < COUNT; i++) {
    const u = Math.random();
    const v = Math.random();
    const theta = 2 * Math.PI * u;
    const phi = Math.acos(2 * v - 1);
    const sp = Math.sin(phi);
    const r = Math.cbrt(INNER ** 3 + Math.random() * (OUTER ** 3 - INNER ** 3));
    positions[i * 3 + 0] = sp * Math.cos(theta) * r;
    positions[i * 3 + 1] = Math.cos(phi) * r;
    positions[i * 3 + 2] = sp * Math.sin(theta) * r;
    velocities[i * 3 + 0] = (Math.random() - 0.5) * 0.02;
    velocities[i * 3 + 1] = (Math.random() - 0.5) * 0.02 + 0.01;
    velocities[i * 3 + 2] = (Math.random() - 0.5) * 0.02;
    seeds[i] = Math.random();
  }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geom.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));

  const material = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uColor: { value: new THREE.Color(0xcccccc) },
      uSize: { value: 0.32 },
      uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
    },
    vertexShader: /* glsl */ `
      attribute float aSeed;
      varying float vSeed;
      uniform float uSize;
      uniform float uPixelRatio;
      void main(){
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_Position = projectionMatrix * mv;
        vSeed = aSeed;
        float sizeT = 0.3 + 0.7 * aSeed;
        gl_PointSize = uSize * uPixelRatio * sizeT * (250.0 / max(-mv.z, 0.1));
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      varying float vSeed;
      uniform vec3 uColor;
      void main(){
        vec2 p = gl_PointCoord - 0.5;
        float d = length(p);
        if (d > 0.5) discard;
        float core = exp(-d * d * 60.0);
        float a = core * (0.35 + 0.45 * vSeed);
        gl_FragColor = vec4(uColor, a);
      }
    `,
  });

  const points = new THREE.Points(geom, material);
  points.frustumCulled = false;
  const posAttr = geom.attributes.position as THREE.BufferAttribute;

  return {
    points,
    tick(dt, t) {
      const FLOW = 0.1;
      const STRENGTH = 0.6;
      const DRAG = 0.98;
      for (let i = 0; i < COUNT; i++) {
        const px = positions[i * 3];
        const py = positions[i * 3 + 1];
        const pz = positions[i * 3 + 2];

        // curl-ish flow: cross product with a sinusoidal axis per position
        const sx = Math.sin(px * FLOW + t * 0.12);
        const sy = Math.cos(py * FLOW - t * 0.09);
        const sz = Math.sin(pz * FLOW + t * 0.15);
        const r = Math.hypot(px, py, pz);
        let nx = 0,
          ny = 0,
          nz = 0;
        if (r > 1e-4) {
          nx = px / r;
          ny = py / r;
          nz = pz / r;
        }
        const cx = ny * sz - nz * sy;
        const cy = nz * sx - nx * sz;
        const cz = nx * sy - ny * sx;

        velocities[i * 3] = (velocities[i * 3] + cx * STRENGTH * dt) * DRAG;
        velocities[i * 3 + 1] =
          (velocities[i * 3 + 1] + cy * STRENGTH * dt) * DRAG;
        velocities[i * 3 + 2] =
          (velocities[i * 3 + 2] + cz * STRENGTH * dt) * DRAG;

        positions[i * 3] += velocities[i * 3] * dt;
        positions[i * 3 + 1] += velocities[i * 3 + 1] * dt;
        positions[i * 3 + 2] += velocities[i * 3 + 2] * dt;

        // wrap when going too far
        const nr = Math.hypot(
          positions[i * 3],
          positions[i * 3 + 1],
          positions[i * 3 + 2],
        );
        if (nr > OUTER) {
          const factor = INNER / nr;
          positions[i * 3] *= factor;
          positions[i * 3 + 1] *= factor;
          positions[i * 3 + 2] *= factor;
        }
      }
      posAttr.needsUpdate = true;
    },
  };
}
