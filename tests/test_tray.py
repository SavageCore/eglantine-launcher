"""Unit tests for minimize/tray window behavior (no display needed)."""

from eglantine_launcher import tray
from eglantine_launcher.tray import ASK_LABEL, CLOSE_CHOICES, LAUNCH_CHOICES, close_label, launch_label


def test_launch_choices_cover_taskbar_tray_keep_visible():
    values = [v for v, _label in LAUNCH_CHOICES]
    assert values == ["taskbar", "tray", "none"]


def test_close_choices_cover_quit_and_tray():
    values = [v for v, _label in CLOSE_CHOICES]
    assert values == ["quit", "tray"]


def test_labels_fall_back_to_ask():
    assert launch_label("taskbar") != ASK_LABEL
    assert launch_label("tray") != ASK_LABEL
    assert launch_label("none") != ASK_LABEL
    assert launch_label(None) == ASK_LABEL
    assert launch_label("bogus") == ASK_LABEL
    assert close_label("quit") != ASK_LABEL
    assert close_label("tray") != ASK_LABEL
    assert close_label(None) == ASK_LABEL
    assert close_label("bogus") == ASK_LABEL


def test_defaults_ask_first_time(fake_home):
    from eglantine_launcher import config

    cfg = config.load()
    assert cfg["minimize_on_launch"] is None
    assert cfg["close_behavior"] is None


def test_choices_survive_save_load_roundtrip(fake_home):
    from eglantine_launcher import config

    cfg = config.load()
    cfg["minimize_on_launch"] = "tray"
    cfg["close_behavior"] = "tray"
    config.save(cfg)
    reloaded = config.load()
    assert reloaded["minimize_on_launch"] == "tray"
    assert reloaded["close_behavior"] == "tray"


def test_tray_module_only_needs_window_methods():
    calls = []

    class FakeWindow:
        def minimize(self):
            calls.append("minimize")

        def unminimize(self):
            calls.append("unminimize")

        def present(self):
            calls.append("present")

        def set_visible(self, visible):
            calls.append(("visible", visible))

    w = FakeWindow()
    tray.minimize_to_taskbar(w)
    tray.hide_to_tray(w)
    tray.restore(w)
    assert calls[0] == "minimize"
    assert ("visible", False) in calls
    assert ("visible", True) in calls
    assert "unminimize" in calls
    assert "present" in calls


def test_hide_falls_back_to_minimize():
    calls = []

    class StubbornWindow:
        def minimize(self):
            calls.append("minimize")

        def set_visible(self, _visible):
            raise RuntimeError("headless")

    tray.hide_to_tray(StubbornWindow())
    assert calls == ["minimize"]
