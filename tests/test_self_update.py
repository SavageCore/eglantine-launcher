"""Unit tests for launcher self-update helpers (no network, no display)."""

import os
import sys

import pytest

from eglantine_launcher import tasks


def _assets():
    return [
        {"name": "eglantine-launcher-x86_64.AppImage", "browser_download_url": "https://x/app"},
        {"name": "eglantine-launcher-windows.zip", "browser_download_url": "https://x/win.zip"},
        {"name": "eglantine-launcher_0.1.8-1_amd64.deb", "browser_download_url": "https://x/deb"},
    ]


def test_launcher_update_asset_appimage():
    asset = tasks.launcher_update_asset(_assets(), "appimage")
    assert asset["name"].endswith(".AppImage")


def test_launcher_update_asset_windows():
    asset = tasks.launcher_update_asset(_assets(), "windows")
    assert asset["name"] == "eglantine-launcher-windows.zip"


def test_launcher_update_asset_other_methods_none():
    assert tasks.launcher_update_asset(_assets(), "dnf") is None
    assert tasks.launcher_update_asset([], "appimage") is None


def test_detect_windows_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert tasks.detect_install_method() == "windows"
    monkeypatch.delattr(sys, "frozen", raising=False)


def test_detect_flatpak(monkeypatch):
    monkeypatch.setenv("FLATPAK_ID", tasks.FLATPAK_APP_ID)
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert tasks.detect_install_method() == "flatpak"


def test_detect_appimage(monkeypatch):
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.setenv("APPIMAGE", "/tmp/eglantine-launcher.AppImage")
    assert tasks.detect_install_method() == "appimage"


def test_detect_system_pkg_repo_paths(monkeypatch, tmp_path):

    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(tasks, "__file__", "/usr/lib/eglantine-launcher/tasks.py")
    dnf = tmp_path / "eglantine-launcher.repo"
    apt = tmp_path / "eglantine-launcher.list"
    monkeypatch.setattr(tasks, "DNF_REPO_PATH", str(dnf))
    monkeypatch.setattr(tasks, "APT_SOURCE_PATH", str(apt))
    assert tasks.detect_install_method() == "manual-pkg"
    dnf.write_text("repo")
    assert tasks.detect_install_method() == "dnf"
    dnf.unlink()
    apt.write_text("deb")
    assert tasks.detect_install_method() == "apt"


def test_detect_source(monkeypatch):
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(tasks, "__file__", "/home/user/git/eglantine-launcher/eglantine_launcher/tasks.py")
    if hasattr(sys, "frozen"):
        monkeypatch.delattr(sys, "frozen", raising=False)
    assert tasks.detect_install_method() == "source"


def test_dialog_text_package_managers():
    for method, cmd in tasks.UPDATE_COMMANDS.items():
        title, body = tasks.self_update_dialog_text(method, "0.2.0")
        assert "0.2.0" in title
        assert cmd in body
        assert "GitHub" in body


def test_dialog_text_manual_points_to_repos():
    title, body = tasks.self_update_dialog_text("manual-pkg", "0.2.0")
    assert tasks.REPOS_PAGE in body
    assert "repo" in body.lower()


def test_stage_appimage_swap(tmp_path):
    current = tmp_path / "eglantine-launcher.AppImage"
    current.write_text("old")
    staged = tmp_path / "eglantine-launcher.AppImage.new"
    staged.write_text("new")
    out = tasks.stage_appimage_swap(str(current), staged)
    assert out == current
    assert current.read_text() == "new"
    assert not staged.exists()
    assert os.access(current, os.X_OK)


def test_build_windows_updater_contains_swap_and_restart(tmp_path):
    install = tmp_path / "install"
    staged = tmp_path / "new"
    script = tasks.build_windows_updater(str(install), str(staged))
    assert "robocopy" in script
    assert "eglantine-launcher.exe" in script
    assert str(install) in script
    assert str(staged) in script


def test_download_url_streams_file(tmp_path, monkeypatch):
    import io

    payload = b"x" * 1024

    class FakeHeaders(dict):
        def get(self, key, default=None):
            return str(len(payload)) if key == "Content-Length" else default

    class FakeResp:
        headers = FakeHeaders()

        def __init__(self):
            self._buf = io.BytesIO(payload)

        def read(self, n=-1):
            return self._buf.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    seen = []
    monkeypatch.setattr(tasks.urllib.request, "urlopen", lambda req, timeout=120: FakeResp())
    dest = tmp_path / "sub" / "file.bin"
    tasks.download_url("https://example/x", dest, progress_cb=seen.append)
    assert dest.read_bytes() == payload
    assert seen and seen[-1] == pytest.approx(1.0)
