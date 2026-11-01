import argparse
import sys

from . import APP_ID


def main() -> int:
    parser = argparse.ArgumentParser(description="Eglantine launcher")
    parser.add_argument("--auto", action="store_true", help="Check updates and launch automatically")
    parser.add_argument("--launch", action="store_true", help="Alias for --auto")
    args = parser.parse_args()

    auto = args.auto or args.launch

    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio
    except ImportError:
        print("GTK 4 / libadwaita not found. Install python3-gobject and libadwaita.", file=sys.stderr)
        return 1

    from .ui.window import LauncherWindow

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
            self._window = None
            self._auto = auto
            self.connect("activate", self._on_activate)

        def _on_activate(self, app):
            if self._window is None:
                self._window = LauncherWindow(app, auto=self._auto)
            self._window.present()

    app = App()
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
