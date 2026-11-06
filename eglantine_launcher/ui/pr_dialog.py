import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..config import game_catalog
from ..config import load as load_config
from ..config import save as save_config
from ..tasks import (
    fetch_pr_info,
    is_pr_project,
    make_pr_project,
    parse_pr_url,
    pr_default_name,
    refresh_pr_project,
    remove_pr_project,
    source_key,
)
from .sidebar import SidebarLayout
from .taglist import TagListEditor


class PREditDialog(Adw.Window):
    """Add or edit a one-off PR build. Name defaults to the PR title."""

    def __init__(self, parent, project=None, on_save=None, cfg=None):
        super().__init__(
            transient_for=parent, modal=True, title="Edit PR build" if project else "New PR build"
        )
        self.set_default_size(680, 560)
        self.on_save = on_save
        self.project = dict(project) if project else {}
        self._cfg = cfg or {}
        self._name_touched = bool(self.project.get("name"))
        self._config_key_touched = bool(self.project.get("config_key"))
        self._updating_config = False
        self._lookup_seq = 0
        self._fetching = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        general_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        group = Adw.PreferencesGroup(
            title="PR build",
            description="Paste a GitHub or Forgejo PR link. The name fills in from the PR title.",
        )
        self.url_entry = Adw.EntryRow(title="PR link")
        self.url_entry.set_text(self.project.get("pr_url", ""))
        self.url_entry.connect("changed", self._on_url_changed)
        group.add(self.url_entry)
        self.name_entry = Adw.EntryRow(title="Name")
        self.name_entry.set_text(self.project.get("name", ""))
        self.name_entry.connect("changed", self._on_name_changed)
        group.add(self.name_entry)
        general_page.append(group)

        settings_group = Adw.PreferencesGroup(
            title="Emulator settings",
            description="Shared with other builds of the same repo unless renamed.",
        )
        self.config_key_entry = Adw.EntryRow(title="Settings folder")
        self.config_key_entry.set_text(self._current_config_key())
        self.config_key_entry.connect("changed", self._on_config_key_changed)
        settings_group.add(self.config_key_entry)
        general_page.append(settings_group)

        self.status_label = Gtk.Label(label="")
        self.status_label.set_wrap(True)
        self.status_label.add_css_class("dim-label")
        self.status_label.set_xalign(0)
        general_page.append(self.status_label)

        if self.project.get("pr_url"):
            link = Gtk.LinkButton.new_with_label(self.project["pr_url"], "Open PR page")
            link.set_halign(Gtk.Align.START)
            general_page.append(link)

        games_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        games_group = Adw.PreferencesGroup(
            title="Best for",
            description="Games this build targets. Shown under Project with hover notes.",
        )
        games_flow = Gtk.FlowBox()
        games_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        games_flow.set_max_children_per_line(2)
        games_flow.set_column_spacing(12)
        current_games = {
            (b.get("title_id") if isinstance(b, dict) else b) for b in (self.project.get("best_for") or [])
        }
        self._game_checks = {}
        self._game_names = {}
        for title_id, name in game_catalog(self._cfg).items():
            check = Gtk.CheckButton(label=name)
            check.set_active(title_id in current_games)
            self._game_checks[title_id] = check
            self._game_names[title_id] = name
            games_flow.append(check)
        games_group.add(games_flow)
        games_page.append(games_group)

        tags_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        tags_lbl = Gtk.Label(label="Custom tags")
        tags_lbl.add_css_class("heading")
        tags_lbl.set_xalign(0)
        tags_page.append(tags_lbl)
        tags_group = Adw.PreferencesGroup(
            description="Freeform labels with hover notes, e.g. fix - Fixes mods on NUP.",
        )
        self._tag_editor = TagListEditor()
        for tag in self.project.get("tags") or []:
            if isinstance(tag, dict):
                self._tag_editor.add_tag(tag.get("label", ""), tag.get("tooltip", ""))
        tags_group.add(self._tag_editor)
        tags_page.append(tags_group)
        add_tag_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_tag_box.set_halign(Gtk.Align.END)
        add_tag_btn = Gtk.Button(label="Add tag")
        add_tag_btn.connect("clicked", lambda *_: self._tag_editor.add_tag())
        add_tag_box.append(add_tag_btn)
        tags_page.append(add_tag_box)

        self._layout = SidebarLayout([("general", "General"), ("games", "Games"), ("tags", "Tags")])
        self._layout.set_margin_top(12)
        self._layout.set_margin_bottom(12)
        self._layout.set_margin_start(12)
        self._layout.set_margin_end(12)
        self._layout.add_page("general", general_page)
        self._layout.add_page("games", games_page)
        self._layout.add_page("tags", tags_page)
        box.append(self._layout)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        btn_box.append(cancel)
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", self._save)
        btn_box.append(self.save_btn)
        box.append(btn_box)

        self.set_content(box)
        if self.project.get("pr_url"):
            self._schedule_lookup()

    def show_page(self, name):
        """Select a sidebar page by name."""
        if getattr(self, "_layout", None) is not None:
            self._layout.show_page(name)

    def _on_name_changed(self, *_args):
        self._name_touched = True

    def _current_config_key(self):
        key = (self.project.get("config_key") or "").strip()
        return key or self._derived_config_key()

    def _derived_config_key(self):
        parsed = parse_pr_url(self.url_entry.get_text().strip())
        if parsed is None:
            return ""
        return source_key(
            {
                "source_url": f"{parsed['scheme']}://{parsed['web_host']}/"
                f"{parsed['owner']}/{parsed['repo']}.git"
            }
        )

    def _on_config_key_changed(self, *_args):
        if not self._updating_config:
            self._config_key_touched = True

    def _collect_tag_state(self):
        """Read the Best for checkboxes and custom tag rows (main thread)."""
        names = getattr(self, "_game_names", {})
        games = [
            {"title_id": title_id, "name": names.get(title_id, title_id)}
            for title_id in names
            if self._game_checks.get(title_id) is not None and self._game_checks[title_id].get_active()
        ]
        return games, self._tag_editor.collect()

    def _sync_config_key(self):
        """Follow the PR's repo while the field has not been edited by hand."""
        if self._config_key_touched:
            return
        self._updating_config = True
        try:
            self.config_key_entry.set_text(self._derived_config_key())
        finally:
            self._updating_config = False

    def _on_url_changed(self, *_args):
        self._schedule_lookup()
        self._sync_config_key()

    def _schedule_lookup(self):
        self._lookup_seq += 1
        seq = self._lookup_seq
        GLib.timeout_add(600, lambda: self._maybe_lookup(seq))

    def _maybe_lookup(self, seq):
        if seq != self._lookup_seq:
            return False
        url = self.url_entry.get_text().strip()
        if not url or parse_pr_url(url) is None:
            return False
        threading.Thread(target=self._lookup, args=(url, seq), daemon=True).start()
        return False

    def _lookup(self, url, seq):
        GLib.idle_add(self._set_status, "Looking up PR...")
        try:
            details = fetch_pr_info(url, self._cfg)
        except Exception as e:
            if seq == self._lookup_seq:
                GLib.idle_add(self._set_status, f"Could not read PR: {e}")
            return
        if seq == self._lookup_seq:
            GLib.idle_add(self._on_info, details)

    def _set_status(self, text):
        self.status_label.set_label(text or "")
        return False

    def _on_info(self, details):
        state = details.get("state", "open")
        title = details.get("title", "")
        head = (details.get("head_sha") or "")[:7]
        self._set_status(f"{state} - {title} ({head})" if head else f"{state} - {title}")
        if not self._name_touched and not self.name_entry.get_text().strip():
            self.name_entry.set_text(
                pr_default_name(details, details.get("number"), details.get("owner"), details.get("repo"))
            )
            self._name_touched = False
        return False

    def _save(self, *_args):
        if self._fetching:
            return
        name = self.name_entry.get_text().strip()
        url = self.url_entry.get_text().strip()
        if not url:
            self._set_status("Enter a PR link.")
            return
        if parse_pr_url(url) is None:
            self._set_status("That does not look like a PR link (expected .../pull/NNN).")
            return
        # Read widget state here on the main thread; the worker must not
        # touch GTK.
        self._pending_tags = self._collect_tag_state()
        existing = self.project or {}
        if existing.get("id") and existing.get("pr_url") == url:
            # URL unchanged: keep the known PR details (head SHA, state,
            # title) and save without touching the network, so tag edits
            # still save when GitHub is rate-limiting or offline.
            project = dict(existing)
            if name:
                project["name"] = name
            games, tags = self._pending_tags
            project["best_for"] = games
            project["tags"] = tags
            project["config_key"] = self._resolved_config_key(project.get("source_url", ""))
            if self.on_save:
                self.on_save(project, existing.get("id"))
            self.close()
            return
        self._fetching = True
        self.save_btn.set_sensitive(False)
        self._set_status("Looking up PR...")
        threading.Thread(target=self._save_lookup, args=(name, url), daemon=True).start()

    def _resolved_config_key(self, source_url):
        return source_key(
            {
                "config_key": self.config_key_entry.get_text().strip(),
                "source_url": source_url,
            }
        )

    def _save_lookup(self, name, url):
        try:
            details = fetch_pr_info(url, self._cfg)
        except Exception as e:
            GLib.idle_add(self._save_offline, name, url, f"Could not read PR: {e}")
            return
        try:
            project = make_pr_project(name, url, details, existing=self.project or None)
        except Exception as e:
            GLib.idle_add(self._save_failed, str(e))
            return
        GLib.idle_add(self._save_done, project)

    def _save_offline(self, name, url, message):
        """Save a stub built from the URL alone (offline or rate-limited).

        Head SHA, title and state are filled in by the list's background
        refresh once GitHub answers, so the entry is usable immediately
        for tags and launching from a warm checkout.
        """
        self._fetching = False
        self.save_btn.set_sensitive(True)
        try:
            project = make_pr_project(name, url, {}, existing=self.project or None)
        except Exception as e:
            self._set_status(str(e) or message)
            return False
        project["pr_state"] = "open"
        self._save_done(project)
        return False

    def _save_failed(self, message):
        self._fetching = False
        self.save_btn.set_sensitive(True)
        self._set_status(message)
        return False

    def _save_done(self, project):
        self._fetching = False
        games, tags = getattr(self, "_pending_tags", ([], []))
        project["best_for"] = games
        project["tags"] = tags
        project["config_key"] = self._resolved_config_key(project.get("source_url", ""))
        old_id = self.project.get("id")
        if self.on_save:
            self.on_save(project, old_id)
        self.close()
        return False


class PRBuildsPanel(Gtk.Box):
    """List of one-off PR builds: launch, edit, delete.

    Shared by the PR Builds dialog and the Settings PR Builds page.
    """

    def __init__(self, on_changed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.on_changed = on_changed
        self.cfg = load_config()

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_valign(Gtk.Align.CENTER)
        lbl = Gtk.Label(label="One-off PR builds")
        lbl.add_css_class("heading")
        lbl.set_xalign(0)
        lbl.set_hexpand(True)
        header.append(lbl)
        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", self._add)
        header.append(add_btn)
        self.append(header)

        hint = Gtk.Label(
            label="Build any GitHub or Forgejo PR by link. Names default to the PR title. "
            "Each build is isolated and appears in the main Project list."
        )
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        hint.set_xalign(0)
        self.append(hint)

        self.pr_group = Adw.PreferencesGroup()
        self._rows = []
        self.append(self.pr_group)

        self.empty_label = Gtk.Label(label="No PR builds yet. Click Add to build your first PR.")
        self.empty_label.add_css_class("dim-label")
        self.empty_label.set_wrap(True)
        self.append(self.empty_label)

        self._refresh()

    def _dialog_parent(self):
        try:
            root = self.get_root()
            return root if isinstance(root, Gtk.Window) else None
        except Exception:
            return None

    def _pr_projects(self):
        return [p for p in self.cfg.get("projects", []) if is_pr_project(p)]

    def _refresh(self):
        self.cfg = load_config()
        for row in self._rows:
            try:
                self.pr_group.remove(row)
            except Exception:
                pass
        self._rows = []
        prs = self._pr_projects()
        self.empty_label.set_visible(not prs)
        for p in prs:
            row = self._pr_row(p)
            self._rows.append(row)
            self.pr_group.add(row)
        # refresh head/state in the background without blocking the list
        if prs:
            threading.Thread(target=self._refresh_states, args=([dict(p) for p in prs],), daemon=True).start()

    def _subtitle(self, p):
        owner, repo, num = p.get("pr_owner", ""), p.get("pr_repo", ""), p.get("pr_number", "")
        head = (p.get("pr_head_sha") or "")[:7]
        state = p.get("pr_state", "open")
        base = f"{owner}/{repo} #{num}" if num else (p.get("pr_url") or "")
        parts = [base, state]
        if head:
            parts.append(head)
        sub = " - ".join([x for x in parts if x])
        if state in ("merged", "closed"):
            sub += " - likely in main builds now, safe to delete"
        return sub

    def _pr_row(self, p):
        row = Adw.ActionRow(title=p.get("name", p.get("id", "")), subtitle=self._subtitle(p))
        row.set_activatable(True)
        row.connect("activated", self._on_edit_clicked, p)
        state = p.get("pr_state", "open")
        if state in ("merged", "closed"):
            badge = Gtk.Label(label="Merged" if state == "merged" else "Closed")
            badge.add_css_class("dim-label")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)
        url = p.get("pr_url", "")
        globe = Path(__file__).resolve().parents[1] / "icons" / "globe-symbolic.svg"
        if globe.exists():
            icon = Gtk.Image.new_from_file(str(globe))
        else:
            icon = Gtk.Image.new_from_icon_name("go-next-symbolic")
        link_btn = Gtk.Button()
        link_btn.set_child(icon)
        link_btn.add_css_class("flat")
        link_btn.set_valign(Gtk.Align.CENTER)
        link_btn.set_tooltip_text(f"Open {url}")
        link_btn.connect("clicked", self._on_link_clicked, url)
        row.add_suffix(link_btn)
        edit = Gtk.Button.new_from_icon_name("document-edit-symbolic")
        edit.add_css_class("flat")
        edit.set_valign(Gtk.Align.CENTER)
        edit.set_tooltip_text("Edit PR build")
        edit.connect("clicked", self._on_edit_clicked, p)
        row.add_suffix(edit)
        delete = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        delete.add_css_class("flat")
        delete.set_valign(Gtk.Align.CENTER)
        delete.set_tooltip_text(f"Delete {p.get('name', '')} and its build data")
        delete.connect("clicked", self._on_delete_clicked, p)
        row.add_suffix(delete)
        return row

    def _refresh_states(self, snapshot):
        changed = False
        for old in snapshot:
            pid = old.get("id")
            url = old.get("pr_url")
            if not pid or not url:
                continue
            try:
                details = fetch_pr_info(url, self.cfg)
            except Exception:
                continue
            for p in self.cfg.get("projects", []):
                if p.get("id") == pid:
                    before = (p.get("pr_state"), p.get("pr_head_sha"), p.get("pr_title"))
                    refresh_pr_project(p, details)
                    if (p.get("pr_state"), p.get("pr_head_sha"), p.get("pr_title")) != before:
                        changed = True
                    break
        if changed:
            try:
                save_config(self.cfg)
            except Exception:
                pass
            GLib.idle_add(self._refresh_labels_only)

    def _refresh_labels_only(self):
        # simplest correct refresh: rebuild rows (cheap for a handful of PRs)
        self._refresh()
        if self.on_changed:
            self.on_changed()
        return False

    def _add(self, *_args):
        def save(project, old_id):
            self.cfg.setdefault("projects", []).append(project)
            save_config(self.cfg)
            self._refresh()
            if self.on_changed:
                self.on_changed()

        PREditDialog(self._dialog_parent(), None, save, cfg=self.cfg).present()

    def _on_edit_clicked(self, _btn, proj):
        def save(project, old_id):
            for i, p in enumerate(self.cfg.get("projects", [])):
                if p.get("id") == old_id:
                    self.cfg["projects"][i] = project
                    break
            save_config(self.cfg)
            self._refresh()
            if self.on_changed:
                self.on_changed()

        PREditDialog(self._dialog_parent(), proj, save, cfg=self.cfg).present()

    def _on_link_clicked(self, _btn, url):
        if not url:
            return
        parent = self.get_root()
        if not isinstance(parent, Gtk.Window):
            parent = None
        Gtk.show_uri(parent, url, 0)

    def _on_delete_clicked(self, _btn, proj):
        name = proj.get("name", proj.get("id", "this PR build"))
        dlg = Adw.MessageDialog(
            transient_for=self._dialog_parent(),
            modal=True,
            heading=f"Delete {name}?",
            body="Removes the PR entry and everything built for it, "
            "including the source checkout. This cannot be undone.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def on_response(_dlg, response):
            if response == "delete":
                remove_pr_project(self.cfg, proj.get("id"))
                self.cfg = load_config()
                self._refresh()
                if self.on_changed:
                    self.on_changed()
            _dlg.close()

        dlg.connect("response", on_response)
        dlg.present()


class PRBuildsDialog(Adw.Window):
    """Standalone PR Builds dialog (thin wrapper around PRBuildsPanel)."""

    def __init__(self, parent, on_changed=None):
        super().__init__(transient_for=parent, modal=True, title="PR Builds")
        self.set_default_size(560, 520)
        self.panel = PRBuildsPanel(on_changed=on_changed)

        toolbar = Adw.HeaderBar()
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.panel)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.append(scrolled)
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda *_: self.close())
        close_btn.set_margin_top(12)
        close_btn.set_margin_bottom(12)
        close_btn.set_margin_start(12)
        close_btn.set_margin_end(12)
        page.append(close_btn)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(toolbar)
        toolbar_view.set_content(page)
        self.set_content(toolbar_view)

    # Backwards-compatible surface for existing callers/tests.
    @property
    def cfg(self):
        return self.panel.cfg

    @property
    def _rows(self):
        return self.panel._rows

    @property
    def empty_label(self):
        return self.panel.empty_label

    def _refresh(self):
        self.panel._refresh()

    def _pr_projects(self):
        return self.panel._pr_projects()
