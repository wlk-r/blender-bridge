# Blender Bridge Development Workflow

`C:\Users\Walker\Dev\blender-bridge` is now the standalone source of truth for this addon.

## Day-to-day workflow

Work directly in this repo:

```bash
git add .
git commit -m "<change>"
git push origin main
```

## Local-only files

These files are intentionally ignored and may exist only on your machine:

- `CLAUDE.md`
- `agent_instructions.local.md`
- `task_symlinkorjunction.md`
- `dist/`
- `__pycache__/`
- `*.zip`

Use them for local agent guidance, machine-specific notes, and build artifacts.

## Tracked docs

Project-wide behavior and usage belong in tracked files:

- `README.md`
- `agent_instructions.md`
- `DEVELOPMENT.md`

If a note is important for future contributors or for the public repo, move it into one of those tracked files instead of leaving it only in `CLAUDE.md` or another ignored local file.

## Notes

- The old `blender-toolbox/blender-bridge` copy has been removed from the parent repo and should no longer be used for development.
- Blender Bridge now uses HTTP POST to `http://localhost:9876`.
- Blocking Blender-side code such as `time.sleep()` will freeze the UI while it runs.
