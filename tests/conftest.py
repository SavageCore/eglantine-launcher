import json
import os
import urllib.request

import pytest


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Isolate config/cache writes."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    return home


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()
        self.headers = {}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture()
def patch_urlopen(monkeypatch):
    def _patch(payload):
        def fake(url_or_req, timeout=None):
            return FakeResponse(payload)

        monkeypatch.setattr(urllib.request, "urlopen", fake)

    return _patch


@pytest.fixture(autouse=True)
def no_api_throttle(monkeypatch):
    """Keep mocked API tests fast; throttle behavior has its own tests."""
    monkeypatch.setenv("EGLANTINE_API_MIN_INTERVAL", "0")
    try:
        from eglantine_launcher import tasks

        tasks._reset_api_throttle()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Hermetic suite: real HTTP is blocked unless a test installs its own fake.

    Several GUI tests never mock urlopen, so LauncherWindow's background
    refreshes used to hit the live GitHub/Forgejo APIs - slow, flaky, and
    burning the shared anonymous rate budget. Autouse fixtures run before
    explicitly requested ones, so patch_urlopen and direct monkeypatching
    in test bodies still win.
    """
    import urllib.error

    def _blocked(url_or_req, timeout=None):
        raise urllib.error.URLError("network disabled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)


@pytest.fixture()
def needs_display():
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("no display; run under xvfb-run for GUI tests")
