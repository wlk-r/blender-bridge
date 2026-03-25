# Blender Bridge Development Workflow

`blender-bridge/` is a subdirectory of the parent repo `blender-toolbox`, and it is published to its own public repo with `git subtree`.

## Source of truth

- Develop inside `blender-toolbox/blender-bridge`
- Commit changes in the parent repo
- Push the parent repo to `origin`
- Publish the addon to the standalone repo with `git subtree push`

Do not treat `blender-bridge/` as an independent local git repo. It does not have its own `.git` directory.

## Day-to-day workflow

From the parent repo root:

```bash
git add blender-bridge
git commit -m "blender-bridge: <change>"
git push origin main
git subtree push --prefix=blender-bridge blender-bridge main
```

## If subtree push is rejected

That means the standalone repo has commits that are not represented in the parent repo subtree history.

From the parent repo root:

```bash
git subtree pull --prefix=blender-bridge blender-bridge main --squash
git subtree push --prefix=blender-bridge blender-bridge main
```

Resolve conflicts in `blender-bridge/` if they appear, commit, then rerun the subtree push.

## Remote setup

Check remotes:

```bash
git remote -v
```

Expected remotes:

- `origin` -> `https://github.com/wlk-r/blender-toolbox.git`
- `blender-bridge` -> `https://github.com/wlk-r/blender-bridge.git`

Add the standalone remote if needed:

```bash
git remote add blender-bridge https://github.com/wlk-r/blender-bridge.git
```

## Notes

- `git push` run from inside `blender-bridge/` still pushes the parent repo because git resolves to the parent repo root.
- HTTP bridge behavior and agent usage rules are documented in `README.md`, `CLAUDE.md`, and `agent_instructions.md`.
