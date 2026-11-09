"""Helpers for bridge requests, available as `bridge`.

bridge.view(path=None, size=960)
    Save what the user's 3D viewport shows now. Returns the PNG path.

bridge.look(objects=None, views=("front", "right", "top", "persp"), size=384, path=None)
    Save one labelled image of the objects seen from standard directions, each framed
    to fit, without moving the user's view. `objects` takes objects or names and
    defaults to all visible geometry. Geometry in front of and behind the objects is
    clipped away, so walls do not hide them. Other views: "back", "left", "bottom".
    Returns the PNG path.

bridge.override(area_type='VIEW_3D')
    Context for operators that need an editor:
    `with bridge.override(): bpy.ops.view3d.view_selected()`

Print the returned path and open the PNG with your image tool. Images use the
viewport's current shading mode and need Blender's window (not --background).
"""

import math
import os
import struct
import tempfile
import zlib

import bpy
from mathutils import Euler, Matrix, Vector

__all__ = ("look", "override", "view")

# Camera rotation (XYZ Euler, degrees) for each named direction, matching Blender's numpad views.
VIEWS = {
    "front": (90, 0, 0),
    "back": (90, 0, 180),
    "right": (90, 0, 90),
    "left": (90, 0, -90),
    "top": (0, 0, 0),
    "bottom": (180, 0, 0),
    "persp": (62, 0, 40),
}
_FOV = math.radians(40)
_MARGIN = 1.08
_NOT_FRAMED = {'CAMERA', 'LIGHT', 'LIGHT_PROBE', 'SPEAKER', 'EMPTY'}


def _area(area_type):
    """(window, area, region) of the largest editor of that type, the active window first."""
    windows = list(bpy.context.window_manager.windows)
    if bpy.context.window in windows:
        windows.insert(0, bpy.context.window)
    for window in windows:
        areas = [area for area in window.screen.areas if area.type == area_type]
        if areas:
            area = max(areas, key=lambda a: a.width * a.height)
            return window, area, next(region for region in area.regions if region.type == 'WINDOW')
    raise RuntimeError(f"No {area_type} editor is open in this Blender")


def override(area_type='VIEW_3D'):
    window, area, region = _area(area_type)
    return bpy.context.temp_override(window=window, area=area, region=region)


def _points(objects):
    """World-space points that bound the objects to frame."""
    if objects is None:
        visible = [obj for obj in bpy.context.view_layer.objects if obj.visible_get()]
        objects = [obj for obj in visible if obj.type not in _NOT_FRAMED] or visible
    elif isinstance(objects, (str, bpy.types.Object)):
        objects = [objects]
    objects = [bpy.data.objects[obj] if isinstance(obj, str) else obj for obj in objects]
    if not objects:
        raise ValueError("Nothing to look at: no visible objects")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        corners = [Vector(corner) for corner in evaluated.bound_box]
        if obj.type in _NOT_FRAMED or max((corner.length for corner in corners), default=0.0) == 0.0:
            points.append(evaluated.matrix_world.translation.copy())
        else:
            points.extend(evaluated.matrix_world @ corner for corner in corners)
    return points


def _frame(points, view):
    """View and projection matrices that fit the points from a named direction."""
    if view not in VIEWS:
        raise ValueError(f"Unknown view {view!r}; choose from {', '.join(VIEWS)}")
    low = Vector(min(p[i] for p in points) for i in range(3))
    high = Vector(max(p[i] for p in points) for i in range(3))
    center = (low + high) / 2
    radius = max(max((p - center).length for p in points), 0.01)
    rotation = Euler([math.radians(angle) for angle in VIEWS[view]]).to_matrix()
    local = [rotation.inverted() @ (p - center) for p in points]  # x right, y up, z towards the camera
    half = max(max(abs(p.x), abs(p.y)) for p in local)
    front, back = max(p.z for p in local), min(p.z for p in local)
    pad = radius * 0.05
    if view == "persp":
        tan = math.tan(_FOV / 2)
        distance = max(max(abs(p.x), abs(p.y)) / tan + p.z for p in local) * _MARGIN
        near, far = max(distance - front - pad, distance * 0.01), distance - back + pad
        projection = Matrix((
            (1 / tan, 0, 0, 0),
            (0, 1 / tan, 0, 0),
            (0, 0, -(far + near) / (far - near), -2 * far * near / (far - near)),
            (0, 0, -1, 0),
        ))
    else:
        size = max(half, 0.01) * _MARGIN
        distance = front + radius
        near, far = radius - pad, distance - back + pad
        projection = Matrix((
            (1 / size, 0, 0, 0),
            (0, 1 / size, 0, 0),
            (0, 0, -2 / (far - near), -(far + near) / (far - near)),
            (0, 0, 0, 1),
        ))
    camera = Matrix.Translation(center + rotation @ Vector((0, 0, distance))) @ rotation.to_4x4()
    return camera.inverted(), projection


def _draw(shots, width, height):
    """Draw the 3D viewport offscreen once per (view, projection, label); RGB byte arrays, top row first."""
    if bpy.app.background:
        raise RuntimeError("bridge.view() and bridge.look() need Blender's window; they cannot draw with --background")
    import blf
    import gpu
    import numpy as np

    window, area, region = _area('VIEW_3D')
    offscreen = gpu.types.GPUOffScreen(width, height)
    images = []
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            for view_matrix, projection, label in shots:
                offscreen.draw_view3d(
                    bpy.context.scene, bpy.context.view_layer, area.spaces.active, region,
                    view_matrix, projection, do_color_management=True, draw_background=True,
                )
                if label:
                    with offscreen.bind():
                        with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                            gpu.matrix.load_identity()
                            gpu.matrix.load_projection_matrix(Matrix((
                                (2 / width, 0, 0, -1), (0, 2 / height, 0, -1), (0, 0, -1, 0), (0, 0, 0, 1),
                            )))
                            blf.size(0, 15)
                            blf.color(0, 1.0, 1.0, 1.0, 1.0)
                            blf.enable(0, blf.SHADOW)
                            blf.shadow(0, 3, 0.0, 0.0, 0.0, 1.0)
                            blf.position(0, 8, height - 20, 0)
                            blf.draw(0, label)
                            blf.disable(0, blf.SHADOW)
                buffer = offscreen.texture_color.read()
                buffer.dimensions = width * height * 4  # Flat first: Blender 5.0 exports wrong strides otherwise.
                pixels = np.asarray(buffer).reshape(height, width, 4)
                if pixels.dtype.kind == 'f':
                    pixels = np.clip(pixels * 255.0 + 0.5, 0, 255)
                images.append(pixels[::-1, :, :3].astype(np.uint8))  # GPU rows run bottom to top
    finally:
        offscreen.free()
    return images


def _save(pixels, path, name):
    """Write RGB bytes as a PNG. Done by hand: a Blender image data-block would count as a scene edit."""
    height, width = pixels.shape[:2]
    path = path or os.path.join(tempfile.gettempdir(), name)
    rows = b"".join(b"\x00" + row.tobytes() for row in pixels)  # filter type 0 before each row

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    with open(path, "wb") as file:
        file.write(b"\x89PNG\r\n\x1a\n")
        file.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        file.write(chunk(b"IDAT", zlib.compress(rows, 6)))
        file.write(chunk(b"IEND", b""))
    return path


def view(path=None, size=960):
    _, area, region = _area('VIEW_3D')
    rv3d = area.spaces.active.region_3d
    scale = min(1.0, size / max(region.width, region.height))
    width, height = max(1, round(region.width * scale)), max(1, round(region.height * scale))
    return _save(_draw([(rv3d.view_matrix, rv3d.window_matrix, "")], width, height)[0], path, "bridge_view.png")


def look(objects=None, views=("front", "right", "top", "persp"), size=384, path=None):
    import numpy as np

    views = [views] if isinstance(views, str) else list(views)
    points = _points(objects)
    tiles = _draw([(*_frame(points, name), name) for name in views], size, size)
    columns = 1 if len(tiles) == 1 else 2 if len(tiles) <= 4 else 3
    rows = math.ceil(len(tiles) / columns)
    gap = 2
    sheet = np.full((rows * size + (rows - 1) * gap, columns * size + (columns - 1) * gap, 3), 255, dtype=np.uint8)
    for index, tile in enumerate(tiles):
        row, column = divmod(index, columns)
        y, x = row * (size + gap), column * (size + gap)
        sheet[y:y + size, x:x + size] = tile
    return _save(sheet, path, "bridge_look.png")
