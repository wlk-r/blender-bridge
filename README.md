# Blender Bridge

Give your AI coding agent direct access to your live Blender scene.

Blender Bridge is a lightweight Blender addon that opens a local HTTP server so external tools like [Claude Code](https://claude.com/claude-code), Cursor, Windsurf, or any CLI agent can execute Python in your running Blender session. No MCP server, no Node.js, no helper script. Install the addon, toggle it on, and your agent has full `bpy` API access.

## Install

1. Download zip folder
2. In Blender: **Edit > Preferences > Add-ons > Install** - select the zip
3. Enable **Blender Bridge** in the addon list
4. Click the toggle icon (reads "Toggle Blender Bridge" on hover) in the top-right of the top bar to start the bridge

## Connect Your Agent

### Claude Code

Either give these directions directly to Claude Code, or add this to your project's `CLAUDE.md`:

```markdown
## Blender Bridge

A Blender Bridge HTTP endpoint is available at `http://localhost:9876`. To execute Python in the user's live Blender session, send the code as plain text in an HTTP POST request:

Mac/Linux:
    curl -X POST http://localhost:9876 -d 'print(bpy.data.objects.keys())'

Windows PowerShell:
    Invoke-RestMethod -Uri http://localhost:9876 -Method Post -Body "print(bpy.data.objects.keys())"

You have full access to the `bpy` API - you can query scene state, create/modify/delete objects, run operators, change settings, export files, and anything else Blender's Python API supports. Write the `bpy` calls directly; there are no pre-defined commands.

You can also see the scene visually:
- Full UI screenshot: `bpy.ops.screen.screenshot(filepath=path)` - see panels, settings, menus
- Viewport only: `bpy.ops.render.opengl(write_still=False)` then save from `bpy.data.images["Render Result"]` - see geometry, materials, lighting

Save screenshots to a temp file and read them when you need visual context.

Safety rules for this bridge:
- Never use `time.sleep()`, polling loops, or blocking wait commands in Blender code.
- Keep each HTTP request small and fast. Prefer short inspection or mutation steps.
- Do heavy math, planning, and text processing outside Blender, then send only the final `bpy` changes.
- Treat renders and other expensive Blender operators as intentionally UI-blocking. Warn before running them.
- If an operation may take noticeable time, tell the user before executing it.
```

### Other CLI Agents

Any agent that can make HTTP requests can use the bridge directly:

```bash
curl -X POST http://localhost:9876 -d 'print(bpy.data.objects.keys())'
curl -X POST http://localhost:9876 --data-binary @script.py
```

### Direct HTTP

The protocol is simple enough to use from any language:

```python
import json
from urllib import request

code = "print(bpy.data.objects.keys())"
req = request.Request(
    "http://localhost:9876",
    data=code.encode("utf-8"),
    method="POST",
)
with request.urlopen(req) as resp:
    result = json.loads(resp.read())
print(result["stdout"])
```

Protocol: HTTP POST to `http://localhost:9876` with a UTF-8 plain-text body containing Python code. The server responds with JSON: `{"ok": true, "stdout": "...", "stderr": "..."}` or `{"ok": false, "error": "...", "stdout": "...", "stderr": "..."}`.

## Agent Safety

This bridge executes Python on Blender's main thread. That is required for `bpy`, but it also means blocking code will freeze Blender's UI until the script returns.

- Never send `time.sleep()`, busy waits, or retry loops to Blender.
- Prefer many short requests over one large monolithic script.
- Do non-Blender computation outside Blender, then send a compact final payload.
- Query the scene first, then make targeted edits.
- Warn before running renders, heavy geometry ops, or exports that may visibly block the UI.
- HTTP client timeouts do not cancel Blender-side execution once it has started.

## Settings

In **Edit > Preferences > Add-ons > Blender Bridge**:

| Setting | Default | Description |
|---|---|---|
| Port | `9876` | HTTP port (restart bridge to apply) |
| Timeout | `60s` | Max execution time per command |

## How It Works

- A background `HTTPServer` thread accepts HTTP POST requests and puts Python code into a thread-safe queue
- A `bpy.app.timers` callback polls that queue every 0.1s on Blender's main thread
- The timer `exec()`s the code with stdout/stderr capture, then pushes the result back through a response queue
- The waiting HTTP handler returns the JSON result to the client
- Main thread execution means all `bpy` calls are safe while keeping HTTP I/O off the UI thread

## Strengths

- **Minimal** - standard library only, no helper process
- **Full API access** - anything `bpy` can do, the bridge can do
- **Safe execution** - all Blender work stays on the main thread via timer
- **Zero config** - install, toggle on, send HTTP

## Limitations

- **Localhost only** - the bridge binds to localhost; not designed for remote access
- **Sequential** - one command at a time
- **Blocking code freezes Blender** - any long-running script holds the main thread until it finishes
- **Security** - runs `exec()` on received code. Only use on trusted machines

---

Created by [Walker Nosworthy](https://github.com/wlk-r) | [MIT License](LICENSE)
