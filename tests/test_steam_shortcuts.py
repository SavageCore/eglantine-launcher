"""Tests for Steam shortcuts.vdf management (per-project, foreign-safe)."""

import sys
from pathlib import Path


def _make_userdata(tmp_path, uid="12345"):
    config = tmp_path / "Steam" / "userdata" / uid / "config"
    config.mkdir(parents=True)
    return config / "shortcuts.vdf"


def _project(pid="eglantine", name="Eglantine"):
    return {"id": pid, "name": name}


def test_add_and_remove_roundtrip(tmp_path):
    from eglantine_launcher import steam_shortcuts as ss

    vdf_path = _make_userdata(tmp_path)
    bases = [tmp_path / "Steam"]
    assert ss.shortcuts_files(bases=bases) == [vdf_path]
    assert ss.shortcut_exists("eglantine", "Eglantine (EL)", files=[vdf_path]) is False

    written = ss.add_shortcut(_project(), "eglantine", "/app/root", files=[vdf_path])
    assert written == [vdf_path]
    assert ss.shortcut_exists("eglantine", "Eglantine (EL)", files=[vdf_path]) is True

    assert ss.remove_shortcut("eglantine", "Eglantine (EL)", files=[vdf_path]) == 1
    assert ss.shortcut_exists("eglantine", "Eglantine (EL)", files=[vdf_path]) is False


def test_foreign_entries_preserved(tmp_path):
    import vdf

    from eglantine_launcher import steam_shortcuts as ss

    vdf_path = _make_userdata(tmp_path)
    foreign = {
        "appid": 111,
        "AppName": "Some Other Game",
        "Exe": "/usr/bin/something",
        "StartDir": "/tmp",
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": "",
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
    with vdf_path.open("wb") as fh:
        fh.write(vdf.binary_dumps({"shortcuts": {"0": foreign}}))

    ss.add_shortcut(_project(), "eglantine", "/app/root", files=[vdf_path])
    entries = ss.load_entries(vdf_path)
    assert len(entries) == 2
    assert any(e.get("AppName") == "Some Other Game" for e in entries.values())

    ss.remove_shortcut("eglantine", "Eglantine (EL)", files=[vdf_path])
    entries = ss.load_entries(vdf_path)
    assert len(entries) == 1
    assert next(iter(entries.values()))["AppName"] == "Some Other Game"


def test_per_project_entries_coexist(tmp_path):
    from eglantine_launcher import steam_shortcuts as ss

    vdf_path = _make_userdata(tmp_path)
    ss.add_shortcut(_project("eglantine", "Eglantine"), "eglantine", "/app/root", files=[vdf_path])
    ss.add_shortcut(_project("fork-a", "Fork A"), "fork-a", "/app/root", files=[vdf_path])
    entries = ss.load_entries(vdf_path)
    assert len(entries) == 2

    assert ss.shortcut_exists("eglantine", "Eglantine (EL)", files=[vdf_path]) is True
    assert ss.shortcut_exists("fork-a", "Fork A (EL)", files=[vdf_path]) is True

    ss.remove_shortcut("eglantine", "Eglantine (EL)", files=[vdf_path])
    assert ss.shortcut_exists("eglantine", "Eglantine (EL)", files=[vdf_path]) is False
    assert ss.shortcut_exists("fork-a", "Fork A (EL)", files=[vdf_path]) is True


def test_add_without_userdata_raises(tmp_path):
    import pytest

    from eglantine_launcher import steam_shortcuts as ss

    with pytest.raises(RuntimeError, match="No Steam userdata"):
        ss.add_shortcut(_project(), "eglantine", "/app/root", files=[])


def test_artwork_written_and_removed(tmp_path):
    from eglantine_launcher import steam_shortcuts as ss

    vdf_path = _make_userdata(tmp_path)
    icon = tmp_path / "emu.png"
    icon.write_bytes(b"\x89PNG-fake-icon")
    grid = vdf_path.parent / "grid"

    ss.add_shortcut(_project(), "eglantine", "/app/root", icon=icon, files=[vdf_path])
    entries = ss.load_entries(vdf_path)
    appid = next(iter(entries.values()))["appid"]
    # Logo only: no stretched capsule/hero cover.
    assert ss.logo_name(appid).endswith("_logo.png")
    assert (grid / ss.logo_name(appid)).read_bytes() == b"\x89PNG-fake-icon"
    assert [p.name for p in grid.iterdir()] == [ss.logo_name(appid)]

    ss.remove_shortcut("eglantine", "Eglantine (EL)", files=[vdf_path])
    assert list(grid.iterdir()) == []


def test_artwork_cleans_legacy_cover(tmp_path):
    from eglantine_launcher import steam_shortcuts as ss

    vdf_path = _make_userdata(tmp_path)
    icon = tmp_path / "emu.png"
    icon.write_bytes(b"\x89PNG-fake-icon")
    grid = vdf_path.parent / "grid"
    grid.mkdir(parents=True)

    # Simulate 0.1.10 layout: capsule/hero files written from the icon.
    ss.add_shortcut(_project(), "eglantine", "/app/root", icon=icon, files=[vdf_path])
    entries = ss.load_entries(vdf_path)
    appid = next(iter(entries.values()))["appid"]
    for stale in ss.grid_names(appid)[1:]:
        (grid / stale).write_bytes(b"stale-cover")

    # Re-applying the shortcut drops the stretched cover, keeps the logo.
    ss.add_shortcut(_project(), "eglantine", "/app/root", icon=icon, files=[vdf_path])
    assert [p.name for p in grid.iterdir()] == [ss.logo_name(appid)]


def test_artwork_preserves_foreign_grid_files(tmp_path):
    from eglantine_launcher import steam_shortcuts as ss

    vdf_path = _make_userdata(tmp_path)
    grid = vdf_path.parent / "grid"
    grid.mkdir(parents=True)
    (grid / "999p.png").write_bytes(b"foreign")
    icon = tmp_path / "emu.png"
    icon.write_bytes(b"\x89PNG-fake-icon")

    ss.add_shortcut(_project(), "eglantine", "/app/root", icon=icon, files=[vdf_path])
    ss.remove_shortcut("eglantine", "Eglantine (EL)", files=[vdf_path])
    assert (grid / "999p.png").read_bytes() == b"foreign"


def test_add_without_icon_skips_artwork(tmp_path):
    from eglantine_launcher import steam_shortcuts as ss

    vdf_path = _make_userdata(tmp_path)
    ss.add_shortcut(_project(), "eglantine", "/app/root", files=[vdf_path])
    assert not (vdf_path.parent / "grid").exists()


def test_entry_uses_packaged_argv(tmp_path):
    from eglantine_launcher import steam_shortcuts as ss

    vdf_path = _make_userdata(tmp_path)
    argv = ["/home/user/eglantine-launcher.AppImage", "--auto", "--project", "eglantine"]
    ss.add_shortcut(
        _project(), "eglantine", "/tmp/.mount-dead", files=[vdf_path], argv=argv, start_dir="/home/user"
    )
    entry = next(iter(ss.load_entries(vdf_path).values()))
    assert entry["Exe"] == "/home/user/eglantine-launcher.AppImage"
    assert "--project eglantine" in entry["LaunchOptions"]
    assert ".mount-dead" not in entry["LaunchOptions"]
    assert entry["StartDir"] == "/home/user"


def test_tasks_multi_target_and_state(tmp_path, fake_home, monkeypatch):
    from eglantine_launcher import tasks

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(tasks, "desktop_dir", lambda: desktop)
    # No Steam userdata under fake HOME: steam target stays False.
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    cfg = {
        "project": "eglantine",
        "install_dir": str(tmp_path / "install"),
        "projects": [
            {"id": "eglantine", "name": "Eglantine", "icon_url": None},
            {"id": "fork-a", "name": "Fork A", "icon_url": None},
        ],
    }
    written = tasks.install_shortcut(cfg, "eglantine", "/app/root", targets=("applications", "desktop"))
    assert set(written) == {"applications", "desktop"}
    assert written["applications"].read_text() == written["desktop"].read_text()
    assert "--auto --project eglantine" in written["desktop"].read_text()

    # Second fork coexists, content differs by --project marker.
    tasks.install_shortcut(cfg, "fork-a", "/app/root", targets=("applications", "desktop"))
    assert tasks.shortcut_state(cfg, "eglantine") == {
        "applications": True,
        "desktop": True,
        "steam": False,
    }
    assert tasks.desktop_shortcut_path(cfg, "fork-a").exists()

    assert tasks.remove_shortcut(cfg, "eglantine", targets=("applications", "desktop")) is True
    assert tasks.shortcut_state(cfg, "eglantine")["applications"] is False
    assert tasks.shortcut_state(cfg, "eglantine")["desktop"] is False
    # Other fork untouched.
    assert tasks.shortcut_path(cfg, "fork-a").exists()
    assert tasks.desktop_shortcut_path(cfg, "fork-a").exists()


def test_tasks_steam_target_uses_shortcuts_files(tmp_path, fake_home, monkeypatch):
    from eglantine_launcher import steam_shortcuts as ss
    from eglantine_launcher import tasks

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    uid_dir = fake_home / ".local" / "share" / "Steam" / "userdata" / "99999" / "config"
    uid_dir.mkdir(parents=True)
    vdf_path = uid_dir / "shortcuts.vdf"

    cfg = {
        "project": "eglantine",
        "install_dir": str(tmp_path / "install"),
        "projects": [{"id": "eglantine", "name": "Eglantine", "icon_url": None}],
    }
    written = tasks.install_shortcut(cfg, "eglantine", "/app/root", targets=("steam",))
    assert written["steam"] == vdf_path
    assert ss.shortcut_exists("eglantine", "Eglantine (EL)") is True
    assert tasks.shortcut_state(cfg, "eglantine")["steam"] is True

    assert tasks.remove_shortcut(cfg, "eglantine", targets=("steam",)) is True
    assert tasks.shortcut_state(cfg, "eglantine")["steam"] is False
    assert sys.version_info >= (3, 11)


def test_windows_steam_roots_from_env(monkeypatch, tmp_path):
    import os

    from eglantine_launcher import steam_shortcuts as ss

    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "PF86"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "PF86"))
    monkeypatch.delenv("ProgramW6432", raising=False)
    assert ss._windows_steam_roots() == [os.path.join(str(tmp_path / "PF86"), "Steam")]


def test_quote_arg_double_quotes():
    from eglantine_launcher.tasks import quote_arg

    assert quote_arg("/usr/bin/eglantine-launcher") == "/usr/bin/eglantine-launcher"
    assert quote_arg("/home/u/My Games/app.AppImage") == '"/home/u/My Games/app.AppImage"'
    assert quote_arg("") == '""'


def test_windows_skips_desktop_targets(tmp_path, fake_home, monkeypatch):
    from eglantine_launcher import tasks

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(tasks, "is_windows", lambda: True)
    cfg = {
        "project": "eglantine",
        "install_dir": str(tmp_path / "install"),
        "projects": [{"id": "eglantine", "name": "Eglantine", "icon_url": None}],
    }
    assert tasks.install_shortcut(cfg, "eglantine", "/app/root", targets=("applications", "desktop")) == {}
    assert tasks.shortcut_state(cfg, "eglantine") == {"applications": False, "desktop": False, "steam": False}
    assert tasks.remove_shortcut(cfg, "eglantine", targets=("applications", "desktop")) is True
