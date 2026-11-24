# Apartment 3D walkthrough (three.js)

Goal: a browser-based, true-to-scale 3D model of an apartment, built from an interior-design package
(dimensioned plans plus photoreal renders), so the owner can walk through it and check proportions,
finishes and lighting. Labels in the source files are Serbian; docs/room-notes.md has a glossary.

**Status (2026-11-23):** plan geometry is extracted and validated for all rooms; the viewer (src/, see
docs/viewer.md) has every v1 scene: the open-plan hallway/living/dining/kitchen, work room, bedroom, bathroom and
toilet, each compared with all of its renders (debug/compare/). Read this file, then docs/room-notes.md and
docs/viewer.md before changing anything. Render analyses per room are in data/estimates/ (note: the designer modelled
each room with a different ceiling height, 2.34-2.67 m, and the open-plan scene has a lowered living-room ceiling; the
owner's real height is 2.40 m everywhere). Open with the owner: the island top is 0.77 m (13 cm under the 0.90 kitchen
worktop) in the renders; whether the living-room ceiling is really lowered; render p17's and p03's viewpoints are
weakly determined.

## What is here

| Path | What it is | Trust it for |
|---|---|---|
| source/Milo_-dispozicija_elemenata.pdf | "Element layout" PDF exported from SketchUp LayOut. 5 A3 pages with a real text layer. | geometry, dimensions |
| reference/plans/*.png | The same 5 pages at 200 dpi, named by room | measuring geometry from pixels |
| data/layout-labels.json | Every label and dimension number on the plans with its bounding box (PDF points, origin top-left; px at 200 dpi = pt x 200/72) | dimensions, element names |
| reference/renders/\<room\>/ | 42 photoreal renders, one per original PDF page, extracted unchanged. No text, no dimensions. | look: materials, furniture, lighting; rough heights |
| reference/contact-sheets/ | One overview sheet per room (two for the living room) | orientation |
| tools/ | measure_plans.py (rough scale check), extract_labels.py (already run) | |

The six render PDFs contain only images, so only their images are included here.

## The plan pages

| Page | Content | Scale | Size |
|---|---|---|---|
| p1 | Entrance hallway + living room + dining + kitchen as one open-plan outline. A checkerboard balcony shows at the bottom right. | 1:50 | outer bounding box incl. walls about 11.2 x 6.6 m; irregular shape |
| p2 | Work room | 1:25 | interior about 2.37 x 4.43 m |
| p3 | Children's room (plan only, **no renders**) | 1:25 | interior about 4.05 x 2.82 m |
| p4 | Bedroom, with a balcony about 1.5 m deep on its right side and an alcove at the top left | 1:25 | main width about 2.67 m (labels 60 + 207) |
| p5 | Bathroom (left) and toilet (right) | 1:25 | bathroom about 1.9 x 2.5 m (stepped top wall); toilet about 0.87 x 2.38 m |

How the plans are drawn:
- Walls are dark-red bands, about 19-20 cm thick (about 15 cm around the toilet). Thin black outline strokes split them
  into separate faces. The thickness may be a drafting convention; confirm it.
- Openings are not drawn uniformly. Some show as gaps in the red bands with the frame or leaf drawn in them (for example
  the bedroom's two balcony openings on its right wall); others are only a thin outline. Detect both kinds, then verify
  each opening against the renders.
- Furniture is drawn as filled outlines. Blue leader lines with black text are names. Bright-red lines and numbers
  are dimensions in cm.
- Metres per pixel = scale x 0.0254 / dpi, so at 200 dpi one pixel is 6.35 mm at 1:50 and 3.175 mm at 1:25.

## Verified so far (use as regression tests for your extraction)

Pixel measurement agrees with the labelled dimensions to within 1-2 cm:
- Work room interior: measured 237.5 x 442.6 cm. Label chains: 67+110+60 = 237 and 114+165+162 = 441.
- Children's room interior: measured 405.4 x 281.6 cm. Label chains: 60+132+82+130 = 404 and 60+97+124 = 281.
- Toilet interior: measured about 87 x 238 cm (the plan carries an "87" label).

## Unknowns: do not silently invent these

1. **Ceiling height** is not stated anywhere. It is probably around 2.6-2.7 m. Estimate it from door-to-ceiling
   proportions in the renders, then ask the owner to confirm.
2. **Door and window heights** (sill, head): widths and positions are in the plans, heights only in the renders.
   Interior doors look like standard height.
3. **Wall thickness**: see above.
4. **How the rooms connect**: there is no master plan. The only clue is that the checkerboard balcony appears in both
   the living-room plan and the bedroom plan.
5. **Children's room**: no renders. Use neutral finishes and flag them as assumed.
6. **Furniture and fixture heights, ceiling details (LED coves, tracks), curtains**: estimate from the renders and
   standard sizes.
7. **Revision status**: five of the six render PDFs were titled IZMENA (revision); the layout PDF and the toilet
   renders were not. Ask whether the plans match the latest revision.

**Answered by the owner on 2026-11-20** (data/owner-facts.json, tag these `"source": "owner"`): ceiling 2.40 m in every
room; standard interior doors; interior walls 15 cm; every PDF is accurate and current; children's room gets no scene,
only its door is shown. Page-1 doors (debug/p1-doors-question.png): I = work room, J = toilet, N = bathroom,
O = bedroom, P = children's room. The front door is the first thing on the hallway's top-left wall. Where a label and
the drawing disagree (dining table, children's desk, boiler cabinet), the label wins.

Plan geometry is extracted by `tools/extract_geometry.py` (vector edges from the PDF, raster only for classification)
into data/plan-geometry.json; the validation report is debug/validation.md.

Every value in data/ that was not read from a plan or a label must carry `"source": "estimated"` or `"assumed"`,
and the viewer needs an Assumptions panel that lists them.

## v1 scope

- One scene per room (hallway + living + dining + kitchen is one scene), with a room switcher.
- Walls from the plan outlines, openings at the measured positions, floors, ceilings.
- Furniture and fixtures as parametric stand-ins sized from plan footprints plus estimated heights. Clearly simplified,
  swappable for GLB models later.
- Materials and lighting approximating the renders (see docs/room-notes.md): LED strip lines in ceiling recesses,
  pendants, round ceiling lights, track lights, under-cabinet and underglow LEDs.
- Cameras: orbit, top-down (un-mirrored), first-person walk (eye height 1.6 m, simple wall collision).
- Tools: measuring tool, dimension overlay from the plan labels, click-to-inspect (name, size, source), Assumptions panel.

Not in v1: path-traced realism, exact product models, a single connected apartment. For the last one, wait for a
master plan or a SketchUp export, or offer a clearly labelled guessed layout.

The renders are path-traced stills. Match colours, light placement and warmth, not global illumination.

## Conventions

- Units are metres. three.js is Y-up. Plan to world: x = plan x, z = plan y (image y grows downward), y = height.
  A top-down camera with up = (0, 0, -1) then shows the plan un-mirrored. Never mirror a room.
- Default stack: Vite + three.js, vanilla TypeScript or JavaScript, no UI framework. Ask the owner if they prefer another.
- data/rooms.json drives the scene (polygons, openings, elements, materials, each with a source field). No coordinates
  hard-coded in scene code.
- Openings: build wall segments around them, or use three-bvh-csg. Pick whichever is more robust.
- Texture UVs in metres. CC0 textures and models (Poly Haven, ambientCG) are fine; record URLs and licences in docs/assets.md.

## How to work

1. Before any code: give a plan, say how you will extract and validate the geometry, and list the assumptions to
   confirm (ceiling height first). Wait for approval.
2. Extract geometry programmatically (colour segmentation and contour tracing on the plan PNGs). Never hand-read
   coordinates from an image. Save a debug overlay of each extracted polygon on its plan to debug/.
3. Validate: every dimension in data/layout-labels.json that spans two measured features should match within 2 cm.
   Report mismatches instead of fudging them.
4. Visual loop: run the dev server, screenshot each room from viewpoints near the reference renders (Playwright or a
   browser integration), compare, fix. A room is not done until it has been compared.
5. Treat source/ and reference/ as read-only.

## If a SketchUp file or model export arrives

The plans were made from a SketchUp model (LayOut, SketchUp Pro 2021). If the owner can get the .skp, or an export
(glTF/GLB, OBJ, DAE or FBX), use it as the geometry source and keep these PDFs as validation. Optimise it with
gltf-transform. Renderer lights usually do not carry over, so re-author them.
