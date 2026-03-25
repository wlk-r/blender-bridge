# Blender Bridge

Give your AI coding agent direct access to your live Blender scene.

Blender Bridge is a lightweight Blender addon that opens a local HTTP server so any coding agent can execute Python in your running Blender session. No MCP server, no Node.js, no helper scripts. Install the addon, toggle it on, and your agent has full `bpy` API access.

## Install

1. Download the zip from [Releases](https://github.com/wlk-r/blender-bridge/releases)
2. In Blender: **Edit > Preferences > Add-ons > Install** — select the zip
3. Enable **Blender Bridge** in the addon list

## Usage

1. Click the bridge icon in the top-right of the top bar to start the server
2. **Ctrl+Click** the same icon to copy the agent instructions to your clipboard
3. Paste those instructions into your coding agent's context (chat, project docs, etc.)
4. Your agent can now talk to Blender

That's it. The agent instructions tell your coding agent everything it needs to know about the HTTP protocol, safety rules, and Blender-specific gotchas.

### Quick test

```bash
curl -X POST http://localhost:9876 -d 'print(bpy.data.objects.keys())'
```

## Agent Instructions

The addon ships with two instruction files that get copied to the agent's context:

| File | Purpose |
|---|---|
| `agent_instructions.md` | Global instructions shared with every agent. Covers the HTTP protocol, safety rules, tips, and gotchas. Edit with care — changes affect all agents. |
| `agent_instructions.local.md` | Optional. Your personal preferences, project-specific prompts, or extra context. Create this file in the addon folder to append to the global instructions. Not tracked by git. |

## Settings

In **Edit > Preferences > Add-ons > Blender Bridge**:

| Setting | Default | Description |
|---|---|---|
| Port | `9876` | HTTP port (restart bridge to apply) |
| Timeout | `60s` | Max execution time per command |

## Limitations

- **Localhost only** — not designed for remote access
- **Sequential** — one command at a time
- **Blocking code freezes Blender** — time-intensive scripts or operations may temporarily freeze Blender's UI until completion. A "Not Responding" window title often just indicates a background process is still running
- **Security** — runs `exec()` on received code; only use on trusted machines

---

Created by [Walker Nosworthy](https://github.com/wlk-r) | [MIT License](LICENSE)
