import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import __version__
from ..config import effective_best_for, effective_tags, project_tooltip
from ..config import load as load_config
from ..config import save as save_config

# Non-selectable divider below favourited projects in the Project dropdown.
# Favourites sort first so one-off builds never hide below the fold behind
# a dozen forks once the user stars them.
PROJECT_SEPARATOR = "────────────"

_PILL_CSS = b"""
.eglantine-pill {
    border-radius: 9999px;
    padding: 1px 10px;
    font-size: smaller;
}
.pill-tone-0 { background-color: alpha(@accent_bg_color, 0.22); }
.pill-tone-1 { background-color: alpha(@success_bg_color, 0.25); }
.pill-tone-2 { background-color: alpha(@warning_bg_color, 0.30); }
.pill-tone-3 { background-color: alpha(@error_bg_color, 0.20); }
.pill-tone-4 { background-color: alpha(@accent_bg_color, 0.12); }
.fav-active { color: @accent_color; }
/* Pills are display-only: kill the FlowBoxChild hover/focus highlight so
   they never look interactive. */
.eglantine-pills > flowboxchild { background: transparent; }
.eglantine-pills > flowboxchild:hover { background: transparent; }
.eglantine-pills > flowboxchild:focus { outline: none; }
.eglantine-pills > flowboxchild:selected { background: transparent; }
"""
_PILL_TONES = 5
_pill_css_loaded = False


def _ensure_pill_css():
    """Register pill badge styling once (needs a display; safe to call blindly)."""
    global _pill_css_loaded
    if _pill_css_loaded:
        return
    try:
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(_PILL_CSS)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _pill_css_loaded = True
    except Exception:
        pass


from ..tasks import (
    LAUNCHER_API_LATEST,
    REPOS_PAGE,
    UPDATE_COMMANDS,
    BuildCancelled,
    _in_flatpak,
    api_get_json,
    build_source,
    build_windows_updater,
    check_build_deps,
    check_release,
    detect_install_method,
    download_url,
    fetch_pr_info,
    fetch_remote_games,
    fetch_remote_sources,
    find_release_for_commit,
    get_project_last_built,
    install_shortcut,
    is_pr_project,
    is_windows,
    launch,
    launcher_update_asset,
    list_commits,
    list_releases,
    migrate_emulator_dirs,
    open_url,
    pr_version_entries,
    refresh_pr_project,
    release_appimage_path,
    release_platforms,
    remove_shortcut,
    self_update_dialog_text,
    shortcut_state,
    source_dir,
    source_version_entries,
    stage_appimage_swap,
    windows_install_dir,
)
from ..tray import hide_to_tray, minimize_to_taskbar
from ..tray import restore as restore_window
from .deps_dialog import DepsDialog
from .settings_window import SettingsWindow


def version_tick_markup(text):
    """Green markup for the installed-build row; None renders plain (white tick).

    White tick = downloaded/built before. Green tick + "(installed build)"
    suffix = what is on disk right now.
    """
    if text.startswith("✓ ") and (text.endswith("(installed build)") or text.endswith("(installed)")):
        return '<span color="#4ade80">✓</span> ' + GLib.markup_escape_text(text[2:])
    return None


class LauncherWindow(Adw.ApplicationWindow):
    def __init__(self, app, auto=False):
        super().__init__(application=app, title="Eglantine Launcher")
        self.cfg = load_config()
        # One-time cutover (then a no-op): legacy/fork emulator dir names
        # become links to the canonical one so saves stay global.
        migrate_emulator_dirs(self.cfg)
        try:
            win_w = int(self.cfg.get("window_width") or 640)
            win_h = self.cfg.get("window_height")
            win_h = int(win_h) if win_h else 600
        except (TypeError, ValueError):
            win_w, win_h = 640, 600
        self.set_default_size(win_w, win_h)
        self.auto = auto
        self._busy = False
        self._release_tags = []
        self._commit_shas = []
        self._latest_commit = None
        self._version_subs = []
        self._launcher_update_url = None
        self._launcher_update_tag = None
        self._launcher_update_assets = []
        self._updating = False
        self._force_quit = False
        self._hidden_for_launch = False
        self._launch_hide = "none"
        self._tray_icon = None
        self._cancel_event = threading.Event()
        self._build_proc = None
        self._build_proc_lock = threading.Lock()
        self.connect("close-request", self._on_close_request)
        self.connect("destroy", self._on_destroy)

        toolbar = Adw.HeaderBar()
        self._menu = Gio.Menu()
        self._menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self._menu_button.set_menu_model(self._menu)
        toolbar.pack_end(self._menu_button)
        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", self.on_settings)
        self.add_action(settings_action)
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.on_about)
        self.add_action(about_action)
        self._build_menu()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        group = Adw.PreferencesGroup()

        self.project_row = Adw.ComboRow(title="Project")
        if hasattr(self.project_row, "set_enable_search"):
            self.project_row.set_enable_search(True)
        # The search entry needs an expression to obtain strings from items;
        # without it the entry shows but never filters.
        self.project_row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        if hasattr(self.project_row, "set_search_match_mode"):
            self.project_row.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        # Popup rows show ★ for favourites and a dimmed divider; the closed
        # row keeps the plain model string.
        self.project_row.set_list_factory(self._make_project_list_factory())
        self.star_btn = Gtk.Button()
        self.star_btn.add_css_class("flat")
        self.star_btn.set_valign(Gtk.Align.CENTER)
        # Fixed box plus a single ★ glyph whose color alone changes state:
        # swapping icon images shifts the row, recoloring cannot.
        self.star_btn.set_size_request(32, 32)
        self._star_label = Gtk.Label(label="★")
        self._star_label.set_halign(Gtk.Align.CENTER)
        self._star_label.set_valign(Gtk.Align.CENTER)
        self.star_btn.set_child(self._star_label)
        self.star_btn.connect("clicked", self._on_star_clicked)
        self.project_row.add_suffix(self.star_btn)
        self._project_ids = []
        self._last_project_idx = 0
        self._set_project_model()
        self._last_project_idx = self.project_row.get_selected()
        self.tag_flow = Gtk.FlowBox()
        self.tag_flow.add_css_class("eglantine-pills")
        self.tag_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.tag_flow.set_max_children_per_line(10)
        self.tag_flow.set_column_spacing(6)
        self.tag_flow.set_row_spacing(6)
        self.tag_flow.set_margin_start(12)
        self.tag_flow.set_margin_end(12)
        self.tag_flow.set_margin_top(10)
        self.tag_flow.set_margin_bottom(6)
        self._update_project_row_meta()
        group.add(self.project_row)
        group.add(self.tag_flow)

        self.mode_row = Adw.ComboRow(title="Mode")
        mode_model = Gtk.StringList.new(["Release (download)", "Build from source"])
        self.mode_row.set_model(mode_model)
        self.mode_row.set_selected(0 if self.cfg.get("mode") == "release" else 1)
        group.add(self.mode_row)

        self.version_row = Adw.ActionRow(title="Version")
        self.version_drop = Gtk.DropDown()
        self.version_drop.set_model(Gtk.StringList.new(["Latest"]))
        self.version_drop.set_factory(self._make_version_factory(short=True))
        self.version_drop.set_list_factory(self._make_version_factory(short=False))
        self.version_drop.set_halign(Gtk.Align.END)
        self.version_drop.set_valign(Gtk.Align.CENTER)
        self.version_row.add_suffix(self.version_drop)
        group.add(self.version_row)
        self.version_drop.connect("notify::selected", self._on_version_changed)

        self.pgo_row = Adw.SwitchRow(title="PGO build (default)")
        self.pgo_row.set_subtitle("Profile-guided build: faster, but takes longer to compile")
        self.pgo_row.set_active(bool(self.cfg.get("pgo", True)))
        group.add(self.pgo_row)

        self.lto_row = Adw.SwitchRow(title="LTO build (default)")
        self.lto_row.set_subtitle("Link-time optimization: faster binary, slower build")
        self.lto_row.set_active(bool(self.cfg.get("lto", True)))
        group.add(self.lto_row)

        self.cpu_row = Adw.ComboRow(title="CPU target")
        self.cpu_row.set_subtitle("Instruction set for source builds (per-CPU build dir)")
        self._cpu_values = ["auto", "native", "4", "3", "2", "1"]
        self.cpu_row.set_model(Gtk.StringList.new(["Auto", "Native", "v4", "v3", "v2", "v1"]))
        try:
            self.cpu_row.set_selected(self._cpu_values.index(str(self.cfg.get("cpu_level", "auto"))))
        except ValueError:
            self.cpu_row.set_selected(0)
        group.add(self.cpu_row)

        box.append(group)

        self.project_row.connect("notify::selected", self._on_selector_changed)
        self.mode_row.connect("notify::selected", self._on_selector_changed)
        GLib.idle_add(self._refresh_versions)
        GLib.idle_add(self._start_self_update_check)
        GLib.timeout_add_seconds(1, self._delayed_startup_check)
        if not auto:
            GLib.timeout_add_seconds(3600, self._hourly_tick)
            GLib.timeout_add_seconds(60, self._initial_update_check)
            GLib.timeout_add_seconds(60, self._poll_external_changes)

        self.banner = Adw.Banner.new("")
        self.banner.set_revealed(False)
        self.banner.connect("button-clicked", self._on_banner_button)
        self._banner_action = None

        self.toast_overlay = Adw.ToastOverlay()

        self.progress = Gtk.ProgressBar()
        self.progress.set_hexpand(True)
        self.progress.set_valign(Gtk.Align.CENTER)
        self.progress.set_visible(False)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_scrolled = Gtk.ScrolledWindow()
        self.log_scrolled.set_child(self.log_view)
        self.log_scrolled.set_vexpand(True)
        self.log_scrolled.set_min_content_height(120)
        self.log_scrolled.set_visible(False)
        box.append(self.log_scrolled)

        self.copy_btn = Gtk.Button(label="Copy log")
        self.copy_btn.connect("clicked", self.on_copy_log)
        self.copy_btn.set_visible(False)
        self.copy_btn.set_halign(Gtk.Align.END)
        box.append(self.copy_btn)

        self.launch_btn = Gtk.Button(label="Launch")
        self.launch_btn.add_css_class("suggested-action")
        self.launch_btn.connect("clicked", self.on_launch)

        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.connect("clicked", self.on_stop_clicked)
        self.stop_btn.set_visible(False)

        self.shortcut_btn = Gtk.Button(label="Shortcuts...")
        self.shortcut_btn.connect("clicked", self.on_shortcuts_clicked)

        self.action_bar = Gtk.ActionBar()
        self.action_bar.pack_start(self.shortcut_btn)
        self.action_bar.pack_start(self.progress)
        # End group reads left-to-right: Stop then Launch keeps
        # Launch rightmost as the primary action.
        self.action_bar.pack_end(self.stop_btn)
        self.action_bar.pack_end(self.launch_btn)

        # persist pgo/lto/cpu choices immediately (project/mode persist via _on_selector_changed)
        self.pgo_row.connect("notify::active", lambda *_: self._save_choices())
        self.lto_row.connect("notify::active", lambda *_: self._save_choices())
        self.cpu_row.connect("notify::selected", lambda *_: self._save_choices())

        # Banner sits edge-to-edge above the margined content; toasts
        # overlay everything transiently; Launch lives in a proper
        # bottom ActionBar instead of a hand-rolled button box.
        # Content stays top-aligned: the pill cloud below Project varies in
        # height per project, and centering would shift the whole card up
        # and down on every selection change.
        box.set_vexpand(True)
        box.set_valign(Gtk.Align.START)
        content_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_wrap.append(self.banner)
        content_wrap.append(box)
        self.toast_overlay.set_child(content_wrap)
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(toolbar)
        toolbar_view.set_content(self.toast_overlay)
        toolbar_view.add_bottom_bar(self.action_bar)
        toolbar_view.set_bottom_bar_style(Adw.ToolbarStyle.RAISED)
        self.set_content(toolbar_view)

        if auto:
            GLib.idle_add(self._auto_start)

    def _initial_update_check(self):
        threading.Thread(target=self._hourly_check, daemon=True).start()
        return False

    def _delayed_startup_check(self):
        threading.Thread(target=self._sync_sources, daemon=True).start()
        return False

    def _sync_sources(self):
        from ..config import merge_remote_games, merge_remote_sources

        try:
            if merge_remote_sources(self.cfg, fetch_remote_sources(self.cfg)):
                GLib.idle_add(self.reload_projects)
        except Exception:
            pass
        try:
            merge_remote_games(self.cfg, fetch_remote_games(self.cfg))
        except Exception:
            pass

    def _on_banner_button(self, *_args):
        action, self._banner_action = self._banner_action, None
        GLib.idle_add(self.banner.set_revealed, False)
        if callable(action):
            action()

    def _reveal_banner(self, title, button_label="", action=None):
        self.banner.set_title(title)
        self.banner.set_button_label(button_label or "")
        self._banner_action = action
        self.banner.set_revealed(True)

    def _show_banner(self, title, button_label="", action=None):
        self._status_seq = getattr(self, "_status_seq", 0) + 1
        GLib.idle_add(self._reveal_banner, title, button_label, action)

    def _set_status(self, text, sticky=False):
        self._status_seq = getattr(self, "_status_seq", 0) + 1
        seq = self._status_seq
        if sticky:
            if text:
                GLib.idle_add(self._reveal_banner, text)
            else:
                GLib.idle_add(self._hide_banner_if_seq, seq)
        elif text:

            def _toast():
                toast = Adw.Toast.new(text)
                toast.set_timeout(4)
                self.toast_overlay.add_toast(toast)

            GLib.idle_add(_toast)
            GLib.timeout_add_seconds(4, lambda: self._clear_status(seq))

    def _dismiss_toasts(self):
        # Successful launch leaves no stale toasts behind (e.g. "Fetching
        # source updates..." / "Source already up to date." from the
        # already-built fast path). Bumping the seq also invalidates pending
        # _clear_status timeouts so they can't hide a later banner.
        self._status_seq = getattr(self, "_status_seq", 0) + 1
        self.toast_overlay.dismiss_all()
        return False

    def _hide_banner_if_seq(self, seq):
        if getattr(self, "_status_seq", 0) == seq:
            self.banner.set_revealed(False)
        return False

    def _clear_status(self, seq):
        return self._hide_banner_if_seq(seq)

    def _set_progress(self, frac):
        GLib.idle_add(self.progress.set_fraction, frac)

    def on_copy_log(self, *_args):
        buf = self.log_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        display = self.get_display()
        if display:
            display.get_clipboard().set(text)

    def _build_menu(self):
        menu = Gio.Menu()
        menu.append("Settings", "win.settings")
        menu.append("About", "win.about")
        self._menu = menu
        self._menu_button.set_menu_model(menu)

    def _set_build_proc(self, proc):
        with self._build_proc_lock:
            self._build_proc = proc

    def _hide_stop(self):
        GLib.idle_add(self.stop_btn.set_visible, False)

    def on_stop_clicked(self, *_args):
        if not self._busy:
            return
        dialog = Adw.MessageDialog.new(
            self,
            "Stop build?",
            "Abort the current source build? Partial build files are kept, "
            "so you can resume by launching again.",
        )
        dialog.add_response("keep", "Keep building")
        dialog.add_response("stop", "Stop build")
        dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("keep")
        dialog.set_close_response("keep")
        dialog.connect("response", self._on_stop_response)
        dialog.present()

    def _on_stop_response(self, dialog, resp):
        dialog.close()
        if resp == "stop":
            self._confirm_stop()

    def _confirm_stop(self):
        self._cancel_event.set()
        self._set_status("Cancelling...", sticky=True)
        GLib.idle_add(self.stop_btn.set_sensitive, False)
        with self._build_proc_lock:
            proc = self._build_proc
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass

    def _append_log(self, line):
        def _do():
            self.log_scrolled.set_visible(True)
            self.copy_btn.set_visible(True)
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), line + "\n")
            # scroll to end
            mark = buf.get_insert()
            self.log_view.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)

        GLib.idle_add(_do)

    def _set_busy(self, busy):
        self._busy = busy
        GLib.idle_add(self.launch_btn.set_sensitive, not busy)
        if not busy:
            GLib.idle_add(self.progress.set_visible, False)

    @staticmethod
    def _make_version_factory(short):
        factory = Gtk.SignalListItemFactory()

        def on_setup(_f, obj):
            label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            obj.set_child(label)

        def on_bind(_f, obj):
            text = obj.get_item().get_string()
            label = obj.get_child()
            if short:
                if len(text) > 76:
                    text = text[:76] + "..."
                label.set_text(text)
                return
            label.set_tooltip_text(text)
            markup = version_tick_markup(text)
            if markup is not None:
                label.set_markup(markup)
            else:
                label.set_text(text)

        factory.connect("setup", on_setup)
        factory.connect("bind", on_bind)
        return factory

    def _set_version_model(
        self,
        labels,
        tags,
        shas,
        subs=None,
        project_id=None,
        is_release=None,
        seen_shas=None,
        latest_commit=None,
    ):
        self._updating = True
        try:
            model = Gtk.StringList.new(labels)
            self.version_drop.set_model(model)
            self.version_drop.set_selected(0)
            self.version_row.set_subtitle("")
            self._release_tags = tags or []
            self._commit_shas = shas or []
            self._version_subs = subs or []
            if bool(is_release):
                self._latest_commit = None
            elif latest_commit is not None:
                self._latest_commit = latest_commit
            elif shas:
                self._latest_commit = shas[0]
            else:
                self._latest_commit = None
            if project_id:
                seen = seen_shas if seen_shas is not None else (shas or [])
                self._record_seen(project_id, bool(is_release), tags or [], seen)
        finally:
            self._updating = False
        return False

    def _record_seen(self, project_id, is_release, tags, shas):
        seen = self.cfg.setdefault("seen", {})
        entry = seen.setdefault(project_id, {})
        changed = False
        if is_release and tags and entry.get("release") != tags[0]:
            entry["release"] = tags[0]
            changed = True
        if not is_release and shas and entry.get("commit") != shas[0]:
            entry["commit"] = shas[0]
            changed = True
        if changed:
            save_config(self.cfg)

    def _hourly_tick(self):
        if self.auto:
            return False
        threading.Thread(target=self._hourly_check, daemon=True).start()
        return True

    def _hourly_check(self):
        try:
            info = self._current_info()
            pid = info.get("id")
            if not pid:
                return
            is_release = self.mode_row.get_selected() == 0
            seen = self.cfg.get("seen", {}).get(pid, {})
            if is_release:
                rels = list_releases(self.cfg)
                tags = [r.get("tag_name") for r in rels if r.get("tag_name")]
                if tags and seen.get("release") and tags[0] != seen["release"]:
                    self._notify_update(f"New {info.get('name', pid)} release: {tags[0]}")
            else:
                commits = list_commits(self.cfg)
                shas = [c.get("sha") or c.get("id") for c in commits if isinstance(c, dict)]
                shas = [s for s in shas if s]
                if shas and seen.get("commit") and shas[0] != seen["commit"]:
                    self._notify_update(f"New {info.get('name', pid)} commits: {shas[0][:7]}")
            GLib.idle_add(self._refresh_versions)
        except Exception:
            pass

    def _notify_update(self, body):
        def _do():
            note = Gio.Notification.new("Eglantine Launcher")
            note.set_body(body)
            self.get_application().send_notification("update-available", note)
            return False

        GLib.idle_add(_do)

    def _on_version_changed(self, *_args):
        if self._updating:
            return
        idx = self.version_drop.get_selected()
        if 0 < idx <= len(self._version_subs):
            self.version_row.set_subtitle(self._version_subs[idx - 1])
        else:
            self.version_row.set_subtitle("")

    def _make_project_list_factory(self):
        factory = Gtk.SignalListItemFactory()

        def on_setup(_f, obj):
            obj.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))

        def on_bind(_f, obj):
            label = obj.get_child()
            text = obj.get_item().get_string()
            if text == PROJECT_SEPARATOR:
                label.set_text(text)
                label.set_xalign(0.5)
                label.add_css_class("dim-label")
                return
            fav_names = {
                p.get("name", p.get("id")) for p in self.cfg.get("projects", []) if p.get("favorite")
            }
            label.set_text(f"★ {text}" if text in fav_names else text)
            label.set_xalign(0)
            label.remove_css_class("dim-label")

        factory.connect("setup", on_setup)
        factory.connect("bind", on_bind)
        return factory

    def _set_project_model(self):
        """Rebuild the Project dropdown: favourites first, then a divider."""
        favs = [p for p in self.cfg.get("projects", []) if p.get("favorite")]
        rest = [p for p in self.cfg.get("projects", []) if not p.get("favorite")]
        labels, ids = [], []
        for p in favs:
            labels.append(p.get("name", p.get("id")))
            ids.append(p.get("id"))
        if favs and rest:
            labels.append(PROJECT_SEPARATOR)
            ids.append(None)
        for p in rest:
            labels.append(p.get("name", p.get("id")))
            ids.append(p.get("id"))
        self._project_ids = ids
        self.project_row.set_model(Gtk.StringList.new(labels))
        cur = self.cfg.get("project")
        if cur and cur in ids:
            self.project_row.set_selected(ids.index(cur))
        else:
            self.project_row.set_selected(0 if not favs else ids.index(favs[0].get("id")))
        self._last_project_idx = self.project_row.get_selected()

    def _poll_external_changes(self):
        """Pick up projects added or removed by another process/instance."""
        if self.auto:
            return False
        try:
            disk = load_config()
            mine = [
                (p.get("id"), p.get("name"), bool(p.get("favorite"))) for p in self.cfg.get("projects", [])
            ]
            theirs = [(p.get("id"), p.get("name"), bool(p.get("favorite"))) for p in disk.get("projects", [])]
            if mine != theirs:
                self.reload_projects()
        except Exception:
            pass
        return True

    def _current_info(self):
        ids = self._project_ids
        idx = self.project_row.get_selected()
        pid = ids[idx] if 0 <= idx < len(ids) else self.cfg.get("project")
        if pid is None:
            return {}
        return next((pr for pr in self.cfg.get("projects", []) if pr.get("id") == pid), {})

    def _release_available(self, info):
        plats = release_platforms(info)
        return plats["windows"] if is_windows() else plats["linux"]

    @staticmethod
    def _make_pill(label, tooltip_text):
        """Small non-clickable badge; color is stable per label, not random."""
        _ensure_pill_css()
        pill = Gtk.Label(label=label)
        pill.add_css_class("eglantine-pill")
        pill.add_css_class(f"pill-tone-{sum(ord(c) for c in label) % _PILL_TONES}")
        if tooltip_text:
            pill.set_tooltip_text(tooltip_text)
        return pill

    @staticmethod
    def _set_row_tooltip(row, text):
        """Set the tooltip on a row and all its children.

        Adw rows pack labels, boxes and buttons that swallow hover on their
        own area, so setting it on the row alone only pops up over parts.
        Walking the subtree makes hovering anywhere on the line show it.
        """

        def _apply(widget):
            try:
                widget.set_tooltip_text(text)
            except Exception:
                pass
            child = widget.get_first_child()
            while child is not None:
                _apply(child)
                child = child.get_next_sibling()

        _apply(row)

    def _update_star(self, info=None):
        """Reflect the selected project's favourite state on the star."""
        info = info if info is not None else self._current_info()
        fav = bool(info.get("favorite"))
        if fav:
            self._star_label.add_css_class("fav-active")
        else:
            self._star_label.remove_css_class("fav-active")
        self.star_btn.set_tooltip_text(
            f"Unfavourite {info.get('name', '')}" if fav else f"Favourite {info.get('name', '')}"
        )
        self.star_btn.set_tooltip_text(
            f"Unfavourite {info.get('name', '')}" if fav else f"Favourite {info.get('name', '')}"
        )

    def _on_star_clicked(self, *_args):
        info = self._current_info()
        if not info or not info.get("id"):
            return
        info["favorite"] = not info.get("favorite")
        save_config(self.cfg)
        self._updating = True
        try:
            self._set_project_model()
        finally:
            self._updating = False
        self._save_choices()
        self._update_project_row_meta()
        self._refresh_versions()

    def _update_project_row_meta(self):
        info = self._current_info()
        # Games only here; tags live as pills below with their own hovers.
        tip = project_tooltip(info, include_tags=False)
        try:
            self._set_row_tooltip(self.project_row, tip)
        except Exception:
            pass
        self._update_star(info)
        flow = getattr(self, "tag_flow", None)
        if flow is None:
            return
        child = flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            flow.remove(child)
            child = nxt
        pills = []
        for item in effective_best_for(info):
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("title_id", "")
            if not name:
                continue
            note = (item.get("note") or "").strip()
            pills.append(self._make_pill(name, f"{name} - {note}" if note else name))
        for tag in effective_tags(info):
            if not isinstance(tag, dict):
                continue
            label = tag.get("label") or tag.get("id", "")
            if not label:
                continue
            tag_tip = (tag.get("tooltip") or "").strip()
            pills.append(self._make_pill(label, f"{label} - {tag_tip}" if tag_tip else label))
        for pill in pills:
            flow.append(pill)
        flow.set_visible(bool(pills))

    def _on_selector_changed(self, *_args):
        if self._updating:
            return
        idx = self.project_row.get_selected()
        ids = self._project_ids
        if 0 <= idx < len(ids) and ids[idx] is None:
            # divider row picked: bounce straight back
            self._updating = True
            try:
                self.project_row.set_selected(self._last_project_idx)
            finally:
                self._updating = False
            return
        self._last_project_idx = idx
        self._save_choices()
        try:
            self._update_project_row_meta()
        except Exception:
            pass
        self._refresh_versions()

    def _refresh_versions(self):
        if self._updating:
            return
        cfg = self.cfg
        info = self._current_info()
        cfg["project"] = info.get("id", cfg.get("project"))
        self._update_mode_options(info)
        if is_pr_project(info):
            self._refresh_pr_versions(dict(info))
            return
        is_release = (self.mode_row.get_selected() == 0) and self._release_available(info)
        if is_release:

            def worker():
                try:
                    rels = list_releases(cfg)
                    have = set(cfg.get("downloaded_releases", []))
                    tags = [r.get("tag_name") for r in rels if r.get("tag_name")]
                    installed_tag = cfg.get("last_release_tag")
                    try:
                        installed_here = bool(installed_tag) and release_appimage_path(cfg).exists()
                    except Exception:
                        installed_here = False

                    def _tag_label(t):
                        if t == installed_tag and installed_here:
                            return f"✓ {t} (installed)"
                        return ("✓ " if t in have else "") + t

                    labels = ["Latest" + (" ✓" if tags and tags[0] in have else "")]
                    labels += [_tag_label(t) for t in tags]
                    subs = [
                        f"{r.get('name') or ''}\n{(r.get('body') or '').splitlines()[0] if r.get('body') else ''}".strip()
                        for r in rels
                        if r.get("tag_name")
                    ]
                    GLib.idle_add(self._set_version_model, labels, tags, [], subs, cfg.get("project"), True)
                except Exception:
                    GLib.idle_add(self._set_version_model, ["Latest"], [], [])

            threading.Thread(target=worker, daemon=True).start()
        else:

            def worker():
                try:
                    commits = list_commits(cfg, 50)
                    have = set(cfg.get("built_commits", []))
                    installed = cfg.get("last_source_commit")
                    binary = source_dir(cfg) / "build" / "release" / "bin"
                    binary = binary / ("eglantine.exe" if is_windows() else "eglantine")
                    labels, shas, subs, latest = source_version_entries(
                        commits, have, installed, binary.exists()
                    )
                    latest_tick = " ✓" if latest in have else ""
                    labels.insert(0, "Latest" + latest_tick)
                    GLib.idle_add(
                        self._set_version_model,
                        labels,
                        [],
                        shas,
                        subs,
                        cfg.get("project"),
                        False,
                        [latest] if latest else [],
                        latest,
                    )
                except Exception:
                    GLib.idle_add(self._set_version_model, ["Latest"], [], [])

            threading.Thread(target=worker, daemon=True).start()

    def _refresh_pr_versions(self, info):
        pid = info.get("id")
        cached_sha = info.get("pr_head_sha") or ""
        cached_title = info.get("pr_title") or ""
        cached_state = info.get("pr_state") or "open"
        if cached_sha:
            have = set(self.cfg.get("built_commits", []))
            installed = get_project_last_built(self.cfg, info)
            try:
                binary = source_dir(self.cfg) / "build" / "release" / "bin"
                binary = binary / ("eglantine.exe" if is_windows() else "eglantine")
                present = binary.exists()
            except Exception:
                present = False
            labels, shas, subs, latest = pr_version_entries(
                cached_title, cached_sha, cached_state, have, installed, present
            )
            labels.insert(0, "Latest" + (" ✓" if latest in have else ""))
            self._set_version_model(labels, [], shas, subs, pid, False, [latest], latest)

        def worker():
            try:
                details = fetch_pr_info(info.get("pr_url", ""), self.cfg)
            except Exception:
                if not cached_sha:
                    GLib.idle_add(self._set_version_model, ["Latest"], [], [])
                return
            for p in self.cfg.get("projects", []):
                if p.get("id") == pid:
                    refresh_pr_project(p, details)
                    break
            save_config(self.cfg)
            try:
                binary = source_dir(self.cfg) / "build" / "release" / "bin"
                binary = binary / ("eglantine.exe" if is_windows() else "eglantine")
                present = binary.exists()
            except Exception:
                present = False
            have = set(self.cfg.get("built_commits", []))
            installed = get_project_last_built(self.cfg, info)
            labels, shas, subs, latest = pr_version_entries(
                details.get("title", ""),
                details.get("head_sha", ""),
                details.get("state", ""),
                have,
                installed,
                present,
            )
            if not shas:
                GLib.idle_add(self._set_version_model, ["Latest"], [], [])
                return
            latest_tick = " ✓" if latest in have else ""
            labels.insert(0, "Latest" + latest_tick)
            GLib.idle_add(
                self._set_version_model,
                labels,
                [],
                shas,
                subs,
                pid,
                False,
                [latest] if latest else [],
                latest,
            )

        threading.Thread(target=worker, daemon=True).start()

    def _update_mode_options(self, info):
        self._updating = True
        try:
            if is_pr_project(info):
                self.mode_row.set_model(Gtk.StringList.new(["Build from source"]))
                self.mode_row.set_selected(0)
                self.mode_row.set_sensitive(bool(self.cfg.get("source_enabled", True)))
                return
            if not self.cfg.get("source_enabled", True):
                self.mode_row.set_model(Gtk.StringList.new(["Release (download)"]))
                self.mode_row.set_selected(0)
                self.mode_row.set_sensitive(self._release_available(info))
                return
            if self._release_available(info):
                current = self.mode_row.get_model()
                count = current.get_n_items() if current else 0
                if count != 2:
                    self.mode_row.set_model(Gtk.StringList.new(["Release (download)", "Build from source"]))
                    self.mode_row.set_selected(0 if self.cfg.get("mode", "release") == "release" else 1)
                self.mode_row.set_sensitive(True)
            else:
                self.mode_row.set_model(Gtk.StringList.new(["Build from source"]))
                self.mode_row.set_selected(0)
        finally:
            self._updating = False

    def _mode_from_row(self):
        """Explicit user choice, derived from the visible row (single-item models constrain it)."""
        model = self.mode_row.get_model()
        if model is None or model.get_n_items() == 0:
            return self.cfg.get("mode", "release")
        label = model.get_string(self.mode_row.get_selected()) or ""
        return "release" if label.startswith("Release") else "source"

    def on_launch(self, *_args):
        if self._busy:
            return
        if self.cfg.get("minimize_on_launch") is None and not self.auto:
            self._set_busy(True)
            self._ask_launch_behavior()
            return
        self._apply_launch_behavior()
        self._begin_launch()

    def _ask_launch_behavior(self):
        dialog = Adw.MessageDialog.new(
            self,
            "Minimize launcher?",
            "Keep it out of the way while the emulator runs.",
        )
        dialog.add_response("taskbar", "Minimize to taskbar")
        dialog.add_response("tray", "Hide to tray")
        dialog.add_response("none", "Keep visible")
        dialog.set_default_response("taskbar")
        dialog.set_close_response("dismissed")
        dialog.connect("response", self._on_launch_choice)
        dialog.present()

    def _on_launch_choice(self, dialog, resp):
        dialog.close()
        if resp in ("taskbar", "tray", "none"):
            self.cfg["minimize_on_launch"] = resp
            save_config(self.cfg)
            self._apply_launch_behavior()
        else:
            # dismissed: minimize once without saving, but still launch
            self._hidden_for_launch = True
            self._launch_hide = "taskbar"
        self._begin_launch()

    def _apply_launch_behavior(self):
        """Record the hide choice; the worker applies it just before launch.

        Hiding here would bury long downloads/builds with their progress
        invisible, so the window stays up until the binary is ready.
        """
        behavior = self.cfg.get("minimize_on_launch")
        if behavior == "tray":
            self._hidden_for_launch = True
            self._launch_hide = "tray"
        elif behavior == "none":
            self._hidden_for_launch = False
            self._launch_hide = "none"
        else:
            # "taskbar", or unset in --auto mode (minimize silently, don't prompt)
            self._hidden_for_launch = True
            self._launch_hide = "taskbar"

    def _begin_launch(self):
        info = self._current_info()
        self.cfg["project"] = info.get("id", self.cfg.get("project"))
        self.cfg["mode"] = self._mode_from_row()
        self.cfg["pgo"] = bool(self.pgo_row.get_active())
        self.cfg["lto"] = bool(self.lto_row.get_active())
        try:
            self.cfg["cpu_level"] = self._cpu_values[int(self.cpu_row.get_selected())]
        except Exception:
            self.cfg["cpu_level"] = "auto"
        save_config(self.cfg)
        self._launch_mode = self.cfg["mode"]
        self._start_worker()

    def _auto_start(self):
        self.on_launch()
        return False

    def _save_window_size(self):
        # Wayland disallows positioning, but size is fine to remember.
        try:
            w, h = self.get_width(), self.get_height()
        except Exception:
            return
        if w > 0 and h > 0:
            self.cfg["window_width"] = w
            self.cfg["window_height"] = h
            save_config(self.cfg)

    def _on_close_request(self, *_args):
        if not self.auto:
            self._save_window_size()
        if self.auto or getattr(self, "_force_quit", False):
            return False
        behavior = self.cfg.get("close_behavior")
        if behavior == "tray":
            self._hide_to_tray()
            return True
        if behavior == "quit":
            return False
        self._ask_close_behavior()
        return True

    def _ask_close_behavior(self):
        dialog = Adw.MessageDialog.new(
            self,
            "Quit launcher?",
            "You can hide it to the tray instead of quitting.",
        )
        dialog.add_response("quit", "Quit")
        dialog.add_response("tray", "Hide to tray")
        dialog.set_default_response("quit")
        dialog.set_close_response("dismissed")
        dialog.connect("response", self._on_close_choice)
        dialog.present()

    def _on_close_choice(self, dialog, resp):
        dialog.close()
        if resp == "tray":
            self.cfg["close_behavior"] = "tray"
            save_config(self.cfg)
            self._hide_to_tray()
        elif resp == "quit":
            self.cfg["close_behavior"] = "quit"
            save_config(self.cfg)
            self._force_quit = True
            self.close()
        # dismissed: stay open, ask again next time

    def _hide_to_tray(self):
        hide_to_tray(self)
        try:
            from ..statusicon import show_tray_icon

            self._drop_tray_icon()
            self._tray_icon = show_tray_icon(
                on_show=self.restore_from_tray,
                on_quit=self._quit_from_tray,
            )
        except Exception:
            self._tray_icon = None
        if self.cfg.get("tray_notice_shown"):
            return
        self.cfg["tray_notice_shown"] = True
        save_config(self.cfg)
        try:
            app = self.get_application()
            if app is not None:
                note = Gio.Notification.new("Eglantine Launcher")
                note.set_body("Running in the background.")
                note.add_button("Show", "app.show-window")
                app.send_notification("tray-hidden", note)
        except Exception:
            pass

    def _drop_tray_icon(self):
        tray, self._tray_icon = self._tray_icon, None
        if tray is not None:
            try:
                from ..statusicon import hide_tray_icon

                hide_tray_icon(tray)
            except Exception:
                pass

    def _quit_from_tray(self):
        self._drop_tray_icon()
        self._force_quit = True
        self.close()

    def _on_destroy(self, *_args):
        if not self.auto:
            self._save_window_size()
        self._drop_tray_icon()
        self._hidden_for_launch = False

    def restore_from_tray(self):
        self._hidden_for_launch = False
        self._drop_tray_icon()
        try:
            app = self.get_application()
            if app is not None:
                app.withdraw_notification("tray-hidden")
        except Exception:
            pass
        restore_window(self)

    def _start_worker(self):
        self._set_busy(True)
        self._cancel_event.clear()
        self._set_build_proc(None)
        self.progress.set_fraction(0.0)
        GLib.idle_add(self.progress.set_visible, True)
        self._last_fraction_time = 0.0
        # clear log
        self.log_view.get_buffer().set_text("")
        GLib.idle_add(self.log_scrolled.set_visible, False)
        GLib.idle_add(self.copy_btn.set_visible, False)
        is_source = getattr(self, "_launch_mode", self.cfg.get("mode", "release")) != "release"
        GLib.idle_add(self.stop_btn.set_visible, is_source)
        GLib.idle_add(self.stop_btn.set_sensitive, True)
        GLib.timeout_add(100, self._pulse_tick)
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def _pulse_tick(self):
        if not self._busy:
            return False
        import time

        if time.monotonic() - self._last_fraction_time > 0.5:
            self.progress.pulse()
        return True

    def _note_fraction(self, downloaded, total):
        import time

        self._last_fraction_time = time.monotonic()
        self._set_progress((downloaded / total) if total else 0.0)

    def _note_git_fraction(self, frac):
        """Determinate progress from git transfer output (0-1 float).

        Runs on the worker thread; also stamps the pulse timer so the bar
        holds the real fraction instead of pulsing during clone/fetch.
        """
        import time

        self._last_fraction_time = time.monotonic()
        try:
            frac = float(frac)
        except (TypeError, ValueError):
            return
        self._set_progress(min(max(frac, 0.0), 1.0))

    def _already_built(self, commit):
        src = source_dir(self.cfg)
        binary = src / "build" / "release" / "bin" / ("eglantine.exe" if is_windows() else "eglantine")
        return get_project_last_built(self.cfg) == commit and binary.exists()

    def _ask_download_or_build(self, tag, sha):
        import threading

        done = threading.Event()
        choice = {"v": "build"}

        def _show():
            dialog = Adw.MessageDialog.new(
                self,
                "Already released",
                f"Commit {sha[:7]} is already published in release {tag}.\n\n"
                "Download it in a minute or two, or build from source anyway?",
            )
            dialog.add_response("build", "Build from source")
            dialog.add_response("download", "Download instead")
            dialog.set_default_response("download")
            dialog.set_close_response("build")

            def _on_response(_d, resp):
                choice["v"] = resp
                done.set()
                _d.close()

            dialog.connect("response", _on_response)
            dialog.present()
            return False

        GLib.idle_add(_show)
        done.wait(timeout=300)
        return choice["v"] == "download"

    def _launch_now(self, path):
        """Hide per the launch choice, then launch. Runs on the worker thread."""
        self._hide_stop()
        GLib.idle_add(self._dismiss_toasts)
        hide = getattr(self, "_launch_hide", "none")
        if hide == "tray":
            GLib.idle_add(self._hide_to_tray)
        elif hide == "taskbar":
            GLib.idle_add(minimize_to_taskbar, self)
        return self._launched(launch(path, self.cfg))

    def _launched(self, proc):
        """Wait for the emulator to exit, then restore the window if we hid it.

        Returns True when something was launched. Runs on the worker thread.
        Skipped for --auto (the app quits) and flatpak (the Popen handle is
        the flatpak-spawn helper, which exits as soon as the host starts).
        """
        if proc is None or self.auto or _in_flatpak():
            return False
        # The build/download is done and the emulator is running: stop the
        # progress pulse now. proc.wait() blocks until the emulator exits,
        # so leaving _busy set keeps the bar moving behind the hidden window.
        self._set_busy(False)
        GLib.idle_add(self.progress.set_fraction, 0.0)
        if not self._hidden_for_launch:
            return True
        try:
            proc.wait()
        except Exception:
            return True
        GLib.idle_add(self.restore_from_tray)
        return True

    def _worker(self):
        launched = False
        try:
            mode = getattr(self, "_launch_mode", self.cfg.get("mode", "release"))
            info = next(
                (pr for pr in self.cfg.get("projects", []) if pr.get("id") == self.cfg.get("project")), {}
            )
            if mode == "release":
                if not self._release_available(info):
                    raise RuntimeError(
                        "No release download for this platform right now "
                        "(offline or API limit); try again or switch to Build from source."
                    )
                idx = self.version_drop.get_selected()
                tag = self._release_tags[idx - 1] if idx > 0 and idx - 1 < len(self._release_tags) else None
                appimage = check_release(
                    self.cfg,
                    progress_cb=self._note_fraction,
                    status_cb=self._set_status,
                    tag=tag,
                )
                launched = self._launch_now(appimage)
            else:
                if not self.cfg.get("source_enabled", True):
                    self._set_status("Source builds are hidden (Settings).")
                    return
                missing = [(n, d) for n, ok, d, req in check_build_deps() if req and not ok]
                if missing:
                    text = "\n".join(f"- {n}: {d}" for n, d in missing)
                    GLib.idle_add(self._show_deps_dialog, self, text)
                    self._set_status(
                        f"Missing build dependencies: {', '.join(n for n, _d in missing)}", sticky=True
                    )
                    return
                idx = self.version_drop.get_selected()
                commit = self._commit_shas[idx - 1] if idx > 0 and idx - 1 < len(self._commit_shas) else None
                latest = getattr(self, "_latest_commit", None)
                resolved = commit or latest or (self._commit_shas[0] if self._commit_shas else None)
                if resolved and self._already_built(resolved):
                    binary = build_source(
                        self.cfg,
                        status_cb=self._set_status,
                        log_cb=self._append_log,
                        commit=resolved,
                        cancelled=self._cancel_event.is_set,
                        on_proc=self._set_build_proc,
                        progress_cb=self._note_git_fraction,
                    )
                    launched = self._launch_now(binary)
                    return
                tag = None
                if not is_pr_project(info):
                    tag = find_release_for_commit(self.cfg, resolved) if resolved else None
                if tag:
                    if self.auto or self._ask_download_or_build(tag, resolved):
                        appimage = check_release(
                            self.cfg,
                            progress_cb=self._note_fraction,
                            status_cb=self._set_status,
                            tag=tag,
                        )
                        launched = self._launch_now(appimage)
                        return
                binary = build_source(
                    self.cfg,
                    status_cb=self._set_status,
                    log_cb=self._append_log,
                    commit=resolved,
                    cancelled=self._cancel_event.is_set,
                    on_proc=self._set_build_proc,
                    progress_cb=self._note_git_fraction,
                )
                launched = self._launch_now(binary)
        except BuildCancelled:
            self._append_log("Build cancelled.")
            self._set_status("Build cancelled.", sticky=True)
        except Exception as e:
            self._set_status(f"Error: {e}", sticky=True)
        finally:
            self._set_build_proc(None)
            self._hide_stop()
            self._set_busy(False)
            GLib.idle_add(self.progress.set_fraction, 0.0)
            if not self.auto and launched:
                GLib.idle_add(self._refresh_versions)
            if self.auto:

                def _quit():
                    self.get_application().quit()
                    return False

                GLib.timeout_add_seconds(2, _quit)

    def on_shortcuts_clicked(self, *_args):
        try:
            info = self._current_info()
            pid = info.get("id", self.cfg.get("project"))
            if not pid:
                self._set_status("No project selected.", sticky=True)
                return
            state = shortcut_state(self.cfg, pid)
        except Exception as e:
            self._set_status(f"Shortcut error: {e}", sticky=True)
            return
        name = info.get("name", pid)
        dialog = Adw.MessageDialog.new(
            self,
            f"Shortcuts - {name}",
            "Choose where to install a launcher for this project. "
            "Each target is independent, so different forks can coexist.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("apply")
        desktop_row = Adw.SwitchRow(title="Desktop", subtitle="~/Desktop")
        desktop_row.set_active(bool(state.get("desktop")))
        menu_row = Adw.SwitchRow(title="Start Menu", subtitle="Application menu")
        menu_row.set_active(bool(state.get("applications")))
        if is_windows():
            # .desktop files do nothing on Windows; Steam is the shortcut there.
            desktop_row.set_sensitive(False)
            desktop_row.set_subtitle("Linux only")
            menu_row.set_sensitive(False)
            menu_row.set_subtitle("Linux only")
        steam_row = Adw.SwitchRow(title="Steam", subtitle="Non-Steam shortcut (restart Steam to see changes)")
        steam_row.set_active(bool(state.get("steam")))
        group = Adw.PreferencesGroup()
        group.add(desktop_row)
        group.add(menu_row)
        group.add(steam_row)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(group)
        dialog.set_extra_child(box)
        dialog.connect("response", self._on_shortcuts_response, pid, desktop_row, menu_row, steam_row)
        dialog.present()

    def _on_shortcuts_response(self, dialog, response, pid, desktop_row, menu_row, steam_row, *_args):
        if response != "apply":
            return
        desired = {
            "desktop": bool(desktop_row.get_active()),
            "applications": bool(menu_row.get_active()),
            "steam": bool(steam_row.get_active()),
        }
        try:
            current = shortcut_state(self.cfg, pid)
        except Exception as e:
            self._set_status(f"Shortcut error: {e}", sticky=True)
            return
        app_root = Path(__file__).resolve().parents[2]
        errors = []
        for target, want in desired.items():
            have = bool(current.get(target))
            if want == have:
                continue
            try:
                if want:
                    install_shortcut(self.cfg, pid, app_root, targets=(target,))
                else:
                    remove_shortcut(self.cfg, pid, targets=(target,))
            except Exception as e:
                errors.append(f"{target}: {e}")
        if errors:
            self._set_status(f"Shortcut error: {'; '.join(errors)}", sticky=True)
            return
        changed = [t for t in desired if desired[t] != current.get(t)]
        if not changed:
            self._set_status("No shortcut changes.")
        else:
            note = " (restart Steam to see changes)" if "steam" in changed else ""
            self._set_status(f"Shortcuts updated: {', '.join(sorted(changed))}.{note}")

    def _show_deps_dialog(self, parent, text):
        DepsDialog(parent, text, on_ignore=self._limit_to_downloads).present()
        return False

    def _limit_to_downloads(self):
        self.cfg["source_enabled"] = False
        self.cfg["mode"] = "release"
        save_config(self.cfg)
        self._set_status("Source builds hidden. Re-enable in Settings.")
        self._refresh_versions()

    def _start_self_update_check(self):
        def worker():
            try:
                from .. import __version__

                data = api_get_json(LAUNCHER_API_LATEST, cfg=None, timeout=20)
                tag = data.get("tag_name", "").lstrip("v")
                if tag and tag != __version__ and tag > __version__:
                    self._launcher_update_url = data.get("html_url")
                    self._launcher_update_tag = tag
                    self._launcher_update_assets = data.get("assets", []) or []
                    method = detect_install_method()
                    GLib.idle_add(self._show_self_update_banner, tag, method)
            except Exception:
                pass

        import threading

        threading.Thread(target=worker, daemon=True).start()

    def _show_self_update_banner(self, tag, method):
        if method in ("appimage", "windows"):
            self._show_banner(
                f"Launcher update available: {tag}",
                button_label="Update now",
                action=self.start_one_click_update,
            )
        else:
            self._show_banner(
                f"Launcher update available: {tag}",
                button_label="How to update",
                action=lambda: self.show_update_instructions(method),
            )
        return False

    def show_update_instructions(self, method=None):
        method = method or detect_install_method()
        tag = self._launcher_update_tag or "new version"
        title, body = self_update_dialog_text(method, tag)
        dialog = Adw.MessageDialog.new(self, title, body)
        if method in ("dnf", "apt", "flatpak"):
            dialog.add_response("copy", "Copy command")
            dialog.set_default_response("copy")
        else:
            if self._launcher_update_url:
                dialog.add_response("open-release", "Open release page")
            dialog.add_response("open-repos", "Open setup guide")
            dialog.set_default_response("open-repos")
        dialog.add_response("close", "Close")
        dialog.set_close_response("close")
        dialog.connect("response", lambda d, resp: self._on_update_dialog_response(d, resp, method))
        dialog.present()

    def _on_update_dialog_response(self, dialog, resp, method):
        if resp == "copy" and method in UPDATE_COMMANDS:
            try:
                self.get_display().get_clipboard().set(UPDATE_COMMANDS[method])
                self._set_status("Update command copied.")
            except Exception:
                pass
        elif resp == "open-release" and self._launcher_update_url:
            open_url(self._launcher_update_url)
        elif resp == "open-repos":
            open_url(REPOS_PAGE)
        try:
            dialog.close()
        except Exception:
            pass

    def on_self_update(self, *_args):
        if self._launcher_update_url:
            open_url(self._launcher_update_url)

    def start_one_click_update(self, *_args):
        method = detect_install_method()
        if method == "appimage":
            self._run_appimage_update()
        elif method == "windows":
            self._run_windows_update()
        else:
            self.show_update_instructions(method)

    def _run_appimage_update(self):
        import os

        current = os.environ.get("APPIMAGE")
        asset = launcher_update_asset(self._launcher_update_assets, "appimage")
        if not current or not asset or not asset.get("browser_download_url"):
            self.show_update_instructions("source")
            return
        self._set_status("Downloading launcher update...")
        GLib.idle_add(self.progress.set_visible, True)

        def worker():
            try:
                from pathlib import Path

                target = Path(current)
                staged = target.parent / (target.name + ".new")
                url = asset["browser_download_url"]
                download_url(url, staged, progress_cb=lambda f: GLib.idle_add(self._set_progress, f))
                GLib.idle_add(self.progress.set_visible, False)
                stage_appimage_swap(str(target), staged)
                self._set_status("Update installed - restarting...")
                GLib.idle_add(self._restart_into, str(target))
            except Exception as e:
                GLib.idle_add(self.progress.set_visible, False)
                GLib.idle_add(self._show_update_error, f"Automatic update failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _restart_into(self, path):
        import os
        import sys

        try:
            os.execv(path, [path, *sys.argv[1:]])
        except Exception as e:
            self._show_update_error(f"Update installed but restart failed: {e}\nReopen {path} manually.")
        return False

    def _run_windows_update(self):
        asset = launcher_update_asset(self._launcher_update_assets, "windows")
        if not asset or not asset.get("browser_download_url"):
            self.show_update_instructions("source")
            return
        self._set_status("Downloading launcher update...")
        GLib.idle_add(self.progress.set_visible, True)

        def worker():
            try:
                import tempfile
                import zipfile
                from pathlib import Path

                tmp = Path(tempfile.mkdtemp(prefix="eglantine-launcher-update-"))
                zpath = tmp / "update.zip"
                download_url(
                    asset["browser_download_url"],
                    zpath,
                    progress_cb=lambda f: GLib.idle_add(self._set_progress, f),
                )
                staged = tmp / "new"
                with zipfile.ZipFile(zpath) as z:
                    z.extractall(staged)
                # The zip wraps the COLLECT dir; descend if needed.
                candidates = list(staged.glob("*/eglantine-launcher.exe"))
                src = candidates[0].parent if candidates else staged
                install_dir = windows_install_dir()
                script = tmp / "updater.cmd"
                script.write_text(build_windows_updater(str(install_dir), str(src)), encoding="utf-8")
                GLib.idle_add(self.progress.set_visible, False)
                self._set_status("Update downloaded - restarting...")
                GLib.idle_add(self._spawn_windows_updater, str(script))
            except Exception as e:
                GLib.idle_add(self.progress.set_visible, False)
                GLib.idle_add(self._show_update_error, f"Automatic update failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _spawn_windows_updater(self, script):
        import os
        import subprocess

        try:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(
                ["cmd", "/c", script, str(os.getpid())],
                creationflags=flags,
                close_fds=True,
            )
            self._force_quit = True
            self.close()
        except Exception as e:
            self._show_update_error(f"Automatic update failed: {e}")
        return False

    def _show_update_error(self, message):
        dialog = Adw.MessageDialog.new(self, "Update failed", message)
        if self._launcher_update_url:
            dialog.add_response("open-release", "Open release page")
        dialog.add_response("close", "Close")
        dialog.set_close_response("close")

        def _resp(d, resp):
            if resp == "open-release" and self._launcher_update_url:
                open_url(self._launcher_update_url)
            try:
                d.close()
            except Exception:
                pass

        dialog.connect("response", _resp)
        dialog.present()
        return False

    def _save_choices(self):
        info = self._current_info()
        self.cfg["project"] = info.get("id", self.cfg.get("project"))
        if is_pr_project(info):
            self.cfg["mode"] = "source"
        elif not self.cfg.get("source_enabled", True):
            self.cfg["mode"] = "release"
        else:
            # persist only the explicit choice; never flip it due to a failed release check
            self.cfg["mode"] = self._mode_from_row()
        self.cfg["pgo"] = bool(self.pgo_row.get_active())
        self.cfg["lto"] = bool(self.lto_row.get_active())
        try:
            self.cfg["cpu_level"] = self._cpu_values[int(self.cpu_row.get_selected())]
        except Exception:
            self.cfg["cpu_level"] = "auto"
        save_config(self.cfg)

    def on_settings(self, *_args):
        win = SettingsWindow(self, on_changed=self.reload_projects)
        win.present()
        return win

    def on_pr_builds(self, *_args):
        win = self.on_settings()
        if win is not None:
            win.show_page("prs")
        return win

    def reload_projects(self):
        # Settings saves a new config to disk; re-read it so toggles apply instantly
        self.cfg = load_config()
        self._updating = True
        try:
            self._set_project_model()
            if self.cfg.get("project") not in (self._project_ids or [None]):
                first = next((i for i in self._project_ids if i is not None), None)
                if first is not None:
                    self.cfg["project"] = first
                    self.project_row.set_selected(self._project_ids.index(first))
            try:
                self._update_project_row_meta()
            except Exception:
                pass
        finally:
            self._updating = False
        self._refresh_versions()

    def on_about(self, *_args):
        dialog = Adw.AboutDialog(
            application_name="Eglantine Launcher",
            application_icon="eglantine-launcher",
            version=__version__,
            comments="Simple launcher for Eglantine and forks. Checks for updates and builds from source. "
            "Game picks come from the wichforks guide.",
            website="https://github.com/SavageCore/eglantine-launcher",
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 SavageCore",
            developers=["SavageCore"],
        )
        dialog.add_credit_section(
            "Fork guide",
            ["wichforks by riyadhHDA https://github.com/riyadhHDA/wichforks"],
        )
        dialog.add_credit_section(
            "Title IDs",
            ["Switchbrew https://switchbrew.org/wiki/Switch_2:_Title_list/Games"],
        )
        # Fixed size for every page: with follows-content-size the Credits
        # page would shrink to its narrow rows and jump around on navigation.
        dialog.set_content_width(420)
        dialog.set_content_height(560)
        dialog.present(self)
