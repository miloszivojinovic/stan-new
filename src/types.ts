// Shape of data/rooms.json (written by tools/build_rooms.py). Room frame, metres:
// x = plan x (right), z = plan y (down the page), y = up; floor at y = 0. three.js world uses the same axes.

export type Source = 'plan' | 'label' | 'owner' | 'estimated' | 'assumed' | string;
export type V2 = [number, number]; // [x, z]
export type V3 = [number, number, number]; // [x, y, z]

export interface MaterialDef {
  color: string;
  roughness?: number;
  metalness?: number;
  texture?: 'wood-planks' | 'plaster' | 'walnut' | 'tiles' | 'rug' | 'door-inlay' | 'oak' | 'fabric' | 'stone' | 'checker' | 'plank-tiles';
  textureScale?: number; // metres per texture repeat
  tile?: [number, number]; // tile size in metres for 'tiles' / 'checker' / 'plank-tiles'
  groutColor?: string;
  altColor?: string; // second colour for 'checker'
  emissive?: string;
  emissiveIntensity?: number;
  opacity?: number;
  transmission?: number;
  source?: Source;
  from?: string;
}

export interface Wall {
  id: string;
  side: 'top' | 'bottom' | 'left' | 'right' | 'other';
  a: V2;
  b: V2;
  thickness: number;
  exterior: boolean;
  material?: string;
}

export interface Opening {
  id: string;
  name: string;
  type: 'door' | 'window' | 'balcony-door' | 'passage';
  wall: string;
  t0: number; // metres along the wall from wall.a
  t1: number;
  sill: number;
  head: number;
  leafWidth?: number;
  hinge?: 't0' | 't1';
  frameMaterial?: string;
  leafMaterial?: string;
  sashes?: number;
  box?: number; // roller-shutter box at the top of a window or balcony door (m)
  sources: Record<string, Source>;
  notes?: string;
}

export interface Element {
  id: string;
  name: string;
  label?: string; // Serbian label on the plan
  type: string;
  footprint?: V2[];
  y0?: number;
  y1?: number;
  center?: V3;
  size?: V3;
  rotationY?: number;
  material?: string;
  materials?: Record<string, string>;
  params?: Record<string, unknown>;
  sources: Record<string, Source>;
  notes?: string;
}

export interface LightDef {
  id: string;
  type: 'spot' | 'point' | 'rect' | 'led-strip' | 'hemisphere' | 'window-sky';
  position?: V3;
  target?: V3;
  from?: V3;
  to?: V3;
  width?: number;
  height?: number;
  color: string;
  intensity: number;
  angle?: number;
  penumbra?: number;
  distance?: number;
  castShadow?: boolean;
  facing?: V3;
  groundColor?: string;
  group?: 'daylight';
  light?: boolean; // led-strip: false = glowing strip only, no area light
  sources?: Record<string, Source>;
}

export interface CameraPreset {
  id: string;
  name: string;
  position: V3;
  target: V3;
  fov: number; // vertical, degrees
  ref?: string; // reference render, relative to the project root
  near?: number; // near clipping plane (m): cameras outside the room look through the wall, like the renders
  daylight?: boolean; // false for evening renders
  hide?: string[]; // element ids (trailing * = prefix) the designer left out of this render
  hideNote?: string;
  aspect?: number;
  source?: Source;
}

export interface PlanDim {
  id: string;
  label_cm: number | null;
  geometry_cm: number | null;
  status: string;
  a: V2;
  b: V2;
}

export interface Assumption {
  item: string;
  value: string;
  source: Source;
  note?: string;
}

export interface Room {
  id: string;
  name: string;
  name_sr?: string;
  ceilingHeight: number;
  polygon: V2[];
  walls: Wall[];
  openings: Opening[];
  materials: Record<string, MaterialDef>;
  floorMaterial: string;
  wallMaterial: string;
  ceilingMaterial: string;
  skirting?: { height: number; material: string };
  elements: Element[];
  lights: LightDef[];
  cameras: CameraPreset[];
  dims: PlanDim[];
  outside?: { sky: string; ground: string };
  // parts of the room with a lower ceiling (soffits); only in the render-model variant
  ceilingZones?: { id: string; name: string; height: number; polygon: V2[] }[];
  envIntensity?: number; // strength of the neutral environment fill light (default 0.45)
  walkStart?: { at: V2; look: V2 }; // where walk mode starts and which way it looks
  assumptions: Assumption[];
}

export interface RoomsData {
  version: number;
  variant?: 'owner' | 'render';
  generated_by: string;
  frame: string;
  rooms: Room[];
}
