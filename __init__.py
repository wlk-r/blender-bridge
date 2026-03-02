bl_info = {
    "name": "Blender Bridge",
    "blender": (4, 5, 0),
    "category": "Development",
    "description": "TCP socket bridge for remote Python execution",
}

import bpy
import socket
import struct
import json
import io
import sys
import traceback

_server: socket.socket | None = None
_active = False
POLL_INTERVAL = 0.1


def _get_port():
    prefs = bpy.context.preferences.addons.get(__package__)
    if prefs:
        return prefs.preferences.port
    return 9876


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
        conn.settimeout(5.0)

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
    except Exception as e:
        try:
            err = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
            conn.sendall(struct.pack(">I", len(err)) + err)
        except Exception:
            pass
    finally:
        conn.close()

    return POLL_INTERVAL


# --- Operator ---

class BRIDGE_OT_toggle(bpy.types.Operator):
    bl_idname = "bridge.toggle"
    bl_label = "Toggle Blender Bridge"
    bl_description = "Start or stop the Blender Bridge socket server"

    def execute(self, context):
        if _active:
            _stop_server()
        else:
            _start_server()
        # Redraw top bar to update button
        for area in context.screen.areas:
            if area.type == 'TOPBAR':
                area.tag_redraw()
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

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.prop(self, "port")
        if _active:
            row.label(text="(restart bridge to apply)")


# --- Top Bar UI ---

def _draw_topbar(self, context):
    layout = self.layout
    if _active:
        layout.operator("bridge.toggle", text="Bridge: ON", depress=True)
    else:
        layout.operator("bridge.toggle", text="Bridge: OFF")


def register():
    bpy.utils.register_class(BRIDGE_OT_toggle)
    bpy.utils.register_class(BridgePreferences)
    bpy.types.TOPBAR_HT_upper_bar.append(_draw_topbar)


def unregister():
    _stop_server()
    bpy.types.TOPBAR_HT_upper_bar.remove(_draw_topbar)
    bpy.utils.unregister_class(BridgePreferences)
    bpy.utils.unregister_class(BRIDGE_OT_toggle)
