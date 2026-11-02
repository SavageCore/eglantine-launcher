"""Tray icon tests. Need a session bus; skipped without one."""

import gi

gi.require_version("GdkPixbuf", "2.0")

import pytest
from gi.repository import GdkPixbuf, Gio, GLib

from eglantine_launcher import statusicon


@pytest.fixture()
def session_bus():
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        pytest.skip("no D-Bus session bus")
        return None
    return bus


def _watcher_present(bus):
    try:
        owned = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (statusicon.WATCHER,)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        return bool(owned[0])
    except Exception:
        return False


class FakeWatcher:
    """Stand in for StatusNotifierWatcher when the desktop has none."""

    XML = """<node>
      <interface name="org.kde.StatusNotifierWatcher">
        <method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
      </interface>
    </node>"""

    def __init__(self, bus):
        self.services = []
        node = Gio.DBusNodeInfo.new_for_xml(self.XML)
        self._reg = bus.register_object(statusicon.WATCHER_PATH, node.interfaces[0], self._call, None, None)

    def _call(self, _conn, _sender, _path, _iface, method, params, invocation):
        import sys

        print(f"FakeWatcher._call method={method}", file=sys.stderr)
        if method == "RegisterStatusNotifierItem":
            self.services.append(params[0])
            invocation.return_value(GLib.Variant("()", ()))
        else:
            invocation.return_error_literal(Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method)


def _bus_address(session_bus):
    """Session bus address for extra connections.

    Gio.bus_get_sync works even when DBUS_SESSION_BUS_ADDRESS is unset
    (autolaunch via XDG_RUNTIME_DIR/bus or dbus-launch), but
    new_for_address_sync needs the address string, so reconstruct it
    from the live connection's socket as a fallback.
    Returns None when it cannot be determined.
    """
    import os

    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not addr:
        try:
            stream = session_bus.get_stream()
            if isinstance(stream, Gio.SocketConnection):
                ra = stream.get_remote_address()
                if isinstance(ra, Gio.UnixSocketAddress):
                    atype = ra.get_address_type()
                    if atype == Gio.UnixSocketAddressType.PATH:
                        addr = "unix:path=" + ra.get_path()
                    elif atype == Gio.UnixSocketAddressType.ABSTRACT:
                        addr = "unix:abstract=" + ra.get_path()
        except Exception:
            addr = None
    return addr or None


@pytest.fixture()
def watcher(session_bus):
    if _watcher_present(session_bus):
        yield None
        return
    import sys
    import threading

    # Like a real watcher process: own connection dispatched by its own
    # MainLoop in a helper thread. Without a running loop the Register
    # handler never runs and _show's blocking call times out.
    addr = _bus_address(session_bus)
    if addr is None:
        print("watcher: cannot determine bus address", file=sys.stderr)
        yield None
        return
    box = {}
    ready = threading.Event()

    def _run():
        loop = GLib.MainLoop()
        box["loop"] = loop
        try:
            wconn = Gio.DBusConnection.new_for_address_sync(
                addr,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None,
                None,
            )
            owned = wconn.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "RequestName",
                GLib.Variant("(su)", (statusicon.WATCHER, 0)),
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            print(f"watcher: RequestName owned={owned[0]}", file=sys.stderr)
            if owned[0] != 1:
                box["error"] = f"RequestName owned={owned[0]}"
                return
            box["wconn"] = wconn
            box["fake"] = FakeWatcher(wconn)
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            box["error"] = e
        finally:
            ready.set()
        if "error" not in box:
            loop.run()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    if not ready.wait(10):
        print("watcher: helper thread did not start", file=sys.stderr)
        yield None
        return
    if "error" in box or "fake" not in box:
        yield None
        return
    yield box["fake"]
    try:
        box["loop"].quit()
    except Exception:
        pass
    thread.join(10)
    try:
        box["wconn"].close_sync(None)
    except Exception:
        pass


class BusClient:
    """Separate connection so server-side handlers get dispatched."""

    def __init__(self, address):
        self.conn = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )

    def call(self, bus_name, path, iface, method, params, reply_type, timeout_ms=5000):
        outcome = {}
        loop = GLib.MainLoop()

        def done(_conn, res, _data):
            try:
                outcome["value"] = self.conn.call_finish(res)
            except Exception as e:  # noqa: BLE001
                outcome["error"] = e
            loop.quit()

        self.conn.call(
            bus_name,
            path,
            iface,
            method,
            params,
            reply_type,
            Gio.DBusCallFlags.NONE,
            timeout_ms,
            None,
            done,
            None,
        )
        loop.run()
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    def close(self):
        try:
            self.conn.close_sync(None)
        except Exception:
            pass


def _make_icon(tmp_path):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8, 8)
    pb.fill(0xFF0000FF)
    path = tmp_path / "icon.png"
    pb.savev(str(path), "png", [], [])
    return path


def _u(value):
    return value.unpack() if isinstance(value, GLib.Variant) else value


def test_tray_icon_registers_and_activates(session_bus, watcher, tmp_path, monkeypatch):
    shown, quit = [], []
    handle = statusicon.show_tray_icon(
        lambda: shown.append(True), lambda: quit.append(True), icon_path=_make_icon(tmp_path)
    )
    assert handle is not None
    addr = _bus_address(session_bus)
    assert addr is not None, "cannot determine session bus address"
    client = BusClient(addr)
    try:
        if watcher is not None:
            assert len(watcher.services) == 1
            service = watcher.services[0]
        else:
            service = None

        target = service or session_bus.get_unique_name()
        # properties readable
        props = client.call(
            target,
            statusicon.ITEM_PATH,
            "org.freedesktop.DBus.Properties",
            "GetAll",
            GLib.Variant("(s)", (statusicon.SNI_IFACE,)),
            GLib.VariantType.new("(a{sv})"),
        )[0]
        props = {k: _u(v) for k, v in props.items()}
        assert props["Title"] == "Eglantine Launcher"
        assert props["Status"] == "Active"
        assert len(props["IconPixmap"]) == 1
        w, h, _px = props["IconPixmap"][0]
        w, h = _u(w), _u(h)
        assert (w, h) == (64, 64)

        # Activate restores
        client.call(
            target,
            statusicon.ITEM_PATH,
            statusicon.SNI_IFACE,
            "Activate",
            GLib.Variant("(ii)", (0, 0)),
            None,
        )
        assert shown == [True]

        # menu has Show + Quit, Quit fires
        rev, layout = client.call(
            target,
            statusicon.MENU_PATH,
            statusicon.MENU_IFACE,
            "GetLayout",
            GLib.Variant("(iias)", (0, 1, [])),
            GLib.VariantType.new("(u(ia{sv}av))"),
        )
        _root_id, _root_props, kids = layout
        labels = []
        for kid in kids:
            _kid_id, kid_props, _kid_kids = _u(kid)
            labels.append(kid_props["label"])
        assert labels == ["Show window", "Quit"]
        assert labels == ["Show window", "Quit"]

        client.call(
            target,
            statusicon.MENU_PATH,
            statusicon.MENU_IFACE,
            "Event",
            GLib.Variant("(isvu)", (2, "clicked", GLib.Variant.new_int32(0), 0)),
            None,
        )
        assert quit == [True]
    finally:
        client.close()
        statusicon.hide_tray_icon(handle)


def test_tray_icon_without_watcher_returns_none(session_bus, monkeypatch):
    import eglantine_launcher.statusicon as mod

    real_call = Gio.DBusConnection.call_sync

    def fake_call(self, *args, **kwargs):
        if len(args) >= 4 and args[3] == "NameHasOwner":
            return GLib.Variant("(b)", (False,))
        return real_call(self, *args, **kwargs)

    monkeypatch.setattr(Gio.DBusConnection, "call_sync", fake_call)
    assert mod.show_tray_icon(lambda: None, lambda: None) is None
    mod.hide_tray_icon(None)
