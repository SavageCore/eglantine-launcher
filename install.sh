#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/dev.savagecore.EglantineLauncher.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Eglantine Launcher
Comment=Launch Eglantine (checks for updates)
Exec=env PYTHONPATH=$DIR python3 -m eglantine_launcher --auto
Path=$DIR
Terminal=false
Categories=Game;Emulator;
EOF
echo "Installed desktop entry. Make sure $DIR is on PYTHONPATH or install the package."