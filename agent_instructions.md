## Blender Bridge

A Blender Bridge HTTP endpoint is available at `http://localhost:{{PORT}}`. Send Python code as plain text in an HTTP POST request to run it in the user's Blender session.

Mac/Linux:
    curl -X POST http://localhost:{{PORT}} -d 'print(bpy.data.objects.keys())'

Windows PowerShell:
    Invoke-RestMethod -Uri http://localhost:{{PORT}} -Method Post -Body "print(bpy.data.objects.keys())"

Response: `{"ok": true, "stdout": "...", "stderr": "..."}` or `{"ok": false, "error": "...", "stdout": "...", "stderr": "..."}`.

Full `bpy` API access — query scene state, create/modify objects, run operators, change settings.

### Streaming mode

Add `Accept: application/x-ndjson` to get real-time output as chunked NDJSON. Each line is a JSON object:

- `{"channel":"stdout","data":"..."}` — a stdout write
- `{"channel":"stderr","data":"..."}` — a stderr write
- `{"channel":"result","ok":true}` or `{"channel":"result","ok":false,"error":"..."}` — final status (always last)

Example:
    curl -X POST http://localhost:{{PORT}} -H "Accept: application/x-ndjson" -d 'for i in range(5): print(f"step {i}")'

Without the header, the bridge returns a single JSON response.

### Quick reference

- **Fresh namespace each call** — import modules and define everything in the same call. Only changes to `bpy.data` / the scene persist between calls.
- **Use `print()` for output** — stdout is captured and returned in the response.
- **Real-time feedback** — if performing a long operation (like a loop over many objects), use `print()` statements frequently. The bridge will stream these back to you in real-time, allowing you to monitor progress.
- **Mode matters** — check/set mode with `bpy.ops.object.mode_set(mode='OBJECT')` before calling operators.
- **Context-heavy operators** — many `bpy.ops` calls require the right mode, selection, active object, or area type. Check context before calling them.
- **Selection pattern** — set active and selected objects before calling operators:
  ```python
  obj.select_set(True)
  bpy.context.view_layer.objects.active = obj
  ```
  Avoid `bpy.ops.object.select_all` — it polls for `VIEW_3D` and may fail.

### Rules

- **Never use `time.sleep()` or blocking loops** — these freeze Blender's UI. Synchronous operators like `bpy.ops.render.render()` already block until finished; a sleep afterward is useless.
- **Warn before expensive actions** — renders, large geometry edits, exports, and similar operations can lock the UI while running.
- **Timeouts do not cancel execution** — if the HTTP client times out, Blender may still be running the script.

### Tips

- **Inspect before modifying** — query relevant scene state before making changes.
- **State awareness** — if unsure of the environment, quickly query `bpy.context.mode`, `bpy.context.active_object`, and `len(bpy.context.selected_objects)`.
- **Avoid bloat** — do not list all objects in `bpy.data.objects` unless explicitly asked; work only with what is relevant to the current task.
- **Multi-line scripts work** — send full multi-line Python, not just one-liners. Use try/except for useful error info.
- **Timeout** — commands time out after 60s by default. Long operations may need the limit increased in addon preferences.
- **Visual inspection** — if you need visual context beyond what scene data provides, you can capture the viewport with `bpy.ops.render.opengl()` and save from `bpy.data.images["Render Result"]`. Prefer querying scene data over taking screenshots when possible.
