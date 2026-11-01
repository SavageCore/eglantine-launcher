import argparse
import sys

from . import APP_ID


def _ensure_win32_pixbuf():
    """Generate a gdk-pixbuf loaders cache on first launch (frozen Windows).

    Absolute build-machine paths can't be shipped, so the cache is generated
    into the user data dir once (and regenerated when loaders change).
    """
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("win"):
        return
    import os
    from pathlib import Path

    base = Path(sys._MEIPASS)
    loaders_dirs = sorted(base.glob("lib/gdk-pixbuf-2.0/*/loaders"))
    if not loaders_dirs:
        return
    loaders_dir = loaders_dirs[0]
    query = base / "gdk-pixbuf-query-loaders.exe"
    if not query.exists():
        return
    import hashlib

    digest = hashlib.sha256("|".join(sorted(p.name for p in loaders_dir.glob("*.dll"))).encode()).hexdigest()[
        :16
    ]
    cache_dir = Path.home() / ".local" / "share" / "eglantine-launcher" / "pixbuf"
    cache = cache_dir / f"loaders-{digest}.cache"
    if not cache.exists():
        import subprocess

        cache_dir.mkdir(parents=True, exist_ok=True)
        with open(cache, "wb") as f:
            subprocess.run([str(query), str(loaders_dir)], stdout=f, check=False)
        for old in cache_dir.glob("loaders-*.cache"):
            if old != cache:
                old.unlink(missing_ok=True)
    if cache.exists():
        os.environ["GDK_PIXBUF_MODULE_FILE"] = str(cache)


def main() -> int:
    parser = argparse.ArgumentParser(description="Eglantine launcher")
    parser.add_argument("--auto", action="store_true", help="Check updates and launch automatically")
    parser.add_argument("--launch", action="store_true", help="Alias for --auto")
    parser.add_argument(
        "--project", metavar="ID", default=None, help="Emulator source to use (for shortcuts)"
    )
    parser.add_argument("--check", action="store_true", help="Headless smoke test: build UI, print ok, exit")
    parser.add_argument(
        "--quit-after",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Quit automatically after N seconds (for scripted screenshots)",
    )
    parser.add_argument(
        "--color-scheme",
        choices=["default", "light", "dark"],
        default="default",
        help="Force libadwaita color scheme (default follows the system)",
    )
    parser.add_argument("--settings", action="store_true", help="Open the Settings window on launch")
    parser.add_argument("--about", action="store_true", help="Open the About dialog on launch")
    args = parser.parse_args()

    auto = args.auto or args.launch

    if args.project:
        from .config import load as _load_config
        from .config import save as _save_config

        _cfg = _load_config()
        if any(p.get("id") == args.project for p in _cfg.get("projects", [])):
            _cfg["project"] = args.project
            _save_config(_cfg)

    _ensure_win32_pixbuf()

    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio
    except ImportError:
        print("GTK 4 / libadwaita not found. Install python3-gobject and libadwaita.", file=sys.stderr)
        return 1

    from .ui.window import LauncherWindow

    if args.check:
        app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        LauncherWindow(app, auto=False)
        print("ok")
        return 0

    if args.color_scheme != "default":
        _color_scheme = args.color_scheme
    else:
        _color_scheme = None

    if args.quit_after:
        _quit_after = args.quit_after
    else:
        _quit_after = 0

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
            self._window = None
            self._auto = auto
            self.connect("activate", self._on_activate)
            self.connect("startup", self._on_startup)

        def _on_startup(self, _app):
            from pathlib import Path

            from gi.repository import Adw as _Adw
            from gi.repository import Gdk, Gtk

            if _color_scheme is not None:
                schemes = {"light": _Adw.ColorScheme.FORCE_LIGHT, "dark": _Adw.ColorScheme.FORCE_DARK}
                _Adw.StyleManager.get_default().set_color_scheme(schemes[_color_scheme])

            icon_dir = Path(__file__).resolve().parents[1] / "data" / "icons"
            if icon_dir.exists():
                display = Gdk.Display.get_default()
                if display:
                    Gtk.IconTheme.get_for_display(display).add_search_path(str(icon_dir))

        def _on_activate(self, app):
            if self._window is None:
                self._window = LauncherWindow(app, auto=self._auto)
            self._window.present()
            if args.settings:
                self._window.on_settings()
            if args.about:
                self._window.on_about()
            if _quit_after:
                from gi.repository import GLib

                GLib.timeout_add_seconds(_quit_after, lambda: (app.quit(), False)[1])

    app = App()
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
