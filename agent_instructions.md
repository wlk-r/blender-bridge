Connect to the running Blender Bridge at `http://127.0.0.1:{{PORT}}` for file {{FILE}}. Use this endpoint only; other ports may control other Blender files. If it stops responding, ask for fresh Ctrl-click instructions rather than switching ports.

Send UTF-8 Python source as the raw HTTP POST body. `bpy` is already available; execution happens on Blender's main thread. No MCP setup or extra packages are needed.

Start with this read-only check (macOS/Linux):
```sh
curl --silent --show-error --noproxy '*' http://127.0.0.1:{{PORT}} --data-binary 'print({"file": bpy.data.filepath, "version": bpy.app.version_string, "scene": bpy.context.scene.name, "mode": bpy.context.mode})'
```
PowerShell:
```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:{{PORT}}' -Method Post -ContentType 'text/plain; charset=utf-8' -Body 'print({"file": bpy.data.filepath, "version": bpy.app.version_string, "scene": bpy.context.scene.name, "mode": bpy.context.mode})'
```
Verify the returned file matches the intended task before editing. If no task has been given, report the connection and wait for one.

- For multiline scripts, write a UTF-8 file and use `curl ... --data-binary @script.py` to preserve newlines and avoid shell quoting problems.
- Responses contain `ok`, `stdout`, `stderr`, and `error` on failure. Check `ok`; Python errors can still return HTTP 200. Use `print()` for results. Each request has a fresh Python namespace; scene changes persist.
- For live output, add `--no-buffer -H 'Accept: application/x-ndjson'` to curl. Lines contain `channel: stdout/stderr` with `data`, followed by `channel: result` with `ok` and optional `error`. A disconnected stream without a result is not confirmation of success.
- Inspect relevant scene state before changing it. Prefer the data API; operators may require a particular mode, selection, active object, or editor context. Use the installed Blender version's API.
- Send one command at a time. Python and synchronous rendering block Blender's UI; avoid sleeps and busy-wait loops. The current response timeout is {{TIMEOUT}} seconds (adjustable in addon preferences). A timeout does not cancel queued or running code: do not blindly retry edits.
- Stay within the user's task. Do not clear the scene, overwrite files, save, or close Blender unless the task authorizes it. Opening another file rotates the bridge port; request fresh instructions afterward.
