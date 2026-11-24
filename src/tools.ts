// Tools: measuring tape, plan-dimension overlay, click-to-inspect panel, assumptions panel.
import * as THREE from 'three';
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import type { Pickable } from './build';
import type { Room } from './types';

function label(text: string, cls: string): CSS2DObject {
  const div = document.createElement('div');
  div.className = cls;
  div.textContent = text;
  return new CSS2DObject(div);
}

export class Measure {
  readonly group = new THREE.Group();
  private a: THREE.Vector3 | null = null;
  private mat = new THREE.MeshBasicMaterial({ color: '#ff2d6f', depthTest: false });

  click(p: THREE.Vector3): void {
    if (!this.a || this.group.children.length > 2) {
      this.clear();
      this.a = p.clone();
      this.group.add(this.dot(p));
      return;
    }
    const b = p.clone();
    this.group.add(this.dot(b));
    const geo = new THREE.BufferGeometry().setFromPoints([this.a, b]);
    const line = new THREE.Line(geo, new THREE.LineBasicMaterial({ color: '#ff2d6f', depthTest: false }));
    line.renderOrder = 10;
    this.group.add(line);
    const d = this.a.distanceTo(b);
    const dh = Math.hypot(b.x - this.a.x, b.z - this.a.z);
    const lab = label(`${(d * 100).toFixed(1)} cm  (horizontal ${(dh * 100).toFixed(1)}, vertical ${(Math.abs(b.y - this.a.y) * 100).toFixed(1)})`, 'tag tag-measure');
    lab.position.copy(this.a).add(b).multiplyScalar(0.5);
    this.group.add(lab);
    this.a = null;
  }

  clear(): void {
    for (const c of [...this.group.children]) {
      this.group.remove(c);
      if (c instanceof CSS2DObject) c.element.remove();
    }
    this.a = null;
  }

  private dot(p: THREE.Vector3): THREE.Mesh {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.012, 12, 8), this.mat);
    m.renderOrder = 10;
    m.position.copy(p);
    return m;
  }
}

export function dimsOverlay(room: Room): THREE.Group {
  const g = new THREE.Group();
  for (const d of room.dims) {
    const ok = d.status.startsWith('pass');
    const col = ok ? '#d40000' : '#ff8a00';
    const a = new THREE.Vector3(d.a[0], 0.015, d.a[1]);
    const b = new THREE.Vector3(d.b[0], 0.015, d.b[1]);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), new THREE.LineBasicMaterial({ color: col, depthTest: false }));
    line.renderOrder = 9;
    g.add(line);
    for (const p of [a, b]) {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.015, 10, 6), new THREE.MeshBasicMaterial({ color: col, depthTest: false }));
      dot.position.copy(p);
      dot.renderOrder = 9;
      g.add(dot);
    }
    const txt = d.geometry_cm !== null && d.label_cm !== null && Math.abs(d.geometry_cm - d.label_cm) >= 0.5
      ? `${d.label_cm} (model ${d.geometry_cm})` : `${d.label_cm ?? '?'}`;
    const lab = label(txt, ok ? 'tag tag-dim' : 'tag tag-dim tag-warn');
    lab.position.copy(a).add(b).multiplyScalar(0.5);
    g.add(lab);
  }
  return g;
}

export function showInspect(panel: HTMLElement, p: Pickable | null, hit?: THREE.Vector3): void {
  if (!p) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.innerHTML = '';
  const h = document.createElement('h3');
  h.textContent = p.title;
  panel.appendChild(h);
  const ul = document.createElement('ul');
  for (const line of p.lines) {
    const li = document.createElement('li');
    li.textContent = line;
    ul.appendChild(li);
  }
  if (hit) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = `clicked at x ${hit.x.toFixed(2)}, y ${hit.y.toFixed(2)}, z ${hit.z.toFixed(2)} m`;
    ul.appendChild(li);
  }
  panel.appendChild(ul);
}

export function fillAssumptions(panel: HTMLElement, room: Room): void {
  const rows = room.assumptions;
  const counts = rows.reduce<Record<string, number>>((acc, r) => ((acc[r.source] = (acc[r.source] ?? 0) + 1), acc), {});
  panel.innerHTML = `<h3>Assumptions and sources: ${room.name}</h3>
    <p class="muted">Every value not read from the plans. ${Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(', ')}.</p>`;
  const table = document.createElement('table');
  table.innerHTML = '<thead><tr><th>Item</th><th>Value</th><th>Source</th><th>Note</th></tr></thead>';
  const tb = document.createElement('tbody');
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.className = `src-${r.source.split(' ')[0]}`;
    for (const v of [r.item, r.value, r.source, r.note ?? '']) {
      const td = document.createElement('td');
      td.textContent = v;
      tr.appendChild(td);
    }
    tb.appendChild(tr);
  }
  table.appendChild(tb);
  panel.appendChild(table);
}
