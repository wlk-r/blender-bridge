"""Run: blender --background --factory-startup --python tests/test_bridge.py"""
import errno
import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import bpy

ROOT = Path(__file__).resolve().parents[1]
JSON = 'application/json'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / '__init__.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # lets the package import its own submodules
    spec.loader.exec_module(module)
    return module


def fetch(url, code=None, headers=None):
    """(status, body) of one HTTP call; a GET when there is no code."""
    data = None if code is None else code.encode()
    try:
        with urlopen(Request(url, data=data, headers=headers or {}), timeout=10) as response:
            return response.status, response.read().decode()
    except HTTPError as exc:
        return exc.code, exc.read().decode()


def request(module, code, accept=None):
    """POST code and run the module's queue by hand: timers do not fire in background mode."""
    result = []

    def send():
        try:
            result.append(fetch(module._url(), code, {'Accept': accept} if accept else None))
        except Exception as exc:
            result.append(exc)

    thread = threading.Thread(target=send)
    thread.start()
    deadline = time.monotonic() + 10
    while thread.is_alive() and time.monotonic() < deadline:
        module._poll()
        time.sleep(0.01)
    thread.join(timeout=1)
    assert not thread.is_alive(), 'HTTP request hung'
    assert not isinstance(result[0], Exception), result[0]
    return result[0]


def ask(module, code):
    status, body = request(module, code, JSON)
    return status, json.loads(body)


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
    assert first._server.token != second._server.token

    # Plain text by default: the body is what the script printed, the status says how it went.
    assert request(first, 'print("first")') == (200, 'first\n')
    assert request(second, 'print("second")') == (200, 'second\n')
    assert request(first, 'x = 1') == (200, '[bridge] ok (no output)\n')
    assert request(first, 'if __name__ == "__main__": print("main")') == (200, 'main\n')
    assert request(first, 'import sys; print(sys.stdout.isatty(), sys.stdout.encoding)') == (200, 'False utf-8\n')

    # A failure is a 500 with the output so far and a traceback that starts in the request's own code.
    status, body = request(first, 'print("before")\ndef f():\n    raise ValueError("test")\nf()')
    assert status == 500 and body.startswith('before\nTraceback'), body
    assert 'raise ValueError("test")' in body and '_execute' not in body and '__init__.py' not in body, body
    status, body = request(first, 'print(')
    assert status == 500 and 'SyntaxError' in body, body

    # sys.exit() must not take Blender down with it.
    status, body = request(first, 'import sys\nsys.exit(3)')
    assert status == 500 and 'SystemExit: 3' in body and 'Blender stays open' in body, body
    assert request(first, 'import sys\nprint("alive")\nsys.exit(0)') == (200, 'alive\n')

    # JSON and streaming on request.
    status, reply = ask(first, 'import sys\nprint("out")\nprint("err", file=sys.stderr)')
    assert status == 200, reply
    assert reply == {'ok': True, 'id': reply['id'], 'state': 'done', 'stdout': 'out\n', 'stderr': 'err\n'}
    status, reply = ask(first, 'raise ValueError("test")')
    assert status == 500 and not reply['ok'] and 'ValueError: test' in reply['error']
    lines = [json.loads(line) for line in request(second, 'print("stream")', 'application/x-ndjson')[1].splitlines()]
    assert lines[0] == {'channel': 'stdout', 'data': 'stream\n'}
    assert lines[-1] == {'channel': 'result', 'ok': True, 'id': lines[-1]['id'], 'state': 'done'}

    # Only the copied URL works: no token, another session's token and web pages are refused, unrun.
    base = f'http://127.0.0.1:{first._get_port()}'
    marker = 'import bpy; bpy.app.driver_namespace["bridge_test_ran"] = True'
    for url, headers in (
        (base, None),
        (f'{base}/{second._server.token}', None),
        (first._url(), {'Origin': 'https://example.com'}),
    ):
        status, body = fetch(url, marker, headers)
        assert status == 403 and body.startswith('[bridge]'), (url, status, body)
    assert fetch(base)[0] == 403
    assert first.request_queue.empty() and 'bridge_test_ran' not in bpy.app.driver_namespace

    # Status is answered without Blender's main thread.
    first._poll()
    status, body = fetch(first._url())
    state = json.loads(body)
    assert status == 200 and state['running'] is None and state['queued'] == 0 and not state['busy'], state
    assert state['blender'] == bpy.app.version_string and state['timeout'] == 2

    # A request that outlives the reply window keeps running, keeps its output, and can be collected.
    prefs.timeout = 0.5
    first._poll()
    late = {}

    def send_late():
        time.sleep(0.2)
        late['reply'] = fetch(first._url(), 'print("second ran")', {'Accept': JSON})

    waiter = threading.Thread(target=send_late)
    waiter.start()
    status, slow = ask(first, 'import time\nprint("step 1")\ntime.sleep(1.2)\nprint("step 2")')
    assert status == 504 and slow['state'] == 'running' and slow['stdout'] == 'step 1\n', slow
    assert f"{first._url()}/result/{slow['id']}" in slow['error']
    waiter.join()
    status, queued = late['reply'][0], json.loads(late['reply'][1])
    assert status == 504 and queued['state'] == 'queued' and 'do not send it again' in queued['error'], queued
    first._poll()
    assert fetch(f"{first._url()}/result/{slow['id']}") == (200, 'step 1\nstep 2\n')
    assert fetch(f"{first._url()}/result/{queued['id']}") == (200, 'second ran\n')
    assert fetch(f'{first._url()}/result/99999')[0] == 404
    prefs.timeout = 2
    first._poll()

    context = SimpleNamespace(window_manager=SimpleNamespace(clipboard=''))
    bound_port = first._get_port()
    prefs.port = 65535
    assert first.BRIDGE_OT_copy_instructions._copy(context) == {'FINISHED'}
    assert first._url() in context.window_manager.clipboard
    assert '{{' not in context.window_manager.clipboard
    calls = []
    first._draw_topbar(SimpleNamespace(layout=SimpleNamespace(operator=lambda *a, **kw: calls.append(kw))), SimpleNamespace(region=SimpleNamespace(alignment='RIGHT')))
    assert calls[0]['text'] == str(bound_port)
    prefs.port = preferred

    # Exercise real file-load callbacks, including persistent registration.
    old_token = first._server.token
    bpy.ops.wm.read_homefile(use_empty=True)
    assert first._active and first._get_port() != bound_port and first._server.token != old_token
    assert bound_port in first._retired_ports
    assert request(first, 'print(len(bpy.data.objects))') == (200, '0\n')
    # Loading a file from a request must not deadlock shutdown, and the reply says where the bridge went.
    previous_port = first._get_port()
    status, body = request(first, 'bpy.ops.wm.read_homefile(use_empty=True)')
    assert first._get_port() != previous_port
    assert (status, body) == (200, f'[bridge] A file was loaded; this Blender now answers at {first._url()}\n'), body
    status, reply = ask(first, 'bpy.ops.wm.read_homefile(use_empty=True)')
    assert status == 200 and reply['url'] == first._url()

    pending = first._Job('raise AssertionError("must not run")')
    first.request_queue.put(pending)
    first._stop_server()
    assert pending.snapshot()[2] == {'ok': False, 'error': 'Bridge stopped before execution'}
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
    print('PASS: ports, tokens, text/JSON/streaming replies, errors, exit guard, status, late results, '
          'clipboard, UI, file rotation, cleanup, bind errors')
finally:
    first.unregister()
    second._stop_server()
    blocker.close()
