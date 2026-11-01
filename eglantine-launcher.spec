# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec. Linux: system GTK via PyGObject. Windows (MSYS2 UCRT64):
bundles the whole GTK runtime (DLLs, typelibs, schemas, icons, pixbuf
loaders) because PyInstaller cannot trace dlopen()ed libraries."""

import glob
import os
import shutil
import subprocess
import sys

datas = []
binaries = []
hiddenimports = []
runtime_hooks = []

if sys.platform == "win32":
    from PyInstaller.utils.hooks import collect_all, collect_submodules

    tmp_ret = collect_all("gi")
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]
    hiddenimports += collect_submodules("gi.repository")
    for mod in ("Gtk", "Adw", "Gio", "GLib", "GObject", "Pango", "GdkPixbuf", "cairo"):
        hiddenimports.append(f"gi.repository.{mod}")

    runtime_hooks.append("packaging/pyi_rth_gtk.py")

    prefix = os.environ.get("MSYSTEM_PREFIX", r"C:\msys64\ucrt64").replace("\\", "/")

    # Every DLL in the runtime prefix: PyInstaller can't trace dlopen()ed
    # GTK modules, and a missing DLL means a silent loader failure.
    for dll in glob.glob(prefix + "/bin/*.dll"):
        binaries.append((dll, "."))

    # Typelibs for girepository.
    datas.append((prefix + "/lib/girepository-1.0", "lib/girepository-1.0"))

    # GSettings schemas, compiled (compiled output has no absolute paths).
    staged_schemas = os.path.join(os.getcwd(), "build", "win-schemas")
    shutil.rmtree(staged_schemas, ignore_errors=True)
    shutil.copytree(prefix + "/share/glib-2.0/schemas", staged_schemas)
    subprocess.run(["glib-compile-schemas", staged_schemas], check=True)
    datas.append((staged_schemas, "share/glib-2.0/schemas"))

    # Icon theme (Adwaita + hicolor fallback).
    for theme in ("Adwaita", "hicolor"):
        src = prefix + f"/share/icons/{theme}"
        if os.path.isdir(src):
            datas.append((src, f"share/icons/{theme}"))

    # gdk-pixbuf loaders; the app generates loaders.cache at first launch.
    for loaders in glob.glob(prefix + "/lib/gdk-pixbuf-2.0/*/loaders"):
        # keep the real version dir name (e.g. 2.10.0); the runtime hook globs it
        ver = loaders.replace("\\", "/").split("/gdk-pixbuf-2.0/")[1].split("/")[0]
        datas.append((loaders, f"lib/gdk-pixbuf-2.0/{ver}/loaders"))
    query = prefix + "/bin/gdk-pixbuf-query-loaders.exe"
    if os.path.exists(query):
        binaries.append((query, "."))

    # Fontconfig config pointing at system fonts + per-user cache dir.
    datas.append(("packaging/fonts.conf", "share/fonts"))
else:
    from PyInstaller.utils.hooks import collect_all

    for pkg in ("gi", "gtk", "adw"):
        try:
            tmp_ret = collect_all(pkg)
        except Exception:
            continue
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]


a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe_kwargs = dict(
    exclude_binaries=True,
    name="eglantine-launcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
if sys.platform == "win32":
    exe_kwargs["icon"] = "data/icons/eglantine-launcher.ico"

exe = EXE(
    pyz,
    a.scripts,
    [],
    **exe_kwargs,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="eglantine-launcher",
)
