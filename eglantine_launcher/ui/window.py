import subprocess
import sys
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
    _on_host,
    build_source,
    check_build_deps,
    check_release,
    fetch_remote_sources,
    is_windows,
    launch,
    list_commits,
    list_releases,
    release_platforms,
)
from ..tasks import arch as get_arch
from .deps_dialog import DepsDialog
from .settings_window import SettingsWindow


class LauncherWindow(Adw.ApplicationWindow):
    def __init__(self, app, auto=False):
        super().__init__(application=app, title="Eglantine Launcher")
        self.set_default_size(640, 600)
        self.cfg = load_config()
        self.auto = auto
        self._busy = False
        self._release_tags = []
        self._commit_shas = []
        self._version_subs = []
        self._launcher_update_url = None
        self._updating = False

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
        GLib.idle_add(self._start_self_update_check)
        GLib.timeout_add_seconds(1, self._delayed_startup_check)
        if not auto:
            GLib.timeout_add_seconds(3600, self._hourly_tick)
            GLib.timeout_add_seconds(60, self._initial_update_check)

        self.status_label = Gtk.Label(label="Ready.")
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

    def _set_status(self, text):
        GLib.idle_add(self.status_label.set_label, text)

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
            if text.startswith("✓ "):
                label.set_markup('<span color="#4ade80">✓</span> ' + GLib.markup_escape_text(text[2:]))
            elif text.endswith(" ✓"):
                label.set_markup(GLib.markup_escape_text(text[:-2]) + ' <span color="#4ade80">✓</span>')
            else:
                label.set_text(text)

        factory.connect("setup", on_setup)
        factory.connect("bind", on_bind)
        return factory

    def _set_version_model(self, labels, tags, shas, subs=None, project_id=None, is_release=None):
        self._updating = True
        try:
            model = Gtk.StringList.new(labels)
            self.version_drop.set_model(model)
            self.version_drop.set_selected(0)
            self.version_row.set_subtitle("")
            self._release_tags = tags or []
            self._commit_shas = shas or []
            self._version_subs = subs or []
            if project_id:
                self._record_seen(project_id, bool(is_release), tags or [], shas or [])
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
                    labels = ["Latest" + (" ✓" if tags and tags[0] in have else "")]
                    labels += [("✓ " if t in have else "") + t for t in tags]
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
                    commits = list_commits(cfg)
                    have = set(cfg.get("built_commits", []))
                    shas = []
                    labels = []
                    subs = []
                    for c in commits:
                        sha = c.get("sha") if isinstance(c, dict) else None
                        if not sha and isinstance(c, dict):
                            sha = c.get("id")
                        if not sha:
                            continue
                        shas.append(sha)
                        msg = ""
                        if isinstance(c, dict):
                            commit = c.get("commit", {})
                            msg = commit.get("message", "") if isinstance(commit, dict) else ""
                        first = msg.splitlines()[0] if msg else ""
                        tick = "✓ " if sha in have else ""
                        labels.append(f"{tick}{sha[:7]} {first[:70]}")
                        subs.append(f"{sha}\n{msg.strip()}" if msg else sha)
                    latest_tick = " ✓" if shas and shas[0] in have else ""
                    labels.insert(0, "Latest" + latest_tick)
                    GLib.idle_add(self._set_version_model, labels, [], shas, subs, cfg.get("project"), False)
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

    def _worker(self):
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
                launch(appimage)
            else:
                if not self.cfg.get("source_enabled", True):
                    self._set_status("Source builds are hidden (Settings).")
                    return
                missing = [(n, d) for n, ok, d, req in check_build_deps() if req and not ok]
                if missing:
                    text = "\n".join(f"- {n}: {d}" for n, d in missing)
                    GLib.idle_add(self._show_deps_dialog, self, text)
                    self._set_status(f"Missing build dependencies: {', '.join(n for n, _d in missing)}")
                    return
                idx = self.version_drop.get_selected()
                commit = self._commit_shas[idx - 1] if idx > 0 and idx - 1 < len(self._commit_shas) else None
                binary = build_source(
                    self.cfg,
                    status_cb=self._set_status,
                    log_cb=self._append_log,
                    commit=commit,
                )
                launch(binary)
        except Exception as e:
            self._set_status(f"Error: {e}")
        finally:
            self._set_busy(False)
            GLib.idle_add(self.progress.set_fraction, 0.0)
            if self.auto:

                def _quit():
                    self.get_application().quit()
                    return False

                GLib.timeout_add_seconds(2, _quit)

    def on_install_shortcut(self, *_args):
        try:
            self._install_shortcut()
            self._set_status("Shortcut installed.")
        except Exception as e:
            self._set_status(f"Shortcut error: {e}")

    def _install_shortcut(self):
        # fallback: module invocation
        desktop_dir = Path.home() / ".local" / "share" / "applications"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = desktop_dir / "eglantine-launcher.desktop"
        icon_src = Path(__file__).resolve().parents[2] / "data" / "icons" / "eglantine-launcher.svg"
        icon_dest = Path.home() / ".local" / "share" / "eglantine-launcher" / "icon.svg"
        icon_dest.parent.mkdir(parents=True, exist_ok=True)
        if icon_src.exists():
            import shutil

            shutil.copyfile(icon_src, icon_dest)
        icon_line = str(icon_dest) if icon_dest.exists() else "application-x-executable"
        content = f"""[Desktop Entry]
Type=Application
Name=Eglantine Launcher
Comment=Launch Eglantine (checks for updates)
Exec=env PYTHONPATH={Path(__file__).resolve().parents[2]} {sys.executable} -m eglantine_launcher --auto
Icon={icon_line}
Terminal=false
Categories=Game;Emulator;
"""
        desktop_file.write_text(content)
        # update desktop database if available
        import subprocess

        subprocess.run(["update-desktop-database", str(desktop_dir)], capture_output=True)

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
        # rebuild project combo
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
