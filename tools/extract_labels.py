"""Extract every text label and number from the layout PDF, with bounding boxes.

Usage:  python3 extract_labels.py <layout.pdf> <out.json>

Needs poppler's `pdftotext` and `pdfinfo` on PATH. You do not have to run this:
its output is already in data/layout-labels.json.

Coordinates are PDF points, origin top-left (A3 landscape = 1190.55 x 841.89 pt).
To place a box on a plan PNG rendered at D dpi, multiply by D / 72.
"""
import html
import json
import re
import subprocess
import sys

# Scales are printed in each page's caption ("R = 1:50" / "R = 1:25").
SCALES = {1: "1:50", 2: "1:25", 3: "1:25", 4: "1:25", 5: "1:25"}

PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">')
LINE_RE = re.compile(
    r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', re.S
)
WORD_RE = re.compile(r"<word [^>]*>(.*?)</word>", re.S)


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def page_count(pdf: str) -> int:
    m = re.search(r"^Pages:\s+(\d+)", run(["pdfinfo", pdf]), re.M)
    return int(m.group(1)) if m else 1


def main(pdf: str, out: str) -> None:
    pages = []
    for p in range(1, page_count(pdf) + 1):
        xml = run(["pdftotext", "-bbox-layout", "-f", str(p), "-l", str(p), pdf, "-"])
        pm = PAGE_RE.search(xml)
        lines = []
        for x0, y0, x1, y1, body in LINE_RE.findall(xml):
            words = [html.unescape(w).strip() for w in WORD_RE.findall(body)]
            text = " ".join(w for w in words if w)
            if not text:
                continue
            is_num = text.isdigit()
            lines.append(
                {
                    "text": text,
                    "value": int(text) if is_num else None,
                    "bbox_pt": [round(float(v), 2) for v in (x0, y0, x1, y1)],
                }
            )
        pages.append(
            {
                "page": p,
                "scale": SCALES.get(p),
                "width_pt": float(pm.group(1)) if pm else None,
                "height_pt": float(pm.group(2)) if pm else None,
                "lines": lines,
            }
        )
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {"source": pdf.split("/")[-1], "units": "pt", "origin": "top-left", "pages": pages},
            f,
            ensure_ascii=False,
            indent=1,
        )
    print(f"wrote {out}: " + ", ".join(f"p{pg['page']}={len(pg['lines'])} lines" for pg in pages))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
