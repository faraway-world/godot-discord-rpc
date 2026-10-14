<div align="center">

# godot-discord-rpc

**Plug-and-play, zero-dependency Discord Rich Presence (RPC) integration for the [Godot 4](https://godotengine.org) Editor.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)]()
[![Runtime](https://img.shields.io/badge/Runtime-Python%203.8%2B%20(Stdlib%20Only)-yellow.svg)]()
[![Engine](https://img.shields.io/badge/Engine-Godot%20%E2%89%A54.0-brightgreen.svg)](https://godotengine.org)

</div>

---

## Overview

`godot-discord-rpc` is an automated, zero-dependency Rich Presence integration designed specifically for the **Godot 4 Editor**. It tracks real-time scene editing, script navigation, caret line positions, and game playtesting sessions, publishing live status directly to Discord without requiring native binary modules (`GDExtension` / `GDNative`), external package managers (`pip`), or manual background service management.

The integration operates out of the box with zero external dependencies and cleans up its presence immediately upon editor shutdown.

---

## Key Features

- **Automated Lifecycle & Zero Configuration**: Runs automatically once enabled in **Project Settings > Plugins**. No compilation, C++ toolchains, or third-party Python packages (`pypresence`) required.
- **Granular Activity Tracking**:
  - **Script Editor**: Broadcasts the active script file alongside 1-indexed caret line and total line count:
    ```text
    Editing player_controller.gd [Line:42/Total:280]
    Project: ChronoFracture
    ```
  - **2D & 3D Scene Viewports**: Detects active workspace tab and scene root:
    ```text
    Editing main_level.tscn (3D)
    Project: ChronoFracture
    ```
  - **Playtesting & Debugging**: Automatically transitions status when launching scenes or testing the project (`F5` / `F6`):
    ```text
    Testing: main_level.tscn
    Project: ChronoFracture
    ```
  - **Idle State**: Indicates idle editor status when no buffer or scene is focused.
- **Official Godot Art Assets**: Automatically connects to the verified Discord Application ID (`928212232213520454`) pre-loaded with official Godot badges (`icon_color`, `logo_vertical_color`, `2d`, `3d`, `script`, `assetlib`).
- **PID-Bound Process Monitoring**: Couples daemon liveness directly to the Godot Editor's process ID (`OS.get_process_id()`), guaranteeing immediate presence teardown within 1 second of closing the editor.
- **Rate-Limit Throttling**: Implements an asynchronous 15-second debounce queue adhering strictly to Discord's local IPC rate limits.

---

## Architecture

Godot's sandboxed scripting environment does not expose native Unix Domain Socket APIs (`StreamPeerUNIX`) on POSIX systems. `godot-discord-rpc` resolves this through a lightweight, decoupled hybrid architecture:

```
┌────────────────────────────────────────────────────────┐
│                   Godot 4 Editor                       │
│                                                        │
│  plugin.gd (@tool EditorPlugin)                        │
│  ├─ Hooks: main_screen_changed, scene_changed, timer   │
│  ├─ Queries: EditorInterface, ScriptEditor, CodeEdit   │
│  └─ Writes atomic JSON payload to runtime path         │
└──────────────────────────┬─────────────────────────────┘
                           │ Atomic JSON Flush (every 2s)
                           ▼
┌────────────────────────────────────────────────────────┐
│        $XDG_RUNTIME_DIR/godot-discord-rpc.json         │
└──────────────────────────┬─────────────────────────────┘
                           │ Poll & Debounce (15s throttle)
                           ▼
┌────────────────────────────────────────────────────────┐
│           daemon.py (Pure Python 3 Stdlib)             │
│                                                        │
│  ├─ Single-instance mutual exclusion lock (flock)      │
│  ├─ PID liveness verification (os.kill(pid, 0))        │
│  └─ Length-prefixed binary frames (Opcode 0 / Opcode 1)│
└──────────────────────────┬─────────────────────────────┘
                           │ Unix Domain Socket / Named Pipe
                           ▼
┌────────────────────────────────────────────────────────┐
│              Discord Desktop Client (IPC)              │
│       /run/user/<uid>/discord-ipc-0 or \\.\pipe\...    │
└──────────────────────────┬─────────────────────────────┘
```

1. **Editor Plugin (`plugin.gd`)**: Collects editor context (active script, caret position, scene tree, playtesting state), tracks the session start timestamp, and writes an atomic state file to `$XDG_RUNTIME_DIR/godot-discord-rpc.json`.
2. **Background Micro-Daemon (`daemon.py`)**: A lightweight Python worker launched seamlessly by the plugin. It establishes a raw IPC stream to Discord's local socket (`/run/user/<uid>/discord-ipc-0` or Windows Named Pipe), maps editor state to official Godot art badges, and ensures graceful teardown.

---

## Installation

### Option 1: Direct Project Installation (Recommended)

1. Clone or copy `addons/godot_discord_rpc` into your Godot project's `addons/` directory:
   ```bash
   git clone https://github.com/faraway-world/godot-discord-rpc.git /tmp/godot-discord-rpc
   cp -r /tmp/godot-discord-rpc/addons/godot_discord_rpc res://addons/
   ```
2. In the Godot Editor, navigate to **Project > Project Settings > Plugins**.
3. Locate **Godot Discord RPC** and set its status to **Enabled**.

### Option 2: Symlink for Local Development
On Linux/macOS, symlink the addon into any project to share a single codebase:
```bash
ln -s /home/user/godot-discord-rpc/addons/godot_discord_rpc /path/to/project/addons/godot_discord_rpc
```

---

## Configuration (Optional)

The integration operates out of the box with zero configuration. To override default parameters, export environment variables:

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `GODOT_DISCORD_CLIENT_ID` | `string` | `"928212232213520454"` | Custom Discord Application ID from the Discord Developer Portal. |

---

## System Requirements

- **Engine**: [Godot Engine](https://godotengine.org) v4.0 or higher (Standard and .NET / Mono builds supported).
- **Python**: Python 3.8+ (standard library only; no virtual environment or `pip` packages required).
- **Discord**: Official Discord Desktop client running locally with active IPC sockets:
  - Linux: `$XDG_RUNTIME_DIR/discord-ipc-0` or `/run/user/<uid>/discord-ipc-0`
  - macOS: `$TMPDIR/discord-ipc-0`
  - Windows: `\\.\pipe\discord-ipc-0`

---

## Troubleshooting

### Presence Does Not Appear in Discord
1. **Verify Discord Client**: Ensure the Discord desktop client is open and running under the same user session.
2. **Verify Plugin State**: Check that the plugin is toggled on under **Project > Project Settings > Plugins**.
3. **Manual Daemon Test**: Run the micro-daemon directly in a terminal to inspect diagnostic logs:
   ```bash
   python3 res://addons/godot_discord_rpc/daemon.py
   ```

### Immediate Teardown
When Godot closes, the daemon's PID tracker detects the termination within 1 second and dispatches an empty frame (`SET_ACTIVITY null`) to Discord before exiting.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
