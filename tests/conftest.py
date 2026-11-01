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


@pytest.fixture()
def needs_display():
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("no display; run under xvfb-run for GUI tests")
