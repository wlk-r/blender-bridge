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

Open each file in a separate Blender process and enable its bridge to work with multiple files simultaneously. Each bridge binds its own available port, starting at the preferred port and skipping occupied ports. Ctrl-click copies the actual port, current file, and timeout; the bridge must be active. Loading another file in an active process rotates its port, so copy fresh instructions afterward. Saving or renaming the current file keeps its port.

The agent instructions tell your coding agent everything it needs to know about the HTTP protocol, safety rules, and Blender-specific gotchas.

### Quick test

Replace `9876` with the port displayed in Blender.

```bash
curl -X POST http://127.0.0.1:9876 -d 'print(bpy.data.objects.keys())'
```

## Agent Instructions

The addon ships with two instruction files that get copied to the agent's context:

| File | Purpose |
|---|---|
| `agent_instructions.md` | Global instructions shared with every agent. Covers the HTTP protocol, safety rules, tips, and gotchas. Edit with care — changes affect all agents. |
| `agent_instructions.local.md` | Optional. Your personal preferences, project-specific prompts, or extra context. Create this file in the addon folder to append to the global instructions. Not tracked by git. |

## Visual feedback

For an occasional visual check, have the agent request a viewport render rather than a full engine render. `render.opengl` draws the scene the way the 3D viewport does, so it finishes in milliseconds regardless of Cycles settings, and the resolution is set explicitly to keep the image small. The agent then reads the PNG with its own file tool.

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
| Port | `9876` | Preferred starting port; skips occupied ports (restart to apply) |
| Timeout | `60s` | Response wait limit; does not cancel execution |

## Limitations

- **Localhost only** — not designed for remote access
- **Sequential** — one command at a time
- **Blocking code freezes Blender** — time-intensive scripts or operations may temporarily freeze Blender's UI until completion. A "Not Responding" window title often just indicates a background process is still running
- **Security** — runs `exec()` on received code; only use on trusted machines

---

Created by [Walker Nosworthy](https://github.com/wlk-r) | [MIT License](LICENSE)
