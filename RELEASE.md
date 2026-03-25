# Release Checklist

- Build zip: `blender.exe --command extension build --source-dir . --output-dir ./dist`
- In Blender, remove any dev symlink/junction install of `blender_bridge`
- Install `dist/blender_bridge-<version>.zip` via `Install from Disk`
- Confirm no manifest warning appears
- Enable addon and verify toggle, `Copy Agent Instructions`, and HTTP POST to `http://localhost:9876`
- Re-enable dev symlink/junction after release testing if needed
