@tool
extends EditorPlugin

## Godot Discord Rich Presence Plugin
## Automatically tracks scenes, scripts, lines, and project runtime state.

var _poll_timer: Timer
var _session_start: int
var _current_main_screen: String = "3D"
var _current_scene_path: String = ""
var _last_serialized_state: String = ""

func _enter_tree() -> void:
	_session_start = int(Time.get_unix_time_from_system())
	
	main_screen_changed.connect(_on_main_screen_changed)
	scene_changed.connect(_on_scene_changed)
	scene_closed.connect(_on_scene_closed)
	resource_saved.connect(_on_resource_saved)
	
	_poll_timer = Timer.new()
	_poll_timer.name = "DiscordRPCPollTimer"
	_poll_timer.wait_time = 2.0
	_poll_timer.autostart = true
	_poll_timer.timeout.connect(_on_poll_timeout)
	add_child(_poll_timer)
	
	_ensure_daemon_running()
	_flush_state(true)
	print("[Godot Discord RPC] Plugin initialized.")

func _exit_tree() -> void:
	if _poll_timer:
		_poll_timer.stop()
		_poll_timer.queue_free()
	
	_flush_empty_state()
	print("[Godot Discord RPC] Plugin stopped.")

func _ensure_daemon_running() -> void:
	var daemon_path = ProjectSettings.globalize_path("res://addons/godot_discord_rpc/daemon.py")
	if FileAccess.file_exists(daemon_path):
		var pid = OS.create_process("python3", [daemon_path])
		if pid > 0:
			print("[Godot Discord RPC] Background micro-daemon active (PID %d)." % pid)
	else:
		push_warning("[Godot Discord RPC] Could not locate daemon script at: %s" % daemon_path)

func _on_main_screen_changed(screen_name: String) -> void:
	_current_main_screen = screen_name
	_flush_state(true)

func _on_scene_changed(scene_root: Node) -> void:
	if scene_root:
		_current_scene_path = scene_root.scene_file_path
	else:
		_current_scene_path = ""
	_flush_state(true)

func _on_scene_closed(filepath: String) -> void:
	if filepath == _current_scene_path:
		var root = EditorInterface.get_edited_scene_root()
		_current_scene_path = root.scene_file_path if root else ""
	_flush_state(true)

func _on_resource_saved(_resource: Resource) -> void:
	_flush_state(true)

func _on_poll_timeout() -> void:
	_flush_state(false)

func _get_state_file_path() -> String:
	var xdg = OS.get_environment("XDG_RUNTIME_DIR")
	if xdg != "" and DirAccess.dir_exists_absolute(xdg):
		return xdg.path_join("godot-discord-rpc.json")
	var tmp = OS.get_environment("TMPDIR")
	if tmp != "" and DirAccess.dir_exists_absolute(tmp):
		return tmp.path_join("godot-discord-rpc.json")
	if OS.get_name() == "Windows":
		var temp_win = OS.get_environment("TEMP")
		if temp_win != "":
			return temp_win.path_join("godot-discord-rpc.json")
	return "/tmp/godot-discord-rpc.json"

func _collect_state() -> Dictionary:
	var proj_name = ProjectSettings.get_setting("application/config/name", "Godot Project")
	var is_playing = EditorInterface.is_playing_scene()
	var playing_scene = EditorInterface.get_playing_scene() if is_playing else ""
	
	var active_script_name: String = ""
	var cursor_line: int = -1
	var line_count: int = -1
	
	var script_editor = EditorInterface.get_script_editor()
	if script_editor:
		var current_script = script_editor.get_current_script()
		if current_script and current_script.resource_path != "":
			active_script_name = current_script.resource_path.get_file()
		
		var current_editor = script_editor.get_current_editor()
		if current_editor:
			var base_editor = current_editor.get_base_editor()
			if base_editor is CodeEdit:
				cursor_line = base_editor.get_caret_line() + 1
				line_count = base_editor.get_line_count()
	
	var active_scene_name = _current_scene_path.get_file() if _current_scene_path != "" else ""
	if active_scene_name == "":
		var root = EditorInterface.get_edited_scene_root()
		if root and root.scene_file_path != "":
			active_scene_name = root.scene_file_path.get_file()
			_current_scene_path = root.scene_file_path
	
	return {
		"enabled": true,
		"pid": OS.get_process_id(),
		"project_name": proj_name,
		"project_path": ProjectSettings.globalize_path("res://"),
		"main_screen": _current_main_screen,
		"is_playing": is_playing,
		"playing_scene": playing_scene.get_file() if playing_scene != "" else "",
		"active_scene": active_scene_name,
		"active_script": active_script_name,
		"cursor_line": cursor_line,
		"line_count": line_count,
		"session_start": _session_start,
		"updated_at": int(Time.get_unix_time_from_system())
	}

func _flush_state(force: bool = false) -> void:
	var state = _collect_state()
	var compare_state = state.duplicate()
	compare_state["updated_at"] = 0
	var serialized = JSON.stringify(compare_state)
	
	if force or serialized != _last_serialized_state:
		_last_serialized_state = serialized
		_write_state_file(state)
	else:
		# Periodic heartbeat touch so daemon knows editor is active
		_write_state_file(state)

func _flush_empty_state() -> void:
	var empty_state = {
		"enabled": false,
		"pid": OS.get_process_id(),
		"updated_at": int(Time.get_unix_time_from_system())
	}
	_write_state_file(empty_state)

func _write_state_file(data: Dictionary) -> void:
	var path = _get_state_file_path()
	var file = FileAccess.open(path, FileAccess.WRITE)
	if file:
		file.store_string(JSON.stringify(data, "  "))
		file.close()
