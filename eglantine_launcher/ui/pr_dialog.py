import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

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
)


class PREditDialog(Adw.Window):
    """Add or edit a one-off PR build. Name defaults to the PR title."""

    def __init__(self, parent, project=None, on_save=None, cfg=None):
        super().__init__(
            transient_for=parent, modal=True, title="Edit PR build" if project else "New PR build"
        )
        self.set_default_size(480, 320)
        self.on_save = on_save
        self.project = dict(project) if project else {}
        self._cfg = cfg or {}
        self._name_touched = bool(self.project.get("name"))
        self._lookup_seq = 0
        self._fetching = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

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
        box.append(group)

        self.status_label = Gtk.Label(label="")
        self.status_label.set_wrap(True)
        self.status_label.add_css_class("dim-label")
        self.status_label.set_xalign(0)
        box.append(self.status_label)

        if self.project.get("pr_url"):
            link = Gtk.LinkButton.new_with_label(self.project["pr_url"], "Open PR page")
            link.set_halign(Gtk.Align.START)
            box.append(link)

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

    def _on_name_changed(self, *_args):
        self._name_touched = True

    def _on_url_changed(self, *_args):
        self._schedule_lookup()

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
        self._fetching = True
        self.save_btn.set_sensitive(False)
        self._set_status("Looking up PR...")
        threading.Thread(target=self._save_lookup, args=(name, url), daemon=True).start()

    def _save_lookup(self, name, url):
        try:
            details = fetch_pr_info(url, self._cfg)
        except Exception as e:
            GLib.idle_add(self._save_failed, f"Could not read PR: {e}")
            return
        try:
            project = make_pr_project(name, url, details, existing=self.project or None)
        except Exception as e:
            GLib.idle_add(self._save_failed, str(e))
            return
        GLib.idle_add(self._save_done, project)

    def _save_failed(self, message):
        self._fetching = False
        self.save_btn.set_sensitive(True)
        self._set_status(message)
        return False

    def _save_done(self, project):
        self._fetching = False
        old_id = self.project.get("id")
        if self.on_save:
            self.on_save(project, old_id)
        self.close()
        return False


class PRBuildsDialog(Adw.Window):
    """List of one-off PR builds: launch, edit, delete."""

    def __init__(self, parent, on_changed=None, on_launch=None):
        super().__init__(transient_for=parent, modal=True, title="PR Builds")
        self.set_default_size(560, 520)
        self.on_changed = on_changed
        self.on_launch = on_launch
        self.cfg = load_config()

        toolbar = Adw.HeaderBar()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

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
        content.append(header)

        hint = Gtk.Label(
            label="Build any GitHub or Forgejo PR by link. Names default to the PR title. "
            "Each build is isolated and appears in the main Project list."
        )
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        hint.set_xalign(0)
        content.append(hint)

        self.pr_group = Adw.PreferencesGroup()
        self._rows = []
        content.append(self.pr_group)

        self.empty_label = Gtk.Label(label="No PR builds yet. Click Add to build your first PR.")
        self.empty_label.add_css_class("dim-label")
        self.empty_label.set_wrap(True)
        content.append(self.empty_label)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(content)
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

        self._refresh()

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
        state = p.get("pr_state", "open")
        if state in ("merged", "closed"):
            badge = Gtk.Label(label="Merged" if state == "merged" else "Closed")
            badge.add_css_class("dim-label")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)
        launch = Gtk.Button(label="Launch")
        launch.add_css_class("suggested-action")
        launch.set_valign(Gtk.Align.CENTER)
        launch.set_tooltip_text(f"Build and launch {p.get('name', '')}")
        launch.connect("clicked", self._on_launch_clicked, p.get("id"))
        row.add_suffix(launch)
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

        PREditDialog(self, None, save, cfg=self.cfg).present()

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

        PREditDialog(self, proj, save, cfg=self.cfg).present()

    def _on_launch_clicked(self, _btn, pid):
        if self.on_launch:
            self.on_launch(pid)
        self.close()

    def _on_delete_clicked(self, _btn, proj):
        name = proj.get("name", proj.get("id", "this PR build"))
        dlg = Adw.MessageDialog(
            transient_for=self,
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
