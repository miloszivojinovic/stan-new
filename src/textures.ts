// Procedural canvas textures (no downloads). Each texture covers `size` metres; UVs are in metres, so the
// material sets repeat = 1 / size.
import * as THREE from 'three';

type Kind = 'wood-planks' | 'plaster' | 'walnut' | 'tiles' | 'rug' | 'door-inlay' | 'oak' | 'fabric' | 'stone' | 'checker' | 'plank-tiles';

export interface TexOpts {
  tile?: [number, number];
  grout?: string;
  alt?: string;
}

function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function hexToRgb(hex: string): [number, number, number] {
  const c = new THREE.Color(hex);
  return [c.r * 255, c.g * 255, c.b * 255];
}

function shade([r, g, b]: [number, number, number], f: number): string {
  const k = (v: number) => Math.max(0, Math.min(255, Math.round(v * f)));
  return `rgb(${k(r)},${k(g)},${k(b)})`;
}

const cache = new Map<string, THREE.Texture>();

export function makeTexture(kind: Kind, color: string, sizeM: number, opts: TexOpts = {}): { map: THREE.Texture; size: number } {
  const key = `${kind}|${color}|${sizeM}|${JSON.stringify(opts)}`;
  const hit = cache.get(key);
  if (hit) return { map: hit, size: sizeM };
  const N = 1024;
  const cv = document.createElement('canvas');
  cv.width = cv.height = N;
  const g = cv.getContext('2d')!;
  const base = hexToRgb(color);
  const r = rng(kind.length * 7919 + Math.round(sizeM * 1000));
  g.fillStyle = shade(base, 1);
  g.fillRect(0, 0, N, N);
  const px = N / sizeM; // pixels per metre

  if (kind === 'wood-planks' || kind === 'oak') {
    const plankW = (kind === 'oak' ? 0.12 : 0.19) * px;
    const plankL = 1.2 * px;
    for (let y = 0; y < N; y += plankW) {
      let x = -r() * plankL;
      while (x < N) {
        const f = 0.82 + r() * 0.3;
        g.fillStyle = shade(base, f);
        g.fillRect(x, y, plankL, plankW);
        for (let k = 0; k < 18; k++) { // grain
          const gy = y + r() * plankW;
          g.strokeStyle = shade(base, f * (0.8 + r() * 0.35));
          g.globalAlpha = 0.35;
          g.lineWidth = 0.6 + r() * 1.6;
          g.beginPath();
          g.moveTo(x, gy);
          g.bezierCurveTo(x + plankL * 0.3, gy + (r() - 0.5) * 6, x + plankL * 0.7, gy + (r() - 0.5) * 6, x + plankL, gy + (r() - 0.5) * 4);
          g.stroke();
        }
        g.globalAlpha = 1;
        g.fillStyle = shade(base, 0.55);
        g.fillRect(x, y, 1.5, plankW);
        x += plankL;
      }
      g.fillStyle = shade(base, 0.6);
      g.fillRect(0, y, N, 1.2);
    }
  } else if (kind === 'walnut') {
    // vertical straight grain with slow colour drift and darker streaks (veneer look)
    let drift = 1;
    for (let x = 0; x < N; x++) {
      drift += (r() - 0.5) * 0.02;
      drift = Math.max(0.82, Math.min(1.18, drift));
      const streak = r() < 0.08 ? 0.72 + r() * 0.15 : 1;
      g.fillStyle = shade(base, drift * streak * (0.94 + 0.12 * Math.sin(x * 0.21)));
      g.fillRect(x, 0, 1, N);
    }
    g.globalAlpha = 0.18;
    for (let k = 0; k < 60; k++) { // faint cathedral arcs
      g.strokeStyle = shade(base, 0.7);
      g.lineWidth = 1;
      const x0 = r() * N;
      g.beginPath();
      g.moveTo(x0, r() * N);
      g.quadraticCurveTo(x0 + (r() - 0.5) * 40, r() * N, x0 + (r() - 0.5) * 20, r() * N);
      g.stroke();
    }
    g.globalAlpha = 1;
  } else if (kind === 'plaster' || kind === 'stone' || kind === 'fabric') {
    const img = g.getImageData(0, 0, N, N);
    const amp = kind === 'plaster' ? 10 : kind === 'fabric' ? 14 : 18;
    for (let i = 0; i < img.data.length; i += 4) {
      const n = (r() - 0.5) * amp;
      img.data[i] += n;
      img.data[i + 1] += n;
      img.data[i + 2] += n;
    }
    g.putImageData(img, 0, 0);
    if (kind === 'stone') { // soft veins
      g.globalAlpha = 0.25;
      for (let k = 0; k < 10; k++) {
        g.strokeStyle = shade(base, 0.7);
        g.lineWidth = 1 + r() * 3;
        g.beginPath();
        g.moveTo(r() * N, 0);
        g.bezierCurveTo(r() * N, N * 0.3, r() * N, N * 0.7, r() * N, N);
        g.stroke();
      }
      g.globalAlpha = 1;
    }
  } else if (kind === 'tiles' || kind === 'checker' || kind === 'plank-tiles') {
    // large-format tiles with soft marble-like variation; checker alternates two colours; plank tiles are
    // wood-look strips laid with a running bond
    const [tw, th] = (opts.tile ?? [0.6, 0.6]).map((v) => v * px);
    const alt = hexToRgb(opts.alt ?? '#1a1a1a');
    for (let j = 0, y = 0; y < N; y += th, j++) {
      const off = kind === 'plank-tiles' ? ((j * 0.37) % 1) * tw : 0;
      for (let i = -1, x = -off; x < N; x += tw, i++) {
        const dark = kind === 'checker' && (i + j) % 2 === 0;
        const f = 0.93 + r() * 0.12;
        g.fillStyle = shade(dark ? alt : base, f);
        g.fillRect(x, y, tw, th);
        if (kind === 'plank-tiles') {
          for (let k = 0; k < 10; k++) {
            g.globalAlpha = 0.25;
            g.strokeStyle = shade(base, f * (0.7 + r() * 0.3));
            g.beginPath();
            const gy = y + r() * th;
            g.moveTo(x, gy);
            g.lineTo(x + tw, gy + (r() - 0.5) * 4);
            g.stroke();
          }
          g.globalAlpha = 1;
        } else {
          g.globalAlpha = 0.12;
          g.strokeStyle = shade(dark ? alt : base, 0.75);
          for (let k = 0; k < 3; k++) {
            g.beginPath();
            g.moveTo(x + r() * tw, y);
            g.bezierCurveTo(x + r() * tw, y + th * 0.4, x + r() * tw, y + th * 0.6, x + r() * tw, y + th);
            g.stroke();
          }
          g.globalAlpha = 1;
        }
      }
    }
    g.strokeStyle = opts.grout ?? shade(base, 0.8);
    g.lineWidth = 2;
    for (let j = 0, y = 0; y <= N; y += th, j++) {
      g.beginPath(); g.moveTo(0, y); g.lineTo(N, y); g.stroke();
      const off = kind === 'plank-tiles' ? ((j * 0.37) % 1) * tw : 0;
      for (let x = -off; x <= N; x += tw) { g.beginPath(); g.moveTo(x, y); g.lineTo(x, y + th); g.stroke(); }
    }
  } else if (kind === 'rug') {
    // low pile with soft brown streaks running one way
    const img = g.getImageData(0, 0, N, N);
    for (let i = 0; i < img.data.length; i += 4) {
      const n = (r() - 0.5) * 16;
      img.data[i] += n; img.data[i + 1] += n; img.data[i + 2] += n;
    }
    g.putImageData(img, 0, 0);
    for (let k = 0; k < 420; k++) {
      g.globalAlpha = 0.12 + r() * 0.25;
      g.fillStyle = shade(base, 0.45 + r() * 0.3);
      const y = r() * N;
      g.fillRect(r() * N - N * 0.2, y, N * (0.1 + r() * 0.5), 1 + r() * 3);
    }
    g.globalAlpha = 1;
  } else if (kind === 'door-inlay') {
    // white leaf with thin dark diagonal inlay lines, like the renders (texture spans the whole leaf: 1 x 1)
    g.strokeStyle = 'rgb(30,30,30)';
    g.lineWidth = 3;
    const L = (pts: number[][]) => { g.beginPath(); g.moveTo(pts[0][0] * N, pts[0][1] * N); for (const p of pts.slice(1)) g.lineTo(p[0] * N, p[1] * N); g.stroke(); };
    L([[0.35, 0], [0.62, 0.27]]);
    L([[1, 0.12], [0.12, 0.42]]);
    L([[0, 0.42], [1, 0.66]]);
    L([[0.72, 0.53], [0.18, 0.92]]);
    L([[0.18, 0.79], [0.55, 1]]);
    L([[1, 0.82], [0.38, 1]]);
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.anisotropy = 8;
  cache.set(key, tex);
  return { map: tex, size: sizeM };
}
