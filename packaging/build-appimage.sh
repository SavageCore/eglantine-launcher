#!/usr/bin/env bash
# Build the launcher AppImage. Uses data/icons/eglantine-launcher.png
# (pre-rendered with transparency; ImageMagick mangles the SVG).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
APPDIR="${APPDIR:-/tmp/opencode/launcher-appdir}"
OUT="${OUT:-$HOME/eglantine-launcher.AppImage}"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$APPDIR/data/icons"
cp -r "$ROOT/eglantine_launcher" "$APPDIR/"
find "$APPDIR" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp "$ROOT"/data/icons/* "$APPDIR/data/icons/"
cat > "$APPDIR/usr/bin/eglantine-launcher" <<'EOF'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
ROOT=$(dirname "$(dirname "$HERE")")
export PYTHONPATH="$ROOT:${PYTHONPATH}"
exec python3 -m eglantine_launcher "$@"
EOF
chmod +x "$APPDIR/usr/bin/eglantine-launcher"
ln -sf usr/bin/eglantine-launcher "$APPDIR/AppRun"
cat > "$APPDIR/usr/share/applications/dev.savagecore.EglantineLauncher.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Eglantine Launcher
Comment=Launch Eglantine (checks for updates)
Exec=eglantine-launcher
Icon=eglantine-launcher
Terminal=false
Categories=Game;Emulator;
EOF
ln -sf usr/share/applications/dev.savagecore.EglantineLauncher.desktop "$APPDIR/dev.savagecore.EglantineLauncher.desktop"
cp "$ROOT/data/icons/eglantine-launcher.png" "$APPDIR/eglantine-launcher.png"
cp "$ROOT/data/icons/eglantine-launcher.png" \
  "$APPDIR/usr/share/icons/hicolor/256x256/apps/eglantine-launcher.png"
cp "$ROOT/data/icons/eglantine-launcher.svg" \
  "$APPDIR/usr/share/icons/hicolor/scalable/apps/eglantine-launcher.svg"
export APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64
appimagetool-x86_64.AppImage "$APPDIR" "$OUT" 2>/dev/null \
  || "$HOME/.local/bin/appimage-tools/appimagetool-x86_64.AppImage" "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "wrote $OUT"
