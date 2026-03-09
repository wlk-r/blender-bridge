## Blender Bridge

A Blender Bridge socket is available on `localhost:{{PORT}}`. Use the included helper to run Python in the user's Blender session:

    bash "{{EXEC_PATH}}" '<python code>'

Full `bpy` API access — query scene state, create/modify objects, run operators, change settings.

### Quick reference

- **Fresh namespace each call** — import modules and define everything in the same call. Only changes to `bpy.data` / the scene persist between calls.
- **Use `print()` for output** — stdout is captured and returned in the response.
- **Mode matters** — check/set mode with `bpy.ops.object.mode_set(mode='OBJECT')` before calling operators.
- **Selection pattern** — set active and selected objects before calling operators:
  ```python
  obj.select_set(True)
  bpy.context.view_layer.objects.active = obj
  ```

### Tips

- **Inspect before modifying** — query the scene first (e.g. `print(bpy.data.objects.keys())`, mesh vertex/face counts, materials) before making changes.
- **Avoid `bpy.ops.object.select_all`** — it polls for `VIEW_3D` and may fail. Use `obj.select_set(True/False)` directly.
- **Multi-line scripts work** — send full multi-line Python, not just one-liners. Use try/except for useful error info.
- **Timeout** — commands time out after 60s by default. Long operations (rendering, complex scripts) may need the limit increased in addon preferences.