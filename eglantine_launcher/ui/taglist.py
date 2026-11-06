"""Shared label + hover-note tag list editor.

Used by the PR build dialog and the Tags tab of the source editor so the
row layout, slug rules and collection stay identical in both.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk


def slugify(label):
    """URL-ish id derived from a tag label."""
    return "".join(c.lower() if c.isalnum() else "-" for c in label).strip("-")


class TagListEditor(Gtk.Box):
    """Vertical list of (label, hover-note) rows with add/remove."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._entries = []

    def add_tag(self, label="", tooltip=""):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label_entry = Gtk.Entry()
        label_entry.set_placeholder_text("Label, e.g. fix")
        label_entry.set_text(label or "")
        label_entry.set_width_chars(12)
        tip_entry = Gtk.Entry()
        tip_entry.set_placeholder_text("Hover note, e.g. Fixes mods on NUP")
        tip_entry.set_text(tooltip or "")
        tip_entry.set_hexpand(True)
        del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        del_btn.add_css_class("flat")
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.connect("clicked", self._on_delete_row, row)
        row.append(label_entry)
        row.append(tip_entry)
        row.append(del_btn)
        self._entries.append((label_entry, tip_entry, row))
        self.append(row)
        return row

    def _on_delete_row(self, _btn, row):
        self._entries = [e for e in self._entries if e[2] is not row]
        self.remove(row)

    def collect(self):
        """Tag dicts for non-empty rows: [{id, label, tooltip}]."""
        tags = []
        for label_entry, tip_entry, _row in self._entries:
            label = label_entry.get_text().strip()
            if not label:
                continue
            tags.append(
                {"id": slugify(label) or "tag", "label": label, "tooltip": tip_entry.get_text().strip()}
            )
        return tags

    def __len__(self):
        return len(self._entries)
