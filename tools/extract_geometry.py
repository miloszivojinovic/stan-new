"""Extract plan geometry from the layout PDF and validate it against the labelled dimensions.

Usage:  tools/.venv/Scripts/python tools/extract_geometry.py [--pages 1,2,3,4,5]
Writes: data/plan-geometry.json, debug/p<N>-geometry.png, debug/p<N>-openings.png, debug/validation.md

Coordinates in the JSON are metres in a per-room frame: x = plan x (right), z = plan y (down the page),
origin at the top-left of the room's interior bounding box. Nothing is mirrored.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
import plangeo as pg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "source" / "Milo_-dispozicija_elemenata.pdf"
LABELS = ROOT / "data" / "layout-labels.json"
OUT_JSON = ROOT / "data" / "plan-geometry.json"
DEBUG = ROOT / "debug"

GLOSSARY = {
    "TRP. STO": "dining table", "POLICA/PARAVAN": "shelf / room divider", "OGLEDALO + SEDENJE": "mirror + bench",
    "CIPELARNIK": "shoe cabinet", "KONZOLNA POLICA": "console shelf", "KUHINJSKO OSTRVO": "kitchen island",
    "KUHINJA": "kitchen", "TV SISTEM SA SKRIVAČEM ZA KLIMU": "TV unit with AC concealer", "POLICA": "shelf",
    "RADNI STO": "desk", "GARDEROBER": "wardrobe", "GARDEROBER SA BOČNOM POLICOM": "wardrobe with side shelf",
    "POLICA ZA TV": "TV shelf", "SKRIVAČ ZA RADIJATOR": "radiator cover", "NOĆNI SOČIĆI": "bedside table",
    "POLICA IZNAD ŠOLJE": "shelf above the toilet", "POLICA SA OGLEDALOM": "shelf with mirror",
    "SKRIVAČ ZA MAŠINE": "washing-machine concealer", "/SKRIVAČ ZA BOJLER": "boiler concealer",
}
# regression tests from CLAUDE.md: interior sizes measured independently (cm), tolerance 2 cm
REGRESSION = {"work-room": (237.5, 442.6), "childrens-room": (405.4, 281.6), "toilet": (87.0, 238.0)}
TOL_CM = 2.0


def px(p):
    return int(round(p[0] * pg.PX_PER_PT)), int(round(p[1] * pg.PX_PER_PT))


def side_name(f: pg.Feature) -> str:
    if f.orient == "h":
        return "top" if f.inward > 0 else "bottom"
    return "left" if f.inward > 0 else "right"


def room_of(point, rooms):
    best = None
    for r in rooms:
        x0, y0, x1, y1 = r["bbox_pt"]
        d = math.hypot(max(x0 - point[0], 0, point[0] - x1), max(y0 - point[1], 0, point[1] - y1))
        if best is None or d < best[0]:
            best = (d, r)
    return best[1] if best else None


def faces_of(poly, src):
    out = []
    for p, q in zip(poly, poly[1:] + poly[:1]):
        if abs(q[0] - p[0]) < 1e-6:
            out.append(("v", p[0], min(p[1], q[1]), max(p[1], q[1]), src))
        elif abs(q[1] - p[1]) < 1e-6:
            out.append(("h", p[1], min(p[0], q[0]), max(p[0], q[0]), src))
    return out


def find_face(pt, dot, measure_x: bool, faces, tol, reach):
    """Feature edge for one end of a dimension. LayOut draws the extension line only part of the way, so the
    feature can be anywhere further along the extension direction (dot -> measured point -> beyond)."""
    pos = pt[0] if measure_x else pt[1]
    p_perp, d_perp = (pt[1], dot[1]) if measure_x else (pt[0], dot[0])
    if abs(p_perp - d_perp) < 1e-6:
        r0, r1 = d_perp - reach, d_perp + reach
    elif p_perp < d_perp:
        r0, r1 = p_perp - reach, d_perp
    else:
        r0, r1 = d_perp, p_perp + reach
    best = None
    for o, c, lo, hi, src in faces:
        if (o == "v") != measure_x or abs(c - pos) > tol or hi < r0 or lo > r1:
            continue
        gap = 0.0 if lo <= p_perp <= hi else min(abs(lo - p_perp), abs(hi - p_perp))
        key = (abs(c - pos) + 0.01 * gap)
        if best is None or key < best[0]:
            best = (key, c, src)
    return best


def validate_dims(pv, rooms, elements, idx, k):
    tol = TOL_CM / k
    geo_faces = []
    for r in rooms:
        geo_faces += faces_of(r["polygon_pt"], "wall")
    for e in elements:
        if e.get("polygon_pt"):
            geo_faces += faces_of(e["polygon_pt"], f"element:{e['name_en']}")
        if e.get("merged"):
            geo_faces += faces_of(e["merged"]["polygon_pt"], f"element:{e['name_en']} (merged)")
    raw = [("v", float(c), float(a), float(b), "edge") for c, a, b in idx.v] + [("h", float(c), float(a), float(b), "edge") for c, a, b in idx.h]
    out = []
    for d in pv.dims:
        label = d.label["value"] if d.label else None
        line_cm = d.length_pt * k
        mx = not d.vertical
        reach = 250 / k
        fa = find_face(d.pa, d.a, mx, geo_faces, tol, reach) or find_face(d.pa, d.a, mx, raw, tol / 4, reach)
        fb = find_face(d.pb, d.b, mx, geo_faces, tol, reach) or find_face(d.pb, d.b, mx, raw, tol / 4, reach)
        meas = abs(fb[1] - fa[1]) * k if fa and fb else None
        srcs = [f[2] if f else None for f in (fa, fb)]
        geo = all(s and s != "edge" for s in srcs)
        if label is None:
            status = "unlabelled"
        elif abs(label - line_cm) > TOL_CM:
            status = "FAIL label vs line"
        elif meas is None:
            status = "no feature found"
        elif abs(label - meas) > TOL_CM:
            status = "FAIL label vs geometry"
        else:
            status = "pass" if geo else "pass (raw model edge)"
        out.append({"id": d.id, "page": pv.page, "label_cm": label, "line_cm": round(line_cm, 1),
                    "geometry_cm": round(meas, 1) if meas is not None else None, "features": srcs, "status": status,
                    "a_pt": [round(v, 2) for v in d.pa], "b_pt": [round(v, 2) for v in d.pb], "vertical": d.vertical,
                    "line_a_pt": [round(v, 2) for v in d.a], "line_b_pt": [round(v, 2) for v in d.b]})
    return out


def to_local(p, origin, m_per_pt):
    return [round((p[0] - origin[0]) * m_per_pt, 4), round((p[1] - origin[1]) * m_per_pt, 4)]


def draw_overlays(pno, img, clean, pv, rooms, openings, elements, dims, wall):
    o = img.copy()
    tint = o.copy()
    tint[wall] = (0, 160, 255)
    o = cv2.addWeighted(tint, 0.25, o, 0.75, 0)
    for r in rooms:
        cv2.polylines(o, [np.array([px(p) for p in r["polygon_pt"]], np.int32)], True, (0, 190, 0), 3)
    for e in elements:
        if e.get("polygon_pt"):
            cv2.polylines(o, [np.array([px(p) for p in e["polygon_pt"]], np.int32)], True, (255, 120, 0), 3)
    for d in dims:
        col = (0, 170, 0) if d["status"].startswith("pass") else (220, 0, 0)
        a, b = px(d["a_pt"]), px(d["b_pt"])
        cv2.line(o, a, b, col, 3)
        txt = f"{d['label_cm']}/{d['geometry_cm']}"
        cv2.putText(o, txt, ((a[0] + b[0]) // 2 + 6, (a[1] + b[1]) // 2 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
    letters_overlay(o, openings, big=False)
    crop_save(o, pv, DEBUG / f"p{pno}-geometry.png")
    c2 = clean.copy()
    for r in rooms:
        cv2.polylines(c2, [np.array([px(p) for p in r["polygon_pt"]], np.int32)], True, (0, 150, 0), 2)
    letters_overlay(c2, openings, big=True)
    crop_save(c2, pv, DEBUG / f"p{pno}-openings.png")


KIND_COL = {"gap": (0, 60, 255), "door?": (230, 0, 120), "window?": (0, 150, 230), "radiator?": (150, 150, 150), "opening?": (255, 140, 0)}


def letters_overlay(o, openings, big):
    for op in openings:
        col = KIND_COL.get(op["kind"], (255, 140, 0))
        a, b = px(op["a_pt"]), px(op["b_pt"])
        cv2.line(o, a, b, col, 12 if big else 7)
        cx, cy = (a[0] + b[0]) // 2, (a[1] + b[1]) // 2
        nx, ny = op["outward_px"]
        tx, ty = cx + nx * (70 if big else 45), cy + ny * (70 if big else 45)
        r = 34 if big else 24
        cv2.circle(o, (tx, ty), r, (255, 255, 255), -1)
        cv2.circle(o, (tx, ty), r, col, 4)
        fs = 1.4 if big else 1.0
        (tw, th), _ = cv2.getTextSize(op["letter"], cv2.FONT_HERSHEY_SIMPLEX, fs, 3)
        cv2.putText(o, op["letter"], (tx - tw // 2, ty + th // 2), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 3)


def crop_save(o, pv, path):
    x0 = min(v[0] for v in pv.viewports) - 25
    y0 = min(v[1] for v in pv.viewports) - 25
    x1 = max(v[2] for v in pv.viewports) + 25
    y1 = max(v[3] for v in pv.viewports) + 25
    c = o[max(0, int(y0 * pg.PX_PER_PT)):int(y1 * pg.PX_PER_PT), max(0, int(x0 * pg.PX_PER_PT)):int(x1 * pg.PX_PER_PT)]
    cv2.imwrite(str(path), cv2.cvtColor(c, cv2.COLOR_RGB2BGR))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default="1,2,3,4,5")
    args = ap.parse_args()
    DEBUG.mkdir(exist_ok=True)
    doc = pymupdf.open(PDF)
    labels = pg.load_labels(LABELS)
    result = {"source": "source/Milo_-dispozicija_elemenata.pdf", "generated_by": "tools/extract_geometry.py",
              "frame": "metres per room: x = plan x (right), z = plan y (down the page), origin = top-left of the room's "
                       "interior bounding box; *_pt fields are PDF points on the page (origin top-left)",
              "rooms": [], "dimensions": [], "regression": []}
    for pno in [int(p) for p in args.pages.split(",")]:
        scale = pg.PAGES[pno]["scale"]
        k = pg.mm_per_pt(scale) / 10  # cm per pt
        m_per_pt = k / 100
        pv = pg.load_vectors(doc, pno, pg.page_labels(labels, pno))
        page = doc[pno - 1]
        img = pg.render(page)
        clean = pg.viewport_raster(doc, page, img)
        wall = pg.wall_mask(img)
        balcony = pg.balcony_mask(img, wall, scale)
        bg = pg.background_mask(img)
        foot = pg.footprint(img, wall, bg | balcony, scale)
        gaps = pg.outer_gaps(wall, foot, scale)
        masks = pg.room_masks(foot, pg.close_gaps(wall, gaps), scale)
        names = pg.PAGES[pno]["rooms"]
        masks = sorted(sorted(masks, key=lambda m: -m.sum())[: len(names)], key=lambda m: np.argwhere(m)[:, 1].mean())
        idx = pg.EdgeIndex(pv.segments)
        rooms = []
        for name, m in zip(names, masks):
            poly, ov = pg.clean_room_polygon(m, scale, idx)
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            rooms.append({"id": name, "page": pno, "scale": scale, "polygon_pt": poly, "mask": m,
                          "bbox_pt": (min(xs), min(ys), max(xs), max(ys)), "snapped_edges": f"{sum(v > 0 for v in ov)}/{len(ov)}"})
        # openings and radiators along each room's wall faces
        openings = []
        for r in rooms:
            for f in pg.wall_face_features(r["polygon_pt"], r["mask"], wall, pv.segments, scale, gaps):
                a = (f.a0, f.face) if f.orient == "h" else (f.face, f.a0)
                b = (f.a1, f.face) if f.orient == "h" else (f.face, f.a1)
                out_dir = (0, -f.inward) if f.orient == "h" else (-f.inward, 0)
                openings.append({"room": r["id"], "kind": f.kind, "wall_side": side_name(f), "a_pt": a, "b_pt": b,
                                 "width_cm": round((f.a1 - f.a0) * k, 1), "evidence": f.evidence, "outward_px": out_dir})
        openings.sort(key=lambda o: (round((o["a_pt"][1] + o["b_pt"][1]) / 2 / 40), (o["a_pt"][0] + o["b_pt"][0]) / 2))
        for i, op in enumerate(openings):
            op["letter"] = chr(ord("A") + i) if i < 26 else f"A{i - 25}"
        # labelled elements via leader arrows
        elements = []
        flab = pg.face_labels(clean, pv.segments)
        for ld in pv.leaders:
            reg = pg.element_faces(flab, clean, ld, scale)
            name = ld.label["text"] if ld.label else "?"
            e = {"label": name, "name_en": GLOSSARY.get(name, name.lower()), "tip_pt": [round(v, 2) for v in ld.tip]}
            # second, looser pass: steps over bundles of edge lines (separate carcasses of one cabinet), but never
            # into a face that holds another element's leader tip
            others = [o.tip for o in pv.leaders if o is not ld]
            reg9 = pg.element_faces(flab, clean, ld, scale, ring_px=9, blocked=others)
            if reg9 is not None and reg is not None and reg9.sum() > reg.sum() * 1.05:
                p9, _ = pg.clean_room_polygon(reg9, scale, idx)
                xs9, ys9 = [p[0] for p in p9], [p[1] for p in p9]
                e["merged"] = {"polygon_pt": p9, "size_cm": [round((max(xs9) - min(xs9)) * k, 1), round((max(ys9) - min(ys9)) * k, 1)]}
            if reg is None:
                e["status"] = "footprint not extracted (face at the leader tip is open or too large)"
            else:
                area_m2 = reg.sum() * (0.0254 / pg.DPI * scale) ** 2
                if area_m2 > 6.0 or area_m2 < 0.01:
                    e["status"] = f"footprint not extracted (region {area_m2:.2f} m2)"
                else:
                    poly, _ = pg.clean_room_polygon(reg, scale, idx)
                    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
                    e.update({"polygon_pt": poly, "size_cm": [round((max(xs) - min(xs)) * k, 1), round((max(ys) - min(ys)) * k, 1)],
                              "status": "extracted"})
            elements.append(e)
        dims = validate_dims(pv, rooms, elements, idx, k)
        draw_overlays(pno, img, clean, pv, rooms, openings, elements, dims, wall)
        # balcony outline (page coordinates; rooms below get it in their own frame)
        bal_poly = None
        if balcony.any():
            bm = cv2.morphologyEx(balcony.astype(np.uint8), cv2.MORPH_OPEN, np.ones((15, 15), np.uint8)).astype(bool)
            if bm.any():
                bal_poly = pg.orthogonalize(pg.mask_polygon(bm, 2.0), 2.0 / k)
        for r in rooms:
            x0, y0 = r["bbox_pt"][0], r["bbox_pt"][1]
            org = (x0, y0)
            w_cm, h_cm = (r["bbox_pt"][2] - x0) * k, (r["bbox_pt"][3] - y0) * k
            entry = {"id": r["id"], "page": pno, "scale": f"1:{scale}", "origin_pt": [round(x0, 3), round(y0, 3)],
                     "m_per_pt": m_per_pt,
                     "interior": {"polygon": [to_local(p, org, m_per_pt) for p in r["polygon_pt"]],
                                  "polygon_pt": [[round(a, 3), round(b, 3)] for a, b in r["polygon_pt"]],
                                  "bbox_cm": [round(w_cm, 1), round(h_cm, 1)],
                                  "area_m2": round(pg.polygon_area(r["polygon_pt"]) * m_per_pt**2, 2),
                                  "edges_snapped_to_vectors": r["snapped_edges"], "source": "plan (raster topology, vector-snapped)"},
                     "openings": [{"id": f"p{pno}-{o['letter']}", "kind": o["kind"], "status": "unverified (check against renders)",
                                   "wall_side": o["wall_side"], "from": to_local(o["a_pt"], org, m_per_pt),
                                   "to": to_local(o["b_pt"], org, m_per_pt), "width_cm": o["width_cm"], "evidence": o["evidence"],
                                   "source": "plan (vector lines on the wall face)" if o["kind"] != "gap" else "plan (gap in wall band)"}
                                  for o in openings if o["room"] == r["id"]],
                     "elements": [{"label": e["label"], "name_en": e["name_en"], "status": e["status"],
                                   "tip": to_local(e["tip_pt"], org, m_per_pt),
                                   **({"footprint": [to_local(p, org, m_per_pt) for p in e["polygon_pt"]], "size_cm": e["size_cm"]}
                                      if e.get("polygon_pt") else {}),
                                   **({"footprint_merged": [to_local(p, org, m_per_pt) for p in e["merged"]["polygon_pt"]],
                                       "size_merged_cm": e["merged"]["size_cm"]} if e.get("merged") else {}),
                                   "source": "plan (leader + raster fill, vector-snapped)"}
                                  for e in elements if room_of(e["tip_pt"], rooms) is r],
                     "dims": [{"id": d["id"], "label_cm": d["label_cm"], "geometry_cm": d["geometry_cm"], "status": d["status"],
                               "a": to_local(d["line_a_pt"], org, m_per_pt), "b": to_local(d["line_b_pt"], org, m_per_pt)}
                              for d in dims if room_of(d["a_pt"], rooms) is r]}
            if bal_poly and room_of(bal_poly[0], rooms) is r:
                entry["balcony"] = {"polygon": [to_local(p, org, m_per_pt) for p in bal_poly], "source": "plan (checkerboard raster)"}
            result["rooms"].append(entry)
            if r["id"] in REGRESSION:
                ew, eh = REGRESSION[r["id"]]
                ok = abs(ew - w_cm) <= TOL_CM and abs(eh - h_cm) <= TOL_CM
                result["regression"].append({"room": r["id"], "expected_cm": [ew, eh], "extracted_cm": [round(w_cm, 1), round(h_cm, 1)], "pass": ok})
            print(f"p{pno} {r['id']}: interior {w_cm:.1f} x {h_cm:.1f} cm, {len(r['polygon_pt'])} vertices, snapped {r['snapped_edges']}")
        for o in openings:
            print(f"   {o['letter']}: {o['room']:30} {o['wall_side']:6} {o['kind']:9} {o['width_cm']:6.1f} cm  {o['evidence']}")
        for e in elements:
            print(f"   element {e['name_en']:28} {e.get('size_cm')} {e['status'][:40]}")
        bad = [d for d in dims if not d["status"].startswith("pass")]
        print(f"   dims: {len(dims)}, pass {len(dims) - len(bad)}, other: " + "; ".join(f"{d['label_cm']} ({d['status']}, line {d['line_cm']}, geo {d['geometry_cm']})" for d in bad))
        result["dimensions"] += dims
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1,
                                   default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
    write_report(result)


def write_report(res: dict) -> None:
    L = ["# Plan geometry validation", "",
         "Generated by `tools/extract_geometry.py` from `source/Milo_-dispozicija_elemenata.pdf`. Geometry comes from the",
         "PDF's vector model edges (exact) with the plan raster used only to tell wall, floor and outside apart.",
         f"Tolerance: {TOL_CM:.0f} cm. Overlays: `debug/p<N>-geometry.png` (dimension checks) and `debug/p<N>-openings.png` (lettered openings).", "",
         "## Regression tests (from CLAUDE.md)", "", "| Room | Expected (cm) | Extracted (cm) | Result |", "|---|---|---|---|"]
    for t in res["regression"]:
        L.append(f"| {t['room']} | {t['expected_cm'][0]} x {t['expected_cm'][1]} | {t['extracted_cm'][0]} x {t['extracted_cm'][1]} | {'pass' if t['pass'] else 'FAIL'} |")
    L += ["", "## Rooms", "", "| Room | Page | Interior bbox (cm) | Area (m2) | Vertices | Edges snapped to vectors |", "|---|---|---|---|---|---|"]
    for r in res["rooms"]:
        i = r["interior"]
        L.append(f"| {r['id']} | {r['page']} ({r['scale']}) | {i['bbox_cm'][0]} x {i['bbox_cm'][1]} | {i['area_m2']} | {len(i['polygon'])} | {i['edges_snapped_to_vectors']} |")
    dims = res["dimensions"]
    geo = [d for d in dims if d["status"] == "pass"]
    raw = [d for d in dims if d["status"] == "pass (raw model edge)"]
    bad = [d for d in dims if not d["status"].startswith("pass")]
    L += ["", "## Dimensions", "",
          f"{len(dims)} labelled dimensions: **{len(geo)} pass against extracted walls/elements**, {len(raw)} pass against a raw model edge "
          f"(the feature is drawn but not extracted as a wall or labelled element, e.g. a chair), **{len(bad)} do not pass**.", "",
          "Each row compares the label with (1) the length of the red dimension line in the PDF and (2) the distance between the two",
          "extracted features at its ends.", "", "| Dim | Page | Label | Line | Geometry | Features | Status |", "|---|---|---|---|---|---|---|"]
    for d in sorted(dims, key=lambda d: (d["status"].startswith("pass"), d["page"])):
        L.append(f"| {d['id']} | {d['page']} | {d['label_cm']} | {d['line_cm']} | {d['geometry_cm']} | {', '.join(str(f) for f in d['features'])} | {d['status']} |")
    L += ["", "## Openings (unverified: each must be confirmed against the renders)", "",
          "Detected from vector lines on the wall faces: `gap` = real gap in the wall band; `door?` = casings drawn outside and inside",
          "the wall; `window?` = frame and sill lines 2.5-9 cm inside the face; `radiator?` = many short fins; `opening?` = lines outside",
          "the wall without casings. Letters match `debug/p<N>-openings.png`.", "",
          "| Id | Room | Wall | Kind | Width (cm) | Evidence |", "|---|---|---|---|---|---|"]
    for r in res["rooms"]:
        for o in r["openings"]:
            L.append(f"| {o['id']} | {r['id']} | {o['wall_side']} | {o['kind']} | {o['width_cm']} | {o['evidence']} |")
    L += ["", "## Labelled elements", "", "| Room | Label | English | Footprint (cm) | Status |", "|---|---|---|---|---|"]
    for r in res["rooms"]:
        for e in r["elements"]:
            L.append(f"| {r['id']} | {e['label']} | {e['name_en']} | {' x '.join(str(v) for v in e.get('size_cm', [])) or '-'} | {e['status']} |")
    (DEBUG / "validation.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    for t in result["regression"]:
        print("regression", t)


if __name__ == "__main__":
    main()
