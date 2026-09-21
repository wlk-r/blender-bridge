"""Run: blender --background --factory-startup --python tests/test_bridge.py"""
import errno
import importlib.util
import json
import queue
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen

import bpy

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request(module, code, streaming=False):
    result = []
    errors = []
    port = module._get_port()

    def send():
        try:
            headers = {'Accept': 'application/x-ndjson'} if streaming else {}
            with urlopen(Request(f'http://127.0.0.1:{port}', data=code.encode(), headers=headers), timeout=5) as response:
                result.append(response.read().decode())
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=send)
    thread.start()
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:
        module._poll()
        time.sleep(0.01)
    thread.join(timeout=1)
    assert not thread.is_alive(), 'HTTP request hung'
    assert not errors, errors
    return result[0]


first, second = load('bridge_test_one'), load('bridge_test_two')
blocker = socket.socket()
blocker.bind(('127.0.0.1', 0))
blocker.listen()
preferred = blocker.getsockname()[1]
prefs = SimpleNamespace(port=preferred, timeout=2)
first._get_prefs = second._get_prefs = lambda: prefs
try:
    first.register()
    first._start_server()
    second._start_server()
    assert len({preferred, first._get_port(), second._get_port()}) == 3
    assert json.loads(request(first, 'print("first")'))['stdout'] == 'first\n'
    assert json.loads(request(second, 'print("second")'))['stdout'] == 'second\n'
    assert not json.loads(request(first, 'raise ValueError("test")'))['ok']
    lines = [json.loads(line) for line in request(second, 'print("stream")', True).splitlines()]
    assert lines[-1] == {'channel': 'result', 'ok': True}
    assert any(line.get('data') == 'stream' for line in lines)
    context = SimpleNamespace(window_manager=SimpleNamespace(clipboard=''))
    bound_port = first._get_port()
    prefs.port = 65535
    assert first.BRIDGE_OT_copy_instructions._copy(context) == {'FINISHED'}
    assert f'127.0.0.1:{bound_port}' in context.window_manager.clipboard
    assert '{{' not in context.window_manager.clipboard
    calls = []
    first._draw_topbar(SimpleNamespace(layout=SimpleNamespace(operator=lambda *a, **kw: calls.append(kw))), SimpleNamespace(region=SimpleNamespace(alignment='RIGHT')))
    assert calls[0]['text'] == str(bound_port)
    prefs.port = preferred
    # Exercise real file-load callbacks, including persistent registration.
    bpy.ops.wm.read_homefile(use_empty=True)
    assert first._active and first._get_port() != bound_port
    assert bound_port in first._retired_ports
    assert json.loads(request(first, 'print(len(bpy.data.objects))'))['stdout'] == '0\n'
    # Loading a file from an HTTP command must not deadlock shutdown.
    previous_port = first._get_port()
    assert json.loads(request(first, 'bpy.ops.wm.read_homefile(use_empty=True)'))['ok']
    assert first._get_port() != previous_port
    pending = queue.Queue()
    first.request_queue.put((999, 'raise AssertionError("must not run")', pending))
    first._stop_server()
    assert pending.get_nowait()[1]['ok'] is False
    assert first.BRIDGE_OT_copy_instructions._copy(context) == {'CANCELLED'}
    assert not bpy.app.timers.is_registered(first._poll)
    # Non-collision bind failures must surface, not scan every port.
    with patch.object(first, 'ThreadingHTTPServer', side_effect=OSError(errno.EACCES, 'denied')):
        try:
            first._start_server()
        except OSError as exc:
            assert exc.errno == errno.EACCES
        else:
            raise AssertionError('Expected permission error')
    assert first._server is None and not first._active
    print('PASS: port collisions, independent servers, HTTP/streaming, clipboard, UI, file rotation, cleanup, bind errors')
finally:
    first.unregister()
    second._stop_server()
    blocker.close()
