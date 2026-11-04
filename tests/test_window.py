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


def test_main_window_restores_saved_size(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.config import load

    app = _make_app()
    window = LauncherWindow(app)
    try:
        assert window.get_default_size() == (640, 600)
        monkeypatch.setattr(window, "get_width", lambda: 500)
        monkeypatch.setattr(window, "get_height", lambda: 650)
        window._save_window_size()
        assert load()["window_width"] == 500
        assert load()["window_height"] == 650
    finally:
        window.close()


def test_stop_button_hidden_initially_and_destructive(needs_display, fake_home):
    window = LauncherWindow(_make_app())
    try:
        assert window.stop_btn.get_visible() is False
        assert window.stop_btn.get_label() == "Stop"
        assert "destructive-action" in window.stop_btn.get_css_classes()
        assert not window.has_action("shortcut")
        assert window.shortcut_btn.get_label() == "Shortcuts..."
        assert window.has_action("folder")
    finally:
        window.close()


class _FakeSwitch:
    def __init__(self, active):
        self._active = active

    def get_active(self):
        return self._active


def test_shortcuts_response_applies_desktop_and_menu(needs_display, fake_home, tmp_path, monkeypatch):
    from eglantine_launcher import tasks

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(tasks, "desktop_dir", lambda: tmp_path / "Desktop")
    (tmp_path / "Desktop").mkdir()

    window = LauncherWindow(_make_app())
    try:
        pid = window._current_info().get("id", window.cfg.get("project"))
        window._on_shortcuts_response(
            None, "apply", pid, _FakeSwitch(True), _FakeSwitch(True), _FakeSwitch(False)
        )
        assert tasks.shortcut_path(window.cfg, pid).exists()
        assert tasks.desktop_shortcut_path(window.cfg, pid).exists()
        window._on_shortcuts_response(
            None, "apply", pid, _FakeSwitch(False), _FakeSwitch(False), _FakeSwitch(False)
        )
        assert not tasks.shortcut_path(window.cfg, pid).exists()
        assert not tasks.desktop_shortcut_path(window.cfg, pid).exists()
    finally:
        window.close()


def test_confirm_stop_sets_event_and_kills_proc(needs_display, fake_home):
    window = LauncherWindow(_make_app())
    try:
        window._busy = True
        killed = []

        class Proc:
            def poll(self):
                return None

            def terminate(self):
                killed.append(True)

        window._set_build_proc(Proc())
        window._confirm_stop()
        assert window._cancel_event.is_set()
        assert killed == [True]
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
        assert window.sources_group is not None
        assert window.source_row is not None
        # default config ships two sources; both rows must render
        assert len(window._source_rows) == 2
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
        monkeypatch.setattr(window, "_hide_to_tray", lambda: applied.append("tray"))
        monkeypatch.setattr(winmod, "minimize_to_taskbar", lambda _w: applied.append("taskbar"))
        monkeypatch.setattr(window, "_begin_launch", lambda: begun.append(True))
        dlg = _FakeDialog()
        window._on_launch_choice(dlg, "tray")
        assert dlg.closed
        assert load()["minimize_on_launch"] == "tray"
        assert window._launch_hide == "tray"
        assert window._hidden_for_launch is True
        # hide is deferred until just before launch, not applied here
        assert applied == []
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
        monkeypatch.setattr(window, "_hide_to_tray", lambda: applied.append("tray"))
        monkeypatch.setattr(winmod, "minimize_to_taskbar", lambda _w: applied.append("taskbar"))
        monkeypatch.setattr(window, "_begin_launch", lambda: begun.append(True))
        window._on_launch_choice(_FakeDialog(), "dismissed")
        assert load()["minimize_on_launch"] is None
        assert window._launch_hide == "taskbar"
        assert window._hidden_for_launch is True
        # hide is deferred until just before launch, not applied here
        assert applied == []
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


def test_version_tick_markup_only_installed_is_green():
    from eglantine_launcher.ui.window import version_tick_markup

    assert version_tick_markup("✓ be71c42 (installed build)").startswith('<span color="#4ade80">✓</span>')
    assert version_tick_markup("✓ build-120 (installed)").startswith('<span color="#4ade80">✓</span>')
    assert version_tick_markup("✓ build-110 some title") is None
    assert version_tick_markup("Latest ✓") is None
    assert version_tick_markup("plain row") is None
    # markup escapes the label text
    assert "&lt;" in version_tick_markup("✓ abc <x> (installed build)")


def test_branch_row_search_is_wired(needs_display, fake_home):
    """The branch search entry needs an expression or it shows but never filters."""
    from gi.repository import Gtk

    from eglantine_launcher.ui.settings_window import ProjectEditDialog, SettingsWindow

    window = SettingsWindow(None)
    try:
        dialog = ProjectEditDialog(
            window,
            None,
            lambda *a: None,
            prefill={
                "source_url": "https://example.com/o/r.git",
                "name": "r",
                "branches": ["nx2", "nx2-experimental", "master"],
                "branch": "nx2",
            },
            cfg={},
        )
        try:
            assert dialog.branch_row.get_enable_search() is True
            assert dialog.branch_row.get_expression() is not None
            # search-match-mode only exists on newer libadwaita
            # (Ubuntu noble lacks it); mirror the source guard.
            get_mode = getattr(dialog.branch_row, "get_search_match_mode", None)
            if get_mode is not None:
                assert get_mode() == Gtk.StringFilterMatchMode.SUBSTRING
        finally:
            dialog.close()
    finally:
        window.close()


def test_source_row_click_opens_edit(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        opened = []
        monkeypatch.setattr(window, "_edit_project", lambda proj: opened.append(proj))
        row = window._source_rows[0]
        assert row is not None
        window._on_row_activated(row)
        assert opened == [row.project]
    finally:
        window.close()


def test_remove_project_updates_config(needs_display, fake_home):
    from eglantine_launcher.config import load
    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        first, second = window.cfg["projects"][0], window.cfg["projects"][1]
        window.cfg["project"] = first["id"]
        window._remove_project(first)
        ids = [p["id"] for p in window.cfg["projects"]]
        assert first["id"] not in ids
        assert window.cfg["project"] == second["id"]
        assert load()["project"] == second["id"]
    finally:
        window.close()


def test_remove_remote_project_hides_it(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        proj = dict(window.cfg["projects"][0], remote=True)
        window.cfg["projects"][0] = proj
        window._remove_project(proj)
        assert proj["id"] not in [p["id"] for p in window.cfg["projects"]]
        assert proj["id"] in window.cfg["hidden_sources"]
    finally:
        window.close()


def test_delete_button_confirms_then_removes(needs_display, fake_home):
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        proj = window.cfg["projects"][0]
        pid = proj["id"]
        window._on_delete_clicked(None, proj)
        dlg = next(w for w in Gtk.Window.list_toplevels() if isinstance(w, Adw.MessageDialog))
        try:
            assert dlg.get_heading() == f"Remove {proj['name']}?"
            dlg.response("remove")
        finally:
            dlg.close()
        assert pid not in [p["id"] for p in window.cfg["projects"]]
    finally:
        window.close()


def test_delete_button_cancel_keeps_project(needs_display, fake_home):
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        proj = window.cfg["projects"][0]
        window._on_delete_clicked(None, proj)
        dlg = next(w for w in Gtk.Window.list_toplevels() if isinstance(w, Adw.MessageDialog))
        try:
            dlg.response("cancel")
        finally:
            dlg.close()
        assert proj["id"] in [p["id"] for p in window.cfg["projects"]]
    finally:
        window.close()


def test_edit_dialog_storage_tab_only_for_existing_project(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    cfg = {"projects": []}
    proj = {"id": "eglantine", "name": "Eglantine", "source_url": "https://github.com/o/r.git"}
    existing = ProjectEditDialog(None, proj, lambda *a: None, cfg=cfg)
    try:
        assert existing._stack is not None
        assert existing._stack.get_child_by_name("settings") is not None
        assert existing._stack.get_child_by_name("storage") is not None
    finally:
        existing.close()
    new = ProjectEditDialog(None, None, lambda *a: None, cfg=cfg)
    try:
        assert new._stack is None
    finally:
        new.close()


def test_storage_tab_refresh_replaces_rows(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    cfg = {"projects": []}
    proj = {"id": "eglantine", "name": "Eglantine", "source_url": "https://github.com/o/r.git"}
    dialog = ProjectEditDialog(None, proj, lambda *a: None, cfg=cfg)
    try:
        rows = [
            {
                "kind": "release",
                "pid": "eglantine",
                "label": "Release download (build-9)",
                "detail": "/tmp/x.AppImage",
                "size": 100,
                "path": "/tmp/x.AppImage",
                "in_use": True,
            },
        ]
        dialog._render_storage(rows, "")
        dialog._render_storage(rows, "")
        assert len(dialog._storage_rows) == 1

        def count_action_rows(widget):
            n = 0
            child = widget.get_first_child()
            while child is not None:
                n += count_action_rows(child)
                child = child.get_next_sibling()
            from gi.repository import Adw

            if isinstance(widget, Adw.ActionRow):
                n += 1
            return n

        assert count_action_rows(dialog._storage_group) == 1
    finally:
        dialog.close()


def test_apply_launch_behavior_defers_hide(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.ui.window import LauncherWindow

    app = _make_app()
    window = LauncherWindow(app)
    try:
        hidden = []
        monkeypatch.setattr(window, "_hide_to_tray", lambda: hidden.append(True))
        window.cfg["minimize_on_launch"] = "tray"
        window._apply_launch_behavior()
        assert window._hidden_for_launch is True
        assert window._launch_hide == "tray"
        assert hidden == []
        window.cfg["minimize_on_launch"] = "none"
        window._apply_launch_behavior()
        assert window._hidden_for_launch is False
        assert window._launch_hide == "none"
        assert hidden == []
    finally:
        window.close()


def test_launch_now_hides_just_before_launch(needs_display, fake_home, monkeypatch):
    from gi.repository import GLib

    import eglantine_launcher.ui.window as winmod
    from eglantine_launcher.ui.window import LauncherWindow

    app = _make_app()
    window = LauncherWindow(app)
    try:
        scheduled = []
        monkeypatch.setattr(GLib, "idle_add", lambda fn, *a: scheduled.append((fn, a)) or 0)
        launched_with = []
        monkeypatch.setattr(
            winmod, "launch", lambda path, cfg=None: launched_with.append((path, cfg)) or "proc"
        )
        finished_with = []
        monkeypatch.setattr(window, "_launched", lambda proc: finished_with.append(proc) or True)

        window._launch_hide = "tray"
        assert window._launch_now("/tmp/eglantine") is True
        assert launched_with == [("/tmp/eglantine", window.cfg)]
        assert finished_with == ["proc"]
        assert len(scheduled) == 3
        assert getattr(scheduled[1][0], "__name__", "") == "_dismiss_toasts"
        assert getattr(scheduled[2][0], "__name__", "") == "_hide_to_tray"

        scheduled.clear()
        window._launch_hide = "none"
        assert window._launch_now("/tmp/eglantine") is True
        assert len(scheduled) == 2
    finally:
        window.close()


def test_settings_open_does_not_verify_token(needs_display, fake_home, monkeypatch):
    import urllib.request

    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    cfg = load()
    cfg["github_token"] = "saved-token"
    cfg.pop("github_token_rejected", None)
    save(cfg)

    def no_network(url_or_req, timeout=None):
        raise AssertionError("no API call expected on settings open")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    window = SettingsWindow(None)
    try:
        assert window._token_status_label.get_visible() is False
    finally:
        window.close()


def _open_settings_with_rejected(monkeypatch, urlopen_fake):
    """Open Settings with a flagged token; run async verify callbacks inline."""
    import time
    import urllib.request

    from gi.repository import GLib

    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    queued = []
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a, **k: queued.append((fn, a)) or 0)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen_fake)
    cfg = load()
    cfg["github_token"] = "saved-token"
    cfg["github_token_rejected"] = True
    save(cfg)
    window = SettingsWindow(None)
    for _ in range(200):
        if queued:
            break
        time.sleep(0.02)
    for fn, args in queued:
        fn(*args)
    return window


def test_settings_open_rechecks_rejected_token_401(needs_display, fake_home, monkeypatch):
    import urllib.error
    import urllib.request  # noqa: F401 (patched via sys.modules below)

    from eglantine_launcher.config import load

    def denied(url_or_req, timeout=None):
        raise urllib.error.HTTPError(
            url_or_req.full_url if hasattr(url_or_req, "full_url") else url_or_req,
            401,
            "Unauthorized",
            {},
            None,
        )

    window = _open_settings_with_rejected(monkeypatch, denied)
    try:
        label = window._token_status_label
        assert label.get_visible() is True
        assert "Invalid token" in label.get_label()
        assert load()["github_token_rejected"] is True
    finally:
        window.close()


def test_settings_open_rechecks_rejected_token_ok(needs_display, fake_home, monkeypatch):
    from conftest import FakeResponse

    from eglantine_launcher.config import load

    def fine(url_or_req, timeout=None):
        return FakeResponse({"resources": {"core": {"limit": 60}}})

    window = _open_settings_with_rejected(monkeypatch, fine)
    try:
        label = window._token_status_label
        assert label.get_visible() is True
        assert "Verified and saved." in label.get_label()
        assert not load().get("github_token_rejected")
    finally:
        window.close()


def test_settings_open_rechecks_rejected_token_offline(needs_display, fake_home, monkeypatch):
    import urllib.error

    from eglantine_launcher.config import load

    def offline(url_or_req, timeout=None):
        raise urllib.error.URLError("boom")

    window = _open_settings_with_rejected(monkeypatch, offline)
    try:
        label = window._token_status_label
        assert label.get_visible() is True
        assert "Couldn't reach GitHub" in label.get_label()
        assert load()["github_token"] == "saved-token"
        assert load()["github_token_rejected"] is True
    finally:
        window.close()


def test_rejected_flag_focuses_token_entry(needs_display, fake_home, monkeypatch):
    import urllib.request

    from gi.repository import GLib

    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    def no_network(url_or_req, timeout=None):
        raise AssertionError("focus test drives no verify by itself")

    # focus assertion must not depend on the async verify running
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    cfg = load()
    cfg["github_token"] = "saved-token"
    cfg["github_token_rejected"] = True
    save(cfg)
    window = SettingsWindow(None)
    try:
        window.present()
        ctx = GLib.MainContext.default()
        for _ in range(100):
            if window.get_focus() is not None:
                break
            ctx.iteration(False)
        focus, node = window.get_focus(), window.token_entry
        assert focus is not None
        while focus is not None and focus is not node:
            focus = focus.get_parent()
        assert focus is node
    finally:
        window.close()


def test_token_enter_triggers_apply(needs_display, fake_home):
    from gi.repository import Gtk

    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        fired = []
        window.token_entry.connect("apply", lambda *_: fired.append(True))

        def find_text(widget):
            if isinstance(widget, Gtk.Text):
                return widget
            child = widget.get_first_child()
            while child is not None:
                found = find_text(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None

        # Text.activate() is gated off-screen; emitting the signal is the
        # same emission a real Enter keypress produces (verified with xdotool).
        find_text(window.token_entry).emit("activate")
        assert fired == [True]
    finally:
        window.close()


def test_token_status_timeout_keeps_newer_message(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        window._set_token_status("Verified and saved.", "success", timeout=30)
        seq = window._token_status_seq
        assert window._token_status_label.get_visible() is True
        # a stale timeout must not clear the newer message
        window._set_token_status("Invalid token - check it and try again.", "error")
        window._clear_token_status(seq)
        assert window._token_status_label.get_visible() is True
        assert "Invalid token" in window._token_status_label.get_label()
        window._clear_token_status(window._token_status_seq)
        assert window._token_status_label.get_visible() is False
    finally:
        window.close()


def _pr_cfg():
    return {
        # Synthetic id: must never match a real entry (see pr-r-366-abcdef).
        "id": "pr-fixture-o-r-123456",
        "name": "Fix shaders (#366)",
        "source_url": "https://github.com/o/r.git",
        "branch": "pr-366",
        "releases_url": None,
        "is_pr": True,
        "pr_url": "https://github.com/o/r/pull/366",
        "pr_number": 366,
        "pr_owner": "o",
        "pr_repo": "r",
        "pr_title": "Fix shaders",
        "pr_state": "open",
        "pr_head_sha": "a" * 40,
    }


def test_pr_menu_action_exists(needs_display, fake_home):
    window = LauncherWindow(_make_app())
    try:
        assert window.has_action("pr-builds")
    finally:
        window.close()


def test_update_mode_options_pr_is_source_only(needs_display, fake_home):
    from eglantine_launcher.config import load, save

    cfg = load()
    cfg["source_enabled"] = True
    save(cfg)
    window = LauncherWindow(_make_app())
    try:
        window.cfg["source_enabled"] = True
        window._update_mode_options(_pr_cfg())
        model = window.mode_row.get_model()
        assert model.get_n_items() == 1
        assert model.get_string(0) == "Build from source"
        assert window.mode_row.get_sensitive() is True
        window.cfg["source_enabled"] = False
        window._update_mode_options(_pr_cfg())
        assert window.mode_row.get_sensitive() is False
    finally:
        window.close()


def test_save_choices_pr_forces_source(needs_display, fake_home):
    from eglantine_launcher.config import load, save

    cfg = load()
    pr = _pr_cfg()
    cfg["projects"].append(pr)
    cfg["project"] = pr["id"]
    cfg["source_enabled"] = False
    save(cfg)
    window = LauncherWindow(_make_app())
    try:
        window._save_choices()
        assert window.cfg["mode"] == "source"
    finally:
        window.close()


def test_refresh_pr_versions_shows_cached_head(needs_display, fake_home, monkeypatch):
    import eglantine_launcher.ui.window as winmod

    def _offline(*a, **k):
        raise Exception("offline")

    monkeypatch.setattr(winmod, "fetch_pr_info", _offline)
    window = LauncherWindow(_make_app())
    try:
        window._refresh_pr_versions(_pr_cfg())
        model = window.version_drop.get_model()
        assert model.get_n_items() == 2  # Latest + head
        assert window._commit_shas == ["a" * 40]
        assert window._latest_commit == "a" * 40
    finally:
        window.close()


def test_refresh_pr_versions_updates_state(needs_display, fake_home, monkeypatch):
    import time

    import eglantine_launcher.ui.window as winmod
    from eglantine_launcher.config import load

    details = {
        "title": "Fix shaders v2",
        "state": "merged",
        "head_sha": "b" * 40,
        "base_clone_url": "https://github.com/o/r.git",
    }
    monkeypatch.setattr(winmod, "fetch_pr_info", lambda *a, **k: details)
    from gi.repository import GLib

    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a, **k: fn(*a))
    from eglantine_launcher.config import save

    seed = load()
    seed_pr = _pr_cfg()
    seed["projects"].append(seed_pr)
    save(seed)
    window = LauncherWindow(_make_app())
    try:
        window._refresh_pr_versions(dict(seed_pr))
        for _ in range(100):
            if any(p.get("pr_head_sha") == "b" * 40 for p in load()["projects"]):
                break
            time.sleep(0.05)
        assert any(p.get("pr_state") == "merged" for p in window.cfg["projects"] if p.get("is_pr"))
    finally:
        window.close()


def test_already_built_uses_per_project_value(needs_display, fake_home, tmp_path):
    from eglantine_launcher.config import save

    window = LauncherWindow(_make_app())
    try:
        pr = _pr_cfg()
        window.cfg["projects"].append(pr)
        window.cfg["project"] = pr["id"]
        window.cfg["source_dir"] = str(fake_home / "src")
        window.cfg["last_source_commit"] = "stale"
        for p in window.cfg["projects"]:
            if p["id"] == pr["id"]:
                p["last_built_commit"] = "a" * 40
        save(window.cfg)
        binary = fake_home / "src" / pr["id"] / "build" / "release" / "bin"
        binary.mkdir(parents=True)
        (binary / "eglantine").write_bytes(b"x")
        assert window._already_built("a" * 40) is True
        assert window._already_built("b" * 40) is False
    finally:
        window.close()


def test_launch_pr_selects_and_launches(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.config import load, save

    cfg = load()
    pr = _pr_cfg()
    cfg["projects"].append(pr)
    save(cfg)
    window = LauncherWindow(_make_app())
    try:
        launched = []
        monkeypatch.setattr(window, "on_launch", lambda *a: launched.append(True))
        window._launch_pr(pr["id"])
        assert launched == [True]
        assert window.cfg["project"] == pr["id"]
        assert load()["project"] == pr["id"]
    finally:
        window.close()


def test_settings_hides_pr_builds(needs_display, fake_home):
    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    cfg = load()
    cfg["projects"].append(_pr_cfg())
    save(cfg)
    window = SettingsWindow(None)
    try:
        assert len(window._source_rows) == 2
        assert all(not getattr(r, "project", {}).get("is_pr") for r in window._source_rows)
    finally:
        window.close()


def test_pr_builds_dialog_lists_and_reports_state(needs_display, fake_home, monkeypatch):
    import eglantine_launcher.ui.pr_dialog as prmod
    from eglantine_launcher.config import load, save

    cfg = load()
    pr = _pr_cfg()
    pr["pr_state"] = "merged"
    cfg["projects"].append(pr)
    save(cfg)

    def _offline(*a, **k):
        raise Exception("offline")

    monkeypatch.setattr(prmod, "fetch_pr_info", _offline)
    dialog = prmod.PRBuildsDialog(None)
    try:
        assert len(dialog._rows) == 1
        assert "merged" in dialog._rows[0].get_subtitle()
        assert "safe to delete" in dialog._rows[0].get_subtitle()
        assert dialog.empty_label.get_visible() is False
    finally:
        dialog.close()


def test_pr_edit_dialog_defaults_name_from_title(needs_display, fake_home, monkeypatch):
    import eglantine_launcher.ui.pr_dialog as prmod

    details = {
        "title": "Cool fix",
        "state": "open",
        "head_sha": "d" * 40,
        "number": 12,
        "owner": "o",
        "repo": "r",
        "base_clone_url": "https://github.com/o/r.git",
    }
    monkeypatch.setattr(prmod, "fetch_pr_info", lambda *a, **k: details)
    from gi.repository import GLib

    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a, **k: fn(*a))
    saved = {}

    def on_save(project, old_id):
        saved["project"] = project

    dialog = prmod.PREditDialog(None, None, on_save, cfg={})
    try:
        dialog.url_entry.set_text("https://github.com/o/r/pull/12")
        dialog._lookup("https://github.com/o/r/pull/12", dialog._lookup_seq)
        assert dialog.name_entry.get_text() == "Cool fix (#12)"
        assert "open" in dialog.status_label.get_label()
    finally:
        dialog.close()


def test_pr_delete_removes_entry_and_data(needs_display, fake_home, tmp_path, monkeypatch):
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    import eglantine_launcher.ui.pr_dialog as prmod
    from eglantine_launcher.config import load, save

    cfg = load()
    pr = _pr_cfg()
    cfg["projects"].append(pr)
    cfg["project"] = pr["id"]
    cfg["source_dir"] = str(fake_home / "src")
    cfg["install_dir"] = str(fake_home / "install")
    save(cfg)
    src = fake_home / "src" / pr["id"]
    (src / ".git").mkdir(parents=True)

    def _offline(*a, **k):
        raise Exception("offline")

    monkeypatch.setattr(prmod, "fetch_pr_info", _offline)
    dialog = prmod.PRBuildsDialog(None)
    try:
        changed = []
        dialog.on_changed = lambda: changed.append(True)
        dialog._on_delete_clicked(None, dict(pr, id=pr["id"], name=pr["name"]))
        dlg = next(w for w in Gtk.Window.list_toplevels() if isinstance(w, Adw.MessageDialog))
        try:
            dlg.response("delete")
        finally:
            dlg.close()
        assert pr["id"] not in [p["id"] for p in load()["projects"]]
        assert not src.exists()
        assert changed == [True]
    finally:
        dialog.close()
