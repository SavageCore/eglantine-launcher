import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


class DepsDialog(Adw.Window):
    def __init__(self, parent, text, on_ignore=None):
        self.on_ignore = on_ignore
        super().__init__(transient_for=parent, modal=True, title="Missing build dependencies")
        self.set_default_size(520, 300)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        label = Gtk.Label(label="These required tools are missing:")
        label.set_xalign(0)
        box.append(label)

        docs_link = Gtk.LinkButton.new_with_label(
            "https://github.com/SavageCore/eglantine-launcher/blob/main/docs/build-windows.md",
            "Windows setup guide",
        )
        docs_link.set_halign(Gtk.Align.START)
        box.append(docs_link)

        self.view = Gtk.TextView()
        self.view.set_editable(False)
        self.view.set_monospace(True)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.view.get_buffer().set_text(text)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(self.view)
        scrolled.set_vexpand(True)
        box.append(scrolled)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        copy_btn = Gtk.Button(label="Copy")
        copy_btn.connect("clicked", self._on_copy)
        btn_box.append(copy_btn)
        if self.on_ignore:
            ignore_btn = Gtk.Button(label="Limit to downloads")
            ignore_btn.set_tooltip_text("Hide source builds. Re-enable any time in Settings.")
            ignore_btn.connect("clicked", self._on_ignore)
            btn_box.append(ignore_btn)
        close_btn = Gtk.Button(label="Close")
        close_btn.add_css_class("suggested-action")
        close_btn.connect("clicked", lambda *_: self.close())
        btn_box.append(close_btn)
        box.append(btn_box)

        self.set_content(box)

    def _on_copy(self, *_args):
        buf = self.view.get_buffer()
        display = self.get_display()
        if display:
            display.get_clipboard().set(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))

    def _on_ignore(self, *_args):
        if self.on_ignore:
            self.on_ignore()
        self.close()
