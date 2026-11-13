# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Andrei and Coda contributors
"""Synthetic export regression suite. Run only in a fresh background process.

blender --background --factory-startup --python tests/background_regression.py -- --output results
Or use Python with an existing compatible bpy installation and --bpy-site PATH.
No owned game data, desktop preferences, add-on installation, or network needed.
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import traceback


def main():
    if sys.flags.optimize:
        raise RuntimeError("Regression assertions require Python optimization to be disabled")
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
    from mathutils import Matrix, Vector

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
    exporter = importlib.import_module(name + ".model.model3doExporter")
    importer = importlib.import_module(name + ".model.model3doImporter")
    utils = importlib.import_module(name + ".model.utils")
    keyloader = importlib.import_module(name + ".key.keyLoader")
    keyexporter = importlib.import_module(name + ".key.keyExporter")
    keytypes = importlib.import_module(name + ".key.key")
    keyutils = importlib.import_module(name + ".key.keyUtils")
    types = importlib.import_module(name + ".model.model3do")
    report = {"schema": "blender-sith-agentic-regression-v2", "blender": bpy.app.version_string,
              "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "addon_sha256": before_hashes, "tests": [], "key_targets": ["JKDF2", "MOTS"],
              "scope": "Synthetic serialized 3DO/KEY only; no game runtime claim"}

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
                "uv": [tuple(d.uv) for d in mesh.uv_layers.active.data] if mesh.uv_layers.active else [],
                "colors": [(layer.name, layer.domain, layer.data_type,
                            [tuple(d.color) for d in layer.data]) for layer in mesh.color_attributes],
                "smooth": [p.use_smooth for p in mesh.polygons],
                "sharp": [edge.use_edge_sharp for edge in mesh.edges],
                "materials": [m.name if m else None for m in mesh.materials]}

    def roundtrip(obj, label, colors=False, absolute=False, version=None):
        filename = output / (label + ".3do")
        exporter.export3do(obj, str(filename), version or loader.Model3doFileVersion.Version2_1,
                        absolute, colors, True)
        model, _ = loader.load3do(str(filename))
        return model

    def check_normals(obj, label, expected_count, use_corners=True):
        mesh = obj.data
        before = snapshot(mesh)
        model = roundtrip(obj, label)
        exported = model.geosets[0].meshes[0]
        assert len(exported.vertices) == expected_count, (len(exported.vertices), expected_count)
        assert len(exported.faces) == len(mesh.polygons)
        worst = 0.0
        for polygon, face in zip(mesh.polygons, exported.faces):
            assert len(face.vertexIdxs) == len(polygon.loop_indices)
            assert (Vector(face.normal) - polygon.normal).length < 2e-6
            for loop_index, index in zip(polygon.loop_indices, face.vertexIdxs):
                loop = mesh.loops[loop_index]
                assert (Vector(exported.vertices[index]) - mesh.vertices[loop.vertex_index].co).length < 1e-6
                expected = (mesh.corner_normals[loop_index].vector if use_corners
                            else mesh.vertices[loop.vertex_index].normal)
                error = (Vector(exported.normals[index]) - expected).length
                worst = max(worst, error)
        assert worst < 2e-6, f"worst corner-normal error {worst}"
        assert snapshot(mesh) == before, "Export changed source mesh"
        return {"source_vertices": len(mesh.vertices), "exported_vertices": expected_count,
                "checked_corners": len(mesh.loops), "max_normal_error": worst,
                "normal_contract": "authored corners" if use_corners else "geometric vertices and face normals",
                "source_unchanged": True}

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
        # Native faces carry their own flat normal. Explicit sharp/custom normals
        # split vertices; Blender's ordinary flat-shading flag alone does not.
        return check_normals(obj, "cube_" + style, 24 if style == "sharp" else 8,
                             use_corners=style == "sharp")

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
                        # Native light values are linear, including BYTE_COLOR's
                        # RNA conversion; exporting its sRGB bytes is a regression.
                        expected_color = mesh.color_attributes["Color"].data[loop_index].color
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

    def radius_empty(label, parent=None, index=0):
        obj = bpy.data.objects.new(label, None)
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = parent
        obj.sith_model3do_hnode_idx = index
        return obj

    def radius_triangle(label, parent=None, index=0):
        obj = mesh_object(label, [(0,0,0), (1,0,0), (0,1,0)], [(0,1,2)])
        obj.parent = parent
        obj.sith_model3do_hnode_idx = index
        return obj

    def radius_result(obj, label, expected):
        # Expectations below are hand-derived in exported model coordinates,
        # independent of the radius implementation and Blender's world matrix.
        before = {o.name: snapshot(o.data) for o in bpy.context.scene.objects if o.type == 'MESH'}
        bpy.context.view_layer.update()
        model = roundtrip(obj, label)
        assert abs(model.radius - expected) < 2e-6, (model.radius, expected)
        assert before == {o.name: snapshot(o.data) for o in bpy.context.scene.objects if o.type == 'MESH'}
        return model, {"radius": model.radius, "expected": expected, "source_unchanged": True}

    def rejected_model(obj, label, message):
        """Unsupported transforms must fail before replacing a saved resource."""
        path = output / (label + '.3do')
        original = b'Existing resource must survive validation failure\n'
        path.write_bytes(original)
        before = {o.name: snapshot(o.data) for o in bpy.context.scene.objects if o.type == 'MESH'}
        try:
            roundtrip(obj, label)
        except ValueError as exc:
            assert message in str(exc), str(exc)
        else:
            raise AssertionError('Unsupported model transform was accepted')
        assert path.read_bytes() == original
        assert before == {o.name: snapshot(o.data) for o in bpy.context.scene.objects if o.type == 'MESH'}

    def model_radius_origin():
        # An off-origin triangle needs radius 9, not half its bounding diagonal.
        obj = mesh_object('rad_origin', [(8,0,0),(9,0,0),(8,1,0)], [(0,1,2)])
        model, details = radius_result(obj, 'radius_origin', 9.0)
        assert len(model.geosets[0].meshes[0].vertices) == 3
        assert model.geosets[0].meshes[0].radius == 9.0
        # Upstream preserves loose vertices, so they must contribute to both
        # automatic bounds. Scene export represents geoset 0 only.
        loose = mesh_object('rad_origin_loose', [(8,0,0),(9,0,0),(8,1,0),(100,0,0)], [(0,1,2)])
        loose_model, _ = radius_result(loose, 'radius_origin_loose', 100.0)
        assert len(loose_model.geosets[0].meshes[0].vertices) == 4
        assert loose_model.geosets[0].meshes[0].radius == 100.0
        details['preserved_loose_vertex_radius'] = 100.0
        details['scene_geosets'] = len(loose_model.geosets)
        assert details['scene_geosets'] == 1
        return details

    def model_radius_hierarchy():
        root = radius_empty('rad_container')
        root.location = (100,-200,300)
        root.rotation_euler.z = 0.7
        parent = radius_empty('rad_parent', root)
        parent.location = (3,4,0)
        parent.rotation_euler.z = 1.5707963267948966
        child = radius_triangle('rad_child', parent, 1)
        child.location = (2,0,0)
        rejected_model(root, 'radius_wrapper_rotation', 'Apply model wrapper rotation')
        root.rotation_euler.z = 0.0
        # Exported points: (3,6,0), (3,7,0), (2,6,0).
        model, details = radius_result(root, 'radius_hierarchy', 58.0 ** 0.5)
        assert tuple(model.insertOffset) == (100,-200,300), 'Insert-offset semantics changed'
        assert tuple(model.meshHierarchy[0].position) == (3,4,0)
        return details

    def model_radius_nested_scale():
        root = radius_empty('rad_scaled_root')
        root.scale = (2,3,4)
        parent = radius_empty('rad_scaled_parent', root)
        parent.location = (1,0,0)
        parent.scale = (-2,0.5,1)
        parent.rotation_euler.z = 1.5707963267948966
        child = mesh_object('rad_scaled_child', [(0,0,0),(1,0,0),(0,1,1)], [(0,1,2)])
        child.parent = parent
        child.location = (0,1,0)
        child.scale = (1,2,0.5)
        child.sith_model3do_hnode_idx = 1
        # The old fixture silently lost shear. Reject that noncommuting scale,
        # then test supported reflected/nonuniform scale with equal X/Y parent
        # factors around the Z rotation: points (1,0,0), (1,-4,0), (-1,0,2).
        rejected_model(root, 'radius_noncommuting_scale', 'Apply nonuniform parent scale')
        root.scale = (2,2,4)
        model, details = radius_result(root, 'radius_nested_scale', 17.0 ** 0.5)
        assert tuple(model.meshHierarchy[0].position) == (2,0,0)
        assert tuple(model.meshHierarchy[1].position) == (0,1,0)
        details['noncommuting_scale_rejected_without_overwrite'] = True
        return details

    def model_radius_scaled_siblings():
        root = radius_empty('rad_siblings')
        joint = radius_empty('rad_siblings_joint', root)
        left = radius_triangle('rad_left', joint, 1)
        left.scale = (2,1,1)
        right = radius_triangle('rad_right', joint, 2)
        right.location = (10,0,0)
        right.scale = (3,1,1)
        # Siblings under one native root: rightmost vertex is (13,0,0),
        # regardless of the left sibling's scale. Multiple roots are invalid.
        model, details = radius_result(root, 'radius_scaled_siblings', 13.0)
        assert [n.parentIdx for n in model.meshHierarchy] == [-1,0,0]
        assert [m.radius for m in model.geosets[0].meshes] == [2.0,3.0]
        return details

    def model_radius_pivot():
        root = radius_empty('rad_pivot_container')
        parent = radius_triangle('rad_pivot_parent', root)
        parent.location = (3,0,0)
        parent.rotation_euler.z = 1.5707963267948966
        pivot = parent.constraints.new('PIVOT')
        pivot.offset = (-2,0,0)
        pivot.rotation_range = 'ALWAYS_ACTIVE'
        pivot.use_relative_location = True
        child = radius_triangle('rad_pivot_child', parent, 1)
        child.location = (4,0,0)
        # Parent points (1,2), (1,3), (0,2); child points (1,4), (1,5), (0,4).
        # A node pivot shifts its own mesh, not its descendants' joint frames.
        model, details = radius_result(root, 'radius_pivot', 26.0 ** 0.5)
        assert tuple(model.meshHierarchy[0].pivot) == (2,0,0)
        assert tuple(model.meshHierarchy[0].position) == (1,0,0)
        return details

    def model_radius_empty_geometry():
        root = radius_empty('rad_empty')
        empty = mesh_object('rad_empty_mesh', [], [])
        empty.parent = root
        helper = radius_triangle('MESH_RADIUS_unused', root, 1)
        model, details = radius_result(root, 'radius_empty_geometry', 0.0)
        assert len(model.meshHierarchy) == 1
        assert not model.geosets[0].meshes[0].vertices
        # A loose point is exported geometry, unlike an empty mesh or helper.
        loose = mesh_object('rad_loose', [(900,0,0)], [])
        loose_model, _ = radius_result(loose, 'radius_loose_only', 900.0)
        assert [tuple(v) for v in loose_model.geosets[0].meshes[0].vertices] == [(900,0,0)]
        details['loose_only_radius'] = 900.0
        return details

    def model_radius_override():
        root = radius_empty('rad_manual')
        radius_triangle('rad_manual_mesh', root)
        helper = mesh_object('MODEL_RADIUS_rad_manual', [(-10,0,0),(10,0,0),(0,1,0)], [(0,1,2)])
        helper.parent = root
        model, details = radius_result(root, 'radius_override', 10.0)
        assert len(model.meshHierarchy) == 1
        assert model.geosets[0].meshes[0].radius == 1.0
        return details

    def pivot_fixture(label, empty_parent=False):
        root = radius_empty(label + '_base')
        root.location = (100,-200,300)
        parent = (radius_empty if empty_parent else radius_triangle)(label + '_parent', root)
        parent.location = (3,4,0)
        parent.rotation_euler.z = 1.5707963267948966
        child = radius_triangle(label + '_child', parent, 1)
        child.location = (2,0,0)
        source = roundtrip(root, label + '_base')
        source.meshHierarchy[0].pivot = utils.Vector3f(2,0,0)
        source.meshHierarchy[1].pivot = utils.Vector3f(0,3,0)
        path = output / (label + '_input.3do')
        writer = importlib.import_module(name + '.model.model3doWriter')
        writer.save3do(source, str(path), loader.Model3doFileVersion.Version2_1, 'Synthetic native pivot fixture')
        return source, path

    def import_pivot_fixture(path):
        return importer.import3do(str(path), mat_dirs=[], uvAbsolute_2_1=False,
                               importVertexColors=False, importRadiusObj=False,
                               preserveOrder=True, clearScene=True)

    def pivot_import_roundtrip(empty_parent=False):
        label = 'pivot_empty' if empty_parent else 'pivot_mesh'
        source, path = pivot_fixture(label, empty_parent)
        for cycle in range(2):
            root = import_pivot_fixture(path)
            bpy.context.view_layer.update()
            actual = roundtrip(root, label + '_' + str(cycle))
            nodes = actual.meshHierarchy
            assert [tuple(n.pivot) for n in nodes] == [(2,0,0),(0,3,0)], 'Native attachment pivots were lost'
            assert [tuple(n.position) for n in nodes] == [(3,4,0),(2,0,0)]
            assert tuple(actual.insertOffset) == (100,-200,300)
            for before, after in zip(source.geosets[0].meshes, actual.geosets[0].meshes):
                assert len(before.vertices) == len(after.vertices)
                assert max((Vector(a)-Vector(b)).length for a,b in zip(before.vertices,after.vertices)) < 2e-6
            # Native right-angle fixture: attachment at parent T(3,4)*Rz(90)
            # applied to child position(2,0) + pivot(0,3), independent of parent pivot.
            p, c = nodes
            attachment = (p.position[0] - c.position[1] - c.pivot[1],
                          p.position[1] + c.position[0] + c.pivot[0], 0)
            assert attachment == (0,6,0), attachment
            path = output / (label + '_' + str(cycle) + '.3do')
        return {'cycles': 2, 'empty_parent': empty_parent, 'pivots': [list(n.pivot) for n in nodes],
                'attachment': list(attachment), 'native_vertices_unchanged': True}

    def pivot_import_scaled():
        source, path = pivot_fixture('pivot_scaled')
        root = import_pivot_fixture(path)
        parent = root.children[0]
        child = parent.children[0]
        parent.scale = (2,3,4)
        child.scale = (-1,0.5,2)
        bpy.context.view_layer.update()
        actual = roundtrip(root, 'pivot_scaled_result')
        nodes = actual.meshHierarchy
        assert [tuple(n.pivot) for n in nodes] == [(4,0,0),(0,4.5,0)]
        assert [tuple(n.position) for n in nodes] == [(3,4,0),(4,0,0)]
        assert [tuple(v) for v in actual.geosets[0].meshes[0].vertices] == [(0,0,0),(2,0,0),(0,3,0)]
        # Reflected scale reverses native winding and can reorder vertex records.
        assert sorted(tuple(v) for v in actual.geosets[0].meshes[1].vertices) == sorted([(0,0,0),(-2,0,0),(0,1.5,0)])
        return {'pivots': [list(n.pivot) for n in nodes], 'scaled_vertices': 'baked exactly once'}

    def pivot_import_constraint_precedence():
        source, path = pivot_fixture('pivot_explicit')
        root = import_pivot_fixture(path)
        parent = root.children[0]
        constraint = parent.constraints.new('PIVOT')
        constraint.offset = (-5,0,0)
        constraint.rotation_range = 'ALWAYS_ACTIVE'
        constraint.use_relative_location = True
        bpy.context.view_layer.update()
        actual = roundtrip(root, 'pivot_explicit_result')
        assert tuple(actual.meshHierarchy[0].pivot) == (5,0,0)
        assert tuple(actual.meshHierarchy[0].position) == (-2,4,0)
        assert tuple(actual.geosets[0].meshes[0].vertices[0]) == (2,0,0), 'Explicit constraint must retain existing baked geometry'
        constraint.influence = 0
        bpy.context.view_layer.update()
        restored = roundtrip(root, 'pivot_explicit_disabled')
        assert tuple(restored.meshHierarchy[0].pivot) == (2,0,0)
        assert tuple(restored.meshHierarchy[0].position) == (3,4,0)
        assert tuple(restored.geosets[0].meshes[0].vertices[0]) == (0,0,0)
        constraint.influence = 1
        constraint.offset = (0,0,0)
        zero = roundtrip(root, 'pivot_explicit_zero')
        assert tuple(zero.meshHierarchy[0].pivot) == (0,0,0), 'Active zero pivot must still override imported metadata'
        assert tuple(zero.geosets[0].meshes[0].vertices[0]) == (2,0,0)
        constraint.mute = True
        muted = roundtrip(root, 'pivot_explicit_muted')
        assert tuple(muted.meshHierarchy[0].pivot) == (2,0,0)
        assert tuple(muted.geosets[0].meshes[0].vertices[0]) == (0,0,0)
        return {'active_constraint_precedence': True, 'active_zero_precedence': True,
                'disabled_and_muted_constraints_preserve_import': True}

    def pivot_import_mesh_radius():
        source, path = pivot_fixture('pivot_bounds')
        source.meshHierarchy[0].pivot = utils.Vector3f(-9,0,0)
        source.geosets[0].meshes[0].vertices = [utils.Vector3f(10,0,0), utils.Vector3f(11,0,0), utils.Vector3f(10,1,0)]
        writer = importlib.import_module(name + '.model.model3doWriter')
        writer.save3do(source, str(path), loader.Model3doFileVersion.Version2_1, 'Pivot toward local origin')
        root = import_pivot_fixture(path)
        parent = root.children[0]
        parent.scale = (2,3,4)
        before = snapshot(parent.data)
        bpy.context.view_layer.update()
        actual = roundtrip(root, 'pivot_bounds_result')
        mesh = actual.geosets[0].meshes[0]
        assert mesh.radius == 22.0, (mesh.radius, 'Native mesh vertices reach 22 after pivot compensation')
        assert tuple(actual.meshHierarchy[0].pivot) == (-18,0,0)
        assert snapshot(parent.data) == before, 'Export must not change the imported mesh'
        # Parenting owns the helper; its local radius 25 receives parent's X scale2.
        helper = mesh_object('MESH_RADIUS_' + utils.stripOrderPrefix(parent.name), [(-25,0,0),(25,0,0),(0,1,0)], [(0,1,2)])
        helper.parent = parent
        bpy.context.view_layer.update()
        manual = roundtrip(root, 'pivot_bounds_override')
        assert manual.geosets[0].meshes[0].radius == 50.0
        return {'automatic_native_mesh_radius': mesh.radius, 'manual_radius': 50.0, 'source_mesh_unchanged': True}

    def pivot_import_saved_blend_key():
        source, path = pivot_fixture('pivot_saved')
        root = import_pivot_fixture(path)
        root_name = root.name
        blend_path = output / 'pivot_saved.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
        bpy.ops.wm.open_mainfile(filepath=str(blend_path), load_ui=False, use_scripts=False)
        root = bpy.context.scene.objects[root_name]
        actual = roundtrip(root, 'pivot_saved_result')
        assert [tuple(n.pivot) for n in actual.meshHierarchy] == [(2,0,0),(0,3,0)]
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end, scene.render.fps = 0, 10, 30
        scene.sith_key_types = 'FFFF'
        parent = root.children[0]
        child = parent.children[0]
        for obj, start in ((parent,(3,4,0)), (child,(2,0,0))):
            for frame, delta in ((0,0), (10,1)):
                obj.location = (start[0]+delta,start[1]+delta,start[2]+delta)
                obj.keyframe_insert(data_path='location', frame=frame)
        key_path = output / 'pivot_locations.key'
        keyexporter.exportKey(root, scene, str(key_path), target='JKDF2')
        key = keyloader.loadKey(str(key_path))
        assert len(key.nodes) == 2
        positions = [[tuple(n.keyframes[0].position), tuple(n.keyframes[-1].position)] for n in key.nodes]
        assert positions == [[(3,4,0),(4,5,1)],[(2,0,0),(3,1,1)]], positions
        playback = [assert_key_playback(obj, node, [i / 4 for i in range(41)])
                    for obj, node in zip((parent, child), key.nodes)]
        return {'saved_blend_pivots_preserved': True, 'key_endpoint_locations': positions,
                'playback': playback, 'pivot_not_subtracted_from_key': True}

    def metadata():
        scene = bpy.context.scene
        if "sith_key_types" in scene:
            del scene["sith_key_types"]
        default_value = scene.sith_key_types
        attempts = []
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            for value, expected in [("FFFF","FFFF"),("0xFFFF","FFFF"),("0X0001","01"),("0000","00"),
                                    ("0x0","00"),("","FFFF"),("-1","FFFF"),
                                    ("10000","010000"),("invalid","010000"),
                                    ("0xFFFFFFFF","FFFFFFFF"),("100000000","0100000000"),
                                    ("-1","0100000000"),("","FFFF")]:
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

    def key_at(node, frame):
        """Evaluate native flags and per-frame slopes at actual KEY timestamps."""
        entries = node.keyframes
        times = [entry.frame for entry in entries]
        assert times and times[0] == 0 and all(a < b for a, b in zip(times, times[1:]))
        entry = entries[max(0, bisect.bisect_right(times, frame) - 1)]
        elapsed = frame - entry.frame
        position = list(entry.position)
        rotation = list(entry.orientation)
        if entry.flags & keytypes.KeyframeFlag.PositionChange:
            position = [v + d * elapsed for v, d in zip(position, entry.deltaPosition)]
        if entry.flags & keytypes.KeyframeFlag.OrientationChange:
            rotation = [v + d * elapsed for v, d in zip(rotation, entry.deltaRotation)]
        return position, rotation

    def assert_key_playback(obj, node, times):
        """Compare serialized playback to Blender's local authored channels.

        This uses no exporter conversion/sampling helper as an oracle. Native PYR
        is Rz(yaw) Rx(pitch) Ry(roll); equivalent Euler branches are acceptable.
        """
        scene = bpy.context.scene
        saved_frame, saved_subframe = scene.frame_current, scene.frame_subframe
        worst_position = worst_rotation = 0.0
        try:
            for time in times:
                frame = math.floor(time)
                scene.frame_set(frame, subframe=time - frame)
                position, pyr = key_at(node, time)
                worst_position = max(worst_position, *(abs(a - b) for a, b in zip(position, obj.location)))
                pitch, yaw, roll = map(math.radians, pyr)
                actual = Matrix.Rotation(yaw, 3, 'Z') @ Matrix.Rotation(pitch, 3, 'X') @ Matrix.Rotation(roll, 3, 'Y')
                expected = (obj.rotation_quaternion.normalized().to_matrix() if obj.rotation_mode == 'QUATERNION'
                            else obj.rotation_euler.to_matrix())
                error = max(abs(actual[row][column] - expected[row][column]) for row in range(3) for column in range(3))
                worst_rotation = max(worst_rotation, error)
                assert worst_position < 2e-5, (time, position, tuple(obj.location), worst_position)
                assert worst_rotation < 0.0012, (time, pyr, worst_rotation)
        finally:
            scene.frame_set(saved_frame, subframe=saved_subframe)
        return {'samples': len(times), 'exported_entries': len(node.keyframes),
                'max_position_error': worst_position, 'max_rotation_matrix_error': worst_rotation}

    def linear_channels(obj):
        for curve in keyutils.getActionFCurves(obj):
            for point in curve.keyframe_points:
                point.interpolation = 'LINEAR'
            curve.update()

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
        # Leave Blender's Bezier interpolation in place: adaptive fractional
        # samples must reproduce its curve, not an assumed two-entry layout.
        results = []
        for target in ('JKDF2', 'MOTS'):
            path = output / ('animation_' + target.lower() + '.key')
            keyexporter.exportKey(obj, scene, str(path), target=target)
            key = keyloader.loadKey(str(path))
            assert int(key.nodeTypes) == 0xFFFF
            assert key.numJoints == 1 and len(key.nodes) == 1
            assert key.numFrames == 11 and key.fps == 30
            assert key.nodes[0].keyframes[0].frame == 0 and key.nodes[0].keyframes[-1].frame == 10
            assert tuple(key.nodes[0].keyframes[-1].position) == (1,2,3)
            results.append({'target': target, **assert_key_playback(obj, key.nodes[0], [i / 8 for i in range(81)])})
        return {"type": "FFFF", "joints": 1, "endpoint_frames": [0,10],
                "last_position": [1,2,3], 'playback': results}

    def key_zero_coordinates():
        """A nonzero coordinate returning to zero must not retain the prior value."""
        from mathutils import Quaternion
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end, scene.render.fps = 0, 2, 30
        scene.sith_key_types = 'FFFF'
        results = []
        for mode in ('YXZ', 'QUATERNION'):
            obj = mesh_object('zero_' + mode.lower(), [(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)])
            obj.rotation_mode = mode
            for frame, value in ((0,0.0), (1,1.0), (2,0.0)):
                obj.location = (value, value, value)
                obj.keyframe_insert(data_path='location', frame=frame)
                if mode == 'QUATERNION':
                    obj.rotation_quaternion = Quaternion((1.0,0.0,0.0), value)
                    obj.keyframe_insert(data_path='rotation_quaternion', frame=frame)
                else:
                    obj.rotation_euler = (value,0.0,0.0)
                    obj.keyframe_insert(data_path='rotation_euler', frame=frame)
            path = output / ('key_zero_' + mode.lower() + '.key')
            keyexporter.exportKey(obj, scene, str(path), target='JKDF2')
            key = keyloader.loadKey(path)
            last = key.nodes[0].keyframes[-1]
            assert max(abs(v) for v in last.position) < 1e-7, tuple(last.position)
            assert max(abs(v) for v in last.orientation) < 1e-5, tuple(last.orientation)
            assert tuple(key_at(key.nodes[0], 1)[0]) == (1.0,1.0,1.0)
            return_points = [key_at(key.nodes[0], time)[0][0] for time in (1,1.25,1.5,1.75,2)]
            assert all(a > b for a, b in zip(return_points, return_points[1:])), return_points
            playback = assert_key_playback(obj, key.nodes[0], [i / 16 for i in range(33)])
            results.append({'rotation_mode':mode,'final_position':list(last.position),
                            'final_pyr':list(last.orientation), 'playback':playback})
        return {'returns_to_zero':results}

    def key_quaternion_turns():
        """Quaternion-to-Euler wrap must not turn a short native interval backwards."""
        import math
        from mathutils import Quaternion
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end, scene.render.fps = 0, 36, 30
        scene.sith_key_types = 'FFFF'
        results = []
        # Native PYR axes correspond to Blender X, Z, Y respectively.
        for axis, blender_axis in enumerate(((1,0,0),(0,0,1),(0,1,0))):
            obj = mesh_object('turn_' + str(axis), [(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)])
            obj.rotation_mode = 'QUATERNION'
            for frame in range(37):
                obj.location = (0.0,0.0,0.0)
                obj.keyframe_insert(data_path='location', frame=frame)
                obj.rotation_quaternion = Quaternion(blender_axis, math.radians(frame * 20.0))
                obj.keyframe_insert(data_path='rotation_quaternion', frame=frame)
            path = output / ('key_turn_' + str(axis) + '.key')
            linear_channels(obj)
            keyexporter.exportKey(obj, scene, str(path), target='JKDF2')
            key = keyloader.loadKey(path)
            frames = key.nodes[0].keyframes
            # Quaternion component interpolation is not constant angular speed.
            # Check actual authored motion densely and around each adaptive KEY
            # interval; do not identify an entry's array index with its frame.
            times = {i / 4 for i in range(145)}
            for first, last in zip(frames, frames[1:]):
                times.update(first.frame + (last.frame-first.frame)*f for f in (0.25,0.5,0.75))
            playback = assert_key_playback(obj, key.nodes[0], sorted(times))
            # At authored integer frames, all three axes must still travel the
            # intended two full turns, not a backward long interval or a hold.
            for frame in range(37):
                _, rotation = key_at(key.nodes[0], frame)
                angle = math.radians(frame * 20.0)
                desired = Matrix.Rotation(angle, 3, 'XZY'[axis])
                pitch, yaw, roll = map(math.radians, rotation)
                actual = Matrix.Rotation(yaw, 3, 'Z') @ Matrix.Rotation(pitch, 3, 'X') @ Matrix.Rotation(roll, 3, 'Y')
                assert max(abs(actual[r][c] - desired[r][c]) for r in range(3) for c in range(3)) < 0.0012
            results.append({'axis_pyr':axis,'authored_degrees':720,'playback':playback})
        return {'continuous_turns':results}

    def key_explicit_euler_winding():
        """Explicit Euler winding remains authored even across a sparse full turn."""
        import math
        scene = bpy.context.scene
        scene.frame_start, scene.frame_end, scene.render.fps = 0, 1, 30
        scene.sith_key_types = 'FFFF'
        obj = mesh_object('euler_winding', [(0,0,0),(1,0,0),(0,1,0)], [(0,1,2)])
        obj.rotation_mode = 'YXZ'
        for frame, degrees in ((0,0.0),(1,720.0)):
            obj.location = (0.0,0.0,0.0)
            obj.keyframe_insert(data_path='location', frame=frame)
            obj.rotation_euler = (0.0,0.0,math.radians(degrees))
            obj.keyframe_insert(data_path='rotation_euler', frame=frame)
        path = output / 'key_euler_winding.key'
        linear_channels(obj)
        keyexporter.exportKey(obj, scene, str(path), target='MOTS')
        key = keyloader.loadKey(path)
        frames = key.nodes[0].keyframes
        assert abs(frames[-1].orientation[1]-720.0) < 0.001
        worst = 0.0
        for time in (0,0.125,0.25,0.5,0.75,0.875,1):
            _, rotation = key_at(key.nodes[0], time)
            worst = max(worst, abs(rotation[1] - 720.0 * time))
        assert worst < 0.001
        return {'final_yaw':frames[-1].orientation[1], 'max_unwrapped_yaw_error':worst,
                'playback':assert_key_playback(obj, key.nodes[0], [i/8 for i in range(9)])}

    def material_name_delimiters():
        # Whitespace-based consumers need index and filename as separate fields,
        # including names that fill or exceed the writer's alignment width.
        writer = importlib.import_module(name + ".model.model3doWriter")
        materials = ["short.mat", "a" * 11 + ".mat", "long_material_name_for_export.mat"]
        model = types.Model3do("material_names")
        model.materials = materials
        stream = io.StringIO()
        writer._write_section_resources(stream, model)
        rows = [line.split() for line in stream.getvalue().splitlines()
                if line.strip() and line.strip()[0].isdigit()]
        assert rows == [[str(i) + ":", mat] for i, mat in enumerate(materials)], rows
        path = output / "material_names.3do"
        writer.save3do(model, path, loader.Model3doFileVersion.Version2_1, "Synthetic material delimiter check")
        loaded, _ = loader.load3do(path)
        assert loaded.materials == materials
        return {"material_lengths": [len(mat) for mat in materials], "names_preserved": True}

    def indexed_mat_cels(with_palette):
        matmod = importlib.import_module(name + ".material.mat")
        cmpmod = importlib.import_module(name + ".material.cmp")
        # Two differently sized cels, each with two mip levels. The absent-CMP
        # path must still consume pixel bytes before reading the next header.
        ci = matmod.ColorFormat(matmod.ColorMode.Indexed, 8, *([0] * 12))
        data = matmod.mh_serf.pack(b"MAT ", 0x32, 2, 2, 2) + matmod.cf_serf.pack(*ci)
        data += b"".join(matmod.mtr_serf.pack(8, 0, 0, 0, 0, 0, 0, 0, 0, i) for i in range(2))
        data += matmod.mmm_serf.pack(4, 4, 1, 0, 0, 2) + bytes([0, 1] * 8) + bytes([1] * 4)
        data += matmod.mmm_serf.pack(2, 2, 0, 0, 0, 2) + bytes([2] * 4) + bytes([2])
        palette = None
        if with_palette:
            palette = cmpmod.ColorMap()
            # Match ColorMap.load's uint8 channel representation.
            palette.palette = [cmpmod.CmpPaletteRGB(*(matmod.np.uint8(i) for _ in range(3)))
                               for i in range(256)]
        stream = io.BytesIO(data)
        header = matmod._read_header(stream)
        matmod._read_records(stream, header)
        cels = [matmod._read_mipmap(stream, ci, palette) for _ in range(2)]
        assert [(cel.width, cel.height) for cel in cels] == [(4, 4), (2, 2)]
        assert stream.tell() == len(data), (stream.tell(), len(data))
        assert [cel.transparent for cel in cels] == [True, False]
        if with_palette:
            assert [len(level) for level in cels[0].pixel_data_array] == [64, 16]
            assert set(cels[0].pixel_data_array[0][3::4]) == {0.0, 1.0}
            assert set(cels[1].pixel_data_array[0][3::4]) == {1.0}
        else:
            assert all(cel.pixel_data_array is None for cel in cels)
            truncated = io.BytesIO(data[:-1])
            short_header = matmod._read_header(truncated)
            matmod._read_records(truncated, short_header)
            matmod._read_mipmap(truncated, ci)
            try:
                matmod._read_mipmap(truncated, ci)
            except ImportError as exc:
                assert 'Truncated MAT data' in str(exc), str(exc)
            else:
                raise AssertionError("Truncated undecoded mipmap was accepted")
        path = output / ("indexed_palette.mat" if with_palette else "indexed_no_palette.mat")
        path.write_bytes(data)
        material = matmod.importMat(path, palette)
        texture = material.node_tree.nodes["SithTexImage"]
        assert tuple(texture.image.size) == (4, 4)
        assert tuple(bpy.data.images[matmod._get_tex_name(1, material.name)].size) == (2, 2)
        bsdf = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
        assert bsdf.inputs["Alpha"].is_linked
        assert material.surface_render_method == "BLENDED"
        return {"palette_supplied": with_palette, "cel_sizes": [[4, 4], [2, 2]],
                "mip_levels": 2, "transparency_preserved": True}

    try:
        check("disconnected_split_normals", split_vertices)
        for style in ("flat", "smooth", "sharp"):
            check("cube_" + style, lambda style=style: cube(style))
        check("custom_corner_normals", custom_normals)
        check("mixed_ngon_disconnected_faces", mixed_polygons)
        check("uv_material_color_face_channels", data_channels)
        check("hierarchy_and_offset", hierarchy)
        check("model_radius_origin", model_radius_origin)
        check("model_radius_hierarchy", model_radius_hierarchy)
        check("model_radius_nested_scale", model_radius_nested_scale)
        check("model_radius_scaled_siblings", model_radius_scaled_siblings)
        check("model_radius_pivot", model_radius_pivot)
        check("model_radius_empty_geometry", model_radius_empty_geometry)
        check("model_radius_override", model_radius_override)
        check("pivot_import_mesh_roundtrip", pivot_import_roundtrip)
        check("pivot_import_empty_roundtrip", lambda: pivot_import_roundtrip(True))
        check("pivot_import_scaled", pivot_import_scaled)
        check("pivot_import_constraint_precedence", pivot_import_constraint_precedence)
        check("pivot_import_mesh_radius", pivot_import_mesh_radius)
        check("pivot_import_saved_blend_key", pivot_import_saved_blend_key)
        check("hex_metadata", metadata)
        check("animated_key_roundtrip", key_roundtrip)
        check("key_zero_coordinates", key_zero_coordinates)
        check("key_quaternion_turns", key_quaternion_turns)
        check("key_explicit_euler_winding", key_explicit_euler_winding)
        check("material_name_delimiters", material_name_delimiters)
        check("indexed_mat_cels_with_palette", lambda: indexed_mat_cels(True))
        check("indexed_mat_cels_without_palette", lambda: indexed_mat_cels(False))
    finally:
        report["addon_source_unchanged"] = before_hashes == hashes()
        report["passed"] = sum(t["passed"] for t in report["tests"])
        report["failed"] = len(report["tests"]) - report["passed"]
        report["fixtures_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted(output.iterdir()) if p.suffix in (".3do", ".key", ".mat")}
        (output / "regression.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("blender", "passed", "failed", "addon_source_unchanged")}, indent=2))
    return 0 if report["failed"] == 0 and report["addon_source_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
