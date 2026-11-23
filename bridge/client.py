"""Blender Lab MCP client for editing and verifying LightWorld scenes."""

from collections import deque
from pathlib import Path
from queue import Empty, Queue
from threading import RLock, Thread
from time import monotonic
import json
import os
import shutil
import subprocess
import sys

from .schema import apply_operation, DEFAULT_DESIGN, validate_design


PROJECT = Path(__file__).resolve().parent.parent
BUNDLED_MCP = PROJECT / "blender-mcp"
BLENDER_EXECUTABLE = Path("Blender.app/Contents/MacOS/Blender")


class BlenderMCPError(RuntimeError):
    pass


def _addon_path():
    override = os.environ.get("LIGHTWORLD_BLENDER_MCP_ADDON")
    if BUNDLED_MCP.is_dir():
        candidates = [BUNDLED_MCP / "addon"]
    elif override:
        candidates = [Path(override).expanduser()]
    else:
        user = Path.home() / "Library/Application Support"
        candidates = sorted(user.glob("Blender/*/extensions/*/mcp"), reverse=True)
        candidates += sorted(user.glob("UPBGE/*/extensions/*/mcp"), reverse=True)
    for candidate in candidates:
        if all((candidate / name).is_file() for name in ("__init__.py", "mcp_to_blender_server.py", "execute_blocking.py")):
            return candidate
    raise BlenderMCPError("Could not find the Blender Lab MCP extension.")


def _server_command():
    if BUNDLED_MCP.is_dir():
        packages = BUNDLED_MCP / "site-packages"
        return [
            sys.executable, "-I", "-B", "-c",
            f"import sys; sys.path.insert(0, {str(packages)!r}); from blmcp import main; sys.exit(main())",
        ]
    override = os.environ.get("LIGHTWORLD_BLENDER_MCP_COMMAND")
    if override:
        return [override]
    found = shutil.which("blender-mcp")
    if found:
        return [found]
    conda = Path.home() / "miniconda3/envs/torch/bin/blender-mcp"
    if os.access(conda, os.X_OK):
        return [str(conda)]
    raise BlenderMCPError("Could not find the Blender Lab blender-mcp command.")


class BlenderMCP:
    def __init__(self, log_path=None):
        self.log_path = Path(log_path) if log_path else None
        self.log = None
        self.blender = None
        self.server = None
        self.lock = RLock()
        self.sequence = 0
        self.replies = Queue()
        self.blender_messages = deque(maxlen=16)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _record(self, line):
        log = self.log
        if log is not None:
            try:
                log.write(line)
            except ValueError:
                pass

    def _read_blender(self, stream, startup):
        announced = False
        try:
            for line in stream:
                self._record(line)
                self.blender_messages.append(line.strip())
                if not announced and line.startswith("LIGHTWORLD_MCP_PORT="):
                    startup.put(int(line.split("=", 1)[1]))
                    announced = True
        finally:
            if not announced:
                startup.put(None)

    def _read_server(self, stream, replies):
        try:
            for line in stream:
                try:
                    replies.put(json.loads(line))
                except json.JSONDecodeError:
                    self._record(line)
        finally:
            replies.put(None)

    def start(self):
        with self.lock:
            if self.blender and self.server and self.blender.poll() is None and self.server.poll() is None:
                return
            self.close()
            upbge = Path(os.environ.get("LIGHTWORLD_UPBGE", "/Applications/UPBGE"))
            blender = Path(os.environ.get("LIGHTWORLD_UPBGE_BLENDER", upbge / BLENDER_EXECUTABLE))
            if not blender.is_file():
                raise BlenderMCPError(f"Could not find UPBGE Blender at {blender}.")
            addon = _addon_path()
            server_command = _server_command()
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self.log = self.log_path.open("a", encoding="utf-8", buffering=1)
            self.replies = Queue()
            self.blender_messages.clear()
            startup = Queue()
            try:
                self.blender = subprocess.Popen(
                    [str(blender), "--background", "--factory-startup", "--disable-autoexec",
                     "--python-exit-code", "1", "--python", str(Path(__file__).with_name("start_blender_mcp.py")),
                     "--", str(addon)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace",
                )
                Thread(target=self._read_blender, args=(self.blender.stdout, startup), daemon=True).start()
                try:
                    port = startup.get(timeout=15)
                except Empty:
                    port = None
                if not port:
                    details = " ".join(self.blender_messages)
                    raise BlenderMCPError("UPBGE Blender MCP could not start." + (f" {details}" if details else ""))
                env = {**os.environ, "BLENDER_MCP_HOST": "127.0.0.1", "BLENDER_MCP_PORT": str(port),
                       "BLENDER_PATH": str(blender)}
                self.server = subprocess.Popen(
                    [*server_command, "--transport", "stdio"], env=env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=self.log if self.log else subprocess.DEVNULL,
                    encoding="utf-8", errors="replace",
                )
                Thread(target=self._read_server, args=(self.server.stdout, self.replies), daemon=True).start()
                self._rpc("initialize", {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "LightWorld", "version": "dev"},
                })
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            except BaseException:
                self.close()
                raise

    def _send(self, value):
        try:
            self.server.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            self.server.stdin.flush()
        except (OSError, ValueError) as exc:
            raise BlenderMCPError("The Blender MCP connection closed.") from exc

    def _rpc(self, method, params, timeout=60):
        self.sequence += 1
        request_id = self.sequence
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = monotonic() + timeout
        while True:
            try:
                message = self.replies.get(timeout=max(0, deadline - monotonic()))
            except Empty as exc:
                raise BlenderMCPError("Blender MCP did not respond in time.") from exc
            if message is None:
                raise BlenderMCPError("Blender MCP stopped.")
            if isinstance(message, dict) and message.get("id") == request_id:
                if "error" in message:
                    raise BlenderMCPError(str(message["error"].get("message", "The Blender MCP request failed.")))
                return message["result"]

    def _execute(self, source, output, name, arguments):
        source = Path(source).resolve()
        if not source.is_file() or source.suffix != ".blend":
            raise BlenderMCPError("Could not find a valid Blender scene file.")
        output = Path(output).resolve() if output else None
        if output is not None:
            if output == source or output.suffix != ".blend":
                raise BlenderMCPError("Blender output must be a separate .blend file.")
            output.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([str(source), str(output) if output else None, name, arguments],
                             ensure_ascii=False, allow_nan=False)
        code = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(PROJECT)!r})\n"
            "try:\n"
            "    from bridge.scene_design import execute\n"
            f"    result = execute(*json.loads({payload!r}))\n"
            "finally:\n"
            "    sys.path.pop(0)\n"
        )
        with self.lock:
            self.start()
            try:
                call = self._rpc("tools/call", {"name": "execute_blender_code", "arguments": {"code": code}})
            except BlenderMCPError:
                self.close()
                raise
        result = call.get("structuredContent")
        if result is None:
            try:
                result = json.loads(call["content"][0]["text"])
            except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
                self._record(f"MCP call error: {call}\n")
                raise BlenderMCPError("Blender MCP did not return usable scene data.") from exc
        if result.get("status") != "ok":
            error = str(result.get("message", "The Blender operation failed."))
            self._record(f"MCP execution error: {error}\n")
            raise BlenderMCPError(error.splitlines()[-1])
        return result["result"]

    def inspect(self, blend_path):
        return validate_design(self._execute(blend_path, None, "get_scene_design", {}))

    def verify_world(self, blend_path):
        return self._execute(blend_path, None, "verify_world", {})

    def apply(self, source_blend, output_blend, name, arguments):
        if name == "replace_scene_design":
            validate_design(arguments)
        elif name == "edit_scene_design":
            apply_operation(DEFAULT_DESIGN, name, arguments)
        else:
            raise ValueError("Unknown Blender edit operation.")
        return self._execute(source_blend, output_blend, name, arguments)

    def close(self):
        with self.lock:
            for process in (self.server, self.blender):
                if process is None:
                    continue
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                if process.stdin:
                    process.stdin.close()
                if process.stdout:
                    process.stdout.close()
            self.server = None
            self.blender = None
            if self.log is not None:
                self.log.close()
                self.log = None
