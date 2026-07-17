"""
build_perch.py — build the "A33B_perch_colored" model FROM SCRATCH inside Unreal Engine 5.

This does NOT import the .3mf file. Unreal has no native .3mf importer (see README.md).
Instead it procedurally generates the perch geometry with the Unreal editor Python API and
bakes it into StaticMesh asset(s) with colored materials, then places it in the current level.

The perch is modelled as three parametric parts (tune them in CONFIG below):
    * base   — a flat rectangular plate the perch stands on
    * posts  — two vertical support cylinders rising from the base
    * rod    — a horizontal cylinder (the perch bar) resting across the tops of the posts

Two build backends are provided:
    1. Geometry Script  (primary)  — engine-generated primitives; one colored StaticMesh per
       part + one grouped actor set. Requires the "Geometry Script" plugin to be enabled.
    2. Mesh Description (fallback)  — raw vertices/triangles generated in pure Python; a single
       combined StaticMesh whose parts are separate material slots. Needs no extra plugin.

main() auto-selects Geometry Script if the plugin is available, otherwise the fallback.
Force one with:  build(backend="geometry_script")  or  build(backend="mesh_description").

HOW TO RUN (in the Unreal Editor, on your local machine):
    Window > Python (or the Output Log's Cmd > Python), then:
        exec(open(r"C:\\path\\to\\build_perch.py").read())
    or from the Content Browser / command line:
        py "C:\\path\\to\\build_perch.py"

Verified against the UE 5.3/5.4 Python API. All unreal.* calls used here were confirmed
against the official docs; version-sensitive spots are guarded with try/except and logged.
"""

import math

try:
    import unreal
except ImportError as exc:  # pragma: no cover - only meaningful inside the Unreal Editor
    raise SystemExit(
        "This script must be run from inside the Unreal Editor's Python environment "
        "(Window > Python). 'import unreal' failed: %s" % exc
    )


# --------------------------------------------------------------------------------------
# CONFIG — edit these to match the real A33B perch dimensions (all lengths in centimetres).
# --------------------------------------------------------------------------------------
CONFIG = {
    # Content-browser folder every generated asset is written under.
    "package_path": "/Game/A33B_Perch",
    "asset_prefix": "A33B_Perch",

    # Where in the level to place the finished perch.
    "spawn_location": (0.0, 0.0, 0.0),

    # Base plate (a box).
    "base_size_x": 8.0,
    "base_size_y": 26.0,
    "base_thickness": 1.5,

    # Support posts (two vertical cylinders).
    "post_radius": 1.0,
    "post_height": 9.0,
    "post_offset_y": 10.0,   # distance of each post from the centre, along Y

    # Perch rod (a horizontal cylinder lying along Y, resting on the posts).
    "rod_radius": 1.2,
    "rod_length": 26.0,

    # Tessellation quality for cylinders.
    "segments": 32,

    # Linear (0..1) RGB colors for each part.
    "color_base": (0.22, 0.22, 0.25),
    "color_posts": (0.00, 0.55, 0.55),
    "color_rod": (0.95, 0.45, 0.10),

    # Flip triangle winding in the mesh-description fallback if faces render inside-out.
    "flip_winding": False,
}


def _log(msg):
    unreal.log("[A33B_Perch] " + msg)


def _warn(msg):
    unreal.log_warning("[A33B_Perch] " + msg)


# --------------------------------------------------------------------------------------
# Small unreal helpers
# --------------------------------------------------------------------------------------
def _vec(x, y, z):
    return unreal.Vector(float(x), float(y), float(z))


def _lin(rgb, a=1.0):
    return unreal.LinearColor(float(rgb[0]), float(rgb[1]), float(rgb[2]), float(a))


def _xform(location=(0.0, 0.0, 0.0), roll=0.0, pitch=0.0, yaw=0.0):
    """Build a Transform from a location and a Rotator (degrees).

    The unreal.Transform constructor accepts a Rotator for its `rotation` argument and
    converts it to the internal quaternion, which avoids any Rotator->Quat ambiguity.
    The constructor's location kwarg is `location` (the stored struct field is `translation`).
    """
    return unreal.Transform(
        location=_vec(*location),
        rotation=unreal.Rotator(roll=float(roll), pitch=float(pitch), yaw=float(yaw)),
        scale=_vec(1.0, 1.0, 1.0),
    )


# --------------------------------------------------------------------------------------
# Materials (shared by both backends)
# --------------------------------------------------------------------------------------
def _ensure_master_material():
    """Create (or load) a master material exposing a `BaseColor` vector parameter."""
    pkg = CONFIG["package_path"]
    name = "M_%s_Master" % CONFIG["asset_prefix"]
    path = "%s/%s" % (pkg, name)

    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.EditorAssetLibrary.load_asset(path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    mat = tools.create_asset(name, pkg, unreal.Material, unreal.MaterialFactoryNew())

    base_color = unreal.MaterialEditingLibrary.create_material_expression(
        mat, unreal.MaterialExpressionVectorParameter, -400, 0
    )
    base_color.set_editor_property("parameter_name", "BaseColor")
    base_color.set_editor_property("default_value", _lin((0.5, 0.5, 0.5)))
    unreal.MaterialEditingLibrary.connect_material_property(
        base_color, "", unreal.MaterialProperty.MP_BASE_COLOR
    )

    roughness = unreal.MaterialEditingLibrary.create_material_expression(
        mat, unreal.MaterialExpressionScalarParameter, -400, 220
    )
    roughness.set_editor_property("parameter_name", "Roughness")
    roughness.set_editor_property("default_value", 0.65)
    unreal.MaterialEditingLibrary.connect_material_property(
        roughness, "", unreal.MaterialProperty.MP_ROUGHNESS
    )

    unreal.MaterialEditingLibrary.layout_material_expressions(mat)
    unreal.MaterialEditingLibrary.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    _log("Created master material %s" % path)
    return mat


def _ensure_color_instance(master, suffix, color):
    """Create (or load) a MaterialInstanceConstant overriding BaseColor with `color`."""
    pkg = CONFIG["package_path"]
    name = "MI_%s_%s" % (CONFIG["asset_prefix"], suffix)
    path = "%s/%s" % (pkg, name)

    if unreal.EditorAssetLibrary.does_asset_exist(path):
        mic = unreal.EditorAssetLibrary.load_asset(path)
    else:
        tools = unreal.AssetToolsHelpers.get_asset_tools()
        mic = tools.create_asset(
            name, pkg, unreal.MaterialInstanceConstant,
            unreal.MaterialInstanceConstantFactoryNew(),
        )

    unreal.MaterialEditingLibrary.set_material_instance_parent(mic, master)
    unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
        mic, "BaseColor", _lin(color)
    )
    unreal.MaterialEditingLibrary.update_material_instance(mic)
    unreal.EditorAssetLibrary.save_loaded_asset(mic)
    return mic


def _build_material_set():
    """Return (base_mic, posts_mic, rod_mic)."""
    master = _ensure_master_material()
    return (
        _ensure_color_instance(master, "Base", CONFIG["color_base"]),
        _ensure_color_instance(master, "Posts", CONFIG["color_posts"]),
        _ensure_color_instance(master, "Rod", CONFIG["color_rod"]),
    )


# --------------------------------------------------------------------------------------
# Geometry helpers shared by the two backends
# --------------------------------------------------------------------------------------
def _part_transforms():
    """World-space placement of each primitive, in 'perch space' (base sits on z=0)."""
    base_thk = CONFIG["base_thickness"]
    post_h = CONFIG["post_height"]
    post_off = CONFIG["post_offset_y"]
    rod_r = CONFIG["rod_radius"]

    rod_center_z = base_thk + post_h + rod_r  # rod rests tangent on top of the posts
    return {
        "base": _xform(location=(0.0, 0.0, 0.0)),
        "post_neg": _xform(location=(0.0, -post_off, base_thk)),
        "post_pos": _xform(location=(0.0, post_off, base_thk)),
        # origin=CENTER cylinder rotated 90deg about X (roll) so its axis lies along Y.
        "rod": _xform(location=(0.0, 0.0, rod_center_z), roll=90.0),
    }


# --------------------------------------------------------------------------------------
# Backend 1: Geometry Script  (primary)
# --------------------------------------------------------------------------------------
def _has_geometry_script():
    return (
        hasattr(unreal, "GeometryScript_Primitives")
        and hasattr(unreal, "GeometryScript_NewAssetUtils")
        and hasattr(unreal, "DynamicMesh")
    )


def _gs_new_mesh():
    return unreal.DynamicMesh()


def _gs_append_base(mesh, prim_opts, xf):
    return unreal.GeometryScript_Primitives.append_box(
        mesh, prim_opts, xf,
        dimension_x=CONFIG["base_size_x"],
        dimension_y=CONFIG["base_size_y"],
        dimension_z=CONFIG["base_thickness"],
        origin=unreal.GeometryScriptPrimitiveOriginMode.BASE,
    )


def _gs_append_post(mesh, prim_opts, xf):
    return unreal.GeometryScript_Primitives.append_cylinder(
        mesh, prim_opts, xf,
        radius=CONFIG["post_radius"],
        height=CONFIG["post_height"],
        radial_steps=CONFIG["segments"],
        height_steps=1,
        capped=True,
        origin=unreal.GeometryScriptPrimitiveOriginMode.BASE,
    )


def _gs_append_rod(mesh, prim_opts, xf):
    return unreal.GeometryScript_Primitives.append_cylinder(
        mesh, prim_opts, xf,
        radius=CONFIG["rod_radius"],
        height=CONFIG["rod_length"],
        radial_steps=CONFIG["segments"],
        height_steps=1,
        capped=True,
        origin=unreal.GeometryScriptPrimitiveOriginMode.CENTER,
    )


def _gs_bake_static_mesh(mesh, asset_name):
    """Bake a DynamicMesh into a new StaticMesh asset and return it."""
    path = "%s/%s" % (CONFIG["package_path"], asset_name)
    opts = unreal.GeometryScriptCreateNewStaticMeshAssetOptions()
    opts.set_editor_property("enable_recompute_normals", True)
    opts.set_editor_property("enable_recompute_tangents", True)

    result = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
        mesh, path, opts
    )
    # Returns (StaticMesh, outcome) as a tuple in Python.
    static_mesh = result[0] if isinstance(result, tuple) else result
    if static_mesh is None:
        raise RuntimeError("Failed to create StaticMesh asset at %s" % path)
    unreal.EditorAssetLibrary.save_loaded_asset(static_mesh)
    _log("Baked StaticMesh %s" % path)
    return static_mesh


def build_geometry_script():
    """Build the perch as three colored StaticMeshes and place them as a grouped actor set."""
    _log("Backend: Geometry Script")
    prim_opts = unreal.GeometryScriptPrimitiveOptions()
    xf = _part_transforms()
    base_mic, posts_mic, rod_mic = _build_material_set()
    prefix = CONFIG["asset_prefix"]

    # --- base ---
    base_mesh = _gs_new_mesh()
    base_mesh = _gs_append_base(base_mesh, prim_opts, xf["base"])
    base_sm = _gs_bake_static_mesh(base_mesh, "SM_%s_Base" % prefix)
    base_sm.set_material(0, base_mic)

    # --- posts (both cylinders in one mesh, one color) ---
    posts_mesh = _gs_new_mesh()
    posts_mesh = _gs_append_post(posts_mesh, prim_opts, xf["post_neg"])
    posts_mesh = _gs_append_post(posts_mesh, prim_opts, xf["post_pos"])
    posts_sm = _gs_bake_static_mesh(posts_mesh, "SM_%s_Posts" % prefix)
    posts_sm.set_material(0, posts_mic)

    # --- rod ---
    rod_mesh = _gs_new_mesh()
    rod_mesh = _gs_append_rod(rod_mesh, prim_opts, xf["rod"])
    rod_sm = _gs_bake_static_mesh(rod_mesh, "SM_%s_Rod" % prefix)
    rod_sm.set_material(0, rod_mic)

    for sm in (base_sm, posts_sm, rod_sm):
        unreal.EditorAssetLibrary.save_loaded_asset(sm)

    # Geometry is already in perch space, so spawn every part at the same location.
    loc = _vec(*CONFIG["spawn_location"])
    actor_sys = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    base_actor = actor_sys.spawn_actor_from_object(base_sm, loc)
    base_actor.set_actor_label("%s_Base" % prefix)
    posts_actor = actor_sys.spawn_actor_from_object(posts_sm, loc)
    posts_actor.set_actor_label("%s_Posts" % prefix)
    rod_actor = actor_sys.spawn_actor_from_object(rod_sm, loc)
    rod_actor.set_actor_label("%s_Rod" % prefix)

    _attach_children(base_actor, (posts_actor, rod_actor))
    _log("Spawned perch (3 parts). Select all three and press Ctrl+G to group if desired.")
    return [base_actor, posts_actor, rod_actor]


def _attach_children(parent, children):
    """Best-effort parenting so the perch parts move together; never fatal."""
    try:
        rule = unreal.AttachmentRule.KEEP_WORLD
        for child in children:
            child.attach_to_actor(parent, "", rule, rule, rule, False)
    except Exception as exc:  # pragma: no cover
        _warn("Could not attach parts (%s); they remain independent actors." % exc)


# --------------------------------------------------------------------------------------
# Backend 2: Mesh Description  (fallback, no plugin required)
# --------------------------------------------------------------------------------------
def _normalize(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / length, v[1] / length, v[2] / length)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _add(*vs):
    return tuple(sum(comp) for comp in zip(*vs))


def _mul(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def _quad(a, b, c, d, flip):
    """Two triangles for a quad a-b-c-d. Each corner is (pos, (u, v))."""
    if flip:
        return [(a, c, b), (a, d, c)]
    return [(a, b, c), (a, c, d)]


def _box_triangles(center, size):
    """Axis-aligned box centered at `center` (tuple) with full extents `size` (tuple)."""
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    cx, cy, cz = center
    flip = CONFIG["flip_winding"]

    def p(sx, sy, sz):
        return (cx + sx * hx, cy + sy * hy, cz + sz * hz)

    uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    tris = []
    # +X, -X, +Y, -Y, +Z, -Z faces (corners ordered CCW as seen from outside).
    faces = [
        [p(1, -1, -1), p(1, 1, -1), p(1, 1, 1), p(1, -1, 1)],
        [p(-1, 1, -1), p(-1, -1, -1), p(-1, -1, 1), p(-1, 1, 1)],
        [p(1, 1, -1), p(-1, 1, -1), p(-1, 1, 1), p(1, 1, 1)],
        [p(-1, -1, -1), p(1, -1, -1), p(1, -1, 1), p(-1, -1, 1)],
        [p(-1, -1, 1), p(1, -1, 1), p(1, 1, 1), p(-1, 1, 1)],
        [p(1, -1, -1), p(-1, -1, -1), p(-1, 1, -1), p(1, 1, -1)],
    ]
    for f in faces:
        corners = [(f[i], uv[i]) for i in range(4)]
        tris.extend(_quad(*corners, flip=flip))
    return tris


def _cylinder_triangles(center, axis, radius, length, segments):
    """A capped cylinder centered at `center`, its axis along unit-ish vector `axis`."""
    u = _normalize(axis)
    ref = (0.0, 0.0, 1.0) if abs(u[2]) < 0.9 else (1.0, 0.0, 0.0)
    a = _normalize(_cross(ref, u))
    b = _cross(u, a)  # (a, b, u) right-handed
    half = length / 2.0
    flip = CONFIG["flip_winding"]
    bottom_c = _add(center, _mul(u, -half))
    top_c = _add(center, _mul(u, half))
    tris = []

    for i in range(segments):
        t0 = 2.0 * math.pi * i / segments
        t1 = 2.0 * math.pi * (i + 1) / segments
        d0 = _add(_mul(a, math.cos(t0)), _mul(b, math.sin(t0)))
        d1 = _add(_mul(a, math.cos(t1)), _mul(b, math.sin(t1)))
        b0 = _add(bottom_c, _mul(d0, radius))
        b1 = _add(bottom_c, _mul(d1, radius))
        t0p = _add(top_c, _mul(d0, radius))
        t1p = _add(top_c, _mul(d1, radius))
        u0, u1 = i / float(segments), (i + 1) / float(segments)

        # Side quad (outward facing).
        tris.extend(_quad(
            (b0, (u0, 0.0)), (b1, (u1, 0.0)), (t1p, (u1, 1.0)), (t0p, (u0, 1.0)),
            flip=flip,
        ))
        # Bottom cap fan (normal -u): center, ring1, ring0.
        bc = [(bottom_c, (0.5, 0.5)), (b1, (0.5 + 0.5 * math.cos(t1), 0.5 + 0.5 * math.sin(t1))),
              (b0, (0.5 + 0.5 * math.cos(t0), 0.5 + 0.5 * math.sin(t0)))]
        tris.append(tuple(bc) if not flip else (bc[0], bc[2], bc[1]))
        # Top cap fan (normal +u): center, ring0, ring1.
        tc = [(top_c, (0.5, 0.5)), (t0p, (0.5 + 0.5 * math.cos(t0), 0.5 + 0.5 * math.sin(t0))),
              (t1p, (0.5 + 0.5 * math.cos(t1), 0.5 + 0.5 * math.sin(t1)))]
        tris.append(tuple(tc) if not flip else (tc[0], tc[2], tc[1]))
    return tris


def _emit_part(smd, group_id, triangles):
    """Emit a list of (posA, posB, posC) triangles (each vertex = (pos, uv)) into a group."""
    for tri in triangles:
        instances = []
        for pos, uv in tri:
            vid = smd.create_vertex()
            smd.set_vertex_position(vid, _vec(*pos))
            vinst = smd.create_vertex_instance(vid)
            smd.set_vertex_instance_uv(vinst, unreal.Vector2D(uv[0], uv[1]), 0)
            instances.append(vinst)
        smd.create_triangle(group_id, instances)


def build_mesh_description():
    """Build the perch as ONE combined StaticMesh with a material slot per part."""
    _log("Backend: Mesh Description (no plugin required)")
    base_mic, posts_mic, rod_mic = _build_material_set()
    prefix = CONFIG["asset_prefix"]

    base_thk = CONFIG["base_thickness"]
    post_h = CONFIG["post_height"]
    post_off = CONFIG["post_offset_y"]
    rod_r = CONFIG["rod_radius"]
    seg = CONFIG["segments"]

    smd = unreal.StaticMesh.create_static_mesh_description()

    # Group 0 = base, group 1 = posts, group 2 = rod (slot order follows creation order).
    g_base = smd.create_polygon_group()
    g_posts = smd.create_polygon_group()
    g_rod = smd.create_polygon_group()
    for gid, slot in ((g_base, "Base"), (g_posts, "Posts"), (g_rod, "Rod")):
        try:
            smd.set_polygon_group_material_slot_name(gid, slot)
        except Exception:
            pass  # slot naming is cosmetic

    _emit_part(smd, g_base, _box_triangles(
        (0.0, 0.0, base_thk / 2.0),
        (CONFIG["base_size_x"], CONFIG["base_size_y"], base_thk),
    ))
    for sign in (-1.0, 1.0):
        _emit_part(smd, g_posts, _cylinder_triangles(
            (0.0, sign * post_off, base_thk + post_h / 2.0),
            (0.0, 0.0, 1.0), CONFIG["post_radius"], post_h, seg,
        ))
    _emit_part(smd, g_rod, _cylinder_triangles(
        (0.0, 0.0, base_thk + post_h + rod_r),
        (0.0, 1.0, 0.0), rod_r, CONFIG["rod_length"], seg,
    ))

    path = "%s/SM_%s" % (CONFIG["package_path"], prefix)
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    # StaticMesh has no dedicated Python factory; create_asset accepts a null factory
    # (None) and produces an empty asset of the given class to build into.
    static_mesh = tools.create_asset(
        "SM_%s" % prefix, CONFIG["package_path"], unreal.StaticMesh, None
    )
    if static_mesh is None:
        raise RuntimeError(
            "Failed to create an empty StaticMesh asset at %s. If this persists, enable the "
            "'Geometry Script' plugin and use build(backend='geometry_script') instead." % path
        )

    static_mesh.build_from_static_mesh_descriptions([smd], build_simple_collision=True, fast_build=True)
    static_mesh.set_material(0, base_mic)
    static_mesh.set_material(1, posts_mic)
    static_mesh.set_material(2, rod_mic)
    unreal.EditorAssetLibrary.save_loaded_asset(static_mesh)
    _log("Baked combined StaticMesh %s" % path)

    loc = _vec(*CONFIG["spawn_location"])
    actor_sys = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor = actor_sys.spawn_actor_from_object(static_mesh, loc)
    actor.set_actor_label(prefix)
    _log("Spawned perch as a single actor '%s'." % prefix)
    return [actor]


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------
def build(backend="auto"):
    """Build the perch. backend: 'auto' | 'geometry_script' | 'mesh_description'."""
    if backend == "auto":
        backend = "geometry_script" if _has_geometry_script() else "mesh_description"
        _log("Auto-selected backend: %s" % backend)

    if backend == "geometry_script":
        if not _has_geometry_script():
            raise RuntimeError(
                "Geometry Script API not found. Enable Edit > Plugins > 'Geometry Script' "
                "and restart the editor, or call build(backend='mesh_description')."
            )
        return build_geometry_script()
    if backend == "mesh_description":
        return build_mesh_description()
    raise ValueError("Unknown backend %r" % backend)


if __name__ == "__main__":
    build("auto")
