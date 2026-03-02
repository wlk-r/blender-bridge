# Blender Bridge

Give your AI coding agent direct access to your live Blender scene.

Blender Bridge is a lightweight Blender addon that opens a TCP socket so external tools — like Claude Code, Codex, Gemini, or any CLI agent — can execute Python with live updates in your running Blender session. No MCP server, no Node.js, no complex setup. Install the addon, toggle it on, and your agent has full `bpy` API access.

## Install

1. Download zip folder
2. In Blender: **Edit > Preferences > Add-ons > Install** — select the zip
3. Enable **Blender Bridge** in the addon list
4. Click the toggle icon (reads 'Toggle Blender Bridge' on hover) in the top-right of the top bar to start the bridge

## Connect Your Agent

### Claude Code

Either give these directions directly to Claude Code, or add this to your project's `CLAUDE.md`:

```markdown
## Blender Bridge

A Blender Bridge socket is available on `localhost:9876`. To execute Python in the user's live Blender session, use the included helper:

    bash /path/to/blender_exec.sh '<python code>'

You have full access to the `bpy` API — you can query scene state, create/modify/delete objects, run operators, change settings, export files, and anything else Blender's Python API supports. Write the `bpy` calls directly; there are no pre-defined commands.

You can also see the scene visually:
- Full UI screenshot: `bpy.ops.screen.screenshot(filepath=path)` — see panels, settings, menus
- Viewport only: `bpy.ops.render.opengl(write_still=False)` then save from `bpy.data.images["Render Result"]` — see geometry, materials, lighting

Save screenshots to a temp file and read them when you need visual context.
```

Replace `/path/to/blender_exec.sh` with the actual path to the helper script included in this repo.

### Other CLI Agents

Any agent that can run shell commands can use the bridge. The helper script `blender_exec.sh` handles the protocol — just pass Python code as an argument:

```bash
bash blender_exec.sh 'print(bpy.data.objects.keys())'
bash blender_exec.sh -f script.py
echo 'bpy.ops.mesh.primitive_cube_add()' | bash blender_exec.sh
```

### Direct Socket (No Helper)

The protocol is simple enough to use from any language:

```python
import socket, struct, json

code = "print(bpy.data.objects.keys())"
s = socket.socket()
s.connect(("localhost", 9876))
data = code.encode()
s.sendall(struct.pack(">I", len(data)) + data)

hdr = s.recv(4)
size = struct.unpack(">I", hdr)[0]
resp = b""
while len(resp) < size:
    resp += s.recv(size - len(resp))
s.close()

result = json.loads(resp)
print(result["stdout"])
```

Protocol: length-prefixed TCP. Client sends `[4 bytes: uint32 big-endian length][UTF-8 Python code]`. Server responds with the same framing, payload is JSON: `{"ok": true, "stdout": "...", "stderr": "..."}` or `{"ok": false, "error": "..."}`.

## Settings

In **Edit > Preferences > Add-ons > Blender Bridge**:

| Setting | Default | Description |
|---|---|---|
| Port | `9876` | TCP port (restart bridge to apply) |
| Timeout | `60s` | Max execution time per command (may need to be extended when conducting heavy tasks like rendering) |

The `blender_exec.sh` helper respects `BLENDER_BRIDGE_PORT` and `BLENDER_BRIDGE_HOST` environment variables.

## How It Works

- A `bpy.app.timers` callback polls a non-blocking socket every 0.1s on Blender's main thread
- When a connection arrives, it reads the Python code, `exec()`s it with stdout/stderr capture, and sends back the result as JSON
- Main thread execution means all `bpy` calls are safe — no threading issues
- One connection at a time, sequential processing

## Strengths

- **Minimal** — ~100 lines, no dependencies, no external processes
- **Full API access** — anything `bpy` can do, the bridge can do
- **Safe execution** — runs on Blender's main thread via timer
- **Zero config** — install, toggle on, connect

## Limitations

- **Localhost only** — the bridge binds to localhost; not designed for remote access
- **Sequential** — one command at a time
- **Security** — runs `exec()` on received code. Only use on trusted machines
- **Experimental** — outcomes not always predictable; save important work frequently

---

Created by [Walker Nosworthy](https://github.com/wlk-r) | [MIT License](LICENSE)
