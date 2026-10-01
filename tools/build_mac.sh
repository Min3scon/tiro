#!/usr/bin/env bash
# Build Tiro.app (Apple Silicon) and release/Tiro-mac-arm64.dmg. Runs on macOS 13+ (arm64), e.g. GitHub's macos-14.
#   bash tools/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export QT_QPA_PLATFORM=offscreen

python tools/make_assets.py
python -m PyInstaller --noconfirm --clean --distpath build/dist --workpath build/work tiro-mac.spec

APP=build/dist/Tiro.app
# Apple Silicon only runs signed code: give the bundle an ad-hoc signature (no Apple Developer ID needed).
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

mkdir -p release
DMG=release/Tiro-mac-arm64.dmg
rm -f "$DMG"
STAGE=build/dmg
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Tiro" -srcfolder "$STAGE" -ov -format UDZO -fs HFS+ "$DMG"
echo "built $DMG"
