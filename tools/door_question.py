"""Picture for the owner: page 1 with only its interior doors marked, plus area names, to ask where each door leads.
Usage: tools/.venv/Scripts/python tools/door_question.py  ->  debug/p1-doors-question.png
Door ids come from data/plan-geometry.json (openings confirmed as doors by checking the plan and renders)."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
import plangeo as pg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOORS = ["p1-I", "p1-J", "p1-N", "p1-O", "p1-P"]

geo = json.loads((ROOT / "data" / "plan-geometry.json").read_text(encoding="utf-8"))
room = next(r for r in geo["rooms"] if r["page"] == 1)
ox, oy = room["origin_pt"]
s = room["m_per_pt"]
to_px = lambda p: (int((p[0] / s + ox) * pg.PX_PER_PT), int((p[1] / s + oy) * pg.PX_PER_PT))  # noqa: E731

doc = pymupdf.open(ROOT / "source" / "Milo_-dispozicija_elemenata.pdf")
page = doc[0]
img = pg.viewport_raster(doc, page, pg.render(page))
cv2.polylines(img, [np.array([to_px(p) for p in room["interior"]["polygon"]], np.int32)], True, (0, 150, 0), 3)

def label(text, at, scale=1.5):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 3)
    x, y = at[0] - tw // 2, at[1] + th // 2
    cv2.rectangle(img, (x - 10, y - th - 10), (x + tw + 10, y + 12), (255, 255, 255), -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (40, 40, 40), 3)

doors = {o["id"]: o for o in room["openings"] if o["id"] in DOORS}
for oid, o in doors.items():
    a, b = to_px(o["from"]), to_px(o["to"])
    cv2.line(img, a, b, (220, 0, 120), 16)
    cx, cy = (a[0] + b[0]) // 2, (a[1] + b[1]) // 2
    dx, dy = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}[o["wall_side"]]
    c = (cx + dx * 105, cy + dy * 105)
    cv2.circle(img, c, 52, (255, 255, 255), -1)
    cv2.circle(img, c, 52, (220, 0, 120), 6)
    t = oid.split("-")[1]
    (tw, th), _ = cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, 2.2, 5)
    cv2.putText(img, t, (c[0] - tw // 2, c[1] + th // 2), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 0, 0), 5)

# area names, placed at extracted features (no hand-placed coordinates)
els = {e["name_en"]: e for e in room["elements"] if e.get("footprint")}
cent = lambda e: to_px(np.mean(e["footprint"], axis=0))  # noqa: E731
label("ENTRANCE HALLWAY", (cent(els["console shelf"])[0] + 330, cent(els["console shelf"])[1] - 180))
corr = np.mean([to_px(doors[d]["from"]) for d in ("p1-N", "p1-O", "p1-P")], axis=0).astype(int)
label("CORRIDOR", (int(corr[0]), int(corr[1]) - 40))
label("KITCHEN", cent(els["kitchen"]))
label("DINING", cent(els["dining table"]))
tv = cent(els["TV unit with AC concealer"])
label("LIVING ROOM", (tv[0], tv[1] - 330))
label("BALCONY", (tv[0] + 120, tv[1] + 360), 1.2)

x0, y0, x1, y1 = (int(v * pg.PX_PER_PT) for v in (125, 230, 830, 680))
cv2.imwrite(str(ROOT / "debug" / "p1-doors-question.png"), cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_RGB2BGR))
print("wrote debug/p1-doors-question.png", sorted(doors))
