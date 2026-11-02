import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import __version__
from ..config import load as load_config
from ..config import save as save_config
from ..tasks import (
    _in_flatpak,
    _on_host,
    build_source,
    check_build_deps,
    check_release,
    fetch_remote_sources,
    find_release_for_commit,
    install_shortcut,
    is_windows,
    launch,
    list_commits,
    list_releases,
    release_appimage_path,
    release_platforms,
    remove_shortcut,
    shortcut_name,
    shortcut_path,
    source_dir,
    source_version_entries,
)
from ..tasks import arch as get_arch
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
        self.set_default_size(640, 600)
        self.cfg = load_config()
        self.auto = auto
        self._busy = False
        self._release_tags = []
        self._commit_shas = []
        self._latest_commit = None
        self._version_subs = []
        self._launcher_update_url = None
        self._updating = False
        self._force_quit = False
        self._hidden_for_launch = False
        self._launch_hide = "none"
        self._tray_icon = None
        self.connect("close-request", self._on_close_request)
        self.connect("destroy", self._on_destroy)

        toolbar = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("Settings", "win.settings")
        menu.append("About", "win.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(menu)
        toolbar.pack_end(menu_button)
        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", self.on_settings)
        self.add_action(settings_action)
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.on_about)
        self.add_action(about_action)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        group = Adw.PreferencesGroup(title="Settings")

        self.project_row = Adw.ComboRow(title="Project")
        self._project_ids = [p.get("id") for p in self.cfg.get("projects", [])]
        project_model = Gtk.StringList.new([p.get("name", p.get("id")) for p in self.cfg.get("projects", [])])
        self.project_row.set_model(project_model)
        cur = self.cfg.get("project")
        if cur in self._project_ids:
            self.project_row.set_selected(self._project_ids.index(cur))
        else:
            self.project_row.set_selected(0)
        group.add(self.project_row)

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

        arch_row = Adw.ActionRow(title="Architecture")
        arch_row.set_subtitle(get_arch())
        group.add(arch_row)

        box.append(group)

        self.project_row.connect("notify::selected", self._on_selector_changed)
        self.mode_row.connect("notify::selected", self._on_selector_changed)
        GLib.idle_add(self._refresh_versions)
        GLib.idle_add(self._refresh_shortcut_btn)
        GLib.idle_add(self._start_self_update_check)
        GLib.timeout_add_seconds(1, self._delayed_startup_check)
        if not auto:
            GLib.timeout_add_seconds(3600, self._hourly_tick)
            GLib.timeout_add_seconds(60, self._initial_update_check)

        self.status_label = Gtk.Label(label="")
        self.status_label.set_wrap(True)
        self.status_label.set_xalign(0)
        self.status_label.set_selectable(True)
        self.status_label.add_css_class("dim-label")

        self.progress = Gtk.ProgressBar()
        box.append(self.progress)

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

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.launch_btn = Gtk.Button(label="Launch")
        self.launch_btn.add_css_class("suggested-action")
        self.launch_btn.connect("clicked", self.on_launch)
        btn_box.append(self.launch_btn)

        self.shortcut_btn = Gtk.Button(label="Install Shortcut")
        self.shortcut_btn.connect("clicked", self.on_install_shortcut)
        btn_box.append(self.shortcut_btn)

        self.folder_btn = Gtk.Button(label="Open Data Folder")
        self.folder_btn.connect("clicked", self.on_open_folder)
        btn_box.append(self.folder_btn)

        self.self_update_btn = Gtk.Button(label="Update Launcher")
        self.self_update_btn.connect("clicked", self.on_self_update)
        self.self_update_btn.set_visible(False)
        btn_box.append(self.self_update_btn)

        box.append(btn_box)

        # persist pgo choice immediately (project/mode persist via _on_selector_changed)
        self.pgo_row.connect("notify::active", lambda *_: self._save_choices())

        # add header bar via toolbar view
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_bar.set_margin_top(4)
        status_bar.set_margin_bottom(4)
        status_bar.set_margin_start(12)
        status_bar.set_margin_end(12)
        status_bar.append(self.status_label)
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(toolbar)
        toolbar_view.set_content(box)
        toolbar_view.add_bottom_bar(status_bar)
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
        from ..config import merge_remote_sources

        try:
            if merge_remote_sources(self.cfg, fetch_remote_sources(self.cfg)):
                GLib.idle_add(self.reload_projects)
        except Exception:
            pass

    def _set_status(self, text, sticky=False):
        self._status_seq = getattr(self, "_status_seq", 0) + 1
        seq = self._status_seq
        GLib.idle_add(self.status_label.set_label, text)
        if not sticky and text:
            GLib.timeout_add_seconds(8, lambda: self._clear_status(seq))

    def _clear_status(self, seq):
        if getattr(self, "_status_seq", 0) == seq:
            self.status_label.set_label("")
        return False

    def _set_progress(self, frac):
        GLib.idle_add(self.progress.set_fraction, frac)

    def on_copy_log(self, *_args):
        buf = self.log_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        display = self.get_display()
        if display:
            display.get_clipboard().set(text)

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

    def _current_info(self):
        ids = [pr.get("id") for pr in self.cfg.get("projects", [])]
        idx = self.project_row.get_selected()
        pid = ids[idx] if 0 <= idx < len(ids) else self.cfg.get("project")
        return next((pr for pr in self.cfg.get("projects", []) if pr.get("id") == pid), {})

    def _release_available(self, info):
        plats = release_platforms(info)
        return plats["windows"] if is_windows() else plats["linux"]

    def _on_selector_changed(self, *_args):
        if self._updating:
            return
        self._save_choices()
        self._refresh_shortcut_btn()
        self._refresh_versions()

    def _refresh_versions(self):
        if self._updating:
            return
        cfg = self.cfg
        info = self._current_info()
        cfg["project"] = info.get("id", cfg.get("project"))
        self._update_mode_options(info)
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

    def _update_mode_options(self, info):
        self._updating = True
        try:
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
        save_config(self.cfg)
        self._launch_mode = self.cfg["mode"]
        self._start_worker()

    def _auto_start(self):
        self.on_launch()
        return False

    def _on_close_request(self, *_args):
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
        self.progress.set_fraction(0.0)
        self._last_fraction_time = 0.0
        # clear log
        self.log_view.get_buffer().set_text("")
        GLib.idle_add(self.log_scrolled.set_visible, False)
        GLib.idle_add(self.copy_btn.set_visible, False)
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

    def _already_built(self, commit):
        src = source_dir(self.cfg)
        binary = src / "build" / "release" / "bin" / ("eglantine.exe" if is_windows() else "eglantine")
        return self.cfg.get("last_source_commit") == commit and binary.exists()

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
        hide = getattr(self, "_launch_hide", "none")
        if hide == "tray":
            GLib.idle_add(self._hide_to_tray)
        elif hide == "taskbar":
            GLib.idle_add(minimize_to_taskbar, self)
        return self._launched(launch(path))

    def _launched(self, proc):
        """Wait for the emulator to exit, then restore the window if we hid it.

        Returns True when something was launched. Runs on the worker thread.
        Skipped for --auto (the app quits) and flatpak (the Popen handle is
        the flatpak-spawn helper, which exits as soon as the host starts).
        """
        if proc is None or self.auto or _in_flatpak():
            return False
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
                    )
                    launched = self._launch_now(binary)
                    return
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
                )
                launched = self._launch_now(binary)
        except Exception as e:
            self._set_status(f"Error: {e}", sticky=True)
        finally:
            self._set_busy(False)
            GLib.idle_add(self.progress.set_fraction, 0.0)
            if not self.auto and launched:
                GLib.idle_add(self._refresh_versions)
            if self.auto:

                def _quit():
                    self.get_application().quit()
                    return False

                GLib.timeout_add_seconds(2, _quit)

    def _refresh_shortcut_btn(self):
        info = self._current_info()
        pid = info.get("id", self.cfg.get("project"))
        self.shortcut_btn.set_label(
            "Remove Shortcut" if shortcut_path(self.cfg, pid).exists() else "Install Shortcut"
        )

    def on_install_shortcut(self, *_args):
        try:
            info = self._current_info()
            pid = info.get("id", self.cfg.get("project"))
            if shortcut_path(self.cfg, pid).exists():
                remove_shortcut(self.cfg, pid)
                self._set_status("Shortcut removed.")
            else:
                app_root = Path(__file__).resolve().parents[2]
                install_shortcut(self.cfg, pid, app_root)
                self._set_status(f"Shortcut installed: {shortcut_name(info)}.")
            self._refresh_shortcut_btn()
        except Exception as e:
            self._set_status(f"Shortcut error: {e}", sticky=True)

    def on_open_folder(self, *_args):
        folder = Path(self.cfg["install_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(_on_host(["xdg-open", str(folder)]))

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
                import json
                import urllib.request

                from .. import __version__

                url = "https://api.github.com/repos/SavageCore/eglantine-launcher/releases/latest"
                req = urllib.request.Request(url, headers={"User-Agent": "eglantine-launcher"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.loads(r.read())
                tag = data.get("tag_name", "").lstrip("v")
                if tag and tag != __version__ and tag > __version__:
                    html_url = data.get("html_url")
                    self._launcher_update_url = html_url
                    GLib.idle_add(self.self_update_btn.set_visible, True)
                    GLib.idle_add(self.status_label.set_label, f"Launcher update available: {tag}")
            except Exception:
                pass

        import threading

        threading.Thread(target=worker, daemon=True).start()

    def on_self_update(self, *_args):
        if self._launcher_update_url:
            import subprocess

            subprocess.Popen(_on_host(["xdg-open", self._launcher_update_url]))

    def _save_choices(self):
        info = self._current_info()
        self.cfg["project"] = info.get("id", self.cfg.get("project"))
        if not self.cfg.get("source_enabled", True):
            self.cfg["mode"] = "release"
        else:
            # persist only the explicit choice; never flip it due to a failed release check
            self.cfg["mode"] = self._mode_from_row()
        self.cfg["pgo"] = bool(self.pgo_row.get_active())
        save_config(self.cfg)

    def on_settings(self, *_args):
        SettingsWindow(self, on_changed=self.reload_projects).present()

    def reload_projects(self):
        # Settings saves a new config to disk; re-read it so toggles apply instantly
        self.cfg = load_config()
        self._updating = True
        try:
            model = Gtk.StringList.new([p.get("name", p.get("id")) for p in self.cfg.get("projects", [])])
            self.project_row.set_model(model)
            ids = [p.get("id") for p in self.cfg.get("projects", [])]
            cur = self.cfg.get("project")
            if cur in ids:
                self.project_row.set_selected(ids.index(cur))
            else:
                self.project_row.set_selected(0)
                if ids:
                    self.cfg["project"] = ids[0]
        finally:
            self._updating = False
        self._refresh_shortcut_btn()
        self._refresh_versions()

    def on_about(self, *_args):
        dialog = Adw.AboutDialog(
            application_name="Eglantine Launcher",
            application_icon="eglantine-launcher",
            version=__version__,
            comments="Simple launcher for Eglantine/PokeDinner. Checks for updates and builds from source.",
            website="https://github.com/SavageCore/eglantine-launcher",
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 SavageCore",
            developers=["SavageCore"],
        )
        dialog.present(self)
