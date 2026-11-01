#!/usr/bin/env bash
# Take light + dark screenshots of each app page into docs/screenshots/.
# Runs the app under Xvfb at a fixed size so results are consistent anywhere.
# Prereqs: Xvfb, xdotool, ImageMagick (import). No window manager needed:
# GTK4 draws its own header bar.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/docs/screenshots"
DISP=:99

pages=(main settings about)
flags_main=""
flags_settings="--settings"
flags_about="--about"

export GDK_BACKEND=x11
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"
# isolated home so screenshots show shipped defaults, never personal config
export SHOT_HOME
SHOT_HOME="$(mktemp -d)"
export HOME="$SHOT_HOME"
export XDG_CONFIG_HOME="$SHOT_HOME/.config"
export XDG_CACHE_HOME="$SHOT_HOME/.cache"
export XDG_DATA_HOME="$SHOT_HOME/.local/share"
trap 'kill $XVFB_PID 2>/dev/null; rm -rf "$SHOT_HOME"' EXIT

which Xvfb xdotool import >/dev/null || {
  echo "need: Xvfb xdotool imagemagick" >&2
  exit 1
}

# fresh X server so the desktop, theme and window size are deterministic
Xvfb $DISP -screen 0 1280x900x24 &
XVFB_PID=$!
sleep 1

shot() {
  local mode="$1" page="$2" extra="$3" title="$4" dest="$5"
  DISPLAY=$DISP python3 -m eglantine_launcher --color-scheme "$mode" $extra &
  local app_pid=$!
  # wait for the window, then capture it (border included for context)
  local win=""
  for _ in $(seq 1 50); do
    win=$(DISPLAY=$DISP xdotool search --name "Eglantine Launcher" 2>/dev/null | head -1 || true)
    [ -n "$win" ] && break
    sleep 0.2
  done
  if [ -z "$win" ]; then
    echo "window not found for $mode/$page" >&2
    kill $app_pid 2>/dev/null || true
    return 1
  fi
  DISPLAY=$DISP xdotool windowactivate "$win" 2>/dev/null || true
  sleep 1.5
  mkdir -p "$(dirname "$dest")"
  DISPLAY=$DISP import -window "$win" "$dest"
  kill $app_pid 2>/dev/null || true
  wait $app_pid 2>/dev/null || true
  echo "saved $dest"
}

for mode in light dark; do
  for page in "${pages[@]}"; do
    var="flags_${page}"
    shot "$mode" "$page" "${!var}" "Eglantine Launcher" "$OUT/$mode/$page.png" || true
  done
done
