"""LightWorld's native frontend service. Its stdout carries newline-delimited JSON."""

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.changes import display_changes, geometry_message, require_coverage, summarize, summary_message
from backend.model import AFTER_VIEW, BEFORE_VIEW, VIEW_TOOL, AuditRejected, DesignModel, ModelError
from backend.player_bundle import prepare_player
from bridge.client import BlenderMCP, BlenderMCPError
from bridge.schema import apply_operation


UPBGE = Path(os.environ.get("LIGHTWORLD_UPBGE", "/Applications/UPBGE"))
MAX_REPAIR_ATTEMPTS = 2


REQUEST_ID = re.compile(r"[a-zA-Z0-9_-]{1,80}")
VISUAL_BRIEF = re.compile(
    r"当前(?:画面|视角)|遮挡|挡住|挡着|视线|视野|窗景|窗外.{0,8}(?:景色|风景|景观)|构图|太暗|太亮|看不[见到]|看得[见到]"
    r"|\b(?:current (?:view|shot)|occlusion|occlud\w*|sightlines?|window view|view out(?:side)?|"
    r"(?:blocks?|blocking) the view|composition|too (?:dark|bright))\b",
    re.IGNORECASE,
)


def emit(event, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


class DesignTrace:
    def __init__(self, state, request_id, operation=None):
        self.path = state / "design-trace.jsonl"
        self.id = request_id if isinstance(request_id, str) and REQUEST_ID.fullmatch(request_id) else uuid4().hex
        self.operation = operation
        self.phase = "received"
        self.resume_world_on_error = False

    def send(self, event, phase, message, level="info", *, blend_path=None, path=None, **values):
        self.phase = phase
        packet = {
            "phase": phase, "level": level, "message": message, "request_id": self.id,
            "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"), **values,
        }
        if self.operation is not None:
            packet["operation"] = self.operation
        try:
            line = json.dumps({"event": event, **packet}, ensure_ascii=False) + "\n"
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            traceback.print_exc(file=sys.stderr)
        if blend_path is not None:
            packet["blend_path"] = blend_path
        if path is not None:
            packet["path"] = path
        emit(event, **packet)

    def stage(self, phase, message, **details):
        self.send("stage", phase, message, **({"details": details} if details else {}))

    def model_request(self, phase, purpose, views):
        count = len(views)
        if count == 0:
            images = "No images"
        elif count == 1:
            images = "1 image"
        else:
            images = f"{count} images"
        self.stage(phase, f"{purpose} · {images}", image_count=count)

    def fail(self, message):
        recovery = {"resume_world": True} if self.resume_world_on_error else {}
        self.send("error", self.phase, message, level="error", **recovery)


@contextmanager
def atomic_file(path):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".lightworld-", delete=False) as output:
        temporary = Path(output.name)
        try:
            yield output
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    with atomic_file(path) as output:
        output.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))


def require_blend(path):
    if not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError("Blender did not produce a usable scene file.")
    return path


class WorldFileError(RuntimeError):
    pass


def selected_world_path(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise WorldFileError("Choose a .blend world file.")
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".blend":
        raise WorldFileError("Choose a world file with the .blend extension.")
    return path


def atomic_copy(source, destination):
    with source.open("rb") as opened, atomic_file(destination) as output:
        shutil.copyfileobj(opened, output)


class LightWorld:
    def __init__(self, state):
        self.state = state.resolve()
        self.scenes = self.state / "scenes"
        self.scenes.mkdir(exist_ok=True)
        self.manifest = self.state / "scene.json"
        self.template = self.state / "runtime-template.blend"
        self.bridge = BlenderMCP(log_path=self.state / "blender-mcp.log")
        self.active_blend = None
        self.retired_blends = set()

    def blender_script(self, name, arguments):
        executable = UPBGE / "Blender.app/Contents/MacOS/Blender"
        command = [
            str(executable), "--background", "--factory-startup", "--disable-autoexec",
            "--python-exit-code", "1", "--python", str(ROOT / "runtime" / name),
            "--", *(str(arg) for arg in arguments),
        ]
        with (self.state / "build.log").open("a") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        if completed.returncode:
            raise RuntimeError(f"UPBGE Blender could not complete {name}.")

    def boot(self):
        emit("stage", phase="boot", level="info", message="Creating a blank world in UPBGE Blender…")
        self.blender_script("build_scene.py", [self.template])
        require_blend(self.template)
        self.publish(lambda output: shutil.copyfile(self.template, output))

    def publish(self, create, keep_pose=True):
        with tempfile.TemporaryDirectory(dir=self.scenes, prefix=".build-") as scratch:
            draft = Path(scratch) / "scene.blend"
            result = create(draft)
            require_blend(draft)
            final = self.scenes / f"world-{uuid4().hex}.blend"
            os.replace(draft, final)
        try:
            atomic_json(self.manifest, {"blend_path": str(final.relative_to(self.state)), "keep_pose": keep_pose})
        except OSError:
            final.unlink(missing_ok=True)
            raise
        if self.active_blend is not None:
            self.retired_blends.add(self.active_blend)
        self.active_blend = final
        return result

    def scene_loaded(self, request):
        player_pid = request.get("player_pid")
        if type(player_pid) is not int or player_pid <= 0 or self.ready_player(player_pid) is None:
            return
        for retired in tuple(self.retired_blends):
            try:
                retired.unlink(missing_ok=True)
            except OSError:
                traceback.print_exc(file=sys.stderr)
            else:
                self.retired_blends.remove(retired)

    def open_world(self, request):
        trace = DesignTrace(self.state, request.get("request_id"), operation="open_world")
        try:
            path = selected_world_path(request.get("path"))
            player_pid = request.get("player_pid")
            if type(player_pid) is not int or player_pid <= 0 or self.viewer(player_pid) is None:
                raise WorldFileError("The player did not confirm the pause. The world was not opened.")
            if not path.is_file():
                raise WorldFileError("The selected world file could not be found.")
            trace.stage("open_validate", "Reading the selected world file…")
            with self.bridge, tempfile.TemporaryDirectory(dir=self.state, prefix=".incoming-world-") as scratch:
                incoming = Path(scratch) / "selected.blend"
                shutil.copyfile(path, incoming)
                try:
                    require_blend(incoming)
                except RuntimeError as error:
                    raise WorldFileError("The selected file is empty or damaged. The current world is still open.") from error

                def prepare(draft):
                    trace.stage("open_verify", "Checking objects, meshes, and materials in the world file…")
                    original = self.bridge.verify_world(incoming)
                    trace.stage("open_prepare", "Preparing a working copy of the world for UPBGE…")
                    rebuilt = self.bridge.apply(self.template, draft, "replace_scene_design", original["design"])
                    if rebuilt["verification"] != original["verification"]:
                        raise WorldFileError("The rebuilt world did not match the selected file.")
                    geometry = rebuilt["verification"]
                    trace.stage("open_verified", geometry_message(geometry), **geometry)

                self.publish(prepare, keep_pose=False)
            trace.send("published", "published", "The world is ready to load in UPBGE.",
                       blend_path=str(self.active_blend))
        except WorldFileError as error:
            trace.fail(str(error))
        except BlenderMCPError:
            traceback.print_exc(file=sys.stderr)
            trace.fail("World verification failed. The current world is still open. See service.log for details.")
        except Exception:
            traceback.print_exc(file=sys.stderr)
            trace.fail("Could not open the selected world. The current world is still open. See service.log for details.")

    def save_world(self, request):
        trace = DesignTrace(self.state, request.get("request_id"), operation="save_world")
        try:
            path = selected_world_path(request.get("path"))
            destination = path.parent.resolve(strict=True) / path.name
            if destination.is_relative_to(self.state):
                raise WorldFileError("Choose a save location outside the session's working folder.")
            active = require_blend(self.active_blend)
            atomic_copy(active, destination)
            trace.send("saved", "save", "World saved.", level="success",
                       path=str(destination))
        except WorldFileError as error:
            trace.send("save_error", "save", str(error), level="error")
        except Exception:
            traceback.print_exc(file=sys.stderr)
            trace.send("save_error", "save", "Could not save the world. Choose a writable folder and try again.", level="error")

    def ready_player(self, player_pid):
        try:
            snapshot = json.loads((self.state / "player.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (not isinstance(snapshot, dict) or snapshot.get("blend_path") != str(self.active_blend)
                or snapshot.get("status") != "ready"
                or (player_pid is not None and snapshot.get("pid") != player_pid)):
            return None
        return snapshot

    def viewer(self, player_pid):
        snapshot = self.ready_player(player_pid)
        if snapshot is None or snapshot.get("paused") is not True:
            return None
        try:
            position, yaw = snapshot["position"], snapshot["yaw"]
            if type(yaw) not in (int, float) or not math.isfinite(yaw):
                return None
            camera_position = snapshot["camera_position"]
            rotation = snapshot["view_rotation"]
            for vector in (position, camera_position, rotation):
                if (not isinstance(vector, list) or len(vector) != 3
                        or not all(type(value) in (int, float) and math.isfinite(value) for value in vector)):
                    return None
            size = snapshot["view_size"]
            if (not isinstance(size, list) or len(size) != 2
                    or not all(type(value) is int and 0 < value <= 32768 for value in size)):
                return None
            viewer = {
                "position": position,
                "camera_position": camera_position,
                "rotation_radians": rotation,
                "view_size": size,
                "forward": [round(-math.sin(yaw), 3), round(math.cos(yaw), 3)],
                "right": [round(math.cos(yaw), 3), round(math.sin(yaw), 3)],
                "heading_radians": round(yaw, 3),
            }
            projection = snapshot.get("projection_scale")
            if (isinstance(projection, list) and len(projection) == 2
                    and all(type(value) in (int, float) and 0.01 <= value <= 1000 for value in projection)):
                viewer["projection_scale"] = projection
            return viewer
        except (ValueError, KeyError, TypeError, OverflowError):
            return None

    def render_preview(self, blend, viewer):
        try:
            with tempfile.TemporaryDirectory(dir=self.state, prefix=".preview-") as scratch:
                output = Path(scratch) / "view.jpg"
                self.blender_script("render_view.py", [blend, output, json.dumps(viewer)])
                if not 512 <= output.stat().st_size <= 2 * 1024 * 1024:
                    raise ValueError("Unexpected preview file size.")
                data = output.read_bytes()
                if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                    raise ValueError("Blender did not produce a valid JPEG.")
                return data
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            raise ModelError("Could not render the current view. See build.log for details.") from None

    def design(self, request):
        trace = DesignTrace(self.state, request.get("request_id"))
        try:
            with self.bridge:
                self.apply_design(trace, request.get("prompt"), request.get("player_pid"), request.get("model_settings"))
        except ModelError as error:
            trace.fail(str(error))
        except Exception:
            traceback.print_exc(file=sys.stderr)
            errors = {
                "inspect": "Blender MCP could not read the current scene.",
                "export": "Blender MCP could not build and save the draft.",
                "verify": "Could not verify the saved Blender scene.",
                "publish": "Could not publish the updated scene.",
            }
            message = errors.get(trace.phase, "Could not process the design prompt.")
            trace.fail(f"{message} The current world is still open. See service.log for details.")

    def build_edit(self, trace, draft, operation, arguments, expected):
        trace.stage("export", "Building the draft…")
        result = self.bridge.apply(self.active_blend, draft, operation, arguments)
        trace.stage("verify", "Verifying the saved Blender scene…")
        saved = result.get("design")
        geometry = result.get("verification")
        if saved != expected or not isinstance(geometry, dict) or geometry.get("assets") != len(expected["elements"]):
            raise ModelError("The saved Blender scene did not match the plan. The draft was discarded.")
        trace.stage("verified", geometry_message(geometry), **geometry)
        return saved

    def apply_design(self, trace, prompt, player_pid, model_settings):
        if not isinstance(prompt, str) or not (0 < len(prompt.strip()) <= 2000):
            raise ModelError("Enter a design prompt of 1 to 2,000 characters.")
        model = DesignModel(model_settings)
        prompt = prompt.strip()
        trace.stage("received", "Design prompt received.")
        viewer = self.viewer(player_pid)
        if player_pid is not None and viewer is None:
            raise ModelError("The player did not confirm the pause. Submit the design prompt again.")
        if viewer:
            x, y, z = viewer["camera_position"]
            trace.stage("viewer", f"({x:g}, {y:g}, {z:g}) m", viewer=viewer)
        trace.stage("inspect", "Reading the current scene through Blender MCP…")
        design = self.bridge.inspect(self.active_blend)
        before = ()
        planning_views = ()
        correction = None

        def propose_edit(views):
            purpose = "Design request" if correction is None else "Revision request"
            trace.model_request("visual_plan" if views else "model", purpose, views)
            return model.choose_edit(prompt, design, viewer, views=views, correction=correction)

        for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
            proposal = None
            if attempt != 0 or not VISUAL_BRIEF.search(prompt):
                proposal = propose_edit(planning_views)
            if proposal is None or proposal[0] == VIEW_TOOL["name"]:
                trace.resume_world_on_error = True
                if viewer is None:
                    raise ModelError("This request needs a view of the scene. Face the area in question and submit the prompt again.")
                trace.stage("render_before", "Rendering the current view…")
                before = ((BEFORE_VIEW, self.render_preview(self.active_blend, viewer)),)
                proposal = propose_edit(before)

            name, arguments, requirements = proposal
            trace.stage("plan", "Checking the design plan…", tool=name)
            try:
                expected = apply_operation(design, name, arguments)
            except (ValueError, TypeError, OverflowError) as error:
                raise ModelError(f"The plan contains invalid scene data. {error}") from None
            changes = summarize(design, expected)
            covered = require_coverage(changes, requirements)
            summary = display_changes(changes)
            for index, targets in enumerate(covered, 1):
                trace.stage("requirement", f"Requested change {index} covers {', '.join(targets)}.")
            trace.stage("plan", f"Plan checked. {summary_message(changes)}.", changes=summary)

            def build_and_review(draft):
                saved = self.build_edit(trace, draft, name, arguments, expected)
                views = ()
                if before:
                    trace.stage("render_after", "Rendering the draft from the same viewpoint…")
                    after = self.render_preview(draft, viewer)
                    views = (*before, (AFTER_VIEW, after))
                phase = "visual_audit" if views else "audit"

                def report_review_retry(error):
                    trace.stage("review_retry", f"{error} Retrying the review for the same draft…")
                    trace.model_request(phase, "Review retry", views)

                trace.resume_world_on_error = True
                trace.model_request(phase, "Review request", views)
                model.verify_edit(prompt, summarize(design, saved), design, viewer, views=views,
                                  on_retry=report_review_retry)
                trace.stage("publish", "Review complete. Publishing the updated scene…")

            try:
                keep_pose = design.get("spawn") == expected.get("spawn")
                self.publish(build_and_review, keep_pose=keep_pose)
            except AuditRejected as error:
                trace.resume_world_on_error = True
                if attempt == MAX_REPAIR_ATTEMPTS:
                    raise ModelError(
                        f"{error} Automatic repair ended after {MAX_REPAIR_ATTEMPTS} revisions. The draft was discarded."
                    ) from None
                phase = "visual_retry" if before else "audit_retry"
                trace.stage(phase, f"{error} Revising the design ({attempt + 1} of {MAX_REPAIR_ATTEMPTS})…")
                correction = {"issues": error.issues, "rejected_edit": {"name": name, "arguments": arguments}}
                planning_views = error.views
            else:
                trace.resume_world_on_error = False
                trace.send("published", "published", "The updated scene is ready to load in UPBGE.",
                           blend_path=str(self.active_blend), changes=summary)
                return


def terminate(_signal, _frame):
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--player-cache", type=Path)
    args = parser.parse_args()
    state = args.state.resolve()
    state.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, terminate)
    with (state / "service.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit("fatal", message="Another LightWorld session is using this workspace.")
            return 1
        app = LightWorld(state)
        boot = {}
        if args.player_cache is not None:
            try:
                emit("stage", phase="boot", level="info", message="Preparing the LightWorld player…")
                info_path = ROOT.parent / "Info.plist"
                info = plistlib.loads(info_path.read_bytes()) if info_path.is_file() else {}
                executable = prepare_player(
                    UPBGE / "Blenderplayer.app", args.player_cache, ROOT / "LightWorld.icns",
                    build_date=info.get("LightWorldBuildDate"),
                )
                boot["player_executable"] = str(executable)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                emit("fatal", message="Could not prepare the LightWorld player. See service.log for details.")
                return 1
        try:
            app.boot()
        except Exception:
            traceback.print_exc(file=sys.stderr)
            emit("fatal", message="Could not open the initial world. See service.log for details.")
            return 1
        emit("boot", blend_path=str(app.active_blend), **boot)
        for line in sys.stdin:
            try:
                request = json.loads(line)
                command = request.get("command")
                if command == "quit":
                    return 0
                if command == "submit":
                    app.design(request)
                elif command == "open_world":
                    app.open_world(request)
                elif command == "save_world":
                    app.save_world(request)
                elif command == "scene_loaded":
                    app.scene_loaded(request)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                emit("error", message="Blender could not apply the design. The current world is still open. See service.log for details.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
