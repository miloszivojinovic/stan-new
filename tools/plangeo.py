"""Plan geometry from the layout PDF.

The PDF is a SketchUp LayOut "hybrid" export: each plan viewport is an embedded raster (textures, dark-red wall
fill) with the model edges drawn on top as black vector lines, and LayOut's dimensions (red) and label leaders
(blue) as vectors. This module

  * parses the vectors: black model edges, dimensions (dots, main line, extension lines), leaders (arrow tips),
  * renders the page and classifies pixels (wall / background) to get the topology: wall bands, gaps in them,
    room interiors,
  * snaps raster-derived edges to the exact vector edges.

All page coordinates are PDF points, origin top-left, y down (same as data/layout-labels.json).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pymupdf

PT_MM = 25.4 / 72.0  # millimetres of paper per point
DPI = 300
PX_PER_PT = DPI / 72.0

RED = (1.0, 0.02, 0.0)
BLUE = (0.0, 0.07, 1.0)
BLACK = (0.0, 0.0, 0.0)

# page number -> scale denominator and the rooms drawn on it (left to right)
PAGES = {
    1: {"scale": 50, "rooms": ["hallway-living-dining-kitchen"]},
    2: {"scale": 25, "rooms": ["work-room"]},
    3: {"scale": 25, "rooms": ["childrens-room"]},
    4: {"scale": 25, "rooms": ["bedroom"]},
    5: {"scale": 25, "rooms": ["bathroom", "toilet"]},
}


def mm_per_pt(scale: int) -> float:
    return PT_MM * scale


def same_colour(c, ref, tol=0.03) -> bool:
    return c is not None and len(c) == 3 and all(abs(a - b) <= tol for a, b in zip(c, ref))


# --------------------------------------------------------------------------------------------------- vectors
@dataclass
class Dim:
    id: str
    a: tuple[float, float]  # dot centres = ends of the dimension line
    b: tuple[float, float]
    pa: tuple[float, float]  # measured points (far ends of the extension lines)
    pb: tuple[float, float]
    label: dict | None = None

    @property
    def length_pt(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def vertical(self) -> bool:
        return abs(self.b[1] - self.a[1]) > abs(self.b[0] - self.a[0])

    def unit(self) -> tuple[float, float]:
        L = self.length_pt or 1.0
        return ((self.b[0] - self.a[0]) / L, (self.b[1] - self.a[1]) / L)


@dataclass
class Leader:
    tip: tuple[float, float]  # arrow tip, inside the element
    dot: tuple[float, float]  # text end
    label: dict | None = None
    base: tuple[float, float] | None = None  # arrow base (tip - base = pointing direction)


@dataclass
class PageVectors:
    page: int
    scale: int
    segments: np.ndarray  # (N, 4) black model edges x0, y0, x1, y1
    dims: list[Dim] = field(default_factory=list)
    leaders: list[Leader] = field(default_factory=list)
    viewports: list[tuple[float, float, float, float]] = field(default_factory=list)


def _circle_centre(d) -> tuple[float, float]:
    r = d["rect"]
    return ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


def _pts(item):
    kind = item[0]
    if kind == "l":
        return [item[1], item[2]]
    if kind == "c":
        return [item[1], item[4]]
    if kind == "re":
        r = item[1]
        return [pymupdf.Point(r.x0, r.y0), pymupdf.Point(r.x1, r.y0), pymupdf.Point(r.x1, r.y1), pymupdf.Point(r.x0, r.y1)]
    if kind == "qu":
        q = item[1]
        return [q.ul, q.ur, q.lr, q.ll]
    return []


def load_vectors(doc: pymupdf.Document, pno: int, labels: dict) -> PageVectors:
    page = doc[pno - 1]
    scale = PAGES[pno]["scale"]
    drawings = page.get_drawings()

    segs = []
    red_dots, red_lines, blue_arrows, blue_dots, blue_lines = [], [], [], [], []
    for d in drawings:
        col, fill = d.get("color"), d.get("fill")
        if same_colour(col, BLACK) and d["type"] == "s":
            for it in d["items"]:
                p = _pts(it)
                if it[0] in ("re", "qu"):
                    p = p + [p[0]]
                for u, v in zip(p, p[1:]):
                    segs.append((u.x, u.y, v.x, v.y))
        elif same_colour(fill, RED):
            red_dots.append(_circle_centre(d))
        elif same_colour(col, RED):
            for it in d["items"]:
                if it[0] == "l":
                    red_lines.append((it[1].x, it[1].y, it[2].x, it[2].y))
        elif same_colour(fill, BLUE):
            if any(it[0] == "c" for it in d["items"]):
                blue_dots.append(_circle_centre(d))
            else:  # arrow head: triangle, tip = vertex farthest from the other two's midpoint
                pts = [(it[1].x, it[1].y) for it in d["items"] if it[0] == "l"]
                best = max(pts, key=lambda p: sum(math.dist(p, q) for q in pts))
                others = [q for q in pts if math.dist(q, best) > 1e-6]
                base = (sum(q[0] for q in others) / len(others), sum(q[1] for q in others) / len(others))
                blue_arrows.append((best, base))
        elif same_colour(col, BLUE):
            poly = []
            for it in d["items"]:
                if it[0] == "l":
                    poly += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
            blue_lines.append(poly)

    pv = PageVectors(pno, scale, np.array(segs, dtype=np.float64).reshape(-1, 4))
    pv.viewports = [tuple(i["bbox"]) for i in page.get_image_info()]
    pv.dims = _parse_dims(red_dots, red_lines, pno)
    _attach_dim_labels(pv.dims, labels)
    pv.leaders = _parse_leaders(blue_arrows, blue_dots, blue_lines, labels)
    return pv


def _parse_dims(dots, lines, pno) -> list[Dim]:
    dots = np.array(dots)
    used = set()
    dims = []
    for (x0, y0, x1, y1) in lines:
        # main line: both ends sit on a dot rim (dot radius ~1.1 pt)
        da = np.hypot(dots[:, 0] - x0, dots[:, 1] - y0)
        db = np.hypot(dots[:, 0] - x1, dots[:, 1] - y1)
        ia, ib = int(np.argmin(da)), int(np.argmin(db))
        if not (0.8 < da[ia] < 1.4 and 0.8 < db[ib] < 1.4) or ia == ib:
            continue
        key = tuple(sorted((ia, ib)))
        if key in used:  # several dims can share a dot position; dots are drawn per dim so indices differ
            continue
        used.add(key)
        a, b = tuple(dots[ia]), tuple(dots[ib])
        ux, uy = (b[0] - a[0]), (b[1] - a[1])
        L = math.hypot(ux, uy)
        ux, uy = ux / L, uy / L
        ends = []
        for c in (a, b):
            cands = []
            for (sx0, sy0, sx1, sy1) in lines:
                for (px, py), (qx, qy) in (((sx0, sy0), (sx1, sy1)), ((sx1, sy1), (sx0, sy0))):
                    if math.dist((px, py), c) < 0.05:
                        vx, vy = qx - px, qy - py
                        ln = math.hypot(vx, vy)
                        if ln > 1e-6 and abs((vx * ux + vy * uy) / ln) < 0.05:  # perpendicular to main line
                            cands.append(((qx, qy), ln, (vx / ln, vy / ln)))
            ends.append(cands)
        pa, pb = _measured_points(a, b, ends)
        dims.append(Dim(f"p{pno}-d{len(dims) + 1}", a, b, pa, pb))
    return dims


def _measured_points(a, b, ends):
    """Each dot has two perpendicular red segments: the extension line from the measured point and a ~5.1 pt
    overshoot pointing the other way. The extension lines of both dots point the same way."""
    OVER = 5.1

    def pick(cands, direction=None):
        if direction is not None:
            same = [c for c in cands if c[2][0] * direction[0] + c[2][1] * direction[1] > 0.9]
            return same[0] if same else None
        non_over = [c for c in cands if abs(c[1] - OVER) > 0.15]
        return non_over[0] if len(non_over) == 1 else None

    ea, eb = pick(ends[0]), pick(ends[1])
    if ea and not eb:
        eb = pick(ends[1], ea[2])
    if eb and not ea:
        ea = pick(ends[0], eb[2])
    pa = ea[0] if ea else a
    pb = eb[0] if eb else b
    return tuple(pa), tuple(pb)


def _attach_dim_labels(dims: list[Dim], labels: dict) -> None:
    nums = [l for l in labels if l.get("value") is not None]
    pairs = []
    for d in dims:
        ux, uy = d.unit()
        for j, l in enumerate(nums):
            x0, y0, x1, y1 = l["bbox_pt"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            label_vertical = (y1 - y0) > (x1 - x0)
            if label_vertical != d.vertical:
                continue
            t = ((cx - d.a[0]) * ux + (cy - d.a[1]) * uy) / d.length_pt
            off = abs((cx - d.a[0]) * -uy + (cy - d.a[1]) * ux)
            if -0.05 <= t <= 1.05 and off < 15:
                pairs.append((off + 5 * abs(t - 0.5), id(d), j, d, l))
    pairs.sort(key=lambda p: p[0])
    taken_d, taken_l = set(), set()
    for _, did, j, d, l in pairs:
        if did in taken_d or j in taken_l:
            continue
        d.label = l
        taken_d.add(did)
        taken_l.add(j)


def _parse_leaders(arrows, dots, lines, labels) -> list[Leader]:
    names = [l for l in labels if l.get("value") is None and "DISPOZICIJA" not in l["text"]]
    leaders = []
    for tip, base in arrows:
        # polyline starting at the arrow base; walk to the end farthest from the arrow
        poly = min(lines, key=lambda p: min(math.dist(q, base) for q in p)) if lines else []
        far = max(poly, key=lambda q: math.dist(q, base)) if poly else base
        dot = min(dots, key=lambda q: math.dist(q, far)) if dots else far
        lab = min(names, key=lambda l: _dist_to_bbox(dot, l["bbox_pt"])) if names else None
        leaders.append(Leader(tuple(tip), tuple(dot), lab, tuple(base)))
    # a label line can continue onto a second line ("POLICA" + "/SKRIVAČ ZA BOJLER"): merge stacked texts
    return leaders


def _dist_to_bbox(p, bb) -> float:
    x0, y0, x1, y1 = bb
    dx = max(x0 - p[0], 0, p[0] - x1)
    dy = max(y0 - p[1], 0, p[1] - y1)
    return math.hypot(dx, dy)


def page_labels(labels_json: dict, pno: int) -> list[dict]:
    for p in labels_json["pages"]:
        if p["page"] == pno:
            return p["lines"]
    return []


# --------------------------------------------------------------------------------------------------- raster
def render(page: pymupdf.Page, dpi: int = DPI) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)
    return img[:, :, :3].copy()  # RGB


def wall_mask(img: np.ndarray) -> np.ndarray:
    r, g, b = (img[..., i].astype(np.int16) for i in range(3))
    wall = (r > 100) & (r < 215) & (g < 60) & (b < 60)
    # bridge the thin black edge strokes and the dimension dots/lines drawn over the bands
    m = cv2.morphologyEx(wall.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    return m.astype(bool)


def _run_through(a: np.ndarray, i: int) -> int:
    if not a[i]:
        return 0
    left = np.flatnonzero(~a[:i][::-1])
    right = np.flatnonzero(~a[i + 1:])
    return (left[0] if left.size else i) + (right[0] if right.size else len(a) - i - 1) + 1


@dataclass
class Gap:
    orient: str  # "h": gap in a horizontal wall band, "v": in a vertical one
    x0: float  # bbox in page points
    y0: float
    x1: float
    y1: float
    kind: str = "opening"  # opening (frame/leaf or nothing drawn) | passage (floor continues through)

    @property
    def width_pt(self) -> float:  # along the wall
        return (self.x1 - self.x0) if self.orient == "h" else (self.y1 - self.y0)

    @property
    def depth_pt(self) -> float:  # across the wall
        return (self.y1 - self.y0) if self.orient == "h" else (self.x1 - self.x0)


def find_gaps(wall: np.ndarray, img: np.ndarray, scale: int) -> list[Gap]:
    mmpx = 25.4 / DPI * scale
    tmin, tmax = 70 / mmpx, 400 / mmpx
    lmin, lmax = 250 / mmpx, 3200 / mmpx
    gaps = []
    for orient in ("h", "v"):
        M = wall if orient == "h" else wall.T
        cand = np.zeros_like(M)
        for y in range(M.shape[0]):
            row = M[y]
            if not row.any():
                continue
            d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
            starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
            for k in range(len(starts) - 1):
                x0, x1 = ends[k], starts[k + 1]
                if not (lmin <= x1 - x0 <= lmax):
                    continue
                if ends[k] - starts[k] < tmin * 0.5 or ends[k + 1] - starts[k + 1] < tmin * 0.5:
                    continue
                ta = _run_through(M[:, x0 - 1], y)
                tb = _run_through(M[:, x1], y)
                if tmin <= ta <= tmax and tmin <= tb <= tmax:
                    cand[y, x0:x1] = True
        n, lab, stats, _ = cv2.connectedComponentsWithStats(cand.astype(np.uint8), connectivity=4)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if not (tmin <= h <= tmax and lmin <= w <= lmax) or area < 0.8 * w * h:
                continue
            if orient == "h":
                bx0, by0, bx1, by1 = x, y, x + w, y + h
            else:
                bx0, by0, bx1, by1 = y, x, y + h, x + w
            g = Gap(orient, bx0 / PX_PER_PT, by0 / PX_PER_PT, bx1 / PX_PER_PT, by1 / PX_PER_PT)
            g.kind = _gap_kind(img, (bx0, by0, bx1, by1), orient)
            gaps.append(g)
    return gaps


def _gap_kind(img, box, orient) -> str:
    """passage if the floor on at least one side continues through the gap (similar colour), else opening."""
    x0, y0, x1, y1 = box
    t = (y1 - y0) if orient == "h" else (x1 - x0)
    inset = max(2, int(0.15 * ((x1 - x0) if orient == "h" else (y1 - y0))))
    if orient == "h":
        gap = img[y0:y1, x0 + inset:x1 - inset]
        sides = [img[max(0, y0 - t):y0, x0 + inset:x1 - inset], img[y1:y1 + t, x0 + inset:x1 - inset]]
    else:
        gap = img[y0 + inset:y1 - inset, x0:x1]
        sides = [img[y0 + inset:y1 - inset, max(0, x0 - t):x0], img[y0 + inset:y1 - inset, x1:x1 + t]]
    g = np.median(gap.reshape(-1, 3), axis=0)
    for s in sides:
        if s.size == 0:
            continue
        m = np.median(s.reshape(-1, 3), axis=0)
        is_floor = not (m > 245).all()  # background white is not floor
        if is_floor and np.abs(g - m).max() < 22:
            return "passage"
    return "opening"


def fill_holes(mask: np.ndarray) -> np.ndarray:
    free = (~mask).astype(np.uint8)
    n, lab = cv2.connectedComponents(free, connectivity=4)
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    return ~np.isin(lab, border)


def background_mask(img: np.ndarray) -> np.ndarray:
    """Plain page white connected to the page border."""
    white = img.min(axis=2) > 245
    n, lab = cv2.connectedComponents(white.astype(np.uint8), connectivity=4)
    border = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    return np.isin(lab, border[border > 0]) & white


def balcony_mask(img: np.ndarray, wall: np.ndarray, scale: int) -> np.ndarray:
    """Checkerboard balcony: many large near-black tiles in one cluster. Includes its dark border."""
    px_m = DPI / 0.0254 / scale  # pixels per metre
    dark = (img.max(axis=2) < 80) & ~wall
    k = max(3, int(0.12 * px_m))  # drop lines, text, thin furniture parts
    blobs = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(blobs, connectivity=8)
    tile = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > (0.15 * px_m) ** 2]
    tiles = np.isin(lab, tile).astype(np.uint8)
    # closing (not dilation) fills the light tiles between dark ones without growing past the checkerboard's
    # straight outer edges, so the balcony stops exactly at the wall face and does not eat the opening reveals
    k2 = int(0.42 * px_m)
    region = cv2.morphologyEx(tiles, cv2.MORPH_CLOSE, np.ones((k2, k2), np.uint8))
    n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(region, connectivity=8)
    out = np.zeros(wall.shape, bool)
    for i in range(1, n2):
        if len(np.unique(lab[(lab2 == i) & (tiles > 0)])) >= 4:  # several separate dark tiles
            out |= lab2 == i
    out = fill_holes(out) & ~wall
    # include the balcony's dark edge (railing / parapet) that runs along the outside of the checkerboard
    edge = (img.max(axis=2) < 140) & ~wall & ~out
    near = cv2.dilate(out.astype(np.uint8), np.ones((int(0.12 * px_m),) * 2, np.uint8)).astype(bool)
    return out | (edge & near & ~cv2.dilate(wall.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool))


def outline_openings(img: np.ndarray, wall: np.ndarray, foot: np.ndarray, background: np.ndarray,
                     rooms: list[np.ndarray], scale: int) -> list[Gap]:
    """Openings drawn only as outlines on a solid wall:
    * outer: a door leaf / frame drawn just outside the wall's outer face (non-white, beyond the wall stroke),
    * inner: a window sill / frame drawn as a thin light strip along the wall's inner face inside the room."""
    mmpx = 25.4 / DPI * scale
    lmin, lmax = 300 / mmpx, 3200 / mmpx
    found = []
    f8 = foot.astype(np.uint8)
    ring = cv2.dilate(f8, np.ones((29, 29), np.uint8)).astype(bool) & ~cv2.dilate(f8, np.ones((5, 5), np.uint8)).astype(bool)
    outer = ring & ~background & (img.max(axis=2) - img.min(axis=2) < 40)  # neutral (no red dims, blue leaders)
    found += _strips(outer, wall, lmin, lmax, max_thick=40 / mmpx + 14, kind="outline-outer")
    light = (img.min(axis=2) > 225) & (img.max(axis=2) - img.min(axis=2) < 25)
    near_wall = cv2.dilate(wall.astype(np.uint8), np.ones((int(90 / mmpx) * 2 + 1,) * 2, np.uint8)).astype(bool)
    room_any = np.zeros_like(wall)
    for r in rooms:
        room_any |= r
    inner = light & room_any & near_wall
    found += _strips(inner, wall, lmin, lmax, max_thick=90 / mmpx, kind="outline-inner")
    return found


def _strips(mask, wall, lmin, lmax, max_thick, kind):
    """Elongated components of `mask` running along a wall face; returns the stretch of wall they mark."""
    out = []
    m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        L, T = max(w, h), min(w, h)
        if not (lmin <= L <= lmax) or T > max_thick or L < 4 * T:
            continue
        orient = "h" if w >= h else "v"
        # the strip must lie along a wall: wall pixels beside it over most of its length
        if orient == "h":
            band = wall[max(0, y - 40):y + h + 40, x:x + w].any(axis=0).mean()
        else:
            band = wall[y:y + h, max(0, x - 40):x + w + 40].any(axis=1).mean()
        if band < 0.8:
            continue
        out.append(Gap(orient, x / PX_PER_PT, y / PX_PER_PT, (x + w) / PX_PER_PT, (y + h) / PX_PER_PT, kind))
    return out


def footprint(img: np.ndarray, wall: np.ndarray, outside: np.ndarray, scale: int) -> np.ndarray:
    """Walls plus everything they enclose; thin stuff outside (labels, leaders, outline-only doors) removed."""
    m_px = 0.0254 / DPI * scale
    nonbg = (~outside).astype(np.uint8)
    nb = cv2.morphologyEx(nonbg, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(nb, connectivity=4)
    keep = np.isin(lab, [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] * m_px**2 > 1.0])
    return fill_holes(keep) & ~outside


def room_masks(foot: np.ndarray, closed_wall: np.ndarray, scale: int, min_area_m2: float = 0.5) -> list[np.ndarray]:
    """Room interiors: connected non-wall parts of the footprint once the openings are closed, left to right."""
    m_px = 0.0254 / DPI * scale
    free = cv2.morphologyEx((foot & ~closed_wall).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    out = [lab == i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] * m_px**2 >= min_area_m2]
    out.sort(key=lambda m: np.argwhere(m)[:, 1].mean())
    return out


def outer_gaps(wall: np.ndarray, foot: np.ndarray, scale: int) -> list[Gap]:
    """Openings = breaks in the walls' outer face: footprint boundary stretches that are not wall."""
    mmpx = 25.4 / DPI * scale
    lmin, lmax, tmax = 250 / mmpx, 3200 / mmpx, 450 / mmpx
    # drop hairline strips of non-wall pixels along wall faces (outline strokes, mask trims); real openings and
    # outline-only door leaves drawn outside the wall are wider than this
    interior = cv2.morphologyEx((foot & ~wall).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)).astype(bool)
    out = cv2.dilate((~foot).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool) & ~interior
    gaps = []
    for orient, shift in (("h", (1, 0)), ("h", (-1, 0)), ("v", (0, 1)), ("v", (0, -1))):
        dy, dx = shift
        # interior pixels whose neighbour in direction (dy, dx) is outside: a mouth facing that way
        mouth = (interior & _shifted(out, dy, dx)).astype(np.uint8)
        # mouths are straight lines; bridge 1-2 px breaks along them
        kern = np.ones((1, 7), np.uint8) if orient == "h" else np.ones((7, 1), np.uint8)
        mouth = cv2.morphologyEx(mouth, cv2.MORPH_CLOSE, kern)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(mouth, connectivity=8)
        for i in range(1, n):
            x, y, w, h, _ = stats[i]
            L = w if orient == "h" else h
            if not (lmin <= L <= lmax) or (h if orient == "h" else w) > 12:
                continue
            g = _gap_from_mouth(wall, (x, y, w, h), orient, (dy, dx), tmax)
            if g is not None:
                gaps.append(g)
    return _dedupe(gaps)


def _shifted(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """b[y, x] = a[y + dy, x + dx], False outside the array."""
    b = np.zeros_like(a)
    H, W = a.shape
    sy, dyd = (slice(dy, H), slice(0, H - dy)) if dy >= 0 else (slice(0, H + dy), slice(-dy, H))
    sx, dxd = (slice(dx, W), slice(0, W - dx)) if dx >= 0 else (slice(0, W + dx), slice(-dx, W))
    b[dyd, dxd] = a[sy, sx]
    return b


def _gap_from_mouth(wall, box, orient, outward, tmax):
    """Turn a mouth (non-wall stretch of the footprint's outer boundary) into the gap rectangle through the wall.
    Works on a transposed view for vertical mouths so both cases share one code path."""
    x, y, w, h = box
    dy, dx = outward
    if orient == "v":  # transpose: columns become rows
        M, (x, y, w, h), d = wall.T, (y, x, h, w), dx
    else:
        M, d = wall, dy
    inward = -d
    row0 = y + h // 2
    for k in range(0, 25):  # frames can stick out past the wall face: step inward until jambs are found
        row = row0 + k * inward
        if not 0 <= row < M.shape[0]:
            return None
        ja, jb = _find_jamb(M[row], x - 1, -1), _find_jamb(M[row], x + w, +1)
        if ja is not None and jb is not None:
            break
    else:
        return None
    ts = [min(_run_through(M[:, j], row), _run_through(M[row], j)) for j in (ja, jb)]
    t = int(np.median([v for v in ts if v > 0] or [0]))
    if not (8 <= t <= tmax):
        return None
    f = int(np.median([_face(M[:, j], row, inward) for j in (ja, jb)]))  # outermost wall row at the jambs
    r0, r1 = (f, f + t) if inward > 0 else (f - t + 1, f + 1)
    c0, c1 = ja + 1, jb
    # a real gap is (mostly) free; an outline-only opening is a door/window drawn outside a solid wall
    frac = M[max(r0, 0):r1, c0:c1].mean() if c1 > c0 and r1 > r0 else 1.0
    kind = "gap" if frac < 0.3 else "outline" if frac > 0.85 else "unclear"
    if orient == "v":
        return Gap("v", r0 / PX_PER_PT, c0 / PX_PER_PT, r1 / PX_PER_PT, c1 / PX_PER_PT, kind)
    return Gap("h", c0 / PX_PER_PT, r0 / PX_PER_PT, c1 / PX_PER_PT, r1 / PX_PER_PT, kind)


def _find_jamb(line, start, step, reach=6):
    for k in range(reach):
        i = start + k * step
        if 0 <= i < len(line) and line[i]:
            return i
    return None


def _face(line, i, inward):
    """Index of the outermost wall pixel of the band through i, scanning from the outside (opposite of inward)."""
    j = i
    while 0 <= j - inward < len(line) and line[j - inward]:
        j -= inward
    return j


def _dedupe(gaps):
    out = []
    for g in gaps:
        if not any(abs(g.x0 - o.x0) < 1 and abs(g.y0 - o.y0) < 1 and abs(g.x1 - o.x1) < 1 and abs(g.y1 - o.y1) < 1 for o in out):
            out.append(g)
    return out


def close_gaps(wall: np.ndarray, gaps: list[Gap], kinds=("opening", "gap", "unclear")) -> np.ndarray:
    m = wall.copy()
    for g in gaps:
        if g.kind in kinds:
            x0, y0, x1, y1 = (int(round(v * PX_PER_PT)) for v in (g.x0, g.y0, g.x1, g.y1))
            m[y0:y1, x0:x1] = True
    return m


def interiors(closed: np.ndarray, scale: int, min_area_m2: float = 0.5) -> list[np.ndarray]:
    """Connected non-wall regions not reachable from the page border = room interiors (boolean masks)."""
    free = (~closed).astype(np.uint8)
    n, lab = cv2.connectedComponents(free, connectivity=4)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    m_per_px = 0.0254 / DPI * scale
    out = []
    for i in range(1, n):
        if i in border:
            continue
        mask = lab == i
        if mask.sum() * m_per_px**2 >= min_area_m2:
            out.append(mask)
    out.sort(key=lambda m: np.argwhere(m)[:, 1].mean())  # left to right
    return out


def mask_polygon(mask: np.ndarray, eps_px: float = 1.5) -> list[tuple[float, float]]:
    """Outer contour of a mask as a polygon in page points (pixel edges, not centres)."""
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cnts, key=cv2.contourArea)
    c = cv2.approxPolyDP(c, eps_px, True)[:, 0, :].astype(float)
    # contour runs through boundary pixel centres of the region: push edges out by half a pixel
    cx, cy = c[:, 0].mean(), c[:, 1].mean()
    c[:, 0] += np.where(c[:, 0] >= cx, 1.0, 0.0)
    c[:, 1] += np.where(c[:, 1] >= cy, 1.0, 0.0)
    return [(float(x) / PX_PER_PT, float(y) / PX_PER_PT) for x, y in c]


# --------------------------------------------------------------------------------------------------- snapping
class EdgeIndex:
    """Axis-aligned black model edges, for snapping raster edges to exact vector coordinates."""

    def __init__(self, segs: np.ndarray, tol: float = 0.02):
        s = segs
        dx, dy = np.abs(s[:, 2] - s[:, 0]), np.abs(s[:, 3] - s[:, 1])
        v = (dx < tol) & (dy > 0.2)
        h = (dy < tol) & (dx > 0.2)
        self.v = np.column_stack([(s[v, 0] + s[v, 2]) / 2, np.minimum(s[v, 1], s[v, 3]), np.maximum(s[v, 1], s[v, 3])])
        self.h = np.column_stack([(s[h, 1] + s[h, 3]) / 2, np.minimum(s[h, 0], s[h, 2]), np.maximum(s[h, 0], s[h, 2])])

    def snap(self, coord: float, lo: float, hi: float, vertical: bool, tol: float = 0.6) -> tuple[float, float]:
        """Best vector line near `coord` (x for vertical edges, y for horizontal) overlapping [lo, hi].
        Returns (snapped coordinate, overlap fraction); falls back to coord with overlap 0."""
        arr = self.v if vertical else self.h
        if lo > hi:
            lo, hi = hi, lo
        near = arr[np.abs(arr[:, 0] - coord) < tol]
        if near.size == 0 or hi - lo <= 0:
            return coord, 0.0
        best, best_ov, best_score = coord, 0.0, -1.0
        for c in np.unique(np.round(near[:, 0], 3)):
            sel = near[np.abs(near[:, 0] - c) < 0.002]
            ov = min(np.clip(np.minimum(sel[:, 2], hi) - np.maximum(sel[:, 1], lo), 0, None).sum() / (hi - lo), 1.0)
            if ov < 0.3:
                continue
            score = ov - 0.3 * abs(c - coord) / tol
            if score > best_score:
                best, best_ov, best_score = float(c), float(ov), score
        return best, best_ov


def snap_polygon(poly: list[tuple[float, float]], idx: EdgeIndex, tol: float = 0.6):
    """Snap an (almost) orthogonal polygon's edges to vector lines. Returns (polygon, per-edge overlap)."""
    n = len(poly)
    edges = []
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if abs(x1 - x0) < abs(y1 - y0):  # vertical edge
            c, ov = idx.snap((x0 + x1) / 2, y0, y1, True, tol)
            edges.append(("v", c, ov))
        else:
            c, ov = idx.snap((y0 + y1) / 2, x0, x1, False, tol)
            edges.append(("h", c, ov))
    out = []
    for i in range(n):
        e_prev, e_next = edges[i - 1], edges[i]
        if e_prev[0] == e_next[0]:  # collinear pair (approx artefact): keep raster vertex
            out.append(poly[i])
            continue
        x = e_prev[1] if e_prev[0] == "v" else e_next[1]
        y = e_prev[1] if e_prev[0] == "h" else e_next[1]
        out.append((x, y))
    return out, [e[2] for e in edges]


def orthogonalize(poly, min_jog_pt: float):
    """Make an almost-orthogonal polygon exactly orthogonal: merge collinear edges, remove jogs < min_jog_pt."""
    n = len(poly)
    edges = []
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if abs(x1 - x0) >= abs(y1 - y0):
            edges.append(["h", (y0 + y1) / 2, abs(x1 - x0) + 1e-9])
        else:
            edges.append(["v", (x0 + x1) / 2, abs(y1 - y0) + 1e-9])

    def merge(es):
        out = []
        for e in es:
            if out and out[-1][0] == e[0]:
                m = out[-1]
                L = m[2] + e[2]
                m[1], m[2] = (m[1] * m[2] + e[1] * e[2]) / L, L
            else:
                out.append(list(e))
        if len(out) > 1 and out[0][0] == out[-1][0]:
            a, b = out.pop(), out[0]
            L = a[2] + b[2]
            b[1], b[2] = (a[1] * a[2] + b[1] * b[2]) / L, L
        return out

    edges = merge(edges)
    changed = True
    while changed and len(edges) > 4:
        changed = False
        for i in range(len(edges)):
            prev, cur, nxt = edges[i - 1], edges[i], edges[(i + 1) % len(edges)]
            if cur[2] < min_jog_pt and prev[0] == nxt[0] and abs(prev[1] - nxt[1]) < min_jog_pt:
                L = prev[2] + nxt[2]
                prev[1], prev[2] = (prev[1] * prev[2] + nxt[1] * nxt[2]) / L, L
                j = (i + 1) % len(edges)
                for idx in sorted((i, j), reverse=True):
                    edges.pop(idx)
                edges = merge(edges)
                changed = True
                break
    verts = []
    for i in range(len(edges)):
        a, b = edges[i - 1], edges[i]
        verts.append((a[1] if a[0] == "v" else b[1], a[1] if a[0] == "h" else b[1]))
    return verts


def clean_room_polygon(mask: np.ndarray, scale: int, idx: "EdgeIndex"):
    px_cm = DPI / 2.54 / scale
    k = max(3, int(round(4 * px_cm)) | 1)  # 4 cm: drop sills, strokes and specks drawn on the faces
    m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    if not m.any():  # thin element (a few cm): keep it as drawn
        m = mask.astype(np.uint8)
    poly = orthogonalize(mask_polygon(m, eps_px=2.0), min_jog_pt=2.0 / (mm_per_pt(scale) / 10))
    return snap_polygon(poly, idx, tol=0.8)


def viewport_raster(doc: pymupdf.Document, page: pymupdf.Page, ref: np.ndarray) -> np.ndarray:
    """The embedded plan rasters (no vector edges, dimensions or labels) resampled onto the page render's pixel
    grid. Orientation is checked against the page render instead of trusting a convention."""
    s = DPI / 72.0
    H, W = ref.shape[:2]
    out = np.full((H, W, 3), 255, np.uint8)
    for info in page.get_image_info(xrefs=True):
        xref = info["xref"]
        base = pymupdf.Pixmap(doc, xref)
        if base.colorspace and base.colorspace.n != 3:
            base = pymupdf.Pixmap(pymupdf.csRGB, base)
        img = np.frombuffer(base.samples, np.uint8).reshape(base.h, base.w, base.n)[:, :, :3].copy()
        sm = doc.extract_image(xref).get("smask", 0)
        if sm:
            mp = pymupdf.Pixmap(doc, sm)
            alpha = np.frombuffer(mp.samples, np.uint8).reshape(mp.h, mp.w, mp.n)[:, :, 0]
            if alpha.shape != img.shape[:2]:
                alpha = cv2.resize(alpha, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        else:
            alpha = np.full(img.shape[:2], 255, np.uint8)
        a, b, c, d, e, f = info["transform"]
        h, w = img.shape[:2]
        best = None
        for flip in (False, True):
            if not flip:
                M = np.float32([[a / w * s, c / h * s, e * s], [b / w * s, d / h * s, f * s]])
            else:
                M = np.float32([[a / w * s, -c / h * s, (c + e) * s], [b / w * s, -d / h * s, (d + f) * s]])
            wi = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
            wa = cv2.warpAffine(alpha, M, (W, H), flags=cv2.INTER_NEAREST, borderValue=0) > 128
            if wa.sum() == 0:
                continue
            err = np.abs(wi[wa].astype(np.int16) - ref[wa].astype(np.int16)).mean()
            if best is None or err < best[0]:
                best = (err, wi, wa)
        if best:
            out[best[2]] = best[1][best[2]]
    return out


def wall_thickness_pt(wall: np.ndarray, orient: str, c: float, a0: float, a1: float, outward: int) -> float | None:
    runs = []
    for t in np.linspace(0.1, 0.9, 15):
        a = a0 + (a1 - a0) * t
        if orient == "h":
            X, Y = int(a * PX_PER_PT), int(c * PX_PER_PT)
            line = wall[:, X] if 0 <= X < wall.shape[1] else None
        else:
            X, Y = int(c * PX_PER_PT), int(a * PX_PER_PT)
            line = wall[Y] if 0 <= Y < wall.shape[0] else None
        if line is None:
            continue
        i = (Y if orient == "h" else X) + outward  # first pixel behind the face
        n = 0
        while 0 <= i < len(line) and line[i] and n < 2000:
            n += 1
            i += outward
        if n > 3:
            runs.append(n)
    return float(np.median(runs)) / PX_PER_PT if runs else None


@dataclass
class Feature:
    """Something drawn on a wall face: an opening (door / window / gap) or a radiator."""
    orient: str
    face: float  # inner face coordinate (pt)
    a0: float  # extent along the wall (pt)
    a1: float
    inward: int  # +1: room is at larger coordinate than the face
    kind: str  # gap | door? | window? | radiator? | opening?
    evidence: dict = field(default_factory=dict)


def wall_face_features(poly, room_mask, wall, segs, scale, gaps) -> list[Feature]:
    k = mm_per_pt(scale) / 10  # cm per pt
    cm = 1 / k  # pt per cm
    s = segs
    hz = np.abs(s[:, 3] - s[:, 1]) < 0.02
    vt = np.abs(s[:, 2] - s[:, 0]) < 0.02
    feats: list[Feature] = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        if abs(q[1] - p[1]) < 1e-6:
            o, c, (a0, a1) = "h", p[1], sorted((p[0], q[0]))
        elif abs(q[0] - p[0]) < 1e-6:
            o, c, (a0, a1) = "v", p[0], sorted((p[1], q[1]))
        else:
            continue
        if (a1 - a0) * k < 30:
            continue
        mid = (a0 + a1) / 2
        nrm = None
        for sgn in (1, -1):
            tx, ty = (mid, c + sgn * 3 * cm) if o == "h" else (c + sgn * 3 * cm, mid)
            X, Y = int(tx * PX_PER_PT), int(ty * PX_PER_PT)
            if 0 <= Y < room_mask.shape[0] and 0 <= X < room_mask.shape[1] and room_mask[Y, X]:
                nrm = sgn
                break
        if nrm is None:
            continue
        t = wall_thickness_pt(wall, o, c, a0, a1, -nrm)
        sel = s[hz] if o == "h" else s[vt]
        coord = sel[:, 1] if o == "h" else sel[:, 0]
        lo = np.minimum(sel[:, 0], sel[:, 2]) if o == "h" else np.minimum(sel[:, 1], sel[:, 3])
        hi = np.maximum(sel[:, 0], sel[:, 2]) if o == "h" else np.maximum(sel[:, 1], sel[:, 3])
        off = (coord - c) * nrm  # > 0: into the room, < 0: into the wall and beyond
        ov = (np.minimum(hi, a1) - np.maximum(lo, a0)) > 0.5 * cm
        near_in = ov & (off > 0.15) & (off <= 2.5 * cm)
        mid_in = ov & (off > 2.5 * cm) & (off <= 9 * cm)
        outer = np.zeros_like(ov)
        if t is not None:
            # exact outer face: the parallel line with most coverage near -t
            cand = ov & (np.abs(off + t) < 0.7)
            face_off = -t
            if cand.any():
                vals, cnt = np.unique(np.round(off[cand], 2), return_counts=True)
                lens = [((np.minimum(hi[cand & (np.round(off, 2) == v)], a1) - np.maximum(lo[cand & (np.round(off, 2) == v)], a0)).sum()) for v in vals]
                face_off = float(vals[int(np.argmax(lens))])
            outer = ov & (off < face_off - 0.15) & (off >= face_off - 3 * cm)

        def spans(mask, min_len_cm):
            iv = sorted(zip(np.maximum(lo[mask], a0), np.minimum(hi[mask], a1)))
            out = []
            for u, v in iv:
                if out and u <= out[-1][1] + 2 * cm:
                    out[-1][1] = max(out[-1][1], v)
                else:
                    out.append([u, v])
            return [(u, v) for u, v in out if (v - u) * k >= min_len_cm]

        def cover(mask, u, v):
            iv = sorted(zip(np.maximum(lo[mask], u), np.minimum(hi[mask], v)))
            tot, cur = 0.0, None
            for x0, x1 in iv:
                if x1 <= x0:
                    continue
                if cur is None or x0 > cur[1]:
                    if cur:
                        tot += cur[1] - cur[0]
                    cur = [x0, x1]
                else:
                    cur[1] = max(cur[1], x1)
            if cur:
                tot += cur[1] - cur[0]
            return tot / max(v - u, 1e-6)

        used = []
        for u, v in spans(outer, 35):
            ev = {"outside_lines": True, "casing_inside": round(cover(near_in, u, v), 2), "frame_inside": round(cover(mid_in, u, v), 2)}
            kind = "door?" if ev["casing_inside"] > 0.5 else "window?" if ev["frame_inside"] > 0.5 else "opening?"
            feats.append(Feature(o, c, u, v, nrm, kind, ev))
            used.append((u, v))
        for u, v in spans(mid_in, 40):  # window frame + sill, radiators: lines 2.5-9 cm inside the face
            if any(min(v, b) - max(u, a) > 0.3 * (v - u) for a, b in used):
                continue
            # radiator: many short fins perpendicular to the wall just inside the face
            perp = s[vt] if o == "h" else s[hz]
            pc = perp[:, 0] if o == "h" else perp[:, 1]  # position along the wall
            po0 = np.minimum(perp[:, 1], perp[:, 3]) if o == "h" else np.minimum(perp[:, 0], perp[:, 2])
            po1 = np.maximum(perp[:, 1], perp[:, 3]) if o == "h" else np.maximum(perp[:, 0], perp[:, 2])
            plen = (po1 - po0) * k
            pmid = ((po0 + po1) / 2 - c) * nrm
            fins = int(((pc > u) & (pc < v) & (plen > 0.4) & (plen < 4) & (pmid > 0) & (pmid < 9 * cm)).sum())
            if (v - u) * k > 320:
                continue  # longer than any window: furniture or panelling along the wall
            depth = max(off[mid_in & (np.minimum(hi, v) - np.maximum(lo, u) > 0)].max(initial=0), 0) * k
            kind = "radiator?" if fins >= 12 else "window?"
            feats.append(Feature(o, c, u, v, nrm, kind, {"outside_lines": False, "depth_cm": round(depth, 1), "fins": fins}))
        # real gaps through this wall
        for g in gaps:
            if g.kind not in ("gap", "unclear") or g.orient != o:
                continue
            gc = (g.y0 + g.y1) / 2 if o == "h" else (g.x0 + g.x1) / 2
            u, v = (g.x0, g.x1) if o == "h" else (g.y0, g.y1)
            if abs(gc - c) < (g.depth_pt + 2 * cm) and min(v, a1) - max(u, a0) > 0.5 * (v - u):
                feats = [f for f in feats if not (f.orient == o and f.face == c and min(f.a1, v) - max(f.a0, u) > 0.3 * (f.a1 - f.a0))]
                feats.append(Feature(o, c, u, v, nrm, "gap", {"gap_depth_cm": round(g.depth_pt * k, 1)}))
    return feats


def face_labels(clean: np.ndarray, segs: np.ndarray):
    """Faces = regions of the clean raster separated by the drawn model edges (1 px barriers)."""
    bar = np.zeros(clean.shape[:2], np.uint8)
    for x0, y0, x1, y1 in segs:
        cv2.line(bar, (int(x0 * PX_PER_PT), int(y0 * PX_PER_PT)), (int(x1 * PX_PER_PT), int(y1 * PX_PER_PT)), 1, 1)
    n, lab, stats, _ = cv2.connectedComponentsWithStats((bar == 0).astype(np.uint8), connectivity=4)
    return lab, stats


def element_faces(flab, clean: np.ndarray, leader: Leader, scale: int, max_face_m2: float = 3.0,
                  max_total_m2: float = 6.0, col_tol: float = 14.0, ring_px: int = 5, blocked=()):
    """Face under the leader tip, grown over neighbouring faces of a similar colour (cabinet carcasses etc.).
    ring_px: how far growth may step over drawn edge lines; blocked: page points (other elements' leader tips)
    whose faces must not be absorbed."""
    lab, stats = flab
    m2_px = (0.0254 / DPI * scale) ** 2
    tx, ty = leader.tip
    bx, by = leader.base or leader.tip
    L = math.hypot(tx - bx, ty - by) or 1.0
    l0 = 0
    for step in (1.2, 2.0, 3.0, 4.5):
        X, Y = int((tx + (tx - bx) / L * step) * PX_PER_PT), int((ty + (ty - by) / L * step) * PX_PER_PT)
        if 0 <= Y < lab.shape[0] and 0 <= X < lab.shape[1] and lab[Y, X] > 0:
            l0 = lab[Y, X]
            break
    if not l0 or stats[l0, cv2.CC_STAT_AREA] * m2_px > max_face_m2:
        return None

    def face_mask(l):
        x, y, w, h, _ = stats[l]
        return (slice(y, y + h), slice(x, x + w)), lab[y:y + h, x:x + w] == l

    region = np.zeros(lab.shape, bool)
    sl, m = face_mask(l0)
    region[sl] |= m
    mean0 = clean[region].mean(axis=0)
    seen = {l0}
    for bx_, by_ in blocked:
        BX, BY = int(bx_ * PX_PER_PT), int(by_ * PX_PER_PT)
        for dx_ in range(-6, 7, 3):
            for dy_ in range(-6, 7, 3):
                if 0 <= BY + dy_ < lab.shape[0] and 0 <= BX + dx_ < lab.shape[1] and lab[BY + dy_, BX + dx_] not in (0, l0):
                    seen.add(lab[BY + dy_, BX + dx_])
    for _ in range(40):
        ys, xs = np.nonzero(region)
        y0, y1, x0, x1 = max(ys.min() - 4, 0), ys.max() + 5, max(xs.min() - 4, 0), xs.max() + 5
        ring = cv2.dilate(region[y0:y1, x0:x1].astype(np.uint8), np.ones((ring_px, ring_px), np.uint8)).astype(bool) & ~region[y0:y1, x0:x1]
        added = False
        for l in np.unique(lab[y0:y1, x0:x1][ring]):
            if l == 0 or l in seen:
                continue
            seen.add(l)
            if stats[l, cv2.CC_STAT_AREA] * m2_px > max_face_m2:
                continue
            sl, m = face_mask(l)
            if np.abs(clean[sl][m].mean(axis=0) - mean0).max() < col_tol:
                region[sl] |= m
                added = True
        if not added or region.sum() * m2_px > max_total_m2:
            break
    # close the 1 px barrier lines inside the merged region
    return cv2.morphologyEx(region.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)).astype(bool)


def element_region(clean: np.ndarray, leader: Leader, tol: int = 12) -> np.ndarray | None:
    tx, ty = leader.tip
    bx, by = leader.base or leader.tip
    L = math.hypot(tx - bx, ty - by) or 1.0
    sx, sy = tx + (tx - bx) / L * 1.2, ty + (ty - by) / L * 1.2  # step just past the arrow tip
    X, Y = int(sx * PX_PER_PT), int(sy * PX_PER_PT)
    H, W = clean.shape[:2]
    if not (0 <= X < W and 0 <= Y < H):
        return None
    mask = np.zeros((H + 2, W + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    cv2.floodFill(clean, mask, (X, Y), 0, (tol,) * 3, (tol,) * 3, flags)
    return mask[1:-1, 1:-1] > 0


def polygon_area(poly) -> float:
    a = 0.0
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        a += x0 * y1 - x1 * y0
    return abs(a) / 2


def load_labels(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
