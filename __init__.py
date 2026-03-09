import bpy
import bpy.utils.previews
import socket
import struct
import json
import io
import os
import sys
import traceback

_server: socket.socket | None = None
_active = False
_icon_collection = None
POLL_INTERVAL = 0.1


def _get_prefs():
    prefs = bpy.context.preferences.addons.get(__package__)
    if prefs:
        return prefs.preferences
    return None


def _get_port():
    prefs = _get_prefs()
    return prefs.port if prefs else 9876


def _get_timeout():
    prefs = _get_prefs()
    return prefs.timeout if prefs else 5.0


def _start_server():
    global _server, _active
    if _server is not None:
        return
    port = _get_port()
    _server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _server.bind(("localhost", port))
    _server.listen(1)
    _server.setblocking(False)
    _active = True
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=POLL_INTERVAL, persistent=True)
    print(f"Blender Bridge listening on localhost:{port}")


def _stop_server():
    global _server, _active
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    if _server is not None:
        _server.close()
        _server = None
    _active = False
    print("Blender Bridge stopped")


def _recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def _poll():
    if _server is None:
        return None

    try:
        conn, _ = _server.accept()
    except BlockingIOError:
        return POLL_INTERVAL

    try:
        conn.setblocking(True)
        conn.settimeout(_get_timeout())

        hdr = _recv_exact(conn, 4)
        size = struct.unpack(">I", hdr)[0]
        code = _recv_exact(conn, size).decode("utf-8")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout_buf, stderr_buf

        try:
            exec(code, {"__builtins__": __builtins__, "bpy": bpy})
            result = {
                "ok": True,
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
            }
        except Exception:
            result = {
                "ok": False,
                "error": traceback.format_exc(),
                "stdout": stdout_buf.getvalue(),
                "stderr": stderr_buf.getvalue(),
            }
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

        payload = json.dumps(result).encode("utf-8")
        conn.sendall(struct.pack(">I", len(payload)) + payload)
    except socket.timeout:
        timeout_val = _get_timeout()
        msg = (
            f"Bridge timeout: command exceeded {timeout_val:.0f}s limit. "
            f"Increase timeout in addon preferences (Edit > Preferences > Add-ons > Blender Bridge) "
            f"if running long operations."
        )
        print(f"[Blender Bridge] {msg}")
        try:
            err = json.dumps({"ok": False, "error": msg}).encode("utf-8")
            conn.sendall(struct.pack(">I", len(err)) + err)
        except Exception:
            pass
    except Exception as e:
        try:
            err = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
            conn.sendall(struct.pack(">I", len(err)) + err)
        except Exception:
            pass
    finally:
        conn.close()

    return POLL_INTERVAL


# --- Operators ---

class BRIDGE_OT_toggle(bpy.types.Operator):
    bl_idname = "bridge.toggle"
    bl_label = "Toggle Blender Bridge"
    bl_description = "Click to toggle bridge. Ctrl+Click to copy agent instructions"

    def invoke(self, context, event):
        if event.ctrl:
            return bpy.ops.bridge.copy_instructions()
        return self.execute(context)

    def execute(self, context):
        if _active:
            _stop_server()
        else:
            _start_server()
        for area in context.screen.areas:
            if area.type == 'TOPBAR':
                area.tag_redraw()
        return {'FINISHED'}


class BRIDGE_OT_copy_instructions(bpy.types.Operator):
    bl_idname = "bridge.copy_instructions"
    bl_label = "Copy Agent Instructions"
    bl_description = "Copy startup instructions for your coding agent to the clipboard"

    def execute(self, context):
        port = _get_port()
        addon_dir = os.path.dirname(__file__)
        exec_sh = os.path.join(addon_dir, "blender_exec.sh").replace("\\", "/")

        template_path = os.path.join(addon_dir, "agent_instructions.md")
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            self.report({'ERROR'}, "agent_instructions.md not found in addon folder")
            return {'CANCELLED'}

        text = text.replace("{{PORT}}", str(port))
        text = text.replace("{{EXEC_PATH}}", exec_sh)

        context.window_manager.clipboard = text
        self.report({'INFO'}, "Agent instructions copied to clipboard")
        return {'FINISHED'}


# --- Preferences ---

class BridgePreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    port: bpy.props.IntProperty(
        name="Port",
        default=9876,
        min=1024,
        max=65535,
        description="TCP port for the bridge socket",
    )

    timeout: bpy.props.FloatProperty(
        name="Timeout (seconds)",
        default=60.0,
        min=1.0,
        soft_max=3600.0,
        description="Max execution time per command before timeout",
    )

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.prop(self, "port")
        row.prop(self, "timeout")
        if _active:
            row.label(text="(restart bridge to apply port changes)")
        layout.operator("bridge.copy_instructions", icon='COPYDOWN')


# --- Top Bar UI ---

def _draw_topbar(self, context):
    if context.region.alignment != 'RIGHT':
        return
    layout = self.layout
    icon_id = _icon_collection["bridge_icon"].icon_id if _icon_collection else 0
    if _active:
        layout.operator("bridge.toggle", text="", icon_value=icon_id, depress=True)
    else:
        layout.operator("bridge.toggle", text="", icon_value=icon_id)


def register():
    global _icon_collection
    _icon_collection = bpy.utils.previews.new()
    icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
    _icon_collection.load("bridge_icon", icon_path, 'IMAGE')

    bpy.utils.register_class(BRIDGE_OT_toggle)
    bpy.utils.register_class(BRIDGE_OT_copy_instructions)
    bpy.utils.register_class(BridgePreferences)
    bpy.types.TOPBAR_HT_upper_bar.append(_draw_topbar)


def unregister():
    global _icon_collection
    _stop_server()
    bpy.types.TOPBAR_HT_upper_bar.remove(_draw_topbar)
    bpy.utils.unregister_class(BridgePreferences)
    bpy.utils.unregister_class(BRIDGE_OT_copy_instructions)
    bpy.utils.unregister_class(BRIDGE_OT_toggle)
    if _icon_collection:
        bpy.utils.previews.remove(_icon_collection)
        _icon_collection = None
