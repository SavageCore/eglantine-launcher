import subprocess
import threading
import uuid
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..config import load as load_config
from ..config import save as save_config
from ..tasks import (
    RESERVED_ENV_KEYS,
    SAVE_BACKUP_KEEP,
    delete_save_backup,
    delete_storage_row,
    fetch_github_branch_dates,
    format_backup_name,
    format_size,
    get_cached_repo_lookup,
    get_default_branch,
    is_valid_env_name,
    known_config_keys,
    list_branches,
    list_save_backups,
    lookup_repo_for_add,
    move_source_config,
    parse_repo,
    project_storage,
    remove_project_data,
    restore_save_backup,
    should_fetch_branch_dates,
    sort_branch_names,
    source_key,
)
from ..tray import ASK_LABEL, CLOSE_CHOICES, LAUNCH_CHOICES
from .pr_dialog import PRBuildsPanel
from .sidebar import SidebarLayout, page_box
from .taglist import TagListEditor


def _slug(name):
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-") or uuid.uuid4().hex[:8]


def _config_picker_label(key, names):
    """Picker line for one folder: slug plus the sources sharing it.

    The source names ride along in the label so the picker's substring
    search matches them too - no need to remember the raw slug.
    """
    shown = [n for n in names if n][:3]
    suffix = f" - {', '.join(shown)}" if shown else ""
    if len(names) > len(shown):
        suffix += f" +{len(names) - len(shown)} more"
    return f"{key}{suffix}"


class AddSourcePage(Gtk.Box):
    """Step 1: paste a GitHub / Forgejo repo link."""

    def __init__(self, on_next, cfg=None, on_cancel=None):
        self.cfg = cfg or {}
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.on_next = on_next
        self.on_cancel = on_cancel
        self._seq = 0

        group = Adw.PreferencesGroup(
            title="Add emulator source", description="Paste a GitHub or Forgejo repository link."
        )
        self.url_entry = Adw.EntryRow(title="Repository link")
        self.url_entry.set_text("")
        group.add(self.url_entry)
        self.append(group)

        self.error_label = Gtk.Label(label="")
        self.error_label.set_wrap(True)
        self.append(self.error_label)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        self.back_btn = Gtk.Button(label="Back")
        self.back_btn.connect("clicked", self._back)
        btn_box.append(self.back_btn)
        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", self._next)
        btn_box.append(self.next_btn)
        self.append(btn_box)

    def set_status(self, msg, is_error=False):
        self.error_label.set_label(msg)
        if is_error:
            self.error_label.add_css_class("error")
            self.error_label.remove_css_class("dim-label")
        else:
            self.error_label.remove_css_class("error")
            if msg:
                self.error_label.add_css_class("dim-label")
            else:
                self.error_label.remove_css_class("dim-label")

    def reset(self):
        """Clear busy/error state so another URL can be tried."""
        self._seq += 1
        self.set_status("")
        self.next_btn.set_sensitive(True)

    def _back(self, *_args):
        self.reset()
        if self.on_cancel is not None:
            self.on_cancel()

    def _next(self, *_args):
        url = self.url_entry.get_text().strip()
        if not url:
            self.set_status("Enter a repository link.", is_error=True)
            return
        cached = get_cached_repo_lookup(url)
        if cached is not None:
            name, branches, releases_url, default_branch = cached
            self.set_status("")
            self.next_btn.set_sensitive(True)
            self.on_next(url, name, branches, releases_url, "", default_branch)
            return
        self._seq += 1
        seq = self._seq
        self.next_btn.set_sensitive(False)
        self.set_status("Looking up repository...", is_error=False)
        threading.Thread(target=self._lookup, args=(url, seq), daemon=True).start()

    def _lookup(self, url, seq):
        try:
            name, branches, releases_url, default_branch = lookup_repo_for_add(url, self.cfg)
            GLib.idle_add(self._lookup_succeeded, seq, url, name, branches, releases_url, default_branch)
        except Exception as e:
            GLib.idle_add(self._lookup_failed, seq, str(e))

    def _lookup_succeeded(self, seq, url, name, branches, releases_url, default_branch):
        if seq != self._seq:
            return False
        self.set_status("")
        self.next_btn.set_sensitive(True)
        self.on_next(url, name, branches, releases_url, "", default_branch)
        return False

    def _lookup_failed(self, seq, err):
        if seq != self._seq:
            return False
        self.set_status(f"Could not read repository: {err}", is_error=True)
        self.next_btn.set_sensitive(True)
        return False


class ProjectEditDialog(Adw.Window):
    """Step 2 (add) or full edit dialog. No ID field, branch is a dropdown."""

    def __init__(self, parent, project=None, on_save=None, prefill=None, cfg=None, on_storage_changed=None):
        super().__init__(transient_for=parent, modal=True, title="Edit source" if project else "New source")
        self.set_default_size(680, 560)
        self.on_save = on_save
        self.on_storage_changed = on_storage_changed
        self.project = dict(project) if project else {}
        prefill = prefill or {}
        self._cfg = cfg or {}
        self._pid = self.project.get("id")
        self._source_url = self.project.get("source_url", "") or prefill.get("source_url", "")
        self._default_branch = prefill.get("default_branch")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        group = Adw.PreferencesGroup()
        self.name_entry = Adw.EntryRow(title="Name")
        self.name_entry.set_text(self.project.get("name", prefill.get("name", "")))
        group.add(self.name_entry)

        self.source_entry = Adw.EntryRow(title="Source URL")
        self.source_entry.set_text(self.project.get("source_url", prefill.get("source_url", "")))
        group.add(self.source_entry)

        self.branch_row = Adw.ComboRow(title="Branch")
        if hasattr(self.branch_row, "set_enable_search"):
            self.branch_row.set_enable_search(True)
        # The search entry needs an expression to obtain strings from items;
        # without it the entry shows but never filters.
        self.branch_row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        if hasattr(self.branch_row, "set_search_match_mode"):
            self.branch_row.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        branches = prefill.get("branches", []) or []
        current = self.project.get("branch", "") or prefill.get("branch", "")
        if current and current not in branches:
            branches = [current] + branches
        if not branches:
            branches = [current or "master"]
        self._branches = branches
        self.branch_row.set_model(Gtk.StringList.new(branches))
        self.branch_row.set_selected(branches.index(current) if current in branches else 0)
        group.add(self.branch_row)

        settings_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        settings_page.append(group)
        if parse_repo(self._source_url)[0] == "github" and len(branches) > 1:
            threading.Thread(target=self._refresh_github_order, daemon=True).start()

        releases_group = Adw.PreferencesGroup(
            title="Releases",
            description="Auto-filled from the repository link. Leave empty to build from source only.",
        )
        self.releases_entry = Adw.EntryRow(title="Releases URL")
        self.releases_entry.set_text(self.project.get("releases_url") or prefill.get("releases_url") or "")
        releases_group.add(self.releases_entry)

        self.prerelease_row = Adw.SwitchRow(title="Include pre-releases")
        self.prerelease_row.set_active(
            self.project.get("include_prerelease", prefill.get("include_prerelease", True))
        )
        releases_group.add(self.prerelease_row)

        self.exclude_entry = Adw.EntryRow(title="Exclude keywords")
        self.exclude_entry.set_text(
            self.project.get("exclude_keywords", prefill.get("exclude_keywords", "")) or ""
        )
        releases_group.add(self.exclude_entry)
        settings_page.append(releases_group)

        shortcut_group = Adw.PreferencesGroup(
            title="Shortcut",
            description="Icon used for the Desktop, Start Menu and Steam shortcut.",
        )
        self.icon_entry = Adw.EntryRow(title="Icon URL")
        self.icon_entry.set_text(self.project.get("icon_url", "") or "")
        shortcut_group.add(self.icon_entry)
        settings_page.append(shortcut_group)

        settings_group = Adw.PreferencesGroup(
            title="Emulator settings",
            description="Each source has its own folder in "
            "~/.config/eglantine-launcher/sources. Use the same name on two "
            "sources to share settings.",
        )
        self.config_key_entry = Adw.EntryRow(title="Settings folder")
        self.config_key_entry.set_text(self._current_config_key())
        self.config_key_entry.set_tooltip_text(
            "Folder under ~/.config/eglantine-launcher/sources holding this source's emulator settings."
        )
        self.config_picker_row = Adw.ComboRow(title="Use existing folder")
        if hasattr(self.config_picker_row, "set_enable_search"):
            self.config_picker_row.set_enable_search(True)
        # Same as the branch picker: without an expression the search
        # entry shows but never filters.
        self.config_picker_row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        if hasattr(self.config_picker_row, "set_search_match_mode"):
            self.config_picker_row.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self.config_picker_keys = []
        self._refresh_config_picker()
        self.config_picker_row.connect("notify::selected", self._on_config_picker_changed)
        settings_group.add(self.config_picker_row)
        settings_group.add(self.config_key_entry)
        settings_page.append(settings_group)

        if self._pid:
            self._layout = SidebarLayout([("settings", "Settings"), ("storage", "Storage"), ("tags", "Tags")])
            self._layout.set_margin_top(12)
            self._layout.set_margin_bottom(12)
            self._layout.set_margin_start(12)
            self._layout.set_margin_end(12)
            self._layout.add_page("settings", settings_page)
            self._storage_group = Adw.PreferencesGroup(
                title="On-disk data",
                description="Delete downloads or source folders you no longer need. "
                "Entries used by the current mode cannot be deleted.",
            )
            storage_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            storage_page.append(self._storage_group)
            self._storage_status = Gtk.Label(label="Loading…")
            self._storage_status.add_css_class("dim-label")
            self._storage_status.set_wrap(True)
            storage_page.append(self._storage_status)
            self._layout.add_page("storage", storage_page)
            self._layout.add_page("tags", self._build_tags_page())
            box.append(self._layout)
            self._stack = self._layout.stack
            self._refresh_storage()
        else:
            box.append(self._scrolled(settings_page))
            self._stack = None
            self._layout = None

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        btn_box.append(cancel)
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._save)
        btn_box.append(save_btn)
        box.append(btn_box)

        self.set_content(box)

    def show_page(self, name):
        """Select a sidebar page by name."""
        if getattr(self, "_layout", None) is not None:
            self._layout.show_page(name)

    def _refresh_storage(self):
        self._storage_status.set_label("Loading…")

        def worker():
            try:
                rows = project_storage(self._cfg, self._pid)
            except Exception as e:  # never break the dialog on a stat failure
                rows, err = [], str(e)
            else:
                err = ""
            GLib.idle_add(self._render_storage, rows, err)

        threading.Thread(target=worker, daemon=True).start()

    def _render_storage(self, rows, err=""):
        if getattr(self, "_storage_group", None) is None:
            return False
        # AdwPreferencesGroup keeps rows in an internal list box, so the
        # group's direct children are not the rows: track what was added
        # and remove exactly those, otherwise refreshes duplicate rows.
        for widget in getattr(self, "_storage_rows", []):
            try:
                self._storage_group.remove(widget)
            except Exception:
                pass
        self._storage_rows = []
        if err:
            self._storage_status.set_label(f"Could not read storage: {err}")
        elif not rows:
            self._storage_status.set_label("Nothing downloaded or checked out for this source yet.")
        else:
            total = sum(r.get("size") or 0 for r in rows)
            self._storage_status.set_label(
                "Total: "
                + format_size(total)
                + ". Deleted data is re-downloaded or re-cloned on next launch."
            )
            for row in rows:
                widget = self._storage_row(row)
                self._storage_rows.append(widget)
                self._storage_group.add(widget)
        return False

    def _storage_row(self, row):
        size = format_size(row.get("size")) if row.get("size") is not None else "unknown size"
        action = Adw.ActionRow(title=row.get("label", ""), subtitle=f"{size} - {row.get('detail', '')}")
        if row.get("in_use"):
            badge = Gtk.Label(label="In use")
            badge.add_css_class("dim-label")
            badge.set_valign(Gtk.Align.CENTER)
            action.add_suffix(badge)
        else:
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.set_tooltip_text(f"Delete {row.get('label', '')}")
            del_btn.connect("clicked", self._on_storage_delete_clicked, row)
            action.add_suffix(del_btn)
        return action

    def _on_storage_delete_clicked(self, _btn, row):
        body = row.get("confirm") or (
            f"Removes {row.get('detail', '')}. The launcher re-downloads or re-clones it on next launch."
        )
        dlg = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading=f"Delete {row.get('label', 'data')}?",
            body=body,
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def on_response(_dlg, response):
            if response == "delete":
                self._delete_storage_row(row)
            _dlg.close()

        dlg.connect("response", on_response)
        dlg.present()

    def _delete_storage_row(self, row):
        self._storage_status.set_label(f"Deleting {row.get('label', '')}…")

        def worker():
            err = delete_storage_row(self._cfg, row)
            if err is None and self.on_storage_changed:
                GLib.idle_add(self.on_storage_changed)
            GLib.idle_add(self._after_storage_delete, err)

        threading.Thread(target=worker, daemon=True).start()

    def _after_storage_delete(self, err):
        if err:
            self._storage_status.set_label(err)
        else:
            self._refresh_storage()
        return False

    def update_branches(self, branches):
        """Replace the dropdown model, preserving the selected branch name."""
        branches = [b for b in (branches or []) if b]
        if not branches:
            return
        selected = None
        current = getattr(self, "_branches", [])
        try:
            idx = self.branch_row.get_selected()
            if 0 <= idx < len(current):
                selected = current[idx]
        except Exception:
            selected = None
        self._branches = branches
        self.branch_row.set_model(Gtk.StringList.new(branches))
        if selected in branches:
            self.branch_row.set_selected(branches.index(selected))
        else:
            self.branch_row.set_selected(0)

    def _refresh_github_order(self):
        try:
            if not should_fetch_branch_dates(self._source_url, self._branches, cfg=self._cfg):
                return
            dates = fetch_github_branch_dates(self._source_url, self._branches, cfg=self._cfg)
            if not dates:
                return
            if self._default_branch is None:
                try:
                    self._default_branch = get_default_branch(self._source_url, self._cfg)
                except Exception:
                    pass
            ordered = sort_branch_names(self._branches, dates, self._default_branch)
            if ordered != self._branches:
                GLib.idle_add(self.update_branches, ordered)
        except Exception:
            pass

    @staticmethod
    def _scrolled(child):
        """Scroll long pages instead of growing past the screen."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_max_content_height(720)
        scrolled.set_child(child)
        return scrolled

    def _build_tags_page(self):
        """Tags tab: read-only upstream curation plus the user's own entries."""
        from ..config import game_catalog

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        curated = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for item in self.project.get("best_for") or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("title_id", "")
            note = (item.get("note") or "").strip()
            lbl = Gtk.Label(label=f"{name} - {note}" if note else name)
            lbl.set_xalign(0)
            lbl.set_wrap(True)
            lbl.add_css_class("dim-label")
            curated.append(lbl)
        for tag in self.project.get("tags") or []:
            if not isinstance(tag, dict):
                continue
            label = tag.get("label") or tag.get("id", "")
            tip = (tag.get("tooltip") or "").strip()
            lbl = Gtk.Label(label=f"{label} - {tip}" if tip else label)
            lbl.set_xalign(0)
            lbl.set_wrap(True)
            lbl.add_css_class("dim-label")
            curated.append(lbl)
        if curated.get_first_child() is not None:
            upstream = Adw.PreferencesGroup(
                title="Recommended",
                description="Hand-picked games from the wichforks guide. They stay "
                "up to date on their own, and anything you add below is always kept.",
            )
            upstream.add(curated)
            page.append(upstream)
            link = Gtk.LinkButton.new_with_label(
                "https://github.com/riyadhHDA/wichforks", "wichforks fork guide on GitHub"
            )
            link.set_halign(Gtk.Align.START)
            page.append(link)

        curated_ids = {
            (b.get("title_id") if isinstance(b, dict) else None) for b in (self.project.get("best_for") or [])
        }
        user_games = {
            (b.get("title_id") if isinstance(b, dict) else None): b
            for b in (self.project.get("user_best_for") or [])
            if isinstance(b, dict)
        }
        games_group = Adw.PreferencesGroup(
            title="Your games",
            description="Extra games this source is good for.",
        )
        self._user_game_checks = {}
        self._user_games_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        games_group.add(self._user_games_box)
        for title_id, name in game_catalog(self._cfg).items():
            if title_id in curated_ids:
                continue
            self._append_user_game_row(title_id, name, title_id in user_games, user_games.get(title_id))
        page.append(games_group)

        add_game_header = Gtk.Label(label="Add a game")
        add_game_header.add_css_class("heading")
        add_game_header.set_xalign(0)
        page.append(add_game_header)
        self._user_game_error = Gtk.Label(label="")
        self._user_game_error.add_css_class("error")
        self._user_game_error.set_wrap(True)
        self._user_game_error.set_xalign(0)
        page.append(self._user_game_error)
        add_game_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._new_game_id_entry = Gtk.Entry()
        self._new_game_id_entry.set_placeholder_text("Title ID, e.g. 0400C3F00006E000")
        add_game_row.append(self._new_game_id_entry)
        self._new_game_name_entry = Gtk.Entry()
        self._new_game_name_entry.set_placeholder_text("Name, e.g. Mario Kart World")
        self._new_game_name_entry.set_hexpand(True)
        add_game_row.append(self._new_game_name_entry)
        add_game_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_game_box.set_halign(Gtk.Align.END)
        add_game_btn = Gtk.Button(label="Add game")
        add_game_btn.connect("clicked", self._on_add_user_game)
        add_game_box.append(add_game_btn)
        add_game_row.append(add_game_box)
        page.append(add_game_row)

        tags_lbl = Gtk.Label(label="Your tags")
        tags_lbl.add_css_class("heading")
        tags_lbl.set_xalign(0)
        page.append(tags_lbl)
        tags_group = Adw.PreferencesGroup(
            description="Freeform labels with hover notes.",
        )
        self._user_tags_editor = TagListEditor()
        for tag in self.project.get("user_tags") or []:
            if isinstance(tag, dict):
                self._user_tags_editor.add_tag(tag.get("label", ""), tag.get("tooltip", ""))
        tags_group.add(self._user_tags_editor)
        page.append(tags_group)
        add_tag_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_tag_box.set_halign(Gtk.Align.END)
        add_tag_btn = Gtk.Button(label="Add tag")
        add_tag_btn.connect("clicked", lambda *_: self._user_tags_editor.add_tag())
        add_tag_box.append(add_tag_btn)
        page.append(add_tag_box)
        return page

    def _append_user_game_row(self, title_id, name, active=False, existing=None):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        check = Gtk.CheckButton(label=name)
        check.set_active(active)
        row.append(check)
        note_entry = Gtk.Entry()
        note_entry.set_placeholder_text("Why (optional)")
        note_entry.set_hexpand(True)
        note_entry.set_margin_start(28)
        note_entry.set_text((existing.get("note") or "") if isinstance(existing, dict) else "")
        row.append(note_entry)
        self._user_game_checks[title_id] = (check, note_entry, name)
        self._user_games_box.append(row)
        return row

    def _on_add_user_game(self, *_args):
        from ..config import normalize_title_id

        title_id = normalize_title_id(self._new_game_id_entry.get_text())
        name = self._new_game_name_entry.get_text().strip()
        if title_id is None:
            self._user_game_error.set_label("That is not a 16-digit hex title ID.")
            return
        if not name:
            self._user_game_error.set_label("Give the game a name.")
            return
        if title_id in self._user_game_checks:
            self._user_game_error.set_label("That game is already listed above.")
            return
        self._user_game_error.set_label("")
        custom = self._cfg.setdefault("custom_games", [])
        if not any(isinstance(g, dict) and (g.get("title_id") or "").upper() == title_id for g in custom):
            custom.append({"title_id": title_id, "name": name})
            save_config(self._cfg)
        self._append_user_game_row(title_id, name, active=True)
        self._new_game_id_entry.set_text("")
        self._new_game_name_entry.set_text("")

    def _collect_user_tags(self):
        """User overlay state from the Tags tab (no-op when the tab is absent)."""
        games = []
        for title_id, (check, note_entry, name) in getattr(self, "_user_game_checks", {}).items():
            if not check.get_active():
                continue
            note = note_entry.get_text().strip()
            games.append(
                {"title_id": title_id, "name": name, "note": note}
                if note
                else {"title_id": title_id, "name": name}
            )
        editor = getattr(self, "_user_tags_editor", None)
        return games, editor.collect() if editor is not None else []

    def _current_config_key(self):
        """Folder name this source uses now: its override, else the repo key."""
        return (self.project.get("config_key") or "").strip() or source_key(
            {
                "id": self._pid or _slug(self.name_entry.get_text().strip() or "source"),
                "source_url": self.source_entry.get_text().strip(),
            }
        )

    def _refresh_config_picker(self):
        """Fill the existing-folder picker; hide it when there is nothing to pick."""
        current = self.config_key_entry.get_text().strip()
        entries = [(k, list(n)) for k, n in known_config_keys(self._cfg, exclude_id=self._pid)]
        if current and all(k != current for k, _names in entries):
            entries.insert(0, (current, []))
        self.config_picker_keys = [k for k, _names in entries]
        self.config_picker_row.set_model(
            Gtk.StringList.new([_config_picker_label(k, names) for k, names in entries])
        )
        if current in self.config_picker_keys:
            self.config_picker_row.set_selected(self.config_picker_keys.index(current))
        self.config_picker_row.set_visible(len(self.config_picker_keys) > 1)

    def _on_config_picker_changed(self, row, _pspec):
        idx = row.get_selected()
        if 0 <= idx < len(self.config_picker_keys):
            key = self.config_picker_keys[idx]
            if key != self.config_key_entry.get_text().strip():
                self.config_key_entry.set_text(key)

    def _save(self, *_args):
        name = self.name_entry.get_text().strip()
        source_url = self.source_entry.get_text().strip()
        if not name or not source_url:
            return
        branches = getattr(self, "_branches", [])
        branch = branches[self.branch_row.get_selected()] if branches else "master"
        old_id = self.project.get("id")
        project = {
            "id": old_id or f"{_slug(name)}-{uuid.uuid4().hex[:6]}",
            "name": name,
            "source_url": source_url,
            "branch": branch,
            "releases_url": self.releases_entry.get_text().strip() or None,
            "include_prerelease": self.prerelease_row.get_active(),
            "exclude_keywords": self.exclude_entry.get_text().strip(),
            "icon_url": self.icon_entry.get_text().strip() or None,
        }
        # Preserve curated/external fields the editor has no widgets for
        # (best_for/tags/confidence/origin/remote/is_pr/...). Otherwise
        # opening and saving a wichforks default would wipe its metadata.
        for key in (
            "origin",
            "remote",
            "confidence",
            "best_for",
            "tags",
            "user_best_for",
            "user_tags",
            "is_pr",
            "pr_url",
            "pr_number",
            "pr_owner",
            "pr_repo",
            "pr_host",
            "pr_title",
            "pr_state",
            "pr_head_sha",
            "last_built_commit",
        ):
            if key in self.project and key not in project:
                project[key] = self.project[key]
        # The Tags tab edits the user overlay; curated best_for/tags stay
        # as sync left them.
        if self._stack is not None:
            user_games, user_tags = self._collect_user_tags()
            project["user_best_for"] = user_games
            project["user_tags"] = user_tags
        if not project.get("origin") and old_id:
            # A shipped id edited by hand stays managed; a truly custom
            # source is marked manual so sync never touches it.
            try:
                from ..config import _default_by_id
            except Exception:
                _default_by_id = None
            if _default_by_id is not None and old_id in _default_by_id():
                project["origin"] = _default_by_id()[old_id].get("origin", "wichforks")
            else:
                project["origin"] = "manual"
        config_key = self.config_key_entry.get_text().strip()
        project["config_key"] = source_key({"config_key": config_key, "source_url": source_url})
        # Editing a source must not make a deleted config reappear.
        if self.project.get("config_seeded"):
            project["config_seeded"] = True
        if self._pid:
            move_source_config(self._cfg, self.project, project["config_key"])
        if self.on_save:
            self.on_save(project, old_id)
        self.close()


class SettingsWindow(Adw.Window):
    _SIDEBAR_PAGES = (
        ("general", "General"),
        ("github", "GitHub"),
        ("backups", "Backups"),
        ("sources", "Sources"),
        ("games", "Games"),
        ("prs", "PR Builds"),
    )

    def __init__(self, parent, on_changed=None):
        super().__init__(transient_for=parent, modal=True, title="Settings")
        self.set_default_size(780, 620)
        self.on_changed = on_changed
        self.cfg = load_config()

        toolbar = Adw.HeaderBar()

        self._api_base_desc = (
            "Optional. Without a token, GitHub allows ~60 requests/hour "
            "per IP and release checks can fail; a token raises that to 5,000/hour. "
            "Expiration can be set to never - the token only reads public data."
        )
        self.api_group = Adw.PreferencesGroup(
            title="GitHub API",
            description=self._api_base_desc,
        )
        self.token_entry = Adw.PasswordEntryRow(title="GitHub token")
        self.token_entry.set_text(self.cfg.get("github_token", "") or "")
        self.token_entry.set_show_apply_button(True)
        self.token_entry.connect("apply", self._on_token_apply)
        self._connect_token_activate(self.token_entry)
        self._reveal_on_focus(self.token_entry)
        self.api_group.add(self.token_entry)
        self._token_status_label = Gtk.Label(label="")
        self._token_status_label.set_wrap(True)
        self._token_status_label.set_xalign(0)
        self._token_status_label.set_visible(False)
        self._token_status_seq = 0

        token_link = Gtk.LinkButton.new_with_label(
            "https://github.com/settings/personal-access-tokens/new"
            "?name=Eglantine%20Launcher"
            "&description=Release%20checks%20for%20public%20repos"
            "&expires_in=none",
            "Create a token (no permissions needed - public data only)",
        )
        token_link.set_halign(Gtk.Align.START)

        build_group = Adw.PreferencesGroup(title="Source builds")
        self.source_row = Adw.SwitchRow(title="Allow building from source")
        self.source_row.set_subtitle("Shows source-build options after a dependency check")
        self.source_row.set_active(self.cfg.get("source_enabled", True))
        self.source_row.connect("notify::active", self._on_source_toggle)
        build_group.add(self.source_row)

        window_group = Adw.PreferencesGroup(title="Window")
        self._launch_values = [None, *[v for v, _label in LAUNCH_CHOICES]]
        self.launch_behavior_row = Adw.ComboRow(title="When launching a game")
        self.launch_behavior_row.set_model(
            Gtk.StringList.new([ASK_LABEL, *[label for _v, label in LAUNCH_CHOICES]])
        )
        self.launch_behavior_row.set_selected(self._launch_index(self.cfg.get("minimize_on_launch")))
        self.launch_behavior_row.connect("notify::selected", self._on_launch_behavior_changed)
        window_group.add(self.launch_behavior_row)
        self._close_values = [None, *[v for v, _label in CLOSE_CHOICES]]
        self.close_behavior_row = Adw.ComboRow(title="When closing the launcher")
        self.close_behavior_row.set_model(
            Gtk.StringList.new([ASK_LABEL, *[label for _v, label in CLOSE_CHOICES]])
        )
        self.close_behavior_row.set_selected(self._close_index(self.cfg.get("close_behavior")))
        self.close_behavior_row.connect("notify::selected", self._on_close_behavior_changed)
        window_group.add(self.close_behavior_row)

        backup_group = Adw.PreferencesGroup(
            title="Save backups",
            description="A copy of your emulator saves is kept every time you launch "
            f"(newest {SAVE_BACKUP_KEEP} kept). Restoring overwrites current saves.",
        )
        self.backup_group = backup_group
        self.backup_status = Gtk.Label(label="Loading…")
        self.backup_status.add_css_class("dim-label")
        self.backup_status.set_wrap(True)
        self.backup_status.set_xalign(0)
        self._backup_rows = []
        self._refresh_backups()

        src_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        src_header.set_valign(Gtk.Align.CENTER)
        lbl = Gtk.Label(label="Emulator sources")
        lbl.add_css_class("heading")
        lbl.set_xalign(0)
        lbl.set_hexpand(True)
        src_header.append(lbl)
        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", self._add)
        src_header.append(add_btn)

        self.sources_group = Adw.PreferencesGroup()
        self._source_rows = []

        self._layout = SidebarLayout(self._SIDEBAR_PAGES)
        self._layout.set_margin_top(12)
        self._layout.set_margin_bottom(12)
        self._layout.set_margin_start(12)
        self._layout.set_margin_end(12)
        data_group = Adw.PreferencesGroup(
            title="Data folder",
            description="Downloads, source checkouts and saves live here.",
        )
        data_row = Adw.ActionRow(title="Open data folder", subtitle=self.cfg.get("install_dir", ""))
        open_btn = Gtk.Button(label="Open")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", self._on_open_data_folder)
        data_row.add_suffix(open_btn)
        data_group.add(data_row)
        self._build_env_widgets()
        self._layout.add_page(
            "general", page_box([window_group, self._env_group, self._env_form, data_group])
        )
        self._layout.add_page("github", page_box([self._token_status_label, self.api_group, token_link]))
        self._layout.add_page("backups", page_box([self.backup_group, self.backup_status]))
        self._layout.add_page("sources", page_box([build_group, src_header, self.sources_group]))
        self._layout.add_page("games", self._build_games_page())
        self._pr_panel = PRBuildsPanel(on_changed=self._on_panel_changed)
        self._layout.add_page("prs", self._pr_panel)
        self._pages = self._layout.stack
        self._sidebar = self._layout.sidebar
        self._sidebar_rows = self._layout.rows

        list_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        list_page.append(self._layout)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda *_: self.close())
        close_btn.set_margin_top(12)
        close_btn.set_margin_bottom(12)
        close_btn.set_margin_start(12)
        close_btn.set_margin_end(12)
        list_page.append(close_btn)

        self.stack = Gtk.Stack()
        self.stack.add_named(list_page, "list")
        self.add_page = AddSourcePage(self._on_repo_next, cfg=self.cfg, on_cancel=self._on_add_back)
        self.stack.add_named(self.add_page, "add")

        if self.cfg.get("github_token_rejected"):
            self.show_page("github")
            # The stamp may be stale (e.g. a transient GitHub 401), so
            # re-check the saved token live instead of asserting it is
            # still bad. Probes carry no "projects" key, so a 401 here
            # never re-stamps the config file.
            self._set_token_status(
                "Saved token was rejected by GitHub - re-checking it…",
                "error",
            )
            self._auto_reverify_token()
            # don't leave the token field focused on open - unless the saved
            # token was flagged rejected, in which case focus it so Enter
            # re-verifies via the row's apply button instead of closing.
            self.connect("map", lambda *_: self.token_entry.grab_focus())
        else:
            self.show_page("general")
            self.connect("map", lambda *_: close_btn.grab_focus())

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(toolbar)
        toolbar_view.set_content(self.stack)
        self.set_content(toolbar_view)

        self._refresh()

    def _build_env_widgets(self):
        """Emulator environment editor for the General page."""
        self._env_group = Adw.PreferencesGroup(
            title="Emulator environment",
            description="Extra environment variables set on the emulator process at "
            "launch. XDG_CONFIG_HOME is reserved and managed by the launcher.",
        )
        self._env_rows = []
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._env_name_entry = Gtk.Entry()
        self._env_name_entry.set_placeholder_text("Name, e.g. EDEN_MKW_MOVIE_UNPACED")
        self._env_name_entry.set_hexpand(True)
        entry_row.append(self._env_name_entry)
        self._env_value_entry = Gtk.Entry()
        self._env_value_entry.set_placeholder_text("Value, e.g. 1")
        self._env_value_entry.set_hexpand(True)
        self._env_value_entry.connect("activate", self._on_add_env)
        entry_row.append(self._env_value_entry)
        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_box.set_halign(Gtk.Align.END)
        add_btn = Gtk.Button(label="Add variable")
        add_btn.connect("clicked", self._on_add_env)
        add_box.append(add_btn)
        self._env_error = Gtk.Label(label="")
        self._env_error.add_css_class("error")
        self._env_error.set_wrap(True)
        self._env_error.set_xalign(0)
        form.append(entry_row)
        form.append(add_box)
        form.append(self._env_error)
        self._env_form = form
        self._refresh_env()

    def _custom_env_dict(self):
        env = self.cfg.get("custom_env")
        if not isinstance(env, dict):
            env = {}
            self.cfg["custom_env"] = env
        return env

    def _refresh_env(self):
        for row in getattr(self, "_env_rows", []):
            try:
                self._env_group.remove(row)
            except Exception:
                pass
        self._env_rows = []
        for name in sorted(self._custom_env_dict()):
            value = self._custom_env_dict()[name]
            row = Adw.ActionRow(title=name, subtitle=str(value))
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.set_tooltip_text(f"Remove {name}")
            del_btn.connect("clicked", self._on_delete_env, name)
            row.add_suffix(del_btn)
            self._env_rows.append(row)
            self._env_group.add(row)
        return False

    def _on_add_env(self, *_args):
        name = self._env_name_entry.get_text().strip()
        value = self._env_value_entry.get_text().strip()
        if not name or not value:
            self._env_error.set_label("Enter a name and a value.")
            return
        if not is_valid_env_name(name):
            self._env_error.set_label("Use letters, digits and underscore, not starting with a digit.")
            return
        if name in RESERVED_ENV_KEYS:
            self._env_error.set_label("XDG_CONFIG_HOME is reserved - managed by the launcher.")
            return
        if name in self._custom_env_dict():
            self._env_error.set_label("That variable is already in the list.")
            return
        self._env_error.set_label("")
        self._custom_env_dict()[name] = value
        save_config(self.cfg)
        self._env_name_entry.set_text("")
        self._env_value_entry.set_text("")
        self._refresh_env()

    def _on_delete_env(self, _btn, name):
        self._custom_env_dict().pop(name, None)
        save_config(self.cfg)
        self._refresh_env()

    def _build_games_page(self):
        """Full game catalog: included entries plus the user's own additions."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        shipped_group = Adw.PreferencesGroup(
            title="Included games",
            description="Games the launcher already knows. Pick any of them when "
            "tagging a source or a PR build - this list keeps itself up to date.",
        )
        self._shipped_games_group = shipped_group
        self._shipped_game_rows = []
        page.append(shipped_group)
        page.append(
            Adw.PreferencesGroup(
                title="Your games",
                description="Games you added yourself. They show up everywhere "
                "the included ones do, and are never changed.",
            )
        )
        self._custom_games_group = Adw.PreferencesGroup()
        self._custom_game_rows = []
        page.append(self._custom_games_group)
        self._games_status = Gtk.Label(label="")
        self._games_status.add_css_class("dim-label")
        self._games_status.set_wrap(True)
        self._games_status.set_xalign(0)
        page.append(self._games_status)
        add_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._custom_game_id_entry = Gtk.Entry()
        self._custom_game_id_entry.set_placeholder_text("Title ID, e.g. 0400C3F00006E000")
        add_row.append(self._custom_game_id_entry)
        self._custom_game_name_entry = Gtk.Entry()
        self._custom_game_name_entry.set_placeholder_text("Name, e.g. Mario Kart World")
        self._custom_game_name_entry.set_hexpand(True)
        add_row.append(self._custom_game_name_entry)
        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_box.set_halign(Gtk.Align.END)
        add_game_btn = Gtk.Button(label="Add game")
        add_game_btn.connect("clicked", self._on_add_custom_game)
        add_box.append(add_game_btn)
        add_row.append(add_box)
        self._games_error = Gtk.Label(label="")
        self._games_error.add_css_class("error")
        self._games_error.set_wrap(True)
        self._games_error.set_xalign(0)
        add_row.append(self._games_error)
        page.append(add_row)
        self._refresh_games()
        return page

    def _refresh_games(self):
        from ..config import game_catalog

        for row in getattr(self, "_custom_game_rows", []):
            try:
                self._custom_games_group.remove(row)
            except Exception:
                pass
        self._custom_game_rows = []
        for row in getattr(self, "_shipped_game_rows", []):
            try:
                self._shipped_games_group.remove(row)
            except Exception:
                pass
        self._shipped_game_rows = []
        custom_ids = {
            (g.get("title_id") or "").upper()
            for g in (self.cfg.get("custom_games", []) or [])
            if isinstance(g, dict)
        }
        for title_id, name in game_catalog(self.cfg).items():
            if title_id in custom_ids:
                continue
            row = Adw.ActionRow(title=name, subtitle=title_id)
            row.add_css_class("dim-label")
            row.set_activatable(False)
            self._shipped_game_rows.append(row)
            self._shipped_games_group.add(row)
        custom = self.cfg.get("custom_games", []) or []
        if not custom:
            self._games_status.set_label("No custom games yet.")
        else:
            self._games_status.set_label(f"{len(custom)} custom game{'s' if len(custom) != 1 else ''}.")
        for game in custom:
            if not isinstance(game, dict):
                continue
            row = Adw.ActionRow(title=game.get("name", ""), subtitle=game.get("title_id", ""))
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.set_tooltip_text(f"Remove {game.get('name', '')}")
            del_btn.connect("clicked", self._on_delete_custom_game, game.get("title_id", ""))
            row.add_suffix(del_btn)
            self._custom_game_rows.append(row)
            self._custom_games_group.add(row)
        return False

    def _on_add_custom_game(self, *_args):
        from ..config import game_catalog, normalize_title_id

        title_id = normalize_title_id(self._custom_game_id_entry.get_text())
        name = self._custom_game_name_entry.get_text().strip()
        if title_id is None:
            self._games_error.set_label("That is not a 16-digit hex title ID.")
            return
        if not name:
            self._games_error.set_label("Give the game a name.")
            return
        if title_id in game_catalog(self.cfg):
            self._games_error.set_label("That game is already in the list.")
            return
        self._games_error.set_label("")
        self.cfg.setdefault("custom_games", []).append({"title_id": title_id, "name": name})
        save_config(self.cfg)
        self._custom_game_id_entry.set_text("")
        self._custom_game_name_entry.set_text("")
        self._refresh_games()

    def _on_delete_custom_game(self, _btn, title_id):
        self.cfg["custom_games"] = [
            g
            for g in (self.cfg.get("custom_games", []) or [])
            if not (isinstance(g, dict) and (g.get("title_id") or "").upper() == title_id)
        ]
        save_config(self.cfg)
        self._refresh_games()

    def _on_open_data_folder(self, *_args):
        from ..tasks import _on_host

        folder = Path(self.cfg["install_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(_on_host(["xdg-open", str(folder)]))

    def _on_panel_changed(self):
        # The panel saves straight to disk with its own cfg copy; pull the
        # fresh projects list into ours (in place - open editors may hold
        # the same object) so a later Settings save cannot clobber a PR
        # entry with a stale list.
        try:
            self.cfg["projects"] = load_config().get("projects", self.cfg.get("projects", []))
        except Exception:
            pass
        if self.on_changed:
            self.on_changed()

    def show_page(self, name):
        """Select a sidebar page by name (screenshot hook)."""
        self._layout.show_page(name)

    def _launch_index(self, value):
        return self._launch_values.index(value) if value in self._launch_values else 0

    def _close_index(self, value):
        return self._close_values.index(value) if value in self._close_values else 0

    def _on_launch_behavior_changed(self, row, _pspec):
        self.cfg["minimize_on_launch"] = self._launch_values[row.get_selected()]
        save_config(self.cfg)

    def _on_close_behavior_changed(self, row, _pspec):
        self.cfg["close_behavior"] = self._close_values[row.get_selected()]
        save_config(self.cfg)

    def _on_source_toggle(self, row, _pspec):
        if getattr(self, "_toggling", False):
            return
        self.cfg["source_enabled"] = row.get_active()
        save_config(self.cfg)
        if self.on_changed:
            self.on_changed()
        if row.get_active():
            threading.Thread(target=self._check_deps_on_enable, daemon=True).start()

    def _check_deps_on_enable(self):
        from ..tasks import check_build_deps

        try:
            missing = [(n, d) for n, ok, d, req in check_build_deps() if req and not ok]
        except Exception:
            return
        if missing:
            text = "\n".join(f"- {n}: {d}" for n, d in missing)
            GLib.idle_add(self._show_enable_deps_dialog, text)

    def _show_enable_deps_dialog(self, text):
        from .deps_dialog import DepsDialog

        def revert():
            self.cfg["source_enabled"] = False
            save_config(self.cfg)
            self._toggling = True
            try:
                self.source_row.set_active(False)
            finally:
                self._toggling = False
            if self.on_changed:
                self.on_changed()

        DepsDialog(self, text, on_ignore=revert).present()

    def _set_token_status(self, msg, kind="", timeout=0):
        label = self._token_status_label
        for cls in ("success", "warning", "error"):
            label.remove_css_class(cls)
        self._token_status_seq = getattr(self, "_token_status_seq", 0) + 1
        seq = self._token_status_seq
        if kind:
            label.add_css_class(kind)
        if msg:
            label.set_label(msg)
            label.set_visible(True)
        else:
            label.set_visible(False)
            return
        if timeout:
            GLib.timeout_add_seconds(timeout, lambda: self._clear_token_status(seq))

    def _clear_token_status(self, seq):
        if getattr(self, "_token_status_seq", 0) == seq:
            self._token_status_label.set_visible(False)
        return False

    @staticmethod
    def _connect_token_activate(row):
        """Enter in the token field verifies, like the tick button.

        Adw does not forward the inner Gtk.Text activation to the row's
        apply signal, so without this Enter falls through to whichever
        button has focus (previously Close, which just closed Settings).
        """
        from gi.repository import Gtk

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

        text = find_text(row)
        if text is not None:
            text.connect("activate", lambda *_: row.emit("apply"))

    @staticmethod
    def _reveal_on_focus(row):
        """Show the token while editing; hide it again on focus out."""
        from gi.repository import Gtk

        def find_entry(widget):
            if isinstance(widget, Gtk.PasswordEntry):
                return widget
            child = widget.get_first_child()
            while child is not None:
                found = find_entry(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None

        entry = find_entry(row)
        if entry is None:
            return
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: entry.set_show_password(True))
        focus.connect("leave", lambda *_: entry.set_show_password(False))
        row.add_controller(focus)

    def _on_token_apply(self, _row):
        token = self.token_entry.get_text().strip()
        if not token:
            self.cfg["github_token"] = None
            self.cfg["github_token_rejected"] = False
            save_config(self.cfg)
            self._set_token_status("")
            return
        self._set_token_status("Verifying...")
        threading.Thread(target=self._verify_token, args=(token,), daemon=True).start()

    def _verify_token(self, token, auto=False):
        from .. import tasks

        try:
            tasks.api_get_json("https://api.github.com/rate_limit", {"github_token": token}, timeout=20)
            GLib.idle_add(self._token_verified, token, auto)
        except Exception as e:
            GLib.idle_add(self._token_failed, token, e, auto)

    def _auto_reverify_token(self):
        """Re-check a flagged token on settings open (the stamp may be stale)."""
        token = self.token_entry.get_text().strip()
        if not token:
            return
        threading.Thread(target=self._verify_token, args=(token, True), daemon=True).start()

    def _token_verified(self, token, _auto=False):
        if self.token_entry.get_text().strip() != token:
            return
        from .. import tasks

        tasks.token_accepted()
        self.cfg["github_token"] = token
        self.cfg["github_token_rejected"] = False
        save_config(self.cfg)
        self._set_token_status("Verified and saved.", "success", timeout=8)

    def _token_failed(self, token, error, auto=False):
        if self.token_entry.get_text().strip() != token:
            return
        from urllib.error import HTTPError

        if isinstance(error, HTTPError) and error.code in (401, 403):
            self._set_token_status("Invalid token - check it and try again.", "error")
        elif auto:
            self._set_token_status("Couldn't reach GitHub to re-check the saved token - kept it.", "warning")
        else:
            reason = getattr(error, "reason", error)
            self.cfg["github_token"] = token
            save_config(self.cfg)
            self._set_token_status(f"Could not verify ({reason}); saved anyway.", "warning")

    def _refresh_backups(self):
        self.backup_status.set_label("Loading…")

        def worker():
            try:
                rows = list_save_backups(self.cfg)
            except Exception as e:  # never break the dialog on a stat failure
                rows, err = [], str(e)
            else:
                err = ""
            GLib.idle_add(self._render_backups, rows, err)

        threading.Thread(target=worker, daemon=True).start()

    def _render_backups(self, rows, err=""):
        if getattr(self, "backup_group", None) is None:
            return False
        for widget in getattr(self, "_backup_rows", []):
            try:
                self.backup_group.remove(widget)
            except Exception:
                pass
        self._backup_rows = []
        if err:
            self.backup_status.set_label(f"Could not read backups: {err}")
        elif not rows:
            self.backup_status.set_label("No backups yet - one is kept every time you launch a game.")
        else:
            total = sum(r.get("size") or 0 for r in rows)
            n = len(rows)
            self.backup_status.set_label(
                f"{n} backup{'s' if n != 1 else ''}, {format_size(total)} total. "
                "Deleted backups cannot be recovered."
            )
            for row in rows:
                widget = self._backup_row(row)
                self._backup_rows.append(widget)
                self.backup_group.add(widget)
        return False

    def _backup_row(self, row):
        size = format_size(row.get("size")) if row.get("size") is not None else "unknown size"
        action = Adw.ActionRow(title=format_backup_name(row.get("name", "")), subtitle=size)
        restore = Gtk.Button(label="Restore")
        restore.set_valign(Gtk.Align.CENTER)
        restore.set_tooltip_text("Overwrite current saves with this backup")
        restore.connect("clicked", self._on_backup_restore_clicked, row)
        action.add_suffix(restore)
        delete = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        delete.add_css_class("flat")
        delete.set_valign(Gtk.Align.CENTER)
        delete.set_tooltip_text(f"Delete backup {row.get('name', '')}")
        delete.connect("clicked", self._on_backup_delete_clicked, row)
        action.add_suffix(delete)
        return action

    def _on_backup_restore_clicked(self, _btn, row):
        dlg = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading=f"Restore saves from {row.get('name', 'backup')}?",
            body="Overwrites your current saves with this backup. "
            "Your current state was itself backed up at the last launch.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("restore", "Restore")
        dlg.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def on_response(_dlg, response):
            if response == "restore":
                self._do_backup_restore(row)
            _dlg.close()

        dlg.connect("response", on_response)
        dlg.present()

    def _do_backup_restore(self, row):
        self.backup_status.set_label(f"Restoring {row.get('name', '')}…")

        def worker():
            err = restore_save_backup(self.cfg, row.get("name"))
            GLib.idle_add(self._after_backup_restore, err)

        threading.Thread(target=worker, daemon=True).start()

    def _after_backup_restore(self, err):
        if err:
            self.backup_status.set_label(err)
        else:
            self._refresh_backups()
        return False

    def _on_backup_delete_clicked(self, _btn, row):
        self.backup_status.set_label(f"Deleting {row.get('name', '')}…")

        def worker():
            delete_save_backup(self.cfg, row.get("name"))
            GLib.idle_add(self._refresh_backups)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh(self):
        for row in getattr(self, "_source_rows", []):
            try:
                self.sources_group.remove(row)
            except Exception:
                pass
        self._source_rows = []
        for p in self.cfg.get("projects", []):
            if p.get("is_pr"):
                # one-off PR builds are managed in the PR Builds dialog
                continue
            try:
                from ..config import best_for_summary, project_tooltip
            except Exception:
                best_for_summary = project_tooltip = None
            summary = best_for_summary(p) if best_for_summary else ""
            url = p.get("source_url", "")
            subtitle = f"{summary} - {url}" if summary else url
            row = Adw.ActionRow(title=p.get("name", p.get("id", "")), subtitle=subtitle)
            if project_tooltip:
                try:
                    row.set_tooltip_text(project_tooltip(p) or None)
                except Exception:
                    pass
            row.set_activatable(True)
            row.connect("activated", self._on_row_activated)
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
            edit_btn = Gtk.Button.new_from_icon_name("document-edit-symbolic")
            edit_btn.add_css_class("flat")
            edit_btn.set_valign(Gtk.Align.CENTER)
            edit_btn.set_tooltip_text(f"Edit {p.get('name', p.get('id', ''))}")
            edit_btn.connect("clicked", self._on_edit_clicked, p)
            row.add_suffix(edit_btn)
            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.set_tooltip_text(f"Remove {p.get('name', p.get('id', ''))}")
            del_btn.connect("clicked", self._on_delete_clicked, p)
            row.add_suffix(del_btn)
            row.project = p
            self._source_rows.append(row)
            self.sources_group.add(row)

    def _on_link_clicked(self, _btn, url):
        if url:
            Gtk.show_uri(self, url, 0)

    def _add(self, *_args):
        self.add_page.reset()
        self.stack.set_visible_child_name("add")

    def _on_add_back(self):
        self.stack.set_visible_child_name("list")

    def _on_repo_next(self, url, name, branches, releases_url, _err, default_branch=None):
        prefill = {
            "source_url": url,
            "name": name,
            "branches": branches,
            "releases_url": releases_url,
            "branch": branches[0] if branches else "master",
            "default_branch": default_branch,
        }

        def save(project, old_id):
            self.cfg.setdefault("projects", []).append(project)
            save_config(self.cfg)
            self.add_page.url_entry.set_text("")
            self.add_page.reset()
            self.stack.set_visible_child_name("list")
            self._refresh()
            if self.on_changed:
                self.on_changed()

        ProjectEditDialog(self, None, save, prefill=prefill, cfg=self.cfg).present()

    def _on_row_activated(self, row, *_args):
        proj = getattr(row, "project", None)
        if proj:
            self._edit_project(proj)

    def _on_edit_clicked(self, _btn, proj):
        if proj:
            self._edit_project(proj)

    def _edit_project(self, proj):
        # Open immediately; the branch list fills in when the API responds
        # instead of making the dialog wait on the network.
        dialog = self._open_source_editor(proj)

        def fetch():
            try:
                branches = list_branches(proj.get("source_url", ""), cfg=self.cfg) or []
            except Exception:
                branches = []
            if not branches and proj.get("branch"):
                branches = [proj["branch"]]
            GLib.idle_add(self._update_editor_branches, dialog, branches)

        threading.Thread(target=fetch, daemon=True).start()

    @staticmethod
    def _update_editor_branches(dialog, branches):
        try:
            dialog.update_branches(branches)
        except Exception:
            pass
        return False

    def _save_project(self, project, old_id):
        projects = self.cfg.get("projects", [])
        for i, p in enumerate(projects):
            if p.get("id") == old_id:
                projects[i] = project
                break
        save_config(self.cfg)
        self._refresh()
        if self.on_changed:
            self.on_changed()

    def _open_source_editor(self, proj, branches=None, default_branch=None, tab=None):
        prefill = {"branches": branches} if branches else {}
        if default_branch:
            prefill["default_branch"] = default_branch
        dialog = ProjectEditDialog(
            self,
            proj,
            self._save_project,
            prefill=prefill,
            cfg=self.cfg,
            on_storage_changed=self.on_changed,
        )
        if tab and getattr(dialog, "_layout", None) is not None:
            dialog.show_page(tab)
        dialog.present()
        return dialog

    def open_source_editor(self, pid, tab=None):
        """Open a source's editor without fetching branches (screenshot hook)."""
        proj = next((p for p in self.cfg.get("projects", []) if p.get("id") == pid), None)
        if proj is not None:
            self._open_source_editor(proj, tab=tab)

    def _on_delete_clicked(self, _btn, proj):
        name = proj.get("name", proj.get("id", "this source"))
        dlg = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading=f"Remove {name}?",
            body="Removes the source and its downloads, source checkout, desktop shortcut and emulator settings.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def on_response(_dlg, response):
            if response == "remove":
                self._remove_project(proj)
            _dlg.close()

        dlg.connect("response", on_response)
        dlg.present()

    def _remove_project(self, proj):
        if not proj:
            return
        pid = proj.get("id")
        remove_project_data(self.cfg, pid)
        self.cfg["projects"] = [p for p in self.cfg.get("projects", []) if p.get("id") != pid]
        if pid and pid not in self.cfg.get("deleted_projects", []):
            self.cfg.setdefault("deleted_projects", []).append(pid)
        if proj.get("remote") and proj.get("id") not in self.cfg.get("hidden_sources", []):
            self.cfg.setdefault("hidden_sources", []).append(proj["id"])
        if self.cfg.get("project") == proj.get("id"):
            projects = self.cfg["projects"]
            self.cfg["project"] = projects[0].get("id") if projects else None
        save_config(self.cfg)
        self._refresh()
        if self.on_changed:
            self.on_changed()
