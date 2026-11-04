import json
import os
from pathlib import Path

from .gpu import gpu_vendors


def _base_home():
    # Resolved at call time (not import time) so tests can redirect HOME.
    return Path(os.environ.get("HOME") or str(Path.home()))


def config_dir():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "eglantine-launcher"
    return _base_home() / ".config" / "eglantine-launcher"


def config_file():
    override = os.environ.get("EGLANTINE_LAUNCHER_CONFIG")
    if override:
        return Path(override)
    return config_dir() / "config.json"


# Set on every dict returned by load(): the file it came from. save() writes
# back there even if the environment changed in between, and never stores it.
_CONFIG_PATH_KEY = "_config_path"


def _default_exclude_keywords():
    # Eglantine marks vendor-specific test builds in the release title/body
    # (e.g. "AMD Experimental Build"); hide the other vendor's builds.
    try:
        vendors = gpu_vendors()
    except Exception:
        return ""
    if "nvidia" in vendors and "amd" not in vendors:
        return "AMD Experimental"
    return ""


DEFAULT_PROJECTS = [
    {
        "id": "eglantine",
        "name": "Eglantine",
        "source_url": "https://github.com/Djihads80/Eglantine.git",
        "branch": "nx2",
        "releases_url": "https://api.github.com/repos/Djihads80/Eglantine/releases",
        "include_prerelease": True,
        "exclude_keywords": _default_exclude_keywords(),
        "icon_url": "https://github.com/Djihads80/Eglantine/raw/refs/heads/nx2/dist/Eglantine.png",
    },
    {
        "id": "pokedinner",
        "name": "PokeDinner (Pokopia)",
        "source_url": "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork.git",
        "branch": "master",
        "releases_url": "https://git.eden-emu.dev/api/v1/repos/Franseven/PokeDinner-a-Eglantine-Fork/releases",
        "include_prerelease": True,
        "icon_url": "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork/raw/branch/master/dist/Eglantine.png",
    },
]


def _data_home():
    return _base_home() / ".local" / "share" / "eglantine-launcher"


def default_config():
    return {
        "project": "eglantine",
        "mode": "release",
        # Windows users get downloads only by default; the toolchain list
        # scares people off. They can opt into source builds in Settings.
        # Source builds are opt-in: the toolchain list scares people off.
        # Turning the Settings switch on runs a dependency check first.
        "source_enabled": False,
        "pgo": True,
        "install_dir": str(_data_home()),
        "source_dir": str(_data_home() / "src"),
        "last_release_tag": None,
        "last_source_commit": None,
        "downloaded_releases": [],
        "built_commits": [],
        "hidden_sources": [],
        # Window behavior. None means "ask on first use, then remember".
        # minimize_on_launch: "taskbar" | "tray" | "none" | None
        # close_behavior: "quit" | "tray" | None
        "minimize_on_launch": None,
        "close_behavior": None,
        # Main window size; updated whenever the window closes or hides.
        # Matches the documented screenshots (630x590: the 1px
        # backdrop frame plus the 4px theme CSD frame are cropped
        # in scripts/shot_alpha.py).
        "window_width": 640,
        "window_height": 600,
        # The "Running in the background" notification only shows once.
        "tray_notice_shown": False,
        "projects": [p.copy() for p in DEFAULT_PROJECTS],
    }


def load():
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        cfg = default_config()
        cfg[_CONFIG_PATH_KEY] = str(path)
        save(cfg)
        return cfg
    try:
        cfg = {**default_config(), **json.loads(path.read_text())}
        # A config remembers the file it came from, so a worker thread that
        # saves after the process environment changed (tests redirecting
        # HOME/XDG) writes back where it read from, never to the real file.
        cfg.pop(_CONFIG_PATH_KEY, None)
        cfg[_CONFIG_PATH_KEY] = str(path)
        if not cfg.get("projects"):
            cfg["projects"] = [p.copy() for p in DEFAULT_PROJECTS]
        else:
            # backfill fields for built-in sources (e.g. PokeDinner gained Forgejo releases)
            changed = False
            for p in cfg["projects"]:
                if not p.get("releases_url"):
                    for default in DEFAULT_PROJECTS:
                        if default["id"] == p.get("id") and default.get("releases_url"):
                            p["releases_url"] = default["releases_url"]
                            changed = True
                if "exclude_keywords" not in p and p.get("id") == "eglantine":
                    p["exclude_keywords"] = _default_exclude_keywords()
                    changed = True
            # seed history lists from legacy single-value keys
            if not cfg.get("downloaded_releases") and cfg.get("last_release_tag"):
                cfg["downloaded_releases"] = [cfg["last_release_tag"]]
                changed = True
            if not cfg.get("built_commits") and cfg.get("last_source_commit"):
                cfg["built_commits"] = [cfg["last_source_commit"]]
                changed = True
            if changed:
                save(cfg)
        return cfg
    except Exception:
        cfg = default_config()
        cfg[_CONFIG_PATH_KEY] = str(path)
        return cfg


# Fields the remote registry is allowed to update on entries it manages.
# User-tuned fields (include_prerelease, exclude_keywords) are never clobbered.
REMOTE_FIELDS = ("name", "source_url", "branch", "releases_url")


def merge_remote_sources(cfg, remote):
    """Fold the remote registry into cfg. Returns True if anything changed.

    New ids are added (unless hidden by the user); known remote entries get
    their upstream fields refreshed. User-tuned flags are left alone.
    """
    hidden = set(cfg.get("hidden_sources", []))
    by_id = {p.get("id"): p for p in cfg.get("projects", [])}
    changed = False
    for entry in remote:
        pid = entry.get("id")
        if not pid or pid in hidden:
            continue
        if pid in by_id:
            p = by_id[pid]
            if p.get("is_pr"):
                continue
            for field in REMOTE_FIELDS:
                if entry.get(field) and p.get(field) != entry[field]:
                    p[field] = entry[field]
                    changed = True
            if not p.get("remote"):
                p["remote"] = True
                changed = True
        else:
            new = {k: v for k, v in entry.items() if k in ("id", *REMOTE_FIELDS, "include_prerelease")}
            new.setdefault("include_prerelease", True)
            new["remote"] = True
            cfg.setdefault("projects", []).append(new)
            changed = True
    if changed:
        save(cfg)
    return changed


def save(cfg):
    # Merge over defaults and the file on disk so a partial or stale dict
    # from any caller can never wipe out keys it doesn't know about (the
    # GitHub token, a setting saved by another window, ...).
    path = Path(cfg.get(_CONFIG_PATH_KEY) or config_file())
    try:
        on_disk = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        on_disk = {}
    merged = {**default_config(), **on_disk, **cfg}
    merged.pop(_CONFIG_PATH_KEY, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2))
