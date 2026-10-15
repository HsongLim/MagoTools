#!/bin/bash
# 맥 설치 파일(dmg) 만들기.  저장소 맨 위 폴더에서:
#   bash build/build_mac.sh
#   PY=/opt/homebrew/bin/python3.14 bash build/build_mac.sh   (Homebrew 파이썬이면 brew install python-tk@3.14 필요)
# 결과: dist/ShotPicker_<버전>_<arm64|x86_64>.dmg
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)

PY=${PY:-python3}
$PY -c "import tkinter" 2>/dev/null || {
  echo "[오류] tkinter 가 없는 Python 입니다: $PY"; exit 1; }

VER=$(grep -m1 '^APP_VERSION' src/shot_picker.py | cut -d'"' -f2)
ARCH=$(uname -m)
echo "Shot Picker $VER 빌드 ($ARCH)"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

$PY -m venv "$WORK/venv"
source "$WORK/venv/bin/activate"
pip install -q --upgrade pip pyinstaller pillow tkinterdnd2 certifi

pyinstaller --noconfirm --clean --windowed --name ShotPicker \
  --distpath "$WORK/dist" --workpath "$WORK/build" --specpath "$WORK" \
  --icon "$ROOT/assets/ShotPicker.png" \
  --osx-bundle-identifier com.studiomago.shotpicker \
  --collect-all tkinterdnd2 src/shot_picker.py

APP="$WORK/dist/ShotPicker.app"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VER" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VER" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Shot Picker" "$APP/Contents/Info.plist" 2>/dev/null || true
codesign --force --deep --sign - "$APP" 2>/dev/null || true

STAGE="$WORK/dmg"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/Shot Picker.app"
ln -s /Applications "$STAGE/Applications"
# 사용법은 앱 안 설명서(help_text)에서 만들어서 항상 같게
python -c "import sys; sys.path.insert(0, 'src'); import shot_picker as g; open(sys.argv[1], 'w', encoding='utf-8').write(g.help_text(True))" "$STAGE/읽어보세요.txt" \
  || cp config/README_mac.txt "$STAGE/읽어보세요.txt"
mkdir -p dist
DMG="dist/ShotPicker_${VER}_${ARCH}.dmg"
rm -f "$DMG"
hdiutil create -volname "Shot Picker $VER" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
deactivate
echo "완료: $DMG"
