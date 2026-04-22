import * as THREE from "three";

export const MONO = { radius: 3.5, h: 7 };

export interface MonolithHandle {
  group: THREE.Group;
  update(t: number, camera: THREE.Camera): void;
}

// Dark stone block with a jagged vertical crack emitting a bright shaft of
// light. Composed of:
//   1. Stone cube   — fragment shader computes distance to a noisy YZ-plane
//      "fissure"; inside = emissive, outside = dark rocky surface
//   2. Light shaft  — billboarded quad, additive, always facing the camera
//   3. Soft halo    — larger billboard behind the cube for volumetric bloom
export function createMonolith(): MonolithHandle {
  const group = new THREE.Group();

  // ---------------------------------------------------------------- stone
  const cubeGeom = new THREE.IcosahedronGeometry(MONO.radius, 5);
  const cubeMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uStone: { value: new THREE.Color(0x06070a) },
      uCrack: { value: new THREE.Color(0xcccccc) },
      uGlow: { value: new THREE.Color(0x707070) },
      uRevealStart: { value: 0 }, // time (sec) when the wave starts
      uRevealSpeed: { value: 1.8 },
      uRevealOrigin: { value: new THREE.Vector3(0, MONO.radius, 0) },
    },
    vertexShader: /* glsl */ `
      varying vec3 vObjPos;
      varying vec3 vNormal;
      varying vec3 vWorldPos;
      void main(){
        vObjPos = position;
        vNormal = normalize(mat3(modelMatrix) * normal);
        vec4 wp = modelMatrix * vec4(position, 1.0);
        vWorldPos = wp.xyz;
        gl_Position = projectionMatrix * viewMatrix * wp;
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      varying vec3 vObjPos;
      varying vec3 vNormal;
      varying vec3 vWorldPos;
      uniform float uTime;
      uniform vec3 uStone;
      uniform vec3 uCrack;
      uniform vec3 uGlow;
      uniform float uRevealStart;
      uniform float uRevealSpeed;
      uniform vec3 uRevealOrigin;

      float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float vnoise(vec2 p){
        vec2 i = floor(p), f = fract(p);
        vec2 u = f * f * (3.0 - 2.0 * f);
        float a = hash(i);
        float b = hash(i + vec2(1.0, 0.0));
        float c = hash(i + vec2(0.0, 1.0));
        float d = hash(i + vec2(1.0, 1.0));
        return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
      }
      float fbm(vec2 p){
        float f = 0.0, amp = 0.5;
        for (int i = 0; i < 5; i++){ f += amp * vnoise(p); p *= 2.03; amp *= 0.5; }
        return f;
      }

      // 3D hash for Voronoi cell offsets
      vec3 hash3(vec3 p){
        p = vec3(
          dot(p, vec3(127.1, 311.7, 74.7)),
          dot(p, vec3(269.5, 183.3, 246.1)),
          dot(p, vec3(113.5, 271.9, 124.6))
        );
        return fract(sin(p) * 43758.5453);
      }

      // Voronoi edge distance: sqrt(F2) - sqrt(F1). ≈ 0 on cell borders,
      // larger inside cells. This is the polyhedral-shatter crack network.
      float worleyEdges(vec3 p){
        vec3 ip = floor(p);
        vec3 fp = fract(p);
        float d1 = 8.0;
        float d2 = 8.0;
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

      // Randomly fracture the entire cube: 3D Voronoi gives a polyhedral
      // break-up; a low-freq domain warp bends the cell edges organically
      // so fractures don't look like a regular tessellation.
      float crackField(vec3 p){
        vec3 warp = vec3(
          fbm(vec2(p.y * 0.9, p.z * 0.9)),
          fbm(vec2(p.x * 0.9 + 4.3, p.z * 0.9 + 1.7)),
          fbm(vec2(p.x * 0.9 + 9.1, p.y * 0.9 + 7.2))
        ) - 0.5;
        vec3 vp = p * 0.55 + warp * 0.35;
        return worleyEdges(vp);
      }

      void main(){
        vec3 n = normalize(vNormal);

        // rocky surface grain
        float g1 = fbm(vObjPos.xy * 3.5 + vObjPos.z);
        float g2 = fbm(vObjPos.yz * 2.8 + vObjPos.x);
        float grain = (g1 + g2) * 0.5;
        vec3 stone = uStone * (0.45 + grain * 1.6);

        // light-facing shading — very subtle
        vec3 ldir = normalize(vec3(0.3, 0.75, 0.55));
        float lam = clamp(dot(n, ldir), 0.0, 1.0);
        vec3 base = stone * (0.35 + 0.65 * lam);

        // fine hairline fractures scattered over the surface
        float hair = smoothstep(0.68, 0.78, fbm(vObjPos.xy * 9.0 + vObjPos.zx * 6.0));
        base += uGlow * hair * 0.04;

        // polyhedral fracture network
        float cd = crackField(vObjPos);
        float aa = fwidth(cd) * 1.6;
        float core = 1.0 - smoothstep(0.02, 0.02 + aa, cd);
        float halo = exp(-cd * cd * 220.0);
        float wide = exp(-cd * cd * 40.0);

        // progressive reveal: a wavefront expands from uRevealOrigin, with
        // per-position jitter so the boundary is organic rather than a
        // clean sphere-slice.
        float distFromStart = length(vObjPos - uRevealOrigin);
        float jitter = (fbm(vec2(vObjPos.x * 1.3 + vObjPos.z * 0.8,
                                 vObjPos.y * 1.1 - vObjPos.z * 0.6)) - 0.5) * 1.4;
        float warpedDist = distFromStart + jitter;
        float front = max(uTime - uRevealStart, 0.0) * uRevealSpeed;
        float revealed = smoothstep(front + 0.4, front - 0.6, warpedDist);
        // bright leading edge — hot white when a fracture just opened
        float tip = exp(-pow(warpedDist - front, 2.0) * 3.5) * revealed;

        // slight per-point pulse so fractures breathe once revealed
        float puls = 0.9 + 0.1 * sin(uTime * 1.5 + vObjPos.x * 2.0 + vObjPos.y * 3.0);

        // tight, low-key emission — cracks read as subtle fissures, not beacons
        vec3 emit = uCrack * core * 1.2 * puls
                  + uGlow  * halo * 0.22;
        emit *= revealed;
        emit += uCrack * core * tip * 0.5;

        gl_FragColor = vec4(base + emit, 1.0);
      }
    `,
  });
  const cube = new THREE.Mesh(cubeGeom, cubeMat);
  group.add(cube);

  // ---------------------------------------------------------------- shaft
  // Narrow vertical bright beam billboarded toward the camera. depthTest is
  // enabled so the cube occludes the central portion — only the parts of
  // the beam that extend *past* the top/bottom of the cube remain visible,
  // reading as light escaping out of the crack.
  const shaftGeom = new THREE.PlaneGeometry(3, MONO.h * 3.2);
  const shaftMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uColor: { value: new THREE.Color(0xd8e8ff) },
    },
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      void main(){
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      precision highp float;
      varying vec2 vUv;
      uniform float uTime;
      uniform vec3 uColor;
      void main(){
        vec2 c = vUv - 0.5;
        // razor-bright central column
        float column = exp(-c.x * c.x * 180.0);
        // wide soft glow
        float glow = exp(-c.x * c.x * 18.0);
        // additional faint rays with jitter
        float rays = exp(-c.x * c.x * 7.0) * (0.5 + 0.5 * sin(c.y * 40.0 + uTime));
        // top/bottom fade so shaft doesn't fill screen
        float vFade = smoothstep(0.5, 0.05, abs(c.y));
        float flicker = 0.88 + 0.12 * sin(uTime * 6.0 + vUv.y * 18.0);
        float intensity = (column * .8 + glow * 0.35 + rays * 0.18) * vFade * flicker;
        gl_FragColor = vec4(uColor * intensity, intensity);
      }
    `,
  });
  const shaft = new THREE.Mesh(shaftGeom, shaftMat);
  shaft.renderOrder = 2;
  shaft.visible = false; // disabled — too bright for the matte ambience
  group.add(shaft);

  return {
    group,
    update(t, camera) {
      cubeMat.uniforms.uTime.value = t;
      shaftMat.uniforms.uTime.value = t;
      // shaft billboards toward camera
      shaft.quaternion.copy(camera.quaternion);
    },
  };
}
