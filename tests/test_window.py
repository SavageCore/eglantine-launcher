"""GUI smoke tests. These would have caught the blank-window regression
(where half of __init__ ended up nested inside another def).

Requires a display (CI runs under xvfb-run).
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from eglantine_launcher.ui.deps_dialog import DepsDialog  # noqa: E402
from eglantine_launcher.ui.window import LauncherWindow  # noqa: E402


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


class _FakeDialog:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_close_request_quits_when_configured(needs_display, fake_home):
    from eglantine_launcher.config import load, save

    app = _make_app()
    cfg = load()
    cfg["close_behavior"] = "quit"
    save(cfg)
    window = LauncherWindow(app)
    try:
        assert window.cfg["close_behavior"] == "quit"
        assert window._on_close_request() is False
    finally:
        window.close()


def test_close_request_hides_to_tray_when_configured(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.config import load, save

    app = _make_app()
    cfg = load()
    cfg["close_behavior"] = "tray"
    save(cfg)
    window = LauncherWindow(app)
    try:
        hid = []
        monkeypatch.setattr(window, "_hide_to_tray", lambda: hid.append(True))
        assert window._on_close_request() is True
        assert hid == [True]
    finally:
        window.close()


def test_close_request_asks_first_time(needs_display, fake_home, monkeypatch):
    app = _make_app()
    window = LauncherWindow(app)
    try:
        assert window.cfg["close_behavior"] is None
        asked = []
        monkeypatch.setattr(window, "_ask_close_behavior", lambda: asked.append(True))
        assert window._on_close_request() is True
        assert asked == [True]
    finally:
        window.close()


def test_close_request_skipped_in_auto_mode(needs_display, fake_home, monkeypatch):
    app = _make_app()
    window = LauncherWindow(app, auto=True)
    try:
        asked = []
        monkeypatch.setattr(window, "_ask_close_behavior", lambda: asked.append(True))
        assert window._on_close_request() is False
        assert asked == []
    finally:
        window.close()


def test_close_choice_quit_saves_and_closes(needs_display, fake_home):
    from eglantine_launcher.config import load

    app = _make_app()
    window = LauncherWindow(app)
    window._on_close_choice(_FakeDialog(), "quit")
    # window closed itself; do not reuse it
    assert load()["close_behavior"] == "quit"


def test_close_choice_tray_saves_and_hides(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.config import load

    app = _make_app()
    window = LauncherWindow(app)
    try:
        hid = []
        monkeypatch.setattr(window, "_hide_to_tray", lambda: hid.append(True))
        dlg = _FakeDialog()
        window._on_close_choice(dlg, "tray")
        assert dlg.closed
        assert hid == [True]
        assert load()["close_behavior"] == "tray"
    finally:
        window.close()


def test_close_choice_dismissed_stays_open(needs_display, fake_home):
    from eglantine_launcher.config import load

    app = _make_app()
    window = LauncherWindow(app)
    try:
        window._on_close_choice(_FakeDialog(), "dismissed")
        assert load()["close_behavior"] is None
    finally:
        window.close()


def test_launch_choice_saves_applies_and_launches(needs_display, fake_home, monkeypatch):
    import eglantine_launcher.ui.window as winmod
    from eglantine_launcher.config import load

    app = _make_app()
    window = LauncherWindow(app)
    try:
        applied = []
        begun = []
        monkeypatch.setattr(winmod, "hide_to_tray", lambda _w: applied.append("tray"))
        monkeypatch.setattr(winmod, "minimize_to_taskbar", lambda _w: applied.append("taskbar"))
        monkeypatch.setattr(window, "_begin_launch", lambda: begun.append(True))
        dlg = _FakeDialog()
        window._on_launch_choice(dlg, "tray")
        assert dlg.closed
        assert load()["minimize_on_launch"] == "tray"
        assert applied == ["tray"]
        assert begun == [True]
    finally:
        window.close()


def test_launch_choice_dismissed_minimizes_without_saving(needs_display, fake_home, monkeypatch):
    import eglantine_launcher.ui.window as winmod
    from eglantine_launcher.config import load

    app = _make_app()
    window = LauncherWindow(app)
    try:
        applied = []
        begun = []
        monkeypatch.setattr(winmod, "hide_to_tray", lambda _w: applied.append("tray"))
        monkeypatch.setattr(winmod, "minimize_to_taskbar", lambda _w: applied.append("taskbar"))
        monkeypatch.setattr(window, "_begin_launch", lambda: begun.append(True))
        window._on_launch_choice(_FakeDialog(), "dismissed")
        assert load()["minimize_on_launch"] is None
        assert applied == ["taskbar"]
        assert begun == [True]
    finally:
        window.close()


def test_settings_window_has_window_behavior_rows(needs_display, fake_home):
    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    cfg = load()
    cfg["minimize_on_launch"] = "tray"
    cfg["close_behavior"] = "quit"
    save(cfg)
    window = SettingsWindow(None)
    try:
        assert window.launch_behavior_row.get_selected() == 2  # Ask, Taskbar, Tray
        assert window.close_behavior_row.get_selected() == 1  # Ask, Quit
        window.launch_behavior_row.set_selected(0)
        window.close_behavior_row.set_selected(2)  # Ask, Quit, Tray
        assert load()["minimize_on_launch"] is None
        assert load()["close_behavior"] == "tray"
    finally:
        window.close()


def test_launched_restores_window_when_hidden(needs_display, fake_home, monkeypatch):
    from gi.repository import GLib

    from eglantine_launcher.ui.window import LauncherWindow

    window = LauncherWindow(_make_app())
    try:
        window._hidden_for_launch = True
        restored = []
        monkeypatch.setattr(window, "restore_from_tray", lambda: restored.append(True))
        waited = []

        class Proc:
            def wait(self):
                waited.append(True)
                return 0

        monkeypatch.setattr(GLib, "idle_add", lambda fn, *a, **k: fn(*a))
        window._launched(Proc())
        monkeypatch.undo()
        assert waited == [True]
        assert restored == [True]
    finally:
        window.close()


def test_launched_skips_restore_in_auto_mode(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.ui.window import LauncherWindow

    window = LauncherWindow(_make_app(), auto=True)
    try:
        window._hidden_for_launch = True
        restored = []
        monkeypatch.setattr(window, "restore_from_tray", lambda: restored.append(True))

        class Proc:
            def wait(self):
                raise AssertionError("must not block in auto mode")

        window._launched(Proc())
        assert restored == []
    finally:
        window.close()


def test_launched_skips_without_proc(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.ui.window import LauncherWindow

    window = LauncherWindow(_make_app())
    try:
        window._hidden_for_launch = True
        restored = []
        monkeypatch.setattr(window, "restore_from_tray", lambda: restored.append(True))
        window._launched(None)
        assert restored == []
    finally:
        window.close()


def test_restore_clears_hidden_flag(needs_display, fake_home):
    from eglantine_launcher.ui.window import LauncherWindow

    window = LauncherWindow(_make_app())
    try:
        window._hidden_for_launch = True
        window.restore_from_tray()
        assert window._hidden_for_launch is False
    finally:
        window.close()


def test_launched_returns_without_waiting_when_visible(needs_display, fake_home):
    from eglantine_launcher.ui.window import LauncherWindow

    window = LauncherWindow(_make_app())
    try:
        window._hidden_for_launch = False

        class Proc:
            def wait(self):
                raise AssertionError("must not block when window stayed visible")

        assert window._launched(Proc()) is True
    finally:
        window.close()


def test_launched_returns_false_without_proc(needs_display, fake_home):
    from eglantine_launcher.ui.window import LauncherWindow

    window = LauncherWindow(_make_app())
    try:
        assert window._launched(None) is False
    finally:
        window.close()
