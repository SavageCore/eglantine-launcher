"""System-tray icon via StatusNotifierItem over D-Bus (no extra dependencies).

Shown while the launcher window is hidden; activating it restores the
window. Best-effort: returns None when no StatusNotifierWatcher is on the
session bus (e.g. GNOME without a tray extension, or a sandbox without bus
access) or when anything else goes wrong - callers fall back to the
notification-only behavior.
"""

import os
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/StatusNotifierMenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
SNI_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"

_ITEM_XML = """<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/><arg name="menu" type="o" direction="out"/></method>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
  </interface>
</node>"""

_PROPS_XML = """<node>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get"><arg name="interface" type="s" direction="in"/><arg name="property" type="s" direction="in"/><arg name="value" type="v" direction="out"/></method>
    <method name="GetAll"><arg name="interface" type="s" direction="in"/><arg name="properties" type="a{sv}" direction="out"/></method>
    <method name="Set"><arg name="interface" type="s" direction="in"/><arg name="property" type="s" direction="in"/><arg name="value" type="v" direction="in"/></method>
  </interface>
</node>"""

_MENU_XML = """<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout"><arg name="parentId" type="i" direction="in"/><arg name="recursionDepth" type="i" direction="in"/><arg name="propertyNames" type="as" direction="in"/><arg name="revision" type="u" direction="out"/><arg name="layout" type="(ia{sv}av)" direction="out"/></method>
    <method name="GetGroupProperties"><arg name="ids" type="ai" direction="in"/><arg name="propertyNames" type="as" direction="in"/><arg name="properties" type="a(ia{sv})" direction="out"/></method>
    <method name="GetProperty"><arg name="id" type="i" direction="in"/><arg name="name" type="s" direction="in"/><arg name="value" type="v" direction="out"/></method>
    <method name="Event"><arg name="id" type="i" direction="in"/><arg name="eventId" type="s" direction="in"/><arg name="data" type="v" direction="in"/><arg name="timestamp" type="u" direction="in"/></method>
    <method name="EventGroup"><arg name="events" type="a(isvu)" direction="in"/><arg name="idErrors" type="ai" direction="out"/></method>
    <method name="AboutToShow"><arg name="id" type="i" direction="in"/><arg name="needUpdate" type="b" direction="out"/></method>
    <method name="AboutToShowGroup"><arg name="ids" type="ai" direction="in"/><arg name="updatesNeeded" type="ai" direction="out"/><arg name="idErrors" type="ai" direction="out"/></method>
    <property name="Version" type="s" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
  </interface>
</node>"""


def _default_icon_path():
    cand = Path(__file__).resolve().parents[1] / "data" / "icons" / "eglantine-launcher.png"
    if cand.is_file():
        return cand
    return None


def _load_pixmap(path, size=64):
    """Return [(width, height, argb_bytes)] for the SNI IconPixmap property."""
    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), size, size)
    if not pb.get_has_alpha():
        pb = pb.add_alpha(False, 0, 0, 0)
    w, h, rs = pb.get_width(), pb.get_height(), pb.get_rowstride()
    raw = bytes(pb.get_pixels())
    out = bytearray(w * h * 4)
    for y in range(h):
        row = raw[y * rs : y * rs + w * 4]
        for x in range(w):
            i, o = x * 4, (y * w + x) * 4
            out[o] = row[i + 3]
            out[o + 1] = row[i]
            out[o + 2] = row[i + 1]
            out[o + 3] = row[i + 2]
    return [(w, h, bytes(out))]


class TrayHandle:
    """Opaque registration; pass to hide_tray_icon() when done."""

    def __init__(self, bus, service):
        self._bus = bus
        self._service = service
        self._reg_ids = []
        self._closed = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        bus, service = self._bus, self._service
        self._bus = None
        try:
            for _path, reg_id in self._reg_ids:
                try:
                    bus.unregister_object(reg_id)
                except Exception:
                    pass
            self._reg_ids = []
            bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ReleaseName",
                GLib.Variant("(s)", (service,)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
        except Exception:
            pass


def show_tray_icon(on_show, on_quit, icon_path=None):
    """Register a StatusNotifierItem. Returns a TrayHandle, or None."""
    try:
        return _show(on_show, on_quit, icon_path)
    except Exception:
        import traceback

        traceback.print_exc()
        return None


def _show(on_show, on_quit, icon_path):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    has_owner = bus.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameHasOwner",
        GLib.Variant("(s)", (WATCHER,)),
        GLib.VariantType.new("(b)"),
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )
    if not has_owner[0]:
        import sys

        print("statusicon: no watcher owner", file=sys.stderr)
        return None
    service = f"org.kde.StatusNotifierItem-{os.getpid()}-eglantine"
    owned = bus.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "RequestName",
        GLib.Variant("(su)", (service, 0)),
        GLib.VariantType.new("(u)"),
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )
    if owned[0] != 1:
        import sys

        print(f"statusicon: RequestName got {owned[0]}", file=sys.stderr)
        return None
    handle = TrayHandle(bus, service)

    pixmap = []
    icon_name = "eglantine-launcher"
    path = Path(icon_path) if icon_path else _default_icon_path()
    if path is not None and path.is_file():
        try:
            pixmap = _load_pixmap(path)
            icon_name = ""
        except Exception:
            pixmap = []

    props = {
        "Category": GLib.Variant.new_string("ApplicationStatus"),
        "Id": GLib.Variant.new_string("eglantine-launcher"),
        "Title": GLib.Variant.new_string("Eglantine Launcher"),
        "Status": GLib.Variant.new_string("Active"),
        "WindowId": GLib.Variant.new_int32(0),
        "IconName": GLib.Variant.new_string(icon_name),
        "IconPixmap": GLib.Variant("a(iiay)", pixmap),
        "OverlayIconName": GLib.Variant.new_string(""),
        "ToolTip": GLib.Variant(
            "(sa(iiay)ss)",
            ("", GLib.Variant("a(iiay)", pixmap), "Eglantine Launcher", "Click to restore"),
        ),
        "ItemIsMenu": GLib.Variant.new_boolean(False),
        "Menu": GLib.Variant.new_object_path(MENU_PATH),
    }

    def sni_call(_conn, _sender, _path, iface, method, params, invocation):
        try:
            if iface == PROPS_IFACE:
                if method == "Get":
                    invocation.return_value(GLib.Variant("(v)", (props[params[1]],)))
                elif method == "GetAll":
                    invocation.return_value(GLib.Variant("(a{sv})", (dict(props),)))
                else:
                    invocation.return_error_literal(
                        Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, "read-only"
                    )
            elif method == "Activate" or method == "SecondaryActivate":
                on_show()
                invocation.return_value(None)
            elif method == "Scroll":
                invocation.return_value(None)
            elif method == "ContextMenu":
                invocation.return_value(GLib.Variant("(o)", (MENU_PATH,)))
            else:
                invocation.return_error_literal(Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method)
        except Exception as e:
            try:
                invocation.return_error_literal(Gio.dbus_error_quark(), Gio.DBusError.FAILED, str(e))
            except Exception:
                pass

    menu_props = {
        "Version": GLib.Variant.new_string("3"),
        "TextDirection": GLib.Variant.new_string("ltr"),
        "Status": GLib.Variant.new_string("normal"),
        "IconThemePath": GLib.Variant("as", []),
    }
    variant_type = GLib.VariantType.new("v")

    def _item_props(label):
        return GLib.Variant(
            "a{sv}",
            {
                "label": GLib.Variant.new_string(label),
                "enabled": GLib.Variant.new_boolean(True),
                "visible": GLib.Variant.new_boolean(True),
                "type": GLib.Variant.new_string("standard"),
            },
        )

    def _item(i, label):
        return GLib.Variant.new_tuple(
            GLib.Variant.new_int32(i),
            _item_props(label),
            GLib.Variant.new_array(variant_type, []),
        )

    def _layout():
        kids = GLib.Variant.new_array(
            variant_type,
            [
                GLib.Variant.new_variant(_item(1, "Show window")),
                GLib.Variant.new_variant(_item(2, "Quit")),
            ],
        )
        root = GLib.Variant.new_tuple(
            GLib.Variant.new_int32(0),
            GLib.Variant("a{sv}", {}),
            kids,
        )
        return GLib.Variant.new_tuple(GLib.Variant.new_uint32(1), root)

    def menu_call(_conn, _sender, _path, iface, method, params, invocation):
        try:
            if iface == PROPS_IFACE:
                if method == "Get":
                    invocation.return_value(GLib.Variant("(v)", (menu_props[params[1]],)))
                elif method == "GetAll":
                    invocation.return_value(GLib.Variant("(a{sv})", (dict(menu_props),)))
                else:
                    invocation.return_error_literal(
                        Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, "read-only"
                    )
            elif method == "GetLayout":
                invocation.return_value(_layout())
            elif method == "GetGroupProperties":
                invocation.return_value(
                    GLib.Variant(
                        "(a(ia{sv}))", ([(1, _item_props("Show window")), (2, _item_props("Quit"))],)
                    )
                )
            elif method == "GetProperty":
                label = GLib.Variant.new_string("Show window" if params[0] == 1 else "Quit")
                invocation.return_value(GLib.Variant("(v)", (label,)))
            elif method == "Event":
                if params[1] == "clicked":
                    if params[0] == 1:
                        on_show()
                    elif params[0] == 2:
                        on_quit()
                invocation.return_value(None)
            elif method == "EventGroup":
                invocation.return_value(GLib.Variant("(ai)", ([],)))
            elif method == "AboutToShow":
                invocation.return_value(GLib.Variant("(b)", (False,)))
            elif method == "AboutToShowGroup":
                invocation.return_value(GLib.Variant("(aiai)", ([], [])))
            else:
                invocation.return_error_literal(Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method)
        except Exception as e:
            try:
                invocation.return_error_literal(Gio.dbus_error_quark(), Gio.DBusError.FAILED, str(e))
            except Exception:
                pass

    sni_node = Gio.DBusNodeInfo.new_for_xml(_ITEM_XML)
    props_node = Gio.DBusNodeInfo.new_for_xml(_PROPS_XML)
    menu_node = Gio.DBusNodeInfo.new_for_xml(_MENU_XML)
    try:
        handle._reg_ids.append(
            (ITEM_PATH, bus.register_object(ITEM_PATH, sni_node.interfaces[0], sni_call, None, None))
        )
        handle._reg_ids.append(
            (ITEM_PATH, bus.register_object(ITEM_PATH, props_node.interfaces[0], sni_call, None, None))
        )
        handle._reg_ids.append(
            (MENU_PATH, bus.register_object(MENU_PATH, menu_node.interfaces[0], menu_call, None, None))
        )
        handle._reg_ids.append(
            (MENU_PATH, bus.register_object(MENU_PATH, props_node.interfaces[0], menu_call, None, None))
        )
        bus.call_sync(
            WATCHER,
            WATCHER_PATH,
            WATCHER_IFACE,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (service,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except Exception:
        import traceback

        traceback.print_exc()
        handle.close()
        return None
    return handle


def hide_tray_icon(handle):
    """Unregister a tray icon. Never raises."""
    try:
        if handle is not None:
            handle.close()
    except Exception:
        pass
