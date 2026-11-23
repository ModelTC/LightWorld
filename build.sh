#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
stage=$(mktemp -d "$PWD/.build.XXXXXXXX")
trap 'rm -rf -- "$stage"' EXIT
bundle="$stage/LightWorld.app"
contents="$bundle/Contents"
mkdir -p "$contents/MacOS" "$contents/Resources" "$stage/LightWorld.iconset"

swiftc -O -swift-version 5 -target arm64-apple-macos13.0 native/OverlayLayout.swift native/ModelSettings.swift native/LightWorld.swift -o "$contents/MacOS/LightWorld"
build_date=$(/usr/bin/stat -f %Sm -t %Y-%m-%d "$contents/MacOS/LightWorld")
swift native/Icon.swift native/AppIcon.png "$stage/LightWorld.iconset"
iconutil -c icns "$stage/LightWorld.iconset" -o "$contents/Resources/LightWorld.icns"
for directory in backend bridge runtime; do
    mkdir -p "$contents/Resources/$directory"
    /usr/bin/find "$directory" -maxdepth 1 -name '*.py' -type f -exec /bin/cp {} "$contents/Resources/$directory" \;
done
/bin/cp runtime/avatar.blend "$contents/Resources/runtime/"

cat > "$contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleDisplayName</key><string>LightWorld</string>
  <key>CFBundleExecutable</key><string>LightWorld</string>
  <key>CFBundleIconFile</key><string>LightWorld</string>
  <key>CFBundleIdentifier</key><string>local.lightworld.app</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleLocalizations</key><array><string>en</string></array>
  <key>CFBundleName</key><string>LightWorld</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

plutil -insert LightWorldBuildDate -string "$build_date" "$contents/Info.plist"
plutil -lint "$contents/Info.plist" >/dev/null
codesign --force --sign - --identifier local.lightworld.app "$bundle"
codesign --verify --strict "$bundle"
if [[ -e LightWorld.app ]]; then
    existing_id=$(plutil -extract CFBundleIdentifier raw LightWorld.app/Contents/Info.plist)
    [[ "$existing_id" == local.lightworld.app ]] || exit 1
    mv -- LightWorld.app "$stage/previous.app"
fi
if ! mv -- "$bundle" LightWorld.app; then
    if [[ -e "$stage/previous.app" ]]; then mv -- "$stage/previous.app" LightWorld.app; fi
    exit 1
fi
print "$PWD/LightWorld.app"
