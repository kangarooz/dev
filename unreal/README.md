# A33B Perch — built from scratch in Unreal Engine 5

This folder builds the **`A33B_perch_colored`** model directly inside Unreal Engine using the
editor's Python API. It does **not** import the `.3mf` file — it *generates* the perch geometry
procedurally and bakes it into colored `StaticMesh` asset(s) placed in your level.

> **Why not just import the `.3mf`?** Unreal Engine has **no native `.3mf` importer**. 3MF is
> absent from both the Interchange supported list (FBX, glTF/GLB, MaterialX) and the Datasmith
> CAD list (STEP, JT, IGES, IFC, …). See [*If you'd rather import your real `.3mf`*](#if-youd-rather-import-your-real-3mf) below.

## What gets built

A parametric perch in three parts (all dimensions in centimetres, edit them in `CONFIG`):

| Part  | Shape                                  | Default color |
|-------|----------------------------------------|---------------|
| base  | flat rectangular plate                 | dark grey     |
| posts | two vertical support cylinders         | teal          |
| rod   | horizontal cylinder resting on the posts | orange      |

## Requirements

- Unreal Engine 5 (5.1+; verified against the 5.3/5.4 Python API).
- The **Python Editor Script Plugin** enabled (Edit → Plugins → search "Python") — standard.
- For the primary backend only: the **Geometry Script** plugin enabled (Edit → Plugins →
  search "Geometry Script", then restart the editor). It's marked *Experimental* and is **off by
  default**. If you don't enable it, the script automatically uses the plugin-free fallback.

## How to run

1. Open your Unreal project and the level you want the perch placed in.
2. Open **Window → Python** (or use the Output Log's `Cmd` dropdown set to `Python`).
3. Run the script (adjust the path to wherever you saved it):

   ```python
   exec(open(r"C:\Users\nicholwilliams\Downloads\build_perch.py").read())
   ```

   or from the Output Log command line:

   ```
   py "C:\Users\nicholwilliams\Downloads\build_perch.py"
   ```

Generated assets land in the Content Browser under `/Game/A33B_Perch/`, and the perch appears in
the current level at the origin (change `spawn_location` in `CONFIG`).

### Choosing a backend explicitly

`build("auto")` picks Geometry Script if available, otherwise the fallback. To force one:

```python
import build_perch
build_perch.build(backend="geometry_script")   # engine primitives; 3 colored meshes + grouped actors
build_perch.build(backend="mesh_description")   # raw geometry; ONE combined mesh, material slot per part
```

- **`geometry_script`** — Unreal generates clean primitives (correct normals/UVs). Produces one
  `StaticMesh` per part (`SM_A33B_Perch_Base/Posts/Rod`) and spawns them as a grouped actor set.
- **`mesh_description`** — geometry is generated in pure Python and welded into a single
  `SM_A33B_Perch` with one material slot per part. No extra plugin needed.

## Tuning

Everything is driven by the `CONFIG` dict at the top of `build_perch.py`: part dimensions, post
spacing, rod length, cylinder `segments` (smoothness), colors, spawn location, and asset paths.
Re-running overwrites/updates the same assets.

> **If faces look inside-out** (only in the `mesh_description` backend), set
> `"flip_winding": True` in `CONFIG` and re-run. Winding could not be visually validated in the
> authoring environment; the Geometry Script backend is unaffected.

## If you'd rather import your real `.3mf`

To bring the actual `A33B_perch_colored.3mf` into Unreal (preserving its true geometry), convert
it to a UE-supported format first — 3MF has no native importer:

1. **Convert** with Blender (File → Import → 3MF via the bundled/community 3MF add-on), then
   **export glTF (`.glb`)** — best material/color fidelity — or FBX. A slicer or Microsoft 3D
   Builder can re-export OBJ/STL, but STL carries no colors or UVs.
2. **Import** the `.glb`/`.fbx` into Unreal (drag into the Content Browser, or File → Import Into
   Level). glTF flows through the modern Interchange pipeline.

## Notes & limitations

- Every `unreal.*` call was verified against the official UE 5.3/5.4 Python API docs. A couple of
  version-sensitive spots (`unreal.Transform` rotation arg, `unreal.StaticMeshFactory`,
  actor attachment) are guarded with `try/except` and log a clear message if unavailable.
- The script could not be executed inside a live editor from the authoring environment, so the
  dimensions produce a *plausible* perch. Tune `CONFIG` to match the real part.
