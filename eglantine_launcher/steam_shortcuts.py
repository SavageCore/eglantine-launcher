"""Manage Steam non-Steam shortcuts (per-project entries) via ``shortcuts.vdf``.

Uses the PyPI ``vdf`` package (same library Lutris uses) for binary VDF
read/write instead of hand-rolling the format. Each launcher project gets
its own shortcut entry so different forks coexist; entries are matched by
the ``--project <id>`` marker in Exe/LaunchOptions (falling back to the
``<name> (EL)`` AppName), never by index, so foreign entries are preserved.

Besides the ``icon`` field in the entry itself, the project icon is also
copied into each user's ``config/grid/`` artwork dir (portrait, capsule,
hero and logo names derived from the entry appid), which is what Steam
shows for library tiles.
"""

import os
import shutil
import zlib
from pathlib import Path

APP_NAME_SUFFIX = "(EL)"


def _home() -> Path:
    return Path.home()


def _windows_steam_roots():
    """Steam install dirs on Windows as strings (no Path: testable on Linux)."""
    candidates = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        root = os.environ.get(var)
        if root:
            candidates.append(os.path.join(root, "Steam"))
    seen, unique = set(), []
    for candidate in candidates:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def steam_base_dirs():
    """Candidate Steam root dirs (native, classic symlink, Flatpak, Windows)."""
    if os.name == "nt":
        return [Path(p) for p in _windows_steam_roots()]
    home = _home()
    return [
        home / ".local" / "share" / "Steam",
        home / ".steam" / "steam",
        home / ".steam" / "root",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    ]


def userdata_dirs(bases=None):
    """Existing ``userdata/<uid>`` dirs across all Steam roots."""
    found = []
    for base in bases if bases is not None else steam_base_dirs():
        userdata = Path(base) / "userdata"
        if not userdata.is_dir():
            continue
        try:
            entries = sorted(p for p in userdata.iterdir() if p.is_dir() and p.name.isdigit())
        except OSError:
            continue
        found.extend(entries)
    return found


def shortcuts_files(bases=None, users=None):
    """Target ``shortcuts.vdf`` paths, one per Steam user.

    Includes paths whose file does not exist yet (user has no shortcuts)
    as long as the ``userdata/<uid>`` dir does, so the first install can
    create it. ``users`` optionally restricts to specific uid strings.
    """
    files = []
    for user_dir in userdata_dirs(bases):
        if users is not None and user_dir.name not in set(users):
            continue
        files.append(user_dir / "config" / "shortcuts.vdf")
    return files


def shortcut_app_id(exe, app_name):
    """Stable 32-bit shortcut id in the SteamGrid/Lutris style."""
    raw = zlib.crc32(f"{exe}{app_name}".encode()) | 0x80000000
    return raw - 0x100000000 if raw > 0x7FFFFFFF else raw


def _exe_launch_options(app_root, pid, argv=None, start_dir=None):
    from .tasks import quote_arg, shortcut_argv, shortcut_start_dir

    cmd = list(argv) if argv else shortcut_argv(pid, app_root)
    exe = cmd[0]
    launch_options = " ".join(quote_arg(a) for a in cmd[1:])
    start = start_dir or shortcut_start_dir(app_root)
    return exe, launch_options, start


def build_entry(project, pid, app_root, icon=None, argv=None, start_dir=None):
    """Build a shortcuts.vdf entry dict for one project."""
    from .tasks import shortcut_name

    app_name = shortcut_name(project)
    exe, launch_options, start = _exe_launch_options(app_root, pid, argv, start_dir)
    return {
        "appid": shortcut_app_id(exe + launch_options, app_name),
        "AppName": app_name,
        "Exe": exe,
        "StartDir": start,
        "icon": str(icon) if icon else "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": "",
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {},
    }


def entry_matches(entry, pid, app_name=None):
    """True when a vdf entry belongs to the given project id."""
    if not isinstance(entry, dict):
        return False
    marker = f"--project {pid}"
    for key in ("Exe", "LaunchOptions"):
        try:
            if marker in str(entry.get(key, "")):
                return True
        except Exception:
            continue
    if app_name and entry.get("AppName") == app_name:
        return True
    return False


def load_entries(path):
    """Load shortcut entries from a shortcuts.vdf file ({} when absent)."""
    import vdf

    path = Path(path)
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            data = vdf.binary_loads(fh.read())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    shortcuts = data.get("shortcuts", data)
    return shortcuts if isinstance(shortcuts, dict) else {}


def save_entries(path, entries):
    """Write entries dict back to shortcuts.vdf, creating parents."""
    import vdf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # entries keys may already be str indices; normalize to 0..n-1
    values = list(entries.values()) if isinstance(entries, dict) else list(entries)
    payload = {"shortcuts": {str(i): v for i, v in enumerate(values)}}
    with path.open("wb") as fh:
        fh.write(vdf.binary_dumps(payload))


def shortcut_exists(pid, app_name=None, files=None):
    """True when any Steam user's shortcuts.vdf contains this pid."""
    targets = files if files is not None else shortcuts_files()
    for path in targets:
        for entry in load_entries(path).values():
            if entry_matches(entry, pid, app_name):
                return True
    return False


def grid_names(appid):
    """Artwork filenames Steam looks up for a shortcut id (unsigned 32-bit)."""
    uid = int(appid) & 0xFFFFFFFF
    return [f"{uid}p.png", f"{uid}.png", f"{uid}_hero.png", f"{uid}_logo.png"]


def write_artwork(shortcuts_path, appid, icon):
    """Copy the project icon into the user's grid/ artwork dir.

    Returns the list of files written (empty when there is no icon).
    """
    if not icon:
        return []
    src = Path(icon)
    if not src.is_file():
        return []
    grid = Path(shortcuts_path).parent / "grid"
    try:
        grid.mkdir(parents=True, exist_ok=True)
    except OSError:
        return []
    written = []
    for name in grid_names(appid):
        try:
            shutil.copyfile(src, grid / name)
            written.append(grid / name)
        except OSError:
            continue
    return written


def remove_artwork(shortcuts_path, appid):
    """Delete this shortcut's grid/ artwork files. Returns removal count."""
    grid = Path(shortcuts_path).parent / "grid"
    removed = 0
    for name in grid_names(appid):
        try:
            (grid / name).unlink()
            removed += 1
        except OSError:
            continue
    return removed


def add_shortcut(project, pid, app_root, icon=None, files=None, argv=None, start_dir=None):
    """Add/update this pid's entry in every Steam user's shortcuts.vdf.

    Returns the list of files written. Existing entries for the pid keep
    their appid (preserves artwork); other pids and foreign entries are
    left untouched. The project icon is also copied to each user's grid/
    artwork dir when one is available.
    """
    from .tasks import shortcut_name

    app_name = shortcut_name(project)
    targets = files if files is not None else shortcuts_files()
    if not targets:
        raise RuntimeError("No Steam userdata found (is Steam installed?)")
    written = []
    for path in targets:
        entries = load_entries(path)
        matched_key = next(
            (k for k, e in entries.items() if entry_matches(e, pid, app_name)),
            None,
        )
        new_entry = build_entry(project, pid, app_root, icon=icon, argv=argv, start_dir=start_dir)
        if matched_key is not None and isinstance(entries[matched_key], dict):
            # Preserve appid/artwork linkage, refresh launch fields.
            new_entry["appid"] = entries[matched_key].get("appid", new_entry["appid"])
            tags = entries[matched_key].get("tags", {})
            new_entry["tags"] = tags if isinstance(tags, dict) else {}
            entries[matched_key] = new_entry
        else:
            entries[str(len(entries))] = new_entry
        save_entries(path, entries)
        write_artwork(path, new_entry["appid"], icon)
        written.append(Path(path))
    return written


def remove_shortcut(pid, app_name=None, files=None):
    """Remove this pid's entry from every Steam user's shortcuts.vdf.

    Returns the number of entries removed. Foreign entries preserved;
    files that end up with no entries at all are left as empty shortcut
    dicts (Steam recreates them). Matching grid/ artwork is deleted too.
    """
    targets = files if files is not None else shortcuts_files()
    removed = 0
    for path in targets:
        entries = load_entries(path)
        doomed = [k for k, e in entries.items() if entry_matches(e, pid, app_name)]
        if not doomed:
            continue
        appids = [e.get("appid") for e in (entries[k] for k in doomed) if isinstance(e, dict)]
        for key in doomed:
            entries.pop(key, None)
            removed += 1
        save_entries(path, entries)
        for appid in appids:
            if appid is not None:
                remove_artwork(path, appid)
    return removed
