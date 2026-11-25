# Sith Blender Addon - Blender registration and user interface
# Copyright (C) 2019-2026 Crt Vavros
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# Reload imported submodules if script is reloaded
if "bpy" in locals():
    import importlib
    from . import key as _key_pkg
    importlib.reload(_key_pkg)
    from . import material as _mat_pkg
    importlib.reload(_mat_pkg)
    from . import model as _model_pkg
    importlib.reload(_model_pkg)
    from .model import utils as _model_utils_pkg
    importlib.reload(_model_utils_pkg)
    from . import text as _text_pkg
    importlib.reload(_text_pkg)
    from . import utils as _utils_pkg
    importlib.reload(_utils_pkg)

import bpy, bmesh, json, mathutils, math, os.path, re
from bpy_extras.io_utils import ImportHelper
from bpy_extras.io_utils import ExportHelper
from pathlib import Path

from .key import (
    exportKey,
    importKey,
    KeyFlag
)
from .key.keyValidation import kKeyTargets
from .key.keyUtils import getSceneFrameRate, prepareSceneFrameRate, setSceneFrameRate, kKeyImportReport

from .material import importMat, exportMat
from .material.matWriter import kMatEncodings

from .model import (
    export3do,
    import3do,
    FaceType,
    GeometryMode,
    LightMode,
    TextureMode
)

from .model.model3doLoader import Model3doFileFormat, Model3doFileVersion
from .model import model3doPreview
from .model.utils import (
    bmFaceGetExtraLight,
    bmFaceGetGeometryMode,
    bmFaceGetLightMode,
    bmFaceGetTextureMode,
    bmFaceGetType,
    bmFaceSetExtraLight,
    bmFaceSetGeometryMode,
    bmFaceSetLightMode,
    bmFaceSetTextureMode,
    bmFaceSetType,
    bmMeshInit3doLayers,
    kModel3doGeometryMode,
    kModel3doImportReport,
    kModel3doShadow,
    kNameOrderPrefix,
    getNativePivot,
    getPivotHelperObj,
    isPivotHelperObj,
    makePivotHelper,
    removePivotHelper,
    setNativePivot,
)

from .utils import *
from .types import HexProperty, Vector4f
from .types.vector import kFloat32Max


def _make_readable(str):
    return re.sub(r"(\w)([A-Z])", r"\1 \2", str)

def _get_key_flags_enum_list():
    return [
        (KeyFlag.MovementControlled.name    , 'Movement controlled'   , "Animation is controlled by the movement of animated game object, and played at speed of the puppet movement. Keyframe's FPS is not used" ),
        (KeyFlag.NoLoop.name                , 'No Loop'               , "Don't loop play animation"                                                                                                               ),
        (KeyFlag.PauseOnLastFrame.name      , 'Pause On Last Frame'   , 'Pause animation on the last frame'                                                                                                       ),
        (KeyFlag.RestartActive.name         , 'Restart Active'        , 'Restart animation if active'                                                                                                             ),
        (KeyFlag.DisableFadeIn.name         , 'Disable Fade-in'       , 'Disable animation fade-in interpolation'                                                                                                 ),
        (KeyFlag.FadeOutAndNoLoop.name      , 'Fade-out & No Loop'    , 'Fade-out animation and finish playing'                                                                                                   ),
        (KeyFlag.SetPositionToLastFrame.name, '(IJIM) Last Frame Pose', '(IJIM only) Animation sets the game object\'s pose to the last frame pose when the animation finishes playing.'                          ),
    ]

def _get_mesh3do_face_type_list():
    return [
        (FaceType.DoubleSided.name   , 'Double Sided'                     , 'Polygon face is rendered in game on both sides'                                                                       ),
        (FaceType.Translucent.name   , 'Translucent'                      , 'Polygon is rendered in game with alpha blending enabled. This makes polygon with transparent texture translucent'     ),
        (FaceType.TexClamp_x.name    , 'Clamp Horizontal (IJIM)'          , '(IJIM only) Polygon texture is clamped horizontally instead of repeated'                                              ),
        (FaceType.TexClamp_y.name    , 'Clamp Vertical (IJIM)'            , '(IJIM only) Polygon texture is clamped vertically instead of repeated'                                                ),
        (FaceType.TexFilterNone.name , 'Disable Bilinear Filtering (IJIM)', '(IJIM only) Uses nearest texture filtering. In MOTS and Grim Fandango, bit 0x10 instead forces lighting mode 1 (Not Lit)' ),
        (FaceType.ZWriteDisabled.name, 'Disable ZWrite (IJIM)'            , '(IJIM only) Disables writing the polygon face to the depth buffer'                                                     ),
        (FaceType.IjimLedge.name     , 'Ledge (IJIM)'                     , '(IJIM only) Polygon face is a ledge that player can grab and hang from'                                                ),
        (FaceType.IjimFogEnabled.name, 'Enable Fog (IJIM)'                , '(IJIM only) Enables fog rendering for polygon face. Enabled by default by the engine'                                  ),
        (FaceType.IjimWhipAim.name   , 'Whip Aim (IJIM)'                  , '(IJIM only) Polygon face is whip aiming spot from which player can search in the area for object(s) to mount whip on'  )
    ]

def _get_model3do_geometry_mode_list():
    l = []
    for f in GeometryMode:
        l.append((f.name, _make_readable(f.name), ''))
    return l

def _get_model3do_light_mode_list():
    l = []
    for f in LightMode:
        if f == LightMode.GfUnknown6:
            # Keep the saved identifier; only the display name describes the recovered behavior.
            l.append((f.name, 'Mesh Intensity (Grim Fandango)',
                      "Use cached mesh lighting in Grim Fandango's software renderer; "
                      "can inherit an ancestor mesh's intensity. Stored lighting mode 6, "
                      'not a Blender lighting setting'))
        else:
            l.append((f.name, _make_readable(f.name), ''))
    return l

def _get_model3do_texture_mode_list():
    l = []
    for f in TextureMode:
        if f == TextureMode.PerspectiveUnknown:
            l.append((f.name, 'Unknown (2)',
                      'Stored texture mode 2; its exact engine behavior is not established. '
                      'Preserved on import/export, not a Blender texture setting'))
        else:
            l.append((f.name, _make_readable(f.name), ''))
    return l

def _get_export_obj(context, report, data_type: str):
    """Resolve only the active selected object's model in this view layer."""
    obj = context.active_object
    # Linked and local objects can have identical names in the same view layer.
    layerObjects = set(context.view_layer.objects)
    if (obj is None or obj not in context.selected_objects
            or obj not in layerObjects):
        report({'ERROR'}, 'No object selected! Make the model or one of its joints the active selected object.')
        return None
    if obj.type not in ('MESH', 'EMPTY'):
        report({'ERROR'}, f"Cannot export {data_type} data from selected object of type '{obj.type}'; select a Mesh or Empty.")
        return None

    # Selection identifies one model, not a batch. Include its unselected joints,
    # but never cross an unsupported parent or enter another scene/view layer.
    while obj.parent is not None:
        parent = obj.parent
        if parent.type not in ('MESH', 'EMPTY'):
            report({'ERROR'}, f"Bake or reparent '{obj.name}' out of unsupported parent '{parent.name}' ({parent.type}) before export.")
            return None
        if parent not in layerObjects:
            report({'ERROR'}, f"Model parent '{parent.name}' is outside the active view layer; include its hierarchy before export.")
            return None
        obj = parent
    return obj


class ImportMat(bpy.types.Operator, ImportHelper):
    """Import Sith game engine texture (.mat)"""
    bl_idname    = 'import_material.sith_mat'
    bl_label     = 'Import MAT'
    filename_ext = '.mat'

    filter_glob: bpy.props.StringProperty(
        default = '*.mat',
        options = {'HIDDEN'}
    )

    cmp_file: bpy.props.StringProperty(
        name        = 'ColorMap File',
        description = "ColorMap (.cmp) for indexed MAT textures in JKDF2, MOTS, DroidWorks and Grim Fandango. Searches the file's directory and resource folders; an empty field tries dflt.cmp and dfltcmp.cmp. Grim costume-specific palettes must be selected explicitly",
    )

    def draw(self, context):
        layout = self.layout
        cmp_file_layout = layout.box().column()
        cmp_file_layout.label(text='ColorMap File')
        cmp_file_layout.prop(self, 'cmp_file', text='')

    def execute(self, context):
        # Validate filepath
        if not self.filepath or not os.path.isfile(self.filepath):
            self.report({'ERROR'}, 'No file selected or file does not exist!')
            return {'CANCELLED'}
        if not self.filepath.lower().endswith('.mat'):
            self.report({'ERROR'}, 'Selected file is not a .mat file!')
            return {'CANCELLED'}

        try:
            cmp = getCmpFileOrDefault(self.cmp_file, self.filepath)
            importMat(self.filepath, cmp)
        except (ImportError, OSError, ValueError, IndexError) as error:
            self.report({'ERROR'}, f'Cannot import MAT: {error}')
            return {'CANCELLED'}
        return {'FINISHED'}


def _get_outliner_mat_source(context):
    source = getattr(context, 'id', None)
    if source is None:
        selected = getattr(context, 'selected_ids', ()) or ()
        if len(selected) == 1:
            source = selected[0]
    return source if isinstance(source, (bpy.types.Material, bpy.types.Image)) else None


def _get_mat_export_source(context):
    """Use the menu's explicit ID, never an unrelated active object's material."""
    source = getattr(context, 'sith_mat_export_source', None)
    if source is None:
        area = getattr(context, 'area', None)
        if area is not None and area.type == 'OUTLINER':
            source = _get_outliner_mat_source(context)
        elif area is not None and area.type == 'IMAGE_EDITOR':
            source = getattr(context.space_data, 'image', None)
        else:
            source = getattr(context, 'material', None)
    return source if isinstance(source, (bpy.types.Material, bpy.types.Image)) else None


def _on_mat_target_update(self, context):
    if self.target == 'GRIM':
        self.encoding = 'INDEXED'


def _on_mat_overwrite_update(self, context):
    self.check_existing = self.overwrite_existing


class ExportMat(bpy.types.Operator, ExportHelper):
    """Export an image or a material's texture cels to a Sith MAT file"""
    bl_idname = 'export_material.sith_mat'
    bl_label = 'Export MAT'
    filename_ext = '.mat'

    filter_glob: bpy.props.StringProperty(default='*.mat', options={'HIDDEN'})
    check_existing: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    target: bpy.props.EnumProperty(
        name='Target', default='SITH', update=_on_mat_target_update,
        items=[('SITH', 'Standard', 'Standard Sith MAT for compatible IJIM, JKDF2, MOTS and DroidWorks engines'),
               ('GRIM', 'Grim Fandango', 'Indexed Grim Fandango MAT with one base image per cel')],
    )
    encoding: bpy.props.EnumProperty(name='Encoding', items=kMatEncodings, default='RGBA32')
    cmp_file: bpy.props.StringProperty(
        name='ColorMap File',
        description='CMP palette for indexed encoding. Relative paths resolve beside the exported MAT; // paths are relative to the blend file',
    )
    generate_mipmaps: bpy.props.BoolProperty(
        name='Generate Mipmaps', default=True,
        description='Generate a complete alpha-aware mip chain down to 1x1 for standard MAT output',
    )
    overwrite_existing: bpy.props.BoolProperty(
        name='Overwrite Existing MAT', default=False, update=_on_mat_overwrite_update,
        description='Allow replacement of an existing MAT, with file-browser confirmation. Disabled by default',
    )
    dither: bpy.props.BoolProperty(
        name='Dither', default=False,
        description='Indexed only: where the CMP has no close color, such as skin, use an ordered 4x4 pattern of two palette colors. Colors the palette matches stay solid, and a pattern never adds a color the CMP never darkens. A material image in sith_dither_mask_<cel> limits dithering to its white texels',
    )
    avoid_fullbright: bpy.props.BoolProperty(
        name='Avoid Fullbright Colours', default=False,
        description='Indexed only: use no palette color that the CMP never darkens (it would glow in shadow), for skin and cloth that must take shadow. Needs a JKDF2/MOTS CMP with its light table',
    )

    _source = None

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'target')
        row = layout.row()
        row.enabled = self.target != 'GRIM'
        row.prop(self, 'encoding')
        if self.encoding == 'INDEXED':
            layout.prop(self, 'cmp_file')
            layout.prop(self, 'dither')
            layout.prop(self, 'avoid_fullbright')
        if self.target != 'GRIM':
            layout.prop(self, 'generate_mipmaps')
        layout.prop(self, 'overwrite_existing')

    def invoke(self, context, event):
        self._source = _get_mat_export_source(context)
        if self._source is None:
            self.report({'ERROR'}, 'Select an image or material to export to MAT')
            return {'CANCELLED'}
        # Preserve identity across the file browser, including linked or renamed IDs.
        if not self.filepath:
            name = self._source.name
            if name.lower().endswith('.mat') or (isinstance(self._source, bpy.types.Image)
                    and Path(name).suffix.lower() in bpy.path.extensions_image):
                name = os.path.splitext(name)[0]
            self.filepath = bpy.path.clean_name(name) + self.filename_ext
        self.check_existing = self.overwrite_existing
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        if self._source is None:
            self._source = _get_mat_export_source(context)
        if self._source is None:
            self.report({'ERROR'}, 'Select an image or material to export to MAT')
            return {'CANCELLED'}
        if not self.filepath:
            self.report({'ERROR'}, 'No MAT output file selected')
            return {'CANCELLED'}
        self.filepath = bpy.path.ensure_ext(self.filepath, self.filename_ext)
        warnings = []
        try:
            written = exportMat(self._source, self.filepath, self.encoding, self.cmp_file,
                                overwrite=self.overwrite_existing, generateMipmaps=self.generate_mipmaps,
                                grim=self.target == 'GRIM', warnings=warnings,
                                dither=getattr(self, 'dither', False) and self.encoding == 'INDEXED',
                                avoidFullbright=getattr(self, 'avoid_fullbright', False) and self.encoding == 'INDEXED')
        except (ImportError, OSError, ReferenceError, RuntimeError, ValueError) as error:
            self.report({'ERROR'}, f'Cannot export MAT: {error}')
            return {'CANCELLED'}
        for warning in warnings:
            self.report({'WARNING'}, warning)
        if not written:
            return {'CANCELLED'}
        self.report({'INFO'}, f"MAT '{os.path.basename(self.filepath)}' was successfully exported")
        return {'FINISHED'}


def _draw_mat_export(layout, source):
    if not isinstance(source, (bpy.types.Material, bpy.types.Image)):
        return
    layout.separator()
    row = layout.row()
    row.operator_context = 'INVOKE_DEFAULT'
    row.context_pointer_set('sith_mat_export_source', source)
    row.operator(ExportMat.bl_idname, text='Export to MAT...', icon='EXPORT')


def menu_export_mat_outliner(self, context):
    space = context.space_data
    if space.type == 'OUTLINER':
        _draw_mat_export(self.layout, _get_outliner_mat_source(context))


def menu_export_mat_image(self, context):
    _draw_mat_export(self.layout, context.space_data.image)


def menu_export_mat_material(self, context):
    _draw_mat_export(self.layout, context.material)


def menu_export_mat_material_slot(self, context):
    ui_list = getattr(context, 'ui_list', None)
    if getattr(ui_list, 'bl_idname', None) != 'MATERIAL_UL_matslots':
        return
    # Row context can differ from the active slot. Never export context.material
    # here: right-clicking another row must export that row's material instead.
    source = getattr(context, 'id', None)
    if source is None:
        source = getattr(getattr(context, 'material_slot', None), 'material', None)
    if isinstance(source, bpy.types.Material):
        _draw_mat_export(self.layout, source)


class ImportModel3do(bpy.types.Operator, ImportHelper):
    """Import Sith game engine 3DO model (.3do)"""
    bl_idname    = 'import_scene.sith_3do'
    bl_label     = 'Import 3DO'
    filename_ext = '.3do'

    filter_glob: bpy.props.StringProperty(
        default = '*.3do',
        options = {'HIDDEN'}
    )

    set_3d_view: bpy.props.BoolProperty(
        name        = 'Adjust 3D View',
        description = 'Adjust 3D View accordingly to the 3DO model position, size etc..',
        default     = True,
    )

    clear_scene: bpy.props.BoolProperty(
        name        = 'Clear Scene',
        description = 'Remove all scenes and content before importing 3DO model to the scene',
        default     = True,
    )

    uv_absolute_3do_2_1: bpy.props.BoolProperty(
        name        = 'v2.1 Absolute UV',
        description = 'Convert absolute pixel-space UVs to normalized Blender UVs for Jedi Knight: Dark Forces II, Mysteries of the Sith, Grim Fandango and DroidWorks',
        default     = True,
    )

    vertex_colors: bpy.props.BoolProperty(
        name        = 'Import Vertex Colors',
        description = 'Import mesh vertex colors from 3DO',
        default     = False,
    )

    vertex_normals: bpy.props.BoolProperty(
        name        = 'Import Vertex Normals (Text)',
        description = 'Preserve authored normals on legacy text models too. Binary and explicitly identified Grim models always preserve their authored normals',
        default     = False,
    )

    import_radius_objects: bpy.props.BoolProperty(
        name        = 'Import Radius Objects',
        description = 'Import mesh radius as wireframe sphere object',
        default     = False,
    )

    import_pivot_helpers: bpy.props.BoolProperty(
        name        = 'Import Pivot Helpers',
        description = "Show each joint's native pivot as a movable arrows helper. Moving a helper sets that joint's pivot, and objects parented to it follow the joint's attachment frame",
        default     = False,
    )

    preserve_order: bpy.props.BoolProperty(
        name        = 'Preserve Mesh Hierarchy',
        description = f"Keep the original node order visible in Blender by prefixing joint object names with '{kNameOrderPrefix}<seq_number>_'.\n\nParent links and stored node sequence numbers are preserved with this option on or off. Without prefixes, Blender's name ordering may change unsynced mesh-list order on export.",
        default     = False,
    )

    mat_dir: bpy.props.StringProperty(
        name        = 'MAT Directory',
        description = "Path to the directory to search for MAT texture files (.mat) of the imported 3DO model.\n\nBy default, required texture files are searched in the 'mat' directory at the location of the imported 3DO model and its parent directory",
    )

    cmp_file: bpy.props.StringProperty(
        name        = 'ColorMap File',
        description = "ColorMap (.cmp) for indexed MAT textures in JKDF2, MOTS, DroidWorks and Grim Fandango. Searches beside the model and in resource folders; an empty field tries dflt.cmp and dfltcmp.cmp. Grim costume-specific palettes must be selected explicitly",
    )

    allow_errors: bpy.props.BoolProperty(
        name        = 'Try Recover Broken File',
        description = 'Attempt to import usable parts of a broken file, omitting unusable faces and substituting missing UVs. Missing geometry and transforms cannot be reconstructed. View Import Report opens the recovery details',
        default     = False,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'set_3d_view')
        layout.prop(self, 'clear_scene')
        layout.prop(self, 'uv_absolute_3do_2_1')
        layout.prop(self, 'vertex_colors')
        layout.prop(self, 'vertex_normals')
        layout.prop(self, 'import_radius_objects')
        layout.prop(self, 'import_pivot_helpers')
        layout.prop(self, 'preserve_order')
        layout.prop(self, 'allow_errors')

        mat_layout = layout.box().column()
        mat_layout.label(text='Texture(s)')
        mat_dir_layout = mat_layout.box().column()
        mat_dir_layout.label(text='MAT Directory')
        mat_dir_layout.prop(self, 'mat_dir', text='')
        cmp_file_layout = mat_layout.box().column()
        cmp_file_layout.label(text='ColorMap File')
        cmp_file_layout.prop(self, 'cmp_file', text='')

    def execute(self, context):
        # Validate filepath
        if not self.filepath or not os.path.isfile(self.filepath):
            self.report({'ERROR'}, 'No file selected or file does not exist!')
            return {'CANCELLED'}
        if not self.filepath.lower().endswith('.3do'):
            self.report({'ERROR'}, 'Selected file is not a .3do file!')
            return {'CANCELLED'}

        warnings = []
        try:
            obj = import3do(self.filepath, [self.mat_dir], self.cmp_file, self.uv_absolute_3do_2_1, self.vertex_colors, self.import_radius_objects, self.preserve_order, self.clear_scene, allowErrors=self.allow_errors, warnings=warnings, importVertexNormals=getattr(self, 'vertex_normals', False), importPivotHelpers=getattr(self, 'import_pivot_helpers', False))
        except (AssertionError, TypeError, ValueError, OSError, IndexError, RuntimeError) as error:
            self.report({'ERROR'}, f'Cannot import 3DO: {error}')
            return {'CANCELLED'}
        if obj is None:
            self.report({'WARNING'}, 'The 3DO contains no importable model')
            return {'CANCELLED'}
        if warnings:
            reportName = obj[kModel3doImportReport]
            warningCount = len(warnings)
            self.report({'WARNING'}, f'Recovery warnings: {warningCount}. View Import Report in 3DO Properties for details')
            if not bpy.app.background and context.window is not None:
                def draw_recovery_notice(menu, context):
                    menu.layout.label(text=f'Recovery warnings: {warningCount}')
                    menu.layout.operator(View3doImportReport.bl_idname, icon='TEXT').report_name = reportName
                context.window_manager.popup_menu(draw_recovery_notice, title='3DO Recovery', icon='ERROR')

        # Background Blender may expose a screen/window while viewport operators
        # still cannot run. Framing is optional and must not fail a valid import.
        if (self.set_3d_view and not bpy.app.background
                and context.window is not None and context.screen is not None):
            try:
                _frame_imported_model(context, obj)
            except RuntimeError as error:
                self.report({'WARNING'}, f'Model imported, but viewport framing failed: {error}')

        return {'FINISHED'}


def _frame_imported_model(context, obj):
    """Frame a model without keeping temporary selection or object-view locks."""
    # Framing must not switch an existing edit/sculpt session to Object Mode.
    if context.mode != 'OBJECT':
        return
    area = next((area for area in context.screen.areas if area.type == 'VIEW_3D'), None)
    if area is None:
        return
    region = next((region for region in area.regions if region.type == 'WINDOW'), None)
    space = next((space for space in area.spaces if space.type == 'VIEW_3D'), None)
    if region is None or space is None or space.region_3d is None:
        return

    active_obj = context.view_layer.objects.active
    selected_objects = tuple(context.selected_objects)
    view_lock = space.lock_object
    perspective = space.region_3d.view_perspective

    # All operators need the viewport override, not the file browser's context.
    with context.temp_override(area=area, region=region):
        try:
            space.shading.type                = 'SOLID'
            space.shading.light               = 'FLAT'
            space.shading.color_type          = 'TEXTURE'
            space.shading.show_object_outline = False
            space.lens                        = 100.0
            space.clip_start                  = 0.001
            space.lock_object                 = obj
            space.overlay.show_floor          = True
            space.overlay.show_axis_x         = True
            space.overlay.show_axis_y         = True
            space.overlay.grid_lines          = 10
            space.overlay.grid_scale          = 1.0
            space.overlay.grid_subdivisions   = 10

            context.view_layer.objects.active = obj
            obj.select_set(True)
            bpy.ops.object.select_grouped(type='CHILDREN_RECURSIVE')
            context.view_layer.update()

            # Measure world-space vertices so child rotations and scale affect
            # framing. Flat models are easier to inspect from above.
            bb_min = mathutils.Vector([float('inf')] * 3)
            bb_max = mathutils.Vector([float('-inf')] * 3)
            for child_obj in [obj] + list(obj.children_recursive):
                if child_obj.type == 'MESH' and child_obj.data:
                    for vertex in child_obj.data.vertices:
                        wco = child_obj.matrix_world @ vertex.co
                        for i in range(3):
                            bb_min[i] = min(bb_min[i], wco[i])
                            bb_max[i] = max(bb_max[i], wco[i])

            view_type = 'BACK'
            if bb_min.x < float('inf'):
                bb_size = bb_max - bb_min
                footprint = max(bb_size.x, bb_size.y)
                if footprint > 0 and bb_size.z < footprint * 0.3:
                    view_type = 'TOP'

            bpy.ops.view3d.view_center_lock()
            bpy.ops.view3d.view_axis(type=view_type, align_active=True)
            space.region_3d.view_perspective = perspective
            if bb_min.x < float('inf'):
                space.region_3d.view_distance = max((bb_max - bb_min).length * 2.5, 1.0)
                space.region_3d.view_location = (bb_min + bb_max) * 0.5
            else:
                bpy.ops.view3d.view_selected()
        finally:
            # Direct selection APIs also work if a viewport operator just failed.
            for selected in context.view_layer.objects:
                if selected.select_get():
                    selected.select_set(False)
            for selected in selected_objects:
                selected.select_set(True)
            context.view_layer.objects.active = active_obj
            space.lock_object = view_lock
            space.region_3d.view_perspective = perspective


def _on_export_format_update(self, context):
    if self.version in (Model3doFileVersion.Version2_1.name, Model3doFileFormat.GrimFandango.name):
        self.texture_encoding = 'INDEXED'


class ExportModel3do(bpy.types.Operator, ExportHelper):
    """Export object(s) to Sith game engine 3DO file format (.3do)"""
    bl_idname    = 'export_scene.sith_3do'
    bl_label     = 'Export 3DO'
    filename_ext = '.3do'

    filter_glob: bpy.props.StringProperty(
        default = '*.3do',
        options = {'HIDDEN'}
    )

    version: bpy.props.EnumProperty(
        name        = 'Format',
        description = '',  # Use the selected format's description without a generic preface.
        items       = [
            (Model3doFileVersion.Version2_1.name, '2.1 - JKDF2 & MOTS', 'Star Wars Jedi Knight: Dark Forces II & Star Wars Jedi Knight: Mysteries of the Sith'),
            (Model3doFileVersion.Version2_2.name, '2.2 - IJIM (RGB)'  , 'Indiana Jones and the Infernal Machine - RGB color' ),
            (Model3doFileVersion.Version2_3.name, '2.3 - IJIM'        , 'Indiana Jones and the Infernal Machine - RGBA color'),
            (Model3doFileFormat.GrimFandango.name, 'Grim Fandango', 'Grim Fandango binary format. For text export, select 2.1')
        ],
        default= Model3doFileVersion.Version2_3.name,
        update = _on_export_format_update,
    )

    absolute_uv: bpy.props.BoolProperty(
        name        = 'v2.1 Absolute UV',
        description = 'Export absolute pixel-space UVs for Jedi Knight: Dark Forces II, Mysteries of the Sith, Grim Fandango and DroidWorks',
        default     = True,
    )

    export_vert_colors: bpy.props.BoolProperty(
        name        = 'Export Vertex Colors',
        description = 'Export vertex colors to 3DO file',
        default     = False,
    )

    sync_mesh_list: bpy.props.BoolProperty(
        name        = 'Sync Mesh List with Node Hierarchy',
        description = 'Number meshes in hierarchy-node order and update every node mesh reference.\n\nEmpty joints use no mesh slot, so mesh IDs do not always equal node IDs. When disabled, keep Blender traversal mesh order; hierarchy node IDs and KEY animation bindings remain independent.',
        default     = True,
    )

    bake_pivots: bpy.props.BoolProperty(
        name        = 'Bake Pivots',
        description = 'Write zero node pivots and bake their offsets into exported mesh coordinates. Discards stored native pivot metadata without changing the Blender scene',
        default     = False,
    )

    detail_levels: bpy.props.IntProperty(
        name        = 'Detail Levels',
        description = 'Number of geosets to write, 1 to 4, each a copy of the full model. The game chooses a geoset by distance, so copies keep full detail at every distance. 1 writes a single geoset, as before',
        default     = 1,
        min         = 1,
        max         = 4,
    )

    fail_over_budget: bpy.props.BoolProperty(
        name        = 'Fail Over Retail Limits',
        description = '2.1 exports are checked against the JKDF2/MOTS limits: 512 vertices, 768 texture vertices and 512 faces per mesh, 24 corners per face, 4 geosets and 64 joints. Enable to stop the export, writing nothing, instead of warning',
        default     = False,
    )

    export_textures: bpy.props.BoolProperty(
        name='Export Textures to MAT', default=False,
        description='Write the model\'s image textures and all bound cels to the MAT directory. Exports image pixels, not procedural shaders or preview lighting',
    )
    texture_directory: bpy.props.StringProperty(
        name='MAT Directory', default='mat',
        description='Folder for exported MATs. Default mat is beside the exported 3DO; other relative paths resolve there too. Absolute paths and blend-relative // paths are accepted',
    )
    texture_encoding: bpy.props.EnumProperty(name='Encoding', items=kMatEncodings, default='RGBA32')
    texture_cmp_file: bpy.props.StringProperty(
        name='ColorMap File', default='',
        description='CMP palette used to encode indexed textures. Relative paths are resolved beside the exported 3DO; // paths are relative to the blend file',
    )
    texture_mipmaps: bpy.props.BoolProperty(
        name='Generate Mipmaps', default=True,
        description='Generate a complete smaller-image chain down to 1x1, with alpha-aware area filtering',
    )
    overwrite_textures: bpy.props.BoolProperty(
        name='Overwrite Existing MAT Files', default=False,
        description='Replace existing MAT files in the export folder. Disabled by default: existing MATs are kept and reported',
    )
    texture_editions: bpy.props.BoolProperty(
        name='8-bit and 16-bit Editions', default=False,
        description='Write every MAT twice under the same name: Indexed with the ColorMap File in the 8-bit MAT Directory, and RGB 565 in the 16-bit MAT Directory. Replaces Encoding and MAT Directory',
    )
    texture_directory_8bit: bpy.props.StringProperty(
        name='8-bit MAT Directory', default='mat8',
        description='Folder for the Indexed edition. Default mat8 is beside the exported 3DO; other relative paths resolve there too. Absolute paths and blend-relative // paths are accepted',
    )
    texture_directory_16bit: bpy.props.StringProperty(
        name='16-bit MAT Directory', default='mat16',
        description='Folder for the RGB 565 edition; it must differ from the 8-bit folder. Default mat16 is beside the exported 3DO; other relative paths resolve there too',
    )
    texture_dither: bpy.props.BoolProperty(
        name='Dither Indexed Textures', default=False,
        description='Where the CMP has no close color, such as skin, use an ordered 4x4 pattern of two palette colors. Colors the palette matches stay solid, and a pattern never adds a color the CMP never darkens. A material image in sith_dither_mask_0 limits dithering to its white texels',
    )
    texture_avoid_fullbright: bpy.props.BoolProperty(
        name='Avoid Fullbright Colours', default=False,
        description='Indexed textures use no palette color that the CMP never darkens (it would glow in shadow), for skin and cloth that must take shadow. Needs a JKDF2/MOTS CMP with its light table',
    )

    obj = None

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'version')
        grim = self.version == Model3doFileFormat.GrimFandango.name
        if self.version in (Model3doFileVersion.Version2_1.name, Model3doFileFormat.GrimFandango.name):
            layout.prop(self, 'absolute_uv')
        layout.prop(self, 'export_vert_colors')
        layout.prop(self, 'sync_mesh_list')
        layout.prop(self, 'bake_pivots')
        layout.prop(self, 'detail_levels')
        if self.version == Model3doFileVersion.Version2_1.name:
            layout.prop(self, 'fail_over_budget')
        layout.prop(self, 'export_textures')
        if self.export_textures:
            textures = layout.column()
            editions = getattr(self, 'texture_editions', False) and not grim
            if not grim:
                textures.prop(self, 'texture_editions')
            if editions:
                textures.prop(self, 'texture_directory_8bit')
                textures.prop(self, 'texture_directory_16bit')
            else:
                textures.prop(self, 'texture_directory')
                textures.prop(self, 'texture_encoding')
            if editions or self.texture_encoding == 'INDEXED':
                textures.prop(self, 'texture_cmp_file')
                textures.prop(self, 'texture_dither')
                textures.prop(self, 'texture_avoid_fullbright')
            if not grim:
                textures.prop(self, 'texture_mipmaps')
            textures.prop(self, 'overwrite_textures')

    def invoke(self, context, event):
        self.obj = _get_export_obj(context, self.report, 'mesh')
        if self.obj is None:
            return {'CANCELLED'}
        self.filepath = bpy.path.ensure_ext(self.obj.name , self.filename_ext)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        # Validate filepath
        if not self.filepath:
            self.report({'ERROR'}, 'No file path specified!')
            return {'CANCELLED'}
        if not self.filepath.lower().endswith('.3do'):
            self.filepath = bpy.path.ensure_ext(self.filepath, self.filename_ext)

        # Scripted EXEC_DEFAULT calls do not pass through the file browser's invoke.
        if self.obj is None:
            self.obj = _get_export_obj(context, self.report, 'mesh')
        if self.obj is None:
            return {'CANCELLED'}

        try:
            version = (Model3doFileFormat.GrimFandango if self.version == Model3doFileFormat.GrimFandango.name
                       else Model3doFileVersion[self.version])
            if version not in (Model3doFileVersion.Version2_1, Model3doFileFormat.GrimFandango):
                self.absolute_uv = False
            # Hidden options keep their values; pass only those that the dialog shows.
            # Scripted stand-ins may predate these options and use their defaults.
            editions = getattr(self, 'texture_editions', False) and version != Model3doFileFormat.GrimFandango
            indexed = editions or self.texture_encoding == 'INDEXED'
            dither = getattr(self, 'texture_dither', False) and indexed
            avoidFullbright = getattr(self, 'texture_avoid_fullbright', False) and indexed
            warnings = []
            export3do(self.obj, self.filepath, version, self.absolute_uv, self.export_vert_colors, self.sync_mesh_list,
                      bakePivots=self.bake_pivots,
                      warnings=warnings, exportTextures=self.export_textures, textureEncoding=self.texture_encoding,
                      texturePalette=self.texture_cmp_file, overwriteTextures=self.overwrite_textures,
                      generateMipmaps=self.texture_mipmaps, textureDirectory=self.texture_directory,
                      detailLevels=getattr(self, 'detail_levels', 1),
                      failOverBudget=getattr(self, 'fail_over_budget', False),
                      textureDither=dither, textureEditions=editions,
                      textureDirectory8bit=getattr(self, 'texture_directory_8bit', 'mat8'),
                      textureDirectory16bit=getattr(self, 'texture_directory_16bit', 'mat16'),
                      textureAvoidFullbright=avoidFullbright)
        except (AssertionError, ImportError, ValueError, OSError, RuntimeError) as e:
            print(f"\nAn exception was encountered while exporting object '{self.obj.name}' to 3DO format!\nError: {e}")
            self.report({'ERROR'}, f'Error: {e}')
            return {'CANCELLED'}

        self.report({'INFO'}, f"3DO model '{os.path.basename(self.filepath)}' was successfully exported")
        for warning in warnings:
            self.report({'WARNING'}, warning)
        return {'FINISHED'}


# TODO: add option to load model first
class ImportKey(bpy.types.Operator, ImportHelper):
    """Import Sith game engine animation (.key)"""
    bl_idname    = 'import_anim.sith_key'
    bl_label     = 'Import KEY'
    filename_ext = '.key'

    filter_glob: bpy.props.StringProperty(
        default = '*.key',
        options = {'HIDDEN'}
    )

    validate_active_object: bpy.props.BoolProperty(
        name        = 'Validate',
        description = 'Validate that the active object has all the required animating nodes before importing the KEY animation.',
        default     = True,
    )

    clear_scene: bpy.props.BoolProperty(
        name        = 'Clear scene',
        description = 'Clear any existing animation data from the scene before importing KEY animation.',
        default     = True,
    )

    named_markers: bpy.props.BoolProperty(
        name        = 'Import markers by name',
        description = 'Import frame markers by name rather than by number.',
        default     = False,
    )

    require_frame_zero: bpy.props.BoolProperty(
        name        = 'Require Frame Zero',
        description = 'Require each animation track to start at frame zero. Disable to import late-start tracks, extrapolating their active deltas back to frame zero. Data-integrity checks remain enabled',
        default     = True,
    )

    allow_errors: bpy.props.BoolProperty(
        name        = 'Try Recover Broken File',
        description = 'Try importing complete valid tracks from a damaged KEY, omitting corrupt tracks with a report. Does not repair unsafe headers or truncated binary records, or disable Require Frame Zero',
        default     = False,
    )

    def draw(self, context):
        for name in ('validate_active_object', 'clear_scene', 'named_markers', 'require_frame_zero', 'allow_errors'):
            self.layout.prop(self, name)

    def execute(self, context):
        # Validate filepath
        if not self.filepath or not os.path.isfile(self.filepath):
            self.report({'ERROR'}, 'No file selected or file does not exist!')
            return {'CANCELLED'}
        if not self.filepath.lower().endswith('.key'):
            self.report({'ERROR'}, 'Selected file is not a .key file!')
            return {'CANCELLED'}

        try:
            scene = context.scene
            warnings = []
            obj = importKey(self.filepath, scene, self.clear_scene, self.validate_active_object, self.named_markers,
                            allowErrors=self.allow_errors, requireFrameZero=self.require_frame_zero, warnings=warnings)
        except Exception as e:
            print(f"\nError: An exception was encountered while importing keyframe '{os.path.basename(self.filepath)}'!\nError: {e}")
            self.report({'ERROR'}, f'Error: {e}')
            return {'CANCELLED'}
        if warnings:
            warningCount = len(warnings)
            self.report({'WARNING'}, f'Recovery warnings: {warningCount}. View KEY Import Report in 3DO Properties for details')
            if not bpy.app.background and context.window is not None and kKeyImportReport in obj:
                reportName = obj[kKeyImportReport]
                def draw_recovery_notice(menu, context):
                    menu.layout.label(text=f'Recovery warnings: {warningCount}')
                    menu.layout.operator(View3doImportReport.bl_idname, icon='TEXT').report_name = reportName
                context.window_manager.popup_menu(draw_recovery_notice, title='KEY Recovery', icon='ERROR')
        return {'FINISHED'}

class ExportKey(bpy.types.Operator, ExportHelper):
    """Export animation to Sith game engine KEY file format (.key)"""
    bl_idname    = 'export_anim.sith_key'
    bl_label     = 'Export KEY'
    filename_ext = '.key'

    filter_glob: bpy.props.StringProperty(
        default = '*.key',
        options = {'HIDDEN'}
    )

    target: bpy.props.EnumProperty(name='Target', items=kKeyTargets, default='IJIM')

    flags: bpy.props.EnumProperty(
        name        = 'Flags',
        description = 'Stored KEY header flags, exposed for format completeness. Original Sith playback uses PUP/script flags instead; Grim has different flag meanings',
        items       = _get_key_flags_enum_list(),
        options     = {'ENUM_FLAG'}
    )

    node_types: HexProperty(
        'node_types',
        name        = 'High Priority Node(s)',
        description = 'Hexadecimal node-type mask, not joint numbers. A joint whose 3DO Type shares any bit with this mask uses the track high priority; other joints use its low priority. PUP/playback settings supply those priorities. FFFF selects all standard type bits; 0 selects none',
        default     = '0xFFFF',
        pad         = True
    )

    fps: bpy.props.FloatProperty(
        name        = 'Frame Rate',
        description = 'Playback frames per second, including fractional rates',
        default     = 30.0,
        min         = 0.0,
        precision   = 6
    )

    obj   = None

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'target')
        layout.prop_menu_enum(self, 'flags')
        types_layout = layout.box().column()
        types_layout.label(text='High Priority Node(s)')
        types_layout.prop(self, 'node_types', text='')
        layout.prop(self, 'fps')

    def invoke(self, context, event):
        self.flags       = context.scene.sith_key_flags
        self.node_types  = context.scene.sith_key_types
        self.fps         = getSceneFrameRate(context.scene)

        self.obj = _get_export_obj(context, self.report, 'animation')
        if self.obj is None:
            return {'CANCELLED'}
        kfname        = bpy.path.display_name_from_filepath(self.obj.name )
        self.filepath = bpy.path.ensure_ext(kfname, self.filename_ext)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        # Validate filepath
        if not self.filepath:
            self.report({'ERROR'}, 'No file path specified!')
            return {'CANCELLED'}
        if not self.filepath.lower().endswith('.key'):
            self.filepath = bpy.path.ensure_ext(self.filepath, self.filename_ext)

        if self.obj is None:
            self.obj = _get_export_obj(context, self.report, 'animation')
        if self.obj is None:
            return {'CANCELLED'}

        try:
            # Build with dialog overrides, without changing scene playback or
            # flags until validation and writing have both succeeded.
            scene = context.scene
            sceneFps = getSceneFrameRate(scene)
            fps = self.fps if self.properties.is_property_set('fps') else sceneFps
            if math.isclose(fps, sceneFps, rel_tol=1e-7):
                fps = sceneFps  # Preserve rates rounded by Blender's float property.
            prepareSceneFrameRate(scene, fps)
            flags = self.flags if self.properties.is_property_set('flags') else scene.sith_key_flags
            # The custom getter returns a default without storing it. Only a
            # value written by the setter is an explicit dialog/script override.
            nodeTypes = self.node_types if 'node_types' in self.properties else scene.sith_key_types
            exportKey(self.obj, scene, self.filepath, target=self.target, fps=fps, flags=flags, nodeTypes=nodeTypes)
        except (AssertionError, ValueError, OSError, RuntimeError) as e:
            print(f"\nAn exception was encountered while exporting animation data of object '{self.obj.name}' to KEY file format!\nError: {e}")
            self.report({'ERROR'}, f'Error: {e}')
            return {'CANCELLED'}

        # Linked scenes may accept transient RNA writes that disappear on reopen.
        # Their overrides belong only to the KEY; local scenes can remember them.
        if scene.is_editable:
            scene.sith_key_flags = flags
            scene.sith_key_types = nodeTypes
            if fps != sceneFps:
                setSceneFrameRate(scene, fps)
        self.report({'INFO'}, f"KEY '{os.path.basename(self.filepath)}' was successfully exported")
        return {'FINISHED'}


class View3doImportReport(bpy.types.Operator):
    """Open the saved recovery report in a separate Blender window"""
    bl_idname = 'wm.sith_view_3do_import_report'
    bl_label  = 'View Import Report'

    report_name: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        report = bpy.data.texts.get(self.report_name)
        if report is None:
            self.report({'ERROR'}, 'The import report is no longer available')
            return {'CANCELLED'}
        if bpy.app.background or context.window is None or context.area is None:
            self.report({'WARNING'}, 'Open the import report from Blender\'s graphical interface')
            return {'CANCELLED'}

        windows = set(context.window_manager.windows)
        try:
            # Duplicate a large editor, not the narrow Properties panel button.
            sourceArea = max(context.window.screen.areas, key=lambda area: area.width * area.height)
            sourceRegion = next(region for region in sourceArea.regions if region.type == 'WINDOW')
            with context.temp_override(area=sourceArea, region=sourceRegion):
                bpy.ops.screen.area_dupli('INVOKE_DEFAULT')
        except RuntimeError as error:
            self.report({'ERROR'}, f'Cannot open the import report window: {error}')
            return {'CANCELLED'}
        window = next((window for window in context.window_manager.windows if window not in windows), None)
        if window is None:
            self.report({'ERROR'}, 'Cannot create the import report window')
            return {'CANCELLED'}

        area = max(window.screen.areas, key=lambda area: area.width * area.height)
        area.type = 'TEXT_EDITOR'
        area.spaces.active.text = report
        area.spaces.active.show_word_wrap = True
        report.cursor_set(0)
        area.spaces.active.top = 0
        return {'FINISHED'}


class Toggle3doPivotHelper(bpy.types.Operator):
    """Show this joint's native pivot as a movable helper, or remove the helper and keep its current value"""
    bl_idname  = 'object.sith_toggle_pivot_helper'
    bl_label   = 'Toggle Pivot Helper'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type in ('EMPTY', 'MESH') and not isPivotHelperObj(obj)

    def execute(self, context):
        obj = context.object
        try:
            if getPivotHelperObj(obj) is None:
                makePivotHelper(obj)
            else:
                removePivotHelper(obj)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class Model3doPanel(bpy.types.Panel):
    """
    Panel exposes 3DO mesh properties to the UI.
    i.e.: light mode, texture mode & hierarchy node properties.
    """
    bl_idname      = 'OBJECT_PT_model_3do_panel'
    bl_label       = '3DO Properties'
    bl_description = '3DO model object properties'
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'object'
    bl_options     = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return (context.object is not None) and \
            ('EMPTY' == context.object.type or context.object.type == 'MESH')

    def draw(self, context):
        obj    = context.object
        layout = self.layout

        root = obj
        while root is not None and kModel3doImportReport not in root:
            root = root.parent
        if root is not None:
            recovery = layout.box()
            recovery.alert = True
            recovery.label(text='Imported with errors', icon='ERROR')
            recovery.operator(View3doImportReport.bl_idname, icon='TEXT').report_name = root[kModel3doImportReport]

        root = obj
        while root is not None and kKeyImportReport not in root:
            root = root.parent
        if root is not None:
            recovery = layout.box()
            recovery.alert = True
            recovery.label(text='KEY imported with errors', icon='ERROR')
            recovery.operator(View3doImportReport.bl_idname, text='View KEY Import Report', icon='TEXT').report_name = root[kKeyImportReport]

        mesh_properties = layout.box()
        mesh_properties.label(text='Mesh Properties')
        mesh_properties.prop(obj, kModel3doGeometryMode, text='Geometry')
        mesh_properties.prop(obj, 'sith_model3do_light_mode'  , text='Lighting')
        mesh_properties.prop(obj, 'sith_model3do_texture_mode', text='Texture')
        if obj.type == 'MESH':
            mesh_properties.prop(obj, kModel3doShadow, text='Shadow (Grim Fandango)')

        preview = layout.box()
        preview.label(text='Preview')
        preview.prop(context.scene, model3doPreview.kPreviewProperty, text='Extra Light')

        node_properties = layout.box()
        node_properties.label(text='Hierarchy Node Properties')
        node_properties.prop(obj, 'sith_model3do_hnode_idx'  , text='Sequence no.')
        node_properties.prop(obj, 'sith_model3do_hnode_name' , text='Name')
        node_properties.prop(obj, 'sith_model3do_hnode_flags', text='Flags')
        node_properties.prop(obj, 'sith_model3do_hnode_type' , text='Type')
        pivot = node_properties.column()
        pivot.enabled = not any(c.type == 'PIVOT' and not c.mute and c.influence for c in obj.constraints)
        pivot.prop(obj, 'sith_model3do_pivot', text='Native Pivot')
        try:
            helper = getPivotHelperObj(obj)
        except ValueError:
            helper = None
        pivot.operator(Toggle3doPivotHelper.bl_idname, icon='EMPTY_ARROWS',
                       text='Remove Pivot Helper' if helper is not None else 'Show Pivot Helper')
        if not pivot.enabled:
            node_properties.label(text='Blender Pivot constraint overrides Native Pivot')


# -- Sith Audits panel: native model and KEY audits (sith.audit) in Blender --
# The operators read the scene or files and write reports to Text datablocks. None of
# them changes model data; Colour Pieces changes only viewport object colours and the
# 3D View's colour mode, and records them so that Restore Colours puts them back.
# Reseat KEYs is the one that writes files: new KEYs, only into the folder the user
# chooses, never into the KEY folder and never over an existing file.

kAuditReportText = 'sith_audit_report'      # on Text datablocks these operators write: the report name
kSavedObjectColor = '_sith_saved_color'     # an object's colour before Colour Pieces
kSavedColorType = '_sith_saved_color_type'  # the 3D View colour mode before Colour Pieces (on the scene)


def _audit_text(name: str, content: str) -> bpy.types.Text:
    """Write a report to the Text this operator made for ``name`` before, else to a new one.

    A text the user made is never overwritten, even when it has the report's name.
    """
    text = next((t for t in bpy.data.texts if t.library is None and t.get(kAuditReportText) == name), None)
    if text is None:
        text = bpy.data.texts.new(name)
        text[kAuditReportText] = name
        text.use_fake_user = True
    text.from_string(content)
    return text


def _audit_document(title: str, lines, report) -> str:
    """A short readable summary, then the complete plain-data report as JSON."""
    return '\n'.join([title, '=' * len(title), *lines, '', 'Complete report (plain data):',
                      json.dumps(report, indent=1, default=str)]) + '\n'


def _srgb_to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _view3d_space(context):
    """The 3D View the operator runs in, else the first one on the screen, else None."""
    space = getattr(context, 'space_data', None)
    if space is not None and space.type == 'VIEW_3D':
        return space
    screen = getattr(context, 'screen', None)
    for area in (screen.areas if screen is not None else ()):
        if area.type == 'VIEW_3D':
            return area.spaces.active
    return None


class CheckRetailLimits(bpy.types.Operator):
    """Count the active model as the 3DO exporter builds it, against the retail JKDF2/MOTS limits. Writes no file and does not change the model; the report goes to a Text datablock"""
    bl_idname  = 'sith.check_retail_limits'
    bl_label   = 'Check Retail Limits'
    bl_options = {'REGISTER'}

    target: bpy.props.EnumProperty(
        name        = 'Target',
        description = 'Game whose retail limits apply. JKDF2 and MOTS load models with the same limits',
        items       = [('JKDF2', 'JKDF2', 'Jedi Knight: Dark Forces II'),
                       ('MOTS', 'MOTS', 'Mysteries of the Sith')],
        default     = 'JKDF2',
    )
    absolute_uv: bpy.props.BoolProperty(
        name        = 'v2.1 Absolute UV',
        description = 'As in the 3DO export dialog: count the pixel-space UVs a 2.1 export writes',
        default     = True,
    )
    export_vert_colors: bpy.props.BoolProperty(
        name        = 'Export Vertex Colors',
        description = 'As in the 3DO export dialog. Corners with different colors stay separate vertices, so this can add vertices',
        default     = False,
    )
    sync_mesh_list: bpy.props.BoolProperty(
        name        = 'Sync Mesh List with Node Hierarchy',
        description = 'As in the 3DO export dialog: number meshes in node order, so mesh numbers in the report match the export',
        default     = True,
    )
    bake_pivots: bpy.props.BoolProperty(
        name        = 'Bake Pivots',
        description = 'As in the 3DO export dialog: count the model with zero pivots and baked vertices',
        default     = False,
    )
    detail_levels: bpy.props.IntProperty(
        name        = 'Detail Levels',
        description = 'As in the 3DO export dialog: geosets to count, 1 to 4, each a copy of the model',
        default     = 1,
        min         = 1,
        max         = 4,
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        for name in ('target', 'absolute_uv', 'export_vert_colors', 'sync_mesh_list', 'bake_pivots', 'detail_levels'):
            self.layout.prop(self, name)

    def execute(self, context):
        root = _get_export_obj(context, self.report, 'mesh')
        if root is None:
            return {'CANCELLED'}
        from .model import setDetailLevels
        from .model.model3doExporter import makeModel3doFromObj
        from .audit.budget import auditBudget, budgetWarnings
        try:
            # The same steps as export3do before it writes: build, then copy geoset 0.
            model = makeModel3doFromObj(root.name, root, uvAbsolute=self.absolute_uv,
                                        exportVertexColors=self.export_vert_colors,
                                        sync_mesh_list=self.sync_mesh_list, bakePivots=self.bake_pivots)
            if not model.meshHierarchy:
                raise ValueError('No exportable joints were found; select a model hierarchy, not only radius helpers.')
            setDetailLevels(model, self.detail_levels)
            report = auditBudget(model, self.target)
        except (AssertionError, ImportError, ValueError, OSError, RuntimeError) as error:
            self.report({'ERROR'}, f'Cannot check the retail limits: {error}')
            return {'CANCELLED'}

        warnings = budgetWarnings(report)
        geoset = report['geosets'][0] if report['geosets'] else None
        largest = {key: (geoset['largest'][key] or {}).get('count', 0) if geoset else 0
                   for key in ('vertices', 'texture_vertices', 'faces')}
        lines = [f"Model: {root.name}", f"Target: {self.target}", f"Status: {report['status']}",
                 f"Geosets: {report['geoset_count']}; hierarchy nodes: {report['hierarchy_nodes']}; "
                 f"materials: {report['materials']}",
                 f"Largest mesh: {largest['vertices']} vertices, {largest['texture_vertices']} texture vertices, "
                 f"{largest['faces']} faces",
                 f"Options: v2.1 Absolute UV {self.absolute_uv}, Export Vertex Colors {self.export_vert_colors}, "
                 f"Sync Mesh List {self.sync_mesh_list}, Bake Pivots {self.bake_pivots}, "
                 f"Detail Levels {self.detail_levels}", ''] + (warnings or ['No findings.'])
        text = _audit_text(f'{root.name} - Retail Limits', _audit_document('Retail limits', lines, report))
        if report['status'] == 'within_limits':
            self.report({'INFO'}, f"Within the {self.target} retail limits; largest mesh {largest['vertices']} "
                                  f"vertices, {largest['faces']} faces. Report: '{text.name}'")
        else:
            for line in warnings:
                self.report({'WARNING'}, line)
            self.report({'WARNING'}, f"Over the {self.target} retail limits: "
                                     f"{sum(report['issue_counts'].values())} findings. Report: '{text.name}'")
        return {'FINISHED'}


class ColourPieces(bpy.types.Operator):
    """Paint each piece of the active model one solid colour in the viewport, to see which piece a lump of surface belongs to. Changes only viewport object colours and the 3D View's colour mode, and records them for Restore Colours"""
    bl_idname  = 'sith.colour_pieces'
    bl_label   = 'Colour Pieces'
    bl_options = {'REGISTER', 'UNDO'}

    by: bpy.props.EnumProperty(
        name        = 'Colour By',
        description = 'Which pieces get their own colour',
        items       = [('NODE', 'Node', 'One colour per hierarchy node, by its exported node number'),
                       ('PART', 'Rigid Part', "One colour per rigid part: the nodes that move with each of Kyle's joints")],
        default     = 'NODE',
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        root = _get_export_obj(context, self.report, 'model')
        if root is None:
            return {'CANCELLED'}
        from .model.model3doExporter import makeModel3doFromObj
        from .audit.segmentation import pieceColours
        try:
            # Exported node numbers, so colours match pieceColours on the exported 3DO.
            model = makeModel3doFromObj(root.name, root, hierarchyOnly=True)
            colours = pieceColours(model, by=self.by.lower())
        except (AssertionError, ValueError, RuntimeError) as error:
            self.report({'ERROR'}, f'Cannot colour the pieces: {error}')
            return {'CANCELLED'}
        painted = 0
        for node, entry in zip(model.meshHierarchy, colours):
            obj = node.obj
            if obj is None or obj.type != 'MESH' or not obj.is_editable:
                continue
            if kSavedObjectColor not in obj:
                obj[kSavedObjectColor] = list(obj.color)
            obj.color = (*(_srgb_to_linear(c) for c in entry['rgb']), 1.0)
            painted += 1
        space = _view3d_space(context)
        if space is not None and space.shading.color_type != 'OBJECT':
            if kSavedColorType not in context.scene:
                context.scene[kSavedColorType] = space.shading.color_type
            space.shading.color_type = 'OBJECT'
        self.report({'INFO'}, f"Coloured {painted} meshes by {'node' if self.by == 'NODE' else 'rigid part'}. "
                              'Restore Colours puts the previous colours back')
        if space is not None and space.shading.type != 'SOLID':
            self.report({'INFO'}, 'Object colours show in Solid shading')
        return {'FINISHED'}


class RestoreColours(bpy.types.Operator):
    """Put back the object colours and the 3D View colour mode that Colour Pieces changed"""
    bl_idname  = 'sith.restore_colours'
    bl_label   = 'Restore Colours'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        restored = 0
        for obj in bpy.data.objects:
            saved = obj.get(kSavedObjectColor)
            if saved is None or not obj.is_editable:
                continue
            obj.color = tuple(saved)
            del obj[kSavedObjectColor]
            restored += 1
        mode = context.scene.get(kSavedColorType)
        if mode is not None:
            space = _view3d_space(context)
            # Leave a colour mode the user has changed since; put back only what Colour Pieces set.
            if space is not None and space.shading.color_type == 'OBJECT':
                try:
                    space.shading.color_type = mode
                except TypeError:
                    pass
            del context.scene[kSavedColorType]
        if not restored and mode is None:
            self.report({'INFO'}, 'No colours to restore')
            return {'CANCELLED'}
        self.report({'INFO'}, f'Restored the colours of {restored} objects')
        return {'FINISHED'}


def _audit_key_paths(folder: Path, names: str):
    """Regular .key files in ``folder`` by name, sorted without case; ``names`` selects some.

    Names are compared without case, as the engine finds KEYs. When two files share a selected
    name in different case (or, with no selection, any name), either could be the KEY meant, so
    the folder is refused, as the MCP server refuses it.
    """
    available = {}
    for path in folder.iterdir():
        if path.is_file() and path.suffix.casefold() == '.key':
            available.setdefault(path.name.casefold(), []).append(path)
    wanted = list(available)
    if names.strip():
        wanted = []
        for name in (part.strip() for part in names.split(',')):
            if not name:
                continue
            if Path(name).name != name or name.casefold() not in available:
                raise ValueError(f"The KEY folder has no file named '{name}'")
            if name.casefold() not in wanted:
                wanted.append(name.casefold())
    for name in wanted:
        if len(available[name]) > 1:
            raise ValueError('KEY names differ only in case: ' + ', '.join(sorted(path.name for path in available[name])))
    return sorted((available[name][0] for name in wanted), key=lambda path: (path.name.casefold(), path.name))


class AuditFiles(bpy.types.Operator, ImportHelper):
    """Audit a 3DO file with the KEYs in a folder: joint gaps, seat offsets, floor contact and KEY lint, as selected. Reads the files only and writes each report to a Text datablock. A full joint-gap sweep can take minutes (about 80 seconds for 91 KEYs on a 14,600-face model), and Blender waits until it ends"""
    bl_idname    = 'sith.audit_files'
    bl_label     = 'Audit Files'
    filename_ext = '.3do'

    filter_glob: bpy.props.StringProperty(
        default = '*.3do',
        options = {'HIDDEN'}
    )
    key_directory: bpy.props.StringProperty(
        name        = 'KEY Folder',
        description = "Folder of the KEYs to audit. Blank uses the 'key' folder beside the 3DO",
        subtype     = 'DIR_PATH',
    )
    key_files: bpy.props.StringProperty(
        name        = 'KEY Files',
        description = 'Comma-separated KEY file names in the KEY Folder, for a shorter run. Blank audits every .key file in it',
    )
    step: bpy.props.FloatProperty(
        name        = 'Step',
        description = 'Sample each KEY every Step frames from frame 0. A larger step is quicker',
        default     = 2.0,
        min         = 0.25,
        max         = 100.0,
    )
    joint_gaps: bpy.props.BoolProperty(
        name        = 'Joint Gaps',
        description = 'Lines of sight through each joint in every sampled pose (sith.audit.articulation). The slowest check',
        default     = True,
    )
    seat_offsets: bpy.props.BoolProperty(
        name        = 'Seat Offsets',
        description = "How far each KEY moves each node from its seat in the reference pose, the usual cause of an opening joint",
        default     = True,
    )
    floor_contact: bpy.props.BoolProperty(
        name        = 'Floor Contact',
        description = "Samples where the model's lowest point is under the engine floor or the feet float above it (sith.audit.ground)",
        default     = True,
    )
    key_lint: bpy.props.BoolProperty(
        name        = 'KEY Lint',
        description = 'Header, track table and pops between entries of each KEY, read the way the engine reads them (sith.audit.keylint)',
        default     = True,
    )
    reference: bpy.props.EnumProperty(
        name        = 'Reference Pose',
        description = 'The pose the pieces were cut in. Joint gaps and seat offsets measure from it, and a wrong one gives no warning',
        items       = [('REST', 'Rest Pose', 'The pose stored in the 3DO'),
                       ('KEY', 'Reference KEY', 'The nodes a KEY drives at its frame; every other node at rest')],
        default     = 'REST',
    )
    reference_key: bpy.props.StringProperty(
        name        = 'Reference KEY File',
        description = "A file name in the KEY Folder or a path. For Red's Kyle-skeleton characters use the stand KEY they were cut against with Zero Rotations (stock kystand1 for Darth Lilitari)",
        default     = 'kystand1.key',
        subtype     = 'FILE_PATH',
    )
    reference_frame: bpy.props.FloatProperty(
        name        = 'Reference Frame',
        description = 'Frame of the reference KEY',
        default     = 0.0,
        min         = 0.0,
    )
    zero_rotations: bpy.props.BoolProperty(
        name        = 'Zero Rotations',
        description = "Give the nodes the reference KEY drives zero rotation: the pose characters cut with straight joints at the stand KEY's seats were built in",
        default     = True,
    )
    move_size: bpy.props.FloatProperty(
        name        = 'Move Size',
        description = "The player template's movesize, which sets the engine floor (stock walkplayer: 0.065)",
        default     = 0.065,
        min         = 0.0,
        precision   = 4,
    )
    crouch_keys: bpy.props.StringProperty(
        name        = 'Crouch KEYs',
        description = 'Comma-separated KEY file names that play on the crouch floor. Names not in the run are ignored',
        default     = 'kycrchgn.key, kycrchbk.key',
    )
    lint_target: bpy.props.EnumProperty(
        name        = 'Lint Target',
        description = 'Game whose KEY limits the lint applies',
        items       = [('JKDF2', 'JKDF2', 'Jedi Knight: Dark Forces II'),
                       ('MOTS', 'MOTS', 'Mysteries of the Sith')],
        default     = 'JKDF2',
    )

    def draw(self, context):
        layout = self.layout
        keys = layout.box().column()
        keys.label(text='KEYs')
        keys.prop(self, 'key_directory')
        keys.prop(self, 'key_files')
        keys.prop(self, 'step')
        checks = layout.box().column()
        checks.label(text='Checks')
        for name in ('joint_gaps', 'seat_offsets', 'floor_contact', 'key_lint'):
            checks.prop(self, name)
        reference = layout.box().column()
        reference.prop(self, 'reference')
        if self.reference == 'KEY':
            reference.prop(self, 'reference_key')
            reference.prop(self, 'reference_frame')
            reference.prop(self, 'zero_rotations')
        if self.floor_contact:
            floor = layout.box().column()
            floor.prop(self, 'move_size')
            floor.prop(self, 'crouch_keys')
        if self.key_lint:
            layout.prop(self, 'lint_target')

    def execute(self, context):
        if not self.filepath or not os.path.isfile(self.filepath) or not self.filepath.lower().endswith('.3do'):
            self.report({'ERROR'}, 'Select an existing .3do file')
            return {'CANCELLED'}
        if not (self.joint_gaps or self.seat_offsets or self.floor_contact or self.key_lint):
            self.report({'ERROR'}, 'Select at least one check')
            return {'CANCELLED'}
        try:
            reports = self._audit(context, Path(self.filepath))
        except (AssertionError, ValueError, OSError, EOFError, TypeError) as error:
            self.report({'ERROR'}, f'Cannot audit the files: {error}')
            return {'CANCELLED'}
        summary = ', '.join(f'{label}: {status}' for label, status, _ in reports)
        names = ', '.join(f"'{text}'" for _, _, text in reports)
        self.report({'INFO'}, f'{summary}. Reports: {names}')
        return {'FINISHED'}

    def _audit(self, context, modelPath: Path):
        from .audit import articulation, ground, keylint
        from .audit.pose import keyFrameTimes
        from .key.keyLoader import loadKey
        from .model.model3doLoader import load3do

        folder = Path(bpy.path.abspath(self.key_directory)) if self.key_directory else modelPath.parent / 'key'
        if not folder.is_dir():
            raise ValueError(f'KEY folder not found: {folder}')
        paths = _audit_key_paths(folder, self.key_files)
        if not paths:
            raise ValueError(f'The KEY folder has no .key files: {folder}')
        model, _ = load3do(modelPath)
        if not model.meshHierarchy or not model.geosets:
            raise ValueError('The 3DO has no hierarchy or geometry to audit')
        keys, skipped = {}, []
        for path in paths:
            try:
                keys[path.name] = loadKey(path)
            except Exception as error:  # one unreadable KEY is reported, not fatal (as loadKeyFolder)
                skipped.append(f'{path.name}: {error}')
        reference = None
        if self.reference == 'KEY':
            refPath = Path(bpy.path.abspath(self.reference_key)) if self.reference_key else None
            if refPath is None or not self.reference_key.strip():
                raise ValueError('Give the Reference KEY File')
            if not refPath.is_absolute():
                refPath = folder / self.reference_key
            standKey = loadKey(refPath)
            reference = articulation.referencePose(model, standKey, self.reference_frame,
                                                   zeroRotations=self.zero_rotations)
        inputs = [f'3DO: {modelPath}', f'KEY folder: {folder}',
                  f'KEYs: {len(keys)} read' + (f', {len(skipped)} could not be read' if skipped else ''),
                  *(f'  not read: {line}' for line in skipped),
                  f"Reference: {reference.description if reference is not None else 'rest pose'}",
                  f'Step: {self.step:g} frames', '']
        name = modelPath.name
        results = []

        def finish(label, report, lines):
            text = _audit_text(f'{name} - {label}', _audit_document(label, inputs + lines, report))
            results.append((label, report['status'], text.name))

        if (self.joint_gaps or self.seat_offsets or self.floor_contact) and not keys:
            raise ValueError('No KEY could be read: ' + '; '.join(skipped[:3]))
        wm = context.window_manager
        if self.joint_gaps:
            total = sum(len(keyFrameTimes(key, self.step)) for key in keys.values())
            done = [0]

            def onPose(keyName, frame, joints):
                done[0] += 1
                wm.progress_update(done[0])

            wm.progress_begin(0, max(total, 1))
            try:
                report = articulation.auditArticulation(model, keys, step=self.step, reference=reference, onPose=onPose)
            finally:
                wm.progress_end()
            diagnostic = report['reference_diagnostic']
            lines = [f"Status: {report['status']}", f"Totals: {report['totals']}",
                     f"Reference: {diagnostic['frames_different_from_rest']} of {diagnostic['node_count']} "
                     "joint frames differ from rest.", diagnostic['advice'], '']
            for f in report['findings']:
                if f['code'] == 'daylight':
                    lines.append(f"{f['joint']}: {f['key']} frame {f['frame']:g}, {f['lines']} lines, "
                                 f"separation {f['separation']:.5f}")
                else:
                    lines.append(f"{f['joint']}: not measured ({f['reason']})")
            finish('Joint Gaps', report, lines + ['', report['reading']])
        if self.seat_offsets:
            report = articulation.seatOffsets(model, keys, reference=reference)
            lines = [f"Status: {report['status']}", '']
            lines += [f"{f['key']}: {f['name']} {f['offset']:.6f} at frame {f['frame']:g}" for f in report['findings']]
            finish('Seat Offsets', report, lines)
        if self.floor_contact:
            standing = ground.thingFloorHeight(model, self.move_size)
            crouching = ground.thingFloorHeight(model, self.move_size, crouching=True)
            crouch = {part.strip().casefold() for part in self.crouch_keys.split(',') if part.strip()}
            sequences = [ground.keySequence(key, self.step, name=keyName,
                                            floor=crouching if keyName.casefold() in crouch else None)
                         for keyName, key in keys.items()]
            report = ground.auditFloorContact(model, sequences, floor=standing, step=self.step)
            lines = [f"Status: {report['status']}", f'Floor: z = {standing:.6f}; crouch floor: z = {crouching:.6f}', '']
            for f in report['findings']:
                size = f.get('depth', f.get('height'))
                lines.append(f"{f['sequence']}: {f['code']} at {f['time']:g}" + (f', {size:.5f}' if size is not None else ''))
            finish('Floor Contact', report, lines)
        if self.key_lint:
            # Lint reads KEYs the way the engine does, keeping tracks the strict loader refuses.
            order = {'incompatible': 0, 'review': 1, 'consistent': 2, 'no_tracks': 3}
            rows, perKey, statuses = [], {}, []
            for path in paths:
                try:
                    key = loadKey(path, strictTracks=False)
                except Exception as error:  # an unreadable KEY is reported, not fatal
                    rows.append(f'{path.name}: review, could not be read ({error})')
                    perKey[path.name] = {'error': str(error)}
                    statuses.append('review')
                    continue
                header = keylint.lintKeyHeader(key, model, target=self.lint_target)
                try:
                    pops = keylint.scanKeyPops(key, model)
                    popStatus = pops['status']
                except ValueError as error:
                    pops = {'status': 'not_scanned', 'reason': str(error), 'issue_counts': {}, 'findings': []}
                    popStatus = 'review'
                status = min(header['status'], popStatus, key=order.get)
                statuses.append(status)
                perKey[path.name] = {'header': header, 'pops': pops}
                if status != 'consistent':
                    counts = {**header['issue_counts'], **(header['binding'] or {}).get('issue_counts', {}),
                              **pops['issue_counts']}
                    rows.append(f'{path.name}: {status} {counts}')
            overall = ('incompatible' if 'incompatible' in statuses else 'review' if 'review' in statuses else
                       'no_tracks' if statuses and all(s == 'no_tracks' for s in statuses) else 'consistent')
            report = {'status': overall, 'target': self.lint_target, 'keys': perKey}
            finish('KEY Lint', report, [f'Status: {overall}', ''] + (rows or ['No findings.']))
        return results


def _reseat_output_folder(value: str, keyFolder: Path, paths) -> Path:
    """The Output Folder for reseated KEYs, refused where writing there could replace a file.

    It must be an absolute path that is not the KEY folder and holds no entry named like one of the
    KEYs to reseat. Names are compared without case, as the engine and Windows compare them.
    """
    if not value.strip():
        raise ValueError('Choose an Output Folder for the reseated KEYs')
    folder = Path(bpy.path.abspath(value))
    if not folder.is_absolute():
        raise ValueError(f'The Output Folder must be an absolute path: {value}')
    if folder.exists() and not folder.is_dir():
        raise ValueError(f'The Output Folder is a file: {folder}')
    if folder.resolve() == keyFolder.resolve() or (folder.is_dir() and folder.samefile(keyFolder)):
        raise ValueError('The Output Folder is the KEY folder; choose another folder')
    if folder.is_dir():
        present = {entry.name.casefold() for entry in folder.iterdir()}
        clash = [path.name for path in paths if path.name.casefold() in present]
        if clash:
            raise ValueError(f"The Output Folder already holds {', '.join(clash[:3])}"
                             f"{' and others' if len(clash) > 3 else ''}; Reseat KEYs never replaces a file")
    return folder


class ReseatKeys(bpy.types.Operator, ImportHelper):
    """Reseat a 3DO file's KEYs (sith.key.keyReseat): the hip piece and the seated joints go back on their seats and no rotation changes. Reseated copies are written only for KEYs that change, only into the Output Folder; nothing is written into the KEY folder and no file is replaced. Reseating stock KEYs changes them for every model in that install. Run the joint-gap audit before and after on the same KEYs and step. Kyle's hip layout only; run it before adding custom tracks that move the hip's frames"""
    bl_idname    = 'sith.reseat_keys'
    bl_label     = 'Reseat KEYs'
    filename_ext = '.3do'

    filter_glob: bpy.props.StringProperty(
        default = '*.3do',
        options = {'HIDDEN'}
    )
    key_directory: bpy.props.StringProperty(
        name        = 'KEY Folder',
        description = "Folder of the KEYs to reseat. Blank uses the 'key' folder beside the 3DO. Nothing is written into it",
        subtype     = 'DIR_PATH',
    )
    key_files: bpy.props.StringProperty(
        name        = 'KEY Files',
        description = 'Comma-separated KEY file names in the KEY Folder. Blank reseats every .key file in it',
    )
    seats: bpy.props.EnumProperty(
        name        = 'Seats',
        description = "Where each joint's seat comes from",
        items       = [('KEY', 'Stand KEY', 'The positions a stand KEY holds at its frame: kystand1.key at frame 0 for Darth Lilitari R9.10 and Lilivane R3'),
                       ('REST', 'Model Rest Positions', "The model's rest positions. Use them only when the rest pose matches the stand KEY")],
        default     = 'KEY',
    )
    stand_key: bpy.props.StringProperty(
        name        = 'Stand KEY File',
        description = 'A file name in the KEY Folder or a path',
        default     = 'kystand1.key',
        subtype     = 'FILE_PATH',
    )
    stand_frame: bpy.props.FloatProperty(
        name        = 'Stand Frame',
        description = 'Frame of the stand KEY',
        default     = 0.0,
        min         = 0.0,
    )
    waist_tuck: bpy.props.FloatProperty(
        name        = 'Waist Tuck',
        description = "The most of the thighs' leftover push into the hip that goes to the waist as a small overlap, in JK units",
        default     = 0.0025,
        min         = 0.0,
        max         = 0.01,
        precision   = 4,
    )
    waist_lift: bpy.props.FloatProperty(
        name        = 'Waist Lift',
        description = 'How far the hip may follow the torso up where a waist gap is left, in JK units. It opens the hip joints by as much. Lilivane R3 used 0.0015',
        default     = 0.0,
        min         = 0.0,
        max         = 0.01,
        precision   = 4,
    )
    check_written: bpy.props.BoolProperty(
        name        = 'Check',
        description = 'Load each written KEY back and pose it against its source: no rotation may change and no joint but the hip and the seated joints may move. A failed check is an error',
        default     = True,
    )
    output_directory: bpy.props.StringProperty(
        name        = 'Output Folder',
        description = 'Required. Folder for the reseated KEYs. It must not be the KEY Folder or hold a file named like one of the KEYs',
        subtype     = 'DIR_PATH',
    )

    def draw(self, context):
        layout = self.layout
        keys = layout.box().column()
        keys.label(text='KEYs')
        keys.prop(self, 'key_directory')
        keys.prop(self, 'key_files')
        seats = layout.box().column()
        seats.prop(self, 'seats')
        if self.seats == 'KEY':
            seats.prop(self, 'stand_key')
            seats.prop(self, 'stand_frame')
        rule = layout.box().column()
        rule.prop(self, 'waist_tuck')
        rule.prop(self, 'waist_lift')
        rule.prop(self, 'check_written')
        layout.prop(self, 'output_directory')

    def execute(self, context):
        if not self.filepath or not os.path.isfile(self.filepath) or not self.filepath.lower().endswith('.3do'):
            self.report({'ERROR'}, 'Select an existing .3do file')
            return {'CANCELLED'}
        try:
            report, outDir, text = self._reseat(Path(self.filepath))
        except (AssertionError, ValueError, OSError, EOFError, TypeError) as error:
            self.report({'ERROR'}, f'Cannot reseat the KEYs: {error}')
            return {'CANCELLED'}
        status, changed = report['status'], report['changed_keys']
        names = ', '.join(changed[:5]) + (f' and {len(changed) - 5} more' if len(changed) > 5 else '')
        review = f"; {sum(report['issue_counts'].values())} findings need review" if status == 'review' else ''
        if status == 'fail':
            failed = report['check']['failed_keys']
            self.report({'ERROR'}, f"The check failed for {', '.join(failed)}: do not install them. "
                                   f"Wrote {len(changed)} KEYs to '{outDir}'. Report: '{text.name}'")
        elif changed:
            self.report({'WARNING'} if review else {'INFO'},
                        f"Wrote {len(changed)} reseated KEYs to '{outDir}': {names}{review}. "
                        f"Install only these, after review. Report: '{text.name}'")
        elif review:
            self.report({'WARNING'}, f"No KEY was written{review}. Report: '{text.name}'")
        else:
            self.report({'INFO'}, f"No KEY needed reseating; nothing was written. Report: '{text.name}'")
        return {'FINISHED'}

    def _reseat(self, modelPath: Path):
        from .key.keyReseat import reseatKeyFiles
        from .model.model3doLoader import load3do

        folder = Path(bpy.path.abspath(self.key_directory)) if self.key_directory else modelPath.parent / 'key'
        if not folder.is_dir():
            raise ValueError(f'KEY folder not found: {folder}')
        paths = _audit_key_paths(folder, self.key_files)
        if not paths:
            raise ValueError(f'The KEY folder has no .key files: {folder}')
        outDir = _reseat_output_folder(self.output_directory, folder, paths)
        stand = None
        if self.seats == 'KEY':
            if not self.stand_key.strip():
                raise ValueError('Give the Stand KEY File')
            stand = Path(bpy.path.abspath(self.stand_key))
            if not stand.is_absolute():
                stand = folder / self.stand_key
            if not stand.is_file():
                raise ValueError(f'Stand KEY not found: {stand}')
        model, _ = load3do(modelPath)
        if not model.meshHierarchy:
            raise ValueError('The 3DO has no hierarchy')
        existing = set(outDir.iterdir()) if outDir.is_dir() else set()
        try:
            report = reseatKeyFiles(model, paths, outDir, standKeyPath=stand, standFrame=self.stand_frame,
                                    waistTuck=self.waist_tuck, waistLift=self.waist_lift, check=self.check_written)
        except (AssertionError, ValueError, OSError, EOFError, TypeError) as error:
            written = sorted(path.name for path in outDir.iterdir() if path not in existing) if outDir.is_dir() else []
            if written:
                raise ValueError(f"{error}. Written to '{outDir}' before the error: {', '.join(written)}") from error
            raise

        check = report['check']
        review = sorted(name for name, entry in report['per_key'].items() if entry['status'] in ('review', 'skipped'))
        lines = [f'3DO: {modelPath}', f'KEY folder: {folder}', f'KEYs: {len(paths)}', f"Seats: {report['seats']}",
                 f'Waist tuck: {self.waist_tuck:g}; waist lift: {self.waist_lift:g}', f'Output folder: {outDir}', '',
                 f"Status: {report['status']}",
                 f"Written: {len(report['changed_keys'])} KEYs; unchanged: {report['unchanged_keys']}",
                 *(f'  {name}' for name in report['changed_keys'])]
        if review:
            lines.append(f"Need review: {', '.join(review)}")
        if check is not None:
            lines += ['', f"Check: {check['checked_keys']} KEYs posed; failed: {', '.join(check['failed_keys']) or 'none'}"]
            for metric in ('largest_orientation_change', 'fixed_moved_at_frames', 'fixed_moved_between_frames',
                           'hip_moved', 'seated_moved'):
                worst = check[metric]
                where = f" ({worst['key']}, {worst['name']} at frame {worst['time']:g})" if worst['key'] else ''
                lines.append(f"  {metric.replace('_', ' ')}: {worst['value']:.3g}{where}")
        lines.append('')
        for f in report['findings']:
            details = ', '.join(f'{name} {value}' for name, value in f.items() if name not in ('key', 'code'))
            lines.append(f"{f.get('key', '')}: {f['code']} {details}".rstrip())
        if not report['findings']:
            lines.append('No findings.')
        lines += ['', 'Install only the written KEYs, after review. Run Audit Files with Joint Gaps on the source '
                      'and the written KEYs, with the same KEY Files, Step and reference pose.']
        text = _audit_text(f'{modelPath.name} - KEY Reseat', _audit_document('KEY Reseat', lines, report))
        return report, outDir, text


class SithAuditPanel(bpy.types.Panel):
    """Native model and KEY audits for the active model or for files, and the KEY reseat pass"""
    bl_idname      = 'VIEW3D_PT_sith_audits'
    bl_label       = 'Sith Audits'
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'Sith'

    def draw(self, context):
        layout = self.layout
        column = layout.column(align=True)
        column.operator(CheckRetailLimits.bl_idname, icon='CHECKMARK')
        row = column.row(align=True)
        row.operator(ColourPieces.bl_idname, icon='COLOR')
        row.operator(RestoreColours.bl_idname, icon='LOOP_BACK')
        layout.operator(AuditFiles.bl_idname, icon='FILE_FOLDER')
        layout.operator(ReseatKeys.bl_idname, icon='KEYINGSET')
        layout.label(text='Reports open in the Text Editor')


# -- Update callbacks for Mesh3doFaceLayer properties --
# These fire only when the user actually edits a value in the UI panel,
# so only the changed property is written to the selected faces.

_face_sync_in_progress = False

def _apply_to_selected_faces(context, apply_fn):
    """Helper: apply a function to every selected face in the active edit mesh."""
    if context.edit_object is None or context.edit_object.type != 'MESH':
        return
    bm = bmesh.from_edit_mesh(context.edit_object.data)
    bmMeshInit3doLayers(bm)
    for face in bm.faces:
        if face.select:
            apply_fn(face, bm)

def _on_face_type_update(self, context):
    if _face_sync_in_progress:
        return

    # Determine which flag was toggled by comparing current vs previous
    prev_flags = set(self.prev_type_flags.split(',')) if self.prev_type_flags else set()
    curr_flags = set(self.type)

    added_flags = curr_flags - prev_flags
    removed_flags = prev_flags - curr_flags

    # Apply only the toggled flag to all selected faces
    def apply_flag_toggle(face, bm):
        face_type = bmFaceGetType(face, bm)
        for flag_name in added_flags:
            if hasattr(FaceType, flag_name):
                face_type |= FaceType[flag_name]
        for flag_name in removed_flags:
            if hasattr(FaceType, flag_name):
                # IntFlag inversion stops at known bits; an integer mask keeps
                # unrelated flags from other engine versions or custom assets.
                face_type = FaceType(int(face_type) & ~int(FaceType[flag_name]))
        bmFaceSetType(face, bm, face_type)

    _apply_to_selected_faces(context, apply_flag_toggle)

    # Update the stored previous flags
    self.prev_type_flags = ','.join(sorted(curr_flags))

def _on_face_geo_mode_update(self, context):
    if _face_sync_in_progress:
        return
    _apply_to_selected_faces(context, lambda f, bm: bmFaceSetGeometryMode(f, bm, GeometryMode[self.geo_mode]))

def _on_face_light_mode_update(self, context):
    if _face_sync_in_progress:
        return
    _apply_to_selected_faces(context, lambda f, bm: bmFaceSetLightMode(f, bm, LightMode[self.light_mode]))

def _on_face_texture_mode_update(self, context):
    if _face_sync_in_progress:
        return
    _apply_to_selected_faces(context, lambda f, bm: bmFaceSetTextureMode(f, bm, TextureMode[self.texture_mode]))

def _on_face_extra_light_update(self, context):
    if _face_sync_in_progress:
        return
    _apply_to_selected_faces(context, lambda f, bm: bmFaceSetExtraLight(f, bm, Vector4f(*self.extra_light)))
    if context.edit_object is not None:
        model3doPreview.syncObjectPreview(context.edit_object)


class Mesh3doFaceLayer(bpy.types.PropertyGroup):
    """
    Intermediate class for temporary storing BMFace properties by Mesh3doFacePanel
    and used it to display stored properties to the UI.
    """
    face_id: bpy.props.IntProperty(default = -1)

    # Track previous type flags to detect which specific flag was toggled
    prev_type_flags: bpy.props.StringProperty(default='', options={'HIDDEN', 'SKIP_SAVE'})

    type: bpy.props.EnumProperty(
        name        = 'Type',
        description = 'Face type flag',
        items       = _get_mesh3do_face_type_list(),
        options     = {'ENUM_FLAG'},
        update      = _on_face_type_update
    )

    geo_mode: bpy.props.EnumProperty(
        name        = 'Geometry Mode',
        description = 'Geometry mode',
        items       = _get_model3do_geometry_mode_list(),
        default     = GeometryMode.Texture.name,
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'},
        update      = _on_face_geo_mode_update
    )

    light_mode: bpy.props.EnumProperty(
        name        = 'Lighting Mode',
        description = 'Lighting mode',
        items       = _get_model3do_light_mode_list(),
        default     = LightMode.Gouraud.name,
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'},
        update      = _on_face_light_mode_update
    )

    texture_mode: bpy.props.EnumProperty(
        name        = 'Texture Mode',
        description = 'Texture mapping mode (Not used by IJIM)',
        items       = _get_model3do_texture_mode_list(),
        default     = TextureMode.PerspectiveCorrected.name,
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'},
        update      = _on_face_texture_mode_update
    )

    extra_light: bpy.props.FloatVectorProperty(
        name        = 'Extra Light',
        description = 'Face extra light color',
        size        = 4,
        subtype     ='COLOR',
        default     = [0.0, 0.0, 0.0, 1.0],
        min         = -kFloat32Max,
        max         = kFloat32Max,
        soft_min    = 0.0,
        soft_max    = 1.0,
        update      = _on_face_extra_light_update
    )

class Mesh3doFacePanel(bpy.types.Panel):
    """
    Panel exposes 3DO mesh face properties to the UI.
    i.e.: face type flags, geometry mode, light mode, texture mode & hierarchy node properties.
    """
    bl_idname      = 'DATA_PT_model3do_face_panel'
    bl_label       = '3DO Mesh Face Properties'
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'data'
    bl_options     = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # show panel if in edit mode with face selection mode and at least one face selected
        if context.edit_object is not None and context.edit_object.type == 'MESH':
            bm = bmesh.from_edit_mesh(context.edit_object.data)
            return 'FACE' in bm.select_mode and isinstance(bm.select_history.active, bmesh.types.BMFace)
        return False

    @staticmethod
    def _get_face_id(face: bmesh.types.BMFace):
        return hash(face) %2**31 -1 # gen. 32 bit signed hash id of face

    def draw(self, context):
        global _face_sync_in_progress
        wm_fl = context.window_manager.sith_mesh3do_face_layer

        bm = bmesh.from_edit_mesh(context.edit_object.data)
        bmMeshInit3doLayers(bm)

        aface   = bm.faces.active
        enabled = aface is not None and aface.select
        selected_faces = [f for f in bm.faces if f.select]

        if enabled:
            fid = self._get_face_id(aface)
            if wm_fl.face_id != fid: # sync panel properties from active face
                _face_sync_in_progress = True
                wm_fl.face_id      = fid
                type_set           = bmFaceGetType(aface, bm).toSet()
                wm_fl.type         = type_set
                wm_fl.prev_type_flags = ','.join(sorted(type_set))
                wm_fl.geo_mode     = bmFaceGetGeometryMode(aface, bm).name
                wm_fl.light_mode   = bmFaceGetLightMode(aface, bm).name
                wm_fl.texture_mode = bmFaceGetTextureMode(aface, bm).name
                wm_fl.extra_light  = bmFaceGetExtraLight(aface, bm)
                _face_sync_in_progress = False
        else:
            wm_fl.face_id = -1

        layout       = self.layout
        if len(selected_faces) > 1:
            layout.label(text=f'{len(selected_faces)} faces selected')
        box          = layout.box()
        box.enabled  = enabled
        tbox         = box.box()
        tbox.label(text= 'Type Flags')
        tbox.props_enum(wm_fl, 'type')
        box.prop(wm_fl, 'geo_mode'    , text='Geometry')
        box.prop(wm_fl, 'light_mode'  , text='Lighting')
        box.prop(wm_fl, 'texture_mode', text='Texture')
        box.prop(wm_fl, 'extra_light' , text='Extra Light')

classes = (
    Mesh3doFaceLayer,
    Mesh3doFacePanel,
    View3doImportReport,
    Toggle3doPivotHelper,
    Model3doPanel,
    ImportMat,
    ExportMat,
    ImportModel3do,
    ExportModel3do,
    ImportKey,
    ExportKey,
    CheckRetailLimits,
    ColourPieces,
    RestoreColours,
    AuditFiles,
    ReseatKeys,
    SithAuditPanel,
)

def menu_func_export(self, context):
    self.layout.operator(ExportKey.bl_idname, text='Sith Game Engine Animation (.key)')
    self.layout.operator(ExportModel3do.bl_idname, text='Sith Game Engine 3D Model (.3do)')


def menu_func_import(self, context):
    self.layout.operator(ImportKey.bl_idname, text='Sith Game Engine Animation (.key)')
    self.layout.operator(ImportMat.bl_idname, text='Sith Game Engine Texture (.mat)')
    self.layout.operator(ImportModel3do.bl_idname, text='Sith Game Engine 3D Model (.3do)')

def register():
    # Register classes
    for cls in classes:
        bpy.utils.register_class(cls)

    # Register menu functions
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.OUTLINER_MT_context_menu.append(menu_export_mat_outliner)
    bpy.types.IMAGE_MT_image.append(menu_export_mat_image)
    bpy.types.MATERIAL_MT_context_menu.append(menu_export_mat_material)
    bpy.types.UI_MT_list_item_context_menu.append(menu_export_mat_material_slot)

    # 3DO custom properties for object
    setattr(bpy.types.Object, kModel3doShadow, bpy.props.BoolProperty(
        name='Shadow (Grim Fandango)', description="Include this mesh in Grim Fandango's separate projected actor-shadow pass, using a bounds-derived shadow proxy. Does not change ordinary mesh rendering or Blender shadow settings",
        default=False, options={'HIDDEN', 'LIBRARY_EDITABLE'}
    ))
    setattr(bpy.types.Object, kModel3doGeometryMode, bpy.props.EnumProperty(
        name        = 'Geometry Mode',
        description = 'Engine mesh geometry mode, independent of viewport display',
        items       = _get_model3do_geometry_mode_list(),
        default     = 'Texture',
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'}
    ))
    bpy.types.Object.sith_model3do_light_mode = bpy.props.EnumProperty(
        name        = 'Lighting Mode',
        description = 'Lighting mode',
        items       = _get_model3do_light_mode_list(),
        default     = 'Gouraud',
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'}
    )

    bpy.types.Object.sith_model3do_texture_mode = bpy.props.EnumProperty(
        name        = 'Texture Mode',
        description = 'Texture mapping mode (Not used by IJIM)',
        items       = _get_model3do_texture_mode_list(),
        default     = 'PerspectiveCorrected',
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'}
    )

    bpy.types.Object.sith_model3do_hnode_idx = bpy.props.IntProperty(
        name        = '3DO Hierarchy Node Number',
        description = 'The node position number in the hierarchy list. If set to -1 the position will be auto assigned when exporting to 3DO file',
        default     = -1,
        min         = -1,
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'}
    )

    bpy.types.Object.sith_model3do_hnode_name = bpy.props.StringProperty(
        name        = '3DO Hierarchy Node Name',
        description = 'The name of hierarchy node',
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'}
    )

    bpy.types.Object.sith_model3do_hnode_flags = HexProperty(
        'sith_model3do_hnode_flags',
        name         = '3DO Hierarchy Node Flags',
        description  = 'The hierarchy node flags',
        pad          = True,
        options      = {'HIDDEN', 'LIBRARY_EDITABLE'}
    )

    bpy.types.Object.sith_model3do_hnode_type = HexProperty(
        'sith_model3do_hnode_type',
        name        = '3DO Hierarchy Node Type',
        description = 'The hierarchy node type',
        default     = '0x01',
        pad         = True,
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'}
    )

    # 3DO Mesh Face custom properties
    bpy.types.Object.sith_model3do_pivot = bpy.props.FloatVectorProperty(
        name='Native Pivot', size=3, subtype='XYZ',
        description='Local native mesh-frame offset for 3DO export. Keeps editable geometry and KEY joint channels unchanged; an active Blender Pivot constraint takes precedence',
        get=getNativePivot, set=setNativePivot,
        options={'LIBRARY_EDITABLE'},
    )

    bpy.types.WindowManager.sith_mesh3do_face_layer = bpy.props.PointerProperty(type=Mesh3doFaceLayer)

    # KEY custom properties
    bpy.types.Scene.sith_key_flags = bpy.props.EnumProperty(
        items       = _get_key_flags_enum_list(),
        name        = 'KEY Flags',
        description = 'Stored KEY header flags, exposed for format completeness. Original Sith playback uses PUP/script flags instead; Grim has different flag meanings',
        options     = {'ENUM_FLAG', 'HIDDEN', 'LIBRARY_EDITABLE'},
    )

    bpy.types.Scene.sith_key_types = HexProperty(
        'sith_key_types',
        name        = 'High Priority Node(s)',
        description = 'Hexadecimal node-type mask, not joint numbers. A joint whose 3DO Type shares any bit with this mask uses the track high priority; other joints use its low priority. PUP/playback settings supply those priorities. FFFF selects all standard type bits; 0 selects none',
        default     = '0xFFFF',
        pad         = True,
        options     = {'HIDDEN', 'LIBRARY_EDITABLE'}
    )
    model3doPreview.register()

def unregister():
    bpy.types.OUTLINER_MT_context_menu.remove(menu_export_mat_outliner)
    bpy.types.IMAGE_MT_image.remove(menu_export_mat_image)
    bpy.types.MATERIAL_MT_context_menu.remove(menu_export_mat_material)
    bpy.types.UI_MT_list_item_context_menu.remove(menu_export_mat_material_slot)
    model3doPreview.unregister()
    del bpy.types.Scene.sith_key_flags
    del bpy.types.Scene.sith_key_types

    del bpy.types.WindowManager.sith_mesh3do_face_layer

    del bpy.types.Object.sith_model3do_hnode_flags
    del bpy.types.Object.sith_model3do_hnode_type
    del bpy.types.Object.sith_model3do_hnode_name
    del bpy.types.Object.sith_model3do_hnode_idx
    del bpy.types.Object.sith_model3do_pivot
    del bpy.types.Object.sith_model3do_texture_mode
    delattr(bpy.types.Object, kModel3doGeometryMode)
    delattr(bpy.types.Object, kModel3doShadow)
    del bpy.types.Object.sith_model3do_light_mode

    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)

    for cls in classes:
        bpy.utils.unregister_class(cls)


if __name__ == '__main__':
    try:
        unregister()
    except:
        pass
    register()
