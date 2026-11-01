"""PyInstaller runtime hook (Windows): point GTK at the bundled runtime."""

import glob
import os
import sys

if getattr(sys, "frozen", False) and sys.platform.startswith("win"):
    base = sys._MEIPASS.replace("\\", "/")
    os.environ.setdefault("GI_TYPELIB_PATH", base + "/lib/girepository-1.0")
    os.environ.setdefault("GSETTINGS_SCHEMA_DIR", base + "/share/glib-2.0/schemas")
    loaders = glob.glob(base + "/lib/gdk-pixbuf-2.0/*/loaders")
    if loaders:
        # GDK_PIXBUF_MODULE_FILE is set by the app itself once it generates
        # a cache; the module dir alone lets pixbuf fall back to a rescan.
        os.environ.setdefault("GDK_PIXBUF_MODULEDIR", loaders[0])
    os.environ.setdefault("XDG_DATA_DIRS", base + "/share")
    os.environ.setdefault("FONTCONFIG_FILE", base + "/share/fonts/fonts.conf")
    os.environ.setdefault("GTK_EXE_PREFIX", base)
