// Viewer shell: room switcher, camera modes (orbit / top-down / walk / render viewpoints), tools, screenshot mode.
// URL parameters: room=<id>, cam=<preset id>, ref=1 (reference overlay), shot=1 (no UI, sets window.__ready),
// exposure=<number>.
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { CSS2DRenderer } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { GTAOPass } from 'three/examples/jsm/postprocessing/GTAOPass.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import rawOwner from '../data/rooms.json';
import rawRender from '../data/rooms-render.json';
import { buildRoom, type BuiltRoom, type Pickable } from './build';
import { dimsOverlay, fillAssumptions, Measure, showInspect } from './tools';
import type { CameraPreset, Room, RoomsData } from './types';
import { Walk } from './walk';

// owner variant: the owner's ceiling height; render variant: the designer's render model, for comparing with renders
const VARIANTS = { owner: rawOwner as unknown as RoomsData, render: rawRender as unknown as RoomsData };
let DATA = VARIANTS.owner;
type Mode = 'orbit' | 'top' | 'walk' | 'preset';
const EYE = 1.6;

export class App {
  private renderer: THREE.WebGLRenderer;
  private labels: CSS2DRenderer;
  private scene = new THREE.Scene();
  private persp = new THREE.PerspectiveCamera(55, 1, 0.03, 200);
  private ortho = new THREE.OrthographicCamera(-5, 5, 5, -5, 0.1, 200);
  private orbit: OrbitControls;
  private orbitTop: OrbitControls;
  private walk: Walk;
  private mode: Mode = 'orbit';
  private room!: Room;
  private built: BuiltRoom | null = null;
  private pickables = new Map<THREE.Object3D, Pickable>();
  private measure = new Measure();
  private dims: THREE.Group | null = null;
  private measuring = false;
  private showDims = false;
  private clock = new THREE.Clock();
  private params = new URLSearchParams(location.search);
  private ui: Record<string, HTMLElement> = {};
  private refImg: HTMLImageElement;
  private frames = 0;
  private composer: EffectComposer;
  private gtao: GTAOPass;
  private aoCam = new THREE.PerspectiveCamera(); // sees layer 0 only: glass and curtains (layer 1) cast no occlusion
  private aoOn = this.params.get('ao') !== '0';
  private daylight = this.params.get('day') !== '0';

  constructor(host: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = Number(this.params.get('exposure') ?? 1);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    host.appendChild(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    this.labels.domElement.className = 'labels';
    host.appendChild(this.labels.domElement);

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.45; // soft fill standing in for the renders' bounce light
    this.scene.background = new THREE.Color('#1d1b19');
    this.scene.add(this.measure.group);

    // ambient occlusion for the soft contact shadows of the path-traced renders (perspective views only)
    this.composer = new EffectComposer(this.renderer);
    this.persp.layers.enable(1);
    this.composer.addPass(new RenderPass(this.scene, this.persp));
    this.gtao = new GTAOPass(this.scene, this.aoCam, 800, 600);
    this.gtao.updateGtaoMaterial({ radius: 0.35, distanceExponent: 1.2, thickness: 1.5, scale: 1.0, samples: 16 });
    this.gtao.blendIntensity = 0.85;
    this.composer.addPass(this.gtao);
    this.composer.addPass(new OutputPass());

    this.orbit = new OrbitControls(this.persp, this.renderer.domElement);
    this.orbit.enableDamping = true;
    this.orbitTop = new OrbitControls(this.ortho, this.renderer.domElement);
    this.orbitTop.enableRotate = false;
    this.orbitTop.screenSpacePanning = true;
    this.walk = new Walk(this.persp, this.renderer.domElement, () => this.built?.colliders ?? []);
    this.walk.controls.addEventListener('unlock', () => this.mode === 'walk' && this.setMode('orbit'));

    this.refImg = document.createElement('img');
    this.refImg.id = 'refimg';
    this.refImg.hidden = true;
    document.body.appendChild(this.refImg);

    if (this.params.get('variant') === 'render') DATA = VARIANTS.render;
    this.buildUi();
    if (this.params.get('shot') === '1') document.body.classList.add('shot');
    window.addEventListener('resize', () => this.resize());
    this.bindClicks();
    this.loadRoom(this.params.get('room') ?? DATA.rooms[0].id);
    const cam = this.params.get('cam');
    const preset = cam ? this.room.cameras.find((c) => c.id === cam) : undefined;
    if (preset) this.applyPreset(preset);
    if (this.params.get('ref') === '1') this.toggleRef(true);
    this.resize();
    this.renderer.setAnimationLoop(() => this.frame());
  }

  // ------------------------------------------------------------------------------------------- UI
  private buildUi(): void {
    const bar = document.createElement('div');
    bar.className = 'bar';
    const roomSel = document.createElement('select');
    for (const r of DATA.rooms) roomSel.add(new Option(r.name, r.id));
    roomSel.onchange = () => this.loadRoom(roomSel.value);
    bar.appendChild(roomSel);
    this.ui.room = roomSel;
    const modeBtn = (m: Mode, text: string) => {
      const b = document.createElement('button');
      b.textContent = text;
      b.onclick = () => this.setMode(m);
      bar.appendChild(b);
      this.ui[`mode-${m}`] = b;
    };
    modeBtn('orbit', 'Orbit');
    modeBtn('top', 'Top-down');
    modeBtn('walk', 'Walk');
    const presetSel = document.createElement('select');
    presetSel.onchange = () => {
      const p = this.room.cameras.find((c) => c.id === presetSel.value);
      if (p) this.applyPreset(p);
    };
    bar.appendChild(presetSel);
    this.ui.presets = presetSel;
    const toggle = (key: string, text: string, fn: (on: boolean) => void) => {
      const b = document.createElement('button');
      b.textContent = text;
      b.onclick = () => {
        const on = !b.classList.contains('on');
        b.classList.toggle('on', on);
        fn(on);
      };
      bar.appendChild(b);
      this.ui[key] = b;
    };
    if (__HAS_RENDERS__) toggle('ref', 'Compare with render', (on) => this.toggleRef(on));
    toggle('dims', 'Plan dimensions', (on) => { this.showDims = on; this.refreshDims(); });
    toggle('measure', 'Measure', (on) => { this.measuring = on; if (!on) this.measure.clear(); });
    toggle('assume', 'Assumptions', (on) => { this.ui.assumptions.hidden = !on; });
    toggle('ao', 'Soft shadows', (on) => { this.aoOn = on; });
    this.ui.ao.classList.toggle('on', this.aoOn);
    toggle('day', 'Daylight', (on) => { this.daylight = on; this.applyDaylight(); });
    this.ui.day.classList.toggle('on', this.daylight);
    const variant = document.createElement('select');
    variant.title = 'Ceiling height: the owner\'s, or the designer\'s render model (for comparing with the renders)';
    variant.add(new Option(`Ceiling ${VARIANTS.owner.rooms[0]?.ceilingHeight.toFixed(2)} m (owner)`, 'owner'));
    variant.add(new Option(`Ceiling ${VARIANTS.render.rooms[0]?.ceilingHeight.toFixed(2)} m (renders)`, 'render'));
    variant.value = DATA === VARIANTS.render ? 'render' : 'owner';
    variant.onchange = () => {
      DATA = variant.value === 'render' ? VARIANTS.render : VARIANTS.owner;
      const cam = (this.ui.presets as HTMLSelectElement).value;
      this.loadRoom(this.room.id);
      const p = this.room.cameras.find((c) => c.id === cam);
      if (p) this.applyPreset(p);
    };
    bar.appendChild(variant);
    const exp = document.createElement('label');
    exp.innerHTML = 'Exposure <input type="range" min="0.3" max="2.5" step="0.05">';
    const input = exp.querySelector('input')!;
    input.value = String(this.renderer.toneMappingExposure);
    input.oninput = () => (this.renderer.toneMappingExposure = Number(input.value));
    bar.appendChild(exp);
    document.body.appendChild(bar);

    const inspect = document.createElement('div');
    inspect.id = 'inspect';
    inspect.className = 'panel';
    inspect.hidden = true;
    document.body.appendChild(inspect);
    this.ui.inspect = inspect;
    const assumptions = document.createElement('div');
    assumptions.id = 'assumptions';
    assumptions.className = 'panel';
    assumptions.hidden = true;
    document.body.appendChild(assumptions);
    this.ui.assumptions = assumptions;
    const help = document.createElement('div');
    help.className = 'help';
    document.body.appendChild(help);
    this.ui.help = help;
  }

  private toggleRef(on: boolean): void {
    const p = this.room.cameras.find((c) => c.id === (this.ui.presets as HTMLSelectElement).value);
    if (!__HAS_RENDERS__) return; // this build leaves the designer's renders out
    this.refImg.hidden = !on || !p?.ref;
    if (p?.ref) this.refImg.src = p.ref; // relative, so it works wherever the site is hosted
    this.ui.ref?.classList.toggle('on', on);
    this.resize();
  }

  private refreshDims(): void {
    if (this.dims) this.scene.remove(this.dims);
    this.dims?.traverse((c) => (c as unknown as { element?: HTMLElement }).element?.remove());
    this.dims = this.showDims ? dimsOverlay(this.room) : null;
    if (this.dims) this.scene.add(this.dims);
  }

  // ------------------------------------------------------------------------------------------- rooms
  loadRoom(id: string): void {
    const room = DATA.rooms.find((r) => r.id === id) ?? DATA.rooms[0];
    this.room = room;
    if (this.built) {
      this.scene.remove(this.built.group);
      this.built.group.traverse((o) => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose();
      });
    }
    this.pickables.clear();
    this.built = buildRoom(room, this.pickables);
    this.scene.environmentIntensity = room.envIntensity ?? 0.45; // soft fill standing in for bounce light
    this.built.group.traverse((o) => { // see-through things go on layer 1, which the occlusion pass ignores
      const m = (o as THREE.Mesh).material as THREE.Material | undefined;
      if ((o as THREE.Mesh).isMesh && m && !Array.isArray(m) && m.transparent) o.layers.set(1);
    });
    this.ortho.layers.enable(1);
    this.scene.add(this.built.group);
    (this.ui.room as HTMLSelectElement).value = room.id;
    const ps = this.ui.presets as HTMLSelectElement;
    ps.innerHTML = '';
    ps.add(new Option('Render viewpoints...', ''));
    for (const c of room.cameras) ps.add(new Option(c.name, c.id));
    fillAssumptions(this.ui.assumptions, room);
    this.measure.clear();
    this.refreshDims();
    showInspect(this.ui.inspect, null);
    this.applyDaylight();
    this.setMode('orbit');
  }

  private applyDaylight(): void {
    this.built?.group.traverse((o) => {
      if (o.userData.group === 'daylight') o.visible = this.daylight;
    });
    this.ui.day?.classList.toggle('on', this.daylight);
  }

  setMode(m: Mode): void {
    if (this.mode === 'walk' && m !== 'walk') this.walk.controls.unlock();
    this.mode = m;
    for (const k of ['orbit', 'top', 'walk']) this.ui[`mode-${k}`]?.classList.toggle('on', k === m);
    const b = this.built!;
    b.ceiling.visible = m === 'walk' || m === 'preset';
    this.orbit.enabled = m === 'orbit' || m === 'preset';
    this.orbitTop.enabled = m === 'top';
    if (m !== 'preset') {
      this.setHidden([]);
      this.refImg.hidden = true;
      this.ui.ref?.classList.remove('on');
      (this.ui.presets as HTMLSelectElement).value = '';
    }
    this.persp.near = 0.03;
    if (m === 'orbit') {
      const c = b.center;
      const r = Math.max(b.size.x, b.size.z);
      this.persp.fov = 45;
      this.persp.position.set(c.x + r * 0.75, r * 1.55 + 1.5, c.z + r * 1.35);
      this.orbit.target.set(c.x, 0.4, c.z);
      this.orbit.update();
      this.ui.help.textContent = 'Drag to orbit, right-drag to pan, wheel to zoom. Click anything to inspect it.';
    } else if (m === 'top') {
      const c = b.center;
      this.ortho.position.set(c.x, 30, c.z);
      this.ortho.up.set(0, 0, -1); // plan orientation, not mirrored
      this.ortho.lookAt(c.x, 0, c.z);
      this.orbitTop.target.set(c.x, 0, c.z);
      this.orbitTop.update();
      this.ui.help.textContent = 'Plan view, oriented like the plan pages. Drag to pan, wheel to zoom.';
    } else if (m === 'walk') {
      const c = b.center;
      this.persp.fov = 65;
      this.persp.position.set(c.x, EYE, c.z);
      const ws = this.room.walkStart; // a free spot to start from (the middle of a big room may be furniture)
      if (ws) {
        this.persp.position.set(ws.at[0], EYE, ws.at[1]);
        this.persp.lookAt(ws.look[0], EYE, ws.look[1]);
      }
      this.walk.controls.lock();
      this.ui.help.textContent = 'Walk: W A S D or arrows, mouse to look, Shift to go faster, Esc to stop. Eye height 1.6 m.';
    }
    this.persp.updateProjectionMatrix();
    this.resize();
  }

  /** Hide elements by id (a trailing * matches a prefix); the designer hid some objects in some renders. */
  private hiddenObjs: THREE.Object3D[] = [];
  private setHidden(ids: string[]): void {
    for (const o of this.hiddenObjs) o.visible = true;
    this.hiddenObjs = [];
    if (!ids.length || !this.built) return;
    this.built.group.traverse((o) => {
      if (!o.name) return;
      if (ids.some((id) => (id.endsWith('*') ? o.name.startsWith(id.slice(0, -1)) : o.name === id))) {
        o.visible = false;
        this.hiddenObjs.push(o);
      }
    });
  }

  applyPreset(p: CameraPreset): void {
    this.setMode('preset');
    this.setHidden(p.hide ?? []);
    (this.ui.presets as HTMLSelectElement).value = p.id;
    this.persp.fov = p.fov;
    this.persp.near = p.near ?? 0.03; // render cameras outside the room see through the wall behind them
    if (p.daylight !== undefined && this.params.get('day') === null) {
      this.daylight = p.daylight;
      this.applyDaylight();
    }
    this.persp.position.set(...p.position);
    this.orbit.target.set(...p.target);
    this.persp.lookAt(...p.target);
    this.orbit.update();
    this.persp.updateProjectionMatrix();
    if (this.ui.ref?.classList.contains('on')) this.toggleRef(true);
    this.ui.help.textContent = `${p.name}: viewpoint estimated from the reference render.` +
      (__HAS_RENDERS__ ? ' "Compare with render" overlays it.' : '') +
      (p.hide?.length ? ` Hidden here, as in the render: ${p.hideNote ?? p.hide.join(', ')}.` : '');
  }

  // ------------------------------------------------------------------------------------------- input
  private bindClicks(): void {
    let down: { x: number; y: number } | null = null;
    const el = this.renderer.domElement;
    el.addEventListener('pointerdown', (e) => (down = { x: e.clientX, y: e.clientY }));
    el.addEventListener('pointerup', (e) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4 || this.mode === 'walk') return;
      const rect = el.getBoundingClientRect();
      const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      const ray = new THREE.Raycaster();
      ray.setFromCamera(ndc, this.activeCamera());
      const hits = ray.intersectObject(this.built!.group, true).filter((h) => h.object.visible && (h.object as THREE.Mesh).isMesh);
      const hit = hits.find((h) => !(this.mode !== 'walk' && this.mode !== 'preset' && this.pickables.get(h.object)?.kind === 'ceiling'));
      if (!hit) return;
      if (this.measuring) {
        this.measure.click(hit.point);
        return;
      }
      let o: THREE.Object3D | null = hit.object;
      while (o && !this.pickables.has(o)) o = o.parent;
      showInspect(this.ui.inspect, o ? this.pickables.get(o)! : null, hit.point);
    });
  }

  private activeCamera(): THREE.Camera {
    return this.mode === 'top' ? this.ortho : this.persp;
  }

  private resize(): void {
    const w = window.innerWidth;
    const h = window.innerHeight;
    this.renderer.setSize(w, h);
    this.labels.setSize(w, h);
    this.composer.setSize(w, h);
    this.gtao.setSize(w, h);
    this.persp.aspect = w / h;
    this.persp.updateProjectionMatrix();
    if (this.built) {
      const s = this.built.size;
      const half = Math.max(s.x * (h / w), s.z) * 0.62 + 0.4;
      this.ortho.left = (-half * w) / h;
      this.ortho.right = (half * w) / h;
      this.ortho.top = half;
      this.ortho.bottom = -half;
      this.ortho.updateProjectionMatrix();
    }
  }

  private frame(): void {
    const dt = Math.min(this.clock.getDelta(), 0.1);
    if (this.mode === 'walk') this.walk.update(dt);
    else if (this.mode === 'top') this.orbitTop.update();
    else this.orbit.update();
    const cam = this.activeCamera();
    if (this.aoOn && cam === this.persp) {
      this.aoCam.copy(this.persp);
      this.aoCam.layers.set(0);
      this.composer.render();
    } else this.renderer.render(this.scene, cam);
    this.labels.render(this.scene, cam);
    if (++this.frames === 6) (window as unknown as { __ready: boolean }).__ready = true;
  }
}
