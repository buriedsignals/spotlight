// Tiny value-noise FBM. Used CPU-side to pre-displace the terrain and to
// sample height when placing filaments so they sit on the surface.
// The GLSL fragment shader for the terrain uses its own matching fbm.

function hash2(x: number, y: number): number {
  const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return s - Math.floor(s);
}

function smooth(t: number): number {
  return t * t * (3 - 2 * t);
}

export function valueNoise2(x: number, y: number): number {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const xf = x - xi;
  const yf = y - yi;
  const u = smooth(xf);
  const v = smooth(yf);
  const a = hash2(xi, yi);
  const b = hash2(xi + 1, yi);
  const c = hash2(xi, yi + 1);
  const d = hash2(xi + 1, yi + 1);
  return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v;
}

export function fbm2(x: number, y: number, octaves = 5): number {
  let f = 0;
  let amp = 0.5;
  let freq = 1;
  let norm = 0;
  for (let i = 0; i < octaves; i++) {
    f += amp * valueNoise2(x * freq, y * freq);
    norm += amp;
    amp *= 0.5;
    freq *= 2;
  }
  return f / norm;
}

export function terrainHeight(x: number, z: number): number {
  // Same signal for CPU and filament placement.
  const big = fbm2(x * 0.035, z * 0.035, 4);
  const small = fbm2(x * 0.12 + 10, z * 0.12 - 7, 3);
  return (big - 0.5) * 6.0 + (small - 0.5) * 0.8;
}
