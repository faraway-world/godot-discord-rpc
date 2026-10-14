#!/usr/bin/env python3
"""
Godot Engine - Discord Rich Presence Micro-Daemon
Zero-dependency, pure Python standard library Discord IPC bridge for Godot 4.
Compatible with Linux, macOS, and Windows.
"""

import os
import sys
import time
import json
import uuid
import struct
import socket
import signal
import functools
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

# Ensure unbuffered standard I/O
print = functools.partial(print, flush=True)

# Default Discord Application ID for Godot Engine
# Official community application ID with built-in asset badges (icon_color, 2d, 3d, script, logo_vertical_color)
DEFAULT_CLIENT_ID = "928212232213520454"

# Discord IPC Opcodes
OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4

def get_state_file_path() -> Path:
    """Determine state file location across platforms."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime and os.path.exists(xdg_runtime):
        return Path(xdg_runtime) / "godot-discord-rpc.json"
    tmp = os.environ.get("TMPDIR") or "/tmp"
    if os.path.exists(tmp):
        return Path(tmp) / f"godot-discord-rpc-{os.getuid() if hasattr(os, 'getuid') else 'user'}.json"
    return Path("/tmp/godot-discord-rpc.json")

def get_lock_file_path() -> Path:
    """Determine lock file location to ensure a single running daemon."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime and os.path.exists(xdg_runtime):
        return Path(xdg_runtime) / "godot-discord-rpc.lock"
    tmp = os.environ.get("TMPDIR") or "/tmp"
    if os.path.exists(tmp):
        return Path(tmp) / f"godot-discord-rpc-{os.getuid() if hasattr(os, 'getuid') else 'user'}.lock"
    return Path("/tmp/godot-discord-rpc.lock")

def acquire_single_instance_lock():
    """Ensure only one instance of the daemon runs simultaneously."""
    lock_file = get_lock_file_path()
    try:
        import fcntl
        lock_fd = open(lock_file, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(f"{os.getpid()}\n")
        lock_fd.flush()
        return lock_fd
    except (IOError, OSError, ImportError):
        print(f"[Godot Discord RPC] Another instance is already active (locked {lock_file}). Exiting.")
        sys.exit(0)

def find_discord_ipc_socket() -> Optional[str]:
    """Locate Discord IPC socket or Named Pipe across Linux, macOS, and Windows."""
    if sys.platform == "win32":
        for i in range(10):
            pipe_path = rf"\\.\pipe\discord-ipc-{i}"
            if os.path.exists(pipe_path):
                return pipe_path
        return None

    candidates = []
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        candidates.extend([os.path.join(xdg, f"discord-ipc-{i}") for i in range(10)])
    
    tmp = os.environ.get("TMPDIR") or "/tmp"
    candidates.extend([os.path.join(tmp, f"discord-ipc-{i}") for i in range(10)])
    
    if hasattr(os, "getuid"):
        uid = os.getuid()
        candidates.extend([f"/run/user/{uid}/discord-ipc-{i}" for i in range(10)])

    for path in candidates:
        if os.path.exists(path):
            return path
    return None

class DiscordIPCClient:
    """Pure-Python standard library Discord IPC client."""

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.sock: Optional[socket.socket] = None
        self.connected = False

    def connect(self) -> bool:
        """Connect to Discord IPC socket and perform handshake."""
        sock_path = find_discord_ipc_socket()
        if not sock_path:
            return False

        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(2.0)
            self.sock.connect(sock_path)
            
            payload = json.dumps({"v": 1, "client_id": self.client_id}).encode("utf-8")
            header = struct.pack("<II", OP_HANDSHAKE, len(payload))
            self.sock.sendall(header + payload)

            op, resp = self._read_frame()
            if op == OP_FRAME and resp.get("evt") == "READY":
                self.connected = True
                return True
            self.close()
            return False
        except Exception:
            self.close()
            return False

    def _read_frame(self) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
        """Read a single length-prefixed frame from Discord."""
        if not self.sock:
            return None, None
        try:
            hdr = self.sock.recv(8)
            if not hdr or len(hdr) < 8:
                return None, None
            op, length = struct.unpack("<II", hdr)
            buf = bytearray()
            while len(buf) < length:
                chunk = self.sock.recv(min(length - len(buf), 4096))
                if not chunk:
                    break
                buf.extend(chunk)
            data = json.loads(buf.decode("utf-8"))
            return op, data
        except Exception:
            return None, None

    def send_activity(self, activity: Optional[Dict[str, Any]]) -> bool:
        """Send SET_ACTIVITY command."""
        if not self.sock or not self.connected:
            return False
        try:
            frame = {
                "cmd": "SET_ACTIVITY",
                "args": {
                    "pid": os.getpid(),
                    "activity": activity
                },
                "nonce": str(uuid.uuid4())
            }
            payload = json.dumps(frame).encode("utf-8")
            header = struct.pack("<II", OP_FRAME, len(payload))
            self.sock.sendall(header + payload)
            
            self._read_frame()
            return True
        except Exception:
            self.close()
            return False

    def close(self):
        """Close socket connection cleanly."""
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            finally:
                self.sock = None

class GodotDiscordBridge:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.client_id = os.environ.get("GODOT_DISCORD_CLIENT_ID") or DEFAULT_CLIENT_ID
        self.client = DiscordIPCClient(self.client_id)
        self.running = True
        self.last_update_time = 0.0
        self.last_payload: Optional[Dict[str, Any]] = None
        self.pending_payload: Optional[Dict[str, Any]] = None
        self.activity_cleared = False

        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        print(f"\n[Godot Discord RPC] Received signal {signum}, stopping...")
        self.running = False

    def is_pid_alive(self, pid: Optional[int]) -> bool:
        """Verify whether the Godot editor process is actively running."""
        if not pid or pid <= 0:
            return self.is_godot_running()
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def is_godot_running(self) -> bool:
        """Fallback process inspection for Godot."""
        try:
            import subprocess
            if sys.platform == "win32":
                out = subprocess.check_output(
                    ["tasklist", "/FI", "IMAGENAME eq Godot.exe", "/NH"],
                    stderr=subprocess.DEVNULL
                ).decode("utf-8", errors="ignore")
                return "godot" in out.lower()
            else:
                res = subprocess.run(
                    ["pgrep", "-i", "-f", "godot"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )
                if res.returncode == 0:
                    lines = [l for l in res.stdout.decode().splitlines() if l.strip() and int(l.strip()) != os.getpid()]
                    return len(lines) > 0
                return False
        except Exception:
            return False

    def read_state(self) -> Optional[Dict[str, Any]]:
        """Read state JSON written by Godot EditorPlugin."""
        if not self.state_file.exists():
            return None
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            return None

    def build_activity(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Construct Discord activity structure from editor state."""
        if not state.get("enabled", True):
            return None

        editor_pid = state.get("pid")
        if not self.is_pid_alive(editor_pid):
            return None

        updated_at = state.get("updated_at", 0)
        now = time.time()
        
        # Consider stale if state not refreshed within 20 seconds
        if now - updated_at > 20.0:
            return None

        project_name = state.get("project_name", "Godot Project")
        main_screen = state.get("main_screen", "3D")
        is_playing = state.get("is_playing", False)
        playing_scene = state.get("playing_scene", "")
        active_scene = state.get("active_scene", "")
        active_script = state.get("active_script", "")
        cursor_line = state.get("cursor_line", -1)
        line_count = state.get("line_count", -1)
        session_start = state.get("session_start", int(now))

        state_text = f"Project: {project_name}"

        # 1. Active Playtesting / Debugging State
        if is_playing:
            details = f"Testing: {playing_scene}" if playing_scene else f"Testing: {project_name}"
            large_image = "logo_vertical_color"
            large_text = "Godot Engine - Running Project"
            small_image = "icon_color"
            small_text = "Godot Engine"

        # 2. Script Editor Active
        elif main_screen == "Script" and active_script:
            line_str = ""
            if cursor_line > 0 and line_count > 0:
                line_str = f" [Line:{cursor_line}/Total:{line_count}]"
            elif cursor_line > 0:
                line_str = f" [Line:{cursor_line}]"
            details = f"Editing {active_script}{line_str}"
            large_image = "script"
            large_text = "Godot Engine - Script Editor"
            small_image = "icon_color"
            small_text = "Godot Engine"

        # 3. 2D / 3D Scene Viewport Active
        elif active_scene:
            viewport_name = "2D" if main_screen == "2D" else "3D"
            details = f"Editing {active_scene} ({viewport_name})"
            large_image = "2d" if main_screen == "2D" else "3d"
            large_text = f"Godot Engine - {viewport_name} Viewport"
            small_image = "icon_color"
            small_text = "Godot Engine"

        # 4. Idle in Editor
        else:
            details = f"Idling in Editor ({main_screen})"
            large_image = "icon_color"
            large_text = "Godot Engine"
            small_image = None
            small_text = None

        activity: Dict[str, Any] = {
            "type": 0,  # Playing
            "details": details[:128],
            "state": state_text[:128],
            "assets": {
                "large_image": large_image,
                "large_text": large_text[:128]
            },
            "timestamps": {
                "start": int(session_start)
            },
            "buttons": [
                {"label": "Godot Engine", "url": "https://godotengine.org"}
            ]
        }
        if small_image:
            activity["assets"]["small_image"] = small_image
            activity["assets"]["small_text"] = (small_text or "")[:128]

        return activity

    def update(self, activity: Optional[Dict[str, Any]]):
        """Update activity with rate-limiting."""
        now = time.time()

        if activity is None:
            if self.client.connected and not self.activity_cleared:
                self.client.send_activity(None)
                self.activity_cleared = True
                self.last_payload = None
                self.pending_payload = None
                print("[Godot Discord RPC] Activity cleared.")
            return

        if self.last_payload == activity:
            return

        # Discord rate limit spacing: 15 seconds
        elapsed = now - self.last_update_time
        if elapsed < 15.0 and self.last_payload is not None:
            self.pending_payload = activity
            return

        if self.client.send_activity(activity):
            self.last_update_time = now
            self.last_payload = activity
            self.pending_payload = None
            self.activity_cleared = False
            print(f"[Godot Discord RPC] Updated: {activity.get('details')} | {activity.get('state')}")
        else:
            print("[Godot Discord RPC] Failed to send activity. Reconnecting...")
            self.client.close()

    def run(self):
        """Main lifecycle loop."""
        print(f"[Godot Discord RPC] Micro-daemon active (PID {os.getpid()}).")
        print(f"[Godot Discord RPC] Watching: {self.state_file}")

        reconnect_cooldown = 0.0
        godot_offline_time: Optional[float] = None

        while self.running:
            now = time.time()

            state = self.read_state()
            editor_pid = state.get("pid") if state else None
            is_alive = self.is_pid_alive(editor_pid)

            if not is_alive:
                if godot_offline_time is None:
                    godot_offline_time = now
                elif now - godot_offline_time > 30.0:
                    print("[Godot Discord RPC] Godot editor not running. Daemon exiting cleanly.")
                    self.running = False
            else:
                godot_offline_time = None

            if not self.client.connected:
                if now >= reconnect_cooldown:
                    if self.client.connect():
                        print(f"[Godot Discord RPC] Connected to Discord IPC (Client ID: {self.client_id}).")
                        reconnect_cooldown = 0.0
                    else:
                        reconnect_cooldown = now + 5.0

            if self.client.connected:
                activity = self.build_activity(state) if (state and is_alive) else None

                if self.pending_payload and (now - self.last_update_time >= 15.0):
                    to_send = activity if activity else self.pending_payload
                    self.update(to_send)
                else:
                    self.update(activity)

            time.sleep(1.0)

        # Teardown
        if self.client.connected:
            self.client.send_activity(None)
            self.client.close()
        print("[Godot Discord RPC] Daemon stopped cleanly.")

def main():
    _lock = acquire_single_instance_lock()
    state_file = get_state_file_path()
    bridge = GodotDiscordBridge(state_file)
    bridge.run()

if __name__ == "__main__":
    main()
