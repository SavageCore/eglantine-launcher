"""Shared iOS-style sidebar layout: categories left, content right.

Used by the Settings window and the source editor so both stay in sync.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk


def page_box(children):
    """Vertical page content with breathing room between groups."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for child in children:
        box.append(child)
    return box


class SidebarLayout(Gtk.Box):
    """Categories ListBox on the left, a Stack of pages on the right."""

    def __init__(self, pages):
        """pages: list of (key, label) shown in the sidebar, in order."""
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._keys = [key for key, _label in pages]

        sidebar = Gtk.ListBox()
        sidebar.add_css_class("navigation-sidebar")
        sidebar.set_selection_mode(Gtk.SelectionMode.SINGLE)
        sidebar.set_vexpand(True)
        self._rows = []
        for _key, label in pages:
            row = Gtk.ListBoxRow()
            text = Gtk.Label(label=label)
            text.set_xalign(0)
            text.set_margin_top(8)
            text.set_margin_bottom(8)
            text.set_margin_start(8)
            text.set_margin_end(8)
            row.set_child(text)
            sidebar.append(row)
            self._rows.append(row)
        sidebar.connect("row-selected", self._on_row_selected)
        self.sidebar = sidebar
        self.append(sidebar)

        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.append(self.stack)

    def add_page(self, key, widget):
        """Add a page, wrapped in its own scroller."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(widget)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.stack.add_named(scrolled, key)

    def show_page(self, key):
        """Select the sidebar row for key (no-op for unknown keys)."""
        if key in self._keys:
            self.sidebar.select_row(self._rows[self._keys.index(key)])

    @property
    def rows(self):
        return list(self._rows)

    @property
    def visible_page(self):
        return self.stack.get_visible_child_name()

    def _on_row_selected(self, _box, row):
        if row is not None:
            index = self._rows.index(row)
            self.stack.set_visible_child_name(self._keys[index])
