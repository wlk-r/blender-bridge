## Blender Bridge

A Blender Bridge HTTP endpoint is available at `http://localhost:{{PORT}}`. Send Python code as plain text in an HTTP POST request to run it in the user's Blender session.

Mac/Linux:
    curl -X POST http://localhost:{{PORT}} -d 'print(bpy.data.objects.keys())'

Windows PowerShell:
    Invoke-RestMethod -Uri http://localhost:{{PORT}} -Method Post -Body "print(bpy.data.objects.keys())"

Full `bpy` API access - query scene state, create/modify objects, run operators, change settings.

### Quick reference

- **Fresh namespace each call** - import modules and define everything in the same call. Only changes to `bpy.data` / the scene persist between calls.
- **Use `print()` for output** - stdout is captured and returned in the response.
- **Mode matters** - check/set mode with `bpy.ops.object.mode_set(mode='OBJECT')` before calling operators.
- **Selection pattern** - set active and selected objects before calling operators:
  ```python
  obj.select_set(True)
  bpy.context.view_layer.objects.active = obj
  ```

### Safety rules

- **Never block Blender** - do not use `time.sleep()`, busy waits, polling loops, or other blocking wait patterns.
- **Keep requests small** - prefer short, focused HTTP calls over one long script.
- **Compute outside Blender** - do heavy math, planning, or text/data processing locally, then send only the final `bpy` operations.
- **Warn before expensive actions** - renders, large geometry edits, exports, and similar operations can lock the UI while running.
- **Timeouts do not cancel execution** - if the HTTP client times out, Blender may still be running the script.

### Tips

- **Inspect before modifying** - query the scene first (e.g. `print(bpy.data.objects.keys())`, mesh vertex/face counts, materials) before making changes.
- **Avoid `bpy.ops.object.select_all`** - it polls for `VIEW_3D` and may fail. Use `obj.select_set(True/False)` directly.
- **Multi-line scripts work** - send full multi-line Python, not just one-liners. Use try/except for useful error info.
- **Timeout** - commands time out after 60s by default. Long operations (rendering, complex scripts) may need the limit increased in addon preferences.

### Known gotchas

- **Do not use `time.sleep()`** - it only pauses Blender's main thread and freezes the UI; it does not help Blender "wait" more safely.
- **Do not poll with retry loops** - repeated `while` loops waiting for state changes will also freeze the UI.
- **Do not "wait for render" manually** - synchronous calls like `bpy.ops.render.render()` already block until finished, so a sleep afterward is useless.
- **Do not assume context-heavy operators always work** - many `bpy.ops` calls require the right mode, selection, active object, or area type.
- **Do not send giant monolithic scripts by default** - short, targeted calls are easier to reason about and less likely to lock Blender for a long time.
