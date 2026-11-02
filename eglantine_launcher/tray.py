"""Minimize / hide-to-tray helpers (no extra dependencies).

"taskbar" means a normal minimized window. "tray" means the window is
hidden; it comes back via app activation or the restore notification.
There is no tray-icon dependency: StatusIcon is gone in GTK4 and
AppIndicator menus need the removed Gtk.Menu, so hiding without a
vendored notifier library would strand the window otherwise.
"""

LAUNCH_CHOICES = (
    ("taskbar", "Minimize to taskbar"),
    ("tray", "Hide to tray"),
    ("none", "Keep visible"),
)

CLOSE_CHOICES = (
    ("quit", "Quit"),
    ("tray", "Hide to tray"),
)

ASK_LABEL = "Ask me"


def launch_label(value):
    for v, label in LAUNCH_CHOICES:
        if v == value:
            return label
    return ASK_LABEL


def close_label(value):
    for v, label in CLOSE_CHOICES:
        if v == value:
            return label
    return ASK_LABEL


def minimize_to_taskbar(window):
    window.minimize()


def hide_to_tray(window):
    """Hide the window; fall back to minimize if hiding fails."""
    try:
        window.set_visible(False)
    except Exception:
        window.minimize()


def restore(window):
    """Bring a minimized or hidden window back."""
    try:
        window.set_visible(True)
    except Exception:
        pass
    try:
        window.unminimize()
    except Exception:
        pass
    try:
        window.present()
    except Exception:
        pass
