"""Compose comparison sheets: reference render | our screenshot, 50/50 blend | reference edges over ours.
Usage: tools/.venv/Scripts/python tools/compare.py [room | room:cam ...]  ->  debug/compare/<room>-<cam>.png
Run tools/snap.mjs first."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
rooms = json.loads((ROOT / "data" / "rooms.json").read_text(encoding="utf-8"))["rooms"]
want = sys.argv[1:]
for room in rooms:
    for cam in room["cameras"]:
        key = f"{room['id']}:{cam['id']}"
        if want and not any(w in (room["id"], key) for w in want):
            continue
        ours_p = ROOT / "debug" / "compare" / f"{room['id']}-{cam['id']}-ours.png"
        if not ours_p.exists() or not cam.get("ref"):
            continue
        ours = cv2.imread(str(ours_p))
        ref = cv2.resize(cv2.imread(str(ROOT / cam["ref"])), (ours.shape[1], ours.shape[0]), interpolation=cv2.INTER_AREA)
        blend = cv2.addWeighted(ref, 0.5, ours, 0.5, 0)
        edges = cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), (5, 5), 0), 40, 110)
        over = (ours * 0.75).astype(np.uint8)
        over[edges > 0] = (0, 0, 255)
        sheet = np.vstack([np.hstack([ref, ours]), np.hstack([blend, over])])
        for txt, (x, y) in (("reference render", (12, 32)), ("our model", (ours.shape[1] + 12, 32)),
                            ("50/50 blend", (12, ours.shape[0] + 32)), ("reference edges over ours", (ours.shape[1] + 12, ours.shape[0] + 32))):
            cv2.putText(sheet, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 3)
            cv2.putText(sheet, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 1)
        out = ROOT / "debug" / "compare" / f"{room['id']}-{cam['id']}.png"
        cv2.imwrite(str(out), cv2.resize(sheet, (sheet.shape[1] * 3 // 4, sheet.shape[0] * 3 // 4), interpolation=cv2.INTER_AREA))
        print("wrote", out.relative_to(ROOT))
