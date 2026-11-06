#!/usr/bin/env bash
# Take light + dark screenshots of each app page into docs/screenshots/.
# Runs the app under Xvfb at a fixed size so results are consistent anywhere.
# Prereqs: Xvfb, xdotool, ImageMagick (import). No window manager needed:
# GTK4 draws its own header bar.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/docs/screenshots"
DISP=:99

pages=(main settings settings_sources settings_games settings_pr_builds source_edit source_edit_storage source_edit_tags about)
if [ $# -gt 0 ]; then
  # e.g. `bash scripts/take-screenshots.sh about` captures one page only
  # (used by the release script to refresh the version-stamped About shot).
  wanted=("$@")
  pages=()
  for p in "${wanted[@]}"; do
    found=""
    for known in main settings settings_sources settings_games settings_pr_builds source_edit source_edit_storage source_edit_tags about; do
      [ "$p" = "$known" ] && found=1 && break
    done
    if [ -z "$found" ]; then
      echo "unknown page '$p'" >&2
      exit 1
    fi
    pages+=("$p")
  done
fi
flags_main=""
flags_settings="--settings"
flags_settings_sources="--settings --settings-page sources"
flags_settings_games="--settings --settings-page games"
flags_settings_pr_builds="--settings --settings-page prs"
flags_source_edit="--edit-source"
flags_source_edit_storage="--edit-source --edit-source-tab storage"
flags_source_edit_tags="--edit-source --edit-source-tab tags"
flags_about="--about"
titles_main="Eglantine Launcher"
titles_settings="Settings"
titles_settings_sources="Settings"
titles_settings_games="Settings"
titles_settings_pr_builds="Settings"
titles_source_edit="Edit source"
titles_source_edit_storage="Edit source"
titles_source_edit_tags="Edit source"
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

# The PR Builds dialog is empty with shipped defaults, which makes a bare
# screenshot. Seed two example entries (one open, one merged to show the
# badge) so the shot shows the list UI. Seeded after the other pages so
# main/settings/about still show plain defaults (settings hides PRs
# anyway, and main keeps Eglantine selected).
seed_pr_dummies() {
  DISPLAY=$DISP PYTHONPATH="$ROOT" python3 - <<'EOF'
from eglantine_launcher.config import load, save

cfg = load()
ids = {p.get("id") for p in cfg.get("projects", [])}
dummies = [
    {
        "id": "pr-eglantine-364-shot",
        "name": "Async shader compile (#364)",
        "source_url": "https://github.com/Djihads80/Eglantine.git",
        "branch": "pr-364",
        "releases_url": None,
        "is_pr": True,
        "pr_url": "https://github.com/Djihads80/Eglantine/pull/364",
        "pr_number": 364,
        "pr_owner": "Djihads80",
        "pr_repo": "Eglantine",
        "pr_title": "Async shader compile",
        "pr_state": "open",
        "pr_head_sha": "deadbeef" * 5,
    },
    {
        "id": "pr-pokedinner-58-shot",
        "name": "Pokopia HUD scale (#58)",
        "source_url": "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork.git",
        "branch": "pr-58",
        "releases_url": None,
        "is_pr": True,
        "pr_url": "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork/pulls/58",
        "pr_number": 58,
        "pr_owner": "Franseven",
        "pr_repo": "PokeDinner-a-Eglantine-Fork",
        "pr_title": "Pokopia HUD scale",
        "pr_state": "merged",
        "pr_head_sha": "cafef00d" * 5,
    },
]
for dummy in dummies:
    if dummy["id"] not in ids:
        cfg.setdefault("projects", []).append(dummy)
save(cfg)
print("seeded example PR entries")
EOF
}

shot() {
  local mode="$1" page="$2" extra="$3" title="$4" dest="$5"
  echo "capturing $mode/$page..."
  if [ "$page" = "settings_pr_builds" ]; then
    seed_pr_dummies
  fi
  if [ "$page" = "about" ]; then
    # the About dialog renders inside the main window and is taller than
    # the default 460px: give it room, then put the size back afterwards
    # so the other main-window shots stay compact.
    DISPLAY=$DISP XDG_CONFIG_HOME="$SHOT_HOME/.config" python3 - <<EOF
import json
from pathlib import Path
cfg = Path("$SHOT_HOME") / ".config" / "eglantine-launcher" / "config.json"
data = json.loads(cfg.read_text()) if cfg.exists() else {}
data["window_width"], data["window_height"] = 640, 760
cfg.parent.mkdir(parents=True, exist_ok=True)
cfg.write_text(json.dumps(data))
EOF
  fi
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
  if [ "$page" = "about" ]; then
    # put the compact size back for any later main-window shots
    DISPLAY=$DISP XDG_CONFIG_HOME="$SHOT_HOME/.config" python3 - <<EOF
import json
from pathlib import Path
cfg = Path("$SHOT_HOME") / ".config" / "eglantine-launcher" / "config.json"
try:
    data = json.loads(cfg.read_text())
except Exception:
    data = {}
data.pop("window_width", None)
data.pop("window_height", None)
cfg.write_text(json.dumps(data))
EOF
  fi
}

for mode in light dark; do
  for page in "${pages[@]}"; do
    var="flags_${page}"
    titlevar="titles_${page}"
    shot "$mode" "$page" "${!var}" "${!titlevar}" "$OUT/$mode/$page.png" || true
  done
done
