"""GUI smoke tests. These would have caught the blank-window regression
(where half of __init__ ended up nested inside another def).

Requires a display (CI runs under xvfb-run).
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from eglantine_launcher.ui.window import DepsDialog, LauncherWindow  # noqa: E402


def _make_app():
    return Adw.Application(
        application_id="dev.savagecore.EglantineLauncherTest", flags=Gio.ApplicationFlags.NON_UNIQUE
    )


def test_main_window_has_content(needs_display, fake_home):
    app = _make_app()
    window = LauncherWindow(app)
    try:
        content = window.get_content()
        assert isinstance(content, Adw.ToolbarView), (
            f"window content missing (got {content!r}); __init__ may be truncated"
        )
        assert window.project_row.get_model().get_n_items() >= 1
        assert window.mode_row.get_model().get_n_items() >= 1
        assert window.version_drop.get_model().get_n_items() >= 1
        assert window.launch_btn.get_sensitive()
    finally:
        window.close()


def test_main_window_mode_row_reflects_source_flag(needs_display, fake_home):
    from eglantine_launcher.config import load, save

    app = _make_app()
    cfg = load()
    cfg["source_enabled"] = False
    save(cfg)
    window = LauncherWindow(app)
    try:
        window._update_mode_options({"id": "x"})
        model = window.mode_row.get_model()
        assert model.get_n_items() == 1
        assert model.get_string(0) == "Release (download)"
    finally:
        window.close()


def test_settings_window_builds(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import SettingsWindow

    _app = _make_app()
    window = SettingsWindow(None)
    try:
        assert window.list_box is not None
        assert window.source_row is not None
    finally:
        window.close()


def test_settings_window_builds_with_token(needs_display, fake_home):
    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    cfg = load()
    cfg["github_token"] = "dummy"
    save(cfg)
    # must not raise (PasswordEntryRow has no subtitle; status goes to the group)
    window = SettingsWindow(None)
    try:
        assert window.api_group is not None
    finally:
        window.close()


def test_seen_recorded_on_version_model(needs_display, fake_home):
    from eglantine_launcher.ui.window import LauncherWindow

    app = Adw.Application(
        application_id="dev.savagecore.EglantineLauncherTest", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    window = LauncherWindow(app)
    try:
        window._set_version_model(["Latest", "build-99"], ["build-99"], [], [], "eglantine", True)
        assert window.cfg["seen"]["eglantine"]["release"] == "build-99"
        window._set_version_model(["Latest"], [], ["abc123"], [], "eglantine", False)
        assert window.cfg["seen"]["eglantine"]["commit"] == "abc123"
    finally:
        window.close()


def test_deps_dialog_builds(needs_display, fake_home):
    app = _make_app()
    parent = LauncherWindow(app)
    try:
        dialog = DepsDialog(parent, "- cmake: not found", on_ignore=lambda: None)
        try:
            assert dialog is not None
        finally:
            dialog.close()
    finally:
        parent.close()
