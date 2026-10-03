import bpy
import bpy.utils.previews
import errno
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
from bpy.app.handlers import persistent
from itertools import chain, count
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_active = False
_restart_after_load = False
_retired_ports = set()
_queue_lock = threading.Lock()
_icon_collection = None
_request_ids = count(1)
request_queue: queue.Queue[tuple[int, str, queue.Queue]] = queue.Queue()
POLL_INTERVAL = 0.1
_STREAM_DONE = "done"


def _get_prefs():
    prefs = bpy.context.preferences.addons.get(__package__)
    if prefs:
        return prefs.preferences
    return None


def _get_port():
    if _server is not None:
        return _server.server_port
    prefs = _get_prefs()
    return prefs.port if prefs else 9876


def _get_timeout():
    prefs = _get_prefs()
    return prefs.timeout if prefs else 60.0


def _clear_queue(q):
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


class _StreamWriter:
    """File-like that pushes each write() into a queue for real-time streaming."""

    def __init__(self, stream_queue, channel):
        self._q = stream_queue
        self._ch = channel

    def write(self, text):
        if text:
            self._q.put((self._ch, text))
        return len(text) if text else 0

    def flush(self):
        pass


def _execute_code(code, stream_q):
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = _StreamWriter(stream_q, "stdout")
    sys.stderr = _StreamWriter(stream_q, "stderr")
    try:
        exec(code, {"__builtins__": __builtins__, "bpy": bpy})
        stream_q.put((_STREAM_DONE, {"ok": True}))
    except Exception:
        stream_q.put((_STREAM_DONE, {"ok": False, "error": traceback.format_exc()}))
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr


class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "BlenderBridge/1.2"

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(400, {"ok": False, "error": "Invalid Content-Length"})
            return

        if length <= 0:
            self._write_json(400, {"ok": False, "error": "Request body is required"})
            return

        try:
            code = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            self._write_json(400, {"ok": False, "error": "Request body must be UTF-8"})
            return

        stream_q = queue.Queue()
        with _queue_lock:
            if self.server is not _server:
                self._write_json(503, {"ok": False, "error": "Bridge stopped; copy fresh instructions"})
                return
            request_queue.put((next(_request_ids), code, stream_q))

        accept = self.headers.get("Accept", "")
        if "application/x-ndjson" in accept:
            self._do_streaming(stream_q)
        else:
            self._do_buffered(stream_q)

    # -- Streaming: chunked NDJSON, real-time output --------------------

    def _do_streaming(self, stream_q):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        deadline = time.monotonic() + _get_timeout()
        try:
            while True:
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    channel, data = stream_q.get(timeout=remaining)
                except queue.Empty:
                    timeout_val = _get_timeout()
                    self._write_chunk(json.dumps({
                        "channel": "result",
                        "ok": False,
                        "error": (
                            f"Bridge timeout: command exceeded {timeout_val:.0f}s limit. "
                            "Increase timeout in addon preferences."
                        ),
                    }) + "\n")
                    return

                if channel == _STREAM_DONE:
                    self._write_chunk(
                        json.dumps({"channel": "result", **data}) + "\n"
                    )
                    return
                else:
                    self._write_chunk(
                        json.dumps({"channel": channel, "data": data}) + "\n"
                    )
        finally:
            self._end_chunked()

    def _write_chunk(self, text):
        encoded = text.encode("utf-8")
        self.wfile.write(f"{len(encoded):x}\r\n".encode())
        self.wfile.write(encoded)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _end_chunked(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    # -- Buffered: classic single JSON response -------------------------

    def _do_buffered(self, stream_q):
        deadline = time.monotonic() + _get_timeout()
        stdout_parts = []
        stderr_parts = []

        while True:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                channel, data = stream_q.get(timeout=remaining)
            except queue.Empty:
                timeout_val = _get_timeout()
                self._write_json(504, {
                    "ok": False,
                    "error": (
                        f"Bridge timeout: command exceeded {timeout_val:.0f}s limit. "
                        "Increase timeout in addon preferences "
                        "(Edit > Preferences > Add-ons > Blender Bridge) "
                        "if running long operations."
                    ),
                })
                return

            if channel == _STREAM_DONE:
                data["stdout"] = "".join(stdout_parts)
                data["stderr"] = "".join(stderr_parts)
                self._write_json(200, data)
                return
            elif channel == "stdout":
                stdout_parts.append(data)
            elif channel == "stderr":
                stderr_parts.append(data)

    def log_message(self, format, *args):
        return

    def _write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server():
    global _server, _server_thread, _active
    if _server is not None:
        return
    port = _get_port()
    _clear_queue(request_queue)
    # Bind directly: probing then binding would race other Blender instances.
    for candidate in chain(range(port, 65536), range(1024, port)):
        if candidate in _retired_ports:
            continue
        try:
            _server = ThreadingHTTPServer(("127.0.0.1", candidate), _BridgeHandler)
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
    else:
        raise OSError("No available Blender Bridge port")
    port = _server.server_port
    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    _active = True
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=POLL_INTERVAL, persistent=True)
    print(f"Blender Bridge listening for HTTP POST on http://127.0.0.1:{port}")


def _stop_server():
    global _server, _server_thread, _active
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    with _queue_lock:
        server = _server
        _server = None
        while not request_queue.empty():
            _, _, stream_q = request_queue.get_nowait()
            stream_q.put((_STREAM_DONE, {"ok": False, "error": "Bridge stopped before execution"}))
    if server is not None:
        server.shutdown()
        server.server_close()
    if _server_thread is not None:
        _server_thread.join(timeout=1.0)
        _server_thread = None
    _clear_queue(request_queue)
    _active = False
    print("Blender Bridge stopped")


def _poll():
    if _server is None:
        return None

    try:
        request_id, code, stream_q = request_queue.get_nowait()
    except queue.Empty:
        return POLL_INTERVAL

    _execute_code(code, stream_q)
    return POLL_INTERVAL


@persistent
def _load_pre(_filepath):
    global _restart_after_load
    _restart_after_load = _active
    if _active:
        _retired_ports.add(_get_port())
        _stop_server()


@persistent
def _load_post(_filepath):
    global _restart_after_load
    if _restart_after_load:
        _restart_after_load = False
        try:
            _start_server()
        except OSError as exc:
            print(f"Blender Bridge could not restart: {exc}")


# --- Terminal ---

def _terminal_dir():
    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)
    path = os.path.join(os.path.expanduser("~"), "Documents", "blender-bridge-sessions")
    os.makedirs(path, exist_ok=True)
    return path


def _open_terminal():
    path = _terminal_dir()
    if sys.platform == "darwin":
        # Both apps open a new window at a folder passed as a document.
        has_ghostty = subprocess.run(["open", "-Ra", "Ghostty"], capture_output=True).returncode == 0
        subprocess.run(["open", "-a", "Ghostty" if has_ghostty else "Terminal", path], check=True)
    elif sys.platform == "win32":
        subprocess.Popen(["powershell.exe", "-NoExit"], cwd=path,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        for term in ("ghostty", "x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
            if shutil.which(term):
                subprocess.Popen([term], cwd=path)
                break
        else:
            raise OSError("No terminal emulator found")
    return path


# --- Operators ---

class BRIDGE_OT_toggle(bpy.types.Operator):
    bl_idname = "bridge.toggle"
    bl_label = "Toggle Blender Bridge"
    bl_description = (
        "Click to toggle bridge. Ctrl+Click to copy agent instructions. "
        "Ctrl+Alt+Click to activate bridge and open a terminal in the project folder"
    )

    def invoke(self, context, event):
        if event.ctrl and event.alt:
            if not _active:
                result = self.execute(context)
                if result == {'CANCELLED'}:
                    return result
            try:
                path = _open_terminal()
            except (OSError, subprocess.SubprocessError) as exc:
                self.report({'ERROR'}, f"Could not open terminal: {exc}")
                return {'CANCELLED'}
            self.report({'INFO'}, f"Opened terminal in {path}")
            return {'FINISHED'}
        if event.ctrl:
            result = BRIDGE_OT_copy_instructions._copy(context)
            if result == {'CANCELLED'}:
                self.report({'ERROR'}, "Start the bridge first and ensure agent_instructions.md exists in the addon folder")
            else:
                self.report({'INFO'}, "Agent instructions copied to clipboard")
            return result
        return self.execute(context)

    def execute(self, context):
        if _active:
            _stop_server()
        else:
            try:
                _start_server()
            except OSError as exc:
                self.report({'ERROR'}, f"Could not start Blender Bridge: {exc}")
                return {'CANCELLED'}
        for area in context.screen.areas:
            if area.type == 'TOPBAR':
                area.tag_redraw()
        return {'FINISHED'}


class BRIDGE_OT_copy_instructions(bpy.types.Operator):
    bl_idname = "bridge.copy_instructions"
    bl_label = "Copy Agent Instructions"
    bl_description = "Copy startup instructions for your coding agent to the clipboard"

    @staticmethod
    def _copy(context):
        if not _active:
            return {'CANCELLED'}
        port = _get_port()
        addon_dir = os.path.dirname(__file__)

        # Load global instructions (ships with addon)
        global_path = os.path.join(addon_dir, "agent_instructions.md")
        try:
            with open(global_path, "r", encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            return {'CANCELLED'}

        # Append local instructions if present
        local_path = os.path.join(addon_dir, "agent_instructions.local.md")
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                text += "\n\n" + f.read()
        except FileNotFoundError:
            pass

        text = text.replace("{{PORT}}", str(port))
        text = text.replace("{{FILE}}", json.dumps(bpy.data.filepath or "(unsaved)", ensure_ascii=False))
        text = text.replace("{{TIMEOUT}}", f"{_get_timeout():g}")

        context.window_manager.clipboard = text
        return {'FINISHED'}

    def execute(self, context):
        result = self._copy(context)
        if result == {'CANCELLED'}:
            self.report({'ERROR'}, "Start the bridge first and ensure agent_instructions.md exists in the addon folder")
        else:
            self.report({'INFO'}, "Agent instructions copied to clipboard")
        return result


# --- Preferences ---

class BridgePreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    port: bpy.props.IntProperty(
        name="Port",
        default=9876,
        min=1024,
        max=65535,
        description="Preferred starting port; occupied ports are skipped automatically",
    )

    timeout: bpy.props.FloatProperty(
        name="Timeout (seconds)",
        default=60.0,
        min=1.0,
        soft_max=3600.0,
        description="HTTP response wait limit; timeout does not cancel Python execution",
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
        layout.operator("bridge.toggle", text=str(_get_port()), icon_value=icon_id, depress=True)
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
    bpy.app.handlers.load_pre.append(_load_pre)
    bpy.app.handlers.load_post.append(_load_post)
    bpy.app.handlers.load_post_fail.append(_load_post)


def unregister():
    global _icon_collection
    global _restart_after_load
    _restart_after_load = False
    _stop_server()
    bpy.app.handlers.load_pre.remove(_load_pre)
    bpy.app.handlers.load_post.remove(_load_post)
    bpy.app.handlers.load_post_fail.remove(_load_post)
    bpy.types.TOPBAR_HT_upper_bar.remove(_draw_topbar)
    bpy.utils.unregister_class(BridgePreferences)
    bpy.utils.unregister_class(BRIDGE_OT_copy_instructions)
    bpy.utils.unregister_class(BRIDGE_OT_toggle)
    if _icon_collection:
        bpy.utils.previews.remove(_icon_collection)
        _icon_collection = None
