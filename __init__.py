import bpy
import bpy.utils.previews
import collections
import errno
import io
import json
import linecache
import os
import queue
import secrets
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
_job_ids = count(1)
request_queue: queue.Queue = queue.Queue()  # of _Job
_jobs: collections.OrderedDict = collections.OrderedDict()  # recent _Job by id, guarded by _queue_lock
_sources: collections.deque = collections.deque()  # linecache names of recent requests
_current = None  # the _Job Blender is executing right now
_scene_updates = 0  # depsgraph updates seen, to tell edits from read-only requests
_load_count = 0
POLL_INTERVAL = 0.1
DEFAULT_PORT = 9797  # Not 9876: the Blender MCP add-ons listen there by default.
JOB_HISTORY = 16
MAX_BODY = 64 * 1024 * 1024
# Written on Blender's main thread and only read by HTTP threads, which must not touch bpy.
_timeout = 60.0
_file = ""
_last_poll = 0.0
_BLENDER = bpy.app.version_string
_ID_COLLECTIONS = [p.identifier for p in bpy.types.BlendData.bl_rna.properties if p.type == 'COLLECTION']


def _get_prefs():
    prefs = bpy.context.preferences.addons.get(__package__)
    if prefs:
        return prefs.preferences
    return None


def _get_port():
    if _server is not None:
        return _server.server_port
    prefs = _get_prefs()
    return prefs.port if prefs else DEFAULT_PORT


def _get_timeout():
    prefs = _get_prefs()
    return prefs.timeout if prefs else 60.0


def _url(server=None):
    server = server or _server
    return f"http://127.0.0.1:{server.server_port}/{server.token}" if server else ""


def _console(message):
    # The real console, not a request's captured output.
    print(message, file=sys.__stdout__, flush=True)


def _sync():
    """Refresh what HTTP threads are allowed to read. Runs on Blender's main thread."""
    global _timeout, _file, _last_poll
    _timeout = _get_timeout()
    _file = bpy.data.filepath
    _last_poll = time.monotonic()


# --- Requests ---

class _Job:
    """One posted script: its output so far and, once finished, its result."""

    def __init__(self, code):
        self.id = next(_job_ids)
        self.code = code
        self.chunks = []  # (channel, text) in arrival order
        self.state = "queued"  # queued -> running -> done
        self.result = {}
        self.created = time.monotonic()
        self.started = self.created
        self._changed = threading.Condition()

    def write(self, channel, text):
        with self._changed:
            self.chunks.append((channel, text))
            self._changed.notify_all()

    def start(self):
        with self._changed:
            self.state = "running"
            self.started = time.monotonic()

    def finish(self, ok, error=None, **extra):
        with self._changed:
            self.result = {"ok": ok, **extra}
            if error:
                self.result["error"] = error
            self.state = "done"
            self._changed.notify_all()

    def follow(self, deadline):
        """Yield output chunks as they arrive, until the job is done or the deadline passes."""
        sent = 0
        while True:
            with self._changed:
                while sent == len(self.chunks) and self.state != "done":
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    self._changed.wait(remaining)
                fresh = self.chunks[sent:]
                sent += len(fresh)
                done = self.state == "done"
            yield from fresh
            if done:
                return

    def snapshot(self):
        with self._changed:
            return self.state, list(self.chunks), dict(self.result)


class _StreamWriter(io.TextIOBase):
    """Stands in for sys.stdout/sys.stderr during a request and forwards whole lines to its job."""

    encoding = "utf-8"

    def __init__(self, job, channel):
        self._job = job
        self._channel = channel
        self._partial = ""

    def writable(self):
        return True

    def write(self, text):
        self._partial += text
        if "\n" in self._partial:
            lines, _, self._partial = self._partial.rpartition("\n")
            self._job.write(self._channel, lines + "\n")
        return len(text)

    def flush(self):
        if self._partial:
            self._job.write(self._channel, self._partial)
            self._partial = ""


def _remember_source(job):
    """Let tracebacks show the request's own source lines."""
    name = f"<bridge-{job.id}>"
    linecache.cache[name] = (len(job.code), None, job.code.splitlines(True), name)
    _sources.append(name)
    while len(_sources) > JOB_HISTORY:
        linecache.cache.pop(_sources.popleft(), None)
    return name


def _format_error(exc):
    # Skip this module's own frame so the traceback starts in the request's code.
    tb = exc.__traceback__.tb_next if exc.__traceback__ else None
    text = "".join(traceback.format_exception(type(exc), exc, tb))
    if isinstance(exc, SystemExit):
        text += "sys.exit() ends only this request: Blender stays open.\n"
    return text


@persistent
def _on_depsgraph_update(_scene, _depsgraph):
    global _scene_updates
    _scene_updates += 1


def _scene_mark():
    """A cheap fingerprint that moves when something the user could see or undo has changed."""
    try:
        bpy.context.view_layer.update()  # Deliver pending updates to _on_depsgraph_update now.
        return _scene_updates, _load_count, sum(len(getattr(bpy.data, name)) for name in _ID_COLLECTIONS)
    except Exception:
        return None


def _undo_label(job):
    """The request's leading comment names its undo step."""
    first = job.code.lstrip().split("\n", 1)[0].strip()
    label = first.lstrip("#").strip()[:60] if first.startswith("#") and not first.startswith("#!") else ""
    return f"Bridge: {label or f'request {job.id}'}"


def _push_undo(job, before):
    """Make a request's edits one undo step, so the user's undo reverts exactly that request."""
    prefs = _get_prefs()
    if not getattr(prefs, "undo", True):
        return None
    after = _scene_mark()
    if before is not None and after == before:
        return None  # Read-only: leave the user's undo history (32 steps by default) alone.
    if before is not None and after is not None and before[1] != after[1]:
        return None  # A file was loaded, which starts a new undo history.
    if "ed.undo" in job.code or "ed.redo" in job.code:
        return None  # The request drives undo itself.
    label = _undo_label(job)
    try:
        window = bpy.context.window or bpy.context.window_manager.windows[0]
        with bpy.context.temp_override(window=window):
            bpy.ops.ed.undo_push(message=label)
    except Exception:
        return None
    return label


def _execute(job):
    global _current
    url_before = _url()
    name = _remember_source(job)
    out, err = _StreamWriter(job, "stdout"), _StreamWriter(job, "stderr")
    old_stdout, old_stderr = sys.stdout, sys.stderr
    error = None
    before = _scene_mark()
    job.start()
    _current = job
    sys.stdout, sys.stderr = out, err
    try:
        namespace = {"__name__": "__main__", "__builtins__": __builtins__, "bpy": bpy}
        exec(compile(job.code, name, "exec"), namespace)
    except SystemExit as exc:  # Must not take Blender down; a zero status is a normal end of script.
        if exc.code not in (0, None):
            error = _format_error(exc)
    except BaseException as exc:  # KeyboardInterrupt included, for the same reason.
        error = _format_error(exc)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        out.flush()
        err.flush()
        _current = None
    extra = {}
    undo = _push_undo(job, before)
    if undo:
        extra["undo"] = undo
    if _server is not None and _url() != url_before:
        extra["url"] = _url()
    job.finish(error is None, error, **extra)


def _describe(job, url):
    """Reply payload for a job (its result, or where it stands) plus its output in arrival order."""
    state, chunks, result = job.snapshot()
    reply = {
        "ok": False,
        "id": job.id,
        "state": state,
        "stdout": "".join(text for channel, text in chunks if channel == "stdout"),
        "stderr": "".join(text for channel, text in chunks if channel == "stderr"),
    }
    if state == "done":
        reply.update(result)
    else:
        waited = time.monotonic() - job.created
        if state == "running":
            where = f"is still running after {waited:.0f}s"
        else:
            where = f"has waited {waited:.0f}s without starting because Blender is busy"
        reply["error"] = (
            f"Request {job.id} {where}. It was not cancelled, so do not send it again. "
            f"Collect its result with a GET to {url}/result/{job.id}"
        )
    return reply, "".join(text for _, text in chunks)


def _end_line(text):
    return text if not text or text.endswith("\n") else text + "\n"


def _as_text(reply, output):
    """Plain-text reply: what the script printed, then anything the bridge has to add."""
    text = output
    notes = []
    if reply["state"] != "done":
        notes.append(reply["error"])
    else:
        if not reply["ok"]:
            text = _end_line(text) + _end_line(reply["error"])
        if reply.get("url"):
            notes.append(f"A file was loaded; this Blender now answers at {reply['url']}")
    if notes:
        return _end_line(text) + "".join(f"[bridge] {note}\n" for note in notes)
    return text or "[bridge] ok (no output)\n"


def _status():
    now = time.monotonic()
    current = _current
    return {
        "ok": True,
        "blender": _BLENDER,
        "file": _file,
        "busy": current is not None or not request_queue.empty() or now - _last_poll > 1.0,
        "running": {"id": current.id, "seconds": round(now - current.started, 1)} if current else None,
        "queued": request_queue.qsize(),
        "timeout": _timeout,
    }


def _ignore_disconnect(method):
    def wrapper(self):
        try:
            method(self)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
    return wrapper


class _BridgeHandler(BaseHTTPRequestHandler):
    """POST /<token> runs Python. GET /<token> reports status. GET /<token>/result/<id> waits for a request."""

    server_version = "BlenderBridge/1.2"

    @_ignore_disconnect
    def do_POST(self):
        raw = self._read_body()
        if raw is None:
            return
        rest = self._authorize()
        if rest is None:
            return
        if rest:
            self._refuse(404, "POST Python source to the bridge URL itself")
            return
        if not raw:
            self._refuse(400, "Request body is required")
            return
        try:
            code = raw.decode("utf-8")
        except UnicodeDecodeError:
            self._refuse(400, "Request body must be UTF-8")
            return

        job = _Job(code.lstrip("﻿"))
        with _queue_lock:
            if self.server is not _server:
                self._refuse(503, "Bridge stopped; copy fresh instructions")
                return
            _jobs[job.id] = job
            for old in [i for i, j in _jobs.items() if j.state == "done"][:-JOB_HISTORY]:
                del _jobs[old]
            request_queue.put(job)
        self._reply(job)

    @_ignore_disconnect
    def do_GET(self):
        rest = self._authorize()
        if rest is None:
            return
        if not rest or rest == ["status"]:
            self._send(200, json.dumps(_status()), "application/json")
        elif len(rest) == 2 and rest[0] == "result" and rest[1].isdigit():
            with _queue_lock:
                job = _jobs.get(int(rest[1]))
            if job is None:
                self._refuse(404, f"No request {rest[1]} in this bridge session (the last {JOB_HISTORY} are kept)")
            else:
                self._reply(job)
        else:
            self._refuse(404, "Unknown path")

    def _read_body(self):
        """The raw request body, or None once a refusal has been sent."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0:
            self._refuse(400, "Invalid Content-Length")
            return None
        if length > MAX_BODY:
            self._refuse(413, "Request body is too large")
            return None
        # Read before any refusal, so the client gets the reply instead of a reset connection.
        return self.rfile.read(length)

    def _authorize(self):
        """Path segments after the session token, or None once a refusal has been sent."""
        if self.headers.get("Origin"):
            self._refuse(403, "Requests from web pages are not accepted")
            return None
        parts = [part for part in self.path.split("?", 1)[0].split("/") if part]
        token = getattr(self.server, "token", "")
        if not parts or not secrets.compare_digest(parts[0].encode(), token.encode()):
            self._refuse(403, (
                "Missing or wrong session token. "
                "Ctrl-click the bridge icon in Blender and paste the fresh instructions"
            ))
            return None
        return parts[1:]

    def _format(self):
        accept = self.headers.get("Accept", "")
        if "application/x-ndjson" in accept:
            return "ndjson"
        return "json" if "application/json" in accept else "text"

    def _reply(self, job):
        """Wait for the job, then answer: 200 finished, 500 the Python failed, 504 not finished yet."""
        style = self._format()
        deadline = time.monotonic() + _timeout
        if style == "ndjson":
            self._stream(job, deadline)
            return
        for _ in job.follow(deadline):
            pass
        reply, output = _describe(job, _url(self.server))
        status = 200 if reply["ok"] else 500 if reply["state"] == "done" else 504
        if style == "json":
            self._send(status, json.dumps(reply), "application/json")
        else:
            self._send(status, _as_text(reply, output), "text/plain; charset=utf-8")

    def _stream(self, job, deadline):
        """One JSON line per piece of output as it is printed, then a final `result` line."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Connection", "close")
        self.end_headers()
        for channel, text in job.follow(deadline):
            self._write_line({"channel": channel, "data": text})
        reply, _ = _describe(job, _url(self.server))
        del reply["stdout"], reply["stderr"]
        self._write_line({"channel": "result", **reply})

    def _write_line(self, payload):
        self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _refuse(self, status, message):
        if self._format() == "text":
            self._send(status, f"[bridge] {message}\n", "text/plain; charset=utf-8")
        else:
            self._send(status, json.dumps({"ok": False, "error": message}), "application/json")

    def _send(self, status, text, content_type):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


# --- Server ---

def _start_server():
    global _server, _server_thread, _active
    if _server is not None:
        return
    port = _get_port()
    # Bind directly: probing then binding would race other Blender instances.
    for candidate in chain(range(port, 65536), range(1024, port)):
        if candidate in _retired_ports:
            continue
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), _BridgeHandler)
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
    else:
        raise OSError("No available Blender Bridge port")
    # A fresh token per start: stale instructions and other local callers get a refusal.
    server.token = secrets.token_urlsafe(9)
    _sync()
    _server = server
    _server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    _server_thread.start()
    _active = True
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=POLL_INTERVAL, persistent=True)
    _console(f"Blender Bridge listening for HTTP POST on {_url()}")


def _stop_server():
    global _server, _server_thread, _active
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    with _queue_lock:
        server = _server
        _server = None
        while True:
            try:
                request_queue.get_nowait().finish(False, "Bridge stopped before execution")
            except queue.Empty:
                break
        _jobs.clear()
    if server is not None:
        server.shutdown()
        server.server_close()
    if _server_thread is not None:
        _server_thread.join(timeout=1.0)
        _server_thread = None
    _active = False
    _console("Blender Bridge stopped")


def _poll():
    if _server is None:
        return None
    try:
        _sync()
        try:
            job = request_queue.get_nowait()
        except queue.Empty:
            return POLL_INTERVAL
        _execute(job)
    except Exception:
        # An error here would silently remove the timer and leave the bridge accepting requests it never runs.
        traceback.print_exc()
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
    global _restart_after_load, _load_count
    _load_count += 1
    if _restart_after_load:
        _restart_after_load = False
        try:
            _start_server()
        except OSError as exc:
            _console(f"Blender Bridge could not restart: {exc}")


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
        # No screen when started from the command line (--python-expr).
        for area in (context.screen.areas if context.screen else ()):
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

        text = text.replace("{{URL}}", _url())
        text = text.replace("{{PORT}}", str(port))
        text = text.replace("{{TOKEN}}", _server.token)
        text = text.replace("{{FILE}}", json.dumps(bpy.data.filepath or "(unsaved)", ensure_ascii=False))
        text = text.replace("{{TIMEOUT}}", f"{_get_timeout():g}")
        text = text.replace("{{VERSION}}", _BLENDER)

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
        default=DEFAULT_PORT,
        min=1024,
        max=65535,
        description="Preferred starting port; occupied ports are skipped automatically",
    )

    timeout: bpy.props.FloatProperty(
        name="Timeout (seconds)",
        default=60.0,
        min=1.0,
        soft_max=3600.0,
        description=(
            "How long a reply waits for a request to finish. "
            "Slower requests keep running and can be collected later"
        ),
    )

    undo: bpy.props.BoolProperty(
        name="Undo Step per Request",
        default=True,
        description="After a request that changes the scene, add an undo step so that Ctrl+Z reverts that request",
    )

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.prop(self, "port")
        row.prop(self, "timeout")
        if _active:
            row.label(text="(restart bridge to apply port changes)")
        layout.prop(self, "undo")
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
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)


def unregister():
    global _icon_collection
    global _restart_after_load
    _restart_after_load = False
    _stop_server()
    while _sources:
        linecache.cache.pop(_sources.popleft(), None)
    bpy.app.handlers.load_pre.remove(_load_pre)
    bpy.app.handlers.load_post.remove(_load_post)
    bpy.app.handlers.load_post_fail.remove(_load_post)
    bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    bpy.types.TOPBAR_HT_upper_bar.remove(_draw_topbar)
    bpy.utils.unregister_class(BridgePreferences)
    bpy.utils.unregister_class(BRIDGE_OT_copy_instructions)
    bpy.utils.unregister_class(BRIDGE_OT_toggle)
    if _icon_collection:
        bpy.utils.previews.remove(_icon_collection)
        _icon_collection = None
