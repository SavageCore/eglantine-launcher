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
titles_main="Eglantine Launcher"
titles_settings="Settings"
titles_about="Eglantine Launcher"

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

# never match a stale window from an earlier run
pkill -f "eglantine_launcher --color-scheme" 2>/dev/null || true

# fresh X server so the desktop, theme and window size are deterministic.
# xkbcomp prints scary-but-harmless warnings on startup; keep them in a log.
XVFB_LOG="$(mktemp)"
Xvfb $DISP -screen 0 1280x900x24 2>"$XVFB_LOG" &
XVFB_PID=$!
trap 'kill $XVFB_PID 2>/dev/null; rm -rf "$SHOT_HOME" "$XVFB_LOG"' EXIT
sleep 1
if ! kill -0 $XVFB_PID 2>/dev/null; then
  cat "$XVFB_LOG" >&2
  exit 1
fi
# solid magenta backdrop behind everything: rounded CSD corners would
# otherwise show the root window; flood-filled to transparent after capture
python3 "$ROOT/scripts/shot_backdrop.py" "$DISP" &
BACKDROP_PID=$!
trap 'kill $BACKDROP_PID $XVFB_PID 2>/dev/null; rm -rf "$SHOT_HOME" "$XVFB_LOG"' EXIT
sleep 0.5
if ! kill -0 $BACKDROP_PID 2>/dev/null; then
  echo "backdrop failed to start" >&2
  exit 1
fi

shot() {
  local mode="$1" page="$2" extra="$3" title="$4" dest="$5"
  echo "capturing $mode/$page..."
  # the app quits itself after 8s: no kill races, no teardown segfaults,
  # no stale window ids for the next shot
  DISPLAY=$DISP python3 -m eglantine_launcher --color-scheme "$mode" --quit-after 8 $extra &
  local app_pid=$!
  # wait for the window, then capture it (border included for context)
  local win=""
  for _ in $(seq 1 50); do
    # last match = topmost window (dialogs open above the main window)
    win=$(DISPLAY=$DISP xdotool search --name "$title" 2>/dev/null | tail -1 || true)
    [ -n "$win" ] && break
    sleep 0.2
    kill -0 $app_pid 2>/dev/null || break
  done
  if [ -z "$win" ]; then
    echo "window not found for $mode/$page" >&2
    wait $app_pid 2>/dev/null || true
    return 1
  fi
  DISPLAY=$DISP xdotool windowactivate "$win" 2>/dev/null || true
  sleep 1.5
  mkdir -p "$(dirname "$dest")"
  if DISPLAY=$DISP timeout 20 import -window "$win" "$dest" \
    && python3 "$ROOT/scripts/shot_alpha.py" "$dest" >/dev/null; then
    echo "saved $dest"
  else
    echo "capture failed for $mode/$page" >&2
    return 1
  fi
  wait $app_pid 2>/dev/null || true
}

for mode in light dark; do
  for page in "${pages[@]}"; do
    var="flags_${page}"
    titlevar="titles_${page}"
    shot "$mode" "$page" "${!var}" "${!titlevar}" "$OUT/$mode/$page.png" || true
  done
done
