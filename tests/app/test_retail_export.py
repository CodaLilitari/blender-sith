# SPDX-License-Identifier: GPL-3.0-or-later
"""Retail export options in real Blender: budget, detail levels, MAT editions and dithering."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import mock

import bpy
import numpy as np

from app.support import SceneTestCase
from core.test_mat_writer import decode, expected_pixels
from fixtures import cmp_bytes
from sith import app
from sith.material import ColorMap, exportMat
from sith.material.mat import ColorMode, kMatCelImagePrefix
from sith.material.matExporter import kMatDitherMaskPrefix
from sith.model.model3doExporter import export3do
from sith.model.model3doLoader import Model3doFileFormat, Model3doFileVersion, load3do
from sith.model.utils import kModel3doGeometryMode


def gradient(width, height):
    """Greys and warm tones that fall between the fixture palette's entries; opaque."""
    y, x = np.mgrid[0:height, 0:width]
    rgb = np.stack((60 + 15 * x, 70 + 12 * y, 50 + 5 * (x + y)), axis=-1) / 255
    return np.concatenate((rgb, np.ones((height, width, 1))), axis=-1).astype(np.float32)


class RetailExportTests(SceneTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.path = self.folder / 'model.3do'
        self.palette_path = self.folder / 'test.cmp'
        self.palette_path.write_bytes(cmp_bytes())
        self.palette = ColorMap.load(self.palette_path)
        self.material = bpy.data.materials.new('test.mat')
        self.pixels = np.array([(0.2, 0.4, 0.6, 1), (0.7, 0.3, 0.1, 0),
                                (0, 1, 0, 0.5), (1, 0, 0, 1)], dtype=np.float32)
        self.image = self.bind('cel0', 2, 2, self.pixels, kMatCelImagePrefix + '0')
        self.obj = self.add_object('triangle', [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])

    def add_object(self, name, vertices, faces):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(vertices, [], faces)
        mesh.uv_layers.new()
        mesh.materials.append(self.material)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        setattr(obj, kModel3doGeometryMode, 'Texture')
        for other in bpy.context.selected_objects:
            other.select_set(False)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        return obj

    def grid(self, size=25):
        """One flat mesh of size x size vertices and (size - 1)^2 quads."""
        bpy.data.objects.remove(self.obj, do_unlink=True)
        vertices = [(0.01 * x, 0.01 * y, 0.0) for y in range(size) for x in range(size)]
        faces = [(row * size + col, row * size + col + 1, (row + 1) * size + col + 1, (row + 1) * size + col)
                 for row in range(size - 1) for col in range(size - 1)]
        self.obj = self.add_object('grid', vertices, faces)

    def bind(self, name, width, height, pixels, prop):
        image = bpy.data.images.new(name, width, height, float_buffer=True)
        image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
        image.update()
        self.material[prop] = image
        return image

    def export(self, version=Model3doFileVersion.Version2_1, **options):
        export3do(self.obj, str(self.path), version, version == Model3doFileVersion.Version2_1, False, True,
                  **options)

    def operator(self, **options):
        reports = []
        values = dict(filepath=str(self.path), obj=self.obj, version=Model3doFileVersion.Version2_1.name,
                      absolute_uv=True, export_vert_colors=False, sync_mesh_list=True, bake_pivots=False,
                      export_textures=False, texture_encoding='RGBA32', texture_cmp_file='',
                      overwrite_textures=False, texture_mipmaps=True, texture_directory='mat',
                      detail_levels=1, fail_over_budget=False, texture_editions=False,
                      texture_directory_8bit='mat8', texture_directory_16bit='mat16', texture_dither=False,
                      report=lambda kinds, message: reports.append((kinds, message)))
        values.update(options)
        return app.ExportModel3do.execute(SimpleNamespace(**values), bpy.context), reports

    def mat(self, *parts):
        return decode(self.folder.joinpath(*parts).read_bytes(), self.palette)

    # Retail budget

    def test_new_options_default_to_the_previous_behavior(self):
        properties = bpy.ops.export_scene.sith_3do.get_rna_type().properties
        self.assertEqual((properties['detail_levels'].default, properties['detail_levels'].hard_min,
                          properties['detail_levels'].hard_max), (1, 1, 4))
        for name in ('fail_over_budget', 'texture_editions', 'texture_dither'):
            with self.subTest(name=name):
                self.assertFalse(properties[name].default)
        self.assertEqual((properties['texture_directory_8bit'].default, properties['texture_directory_16bit'].default),
                         ('mat8', 'mat16'))
        self.assertFalse(bpy.ops.export_material.sith_mat.get_rna_type().properties['dither'].default)
        warnings = []
        self.export(warnings=warnings)
        model, _ = load3do(self.path)
        self.assertEqual((len(model.geosets), warnings), (1, []))

    def test_meshes_over_the_retail_limits_warn_on_2_1_and_can_stop_the_export(self):
        self.grid()
        warnings = []
        self.export(warnings=warnings)
        self.assertEqual(warnings, [
            "Over the retail JKDF2/MOTS limits: Mesh 0 'grid' has 625 vertices; JKDF2 allows 512 per mesh (113 over) in geoset 0",
            "Over the retail JKDF2/MOTS limits: Mesh 0 'grid' has 576 faces; JKDF2 allows 512 per mesh (64 over) in geoset 0"])
        self.assertEqual(len(load3do(self.path)[0].geosets[0].meshes[0].vertices), 625)
        self.path.write_bytes(b'previous model')
        with self.assertRaisesRegex(ValueError, r"over the retail JKDF2/MOTS limits: Mesh 0 'grid' has 625 vertices"):
            self.export(failOverBudget=True)
        self.assertEqual(self.path.read_bytes(), b'previous model')
        for version in (Model3doFileVersion.Version2_3, Model3doFileFormat.GrimFandango):
            with self.subTest(version=version):
                warnings = []
                self.export(version, warnings=warnings, failOverBudget=True)
                self.assertFalse(any('retail' in warning for warning in warnings))

    def test_the_operator_reports_budget_warnings_or_cancels_when_asked(self):
        self.grid()
        result, reports = self.operator()
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(sum(kinds == {'WARNING'} and 'retail JKDF2/MOTS' in message for kinds, message in reports), 2)
        self.path.unlink()
        result, reports = self.operator(fail_over_budget=True)
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports[-1][0], {'ERROR'})
        self.assertFalse(self.path.exists())

    # Detail levels

    def test_detail_levels_write_identical_geosets_and_keep_the_model_frame(self):
        self.export()
        single, _ = load3do(self.path)
        self.assertEqual(bpy.ops.export_scene.sith_3do(filepath=str(self.path), version='Version2_1',
                                                       detail_levels=4), {'FINISHED'})
        copies, _ = load3do(self.path)
        self.assertEqual(len(copies.geosets), 4)
        self.assertEqual((copies.radius, tuple(copies.insertOffset)), (single.radius, tuple(single.insertOffset)))
        self.assertEqual([(node.name, node.meshIdx, node.parentIdx, tuple(node.position), tuple(node.pivot))
                          for node in copies.meshHierarchy],
                         [(node.name, node.meshIdx, node.parentIdx, tuple(node.position), tuple(node.pivot))
                          for node in single.meshHierarchy])
        for geoset in copies.geosets:
            for mesh, original in zip(geoset.meshes, single.geosets[0].meshes):
                self.assertEqual((mesh.name, mesh.radius, mesh.vertices, mesh.uvs, mesh.normals,
                                  [face.vertexIdxs for face in mesh.faces], [face.uvIdxs for face in mesh.faces]),
                                 (original.name, original.radius, original.vertices, original.uvs, original.normals,
                                  [face.vertexIdxs for face in original.faces],
                                  [face.uvIdxs for face in original.faces]))
        for count in (0, 5):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'Detail level'):
                self.export(detailLevels=count)

    def test_budget_warnings_for_copied_detail_levels_name_every_geoset(self):
        self.grid()
        warnings = []
        self.export(warnings=warnings, detailLevels=3)
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all(warning.endswith('in geosets 0, 1, 2') for warning in warnings))

    # Both MAT editions

    def test_editions_write_the_same_names_indexed_and_rgb565_in_two_folders(self):
        warnings = []
        self.export(exportTextures=True, textureEditions=True, texturePalette=str(self.palette_path),
                    warnings=warnings)
        self.assertTrue(self.path.is_file())
        self.assertFalse((self.folder / 'mat').exists())
        self.assertEqual(sorted(path.name for path in (self.folder / 'mat8').iterdir()), ['test.mat'])
        self.assertEqual(sorted(path.name for path in (self.folder / 'mat16').iterdir()), ['test.mat'])
        indexed, _, cels = self.mat('mat8', 'test.mat')
        self.assertEqual((indexed.color_info.color_mode, indexed.color_info.bpp), (ColorMode.Indexed, 8))
        np.testing.assert_allclose(np.asarray(cels[0].pixel_data_array[0]).reshape((-1, 4)),
                                   expected_pixels(self.pixels, 'INDEXED', self.palette), atol=1e-12, rtol=0)
        color, _, cels = self.mat('mat16', 'test.mat')
        info = color.color_info
        self.assertEqual((info.bpp, info.red_bpp, info.green_bpp, info.blue_bpp, info.alpha_bpp), (16, 5, 6, 5, 0))
        np.testing.assert_allclose(np.asarray(cels[0].pixel_data_array[0]).reshape((-1, 4)),
                                   expected_pixels(self.pixels, 'RGB565'), atol=1e-12, rtol=0)
        self.assertEqual([len(cel.pixel_data_array) for cel in cels], [2])
        self.assertTrue(any('RGB565 discards alpha' in warning for warning in warnings))
        self.assertEqual(load3do(self.path)[0].materials, ['test.mat'])

    def test_edition_folders_are_configurable_and_follow_the_mat_folder_rules(self):
        self.export(exportTextures=True, textureEditions=True, texturePalette=str(self.palette_path),
                    textureDirectory8bit='gob8/3do/mat', textureDirectory16bit=str(self.folder / 'gob16/3do/mat'))
        self.assertTrue((self.folder / 'gob8/3do/mat/test.mat').is_file())
        self.assertTrue((self.folder / 'gob16/3do/mat/test.mat').is_file())
        self.path.unlink()
        properties = dict(export_textures=True, texture_editions=True, texture_cmp_file='test.cmp',
                          texture_directory_8bit='eight', texture_directory_16bit='sixteen')
        self.assertEqual(bpy.ops.export_scene.sith_3do(filepath=str(self.path), version='Version2_1', **properties),
                         {'FINISHED'})
        self.assertEqual(self.mat('eight', 'test.mat')[0].color_info.bpp, 8)
        self.assertEqual(self.mat('sixteen', 'test.mat')[0].color_info.bpp, 16)

    def test_editions_reject_one_folder_grim_and_a_missing_palette_before_writing(self):
        self.path.write_bytes(b'previous model')
        cases = ((dict(textureDirectory8bit='shared', textureDirectory16bit='./shared'), 'must differ'),
                 # One folder on case-insensitive volumes (the macOS and Windows defaults).
                 (dict(textureDirectory8bit='mat8', textureDirectory16bit='MAT8'), 'must differ'),
                 (dict(), 'CMP palette'),
                 (dict(version=Model3doFileFormat.GrimFandango), 'Grim Fandango'))
        for options, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                palette = {} if message == 'CMP palette' else {'texturePalette': str(self.palette_path)}
                self.export(exportTextures=True, textureEditions=True, **palette, **options)
            self.assertEqual(self.path.read_bytes(), b'previous model')
            self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ['model.3do', 'test.cmp'])

    def test_editions_keep_existing_mats_unless_overwrite_is_set(self):
        old = self.folder / 'mat8/test.mat'
        old.parent.mkdir()
        old.write_bytes(b'old 8-bit material')
        warnings = []
        options = dict(exportTextures=True, textureEditions=True, texturePalette=str(self.palette_path))
        self.export(warnings=warnings, **options)
        self.assertEqual(old.read_bytes(), b'old 8-bit material')
        self.assertEqual([warning for warning in warnings if 'Kept existing MAT' in warning],
                         ["Kept existing MAT 'test.mat' in 'mat8' (Overwrite Existing MAT Files is disabled)"])
        self.assertEqual(self.mat('mat16', 'test.mat')[0].color_info.bpp, 16)
        self.export(overwriteTextures=True, **options)
        self.assertEqual(self.mat('mat8', 'test.mat')[0].color_info.bpp, 8)

    # Dithering

    def use_gradient(self, width=8, height=8):
        self.pixels = gradient(width, height)
        self.image.scale(width, height)
        self.image.pixels.foreach_set(self.pixels.ravel())
        self.image.update()

    def indexed(self, *parts, **options):
        self.export(exportTextures=True, textureEncoding='INDEXED', texturePalette=str(self.palette_path),
                    overwriteTextures=True, generateMipmaps=False, **options)
        return np.asarray(self.mat(*(parts or ('mat', 'test.mat')))[2][0].pixel_data_array[0]).reshape((8, 8, 4))

    def test_dithering_changes_only_indexed_output_and_follows_a_material_mask(self):
        self.use_gradient()
        nearest = self.indexed()
        dithered = self.indexed(textureDither=True)
        self.assertTrue(np.any(dithered != nearest))
        np.testing.assert_allclose(nearest.reshape((-1, 4)), expected_pixels(self.pixels.reshape((-1, 4)), 'INDEXED', self.palette),
                                   atol=1e-12, rtol=0)
        mask = np.zeros((8, 8, 4), dtype=np.float32)
        mask[:, :4] = 1
        self.bind('mask', 8, 8, mask, kMatDitherMaskPrefix + '0')
        masked = self.indexed(textureDither=True)
        np.testing.assert_array_equal(masked[:, 4:], nearest[:, 4:])
        np.testing.assert_array_equal(masked[:, :4], dithered[:, :4])
        self.assertTrue(np.any(masked[:, :4] != nearest[:, :4]))
        # Editions dither only their Indexed MAT; the RGB 565 MAT matches an undithered export.
        self.export(exportTextures=True, textureEditions=True, texturePalette=str(self.palette_path),
                    generateMipmaps=False, textureDither=True)
        np.testing.assert_array_equal(np.asarray(self.mat('mat8', 'test.mat')[2][0].pixel_data_array[0]).reshape((8, 8, 4)),
                                      masked)
        np.testing.assert_allclose(np.asarray(self.mat('mat16', 'test.mat')[2][0].pixel_data_array[0]).reshape((-1, 4)),
                                   expected_pixels(self.pixels.reshape((-1, 4)), 'RGB565'), atol=1e-12, rtol=0)

    def test_invalid_masks_and_dithering_without_indexed_output_reject(self):
        self.use_gradient()
        with self.assertRaisesRegex(ValueError, 'Indexed'):
            self.export(exportTextures=True, textureEncoding='RGBA32', textureDither=True)
        self.material[kMatDitherMaskPrefix + '0'] = 'not an image'
        with self.assertRaisesRegex(ValueError, 'must reference an image'):
            self.indexed(textureDither=True)
        self.bind('small mask', 4, 4, np.ones((4, 4, 4)), kMatDitherMaskPrefix + '0')
        with self.assertRaisesRegex(ValueError, 'is 4x4'):
            self.indexed(textureDither=True)
        # A mask is read only when dithering is requested.
        self.indexed()

    def test_hidden_dither_and_edition_options_are_ignored_by_the_operator(self):
        self.use_gradient()
        result, reports = self.operator(export_textures=True, texture_encoding='RGBA32', texture_dither=True)
        self.assertEqual(result, {'FINISHED'}, reports)
        self.assertEqual(self.mat('mat', 'test.mat')[0].color_info.bpp, 32)
        result, reports = self.operator(version='GrimFandango', export_textures=True, texture_encoding='INDEXED',
                                        texture_cmp_file=str(self.palette_path), texture_editions=True,
                                        overwrite_textures=True)
        self.assertEqual(result, {'FINISHED'}, reports)
        self.assertFalse((self.folder / 'mat8').exists())

    def test_standalone_mat_export_dithers_indexed_output(self):
        self.use_gradient()
        target = self.folder / 'standalone.mat'
        self.assertTrue(exportMat(self.material, target, 'INDEXED', str(self.palette_path)))
        nearest = target.read_bytes()
        with bpy.context.temp_override(sith_mat_export_source=self.material):
            self.assertEqual(bpy.ops.export_material.sith_mat(filepath=str(target), encoding='INDEXED',
                                                              cmp_file=str(self.palette_path), dither=True,
                                                              overwrite_existing=True), {'FINISHED'})
        self.assertNotEqual(target.read_bytes(), nearest)
        with self.assertRaisesRegex(ValueError, 'Indexed'):
            exportMat(self.material, self.folder / 'packed.mat', 'RGB565', dither=True)

    # Dialog layout

    def test_dialogs_show_edition_dither_and_budget_options_only_where_they_apply(self):
        def shown(**values):
            layout = mock.Mock()
            options = dict(layout=layout, version='Version2_1', export_textures=True, texture_encoding='RGBA32',
                           texture_editions=False)
            options.update(values)
            app.ExportModel3do.draw(SimpleNamespace(**options), None)
            return [call.args[1] for call in layout.mock_calls if call[0].endswith('prop')]

        names = shown()
        self.assertIn('detail_levels', names)
        self.assertIn('fail_over_budget', names)
        self.assertIn('texture_editions', names)
        self.assertNotIn('texture_dither', names)
        self.assertNotIn('fail_over_budget', shown(version='Version2_3'))
        names = shown(texture_editions=True)
        self.assertEqual([name in names for name in ('texture_directory_8bit', 'texture_directory_16bit',
                                                     'texture_cmp_file', 'texture_dither', 'texture_directory',
                                                     'texture_encoding')],
                         [True, True, True, True, False, False])
        self.assertIn('texture_dither', shown(texture_encoding='INDEXED'))
        names = shown(version='GrimFandango', texture_encoding='INDEXED', texture_editions=True)
        self.assertNotIn('texture_editions', names)
        self.assertNotIn('texture_directory_8bit', names)
        self.assertIn('texture_directory', names)
        for encoding in ('RGBA32', 'INDEXED'):
            layout = mock.Mock()
            app.ExportMat.draw(SimpleNamespace(layout=layout, target='SITH', encoding=encoding), None)
            names = [call.args[1] for call in layout.mock_calls if call[0].endswith('prop')]
            self.assertEqual('dither' in names, encoding == 'INDEXED')
