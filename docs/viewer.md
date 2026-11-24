# Running the walkthrough

```
npm install
npm run dev
```

Open http://localhost:5173. The bar at the top has:

- **Room** switcher: hallway + living room + dining + kitchen (one open-plan scene, with its balcony), work room,
  bedroom (with its balcony), bathroom, toilet. The children's room is not modelled (owner); its door is in the
  open-plan scene.
- **Orbit**, **Top-down** (oriented like the plan pages, never mirrored) and **Walk** (W A S D or arrows, mouse to
  look, Shift to go faster, Esc to stop; eye height 1.6 m; walls and furniture block you).
- **Render viewpoints**: the camera of each reference render, fitted from the renders. **Compare with render** lays
  the reference image over the view.
- **Plan dimensions**: the labelled dimensions from the plan, drawn on the floor (orange = label and drawing differ).
- **Measure**: click two points.
- **Assumptions**: every value that was not read from the plan, with its source (owner, estimated, assumed).
- **Soft shadows** (ambient occlusion), **Daylight**, **Exposure**.
- **Ceiling**: the owner's 2.40 m, or the ceiling the designer's renders were modelled with (2.34-2.63 m, different
  in every room). In the open-plan scene the render model's ceiling is not flat: the living room is about 2.50, the
  hallway 2.54, dining and kitchen 2.63; the render variant builds those steps as lowered-ceiling blocks. The owner
  variant is 2.40 m everywhere.

Click any object to see its name, size and where each number came from.

Some render viewpoints hide objects that the designer left out of that render (for example the kitchen island in
the dining-table view). The help line under the bar names them; they come back when you leave the viewpoint.

The open-plan scene is the largest: about 3 s to load on a normal GPU. Each LED outline glows on the ceiling but
lights the room through one soft area light, because every area light adds to each shader's compile time.

## How the data is made

```
tools/.venv/Scripts/python tools/extract_geometry.py   # plan PDF -> data/plan-geometry.json, debug/ overlays
tools/.venv/Scripts/python tools/build_rooms.py        # + owner facts + data/room-specs/*.json -> data/rooms.json
node tools/snap.mjs work-room                          # screenshots from the render viewpoints (dev server running)
tools/.venv/Scripts/python tools/compare.py work-room  # debug/compare/<room>-<view>.png comparison sheets
```

- `data/plan-geometry.json`: walls, openings, labelled furniture, checked against all 58 plan dimensions
  (`debug/validation.md`).
- `data/estimates/<room>.json`: camera fits, heights and colours measured from the renders.
- `data/room-specs/<room>.json`: how each room is built; every non-plan value carries its source. Room-level keys
  besides the obvious ones: `render_ceiling` and `ceiling_zones` (render-model ceiling heights), `exterior_walls`
  (`"right"` or `"right@10.84"` for one wall), `env_intensity` (neutral fill light), `walk_start`; per viewpoint
  `hide`. Heights can be `"ceiling"`, `{"below_ceiling": d}` or carry a `"render"` value for the render variant.
- `data/rooms.json` (owner ceiling) and `data/rooms-render.json` (render-model ceiling) are generated: do not edit.

## Publishing (Vercel)

The viewer is a static site: `npm run build` writes `dist/` (index.html plus one script, about 1.2 MB) and the
visitor's browser does all the 3D drawing. Paths are relative, so `dist/` works at any address.

The designer's renders are left out of builds by default, because a hosted site is public and the renders are the
designer's work; "Compare with render" is then hidden. Set the environment variable `INCLUDE_RENDERS=1` for the
build to copy them into `dist/reference/renders` (29 MB) and bring the button back.

Vercel (free Hobby plan) imports the GitHub repository and detects Vite: build command `npm run build`, output
directory `dist`. Every push to `main` redeploys the production URL; other branches get preview URLs. The
production URL is open to anyone who has it; password protection is a paid Vercel feature.
