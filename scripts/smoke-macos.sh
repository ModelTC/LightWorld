#!/bin/zsh
set -euo pipefail
scratch=$(mktemp -d "${TMPDIR:-/tmp}/lightworld-smoke.XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT

# Relocate the app and give it an empty home and a system-only PATH.
unpacked="$scratch/Downloaded App"
mkdir -p "$unpacked" "$scratch/home" "$scratch/tmp"
ditto -x -k "$1" "$unpacked"
app="$unpacked/LightWorld.app"
contents="$app/Contents"
resources="$contents/Resources"
helpers="$contents/Helpers"
python="$app/$(plutil -extract LightWorldPythonExecutable raw "$contents/Info.plist")"
player="$helpers/LightWorld.app/Contents/MacOS/Blenderplayer"
for executable in "$contents/MacOS/LightWorld" "$helpers/Blender.app/Contents/MacOS/Blender" "$python" "$player"; do
    lipo "$executable" -verify_arch arm64
done
codesign --verify --deep --strict "$app"

cd -- "$scratch"
env -i HOME="$scratch/home" TMPDIR="$scratch/tmp" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    LIGHTWORLD_UPBGE="$helpers" SSL_CERT_FILE="$resources/blender-mcp/site-packages/certifi/cacert.pem" \
    "$python" -I -B - "$resources" "$player" <<'PYTHON'
import json
from pathlib import Path
import ssl
import subprocess
import sys

resources, player = map(Path, sys.argv[1:])
state = Path.cwd() / "Session"
events = subprocess.run(
    [sys.executable, "-I", "-B", resources / "backend/main.py", "--state", state, "--player", player],
    input=json.dumps({"command": "quit"}) + "\n", text=True, stdout=subprocess.PIPE, timeout=90,
)
print(events.stdout, end="")
if events.returncode:
    log = state / "build.log"
    if log.is_file():
        print(log.read_text(), file=sys.stderr)
    events.check_returncode()
boot, = (event for event in map(json.loads, events.stdout.splitlines()) if event["event"] == "boot")
assert boot["player_executable"] == str(player)
world = Path(boot["blend_path"])
assert world.is_file()
assert ssl.create_default_context().cert_store_stats()["x509_ca"] > 0

sys.path.insert(0, str(resources))
from bridge.client import BlenderMCP

log = state / "blender-mcp.log"
try:
    with BlenderMCP(log) as bridge:
        design = bridge.inspect(world)
        edited = state / "Edited.blend"
        design["title"] = "Package smoke test"
        bridge.apply(world, edited, "replace_scene_design", design)
        assert bridge.verify_world(edited)["design"] == design
except Exception:
    if log.is_file():
        print(log.read_text(), file=sys.stderr)
    raise
print("Bundled Blender and Blender MCP opened, edited, and verified the world.")
PYTHON

# Boot and scene editing must leave the signed bundle intact.
for engine in "$helpers/Blender.app" "$helpers/LightWorld.app"; do
    codesign --verify --deep --strict --verbose=2 "$engine"
done
codesign --verify --deep --strict "$app"
