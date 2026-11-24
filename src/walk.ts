// First-person walk: pointer-lock mouse look, WASD / arrows, eye height from the data, circle-vs-segment collision.
import * as THREE from 'three';
import { PointerLockControls } from 'three/examples/jsm/controls/PointerLockControls.js';
import type { V2 } from './types';

export class Walk {
  readonly controls: PointerLockControls;
  private keys = new Set<string>();
  radius = 0.22;
  speed = 1.3;

  constructor(private camera: THREE.PerspectiveCamera, dom: HTMLElement, private colliders: () => [V2, V2][]) {
    this.controls = new PointerLockControls(camera, dom);
    window.addEventListener('keydown', (e) => this.keys.add(e.code));
    window.addEventListener('keyup', (e) => this.keys.delete(e.code));
    window.addEventListener('blur', () => this.keys.clear());
  }

  update(dt: number): void {
    if (!this.controls.isLocked) return;
    const k = this.keys;
    const fwd = new THREE.Vector3();
    this.camera.getWorldDirection(fwd);
    fwd.y = 0;
    fwd.normalize();
    const right = new THREE.Vector3().crossVectors(fwd, new THREE.Vector3(0, 1, 0)).normalize();
    const move = new THREE.Vector3();
    if (k.has('KeyW') || k.has('ArrowUp')) move.add(fwd);
    if (k.has('KeyS') || k.has('ArrowDown')) move.sub(fwd);
    if (k.has('KeyD') || k.has('ArrowRight')) move.add(right);
    if (k.has('KeyA') || k.has('ArrowLeft')) move.sub(right);
    if (move.lengthSq() === 0) return;
    move.normalize().multiplyScalar(this.speed * dt * (k.has('ShiftLeft') ? 2 : 1));
    const p = this.camera.position;
    let x = p.x + move.x;
    let z = p.z + move.z;
    for (let it = 0; it < 4; it++) {
      for (const [a, b] of this.colliders()) {
        const [qx, qz] = closest(a, b, x, z);
        const dx = x - qx;
        const dz = z - qz;
        const d = Math.hypot(dx, dz);
        if (d < this.radius && d > 1e-9) {
          x = qx + (dx / d) * this.radius;
          z = qz + (dz / d) * this.radius;
        }
      }
    }
    p.x = x;
    p.z = z;
  }
}

function closest(a: V2, b: V2, x: number, z: number): V2 {
  const vx = b[0] - a[0];
  const vz = b[1] - a[1];
  const L2 = vx * vx + vz * vz || 1e-12;
  const t = Math.max(0, Math.min(1, ((x - a[0]) * vx + (z - a[1]) * vz) / L2));
  return [a[0] + vx * t, a[1] + vz * t];
}
