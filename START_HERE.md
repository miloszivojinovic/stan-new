# Start here

This folder is a handoff for building a three.js walkthrough of an apartment. It was prepared in a Claude chat after
reading all seven design PDFs (one layout PDF and six render sets).

## Steps

1. Unzip this folder.
2. Open it in Claude Code (in a terminal: `cd` into the folder and run `claude`).
3. Paste the prompt below as your first message. Claude Code reads CLAUDE.md on its own.

## Prompt to paste

> Read CLAUDE.md and docs/room-notes.md, then look at reference/contact-sheets and reference/plans. Don't write code
> yet. First give me: (1) your build plan, (2) how you'll extract the plan geometry and check it against the labelled
> dimensions, and (3) the short list of assumptions you need me to confirm, ceiling height first. Then wait for my
> answers.

## Worth doing in parallel

Ask your designer for the SketchUp (.skp) file the plans were made from, or an export of it (glTF/GLB, OBJ, DAE or FBX).
It would replace most of the estimating: real heights, openings and room positions. Also useful: a whole-apartment
floor plan, renders of the children's room, and the ceiling height.

## What is in this folder

```
apartment-3d-handoff/
|-- START_HERE.md
|-- CLAUDE.md                  brief that Claude Code reads automatically
|-- docs/room-notes.md         glossary, per-room contents, look notes, render index
|-- data/layout-labels.json    every plan label and dimension with its position
|-- source/                    the original layout PDF (it has the text layer)
|-- reference/
|   |-- plans/                 5 plan pages as 200 dpi PNGs
|   |-- renders/<room>/        42 renders, unchanged from the PDFs
|   `-- contact-sheets/        overview sheets per room
`-- tools/                     measure_plans.py, extract_labels.py
```

The scripts in tools/ need Python with numpy and opencv-python (measure_plans.py) or poppler (extract_labels.py).
You do not have to run either: the label data is already in data/.
