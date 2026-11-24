// Builds a three.js scene graph for one room from data/rooms.json. No coordinates live here: everything comes
// from the room data. Plan frame -> world: x = x, z = z, y = up.
import * as THREE from 'three';
import { RectAreaLightUniformsLib } from 'three/examples/jsm/lights/RectAreaLightUniformsLib.js';
import type { Element, LightDef, MaterialDef, Opening, Room, V2, Wall } from './types';
import { makeTexture } from './textures';

RectAreaLightUniformsLib.init();

export interface Pickable {
  kind: 'element' | 'opening' | 'wall' | 'floor' | 'ceiling';
  title: string;
  lines: string[];
}

export interface BuiltRoom {
  group: THREE.Group;
  ceiling: THREE.Object3D;
  colliders: [V2, V2][]; // 2D segments for walk collision
  center: THREE.Vector3;
  size: THREE.Vector3;
}

const cm = (m: number) => `${(m * 100).toFixed(1)} cm`;

export class MaterialLib {
  private cache = new Map<string, THREE.Material>();
  constructor(private defs: Record<string, MaterialDef>) {}

  get(name: string | undefined, fallback = '#cccccc'): THREE.Material {
    const key = name ?? '__fallback';
    const hit = this.cache.get(key);
    if (hit) return hit;
    const d: MaterialDef = (name && this.defs[name]) || { color: name && name.startsWith('#') ? name : fallback };
    let m: THREE.Material;
    if (d.transmission !== undefined || d.opacity !== undefined) {
      m = new THREE.MeshPhysicalMaterial({
        color: d.color, roughness: d.roughness ?? 0.05, metalness: 0, transparent: true,
        opacity: d.opacity ?? 1, transmission: d.transmission ?? 0, side: THREE.DoubleSide, depthWrite: (d.opacity ?? 1) > 0.9,
        envMapIntensity: 0.15, // keep glass and sheers from veiling what is behind them with reflected haze
      });
    } else {
      const sm = new THREE.MeshStandardMaterial({ color: d.color, roughness: d.roughness ?? 0.8, metalness: d.metalness ?? 0 });
      if (d.metalness !== undefined && d.metalness >= 0.95) sm.envMapIntensity = 1.6; // mirrors and chrome
      if (d.texture) {
        const size = d.textureScale ?? 1;
        const { map } = makeTexture(d.texture, d.color, size, { tile: d.tile, grout: d.groutColor, alt: d.altColor });
        const t = map.clone();
        t.needsUpdate = true;
        if (d.texture !== 'door-inlay') t.repeat.set(1 / size, 1 / size);
        sm.map = t;
        sm.color.set('#ffffff');
      }
      if (d.emissive) {
        sm.emissive.set(d.emissive);
        sm.emissiveIntensity = d.emissiveIntensity ?? 1;
      }
      m = sm;
    }
    m.name = name ?? 'fallback';
    this.cache.set(key, m);
    return m;
  }
}

// ---------------------------------------------------------------------------------------------- geometry helpers
function signedArea(poly: V2[]): number {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, z0] = poly[i];
    const [x1, z1] = poly[(i + 1) % poly.length];
    a += x0 * z1 - x1 * z0;
  }
  return a / 2;
}

export function pointInPolygon(x: number, z: number, poly: V2[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i];
    const [xj, zj] = poly[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}

interface WallFrame {
  ax: number; az: number; ux: number; uz: number; nx: number; nz: number; len: number; rotY: number;
}

function wallFrame(w: Wall, poly: V2[]): WallFrame {
  const [ax, az] = w.a;
  const [bx, bz] = w.b;
  const len = Math.hypot(bx - ax, bz - az);
  const ux = (bx - ax) / len;
  const uz = (bz - az) / len;
  let nx = -uz;
  let nz = ux;
  const mx = (ax + bx) / 2;
  const mz = (az + bz) / 2;
  if (!pointInPolygon(mx + nx * 0.05, mz + nz * 0.05, poly)) {
    nx = -nx;
    nz = -nz;
  }
  return { ax, az, ux, uz, nx, nz, len, rotY: Math.atan2(-uz, ux) };
}

/** Texture coordinates in metres on a box, measured from a shared origin, so tile joints keep their real size and
 * line up across neighbouring boxes. (cx, cy, cz) = the box centre in the frame whose axes the box follows. */
function metreUVs(geo: THREE.BufferGeometry, cx: number, cy: number, cz: number): void {
  const pos = geo.attributes.position as THREE.BufferAttribute;
  const nor = geo.attributes.normal as THREE.BufferAttribute;
  const uv = geo.attributes.uv as THREE.BufferAttribute;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i) + cx;
    const y = pos.getY(i) + cy;
    const z = pos.getZ(i) + cz;
    if (Math.abs(nor.getX(i)) > 0.5) uv.setXY(i, z, y);
    else if (Math.abs(nor.getY(i)) > 0.5) uv.setXY(i, x, z);
    else uv.setXY(i, x, y);
  }
  uv.needsUpdate = true;
}

/** Box placed along a wall: t along the wall, d = offset from the inner face into the room (negative = into the
 * wall), y vertical. Sizes: along, height, depth. */
function wallBox(f: WallFrame, t0: number, t1: number, y0: number, y1: number, d0: number, d1: number, mat: THREE.Material, uvMetres = true): THREE.Mesh {
  const g = new THREE.BoxGeometry(t1 - t0, y1 - y0, Math.abs(d1 - d0));
  const tc = (t0 + t1) / 2;
  const dc = (d0 + d1) / 2;
  if (uvMetres) metreUVs(g, tc, (y0 + y1) / 2, dc);
  const m = new THREE.Mesh(g, mat);
  m.position.set(f.ax + f.ux * tc + f.nx * dc, (y0 + y1) / 2, f.az + f.uz * tc + f.nz * dc);
  m.rotation.y = f.rotY;
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

function planeShape(poly: V2[], flipZ: boolean): THREE.ShapeGeometry {
  const shape = new THREE.Shape(poly.map(([x, z]) => new THREE.Vector2(x, flipZ ? -z : z)));
  return new THREE.ShapeGeometry(shape);
}

function extrude(poly: V2[], y0: number, y1: number, mat: THREE.Material | THREE.Material[]): THREE.Mesh {
  const shape = new THREE.Shape(poly.map(([x, z]) => new THREE.Vector2(x, -z)));
  const g = new THREE.ExtrudeGeometry(shape, { depth: y1 - y0, bevelEnabled: false });
  g.rotateX(-Math.PI / 2); // shape (x, -z) in XY, extruded along +Z -> along +Y after rotation
  g.translate(0, y0, 0);
  const m = new THREE.Mesh(g, mat);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

function bbox(poly: V2[]): { x0: number; x1: number; z0: number; z1: number } {
  const xs = poly.map((p) => p[0]);
  const zs = poly.map((p) => p[1]);
  return { x0: Math.min(...xs), x1: Math.max(...xs), z0: Math.min(...zs), z1: Math.max(...zs) };
}

function box(w: number, h: number, d: number, mat: THREE.Material, x: number, y: number, z: number): THREE.Mesh {
  const geo = new THREE.BoxGeometry(w, h, d);
  metreUVs(geo, x, y, z);
  const m = new THREE.Mesh(geo, mat);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

/** Edges of an element footprint that are free (not against a wall): front faces for doors, grooves etc. */
function freeEdges(poly: V2[], room: Room): [V2, V2][] {
  const out: [V2, V2][] = [];
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i];
    const b = poly[(i + 1) % poly.length];
    const mx = (a[0] + b[0]) / 2;
    const mz = (a[1] + b[1]) / 2;
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (L < 0.05) continue;
    let nx = -(b[1] - a[1]) / L;
    let nz = (b[0] - a[0]) / L;
    if (pointInPolygon(mx + nx * 0.02, mz + nz * 0.02, poly)) { nx = -nx; nz = -nz; }
    // free if the point 5 cm in front is inside the room
    if (pointInPolygon(mx + nx * 0.05, mz + nz * 0.05, room.polygon)) out.push([a, b]);
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- builder
export function buildRoom(room: Room, pickables: Map<THREE.Object3D, Pickable>): BuiltRoom {
  const lib = new MaterialLib(room.materials);
  const group = new THREE.Group();
  group.name = room.id;
  const H = room.ceilingHeight;
  const poly = room.polygon;
  const ccw = signedArea(poly) > 0;
  const colliders: [V2, V2][] = [];

  // floor and ceiling (UVs in metres)
  const floor = new THREE.Mesh(planeShape(poly, true), lib.get(room.floorMaterial));
  floor.geometry.rotateX(-Math.PI / 2);
  floor.receiveShadow = true;
  group.add(floor);
  pickables.set(floor, { kind: 'floor', title: 'Floor', lines: [`material: ${room.floorMaterial}`, `source: ${room.materials[room.floorMaterial]?.source ?? '?'}`] });
  const ceiling = new THREE.Mesh(planeShape(poly, false), lib.get(room.ceilingMaterial));
  ceiling.geometry.rotateX(Math.PI / 2);
  ceiling.position.y = H;
  ceiling.receiveShadow = true;
  group.add(ceiling);
  pickables.set(ceiling, { kind: 'ceiling', title: 'Ceiling', lines: [`height ${H.toFixed(2)} m`, 'source: owner'] });
  for (const z of room.ceilingZones ?? []) { // lower ceiling over part of the room, as a soffit block up to H
    const soffit = extrude(z.polygon, z.height, H + 0.002, lib.get(room.ceilingMaterial));
    soffit.name = `ceiling-zone-${z.id}`;
    soffit.castShadow = false;
    soffit.position.y = -H; // child of the ceiling (at y = H), so it hides with it in the orbit and plan views
    ceiling.add(soffit);
    pickables.set(soffit, { kind: 'ceiling', title: `Ceiling (${z.name})`, lines: [`height ${z.height.toFixed(2)} m`, 'source: estimated (render model)'] });
  }

  // walls with openings
  const n = poly.length;
  const convex = poly.map((p, i) => {
    const prev = poly[(i - 1 + n) % n];
    const next = poly[(i + 1) % n];
    const cross = (p[0] - prev[0]) * (next[1] - p[1]) - (p[1] - prev[1]) * (next[0] - p[0]);
    return cross > 0 === ccw;
  });
  room.walls.forEach((w, wi) => {
    const f = wallFrame(w, poly);
    const mat = lib.get(w.material ?? room.wallMaterial);
    const t = w.thickness;
    const ext0 = convex[wi] ? t : 0; // wall i runs from vertex i to vertex i+1
    const ext1 = convex[(wi + 1) % n] ? t : 0;
    const ops = room.openings.filter((o) => o.wall === w.id).sort((a, b) => a.t0 - b.t0);
    const wallGroup = new THREE.Group();
    let cur = -ext0;
    const addSolid = (a: number, b: number) => {
      if (b - a > 1e-4) wallGroup.add(wallBox(f, a, b, 0, H, -t, 0, mat));
    };
    for (const o of ops) {
      addSolid(cur, o.t0);
      if (o.sill > 0) wallGroup.add(wallBox(f, o.t0, o.t1, 0, o.sill, -t, 0, mat));
      if (o.head < H) wallGroup.add(wallBox(f, o.t0, o.t1, o.head, H, -t, 0, mat));
      cur = o.t1;
      group.add(buildOpening(o, f, t, lib, pickables, room));
    }
    addSolid(cur, f.len + ext1);
    wallGroup.children.forEach((c) => pickables.set(c, { kind: 'wall', title: `Wall (${w.side})`, lines: [`length ${cm(f.len)}`, `thickness ${cm(t)}${w.exterior ? ' (exterior, as drawn: assumed)' : ' (interior: owner)'}`] }));
    group.add(wallGroup);
    colliders.push([w.a, w.b]);
    // skirting along the solid parts
    if (room.skirting) {
      const sm = lib.get(room.skirting.material);
      let s = 0;
      for (const o of [...ops, { t0: f.len, t1: f.len, type: 'end' } as unknown as Opening]) {
        const end = o.type === 'window' ? s : o.t0;
        if (o.type !== 'window' && end - s > 0.02) group.add(wallBox(f, s, end, 0, room.skirting.height, 0, 0.012, sm));
        if (o.type !== 'window') s = o.t1;
      }
    }
  });

  // elements, lights
  for (const e of room.elements) {
    const obj = buildElement(e, room, lib);
    if (!obj) continue;
    group.add(obj);
    const dims = describeSize(e);
    obj.traverse((c) => pickables.set(c, {
      kind: 'element', title: `${e.name}${e.label ? ` (${e.label})` : ''}`,
      lines: [dims, ...Object.entries(e.sources).map(([k, v]) => `${k}: ${v}`), ...(e.notes ? [e.notes] : [])],
    }));
    if (e.footprint && (e.y0 ?? 0) < 0.5 && !['rug', 'curtain', 'track', 'tv', 'panel'].includes(e.type)) {
      const p = e.footprint;
      for (let i = 0; i < p.length; i++) colliders.push([p[i], p[(i + 1) % p.length]]);
    }
    if (e.type === 'parts') { // walk collision for parts reaching below 1 m (wardrobes, shelves, table tops), not thin trim
      const parts = ((e.params?.parts as unknown) as { x: number[]; y: number[]; z: number[] }[] | undefined) ?? [];
      for (const q of parts) {
        if (q.y[0] > 1.0 || q.y[1] < 0.05 || q.x[1] - q.x[0] < 0.1 || q.z[1] - q.z[0] < 0.1) continue; // not floor tiles or trim
        const r: V2[] = [[q.x[0], q.z[0]], [q.x[1], q.z[0]], [q.x[1], q.z[1]], [q.x[0], q.z[1]]];
        for (let i = 0; i < 4; i++) colliders.push([r[i], r[(i + 1) % 4]]);
      }
    }
  }
  for (const l of room.lights) {
    const lo = buildLight(l, lib);
    if (l.group) lo.userData.group = l.group;
    group.add(lo);
  }

  const bb = bbox(poly);
  return {
    group, ceiling, colliders,
    center: new THREE.Vector3((bb.x0 + bb.x1) / 2, H / 2, (bb.z0 + bb.z1) / 2),
    size: new THREE.Vector3(bb.x1 - bb.x0, H, bb.z1 - bb.z0),
  };
}

function describeSize(e: Element): string {
  if (e.footprint) {
    const b = bbox(e.footprint);
    return `footprint ${cm(b.x1 - b.x0)} x ${cm(b.z1 - b.z0)}, height ${cm((e.y1 ?? 0) - (e.y0 ?? 0))} (y ${(e.y0 ?? 0).toFixed(2)}-${(e.y1 ?? 0).toFixed(2)} m)`;
  }
  if (e.size) return `size ${cm(e.size[0])} x ${cm(e.size[1])} x ${cm(e.size[2])}`;
  return '';
}

// ---------------------------------------------------------------------------------------------- openings
function buildOpening(o: Opening, f: WallFrame, t: number, lib: MaterialLib, pickables: Map<THREE.Object3D, Pickable>, room: Room): THREE.Group {
  const g = new THREE.Group();
  const frame = lib.get(o.frameMaterial ?? 'white');
  const w = o.t1 - o.t0;
  if (o.type === 'door') {
    const fw = 0.04; // frame (jamb) thickness
    const cw = 0.07; // casing width
    g.add(wallBox(f, o.t0, o.t0 + fw, 0, o.head, -t - 0.005, 0.005, frame));
    g.add(wallBox(f, o.t1 - fw, o.t1, 0, o.head, -t - 0.005, 0.005, frame));
    g.add(wallBox(f, o.t0, o.t1, o.head - fw, o.head, -t - 0.005, 0.005, frame));
    g.add(wallBox(f, o.t0 - cw, o.t0, 0, o.head + cw, 0, 0.015, frame)); // casings on the room side
    g.add(wallBox(f, o.t1, o.t1 + cw, 0, o.head + cw, 0, 0.015, frame));
    g.add(wallBox(f, o.t0 - cw, o.t1 + cw, o.head, o.head + cw, 0, 0.015, frame));
    const lw = o.leafWidth ?? w - 2 * fw;
    const lt0 = o.t0 + (w - lw) / 2;
    const leaf = wallBox(f, lt0, lt0 + lw, 0.008, o.head - fw, -0.045, -0.005, lib.get(o.leafMaterial ?? 'door'), false);
    g.add(leaf);
    const latch = o.hinge === 't1' ? lt0 + 0.07 : lt0 + lw - 0.07;
    const black = lib.get('black');
    g.add(wallBox(f, latch - 0.01, latch + 0.01, 1.0, 1.06, -0.005, 0.05, black));
    const dir = o.hinge === 't1' ? 1 : -1;
    g.add(wallBox(f, Math.min(latch, latch + dir * 0.13), Math.max(latch, latch + dir * 0.13), 1.04, 1.06, 0.035, 0.05, black));
  } else if (o.type === 'window' || o.type === 'balcony-door') {
    const fw = 0.07;
    const dmid = -t * 0.45;
    const y0 = o.sill;
    const y1 = o.head - (o.box ?? 0); // frame top; a roller-shutter box fills the rest of the opening
    if (o.box) g.add(wallBox(f, o.t0, o.t1, y1, o.head, dmid - 0.035, dmid + 0.035, lib.get('shutter-box', '#ddd8cf')));
    g.add(wallBox(f, o.t0, o.t0 + fw, y0, y1, dmid - 0.035, dmid + 0.035, frame));
    g.add(wallBox(f, o.t1 - fw, o.t1, y0, y1, dmid - 0.035, dmid + 0.035, frame));
    g.add(wallBox(f, o.t0, o.t1, y1 - fw, y1, dmid - 0.035, dmid + 0.035, frame));
    g.add(wallBox(f, o.t0, o.t1, y0, y0 + fw, dmid - 0.035, dmid + 0.035, frame));
    const sashes = o.sashes ?? 1;
    for (let k = 1; k < sashes; k++) {
      const tm = o.t0 + (w * k) / sashes;
      g.add(wallBox(f, tm - 0.05, tm + 0.05, y0, y1, dmid - 0.035, dmid + 0.035, frame));
    }
    const glass = wallBox(f, o.t0 + fw, o.t1 - fw, y0 + fw, y1 - fw, dmid - 0.004, dmid + 0.004, lib.get('glass'));
    glass.castShadow = false;
    g.add(glass);
    if (o.type === 'window' && y0 > 0.05) {
      g.add(wallBox(f, o.t0 - 0.02, o.t1 + 0.02, y0 - 0.02, y0, dmid, 0.05, lib.get(o.frameMaterial ?? 'white'))); // inner sill
    }
    // bright view outside: an unlit painted landscape (sky, distant hills, fields) a little outside the wall
    if (room.outside) {
      const tex = landscape(room.outside.sky, room.outside.ground);
      const bd = new THREE.Mesh(new THREE.PlaneGeometry(w + 4, 3.6), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
      const tc = (o.t0 + o.t1) / 2;
      bd.position.set(f.ax + f.ux * tc - f.nx * (t + 1.5), 1.45, f.az + f.uz * tc - f.nz * (t + 1.5));
      bd.rotation.y = Math.atan2(f.nx, f.nz);
      g.add(bd);
    }
  }
  g.traverse((c) => pickables.set(c, {
    kind: 'opening', title: o.name,
    lines: [`width ${cm(o.t1 - o.t0)}, sill ${cm(o.sill)}, head ${cm(o.head)}`, ...Object.entries(o.sources).map(([k, v]) => `${k}: ${v}`), ...(o.notes ? [o.notes] : [])],
  }));
  return g;
}

let landscapeTex: THREE.Texture | null = null;
function landscape(sky: string, ground: string): THREE.Texture {
  if (landscapeTex) return landscapeTex;
  const W = 1024;
  const H = 512;
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const g = cv.getContext('2d')!;
  let s = 7;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const horizon = H * 0.5;
  const sk = g.createLinearGradient(0, 0, 0, horizon);
  sk.addColorStop(0, sky);
  sk.addColorStop(1, '#eef2f2');
  g.fillStyle = sk;
  g.fillRect(0, 0, W, horizon);
  for (const [base, amp, col] of [[horizon - 20, 26, '#9aa7ae'], [horizon - 6, 16, '#8e9a86']] as const) { // hills
    g.fillStyle = col;
    g.beginPath();
    g.moveTo(0, H);
    for (let x = 0; x <= W; x += 8) g.lineTo(x, base - amp * (0.5 + 0.5 * Math.sin(x * 0.006 + base) * Math.sin(x * 0.017)));
    g.lineTo(W, H);
    g.fill();
  }
  const gr = g.createLinearGradient(0, horizon, 0, H);
  gr.addColorStop(0, '#b4b08a');
  gr.addColorStop(1, ground);
  g.fillStyle = gr;
  g.fillRect(0, horizon + 4, W, H - horizon);
  for (let k = 0; k < 140; k++) { // fields and bushes
    g.fillStyle = ['#7d8a52', '#a6a26c', '#6a7a45', '#c2b98a'][k % 4];
    g.globalAlpha = 0.5;
    const y = horizon + 6 + rnd() * (H - horizon);
    g.beginPath();
    g.ellipse(rnd() * W, y, 6 + rnd() * 30, 2 + rnd() * 5, 0, 0, Math.PI * 2);
    g.fill();
  }
  g.globalAlpha = 1;
  landscapeTex = new THREE.CanvasTexture(cv);
  landscapeTex.colorSpace = THREE.SRGBColorSpace;
  return landscapeTex;
}

// ---------------------------------------------------------------------------------------------- elements
function buildElement(e: Element, room: Room, lib: MaterialLib): THREE.Object3D | null {
  const mat = (k: string, fb?: string) => lib.get(e.materials?.[k] ?? fb ?? e.material);
  const p = (e.params ?? {}) as Record<string, number | number[] | string | boolean>;
  const g = new THREE.Group();
  g.name = e.id;
  const y0 = e.y0 ?? 0;
  const y1 = e.y1 ?? 0;
  switch (e.type) {
    case 'block':
    case 'wardrobe':
    case 'cabinet': {
      if (!e.footprint) return null;
      g.add(extrude(e.footprint, y0, y1, mat('body')));
      if (e.type !== 'block') {
        // door gaps and black vertical groove handles on the free faces
        const door = Number(p.doorWidth ?? 0.5);
        for (const [a, b] of freeEdges(e.footprint, room)) {
          const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
          const ux = (b[0] - a[0]) / L;
          const uz = (b[1] - a[1]) / L;
          let nx = -uz;
          let nz = ux;
          if (pointInPolygon((a[0] + b[0]) / 2 + nx * 0.02, (a[1] + b[1]) / 2 + nz * 0.02, e.footprint)) { nx = -nx; nz = -nz; }
          const count = Math.max(1, Math.round(L / door));
          const rot = Math.atan2(-uz, ux);
          for (let k = 0; k <= count; k++) {
            const tt = (L * k) / count;
            const gap = new THREE.Mesh(new THREE.BoxGeometry(0.004, y1 - y0 - 0.01, 0.004), mat('gap', 'black'));
            gap.position.set(a[0] + ux * tt + nx * 0.001, (y0 + y1) / 2, a[1] + uz * tt + nz * 0.001);
            gap.rotation.y = rot;
            if (k > 0 && k < count) g.add(gap);
            if (k < count && p.grooves !== false) {
              const hx = a[0] + ux * (tt + (k % 2 === 0 ? L / count - 0.05 : 0.05));
              const hz = a[1] + uz * (tt + (k % 2 === 0 ? L / count - 0.05 : 0.05));
              const hy0 = Number(p.grooveFrom ?? 0.75);
              const hy1 = Number(p.grooveTo ?? 1.35);
              const groove = new THREE.Mesh(new THREE.BoxGeometry(0.012, hy1 - hy0, 0.006), mat('handle', 'black'));
              groove.position.set(hx + nx * 0.002, (hy0 + hy1) / 2, hz + nz * 0.002);
              groove.rotation.y = rot;
              g.add(groove);
            }
          }
        }
      }
      return g;
    }
    case 'open-shelf': {
      if (!e.footprint) return null;
      const b = bbox(e.footprint);
      const th = Number(p.board ?? 0.022);
      const alongX = b.x1 - b.x0 >= b.z1 - b.z0 && p.along !== 'z';
      const body = mat('body');
      const w = b.x1 - b.x0;
      const d = b.z1 - b.z0;
      const cx = (b.x0 + b.x1) / 2;
      const cz = (b.z0 + b.z1) / 2;
      // sides at both ends of the run, back panel at the wall, boards
      if (alongX) {
        g.add(box(th, y1 - y0, d, body, b.x0 + th / 2, (y0 + y1) / 2, cz));
        g.add(box(th, y1 - y0, d, body, b.x1 - th / 2, (y0 + y1) / 2, cz));
      } else {
        g.add(box(w, y1 - y0, th, body, cx, (y0 + y1) / 2, b.z0 + th / 2));
        g.add(box(w, y1 - y0, th, body, cx, (y0 + y1) / 2, b.z1 - th / 2));
      }
      for (const yb of (p.boards as number[] | undefined) ?? [y0 + th / 2, y1 - th / 2]) g.add(box(w, th, d, body, cx, yb, cz));
      const backSide = String(p.back ?? '');
      if (backSide) {
        const bm = mat('back', e.materials?.body);
        if (backSide === 'x0') g.add(box(0.01, y1 - y0, d, bm, b.x0 + 0.005, (y0 + y1) / 2, cz));
        if (backSide === 'x1') g.add(box(0.01, y1 - y0, d, bm, b.x1 - 0.005, (y0 + y1) / 2, cz));
        if (backSide === 'z0') g.add(box(w, y1 - y0, 0.01, bm, cx, (y0 + y1) / 2, b.z0 + 0.005));
        if (backSide === 'z1') g.add(box(w, y1 - y0, 0.01, bm, cx, (y0 + y1) / 2, b.z1 - 0.005));
      }
      return g;
    }
    case 'desk': {
      if (!e.footprint) return null;
      const b = bbox(e.footprint);
      const top = Number(p.top ?? 0.03);
      const w = b.x1 - b.x0;
      const d = b.z1 - b.z0;
      g.add(box(w, top, d, mat('top'), (b.x0 + b.x1) / 2, y1 - top / 2, (b.z0 + b.z1) / 2));
      const frame = mat('frame', 'black');
      const legAlongZ = d > w; // legs at both ends of the long side
      const inset = 0.1;
      const legs = (p.legs as number[] | undefined) ?? null; // measured leg positions along the long side
      for (const s of [0, 1]) {
        if (legAlongZ) {
          const z = legs ? legs[s] : s ? b.z1 - inset : b.z0 + inset;
          g.add(box(0.06, y1 - top, 0.06, frame, (b.x0 + b.x1) / 2, (y1 - top) / 2, z));
          g.add(box(w * 0.8, 0.03, 0.07, frame, (b.x0 + b.x1) / 2, 0.015, z));
        } else {
          const x = legs ? legs[s] : s ? b.x1 - inset : b.x0 + inset;
          g.add(box(0.06, y1 - top, 0.06, frame, x, (y1 - top) / 2, (b.z0 + b.z1) / 2));
          g.add(box(0.07, 0.03, d * 0.8, frame, x, 0.015, (b.z0 + b.z1) / 2));
        }
      }
      if (legAlongZ) g.add(box(0.04, 0.05, d - 2 * inset, frame, (b.x0 + b.x1) / 2, y1 - top - 0.04, (b.z0 + b.z1) / 2));
      else g.add(box(w - 2 * inset, 0.05, 0.04, frame, (b.x0 + b.x1) / 2, y1 - top - 0.04, (b.z0 + b.z1) / 2));
      return g;
    }
    case 'parts': {
      // exact boxes measured from the renders/plan: [{x:[a,b], y:[a,b], z:[a,b], m:material}]
      // optional r: [rx, ry, rz] rotation (radians) about the box centre, e.g. for crossed table legs
      const parts = ((p.parts as unknown) as { x: number[]; y: number[]; z: number[]; m?: string; r?: number[] }[] | undefined) ?? [];
      for (const q of parts) {
        const w = q.x[1] - q.x[0];
        const h = q.y[1] - q.y[0];
        const d = q.z[1] - q.z[0];
        if (w <= 0 || h <= 0 || d <= 0) continue;
        const m = box(w, h, d, lib.get(q.m ?? e.materials?.body ?? e.material), (q.x[0] + q.x[1]) / 2, (q.y[0] + q.y[1]) / 2, (q.z[0] + q.z[1]) / 2);
        if (q.r) m.rotation.set(q.r[0], q.r[1], q.r[2]);
        g.add(m);
      }
      return g;
    }
    case 'box':
    case 'tv':
    case 'panel':
    case 'monitor':
    case 'rug':
    case 'radiator': {
      if (!e.center || !e.size) return null;
      const [sx, sy, sz] = e.size;
      const m = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat('body'));
      m.castShadow = e.type !== 'rug';
      m.receiveShadow = true;
      g.add(m);
      if (e.type === 'radiator') { // vertical fins
        const fins = Math.max(4, Math.round((Math.max(sx, sz)) / 0.05));
        const along = sx >= sz;
        for (let k = 0; k < fins; k++) {
          const t = -0.5 + (k + 0.5) / fins;
          const fin = new THREE.Mesh(new THREE.BoxGeometry(along ? 0.012 : sx + 0.01, sy * 0.96, along ? sz + 0.01 : 0.012), mat('body'));
          fin.position.set(along ? t * sx : 0, 0, along ? 0 : t * sz);
          g.add(fin);
        }
      }
      if (e.type === 'tv' || e.type === 'monitor') {
        const screen = mat('screen', 'screen');
        const face = String(p.face ?? '+x');
        const s = new THREE.Mesh(new THREE.PlaneGeometry(face.endsWith('x') ? sz * 0.97 : sx * 0.97, sy * 0.95), screen);
        const sign = face.startsWith('-') ? -1 : 1;
        if (face.endsWith('x')) { s.position.x = (sign * sx) / 2 + sign * 0.001; s.rotation.y = (sign * Math.PI) / 2; }
        else { s.position.z = (sign * sz) / 2 + sign * 0.001; s.rotation.y = sign > 0 ? 0 : Math.PI; }
        g.add(s);
      }
      g.position.set(...e.center);
      g.rotation.y = e.rotationY ?? 0;
      return g;
    }
    case 'curtain': {
      // pleated sheer curtain hanging from a rod: from -> to along a wall at a given offset
      const a = p.from as number[];
      const b = p.to as number[];
      const top = y1;
      const bottom = y0;
      const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const pleats = Math.max(4, Math.round(L / 0.09));
      const geo = new THREE.PlaneGeometry(L, top - bottom, pleats * 4, 1);
      const pos = geo.attributes.position as THREE.BufferAttribute;
      for (let i = 0; i < pos.count; i++) pos.setZ(i, Math.sin((pos.getX(i) / L) * pleats * Math.PI * 2) * 0.025);
      geo.computeVertexNormals();
      const m = new THREE.Mesh(geo, mat('body'));
      m.position.set((a[0] + b[0]) / 2, (top + bottom) / 2, (a[1] + b[1]) / 2);
      m.rotation.y = Math.atan2(-(b[1] - a[1]), b[0] - a[0]);
      m.castShadow = false;
      g.add(m);
      if (p.rod !== false) {
        const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.01, 0.01, L + 0.2, 8), mat('rod', 'black'));
        rod.rotation.z = Math.PI / 2;
        rod.position.set(0, top - (top + bottom) / 2 + 0.03, 0);
        m.add(rod);
      }
      return g;
    }
    case 'chair': {
      if (!e.center) return null;
      const seat = mat('seat');
      const base = mat('frame', 'black');
      const sh = Number(p.seatHeight ?? 0.48);
      g.add(box(0.52, 0.09, 0.5, seat, 0, sh, 0));
      const back = box(0.5, 0.62, 0.09, seat, 0, sh + 0.36, 0.24);
      back.rotation.x = -0.12;
      g.add(back);
      for (const s of [-1, 1]) g.add(box(0.06, 0.05, 0.36, seat, s * 0.29, sh + 0.2, 0.02));
      const col = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.03, sh - 0.08, 10), base);
      col.position.y = (sh - 0.08) / 2 + 0.05;
      g.add(col);
      for (let k = 0; k < 5; k++) {
        const leg = box(0.32, 0.03, 0.04, base, 0, 0.06, 0);
        leg.geometry.translate(0.16, 0, 0);
        leg.rotation.y = (k * 2 * Math.PI) / 5;
        g.add(leg);
      }
      g.position.set(...e.center);
      g.rotation.y = e.rotationY ?? 0;
      return g;
    }
    case 'floor-lamp': {
      if (!e.center) return null;
      const h = Number(p.height ?? 1.6);
      const black = mat('frame', 'black');
      const base = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.15, 0.025, 32), black);
      base.position.y = 0.0125;
      g.add(base);
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, h, 8), mat('accent', 'brass'));
      pole.position.y = h / 2;
      g.add(pole);
      const shade = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.22, 0.03, 32), black);
      shade.position.y = h;
      g.add(shade);
      const glow = new THREE.Mesh(new THREE.CircleGeometry(0.18, 32), new THREE.MeshBasicMaterial({ color: '#fff1d6' }));
      glow.rotation.x = Math.PI / 2;
      glow.position.y = h - 0.016;
      g.add(glow);
      const light = new THREE.SpotLight(String(p.color ?? '#ffd9a8'), Number(p.intensity ?? 25), 6, Math.PI / 2.6, 0.8, 2);
      light.position.set(0, h - 0.03, 0);
      light.target.position.set(0, 0, 0);
      light.castShadow = true;
      light.shadow.mapSize.set(1024, 1024);
      g.add(light, light.target);
      g.position.set(e.center[0], 0, e.center[2]);
      return g;
    }
    case 'track': {
      // ceiling rail with spots; each spot aims at a target and carries a spot light
      const a = p.from as number[];
      const b = p.to as number[];
      const H = Number(p.ceilingY ?? room.ceilingHeight);
      const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const black = mat('frame', 'black');
      const rail = new THREE.Mesh(new THREE.BoxGeometry(L, 0.03, 0.035), black);
      rail.position.set((a[0] + b[0]) / 2, H - 0.015, (a[1] + b[1]) / 2);
      rail.rotation.y = Math.atan2(-(b[1] - a[1]), b[0] - a[0]);
      g.add(rail);
      const spots = ((p.spots as unknown) as number[][] | undefined) ?? [];
      for (const s of spots) {
        // s = [t along rail 0..1, target x, target y, target z]
        const x = a[0] + (b[0] - a[0]) * s[0];
        const z = a[1] + (b[1] - a[1]) * s[0];
        const pos = new THREE.Vector3(x, H - 0.11, z);
        const tgt = new THREE.Vector3(s[1], s[2], s[3]);
        const can = new THREE.Mesh(new THREE.CylinderGeometry(0.028, 0.028, 0.13, 16), black);
        can.position.copy(pos);
        can.lookAt(tgt);
        can.rotateX(Math.PI / 2);
        g.add(can);
        const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 0.07, 8), black);
        stem.position.set(x, H - 0.05, z);
        g.add(stem);
        const light = new THREE.SpotLight(String(p.color ?? '#ffd7a8'), Number(p.intensity ?? 18), 8, Number(p.angle ?? 0.38), Number(p.penumbra ?? 0.8), 2);
        light.position.copy(pos);
        light.target.position.copy(tgt);
        // shadow maps use texture units (16 per shader): only spots flagged with a 5th value of 1 cast shadows
        light.castShadow = s[4] === 1;
        light.shadow.mapSize.set(512, 512);
        light.shadow.bias = -0.0005;
        g.add(light, light.target);
      }
      return g;
    }
    case 'bed': {
      // upholstered bed: base, mattress, duvet over the foot end, headboard on the `head` side, pillows
      if (!e.footprint) return null;
      const b = bbox(e.footprint);
      const head = String(p.head ?? 'x0'); // which side of the footprint is the headboard
      const baseTop = Number(p.baseTop ?? 0.3);
      const top = Number(p.mattressTop ?? 0.55);
      const hbTop = Number(p.headboardTop ?? 1.1);
      const hbT = Number(p.headboardThickness ?? 0.1);
      const alongX = head === 'x0' || head === 'x1'; // bed length runs along x
      const L = alongX ? b.x1 - b.x0 : b.z1 - b.z0;
      const W = alongX ? b.z1 - b.z0 : b.x1 - b.x0;
      const sgn = head.endsWith('0') ? 1 : -1; // direction from head to foot
      const h0 = alongX ? (sgn > 0 ? b.x0 : b.x1) : (sgn > 0 ? b.z0 : b.z1);
      const cW = alongX ? (b.z0 + b.z1) / 2 : (b.x0 + b.x1) / 2;
      const at = (t: number, w: number, y0: number, y1: number, len: number, w2: number, m: THREE.Material) => {
        // t = distance from the head along the bed, w = offset across, sizes len (along) x w2 (across)
        const along = h0 + sgn * (t + len / 2);
        const mesh = alongX ? box(len, y1 - y0, w2, m, along, (y0 + y1) / 2, cW + w) : box(w2, y1 - y0, len, m, cW + w, (y0 + y1) / 2, along);
        g.add(mesh);
      };
      at(hbT, 0, 0.06, baseTop, L - hbT, W, mat('frame'));
      at(hbT + 0.02, 0, baseTop, top, L - hbT - 0.04, W - 0.04, mat('mattress', e.materials?.frame));
      at(hbT + (L - hbT) * 0.32, 0, top, top + 0.07, (L - hbT) * 0.68, W + 0.04, mat('duvet', e.materials?.frame));
      at(0, 0, 0, hbTop, hbT, W + 0.06, mat('headboard', e.materials?.frame));
      for (const s of [-1, 1]) at(hbT + 0.06, (s * W) / 4, top, top + 0.18, 0.4, W / 2 - 0.12, mat('pillow', e.materials?.mattress));
      return g;
    }
    case 'armchair': {
      // tub armchair: round seat on short legs and a curved back shell; faces +z before rotation
      if (!e.center) return null;
      const R = Number(p.radius ?? 0.38);
      const seat = Number(p.seatHeight ?? 0.44);
      const backTop = Number(p.backHeight ?? 0.74);
      const legs = 0.12;
      const body = mat('body');
      const cush = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.92, R * 0.92, seat - legs, 32), body);
      cush.position.y = legs + (seat - legs) / 2;
      cush.castShadow = cush.receiveShadow = true;
      g.add(cush);
      const shape = new THREE.Shape();
      const a0 = (-20 * Math.PI) / 180;
      const a1 = (200 * Math.PI) / 180;
      shape.absarc(0, 0, R, a0, a1, false);
      shape.absarc(0, 0, R - 0.11, a1, a0, true);
      const shell = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: backTop - legs - 0.05, bevelEnabled: true, bevelSize: 0.02, bevelThickness: 0.02, bevelSegments: 3 }), body);
      shell.geometry.rotateX(-Math.PI / 2); // shape +y (back) -> -z
      shell.position.y = legs + 0.03;
      shell.castShadow = shell.receiveShadow = true;
      g.add(shell);
      for (const [lx, lz] of [[-0.6, -0.6], [0.6, -0.6], [-0.6, 0.6], [0.6, 0.6]]) {
        const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, legs, 8), mat('legs', 'black'));
        leg.position.set(lx * R * 0.8, legs / 2, lz * R * 0.8);
        g.add(leg);
      }
      g.position.set(e.center[0], 0, e.center[2]);
      g.rotation.y = e.rotationY ?? 0;
      return g;
    }
    case 'chair-dining':
    case 'stool': {
      // dining chair (upholstered seat and curved back on 4 wooden legs) or bar stool (higher seat, low back);
      // faces +z before rotation
      if (!e.center) return null;
      const seatH = Number(p.seatHeight ?? (e.type === 'stool' ? 0.66 : 0.47));
      const W = Number(p.width ?? 0.48);
      const D = Number(p.depth ?? 0.46);
      const backTop = Number(p.backHeight ?? (e.type === 'stool' ? seatH + 0.22 : 0.8));
      const seat = mat('seat');
      const legs = mat('legs', 'walnut');
      g.add(box(W, 0.07, D, seat, 0, seatH - 0.035, 0));
      // curved back: a full shell, or (backBand) an upholstered band at the top on two wooden back posts
      const band = Number(p.backBand ?? backTop - seatH - 0.04);
      const back = new THREE.Mesh(new THREE.CylinderGeometry(W * 0.62, W * 0.62, band, 24, 1, true, -1.1, 2.2), seat);
      back.material = seat;
      (back.material as THREE.Material).side = THREE.DoubleSide;
      back.scale.set(1, 1, 0.7);
      back.position.set(0, backTop - band / 2, -D * 0.12);
      back.rotation.y = Math.PI; // open side to the front
      back.castShadow = true;
      g.add(back);
      if (p.backBand) {
        for (const s of [-1, 1]) {
          const post = new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.014, backTop - band - seatH + 0.04, 8), legs);
          post.position.set(s * W * 0.42, (seatH + backTop - band) / 2, -D * 0.12 - W * 0.62 * 0.7 * Math.cos(0.75));
          g.add(post);
        }
      }
      for (const [lx, lz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
        const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.012, seatH - 0.07, 8), legs);
        leg.position.set((lx * W) / 2.4, (seatH - 0.07) / 2, (lz * D) / 2.4);
        leg.rotation.set(lz * 0.06, 0, -lx * 0.06);
        leg.castShadow = true;
        g.add(leg);
      }
      if (e.type === 'stool') g.add(box(W * 0.8, 0.02, 0.02, legs, 0, 0.28, D / 2.4)); // footrest
      g.position.set(e.center[0], 0, e.center[2]);
      g.rotation.y = e.rotationY ?? 0;
      return g;
    }
    case 'wc': {
      // wall-hung toilet: ellipsoid bowl with a flat seat; faces +z before rotation, back to the wall at -z
      if (!e.center) return null;
      const seat = e.center[1];
      const L = Number(p.length ?? 0.53);
      const W = Number(p.width ?? 0.36);
      const bowl = new THREE.Mesh(new THREE.SphereGeometry(0.5, 32, 20, 0, Math.PI * 2, 0, Math.PI * 0.62), mat('body', 'white'));
      bowl.scale.set(W, 0.32, L);
      bowl.position.set(0, seat - 0.05, L / 2);
      bowl.rotation.x = Math.PI; // dome downwards
      bowl.castShadow = bowl.receiveShadow = true;
      g.add(bowl);
      const lid = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.5, 0.04, 32), mat('seat', e.materials?.body ?? 'white'));
      lid.scale.set(W * 1.02, 1, L * 0.98);
      lid.position.set(0, seat - 0.02, L / 2);
      lid.castShadow = true;
      g.add(lid);
      g.add(box(W * 0.75, 0.2, 0.12, mat('body', 'white'), 0, seat - 0.1, 0.06));
      g.position.set(e.center[0], 0, e.center[2]);
      g.rotation.y = e.rotationY ?? 0;
      return g;
    }
    case 'basin': {
      // vessel basin on a counter, or a wall basin with a half pedestal; faces +z before rotation
      if (!e.center) return null;
      const W = Number(p.width ?? 0.5);
      const D = Number(p.depth ?? 0.36);
      const Hb = Number(p.height ?? 0.13);
      const rim = e.center[1];
      const shell = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.42, 1, 40), mat('body', 'white'));
      shell.scale.set(W, Hb, D);
      shell.position.y = rim - Hb / 2;
      shell.castShadow = shell.receiveShadow = true;
      g.add(shell);
      const hollow = new THREE.Mesh(new THREE.CircleGeometry(0.5, 40), mat('inside', e.materials?.body ?? 'white'));
      hollow.scale.set(W * 0.86, D * 0.84, 1);
      hollow.rotation.x = -Math.PI / 2;
      hollow.position.y = rim - Hb * 0.55;
      g.add(hollow);
      if (p.pedestal) {
        const ped = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.07, 0.3, 24), mat('body', 'white'));
        ped.scale.set(1, 1, 0.8);
        ped.position.set(0, rim - Hb - 0.15, -D * 0.15);
        g.add(ped);
      }
      g.position.set(e.center[0], 0, e.center[2]);
      g.rotation.y = e.rotationY ?? 0;
      return g;
    }
    case 'glass': {
      // shower screens: transparent panels with thin black frames, [{from:[x,z], to:[x,z], y:[a,b]}]
      const panels = ((p.panels as unknown) as { from: number[]; to: number[]; y: number[] }[] | undefined) ?? [];
      const glass = mat('glass', 'glass');
      const frame = mat('frame', 'black');
      for (const q of panels) {
        const L = Math.hypot(q.to[0] - q.from[0], q.to[1] - q.from[1]);
        const rot = Math.atan2(-(q.to[1] - q.from[1]), q.to[0] - q.from[0]);
        const cx = (q.from[0] + q.to[0]) / 2;
        const cz = (q.from[1] + q.to[1]) / 2;
        const pane = new THREE.Mesh(new THREE.BoxGeometry(L, q.y[1] - q.y[0], 0.008), glass);
        pane.position.set(cx, (q.y[0] + q.y[1]) / 2, cz);
        pane.rotation.y = rot;
        g.add(pane);
        for (const yy of [q.y[0] + 0.01, q.y[1] - 0.01]) {
          const bar = new THREE.Mesh(new THREE.BoxGeometry(L, 0.02, 0.02), frame);
          bar.position.set(cx, yy, cz);
          bar.rotation.y = rot;
          g.add(bar);
        }
        for (const t of [0, 1]) {
          const post = new THREE.Mesh(new THREE.BoxGeometry(0.02, q.y[1] - q.y[0], 0.02), frame);
          post.position.set(q.from[0] + (q.to[0] - q.from[0]) * t, (q.y[0] + q.y[1]) / 2, q.from[1] + (q.to[1] - q.from[1]) * t);
          post.rotation.y = rot;
          g.add(post);
        }
      }
      return g;
    }
    case 'ceiling-disc': {
      // flush round ceiling light: glowing disc and a soft light below it
      const c = p.at as number[];
      const H = Number(p.ceilingY ?? room.ceilingHeight);
      const dia = Number(p.diameter ?? 0.4);
      const ring = new THREE.Mesh(new THREE.CylinderGeometry(dia / 2, dia / 2, 0.06, 40), mat('frame', 'white'));
      ring.position.set(c[0], H - 0.03, c[1]);
      g.add(ring);
      const glow = new THREE.Mesh(new THREE.CircleGeometry(dia / 2 - 0.02, 40), new THREE.MeshBasicMaterial({ color: String(p.color ?? '#fff4e2'), toneMapped: false }));
      glow.rotation.x = Math.PI / 2;
      glow.position.set(c[0], H - 0.061, c[1]);
      g.add(glow);
      const light = new THREE.PointLight(String(p.color ?? '#fff1dc'), Number(p.intensity ?? 6), 0, 2);
      light.position.set(c[0], H - 0.2, c[1]);
      g.add(light);
      return g;
    }
    case 'pendant': {
      // cord from the ceiling to a cylinder body, light shining down from its bottom
      const c = p.at as number[];
      const H = Number(p.ceilingY ?? room.ceilingHeight);
      const bottom = Number(p.bottom ?? 1.4);
      const len = Number(p.length ?? 0.2);
      const dia = Number(p.diameter ?? 0.06);
      const depth = Number(p.depth ?? dia); // oval shade: depth (z) smaller than the diameter (x)
      const canopyLen = Number(p.canopyLength ?? 0); // linear canopy along x with two cables
      const cordXs = canopyLen > 0 ? [-1, 1].map((s) => s * (Math.min(canopyLen, dia) / 2 - 0.04)) : [0];
      for (const dx of cordXs) {
        const cord = new THREE.Mesh(new THREE.CylinderGeometry(0.002, 0.002, H - bottom - len, 6), mat('cord', 'black'));
        cord.position.set(c[0] + dx, (H + bottom + len) / 2, c[1]);
        g.add(cord);
      }
      // shape: cylinder (brass tube), drum (wide fabric shade, open at the bottom) or cone (shade widening downwards)
      const shape = String(p.shape ?? 'cylinder');
      const geo = shape === 'cone'
        ? new THREE.CylinderGeometry(Number(p.topDiameter ?? dia * 0.35) / 2, dia / 2, len, 32, 1, true)
        : new THREE.CylinderGeometry(dia / 2, dia / 2, len, shape === 'drum' ? 48 : 24, 1, shape === 'drum');
      const bodyMat = mat('body', 'brass');
      if (shape !== 'cylinder') bodyMat.side = THREE.DoubleSide;
      const bodyMesh = new THREE.Mesh(geo, bodyMat);
      bodyMesh.position.set(c[0], bottom + len / 2, c[1]);
      bodyMesh.scale.z = depth / dia;
      bodyMesh.castShadow = shape === 'cylinder';
      g.add(bodyMesh);
      if (shape !== 'cylinder') { // glowing diffuser inside the open bottom
        const glow = new THREE.Mesh(new THREE.CircleGeometry(dia / 2 - 0.01, 32), new THREE.MeshBasicMaterial({ color: '#fff1da', toneMapped: false }));
        glow.rotation.x = Math.PI / 2;
        glow.scale.y = depth / dia;
        glow.position.set(c[0], bottom + len * 0.25, c[1]);
        g.add(glow);
      }
      if (p.canopy !== false) {
        const canopy = canopyLen > 0
          ? new THREE.Mesh(new THREE.BoxGeometry(canopyLen, 0.02, 0.035), mat('canopy', 'black'))
          : new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.015, 24), mat('canopy', e.materials?.body ?? 'brass'));
        canopy.position.set(c[0], H - (canopyLen > 0 ? 0.01 : 0.0075), c[1]);
        g.add(canopy);
      }
      const light = new THREE.SpotLight(String(p.color ?? '#ffd9a8'), Number(p.intensity ?? 6), 6, Number(p.angle ?? 0.9), 0.9, 2);
      light.position.set(c[0], bottom - 0.01, c[1]);
      light.target.position.set(c[0], 0, c[1]);
      g.add(light, light.target);
      if (p.uplight) { // open-top drum: light on the ceiling above it
        const up = new THREE.SpotLight(String(p.color ?? '#ffd9a8'), Number(p.uplight), 3, 1.2, 1, 2);
        up.position.set(c[0], bottom + len, c[1]);
        up.target.position.set(c[0], H + 1, c[1]);
        g.add(up, up.target);
      }
      return g;
    }
    case 'led-line': {
      // LED profile flush in the ceiling: glowing strip plus a thin area light shining down
      const a = p.from as number[];
      const b = p.to as number[];
      const H = Number(p.ceilingY ?? room.ceilingHeight);
      const L = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const rot = Math.atan2(-(b[1] - a[1]), b[0] - a[0]);
      const strip = new THREE.Mesh(new THREE.BoxGeometry(L, 0.004, Number(p.width ?? 0.025)), new THREE.MeshBasicMaterial({ color: String(p.color ?? '#fff6ea'), toneMapped: false }));
      strip.position.set((a[0] + b[0]) / 2, H - 0.002, (a[1] + b[1]) / 2);
      strip.rotation.y = rot;
      g.add(strip);
      if (p.light === false) return g; // glow only; a room-level 'rect' light stands in for a whole LED outline
      const r = new THREE.RectAreaLight(String(p.color ?? '#fff1dc'), Number(p.intensity ?? 25), L, Number(p.width ?? 0.025));
      r.position.set((a[0] + b[0]) / 2, H - 0.005, (a[1] + b[1]) / 2);
      r.rotation.set(-Math.PI / 2, 0, rot); // face down; long side along the strip
      g.add(r);
      return g;
    }
    case 'balcony': {
      // balcony slab with tiles and a railing on its free sides (not on the side against the room wall)
      if (!e.footprint) return null;
      const b = bbox(e.footprint);
      const fy = Number(p.floorY ?? -0.05);
      g.add(extrude(e.footprint, fy - 0.15, fy, mat('floor')));
      const rh = Number(p.railHeight ?? 1.0);
      const rail = mat('rail', 'black');
      const skip = String(p.wallSide ?? 'x0'); // side touching the room
      const sides: [number, number, number, number, string][] = [
        [b.x0, b.z0, b.x1, b.z0, 'z0'], [b.x1, b.z0, b.x1, b.z1, 'x1'], [b.x1, b.z1, b.x0, b.z1, 'z1'], [b.x0, b.z1, b.x0, b.z0, 'x0'],
      ];
      for (const [x0, z0, x1, z1, side] of sides) {
        if (side === skip) continue;
        const L = Math.hypot(x1 - x0, z1 - z0);
        const rot = Math.atan2(-(z1 - z0), x1 - x0);
        if (p.solid) { // solid parapet with a black top rail
          const wallM = new THREE.Mesh(new THREE.BoxGeometry(L + 0.12, rh - 0.04, 0.12), mat('parapet', '#a9aba8'));
          wallM.position.set((x0 + x1) / 2, fy + (rh - 0.04) / 2, (z0 + z1) / 2);
          wallM.rotation.y = rot;
          g.add(wallM);
          const cap = new THREE.Mesh(new THREE.BoxGeometry(L + 0.12, 0.04, 0.06), rail);
          cap.position.set((x0 + x1) / 2, fy + rh - 0.02, (z0 + z1) / 2);
          cap.rotation.y = rot;
          g.add(cap);
          continue;
        }
        const top = new THREE.Mesh(new THREE.BoxGeometry(L, 0.04, 0.05), rail);
        top.position.set((x0 + x1) / 2, fy + rh, (z0 + z1) / 2);
        top.rotation.y = rot;
        g.add(top);
        const n = Math.round(L / 0.12);
        for (let k = 0; k <= n; k++) {
          const bar = new THREE.Mesh(new THREE.BoxGeometry(0.015, rh, 0.015), rail);
          bar.position.set(x0 + ((x1 - x0) * k) / n, fy + rh / 2, z0 + ((z1 - z0) * k) / n);
          g.add(bar);
        }
      }
      return g;
    }
    default:
      console.warn('unknown element type', e.type, e.id);
      return null;
  }
}

// ---------------------------------------------------------------------------------------------- lights
function buildLight(l: LightDef, lib: MaterialLib): THREE.Object3D {
  const g = new THREE.Group();
  if (l.type === 'hemisphere') {
    g.add(new THREE.HemisphereLight(l.color, l.groundColor ?? '#3a2e25', l.intensity));
  } else if (l.type === 'point' && l.position) {
    const p = new THREE.PointLight(l.color, l.intensity, l.distance ?? 0, 2);
    p.position.set(...l.position);
    p.castShadow = l.castShadow ?? false;
    g.add(p);
  } else if (l.type === 'spot' && l.position && l.target) {
    const s = new THREE.SpotLight(l.color, l.intensity, l.distance ?? 0, l.angle ?? 0.5, l.penumbra ?? 0.5, 2);
    s.position.set(...l.position);
    s.target.position.set(...l.target);
    s.castShadow = l.castShadow ?? false;
    g.add(s, s.target);
  } else if ((l.type === 'rect' || l.type === 'window-sky') && l.position && l.facing) {
    const r = new THREE.RectAreaLight(l.color, l.intensity, l.width ?? 1, l.height ?? 1);
    r.position.set(...l.position);
    // straight up or down: lookAt is degenerate, so set the rotation (width along x, height along z)
    if (Math.abs(l.facing[1]) > 0.999) r.rotation.set(l.facing[1] < 0 ? -Math.PI / 2 : Math.PI / 2, 0, 0);
    else r.lookAt(l.position[0] + l.facing[0], l.position[1] + l.facing[1], l.position[2] + l.facing[2]);
    g.add(r);
  } else if (l.type === 'led-strip' && l.from && l.to && l.facing) {
    // visible emissive strip plus a thin area light shining in `facing`
    const a = new THREE.Vector3(...l.from);
    const b = new THREE.Vector3(...l.to);
    const L = a.distanceTo(b);
    const mid = a.clone().add(b).multiplyScalar(0.5);
    const dir = b.clone().sub(a).normalize();
    const strip = new THREE.Mesh(new THREE.BoxGeometry(L, 0.012, 0.012), new THREE.MeshBasicMaterial({ color: l.color, toneMapped: false }));
    strip.position.copy(mid);
    strip.quaternion.setFromUnitVectors(new THREE.Vector3(1, 0, 0), dir);
    g.add(strip);
    if (l.light === false) return g; // glow only (each area light adds to every shader's compile time)
    // RectAreaLight: width along local x, height along local y; with up = strip direction the long side follows it
    const r = new THREE.RectAreaLight(l.color, l.intensity, l.width ?? 0.03, L);
    r.position.copy(mid);
    r.up.copy(dir);
    r.lookAt(mid.clone().add(new THREE.Vector3(...l.facing)));
    g.add(r);
  }
  void lib;
  return g;
}
