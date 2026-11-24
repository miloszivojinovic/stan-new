"""Rough scale check: measure the dark-red wall bands in the plan PNGs.

Usage:  python3 measure_plans.py <folder with layout-pN-*.png> [dpi]     (default dpi: 200)
Needs:  pip install numpy opencv-python

It finds the dark-red wall bands, bridges the thin black outline strokes that split them
into separate faces, and prints each connected wall shape's outer size, band thickness and
an approximate interior size (outer minus bands), all in centimetres.

Caveat: the "interior" figure is only meaningful for plain rectangular rooms (work room,
children's room, toilet). L-shaped or stepped rooms (hallway/living, bedroom, bathroom) and
rings split by door or window openings need proper polygon tracing. This is a scale check,
not the extraction pipeline.

Scales come from the page captions: page 1 is 1:50, pages 2-5 are 1:25.
"""
import glob
import sys

import cv2
import numpy as np

PAGES = {
    1: (50, "hallway + living/dining/kitchen"),
    2: (25, "work room"),
    3: (25, "children's room"),
    4: (25, "bedroom"),
    5: (25, "bathroom + toilet"),
}


def cm_per_px(scale: int, dpi: float) -> float:
    # px -> inches (1/dpi) -> mm on paper -> mm in reality (x scale) -> cm
    return scale * 25.4 / dpi / 10.0


def first_run(line: np.ndarray) -> int:
    idx = np.flatnonzero(line)
    if idx.size == 0:
        return 0
    breaks = np.flatnonzero(np.diff(idx) > 1)
    end = idx[breaks[0]] if breaks.size else idx[-1]
    return int(end - idx[0] + 1)


def band(mask: np.ndarray, axis_lines, from_end: bool, along_rows: bool) -> float:
    vals = []
    for i in axis_lines:
        line = mask[i, :] if along_rows else mask[:, i]
        if from_end:
            line = line[::-1]
        r = first_run(line)
        if r:
            vals.append(r)
    return float(np.median(vals)) if vals else 0.0


def main(src: str, dpi: float) -> None:
    for page, (scale, name) in PAGES.items():
        files = glob.glob(f"{src}/layout-p{page}-*.png")
        img = cv2.imread(files[0]) if files else None
        if img is None:
            print(f"page {page}: image not found")
            continue
        b, g, r = (img[..., i].astype(int) for i in range(3))
        # wall red is dark (R ~120-200); dimension lines and numbers are bright red (R > 200)
        wall = ((r > 120) & (r < 200) & (g < 40) & (b < 40)).astype(np.uint8)
        wall = cv2.morphologyEx(wall, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        k = cm_per_px(scale, dpi)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(wall, connectivity=8)
        comps = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 5000]
        print(f"== page {page}: {name}  (1:{scale}, {k:.3f} cm/px)")
        for i in sorted(comps, key=lambda c: stats[c, cv2.CC_STAT_LEFT]):
            x, y, w, h, _area = stats[i]
            m = lab[y : y + h, x : x + w] == i
            rows = range(int(h * 0.3), int(h * 0.7))
            cols = range(int(w * 0.3), int(w * 0.7))
            left = band(m, rows, False, True)
            right = band(m, rows, True, True)
            top = band(m, cols, False, False)
            bottom = band(m, cols, True, False)
            iw, ih = w - left - right, h - top - bottom
            print(
                f"  outer {w * k:6.1f} x {h * k:6.1f} cm | bands L{left * k:4.1f} R{right * k:4.1f} "
                f"T{top * k:4.1f} B{bottom * k:4.1f} cm | interior ~ {iw * k:6.1f} x {ih * k:6.1f} cm"
            )


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 200.0)
