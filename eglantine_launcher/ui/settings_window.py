import threading
import uuid
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..config import load as load_config
from ..config import save as save_config
from ..tasks import fetch_repo_name, infer_releases_url, list_branches


def _slug(name):
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-") or uuid.uuid4().hex[:8]


class AddSourcePage(Gtk.Box):
    """Step 1: paste a GitHub / Forgejo repo link."""

    def __init__(self, on_next, cfg=None):
        self.cfg = cfg or {}
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.on_next = on_next

        group = Adw.PreferencesGroup(
            title="Add emulator source", description="Paste a GitHub or Forgejo repository link."
        )
        self.url_entry = Adw.EntryRow(title="Repository link")
        self.url_entry.set_text("")
        group.add(self.url_entry)
        self.append(group)

        self.error_label = Gtk.Label(label="")
        self.error_label.add_css_class("error")
        self.error_label.set_wrap(True)
        self.append(self.error_label)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", self._next)
        btn_box.append(self.next_btn)
        self.append(btn_box)

    def _next(self, *_args):
        url = self.url_entry.get_text().strip()
        if not url:
            self.error_label.set_label("Enter a repository link.")
            return
        self.next_btn.set_sensitive(False)
        self.error_label.set_label("Looking up repository...")
        threading.Thread(target=self._lookup, args=(url,), daemon=True).start()

    def _lookup(self, url):
        try:
            name = fetch_repo_name(url, self.cfg) or ""
            branches = list_branches(url, cfg=self.cfg)
            releases_url = infer_releases_url(url)
            GLib.idle_add(self.on_next, url, name, branches, releases_url, "")
        except Exception as e:
            GLib.idle_add(self.error_label.set_label, f"Could not read repository: {e}")
            GLib.idle_add(self.next_btn.set_sensitive, True)


class ProjectEditDialog(Adw.Window):
    """Step 2 (add) or full edit dialog. No ID field, branch is a dropdown."""

    def __init__(self, parent, project=None, on_save=None, prefill=None):
        super().__init__(transient_for=parent, modal=True, title="Edit source" if project else "New source")
        self.set_default_size(480, 420)
        self.on_save = on_save
        self.project = dict(project) if project else {}
        prefill = prefill or {}

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
        box.append(group)

        group2 = Adw.PreferencesGroup()
        self.branch_row = Adw.ComboRow(title="Branch")
        branches = prefill.get("branches", []) or []
        current = self.project.get("branch", "") or prefill.get("branch", "")
        if current and current not in branches:
            branches = [current] + branches
        if not branches:
            branches = [current or "master"]
        self._branches = branches
        self.branch_row.set_model(Gtk.StringList.new(branches))
        self.branch_row.set_selected(branches.index(current) if current in branches else 0)
        group2.add(self.branch_row)

        self.releases_entry = Adw.EntryRow(title="Releases URL")
        self.releases_entry.set_text(self.project.get("releases_url") or prefill.get("releases_url") or "")
        group2.add(self.releases_entry)

        self.prerelease_row = Adw.SwitchRow(title="Include pre-releases")
        self.prerelease_row.set_active(
            self.project.get("include_prerelease", prefill.get("include_prerelease", True))
        )
        group2.add(self.prerelease_row)

        self.exclude_entry = Adw.EntryRow(title="Exclude keywords")
        self.exclude_entry.set_text(
            self.project.get("exclude_keywords", prefill.get("exclude_keywords", "")) or ""
        )
        group2.add(self.exclude_entry)
        box.append(group2)

        hint = Gtk.Label(label="Releases auto-fill from the repo link. Leave empty for source-only forks.")
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        box.append(hint)

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
        }
        if self.on_save:
            self.on_save(project, old_id)
        self.close()


class SettingsWindow(Adw.Window):
    def __init__(self, parent, on_changed=None):
        super().__init__(transient_for=parent, modal=True, title="Settings")
        self.set_default_size(620, 720)
        self.on_changed = on_changed
        self.cfg = load_config()

        toolbar = Adw.HeaderBar()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

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
        self.token_entry.connect("changed", self._on_token_changed)
        self._reveal_on_focus(self.token_entry)
        self._verify_timer = None
        self.api_group.add(self.token_entry)
        box.append(self.api_group)

        token_link = Gtk.LinkButton.new_with_label(
            "https://github.com/settings/personal-access-tokens/new"
            "?name=Eglantine%20Launcher"
            "&description=Release%20checks%20for%20public%20repos"
            "&expires_in=none",
            "Create a token (no permissions needed - public data only)",
        )
        token_link.set_halign(Gtk.Align.START)
        box.append(token_link)

        build_group = Adw.PreferencesGroup(title="Source builds")
        self.source_row = Adw.SwitchRow(title="Allow building from source")
        self.source_row.set_subtitle("Shows source-build options after a dependency check")
        self.source_row.set_active(self.cfg.get("source_enabled", True))
        self.source_row.connect("notify::active", self._on_source_toggle)
        build_group.add(self.source_row)
        box.append(build_group)

        lbl = Gtk.Label(label="Emulator sources")
        lbl.add_css_class("heading")
        lbl.set_xalign(0)
        box.append(lbl)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.add_css_class("boxed-list")

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.list_box)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(240)
        box.append(scrolled)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_btn = Gtk.Button(label="Add")
        add_btn.connect("clicked", self._add)
        btn_box.append(add_btn)
        edit_btn = Gtk.Button(label="Edit")
        edit_btn.connect("clicked", self._edit)
        btn_box.append(edit_btn)
        remove_btn = Gtk.Button(label="Remove")
        remove_btn.connect("clicked", self._remove)
        btn_box.append(remove_btn)
        box.append(btn_box)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda *_: self.close())
        box.append(close_btn)
        # don't leave the token field focused on open
        self.connect("map", lambda *_: close_btn.grab_focus())

        self.stack = Gtk.Stack()
        self.stack.add_named(box, "list")
        self.add_page = AddSourcePage(self._on_repo_next, cfg=self.cfg)
        self.stack.add_named(self.add_page, "add")

        if self.cfg.get("github_token"):
            self._set_token_status("Checking saved token...")
            saved = self.cfg["github_token"]
            threading.Thread(target=self._verify_token, args=(saved, False), daemon=True).start()

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(toolbar)
        toolbar_view.set_content(self.stack)
        self.set_content(toolbar_view)

        self._refresh()

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

    def _set_token_status(self, msg):
        if msg:
            self.api_group.set_description(self._api_base_desc + "\n\nStatus: " + msg)
        else:
            self.api_group.set_description(self._api_base_desc)

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
            save_config(self.cfg)
            self._set_token_status("")
            return
        self._set_token_status("Verifying...")
        threading.Thread(target=self._verify_token, args=(token, True), daemon=True).start()

    def _on_token_changed(self, _row):
        if self._verify_timer:
            GLib.source_remove(self._verify_timer)
        self._verify_timer = GLib.timeout_add(800, self._verify_typed_token)

    def _verify_typed_token(self):
        self._verify_timer = None
        token = self.token_entry.get_text().strip()
        if token and token != (self.cfg.get("github_token") or ""):
            self._set_token_status("Verifying...")
            threading.Thread(target=self._verify_token, args=(token, False), daemon=True).start()
        return False

    def _verify_token(self, token, save):
        from .. import tasks

        try:
            tasks.api_get_json("https://api.github.com/rate_limit", {"github_token": token}, timeout=20)
            GLib.idle_add(self._token_verified, token, save)
        except Exception as e:
            GLib.idle_add(self._token_failed, token, e, save)

    def _token_verified(self, token, save):
        if self.token_entry.get_text().strip() != token:
            return
        from .. import tasks

        tasks.token_accepted()
        if save:
            self.cfg["github_token"] = token
            save_config(self.cfg)
            self._set_token_status("Verified and saved.")
        elif token == (self.cfg.get("github_token") or ""):
            self._set_token_status("Verified.")
        else:
            self._set_token_status("Looks valid - press Enter to save.")

    def _token_failed(self, token, error, save):
        if self.token_entry.get_text().strip() != token:
            return
        from urllib.error import HTTPError

        if isinstance(error, HTTPError) and error.code in (401, 403):
            self._set_token_status("Invalid token - check it and try again.")
        else:
            reason = getattr(error, "reason", error)
            self._set_token_status(f"Could not verify ({reason}).")
            if save:
                self.cfg["github_token"] = token
                save_config(self.cfg)
                self._set_token_status(f"Could not verify ({reason}); saved anyway.")

    def _refresh(self):
        while True:
            row = self.list_box.get_row_at_index(0)
            if row is None:
                break
            self.list_box.remove(row)
        for p in self.cfg.get("projects", []):
            row = Adw.ActionRow(title=p.get("name", p.get("id", "")), subtitle=p.get("source_url", ""))
            url = p.get("source_url", "")
            globe = Path(__file__).resolve().parents[1] / "icons" / "globe-symbolic.svg"
            if globe.exists():
                icon = Gtk.Image.new_from_file(str(globe))
            else:
                icon = Gtk.Image.new_from_icon_name("go-next-symbolic")
            link_btn = Gtk.Button()
            link_btn.set_child(icon)
            link_btn.add_css_class("flat")
            link_btn.set_tooltip_text(f"Open {url}")
            link_btn.connect("clicked", self._on_link_clicked, url)
            row.add_suffix(link_btn)
            row.project = p
            self.list_box.append(row)

    def _on_link_clicked(self, _btn, url):
        if url:
            Gtk.show_uri(self, url, 0)

    def _selected_project(self):
        row = self.list_box.get_selected_row()
        return getattr(row, "project", None) if row else None

    def _add(self, *_args):
        self.stack.set_visible_child_name("add")

    def _on_repo_next(self, url, name, branches, releases_url, _err):
        prefill = {
            "source_url": url,
            "name": name,
            "branches": branches,
            "releases_url": releases_url,
            "branch": branches[0] if branches else "master",
        }

        def save(project, old_id):
            self.cfg.setdefault("projects", []).append(project)
            save_config(self.cfg)
            self.stack.set_visible_child_name("list")
            self._refresh()
            if self.on_changed:
                self.on_changed()

        ProjectEditDialog(self, None, save, prefill=prefill).present()

    def _edit(self, *_args):
        proj = self._selected_project()
        if not proj:
            return

        def open_editor(branches):
            def save(project, old_id):
                projects = self.cfg.get("projects", [])
                for i, p in enumerate(projects):
                    if p.get("id") == old_id:
                        projects[i] = project
                        break
                save_config(self.cfg)
                self._refresh()
                if self.on_changed:
                    self.on_changed()

            prefill = {"branches": branches} if branches else {}
            ProjectEditDialog(self, proj, save, prefill=prefill).present()

        def fetch():
            try:
                branches = list_branches(proj.get("source_url", ""), cfg=self.cfg) or []
            except Exception:
                branches = []
            if not branches and proj.get("branch"):
                branches = [proj["branch"]]
            GLib.idle_add(open_editor, branches)

        threading.Thread(target=fetch, daemon=True).start()

    def _remove(self, *_args):
        proj = self._selected_project()
        if not proj:
            return
        self.cfg["projects"] = [p for p in self.cfg.get("projects", []) if p.get("id") != proj.get("id")]
        if proj.get("remote") and proj.get("id") not in self.cfg.get("hidden_sources", []):
            self.cfg.setdefault("hidden_sources", []).append(proj["id"])
        if self.cfg.get("project") == proj.get("id"):
            projects = self.cfg["projects"]
            self.cfg["project"] = projects[0].get("id") if projects else None
        save_config(self.cfg)
        self._refresh()
        if self.on_changed:
            self.on_changed()
