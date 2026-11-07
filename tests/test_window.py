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


def test_lto_row_greyed_out_with_reason_when_toolchain_cannot_link(needs_display, fake_home, monkeypatch):
    import eglantine_launcher.ui.window as window_mod

    monkeypatch.setattr(window_mod, "describe_lto_support", lambda cfg: (False, "no linker links LTO here"))
    window = LauncherWindow(_make_app())
    try:
        window._lto_probe_seq += 1  # invalidate the startup probe
        window._apply_lto_support(window._lto_probe_seq, False, "no linker links LTO here")
        assert window.lto_row.get_sensitive() is False
        assert window.lto_row.get_active() is False
        assert window.lto_row.get_tooltip_text() == "no linker links LTO here"
        window._apply_lto_support(window._lto_probe_seq, True, "LTO links with mold")
        assert window.lto_row.get_sensitive() is True
        assert window.lto_row.get_tooltip_text() == "LTO links with mold"
    finally:
        window.close()


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


def test_demo_build_log_fills_log(needs_display, fake_home):
    window = LauncherWindow(_make_app(), demo_build_log=True)
    try:
        buf = window.log_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        assert "[1153/1270]" in text
        assert window.copy_btn.get_visible() is True
        assert window.status_label.get_visible() is True
        assert "Building" in window.status_label.get_label()
        assert window.progress.get_visible() is True
        assert window.stop_btn.get_visible() is True
        assert window.log_panel.get_visible() is True
    finally:
        window.close()


def test_log_panel_hidden_until_build_output(needs_display, fake_home, monkeypatch):
    from gi.repository import GLib

    window = LauncherWindow(_make_app())
    try:
        assert window.log_panel.get_visible() is False
        assert window.status_label.get_parent() is not window.log_panel
        monkeypatch.setattr(GLib, "idle_add", lambda fn, *a, **k: fn(*a))
        window._append_log("[1/10] Building...")
        assert window.log_panel.get_visible() is True
        assert window.copy_btn.get_visible() is True
    finally:
        window.close()


def _pill_labels(window):
    labels = []
    child = window.tag_flow.get_first_child()
    while child is not None:
        labels.append(child.get_child().get_label())
        child = child.get_next_sibling()
    return labels


def _pill_classes(window):
    classes = []
    child = window.tag_flow.get_first_child()
    while child is not None:
        classes.append(child.get_child().get_css_classes())
        child = child.get_next_sibling()
    return classes


def test_project_pills_show_games_and_tags(needs_display, fake_home):
    app = _make_app()
    window = LauncherWindow(app)
    try:
        assert window.tag_flow.get_visible()
        assert _pill_labels(window) == [
            "Mario Kart World",
            "Splatoon Raiders",
            "Hyrule Warriors: Age of Imprisonment",
            "Default",
        ]
        for classes in _pill_classes(window):
            assert "eglantine-pill" in classes
            assert any(c.startswith("pill-tone-") for c in classes)
        info = window._current_info()
        info["user_best_for"] = [{"title_id": "040074A01BF12000", "name": "Donkey Kong Bananza"}]
        info["user_tags"] = [{"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"}]
        window._update_project_row_meta()
        assert _pill_labels(window) == [
            "Mario Kart World",
            "Splatoon Raiders",
            "Hyrule Warriors: Age of Imprisonment",
            "Donkey Kong Bananza",
            "Default",
            "fix",
        ]
    finally:
        window.close()


def test_main_window_restores_saved_size(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.config import load

    app = _make_app()
    window = LauncherWindow(app)
    try:
        assert window.get_default_size() == (600, 620)
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
        # PR builds and the data folder moved into Settings; the menu
        # keeps only Settings and About.
        assert not window.has_action("folder")
        assert not window.has_action("pr-builds")
        assert window.has_action("settings")
        assert window.has_action("about")
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
        # default config ships the wichforks defaults; all rows must render
        from eglantine_launcher.config import DEFAULT_PROJECTS

        assert len(window._source_rows) == len(DEFAULT_PROJECTS)
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


def test_settings_sidebar_pages(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        assert [key for key, _label in window._SIDEBAR_PAGES] == [
            "general",
            "github",
            "backups",
            "sources",
            "games",
            "prs",
        ]
        assert window._pages.get_visible_child_name() == "general"
        window.show_page("sources")
        assert window._pages.get_visible_child_name() == "sources"
        # sources page holds the build toggle and the source list
        assert window.source_row is not None
        assert len(window._source_rows) > 0
    finally:
        window.close()


def test_settings_prs_page_lists_builds(needs_display, fake_home):
    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    cfg = load()
    cfg["projects"].append(
        {
            "id": "pr-x-1",
            "name": "PR X",
            "source_url": "https://github.com/o/r.git",
            "branch": "pr-1",
            "is_pr": True,
            "pr_url": "https://github.com/o/r/pull/1",
            "pr_number": 1,
            "pr_state": "open",
            "pr_head_sha": "abc123",
        }
    )
    save(cfg)
    window = SettingsWindow(None)
    try:
        window.show_page("prs")
        assert window._pages.get_visible_child_name() == "prs"
        assert len(window._pr_panel._rows) == 1
    finally:
        window.close()


def test_settings_save_after_panel_change_keeps_pr(needs_display, fake_home):
    """Regression: a Settings save after a panel save must not drop PRs."""
    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    cfg = load()
    pr = _pr_cfg()
    cfg["projects"].append(pr)
    save(cfg)
    window = SettingsWindow(None)
    try:
        # stale in-memory list, as if the panel saved after Settings opened
        window.cfg["projects"] = [p for p in window.cfg["projects"] if not p.get("is_pr")]
        window._on_panel_changed()
        assert any(p.get("id") == pr["id"] for p in window.cfg["projects"])
        # a later Settings save keeps the PR entry on disk
        window._custom_game_id_entry.set_text("0400999900000000")
        window._custom_game_name_entry.set_text("Mine")
        window._on_add_custom_game()
        assert any(p.get("id") == pr["id"] for p in load()["projects"])
    finally:
        window.close()


def test_settings_data_folder_opens_install_dir(needs_display, fake_home, monkeypatch):
    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    cfg = load()
    cfg["install_dir"] = str(fake_home / "install")
    save(cfg)
    window = SettingsWindow(None)
    try:
        window.show_page("general")
        called = []
        monkeypatch.setattr("subprocess.Popen", lambda *a, **k: called.append(a) or None)
        window._on_open_data_folder()
        assert (fake_home / "install").is_dir()
        assert called and "xdg-open" in called[0][0]
    finally:
        window.close()


def test_games_page_lists_included_catalog(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        window.show_page("games")
        shipped = [r.get_title() for r in window._shipped_game_rows]
        assert "Orbitals" in shipped
        assert "Mario Kart World" in shipped
        # custom games move out of the shipped list into Your games
        window._custom_game_id_entry.set_text("0400999900000000")
        window._custom_game_name_entry.set_text("Mine")
        window._on_add_custom_game()
        shipped = [r.get_title() for r in window._shipped_game_rows]
        assert "Mine" not in shipped
        assert any(r.get_title() == "Mine" for r in window._custom_game_rows)
    finally:
        window.close()


def test_settings_games_page_add_remove(needs_display, fake_home):
    from eglantine_launcher.config import load
    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        window.show_page("games")
        assert window._pages.get_visible_child_name() == "games"
        assert "No custom games yet." in window._games_status.get_label()
        window._custom_game_id_entry.set_text("0400999900000000")
        window._custom_game_name_entry.set_text("Mine")
        window._on_add_custom_game()
        assert load()["custom_games"] == [{"title_id": "0400999900000000", "name": "Mine"}]
        assert len(window._custom_game_rows) == 1
        # duplicates and bad IDs are rejected with an error, not saved
        window._custom_game_id_entry.set_text("0400999900000000")
        window._custom_game_name_entry.set_text("Mine")
        window._on_add_custom_game()
        assert "already" in window._games_error.get_label()
        window._custom_game_id_entry.set_text("xyz")
        window._on_add_custom_game()
        assert "16-digit" in window._games_error.get_label()
        assert len(load()["custom_games"]) == 1
        window._on_delete_custom_game(None, "0400999900000000")
        assert load().get("custom_games") == []
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


def test_source_row_has_working_edit_button(needs_display, fake_home, monkeypatch):
    from gi.repository import Gtk

    from eglantine_launcher.ui.settings_window import SettingsWindow

    window = SettingsWindow(None)
    try:
        opened = []
        monkeypatch.setattr(window, "_edit_project", lambda proj: opened.append(proj))
        row = window._source_rows[0]

        def buttons(widget):
            found = []
            child = widget.get_first_child()
            while child is not None:
                if isinstance(child, Gtk.Button):
                    found.append(child)
                found.extend(buttons(child))
                child = child.get_next_sibling()
            return found

        edit_btns = [b for b in buttons(row) if (b.get_tooltip_text() or "").startswith("Edit ")]
        assert len(edit_btns) == 1
        edit_btns[0].emit("clicked")
        assert opened == [row.project]
    finally:
        window.close()


def test_pr_row_click_opens_editor(needs_display, fake_home, monkeypatch):
    import eglantine_launcher.ui.pr_dialog as prmod
    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.pr_dialog import PRBuildsPanel

    cfg = load()
    cfg["projects"].append(_pr_cfg())
    save(cfg)
    panel = PRBuildsPanel()
    assert len(panel._rows) == 1
    row = panel._rows[0]
    assert row.get_activatable() is True
    opened = []

    class FakeDialog:
        def __init__(self, *a, **k):
            opened.append((a, k))

        def present(self):
            pass

    monkeypatch.setattr(prmod, "PREditDialog", FakeDialog)
    row.emit("activated")
    assert len(opened) == 1


def test_source_editor_opens_before_branch_lookup(needs_display, fake_home, monkeypatch):
    """The editor must not wait on the network to open (cf. PR dialog)."""
    import threading

    from gi.repository import Gtk

    import eglantine_launcher.ui.settings_window as swmod
    from eglantine_launcher.config import load

    proj = next(p for p in load()["projects"] if p["id"] == "eglantine")
    window = swmod.SettingsWindow(None)
    try:
        gate = threading.Event()

        def slow(*a, **k):
            gate.set()
            threading.Event().wait(timeout=30)
            return ["nx2", "main"]

        monkeypatch.setattr(swmod, "list_branches", slow)
        before = {id(w) for w in Gtk.Window.list_toplevels() if isinstance(w, swmod.ProjectEditDialog)}
        window._edit_project(proj)
        assert gate.wait(timeout=10), "branch lookup never started"
        dialogs = [
            w
            for w in Gtk.Window.list_toplevels()
            if isinstance(w, swmod.ProjectEditDialog) and id(w) not in before
        ]
        # dialog is already open even though the lookup above is still blocked
        assert len(dialogs) == 1
        for d in dialogs:
            d.close()
    finally:
        gate.set()
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


def test_pr_builds_opens_settings_prs_page(needs_display, fake_home):
    window = LauncherWindow(_make_app())
    try:
        settings = window.on_pr_builds()
        assert settings._pages.get_visible_child_name() == "prs"
        settings.close()
    finally:
        window.close()


def test_pr_added_in_settings_appears_in_main_list(needs_display, fake_home, monkeypatch):
    """End to end: panel Add -> disk -> settings refresh -> main dropdown."""
    import eglantine_launcher.ui.pr_dialog as prmod
    from eglantine_launcher.config import load

    window = LauncherWindow(_make_app())
    try:
        settings = window.on_settings()
        try:
            assert "pr-fixture-o-r-123456" not in window._project_ids
            pr = _pr_cfg()

            def fake_edit_dialog(parent, project, on_save, **kwargs):
                from types import SimpleNamespace

                on_save(dict(pr), pr["id"])
                return SimpleNamespace(present=lambda: None)

            monkeypatch.setattr(prmod, "PREditDialog", fake_edit_dialog)
            settings._pr_panel._add(None)
            assert "pr-fixture-o-r-123456" in window._project_ids
            assert any(p.get("id") == pr["id"] for p in load()["projects"])
            # a later Settings save keeps it on disk too
            settings._custom_game_id_entry.set_text("0400999900000000")
            settings._custom_game_name_entry.set_text("Mine")
            settings._on_add_custom_game()
            assert any(p.get("id") == pr["id"] for p in load()["projects"])
        finally:
            settings.close()
    finally:
        window.close()


def test_project_dropdown_lists_favourites_first_with_divider(needs_display, fake_home):
    from eglantine_launcher.ui import window as winmod

    window = LauncherWindow(_make_app())
    try:
        from eglantine_launcher.config import load, save

        cfg = load()
        cfg["projects"].append(_pr_cfg())
        cfg["projects"][1]["favorite"] = True
        save(cfg)
        window.reload_projects()
        # favourite first, plain PR in normal position, divider between groups
        assert window._project_ids[0] == "pokedinner"
        assert window._project_ids[1] is None
        assert window.project_row.get_model().get_string(0) == "PokeDinner (Pokopia)"
        assert window.project_row.get_model().get_string(1) == winmod.PROJECT_SEPARATOR
        assert window._project_ids.index("pr-fixture-o-r-123456") > 1
        eglantine_idx = window._project_ids.index("eglantine")
        window.project_row.set_selected(1)
        assert window.project_row.get_selected() == eglantine_idx
        assert window._current_info().get("id") == "eglantine"
    finally:
        window.close()


def test_project_dropdown_is_searchable(needs_display, fake_home):
    window = LauncherWindow(_make_app())
    try:
        if hasattr(window.project_row, "get_enable_search"):
            assert window.project_row.get_enable_search()
    finally:
        window.close()


def test_project_dropdown_has_no_divider_without_prs(needs_display, fake_home):
    window = LauncherWindow(_make_app())
    try:
        assert None not in window._project_ids
        assert window._project_ids[0] == "eglantine"
    finally:
        window.close()


def test_poll_external_changes_reloads_projects(needs_display, fake_home):
    from eglantine_launcher.config import load, save

    window = LauncherWindow(_make_app())
    try:
        assert "pr-fixture-o-r-123456" not in window._project_ids
        cfg = load()
        cfg["projects"].append(_pr_cfg())
        save(cfg)
        assert window._poll_external_changes() is True
        assert "pr-fixture-o-r-123456" in window._project_ids
        assert window._poll_external_changes() is True
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


def test_selecting_pr_in_dropdown_sets_source_mode(needs_display, fake_home):
    from eglantine_launcher.config import load, save

    cfg = load()
    pr = _pr_cfg()
    cfg["projects"].append(pr)
    save(cfg)
    window = LauncherWindow(_make_app())
    try:
        window.project_row.set_selected(window._project_ids.index(pr["id"]))
        assert window.cfg["project"] == pr["id"]
        assert window.cfg["mode"] == "source"
        assert load()["project"] == pr["id"]
        assert window._current_info().get("id") == pr["id"]
    finally:
        window.close()


def test_pr_row_has_globe_link_not_launch(needs_display, fake_home):
    from gi.repository import Gtk

    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.pr_dialog import PRBuildsPanel

    cfg = load()
    cfg["projects"].append(_pr_cfg())
    save(cfg)
    panel = PRBuildsPanel()
    row = panel._rows[0]

    def labels(widget):
        found = []
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                found.append(child)
            found.extend(labels(child))
            child = child.get_next_sibling()
        return found

    btns = labels(row)
    assert not [b for b in btns if b.get_label() == "Launch"]
    links = [b for b in btns if (b.get_tooltip_text() or "").startswith("Open https://")]
    assert len(links) == 1


def test_settings_hides_pr_builds(needs_display, fake_home):
    from eglantine_launcher.config import load, save
    from eglantine_launcher.ui.settings_window import SettingsWindow

    cfg = load()
    cfg["projects"].append(_pr_cfg())
    save(cfg)
    window = SettingsWindow(None)
    try:
        from eglantine_launcher.config import DEFAULT_PROJECTS

        assert len(window._source_rows) == len(DEFAULT_PROJECTS)
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
        dialog.panel.on_changed = lambda: changed.append(True)
        dialog.panel._on_delete_clicked(None, dict(pr, id=pr["id"], name=pr["name"]))
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


def test_favourite_sorts_with_prs_above_divider(needs_display, fake_home):
    from eglantine_launcher.config import load, save

    cfg = load()
    cfg["projects"][1]["favorite"] = True
    save(cfg)
    window = LauncherWindow(_make_app())
    try:
        from eglantine_launcher.ui import window as winmod

        assert window._project_ids[0] == "pokedinner"
        assert window._project_ids[1] is None
        assert window.project_row.get_model().get_string(0) == "PokeDinner (Pokopia)"
        assert window.project_row.get_model().get_string(1) == winmod.PROJECT_SEPARATOR
        # star follows the selection: eglantine selected, not a favourite
        assert window._star_label.get_label() == "★"
        assert "fav-active" not in window._star_label.get_css_classes()
        window.project_row.set_selected(0)
        assert "fav-active" in window._star_label.get_css_classes()
        assert window._current_info().get("id") == "pokedinner"
    finally:
        window.close()


def test_star_toggle_favourites_and_persists(needs_display, fake_home):
    from eglantine_launcher.config import load

    window = LauncherWindow(_make_app())
    try:
        assert window._star_label.get_label() == "★"
        assert "fav-active" not in window._star_label.get_css_classes()
        window._on_star_clicked()
        assert window.cfg["project"] is not None
        info = window._current_info()
        assert info.get("favorite") is True
        assert "fav-active" in window._star_label.get_css_classes()
        assert load()["projects"][0].get("favorite") is True
        window._on_star_clicked()
        assert window._current_info().get("favorite", False) is False
    finally:
        window.close()
