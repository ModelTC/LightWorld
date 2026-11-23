#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h:h}"

if [[ "$(uname -m)" != arm64 ]]; then
    print -u2 "Build this package on an Apple Silicon Mac."
    exit 1
fi

output=${1:-"$PWD/dist"}
output=${output:a}
mkdir -p "$output"
scratch=$(mktemp -d "${TMPDIR:-/tmp}/lightworld-package.XXXXXXXX")
mount="$scratch/upbge"
mounted=0
cleanup() {
    if (( mounted )); then hdiutil detach "$mount" -quiet || return; fi
    rm -rf -- "$scratch"
}
trap cleanup EXIT

mcp_release=v1.0.3
mcp_sha=2cea8d566dde07fbac28a61d698909d69724e853
source scripts/fetch-upbge.sh
if [[ -n "${UPBGE_DMG_PATH:-}" ]]; then
    image=${UPBGE_DMG_PATH:A}
    verify_upbge "$image"
else
    image=$(fetch_upbge "$scratch")
fi

app=$(./build.sh "$scratch/LightWorld.app")
contents="$app/Contents"
helpers="$contents/Helpers"
editor="$helpers/Blender.app"
player="$helpers/LightWorld.app"
resources="$contents/Resources"
bridge="$resources/blender-mcp"
packages="$bridge/site-packages"

mkdir -p "$mount" "$helpers" "$packages"
hdiutil attach -readonly -nobrowse -quiet -mountpoint "$mount" "$image"
mounted=1
ditto --noqtn "$mount/Blender.app" "$editor"
ditto --noqtn "$mount/Blenderplayer.app" "$player"
hdiutil detach "$mount" -quiet
mounted=0

python_path=Contents/Helpers/Blender.app/Contents/Resources/5.4/python/bin/python3.13
python="$app/$python_path"
mcp_source="$scratch/blender-mcp"
git clone --quiet --depth 1 --branch "$mcp_release" --config advice.detachedHead=false \
    https://projects.blender.org/lab/blender_mcp.git "$mcp_source"
[[ "$(git -C "$mcp_source" rev-parse HEAD)" == "$mcp_sha" ]]
ditto "$mcp_source/addon/blender_mcp_addon" "$bridge/addon"
ditto "$mcp_source/mcp/blmcp" "$packages/blmcp"
install -m 644 "$editor/Contents/Resources/text/license/spdx/GPL-3.0-or-later.txt" "$bridge/LICENSE"

mkdir "$scratch/wheels"
"$python" -I -B -m pip --isolated download --disable-pip-version-check --quiet \
    --platform macosx_13_0_arm64 --only-binary=:all: --require-hashes \
    --index-url https://pypi.org/simple --dest "$scratch/wheels" -r scripts/macos-requirements.txt
"$python" -I -B -m pip --isolated install --disable-pip-version-check --quiet \
    --no-index --find-links "$scratch/wheels" --require-hashes --ignore-installed \
    --no-compile --target "$packages" -r scripts/macos-requirements.txt
rm -rf -- "$packages/bin"
install -m 644 scripts/runtime-sources.txt "$resources/Runtime Sources.txt"

build_date=$(plutil -extract LightWorldBuildDate raw "$contents/Info.plist")
"$python" -I -B -c '
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from backend.player_bundle import LIGHTWORLD_VERSION, brand_player
brand_player(Path(sys.argv[2]), Path(sys.argv[3]).read_bytes(), f"{LIGHTWORLD_VERSION} {sys.argv[4]}")
' "$resources" "$player" "$resources/LightWorld.icns" "$build_date"
plutil -insert LightWorldPythonExecutable -string "$python_path" "$contents/Info.plist"

for engine in "$editor" "$player"; do
    # Embedded Blender Python ignores PYTHON* variables. Disable bytecode
    # writes before its first scene import so it cannot invalidate the seal.
    python_home="$engine/Contents/Resources/5.4/python"
    library="$python_home/lib/python3.13"
    customization="$library/site-packages/sitecustomize.py"
    printf '\n# Keep this application bundle immutable at runtime.\nsys.dont_write_bytecode = True\n' >> "$customization"
    # The standard library and the setuptools .pth file can import modules before sitecustomize.
    "$python_home/bin/python3.13" -I -B -m compileall -q -f --invalidation-mode checked-hash \
        -s "$library" -l "$library" "$library/encodings" "$library/importlib" \
        "$library/site-packages/_distutils_hack" "$customization"
done

identity=${LIGHTWORLD_SIGN_IDENTITY:--}
sign=(codesign --force --sign "$identity")
if [[ "$identity" != - ]]; then
    sign+=(--timestamp)
    if [[ -n "${LIGHTWORLD_SIGN_KEYCHAIN:-}" ]]; then sign+=(--keychain "$LIGHTWORLD_SIGN_KEYCHAIN"); fi
else
    sign+=(--timestamp=none)
fi

while IFS= read -r -d '' library; do
    "${sign[@]}" "$library"
done < <(find "$packages" -type f \( -name '*.so' -o -name '*.dylib' \) -print0)
# The interpreter loads both the engine's signed standard library and vendor wheels.
"${sign[@]}" --options runtime --entitlements scripts/python.entitlements "$python"
for engine in "$editor" "$player"; do
    "${sign[@]}" --preserve-metadata=entitlements,flags,runtime "$engine"
done
"${sign[@]}" --options runtime "$app"
codesign --verify --deep --strict "$app"

archive="$scratch/LightWorld-macOS-arm64.zip"
ditto -c -k --sequesterRsrc --keepParent "$app" "$archive"
mv -- "$archive" "$output/LightWorld-macOS-arm64.zip"
print -r -- "$output/LightWorld-macOS-arm64.zip"
