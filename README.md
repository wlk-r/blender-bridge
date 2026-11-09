# Blender Bridge

Give your AI coding agent direct access to your live Blender scene.

Blender Bridge is a lightweight Blender addon that opens a local HTTP server so any coding agent can execute Python in your running Blender session. No MCP server, no Node.js, no helper scripts. Install the addon, toggle it on, and your agent has full `bpy` API access.

## Install

1. Download the zip from [Releases](https://github.com/wlk-r/blender-bridge/releases)
2. In Blender: **Edit > Preferences > Add-ons > Install** — select the zip
3. Enable **Blender Bridge** in the addon list

## Usage

1. Click the bridge icon in the top-right of the top bar to start the server. Its active port appears beside the icon.
2. **Ctrl+Click** the same icon to copy the agent instructions to your clipboard
3. Paste those instructions into your coding agent's context (chat, project docs, etc.)
4. Your agent can now talk to Blender

Open each file in a separate Blender process and enable its bridge to work with multiple files simultaneously. Each bridge binds its own available port, starting at the preferred port and skipping occupied ports, and creates a new session token every time it starts. Ctrl-click copies the actual URL (port and token), current file, and timeout; the bridge must be active. Loading another file in an active process restarts the bridge on a new port with a new token, so copy fresh instructions afterward. Saving or renaming the current file keeps its URL.

**Ctrl+Alt+Click** the icon to activate Bridge if needed and open a terminal in the current file's folder (or `~/Documents/blender-bridge-sessions` if the file is unsaved), ready to launch your agent. Bridge stays active if it is already running. macOS uses Ghostty when installed, otherwise Terminal; Windows opens PowerShell; Linux tries Ghostty, then common terminal emulators.

The agent instructions tell your coding agent everything it needs to know about the HTTP protocol, safety rules, and Blender-specific gotchas.

### Quick test

Ctrl-click the icon and take the URL from the copied instructions. It ends with the session token.

```bash
curl http://127.0.0.1:9797/<token> --data-binary 'print(bpy.data.objects.keys())'
```

### Starting from the command line

An agent can start its own Blender and read the URL from its output:

```bash
blender --python-expr "import bpy; bpy.ops.bridge.toggle()"
# Blender Bridge listening for HTTP POST on http://127.0.0.1:9797/<token>
```

Blender needs its window: the bridge runs on a timer, and timers do not fire with `--background`.

## Agent Instructions

The addon ships with two instruction files that get copied to the agent's context:

| File | Purpose |
|---|---|
| `agent_instructions.md` | Global instructions shared with every agent. Covers the HTTP protocol, safety rules, tips, and gotchas. Edit with care — changes affect all agents. |
| `agent_instructions.local.md` | Optional. Your personal preferences, project-specific prompts, or extra context. Create this file in the addon folder to append to the global instructions. Not tracked by git. |

## Protocol

| Request | What it does |
|---|---|
| `POST <url>` with Python source as the body | Runs it on Blender's main thread and replies with its output |
| `GET <url>` | Status as JSON: whether Blender is busy, the running request, the queue length. Answered even while Blender is blocked |
| `GET <url>/result/<id>` | Waits for that request and returns its reply. The last 16 requests are kept |

The reply to a request is plain text: whatever the script printed, then the traceback if it failed. Lines starting with `[bridge]` are added by the bridge. The HTTP status tells you how it went:

| Status | Meaning |
|---|---|
| `200` | The script finished |
| `500` | The script raised an error |
| `504` | The script is still running or queued after the timeout. It was not cancelled; the reply has the output so far and the id to collect it with |
| `403` | Missing or wrong token, or the request came from a web page |

Send `Accept: application/json` for one JSON object instead (`ok`, `id`, `state`, `stdout`, `stderr`, and `error` on failure), or `Accept: application/x-ndjson` to stream output while the script runs: one JSON line per printed line (`channel`, `data`), then a final `result` line.

Each request runs as `__main__` in a fresh namespace with `bpy` imported. Tracebacks show the request's own source lines. `sys.exit()` ends the request, not Blender.

## Visual feedback

When a visual check is necessary or helpful, use the fastest suitable method: a viewport screenshot for quick checks, a viewport render for a clean preview, or an engine render for final appearance. Prefer inspecting scene data to analyze progress unless visual verification is directly applicable.

For a viewport render, the example below uses `render.opengl` with an explicit small resolution. The agent then reads the PNG with its own file tool.

```python
import bpy, os, tempfile
path = os.path.join(tempfile.gettempdir(), "bridge_view.png")
win = bpy.context.window_manager.windows[0]
area = next(a for a in win.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
r = bpy.context.scene.render
saved = (r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath, r.image_settings.file_format)
r.resolution_x, r.resolution_y, r.resolution_percentage = 960, 540, 100
r.image_settings.file_format, r.filepath = 'PNG', path
try:
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.render.opengl(write_still=True, view_context=True)
finally:
    r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath, r.image_settings.file_format = saved
print(path)
```

`view_context=True` renders what the viewport currently shows. Set it to `False` to render from the scene camera instead. Image cost scales with pixel area, so lower the resolution for quick sanity checks.

## Settings

In **Edit > Preferences > Add-ons > Blender Bridge**:

| Setting | Default | Description |
|---|---|---|
| Port | `9797` | Preferred starting port; skips occupied ports (restart to apply) |
| Timeout | `60s` | How long a reply waits. Slower requests keep running and can be collected with `GET <url>/result/<id>` |

## Limitations

- **Localhost only** — not designed for remote access
- **Sequential** — one command at a time
- **Blocking code freezes Blender** — time-intensive scripts or operations may temporarily freeze Blender's UI until completion. A "Not Responding" window title often just indicates a background process is still running
- **Security** — runs `exec()` on received code. Requests need the session token and requests from web pages are refused, but anything that can read the token (your clipboard, your terminal, Blender's console) can run code as you; only use on trusted machines

---

Created by [Walker Nosworthy](https://github.com/wlk-r) | [MIT License](LICENSE)
