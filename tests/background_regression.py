# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Andrei and Coda contributors
"""Synthetic export regression suite. Run only in a fresh background process.

blender --background --factory-startup --python tests/background_regression.py -- --output results
Or use Python with an existing compatible bpy installation and --bpy-site PATH.
No owned game data, desktop preferences, add-on installation, or network needed.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import traceback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon", type=Path, default=Path(__file__).resolve().parents[1] / "sith")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--bpy-site", type=Path)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    if args.bpy_site:
        sys.path.insert(0, str(args.bpy_site.resolve()))
    sys.dont_write_bytecode = True
    import bpy
    import bmesh
    from mathutils import Vector

    if not bpy.app.background:
        parser.error("Use a fresh background Blender process, never an interactive scene")
    addon_path = args.addon.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    def hashes():
        return {p.relative_to(addon_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(addon_path.rglob("*")) if p.suffix in (".py", ".toml")}

    before_hashes = hashes()
    name = "sith_regression"
    spec = importlib.util.spec_from_file_location(name, addon_path / "__init__.py",
                                                 submodule_search_locations=[str(addon_path)])
    addon = importlib.util.module_from_spec(spec)
    sys.modules[name] = addon
    spec.loader.exec_module(addon)
    addon.register()
    loader = importlib.import_module(name + ".model.model3doLoader")
    utils = importlib.import_module(name + ".model.utils")
    keyloader = importlib.import_module(name + ".key.keyLoader")
    types = importlib.import_module(name + ".model.model3do")
    report = {"schema": "blender-sith-agentic-regression-v1", "blender": bpy.app.version_string,
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "addon_sha256": before_hashes, "tests": [], "scope": "Synthetic serialized 3DO/KEY only; no game runtime claim"}

    def reset():
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)

    def check(label, fn):
        reset()
        try:
            details = fn()
            report["tests"].append({"name": label, "passed": True, "details": details})
        except Exception as exc:
            report["tests"].append({"name": label, "passed": False,
                                    "error": type(exc).__name__ + ": " + str(exc)})
            traceback.print_exc()

    def mesh_object(label, vertices, faces):
        mesh = bpy.data.meshes.new(label)
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(label, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.sith_model3do_hnode_idx = 0
        return obj

    def snapshot(mesh):
        return {"vertices": [tuple(v.co) for v in mesh.vertices],
                "polygons": [tuple(p.vertices) for p in mesh.polygons],
                "corner_normals": [tuple(n.vector) for n in mesh.corner_normals],
                "uv": [tuple(d.uv) for d in mesh.uv_layers.active.data] if mesh.uv_layers.active else []}

    def roundtrip(obj, label, colors=False, absolute=False, version=None):
        filename = output / (label + ".3do")
        addon.export3do(obj, str(filename), version or loader.Model3doFileVersion.Version2_1,
                        absolute, colors, True)
        model, _ = loader.load3do(str(filename))
        return model

    def check_normals(obj, label, expected_count):
        mesh = obj.data
        before = snapshot(mesh)
        model = roundtrip(obj, label)
        exported = model.geosets[0].meshes[0]
        assert len(exported.vertices) == expected_count, (len(exported.vertices), expected_count)
        assert len(exported.faces) == len(mesh.polygons)
        worst = 0.0
        for polygon, face in zip(mesh.polygons, exported.faces):
            assert len(face.vertexIdxs) == len(polygon.loop_indices)
            for loop_index, index in zip(polygon.loop_indices, face.vertexIdxs):
                loop = mesh.loops[loop_index]
                assert (Vector(exported.vertices[index]) - mesh.vertices[loop.vertex_index].co).length < 1e-6
                error = (Vector(exported.normals[index]) - mesh.corner_normals[loop_index].vector).length
                worst = max(worst, error)
        assert worst < 2e-6, f"worst corner-normal error {worst}"
        assert snapshot(mesh) == before, "Export changed source mesh"
        return {"source_vertices": len(mesh.vertices), "exported_vertices": expected_count,
                "checked_corners": len(mesh.loops), "max_normal_error": worst, "source_unchanged": True}

    def split_vertices():
        obj = mesh_object("split", [(0,0,0),(1,0,0),(0,1,0),(0,0,0),(0,0,1),(1,0,0)], [(0,1,2),(3,4,5)])
        return check_normals(obj, "split", 6)

    def cube(style):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.object
        obj.name = "cube_" + style
        obj.sith_model3do_hnode_idx = 0
        for face in obj.data.polygons:
            face.use_smooth = style != "flat"
        if style == "sharp":
            for edge in obj.data.edges:
                edge.use_edge_sharp = True
        obj.data.update()
        return check_normals(obj, "cube_" + style, 8 if style == "smooth" else 24)

    def custom_normals():
        obj = mesh_object("custom", [(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [(0,1,2),(0,2,3)])
        for p in obj.data.polygons:
            p.use_smooth = True
        obj.data.normals_split_custom_set([(0.6,0,0.8)] * 3 + [(0,0.6,0.8)] * 3)
        return check_normals(obj, "custom", 6)

    def mixed_polygons():
        # A pentagon, an isolated triangle, and a triangle whose first two
        # positions coincide with the pentagon. Distinct custom corner normals
        # make a wrong polygon/loop correspondence observable after reparse.
        obj = mesh_object("mixed", [(0,0,0),(2,0,0),(3,1,0),(1,3,0),(-1,1,0),
                                    (5,0,0),(6,0,0),(5,1,0),(0,0,0),(2,0,0),(1,3,0)],
                          [(0,1,2,3,4),(5,6,7),(8,9,10)])
        for p in obj.data.polygons:
            p.use_smooth = True
        normals = [Vector((0.03*(i+1), 0.02*(11-i), 1)).normalized()
                   for i in range(len(obj.data.loops))]
        obj.data.normals_split_custom_set(normals)
        return check_normals(obj, "mixed", 11)

    def data_channels():
        obj = mesh_object("channels", [(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [(0,1,2),(0,2,3)])
        mesh = obj.data
        uv = mesh.uv_layers.new(name="UVMap")
        coords = [(0,0),(0.5,0),(0.5,0.5),(0.25,0.25),(0.75,0.75),(0.25,0.75)]
        for d, co in zip(uv.data, coords):
            d.uv = co
        for i in range(2):
            mat = bpy.data.materials.new("channel%d.mat" % i)
            mat.use_nodes = True
            node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = bpy.data.images.new("channel%d" % i, width=128, height=64)
            mesh.materials.append(mat)
        mesh.polygons[1].material_index = 1
        colors = mesh.color_attributes.new(name="Color", type="BYTE_COLOR", domain="CORNER")
        for i, d in enumerate(colors.data):
            d.color = (0.25,0.25,0.25,1) if i < 3 else (0.75,0.75,0.75,1)
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            utils.bmMeshInit3doLayers(bm)
            for i, face in enumerate(bm.faces):
                utils.bmFaceSetType(face, bm, types.FaceType.DoubleSided)
                utils.bmFaceSetGeometryMode(face, bm, types.GeometryMode.Texture)
                utils.bmFaceSetLightMode(face, bm, types.LightMode.Gouraud)
                utils.bmFaceSetTextureMode(face, bm, types.TextureMode.Perspective)
            bm.to_mesh(mesh)
        finally:
            bm.free()
        before = snapshot(mesh)
        checks = []
        for absolute in (False, True):
            for version in (loader.Model3doFileVersion.Version2_1,):
                label = "channels_%s_%s" % (int(absolute), version.value)
                model = roundtrip(obj, label, colors=True, absolute=absolute, version=version)
                exported = model.geosets[0].meshes[0]
                assert len(exported.vertices) == 6, "Color discontinuities must remain split"
                assert model.materials == ["channel0.mat", "channel1.mat"]
                for p, face in zip(mesh.polygons, exported.faces):
                    assert face.materialIdx == p.material_index
                    assert (int(face.type), int(face.geometryMode), int(face.lightMode), int(face.textureMode)) == (1,4,3,1)
                    for loop_index, index, ui in zip(p.loop_indices, face.vertexIdxs, face.uvIdxs):
                        u, v = coords[loop_index]
                        expected_uv = Vector((u * (128 if absolute else 1), -v * (64 if absolute else 1)))
                        assert (Vector(exported.uvs[ui]) - expected_uv).length < 2e-6
                        # Existing exporter uses BMesh byte-color (sRGB) values.
                        expected_color = mesh.color_attributes["Color"].data[loop_index].color_srgb
                        assert max(abs(a-b) for a,b in zip(exported.vertexColors[index][:3], expected_color[:3])) < 2e-6
                checks.append(label)
        assert snapshot(mesh) == before
        # UV seams alone need separate texture indices, not duplicate geometry.
        plain = roundtrip(obj, "uv_seam", colors=False).geosets[0].meshes[0]
        assert len(plain.vertices) == 4 and len(plain.uvs) == 6
        return {"roundtrips": checks, "uv_seam_vertices": 4, "source_unchanged": True}

    def hierarchy():
        root = bpy.data.objects.new("root", None)
        bpy.context.scene.collection.objects.link(root)
        root.location = (2,3,4)
        obj = mesh_object("parent", [(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)])
        obj.parent = root
        obj.location = (1,2,3)
        child = mesh_object("child", [(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)])
        child.parent = obj
        child.location = (0,1,0)
        child.sith_model3do_hnode_idx = 1
        model = roundtrip(root, "hierarchy")
        assert tuple(model.insertOffset) == (2,3,4)
        nodes = model.meshHierarchy
        assert [(n.idx, n.parentIdx, n.firstChildIdx, n.meshIdx) for n in nodes] == [(0,-1,1,0),(1,0,-1,1)]
        assert tuple(nodes[0].position) == (1,2,3) and tuple(nodes[1].position) == (0,1,0)
        return {"nodes": 2, "meshes": 2, "offset_and_local_positions": "preserved"}

    def metadata():
        scene = bpy.context.scene
        if "sith_key_types" in scene:
            del scene["sith_key_types"]
        default_value = scene.sith_key_types
        attempts = []
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            for value, expected in [("FFFF","FFFF"),("0xFFFF","FFFF"),("0X0001","01"),("0000","00"),
                                    ("0x0","00"),("","FFFF"),("-1","FFFF"),("10000","FFFF"),("invalid","FFFF")]:
                scene.sith_key_types = value
                actual = scene.sith_key_types
                attempts.append({"input": value, "expected": expected, "actual": actual})
        assert not errors.getvalue(), errors.getvalue()
        assert default_value == "FFFF", ("default", default_value)
        assert all(x["actual"] == x["expected"] for x in attempts), attempts
        obj = mesh_object("metadata", [(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)])
        assert obj.sith_model3do_hnode_type == "01"
        obj.sith_model3do_hnode_flags = "0x8000"
        obj.sith_model3do_hnode_type = "0x0008"
        model = roundtrip(obj, "metadata")
        assert int(model.meshHierarchy[0].flags) == 0x8000 and int(model.meshHierarchy[0].type) == 8
        return {"default": default_value, "attempts": attempts, "3do_flags_and_type": "roundtripped"}

    def key_roundtrip():
        scene = bpy.context.scene
        scene.sith_key_types = "0xFFFF"
        scene.frame_start = 0
        scene.frame_end = 10
        scene.render.fps = 30
        obj = mesh_object("animated", [(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)])
        for frame, location in [(0,(0,0,0)), (10,(1,2,3))]:
            obj.location = location
            obj.keyframe_insert(data_path="location", frame=frame)
            obj.rotation_euler = (0,0,0)
            obj.keyframe_insert(data_path="rotation_euler", frame=frame)
        path = output / "animation.key"
        addon.exportKey(obj, scene, str(path))
        key = keyloader.loadKey(str(path))
        assert int(key.nodeTypes) == 0xFFFF
        assert key.numJoints == 1 and len(key.nodes) == 1
        assert key.numFrames == 11 and key.fps == 30
        assert [k.frame for k in key.nodes[0].keyframes] == [0,10]
        assert tuple(key.nodes[0].keyframes[-1].position) == (1,2,3)
        return {"type": "FFFF", "joints": 1, "frames": [0,10], "last_position": [1,2,3]}

    try:
        check("disconnected_split_normals", split_vertices)
        for style in ("flat", "smooth", "sharp"):
            check("cube_" + style, lambda style=style: cube(style))
        check("custom_corner_normals", custom_normals)
        check("mixed_ngon_disconnected_faces", mixed_polygons)
        check("uv_material_color_face_channels", data_channels)
        check("hierarchy_and_offset", hierarchy)
        check("hex_metadata", metadata)
        check("animated_key_roundtrip", key_roundtrip)
    finally:
        report["addon_source_unchanged"] = before_hashes == hashes()
        report["passed"] = sum(t["passed"] for t in report["tests"])
        report["failed"] = len(report["tests"]) - report["passed"]
        report["fixtures_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted(output.iterdir()) if p.suffix in (".3do", ".key")}
        (output / "regression.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("blender", "passed", "failed", "addon_source_unchanged")}, indent=2))
    return 0 if report["failed"] == 0 and report["addon_source_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
