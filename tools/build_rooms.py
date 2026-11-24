"""Build data/rooms.json (what the viewer loads) from three inputs:

  data/plan-geometry.json   geometry extracted from the plan PDF          -> source "plan"
  data/owner-facts.json     the owner's answers                           -> source "owner"
  data/room-specs/*.json    per-room choices: element types, heights, materials, lights, cameras. Every value there
                            is either a plain plan reference or {"value": ..., "source": "estimated"|"assumed", ...}

Every estimated, assumed or owner value is also listed in the room's "assumptions" for the viewer's panel.

Heights may be written relative to the ceiling: "ceiling", or {"below_ceiling": d}; "ceiling" means the ceiling above
that element, so it follows "ceiling_zones" (render variant only: parts of the scene where the designer's model has a
lower ceiling, built as soffits). Two variants are written:
  data/rooms.json         ceiling from the owner (2.40 m)
  data/rooms-render.json  ceiling of the designer's render model (from data/estimates/<room>.json), used to check
                          the model against the reference renders; a value may carry "render": x for this variant.
Usage: tools/.venv/Scripts/python tools/build_rooms.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "data" / "plan-geometry.json"
OWNER = ROOT / "data" / "owner-facts.json"
SPECS = ROOT / "data" / "room-specs"
OUT = ROOT / "data" / "rooms.json"


class Log:
    def __init__(self):
        self.rows = []

    def add(self, item, value, source, note=""):
        self.rows.append({"item": item, "value": value, "source": source, **({"note": note} if note else {})})


H = 2.40  # ceiling height of the variant being built (set per room in build_room)
VARIANT = "owner"
ZONES = []  # [(shapely polygon, height)] lower-ceiling zones of the room being built (render variant only)


def ceiling_at(at=None):
    """Ceiling height above a point (x, z) or a rect (x0, x1, z0, z1): a zone's height if the point or the whole rect
    lies in that zone, else the main ceiling H (the highest), so tall items reach the ceiling above all of them."""
    if at is None or not ZONES:
        return H
    from shapely.geometry import Point, box as sbox
    geom = Point(at[0], at[1]) if len(at) == 2 else sbox(at[0], at[2], at[1], at[3])
    for poly, h in ZONES:
        if poly.buffer(0.005).covers(geom):
            return h
    return H


def resolve_y(v, at=None):
    """Height that may be relative to the ceiling above `at`, or carry a separate "render" value."""
    if v == "ceiling":
        return round(ceiling_at(at), 4)
    if isinstance(v, dict):
        if VARIANT == "render" and "render" in v:
            return resolve_y(v["render"], at)
        if "below_ceiling" in v:
            return round(ceiling_at(at) - v["below_ceiling"], 4)
        if "value" in v:
            return resolve_y(v["value"], at)
    if isinstance(v, list):
        return [resolve_y(x, at) for x in v]
    return v


def val(v, item: str, log: Log, unit: str = "m", default_source: str = "assumed", at=None):
    """Unwrap {"value", "source", "from"/"note"} (and "render" for the render variant); log non-plan values."""
    if isinstance(v, dict) and "value" in v:
        src = v.get("source", default_source)
        out = v["render"] if VARIANT == "render" and "render" in v else v["value"]
        out = resolve_y(out, at)
        if src not in ("plan", "label"):
            shown = f"{out:.2f} {unit}" if isinstance(out, (int, float)) and unit else str(out)
            log.add(item, shown, src, v.get("from", v.get("note", "")))
        return out
    return resolve_y(v, at)


def point_in_poly(x, z, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, zi = poly[i]
        xj, zj = poly[j]
        if (zi > z) != (zj > z) and x < (xj - xi) * (z - zi) / (zj - zi) + xi:
            inside = not inside
        j = i
    return inside


def wall_side(a, b, poly):
    mx, mz = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    if abs(a[1] - b[1]) < 1e-6:
        return "top" if point_in_poly(mx, mz + 0.05, poly) else "bottom"
    if abs(a[0] - b[0]) < 1e-6:
        return "left" if point_in_poly(mx + 0.05, mz, poly) else "right"
    return "other"


def on_wall(p, w, tol=0.02):
    (ax, az), (bx, bz) = w["a"], w["b"]
    L = math.hypot(bx - ax, bz - az)
    ux, uz = (bx - ax) / L, (bz - az) / L
    t = (p[0] - ax) * ux + (p[1] - az) * uz
    d = abs((p[0] - ax) * -uz + (p[1] - az) * ux)
    return (t, d) if d <= tol and -tol <= t <= L + tol else None


def resolve_opening(o, plan, walls, log: Log):
    name = o.get("name", o["id"])
    sources = {}
    if "plan" in o:
        po = next(p for p in plan["openings"] if p["id"] == o["plan"])
        for w in walls:
            ta, tb = on_wall(po["from"], w), on_wall(po["to"], w)
            if ta and tb:
                break
        else:
            raise SystemExit(f"opening {o['plan']} is not on any wall of {plan['id']}")
        t0, t1 = sorted((ta[0], tb[0]))
        sources["position"] = f"plan ({o['plan']}: {po['width_cm']} cm across the drawn casings)"
        trim = val(o.get("trim", 0.0), f"{name}: casing width (opening = drawn span minus 2 casings)", log)
        t0, t1 = t0 + trim, t1 - trim
    else:
        rng = val(o["range"], f"{name}: extent along the {o['wall']} wall", log)
        for w in walls:  # the wall of that side that contains the whole extent
            if w["side"] != o["wall"]:
                continue
            ta, tb = on_wall(_along(w, rng[0]), w, 0.05), on_wall(_along(w, rng[1]), w, 0.05)
            if ta and tb:
                break
        else:
            raise SystemExit(f"{name}: no {o['wall']} wall of {plan['id']} contains {rng}")
        t0, t1 = sorted((ta[0], tb[0]))
        sources["position"] = o["range"].get("source", "estimated") if isinstance(o["range"], dict) else "plan"
    sill = val(o.get("sill", 0.0), f"{name}: sill height", log)
    head = val(o["head"], f"{name}: head height", log)
    out = {"id": o["id"], "name": name, "type": o["type"], "wall": w["id"], "t0": round(t0, 4), "t1": round(t1, 4),
           "sill": sill, "head": head, "sources": {**sources, "sill": _src(o.get("sill")), "head": _src(o["head"])}}
    for k in ("leafWidth", "hinge", "frameMaterial", "leafMaterial", "sashes", "box"):
        if k in o:
            out[k] = val(o[k], f"{name}: {k}", log)
    if "latch" in o:  # "x-min" | "x-max" | "z-min" | "z-max": which end of the opening has the handle
        axis = 0 if o["latch"][0] == "x" else 1
        (ax, az), (bx, bz) = w["a"], w["b"]
        grows = (bx - ax, bz - az)[axis] > 0  # does t increase along that axis?
        latch_at_t0 = grows == o["latch"].endswith("min")
        out["hinge"] = "t1" if latch_at_t0 else "t0"
        sources["handle side"] = val(o.get("latch_source", {"value": o["latch"], "source": "estimated"}), f"{name}: handle side", log, unit="")
    if "notes" in o:
        out["notes"] = o["notes"]
    return out


def _along(w, coord):
    """Point on wall w at the given x (for horizontal walls) or z (vertical walls)."""
    (ax, az), (bx, bz) = w["a"], w["b"]
    return [coord, az] if abs(az - bz) < 1e-6 else [ax, coord]


def _src(v):
    if isinstance(v, dict):
        return v.get("source", "assumed") + (f" ({v['from']})" if v.get("from") else "")
    return "plan" if v is not None else "n/a"


def resolve_element(e, plan, log: Log):
    name = e.get("name", e["id"])
    out = {"id": e["id"], "name": name, "type": e["type"], "sources": {}}
    if "plan" in e:
        ref = e["plan"]
        cands = [p for p in plan["elements"] if p["label"] == ref["label"]]
        pe = cands[ref.get("index", 0)]
        use = ref.get("use", "footprint")
        if use not in pe:
            raise SystemExit(f"{plan['id']}: element {ref['label']} has no {use}")
        out["footprint"] = pe[use]
        out["label"] = pe["label"]
        out["sources"]["footprint"] = f"plan ({pe['label']}, {pe.get('size_merged_cm' if use == 'footprint_merged' else 'size_cm')} cm)"
    if "rect" in e:
        r = e["rect"]
        x0, x1 = val(r["x"], f"{name}: x extent", log) if isinstance(r["x"], dict) else r["x"]
        z0, z1 = val(r["z"], f"{name}: z extent", log) if isinstance(r["z"], dict) else r["z"]
        out["footprint"] = [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]
        out["sources"]["footprint"] = r.get("source", "estimated") + (f" ({r['from']})" if r.get("from") else "")
        if r.get("source", "estimated") not in ("plan", "label"):
            log.add(f"{name}: footprint", f"x {x0:.2f}-{x1:.2f}, z {z0:.2f}-{z1:.2f} m", r.get("source", "estimated"), r.get("from", ""))
    at = _element_at(e, out)
    for k in ("y0", "y1"):
        if k in e:
            out[k] = val(e[k], f"{name}: {'bottom' if k == 'y0' else 'top'} height", log, at=at)
            out["sources"]["bottom" if k == "y0" else "height"] = _src(e[k])
    for k in ("center", "size"):
        if k in e:
            out[k] = val(e[k], f"{name}: {k}", log, unit="")
            out["sources"][k] = _src(e[k])
    for k in ("rotationY", "material", "materials", "notes", "label"):
        if k in e:
            out[k] = e[k]
    if "params" in e:
        p = dict(e["params"])
        if "parts" in p:  # boxes [{x:[a,b], y:[a,b], z:[a,b], m:material}], y may be ceiling-relative
            p["parts"] = [{**part, "y": resolve_y(part["y"], (part["x"][0], part["x"][1], part["z"][0], part["z"][1]))}
                          for part in p["parts"]]
        for k in ("height", "railY", "rodY", "top", "bottom"):
            if k in p:
                p[k] = resolve_y(p[k], at)
        if e["type"] in ("led-line", "pendant", "ceiling-disc", "track"):
            p["ceilingY"] = round(ceiling_at(at), 4)  # the ceiling these hang from or sit in
        out["params"] = p
    if "source_note" in e:
        out["sources"]["note"] = e["source_note"]
    return out


def _wall_matches(side, a, b, rule):
    """"right" matches every right wall; "right@10.84" only the right wall at x = 10.84 (z for top/bottom walls)."""
    name, _, at = rule.partition("@")
    if name != side:
        return False
    if not at:
        return True
    coord = a[0] if side in ("left", "right") else a[1]
    return abs(coord - float(at)) < 0.01


def _element_at(e, out):
    """Where an element is, for ceiling-relative heights: footprint rect, centre, or the from/to/at of its params."""
    if out.get("footprint"):
        xs = [q[0] for q in out["footprint"]]
        zs = [q[1] for q in out["footprint"]]
        return (min(xs), max(xs), min(zs), max(zs))
    c = e.get("center")
    if isinstance(c, dict):
        c = c.get("value")
    if isinstance(c, list) and len(c) == 3:
        return (c[0], c[2])
    p = e.get("params", {})
    if "from" in p and "to" in p:
        a, b = p["from"], p["to"]
        return (min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]))
    if "at" in p:
        return tuple(p["at"][:2])
    return None


def _ceiling_zones(spec, poly, log: Log):
    """Lower-ceiling zones (render variant): rect x [a,b], z [a,b] clipped to the room outline."""
    from shapely.geometry import Polygon, box as sbox
    room_poly = Polygon(poly)
    zones, out = [], []
    for z in spec.get("ceiling_zones", []):
        h = val(z["height"], f"Ceiling over the {z['name']}", log)
        if h >= H - 1e-4:
            continue
        clip = room_poly.intersection(sbox(z["x"][0], z["z"][0], z["x"][1], z["z"][1]))
        parts = [clip] if clip.geom_type == "Polygon" else [g for g in getattr(clip, "geoms", []) if g.geom_type == "Polygon"]
        for k, g in enumerate(parts):
            if g.area < 1e-4:
                continue
            zones.append((g, h))
            out.append({"id": f"{z['id']}-{k}", "name": z["name"], "height": h,
                        "polygon": [[round(x, 4), round(y, 4)] for x, y in list(g.exterior.coords)[:-1]]})
    return zones, out


def build_room(spec, plan, owner, variant="owner"):
    global H, VARIANT, ZONES
    log = Log()
    VARIANT = variant
    ZONES = []
    if variant == "render":
        if "render_ceiling" in spec:  # e.g. a scene made from several estimate files
            H = spec["render_ceiling"]
        else:
            est = json.loads((ROOT / "data" / "estimates" / f"{spec['id']}.json").read_text(encoding="utf-8"))
            H = est["scale"]["render_ceiling_height_m"]
        log.add("Ceiling height", f"{H:.2f} m", "estimated", "render model, for comparing with the renders only; "
                f"the owner's height is {owner['ceiling_height_m']['value']:.2f} m")
    else:
        H = owner["ceiling_height_m"]["value"]
        log.add("Ceiling height", f"{H:.2f} m", "owner", "all rooms")
    poly = plan["interior"]["polygon"]
    ZONES, zone_out = _ceiling_zones(spec, poly, log)
    t_int = owner["interior_wall_thickness_m"]["value"]
    log.add("Interior wall thickness", f"{t_int:.2f} m", "owner", "plans draw about 0.20 m")
    t_ext = val(spec.get("exterior_thickness", {"value": 0.20, "source": "assumed"}), "Exterior wall thickness", log)
    walls = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        side = wall_side(a, b, poly)
        ext = any(_wall_matches(side, a, b, rule) for rule in spec.get("exterior_walls", []))
        walls.append({"id": f"w{i}", "side": side, "a": a, "b": b, "thickness": t_ext if ext else t_int, "exterior": ext})
    mats = {}
    for k, m in spec["materials"].items():
        mats[k] = m
        if m.get("source") not in (None, "plan", "label"):
            log.add(f"Material {k}", f"{m['color']}{' ' + m['texture'] if m.get('texture') else ''}", m["source"], m.get("from", ""))
    room = {
        "id": spec["id"], "name": spec["name"], "name_sr": spec.get("name_sr", ""), "ceilingHeight": H,
        "polygon": poly, "walls": walls,
        "openings": [resolve_opening(o, plan, walls, log) for o in spec.get("openings", [])],
        "materials": mats, "floorMaterial": spec["floor"], "wallMaterial": spec["wall"], "ceilingMaterial": spec["ceiling"],
        "elements": [resolve_element(e, plan, log) for e in spec.get("elements", []) if e.get("variant", variant) == variant],
        "lights": [],
        "cameras": [],
        "dims": plan.get("dims", []),
        "assumptions": [],
    }
    if zone_out:
        room["ceilingZones"] = zone_out
    if "walk_start" in spec:  # {"at": [x, z], "look": [x, z]}
        room["walkStart"] = spec["walk_start"]
    if "env_intensity" in spec:  # neutral environment fill (stand-in for bounce light), viewer default 0.45
        room["envIntensity"] = val(spec["env_intensity"], "Environment fill light", log, unit="")
    if "skirting" in spec:
        s = spec["skirting"]
        room["skirting"] = {"height": val(s["height"], "Skirting height", log), "material": s["material"]}
    if "outside" in spec:
        room["outside"] = spec["outside"]
    for l in spec.get("lights", []):
        if l.get("source") not in (None, "plan"):
            note = l.get("from_render") or (l["from"] if isinstance(l.get("from"), str) else "")
            log.add(f"Light {l['id']}", f"{l['type']}, intensity {l.get('intensity')}", l["source"], note)
        room["lights"].append({k: [v[0], resolve_y(v[1], (v[0], v[2])), v[2]] if k in ("position", "target", "from", "to") and isinstance(v, list) and len(v) == 3 else v
                               for k, v in l.items() if k not in ("source",)})
    for c in spec.get("cameras", []):
        log.add(f"Viewpoint {c['id']}", f"position {c['position']}, vertical fov {c['fov']}", c.get("source", "estimated"), c.get("from", ""))
        room["cameras"].append(c)
    room["assumptions"] = log.rows
    return room


def main():
    geo = json.loads(GEO.read_text(encoding="utf-8"))
    owner = json.loads(OWNER.read_text(encoding="utf-8"))
    for variant, out_path in (("owner", OUT), ("render", OUT.with_name("rooms-render.json"))):
        rooms = []
        specs = sorted(SPECS.glob("*.json"), key=lambda f: (json.loads(f.read_text(encoding="utf-8")).get("order", 99), f.name))
        for f in specs:  # "order" in a spec sets its place in the viewer's room switcher
            spec = json.loads(f.read_text(encoding="utf-8"))
            plan = next(r for r in geo["rooms"] if r["id"] == spec["plan_room"])
            rooms.append(build_room(spec, plan, owner, variant))
            print(f"[{variant}] {spec['id']}: ceiling {rooms[-1]['ceilingHeight']:.3f} m, {len(rooms[-1]['walls'])} walls, "
                  f"{len(rooms[-1]['openings'])} openings, {len(rooms[-1]['elements'])} elements, {len(rooms[-1]['assumptions'])} assumption rows")
        out = {"version": 1, "variant": variant, "generated_by": "tools/build_rooms.py", "frame": geo["frame"], "rooms": rooms}
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
