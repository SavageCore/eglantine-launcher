"""Unit tests for release/commit matching logic (no network, no display)."""

from pathlib import Path

import pytest

from eglantine_launcher import tasks
from eglantine_launcher.tasks import (
    fetch_release,
    find_asset,
    infer_releases_url,
    list_commits,
    list_releases,
    parse_repo,
    release_platforms,
)


def test_parse_repo_github():
    assert parse_repo("https://github.com/Djihads80/Eglantine.git") == ("github", "Djihads80", "Eglantine")


def test_parse_repo_forgejo():
    host, owner, repo = parse_repo("https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork.git")
    assert (host, owner, repo) == ("forgejo", "Franseven", "PokeDinner-a-Eglantine-Fork")


def test_infer_releases_url():
    assert (
        infer_releases_url("https://github.com/Djihads80/Eglantine.git")
        == "https://api.github.com/repos/Djihads80/Eglantine/releases"
    )
    assert (
        infer_releases_url("https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork.git")
        == "https://git.eden-emu.dev/api/v1/repos/Franseven/PokeDinner-a-Eglantine-Fork/releases"
    )
    assert (
        infer_releases_url("https://forge.example.com/o/r.git")
        == "https://forge.example.com/api/v1/repos/o/r/releases"
    )
    assert infer_releases_url("not a url") is None
    assert infer_releases_url("") is None


def test_forgejo_api_base_derives_host():
    from eglantine_launcher.tasks import _forgejo_api_base

    assert _forgejo_api_base("https://git.eden-emu.dev/o/r.git") == "https://git.eden-emu.dev/api/v1"
    assert _forgejo_api_base("https://forge.example.com/o/r.git") == "https://forge.example.com/api/v1"
    assert _forgejo_api_base("http://192.168.1.5:3000/o/r.git") == "http://192.168.1.5:3000/api/v1"
    assert _forgejo_api_base("git@forge.example.com:o/r.git") == "https://forge.example.com/api/v1"
    assert _forgejo_api_base("not a url") is None
    assert _forgejo_api_base("") is None
    assert _forgejo_api_base(None) is None


def _releases():
    return [
        {
            "tag_name": "build-99",
            "name": "AMD Experimental Build",
            "body": "experimental amd driver test",
            "prerelease": True,
            "assets": [
                {"name": "eglantine-abc-linux-x86_64.AppImage", "browser_download_url": "https://x/a"}
            ],
        },
        {
            "tag_name": "build-98",
            "name": "Eglantine nx2 build 98",
            "body": "regular build",
            "prerelease": True,
            "assets": [
                {"name": "eglantine-def-linux-x86_64-pgo.AppImage", "browser_download_url": "https://x/b"},
                {"name": "eglantine-windows-x86_64-def.zip", "browser_download_url": "https://x/c"},
            ],
        },
        {
            "tag_name": "v1.0",
            "name": "Stable",
            "body": "",
            "prerelease": False,
            "assets": [{"name": "PokeDinner-Eglantine-1.0-win64.zip", "browser_download_url": "https://x/d"}],
        },
    ]


def _cfg(patch_urlopen, **overrides):
    patch_urlopen(_releases())
    info = {
        "id": "test",
        "releases_url": "https://api.github.com/repos/o/r/releases",
        "include_prerelease": True,
    }
    info.update(overrides)
    return {"project": "test", "pgo": True, "projects": [info]}


def test_list_releases_prerelease_filter(patch_urlopen):
    cfg = _cfg(patch_urlopen)
    assert [r["tag_name"] for r in list_releases(cfg)] == ["build-99", "build-98", "v1.0"]
    cfg["projects"][0]["include_prerelease"] = False
    assert [r["tag_name"] for r in list_releases(cfg)] == ["v1.0"]


def test_list_releases_exclude_keywords(patch_urlopen):
    cfg = _cfg(patch_urlopen, exclude_keywords="AMD Experimental")
    assert [r["tag_name"] for r in list_releases(cfg)] == ["build-98", "v1.0"]


def test_find_asset_linux_pgo():
    assets = _releases()[1]["assets"]
    assert find_asset(None, assets, True)["name"].endswith("-pgo.AppImage")
    # only a PGO asset exists for linux: non-PGO request falls back to it
    assert find_asset(None, assets, False)["name"].endswith("-pgo.AppImage")


def test_find_asset_win64_variant(monkeypatch):
    monkeypatch.setattr(tasks, "is_windows", lambda: True)
    win = [a for a in _releases()[2]["assets"]]
    assert find_asset(None, win, True)["name"].endswith("win64.zip")


def test_find_asset_falls_back_when_no_pgo_variant():
    assets = [{"name": "eglantine-abc-linux-x86_64.AppImage", "browser_download_url": "https://x"}]
    assert find_asset(None, assets, True)["name"] == assets[0]["name"]
    assert find_asset(None, [], True) is None


def test_release_platforms(patch_urlopen):
    cfg = _cfg(patch_urlopen)
    assert release_platforms(cfg) == {"linux": True, "windows": True}
    cfg["projects"][0]["exclude_keywords"] = "AMD Experimental"
    assert release_platforms(cfg) == {"linux": True, "windows": True}


def test_record_keeps_unique_capped():
    from eglantine_launcher.tasks import _record

    cfg = {"downloaded_releases": ["b", "a"]}
    _record(cfg, "downloaded_releases", "c")
    assert cfg["downloaded_releases"] == ["c", "b", "a"]
    _record(cfg, "downloaded_releases", "b")
    assert cfg["downloaded_releases"] == ["b", "c", "a"]


def test_release_platforms_none_without_releases_url():
    cfg = {"project": "test", "projects": [{"id": "test", "releases_url": None}]}
    assert release_platforms(cfg) == {"linux": False, "windows": False}


def test_fetch_release_by_tag(patch_urlopen):
    cfg = _cfg(patch_urlopen)
    tag, assets = fetch_release(cfg, "build-98")
    assert tag == "build-98"
    assert len(assets) == 2


def test_list_commits_github(patch_urlopen):
    patch_urlopen([{"sha": "abc123", "commit": {"message": "fix things"}}])
    cfg = {
        "project": "t",
        "projects": [{"id": "t", "source_url": "https://github.com/o/r.git", "branch": "main"}],
    }
    commits = list_commits(cfg)
    assert commits[0]["sha"] == "abc123"


def test_find_pgo_clang_prefers_opt_llvm(tmp_path, monkeypatch):
    import eglantine_launcher.tasks as tasks

    fake_home = tmp_path / "home"
    (fake_home / "opt" / "llvm23" / "bin").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    # opt files don't exist -> falls through to PATH lookup
    monkeypatch.setattr(
        tasks,
        "_host_which",
        lambda name: None if name.startswith("/") else (name if "clang" in name else None),
    )
    cc, cxx = tasks._find_pgo_clang()
    assert cxx == "clang++"
    assert cc == "clang"


def test_find_pgo_clang_none(monkeypatch):
    import eglantine_launcher.tasks as tasks

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/nonexistent")))
    monkeypatch.setattr(tasks, "_host_which", lambda name: None)
    assert tasks._find_pgo_clang() == (None, None)


def test_profile_readable_probe(monkeypatch):
    import eglantine_launcher.tasks as tasks

    class Proc:
        def __init__(self, rc):
            self.returncode = rc

    monkeypatch.setattr(tasks, "_host_run", lambda cmd, **kw: Proc(0))
    assert tasks._profile_readable("clang++", "/tmp/x.profdata") is True
    monkeypatch.setattr(tasks, "_host_run", lambda cmd, **kw: Proc(1))
    assert tasks._profile_readable("clang++", "/tmp/x.profdata") is False


def test_build_source_uses_pgo_flags(monkeypatch, tmp_path, fake_home):
    import eglantine_launcher.tasks as tasks

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(
        tasks, "ensure_source", lambda cfg, status_cb=None, commit=None, **kw: (src, "abc123")
    )
    monkeypatch.setattr(tasks, "_find_pgo_clang", lambda: ("/opt/clang", "/opt/clang++"))
    monkeypatch.setattr(tasks, "_ensure_pgo_profile", lambda status_cb=None: Path("/tmp/eden.profdata"))
    monkeypatch.setattr(tasks, "_profile_readable", lambda cxx, prof: True)

    seen = {}

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            seen.setdefault("cmds", []).append(cmd)
            self.stdout = iter(["configuring..."])
            self.returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(tasks, "_host_popen", FakeProc)
    cfg = {"project": "t", "pgo": True, "projects": [{"id": "t"}]}
    with monkeypatch.context() as m:
        m.setattr("os.cpu_count", lambda: 16)
        tasks.build_source(cfg, commit="abc123")
    configure = seen["cmds"][0]
    assert "-DCMAKE_CXX_COMPILER=/opt/clang++" in configure
    assert any(a.startswith("-DCMAKE_CXX_FLAGS=") and "-fprofile-use=" in a for a in configure)
    cxx_flags = next(a for a in configure if a.startswith("-DCMAKE_CXX_FLAGS="))
    assert "-fuse-ld" not in cxx_flags, "linker flags must not leak into compile flags"
    assert "-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=bfd" in configure
    build_cmd = seen["cmds"][1]
    assert build_cmd[-2:] == ["--parallel", "6"]


def test_build_source_skips_pgo_without_clang(monkeypatch, tmp_path, fake_home):
    import eglantine_launcher.tasks as tasks

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(
        tasks, "ensure_source", lambda cfg, status_cb=None, commit=None, **kw: (src, "abc123")
    )
    monkeypatch.setattr(tasks, "_find_pgo_clang", lambda: (None, None))

    seen = {}

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            seen.setdefault("cmds", []).append(cmd)
            self.stdout = iter([])
            self.returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(tasks, "_host_popen", FakeProc)
    cfg = {"project": "t", "pgo": True, "projects": [{"id": "t"}]}
    tasks.build_source(cfg, commit="abc123")
    configure = seen["cmds"][0]
    assert not any("CMAKE_CXX_COMPILER" in a for a in configure)
    assert not any("fprofile-use" in a for a in configure)


def test_build_source_cancelled_before_configure(monkeypatch, tmp_path, fake_home):
    import pytest

    import eglantine_launcher.tasks as tasks

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(
        tasks, "ensure_source", lambda cfg, status_cb=None, commit=None, **kw: (src, "abc123")
    )
    monkeypatch.setattr(tasks, "_find_pgo_clang", lambda: (None, None))

    def no_popen(cmd, **kwargs):
        raise AssertionError("must not spawn a build process once cancelled")

    monkeypatch.setattr(tasks, "_host_popen", no_popen)
    cfg = {"project": "t", "pgo": False, "projects": [{"id": "t"}]}
    with pytest.raises(tasks.BuildCancelled):
        tasks.build_source(cfg, commit="abc123", cancelled=lambda: True)


def test_build_source_cancelled_during_build(monkeypatch, tmp_path, fake_home):
    import pytest

    import eglantine_launcher.tasks as tasks

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setattr(
        tasks, "ensure_source", lambda cfg, status_cb=None, commit=None, **kw: (src, "abc123")
    )
    monkeypatch.setattr(tasks, "_find_pgo_clang", lambda: (None, None))

    killed = []
    spawned = {"n": 0}

    class FakeProc:
        def __init__(self, cmd, **kwargs):
            spawned["n"] += 1
            self.stdout = iter(["compiling..."])
            self.returncode = 0

        def terminate(self):
            killed.append(True)

        def wait(self, timeout=None):
            return 0

        def kill(self):
            killed.append(True)

    monkeypatch.setattr(tasks, "_host_popen", FakeProc)
    cfg = {"project": "t", "pgo": False, "projects": [{"id": "t"}]}
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 4  # let configure pass, cancel during the build step

    with pytest.raises(tasks.BuildCancelled):
        tasks.build_source(cfg, commit="abc123", cancelled=cancelled)
    assert killed, "cancelled build must terminate the child process"


def test_config_save_load_roundtrip_in_fake_home(fake_home):
    from eglantine_launcher import config

    assert str(config.config_file()).startswith(str(fake_home))
    cfg = config.load()
    assert config.config_file().exists()
    assert {p["id"] for p in cfg["projects"]} >= {"eglantine", "pokedinner"}
    cfg["project"] = "t"
    cfg["projects"] = [{"id": "t"}]
    config.save(cfg)
    assert config.load()["project"] == "t"


def test_api_get_json_falls_back_on_401(monkeypatch):
    import json as _json
    import urllib.error
    import urllib.request

    import eglantine_launcher.tasks as tasks

    tasks._token_rejected = False
    calls = []

    class Resp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(url_or_req, timeout=None):
        calls.append(dict(url_or_req.header_items()).get("Authorization"))
        if len(calls) == 1:
            raise urllib.error.HTTPError(str(url_or_req.full_url), 401, "x", {}, None)
        return Resp(_json.dumps([{"tag_name": "v1"}]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    cfg = {"github_token": "dead-token"}
    assert tasks.api_get_json("https://x", cfg) == [{"tag_name": "v1"}]
    assert calls[0] == "Bearer dead-token"
    assert calls[1] is None
    assert tasks._token_rejected is True
    tasks.token_accepted()
    assert tasks._token_rejected is False


def test_api_get_json_raises_other_errors(monkeypatch):
    import urllib.error
    import urllib.request

    import eglantine_launcher.tasks as tasks

    tasks._token_rejected = False

    def fake(url_or_req, timeout=None):
        raise urllib.error.HTTPError(str(url_or_req.full_url), 403, "x", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(urllib.error.HTTPError):
        tasks.api_get_json("https://x", {"github_token": "t"})


def test_api_get_json_flags_rejected_saved_token(monkeypatch, fake_home):
    import urllib.error
    import urllib.request

    from conftest import FakeResponse

    import eglantine_launcher.tasks as tasks

    tasks._token_rejected = False

    def fake(url_or_req, timeout=None):
        if not hasattr(fake, "n"):
            fake.n = 0
        fake.n += 1
        if fake.n == 1:
            raise urllib.error.HTTPError(str(url_or_req.full_url), 401, "x", {}, None)
        return FakeResponse({"ok": True})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cfg = {"projects": [], "github_token": "dead-token"}
    assert tasks.api_get_json("https://x", cfg) == {"ok": True}
    assert cfg["github_token_rejected"] is True
    from eglantine_launcher.config import load

    assert load()["github_token_rejected"] is True
    tasks.token_accepted()


def test_api_get_json_skips_flag_for_env_or_throwaway_cfg(monkeypatch, fake_home):
    import urllib.error
    import urllib.request

    from conftest import FakeResponse

    import eglantine_launcher.tasks as tasks

    def fake(url_or_req, timeout=None):
        if not hasattr(fake, "n"):
            fake.n = 0
        fake.n += 1
        if fake.n == 1:
            raise urllib.error.HTTPError(str(url_or_req.full_url), 401, "x", {}, None)
        return FakeResponse({"ok": True})

    monkeypatch.setattr(urllib.request, "urlopen", fake)

    # env token takes precedence: a 401 here says nothing about the saved one
    tasks._token_rejected = False
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    cfg = {"projects": [], "github_token": "saved-token"}
    assert tasks.api_get_json("https://x", cfg) == {"ok": True}
    assert "github_token_rejected" not in cfg
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    # throwaway dicts (verification probes) must never touch the config file
    tasks._token_rejected = False
    fake.n = 0
    probe = {"github_token": "probe-token"}
    assert tasks.api_get_json("https://x", probe) == {"ok": True}
    assert "github_token_rejected" not in probe
    tasks.token_accepted()


def test_merge_remote_sources_adds_and_updates(fake_home):
    from eglantine_launcher.config import merge_remote_sources

    cfg = {
        "projects": [
            {
                "id": "eglantine",
                "name": "Old",
                "source_url": "u",
                "include_prerelease": False,
                "exclude_keywords": "Foo",
            }
        ],
        "hidden_sources": ["gone"],
    }
    remote = [
        {"id": "eglantine", "name": "New", "source_url": "u2", "branch": "nx2"},
        {"id": "newfork", "name": "NewFork", "source_url": "https://x/y.git"},
        {"id": "gone", "name": "Gone", "source_url": "https://x/gone.git"},
        {"noid": True},
    ]
    assert merge_remote_sources(cfg, remote) is True
    by_id = {p["id"]: p for p in cfg["projects"]}
    assert by_id["eglantine"]["name"] == "New"
    assert by_id["eglantine"]["include_prerelease"] is False
    assert by_id["eglantine"]["exclude_keywords"] == "Foo"
    assert by_id["newfork"]["remote"] is True
    assert "gone" not in by_id
    assert merge_remote_sources(cfg, remote) is False


def test_sources_json_matches_builtins():
    import json
    from pathlib import Path

    from eglantine_launcher.config import DEFAULT_PROJECTS

    shipped = json.loads((Path(__file__).resolve().parents[1] / "data" / "sources.json").read_text())[
        "sources"
    ]
    for default in DEFAULT_PROJECTS:
        match = next(s for s in shipped if s["id"] == default["id"])
        for field in ("name", "source_url", "branch", "releases_url"):
            assert match[field] == default[field], field


def test_shortcut_install_remove_roundtrip(tmp_path, fake_home, monkeypatch):
    from eglantine_launcher import tasks

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    cfg = {
        "project": "eglantine",
        "install_dir": str(tmp_path / "install"),
        "projects": [{"id": "eglantine", "name": "Eglantine", "icon_url": None}],
    }
    path = tasks.install_shortcut(cfg, "eglantine", "/app/root")
    assert path.name == "eglantine-launcher-eglantine.desktop"
    text = path.read_text()
    assert "Name=Eglantine (EL)" in text
    assert "--auto --project eglantine" in text
    assert tasks.shortcut_path(cfg, "eglantine").exists()
    assert tasks.remove_shortcut(cfg, "eglantine") is True
    assert not path.exists()


def test_shortcut_name_fallback():
    from eglantine_launcher.tasks import shortcut_name

    assert shortcut_name({"id": "abc"}) == "abc (EL)"
    assert shortcut_name({}) == "Eglantine (EL)"


def _url_fixture(monkeypatch, releases, tags):
    import urllib.request

    from conftest import FakeResponse

    def fake(url_or_req, timeout=None):
        url = url_or_req.full_url if hasattr(url_or_req, "full_url") else url_or_req
        if "/tags" in url:
            return FakeResponse(tags)
        return FakeResponse(releases)

    monkeypatch.setattr(urllib.request, "urlopen", fake)


def _match_cfg(**overrides):
    info = {
        "id": "t",
        "name": "Test",
        "source_url": "https://github.com/o/r.git",
        "branch": "main",
        "releases_url": "https://api.github.com/repos/o/r/releases",
        "include_prerelease": True,
    }
    info.update(overrides)
    return {"project": "t", "pgo": False, "projects": [info]}


def test_find_release_for_commit_exact_match(monkeypatch):
    from eglantine_launcher.tasks import find_release_for_commit

    releases = [
        {
            "tag_name": "build-99",
            "name": "AMD experimental",
            "body": "",
            "prerelease": True,
            "assets": [
                {
                    "name": "eglantine-a42b4d4c744e12ce5262648eee50daecd1ca456-linux-x86_64.AppImage",
                    "browser_download_url": "https://x/a",
                }
            ],
        },
        {
            "tag_name": "build-98",
            "name": "regular",
            "body": "",
            "prerelease": True,
            "assets": [
                {
                    "name": "eglantine-def456-linux-x86_64.AppImage",
                    "browser_download_url": "https://x/b",
                }
            ],
        },
    ]
    tags = [
        {"name": "build-99", "commit": {"sha": "a42b4d4c744e12ce5262648eee50daecd1ca456"}},
        {"name": "build-98", "commit": {"sha": "def4560000000000000000000000000000000000"}},
    ]
    _url_fixture(monkeypatch, releases, tags)
    cfg = _match_cfg()
    assert find_release_for_commit(cfg, "a42b4d4c744e12ce5262648eee50daecd1ca456") == "build-99"
    assert find_release_for_commit(cfg, "def4560000000000000000000000000000000000") == "build-98"
    assert find_release_for_commit(cfg, "0000000000000000000000000000000000000000") is None
    assert find_release_for_commit(cfg, "a42b4d4") is None
    assert find_release_for_commit(cfg, "") is None


def test_find_release_for_commit_no_asset_falls_back_to_source(monkeypatch):
    from eglantine_launcher.tasks import find_release_for_commit

    releases = [
        {
            "tag_name": "build-99",
            "name": "Windows only",
            "body": "",
            "prerelease": True,
            "assets": [{"name": "eglantine-win64.zip", "browser_download_url": "https://x/a"}],
        }
    ]
    tags = [{"name": "build-99", "commit": {"sha": "a42b4d4c744e12ce5262648eee50daecd1ca456"}}]
    _url_fixture(monkeypatch, releases, tags)
    cfg = _match_cfg()
    # commit matches, but no Linux asset: build from source instead
    assert find_release_for_commit(cfg, "a42b4d4c744e12ce5262648eee50daecd1ca456") is None


def test_release_commit_map_forgejo_uses_real_host(monkeypatch):
    import urllib.request

    from conftest import FakeResponse

    from eglantine_launcher.tasks import _release_commit_map

    seen = []
    tags = [{"name": "build-7", "commit": {"sha": "abc123"}}]

    def fake(url_or_req, timeout=None):
        url = url_or_req.full_url if hasattr(url_or_req, "full_url") else url_or_req
        seen.append(url)
        return FakeResponse(tags)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    project = {"source_url": "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork.git"}
    assert _release_commit_map({}, project) == {"build-7": "abc123"}
    assert len(seen) == 1
    assert seen[0].startswith("https://git.eden-emu.dev/api/v1/repos/Franseven/")


def test_forgejo_calls_use_url_host(monkeypatch):
    import urllib.request

    from conftest import FakeResponse

    from eglantine_launcher import tasks

    seen = []

    def fake(url_or_req, timeout=None):
        url = url_or_req.full_url if hasattr(url_or_req, "full_url") else url_or_req
        seen.append(url)
        if "/branches" in url:
            return FakeResponse([{"name": "master", "commit": {}}])
        if "/tags" in url:
            return FakeResponse([{"name": "build-7", "commit": {"sha": "abc123"}}])
        if "/commits" in url:
            return FakeResponse([{"sha": "abc123", "commit": {"message": "m"}}])
        return FakeResponse({"name": "r", "default_branch": "master"})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    url = "https://forge.example.com/o/r.git"
    cfg = {
        "project": "t",
        "projects": [{"id": "t", "source_url": url, "branch": "master"}],
    }
    assert tasks.list_commits(cfg)[0]["sha"] == "abc123"
    assert tasks.list_branches(url, cfg={}) == ["master"]
    assert tasks._release_commit_map(cfg, {"source_url": url}) == {"build-7": "abc123"}
    assert tasks.fetch_repo_info(url, {}) == {"name": "r", "default_branch": "master"}
    assert seen
    assert all(u.startswith("https://forge.example.com/api/v1/") for u in seen)


def test_bad_source_url_makes_no_api_call(monkeypatch):
    import urllib.request

    from eglantine_launcher import tasks

    def fake(url_or_req, timeout=None):
        raise AssertionError(f"no network expected: {url_or_req}")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    cfg = {
        "project": "t",
        "projects": [{"id": "t", "source_url": "not a url", "branch": "master"}],
    }
    assert tasks.list_commits(cfg) == []
    assert tasks.list_branches("not a url", cfg={}) == []
    assert tasks._release_commit_map(cfg, {"source_url": "not a url"}) == {}
    assert tasks.fetch_repo_info("not a url", {}) == {}
    assert tasks.infer_releases_url("not a url") is None


def test_format_size_units():
    from eglantine_launcher.tasks import format_size

    assert format_size(0) == "0 B"
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2.0 KiB"
    assert format_size(3 * 1024**3) == "3.0 GiB"


def _storage_cfg(tmp_path, **overrides):
    install = tmp_path / "install"
    source = tmp_path / "source"
    install.mkdir()
    source.mkdir()
    cfg = {
        "project": "eglantine",
        "mode": "source",
        "install_dir": str(install),
        "source_dir": str(source),
        "downloaded_releases": [],
        "built_commits": [],
        "last_release_tag": None,
        "last_source_commit": None,
    }
    cfg.update(overrides)
    return cfg


def test_project_storage_lists_release_and_source(monkeypatch, tmp_path, fake_home):
    import eglantine_launcher.tasks as tasks

    cfg = _storage_cfg(tmp_path)
    app = tmp_path / "install" / "eglantine" / "eglantine.AppImage"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"x" * 100)
    src = tmp_path / "source" / "eglantine"
    (src / ".git").mkdir(parents=True)
    (src / "big.bin").write_bytes(b"y" * 200)
    monkeypatch.setattr(tasks, "_host_check_output", lambda *a, **k: "abc123def456\n")
    rows = tasks.project_storage(cfg, "eglantine")
    kinds = {r["kind"]: r for r in rows}
    assert set(kinds) == {"release", "source"}
    assert kinds["release"]["size"] == 100
    assert kinds["release"]["in_use"] is False  # mode is source
    assert kinds["source"]["in_use"] is True
    assert kinds["source"]["size"] >= 200
    assert "abc123d" in kinds["source"]["label"]


def test_project_storage_hides_missing_and_unknown_project(monkeypatch, tmp_path, fake_home):
    import eglantine_launcher.tasks as tasks

    cfg = _storage_cfg(tmp_path)
    monkeypatch.setattr(tasks, "_host_check_output", lambda *a, **k: "")
    assert tasks.project_storage(cfg, "eglantine") == []
    assert tasks.project_storage(cfg, "") == []
    assert tasks.project_storage(cfg, None) == []


def test_delete_storage_release_fixes_config(tmp_path, fake_home):
    import eglantine_launcher.tasks as tasks

    cfg = _storage_cfg(tmp_path, mode="source", last_release_tag="build-9", downloaded_releases=["build-9"])
    app = tmp_path / "install" / "eglantine" / "eglantine.AppImage"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"x")
    rows = tasks.project_storage(cfg, "eglantine")
    assert len(rows) == 1
    assert tasks.delete_storage_row(cfg, rows[0]) is None
    assert not app.exists()
    assert cfg["last_release_tag"] is None
    assert cfg["downloaded_releases"] == []


def test_delete_storage_source_prunes_commit(monkeypatch, tmp_path, fake_home):
    import eglantine_launcher.tasks as tasks

    head = "a" * 40
    cfg = _storage_cfg(tmp_path, mode="release", last_source_commit=head, built_commits=[head, "b" * 40])
    src = tmp_path / "source" / "eglantine"
    (src / ".git").mkdir(parents=True)
    monkeypatch.setattr(tasks, "_host_check_output", lambda *a, **k: head + "\n")
    rows = tasks.project_storage(cfg, "eglantine")
    assert len(rows) == 1 and rows[0]["in_use"] is False
    assert tasks.delete_storage_row(cfg, rows[0]) is None
    assert not src.exists()
    assert cfg["last_source_commit"] is None
    assert cfg["built_commits"] == ["b" * 40]


def test_delete_storage_refuses_in_use_and_outside_dirs(tmp_path, fake_home):
    import eglantine_launcher.tasks as tasks

    cfg = _storage_cfg(tmp_path, mode="release")
    assert tasks.delete_storage_row(cfg, {"kind": "release", "in_use": True}) is not None
    evil = tmp_path / "elsewhere.bin"
    evil.write_bytes(b"x")
    row = {"kind": "release", "pid": "eglantine", "path": str(evil), "in_use": False}
    assert tasks.delete_storage_row(cfg, row) is not None
    assert evil.exists()
    assert tasks.delete_storage_row(cfg, {"kind": "bogus"}) is not None


def test_parse_branch_timestamp():
    from eglantine_launcher.tasks import _parse_branch_timestamp

    assert _parse_branch_timestamp("2026-10-31T00:16:57Z").year == 2026
    assert _parse_branch_timestamp("2026-11-01T19:49:46+01:00") is not None
    assert _parse_branch_timestamp("") is None
    assert _parse_branch_timestamp(None) is None
    assert _parse_branch_timestamp("not-a-date") is None


def test_sort_branch_names_default_first_then_newest():
    from datetime import datetime, timezone

    from eglantine_launcher.tasks import sort_branch_names

    names = ["b-old", "main", "b-new"]
    dates = {
        "b-old": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "b-new": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "main": datetime(2020, 1, 1, tzinfo=timezone.utc),
    }
    assert sort_branch_names(names, dates, "main") == ["main", "b-new", "b-old"]
    # undated branches keep API order after dated ones
    assert sort_branch_names(["z", "a", "main"], {}, "main") == ["main", "z", "a"]
    # unknown default leaves date-desc order intact
    assert sort_branch_names(["b-old", "b-new"], dates, "ghost") == ["b-new", "b-old"]


def test_get_default_branch(monkeypatch):
    import urllib.request

    from conftest import FakeResponse

    from eglantine_launcher import tasks

    def fake(url_or_req, timeout=None):
        url = url_or_req.full_url if hasattr(url_or_req, "full_url") else url_or_req
        assert "/branches" not in url and "/commits" not in url
        return FakeResponse({"name": "Eden", "default_branch": "nx2"})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert tasks.get_default_branch("https://github.com/o/r.git", {}) == "nx2"


def test_list_branches_forgejo_sorts_by_date(monkeypatch):
    import urllib.request

    from conftest import FakeResponse

    from eglantine_launcher import tasks

    branches = [
        {"name": "old", "commit": {"timestamp": "2024-01-01T00:00:00Z"}},
        {"name": "master", "commit": {"timestamp": "2020-01-01T00:00:00Z"}},
        {"name": "new", "commit": {"timestamp": "2026-01-01T00:00:00Z"}},
    ]

    def fake(url_or_req, timeout=None):
        url = url_or_req.full_url if hasattr(url_or_req, "full_url") else url_or_req
        if "/branches" in url:
            return FakeResponse(branches)
        return FakeResponse({"name": "r", "default_branch": "master"})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert tasks.list_branches("https://git.eden-emu.dev/o/r.git", cfg={}) == [
        "master",
        "new",
        "old",
    ]


def test_list_branches_github_default_first(monkeypatch):
    import urllib.request

    from conftest import FakeResponse

    from eglantine_launcher import tasks

    branches = [
        {"name": "zebra", "commit": {}},
        {"name": "main", "commit": {}},
        {"name": "apple", "commit": {}},
    ]

    def fake(url_or_req, timeout=None):
        url = url_or_req.full_url if hasattr(url_or_req, "full_url") else url_or_req
        if "/branches" in url:
            return FakeResponse(branches)
        return FakeResponse({"name": "r", "default_branch": "main"})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert tasks.list_branches("https://github.com/o/r.git", cfg={}) == ["main", "zebra", "apple"]


def test_fetch_github_branch_dates(monkeypatch):
    from eglantine_launcher import tasks

    def fake_api(url, cfg=None, timeout=None):
        if "sha=new" in url:
            return [{"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}]
        if "sha=old" in url:
            return [{"commit": {"committer": {"date": "2024-01-01T00:00:00Z"}}}]
        raise AssertionError(url)

    monkeypatch.setattr(tasks, "api_get_json", fake_api)
    dates = tasks.fetch_github_branch_dates("https://github.com/o/r.git", ["new", "old"], cfg={})
    ordered = tasks.sort_branch_names(["old", "main", "new"], dates, "main")
    assert ordered == ["main", "new", "old"]


def _paged_branches(monkeypatch, pages):
    """Mock api_get_json with per-page branch payloads; repo default is main."""
    import urllib.request

    from conftest import FakeResponse

    calls = []

    def fake(url_or_req, timeout=None):
        url = url_or_req.full_url if hasattr(url_or_req, "full_url") else url_or_req
        calls.append(url)
        if "/branches" in url:
            import re

            m = re.search(r"[?&]page=(\d+)", url)
            page = int(m.group(1)) if m else 1
            return FakeResponse(pages[min(page, len(pages)) - 1] if page <= len(pages) else [])
        return FakeResponse({"name": "r", "default_branch": "main"})

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


def _branch_page(names):
    return [{"name": n, "commit": {}} for n in names]


def test_list_branches_paginates_github(monkeypatch):
    from eglantine_launcher import tasks

    full = ["main"] + [f"b{i:03d}" for i in range(1, 100)]
    calls = _paged_branches(monkeypatch, [_branch_page(full), _branch_page(["nx2-experimental"])])
    names = tasks.list_branches("https://github.com/o/r.git", cfg={})
    assert "nx2-experimental" in names
    assert len(names) == 101
    assert names[0] == "main"  # default pinned despite page order
    assert sum("/branches" in c for c in calls) == 2


def test_list_branches_stops_on_short_page(monkeypatch):
    from eglantine_launcher import tasks

    calls = _paged_branches(monkeypatch, [_branch_page(["main", "other"])])
    assert tasks.list_branches("https://github.com/o/r.git", cfg={}) == ["main", "other"]
    assert sum("/branches" in c for c in calls) == 1


def test_should_fetch_branch_dates():
    from eglantine_launcher.tasks import should_fetch_branch_dates

    assert should_fetch_branch_dates("https://github.com/o/r.git", ["a"] * 200, {"github_token": "t"})
    assert should_fetch_branch_dates("https://github.com/o/r.git", ["a"] * 30, {})
    assert not should_fetch_branch_dates("https://github.com/o/r.git", ["a"] * 31, {})
    assert not should_fetch_branch_dates("https://git.eden-emu.dev/o/r.git", ["a"], {"github_token": "t"})


def test_fetch_github_branch_dates_limit(monkeypatch):
    from eglantine_launcher import tasks

    seen = []

    def fake_api(url, cfg=None, timeout=None):
        seen.append(url)
        return [{"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}]

    monkeypatch.setattr(tasks, "api_get_json", fake_api)
    dates = tasks.fetch_github_branch_dates("https://github.com/o/r.git", ["one", "two"], cfg={}, limit=1)
    assert set(dates) == {"one"}
    assert len(seen) == 1


def test_remove_project_data_deletes_artifacts(tmp_path, fake_home, monkeypatch):
    from eglantine_launcher import tasks

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    install = tmp_path / "install"
    (install / "eglantine" / "subdir").mkdir(parents=True)
    (install / "eglantine" / "eglantine.AppImage").write_text("x")
    (install / "icons").mkdir(parents=True)
    (install / "icons" / "eglantine.png").write_text("x")
    (install / "other").mkdir()
    (install / "other" / "keep").write_text("x")
    src = tmp_path / "src"
    (src / "eglantine" / ".git").mkdir(parents=True)
    cfg = {"install_dir": str(install), "source_dir": str(src), "projects": []}
    tasks.install_shortcut(
        {**cfg, "projects": [{"id": "eglantine", "name": "Eglantine", "icon_url": None}]},
        "eglantine",
        "/app/root",
    )
    assert tasks.shortcut_path(cfg, "eglantine").exists()

    tasks.remove_project_data(cfg, "eglantine")

    assert not (install / "eglantine").exists()
    assert not (src / "eglantine").exists()
    assert not (install / "icons" / "eglantine.png").exists()
    assert not tasks.shortcut_path(cfg, "eglantine").exists()
    assert (install / "other" / "keep").read_text() == "x"
    assert install.is_dir() and src.is_dir()


def test_remove_project_data_rejects_unsafe_ids(tmp_path):
    from eglantine_launcher import tasks

    install = tmp_path / "install"
    install.mkdir()
    (install / "victim").mkdir()
    cfg = {"install_dir": str(install), "source_dir": str(tmp_path / "src"), "projects": []}
    tasks.remove_project_data(cfg, "../victim")
    tasks.remove_project_data(cfg, "")
    tasks.remove_project_data(cfg, None)
    assert (install / "victim").is_dir()


def test_source_version_entries_ticks_have():
    from eglantine_launcher.tasks import source_version_entries

    commits = [
        {"sha": "aaa1111", "commit": {"message": "new stuff"}},
        {"sha": "bbb2222", "commit": {"message": "old stuff"}},
    ]
    labels, shas, subs, latest = source_version_entries(commits, {"bbb2222"})
    assert labels == ["aaa1111 new stuff", "✓ bbb2222 old stuff"]
    assert shas == ["aaa1111", "bbb2222"]
    assert subs[0].startswith("aaa1111\n")
    assert latest == "aaa1111"


def test_source_version_entries_forgejo_id_and_skips():
    from eglantine_launcher.tasks import source_version_entries

    commits = [{"id": "abc123"}, {"nope": True}, {"sha": "def456", "commit": {}}]
    labels, shas, subs, latest = source_version_entries(commits)
    assert shas == ["abc123", "def456"]
    assert labels == ["abc123 ", "def456 "]
    assert latest == "abc123"


def test_source_version_entries_appends_installed():
    from eglantine_launcher.tasks import source_version_entries

    commits = [{"sha": "new111", "commit": {"message": "upstream moved on"}}]
    labels, shas, subs, latest = source_version_entries(
        commits, {"new111"}, installed_sha="old999", installed_present=True
    )
    assert latest == "new111"
    assert shas == ["new111", "old999"]
    assert labels[0] == "✓ new111 upstream moved on"
    assert labels[1] == "✓ old999 (installed build)"
    assert subs[1].startswith("old999\n")


def test_source_version_entries_latest_survives_rewritten_history():
    from eglantine_launcher.tasks import source_version_entries

    commits = [{"sha": "a407703f", "commit": {"message": "newest upstream"}}]
    labels, shas, subs, latest = source_version_entries(
        commits, set(), installed_sha="be71c425", installed_present=True
    )
    assert latest == "a407703f"
    assert shas[0] == "a407703f"
    assert shas[-1] == "be71c425"


def test_source_version_entries_no_append_when_visible_or_missing():
    from eglantine_launcher.tasks import source_version_entries

    commits = [{"sha": "aaa", "commit": {"message": "m"}}]
    _, shas, _, _ = source_version_entries(commits, set(), "aaa", True)
    assert shas == ["aaa"]
    _, shas, _, _ = source_version_entries(commits, set(), "zzz", False)
    assert shas == ["aaa"]
    _, shas, _, latest = source_version_entries([], set(), None, False)
    assert shas == [] and latest is None
    _, shas, _, latest = source_version_entries([], set(), "zzz", True)
    assert shas == ["zzz"] and latest == "zzz"


def test_parse_pr_url_github():
    from eglantine_launcher.tasks import parse_pr_url

    parsed = parse_pr_url("https://github.com/Djihads80/Eglantine/pull/366")
    assert parsed == {
        "host_kind": "github",
        "web_host": "github.com",
        "scheme": "https",
        "owner": "Djihads80",
        "repo": "Eglantine",
        "number": 366,
        "canonical": "https://github.com/Djihads80/Eglantine/pull/366",
    }


def test_parse_pr_url_forgejo_pulls_and_pull():
    from eglantine_launcher.tasks import parse_pr_url

    for url in (
        "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork/pulls/55",
        "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork/pull/55",
        "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork/pulls/55/files?x=1#y",
    ):
        parsed = parse_pr_url(url)
        assert parsed["host_kind"] == "forgejo"
        assert parsed["owner"] == "Franseven"
        assert parsed["number"] == 55
        assert parsed["canonical"].endswith("/pulls/55")


def test_parse_pr_url_rejects_non_pr():
    from eglantine_launcher.tasks import parse_pr_url

    assert parse_pr_url("https://github.com/Djihads80/Eglantine") is None
    assert parse_pr_url("https://github.com/Djihads80/Eglantine/issues/1") is None
    assert parse_pr_url("not a url") is None
    assert parse_pr_url("") is None
    assert parse_pr_url(None) is None


def test_pr_api_url_uses_real_hosts():
    from eglantine_launcher.tasks import parse_pr_url, pr_api_url

    assert (
        pr_api_url(parse_pr_url("https://github.com/o/r/pull/7"))
        == "https://api.github.com/repos/o/r/pulls/7"
    )
    assert (
        pr_api_url(parse_pr_url("https://git.eden-emu.dev/o/r/pulls/9"))
        == "https://git.eden-emu.dev/api/v1/repos/o/r/pulls/9"
    )
    assert (
        pr_api_url(parse_pr_url("https://forge.example.com:3000/o/r/pull/3"))
        == "https://forge.example.com:3000/api/v1/repos/o/r/pulls/3"
    )
    assert pr_api_url(None) is None


def test_fetch_pr_info_github(monkeypatch):
    from eglantine_launcher import tasks

    payload = {
        "title": "Fix shaders",
        "state": "open",
        "merged_at": None,
        "head": {"sha": "a" * 40, "ref": "fix", "repo": {"clone_url": "https://github.com/u/r.git"}},
        "base": {"ref": "nx2", "repo": {"clone_url": "https://github.com/Djihads80/Eglantine.git"}},
    }
    monkeypatch.setattr(tasks, "api_get_json", lambda url, cfg=None, timeout=None: payload)
    info = tasks.fetch_pr_info("https://github.com/Djihads80/Eglantine/pull/366", {})
    assert info["title"] == "Fix shaders"
    assert info["state"] == "open"
    assert info["merged"] is False
    assert info["head_sha"] == "a" * 40
    assert info["base_clone_url"] == "https://github.com/Djihads80/Eglantine.git"
    assert info["number"] == 366


def test_fetch_pr_info_merged_state(monkeypatch):
    from eglantine_launcher import tasks

    payload = {
        "title": "Done",
        "state": "closed",
        "merged_at": "2026-01-01T00:00:00Z",
        "head": {"sha": "b" * 40},
        "base": {},
    }
    monkeypatch.setattr(tasks, "api_get_json", lambda url, cfg=None, timeout=None: payload)
    info = tasks.fetch_pr_info("https://github.com/o/r/pull/1", {})
    assert info["state"] == "merged"
    assert info["merged"] is True
    assert info["base_clone_url"] == "https://github.com/o/r.git"


def test_fetch_pr_info_forgejo(monkeypatch):
    from eglantine_launcher import tasks

    seen = []
    payload = {
        "title": "Dinner update",
        "state": "open",
        "head": {"sha": "c" * 40, "ref": "feat"},
        "base": {"ref": "master"},
    }

    def fake(url, cfg=None, timeout=None):
        seen.append(url)
        return payload

    monkeypatch.setattr(tasks, "api_get_json", fake)
    info = tasks.fetch_pr_info("https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork/pulls/55", {})
    assert seen[0].startswith("https://git.eden-emu.dev/api/v1/repos/Franseven/")
    assert info["head_sha"] == "c" * 40
    assert info["base_clone_url"].endswith(".git")


def test_fetch_pr_info_bad_url_raises():
    import pytest

    from eglantine_launcher import tasks

    with pytest.raises(RuntimeError):
        tasks.fetch_pr_info("https://github.com/o/r", {})


def test_make_pr_project_defaults_and_edit():
    from eglantine_launcher.tasks import make_pr_project

    details = {
        "title": "Fix shaders",
        "state": "open",
        "head_sha": "a" * 40,
        "base_clone_url": "https://github.com/o/r.git",
        "number": 366,
        "owner": "o",
        "repo": "r",
    }
    proj = make_pr_project("", "https://github.com/o/r/pull/366", details)
    assert proj["is_pr"] is True
    assert proj["id"].startswith("pr-r-366-")
    assert proj["name"] == "Fix shaders (#366)"
    assert proj["releases_url"] is None
    assert proj["pr_head_sha"] == "a" * 40
    edited = make_pr_project("Mine", "https://github.com/o/r/pull/366", details, existing=proj)
    assert edited["id"] == proj["id"]
    assert edited["name"] == "Mine"


def test_refresh_pr_project_updates_in_place():
    from eglantine_launcher.tasks import refresh_pr_project

    proj = {"source_url": "old", "pr_state": "open", "pr_head_sha": "old", "pr_title": "old"}
    refresh_pr_project(
        proj, {"base_clone_url": "new", "state": "merged", "head_sha": "n" * 40, "title": "New"}
    )
    assert proj == {"source_url": "new", "pr_state": "merged", "pr_head_sha": "n" * 40, "pr_title": "New"}


def test_pr_version_entries_tick_and_installed():
    from eglantine_launcher.tasks import pr_version_entries

    labels, shas, subs, latest = pr_version_entries("Fix shaders", "a" * 40, "open", {"a" * 40})
    assert shas == ["a" * 40] and latest == "a" * 40
    assert labels == [f"✓ {'a' * 7} Fix shaders (open)"]
    assert subs[0].startswith(("a" * 40) + "\n")
    labels, shas, _, latest = pr_version_entries(
        "T", "a" * 40, "open", set(), installed_sha="b" * 40, installed_present=True
    )
    assert shas == ["a" * 40, "b" * 40]
    assert labels[1] == f"✓ {'b' * 7} (installed build)"
    assert latest == "a" * 40
    assert pr_version_entries("T", "", "open") == ([], [], [], None)


def test_is_pr_and_per_project_build_record(fake_home):
    from eglantine_launcher import tasks
    from eglantine_launcher.config import load

    assert tasks.is_pr_project({"is_pr": True}) is True
    assert tasks.is_pr_project({"id": "x"}) is False
    cfg = load()
    cfg["project"] = "eglantine"
    tasks.record_source_build(cfg, "a" * 40)
    assert cfg["last_source_commit"] == "a" * 40
    assert next(p for p in cfg["projects"] if p["id"] == "eglantine")["last_built_commit"] == "a" * 40
    assert tasks.get_project_last_built(cfg) == "a" * 40
    # per-project value wins over a stale global from another project
    cfg["last_source_commit"] = "stale"
    assert tasks.get_project_last_built(cfg) == "a" * 40


def test_ensure_source_pr_fresh_clone(monkeypatch, tmp_path, fake_home):
    from eglantine_launcher import tasks

    src_base = tmp_path / "src"
    src_base.mkdir()
    cfg = {
        "project": "pr-x",
        "source_dir": str(src_base),
        "projects": [
            {
                "id": "pr-x",
                "is_pr": True,
                "pr_number": 366,
                "source_url": "https://github.com/o/r.git",
                "pr_head_sha": "a" * 40,
            }
        ],
    }
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["git", "clone"]:
            Path(cmd[-1]).mkdir(parents=True)
            (Path(cmd[-1]) / ".git").mkdir()
            return None
        if cmd[-2:] == ["rev-parse", "HEAD"]:
            raise AssertionError("rev-parse must go through check_output")
        return None

    def fake_out(cmd, **kw):
        if "--verify" in cmd:
            raise Exception("no local branch yet")
        return "a" * 40 + "\n"

    monkeypatch.setattr(tasks, "_host_run", fake_run)
    monkeypatch.setattr(tasks, "_host_check_output", fake_out)
    src, commit = tasks.ensure_source(cfg)
    assert commit == "a" * 40
    assert any("clone" in c for c in calls)
    assert any("pull/366/head" in " ".join(c) for c in calls)
    assert any("checkout" in c for c in calls)


def test_ensure_source_pr_fetch_falls_back_to_pulls(monkeypatch, tmp_path, fake_home):
    from eglantine_launcher import tasks

    src = tmp_path / "src" / "pr-x"
    (src / ".git").mkdir(parents=True)
    cfg = {
        "project": "pr-x",
        "source_dir": str(tmp_path / "src"),
        "projects": [{"id": "pr-x", "is_pr": True, "pr_number": 55, "source_url": "https://x/y.git"}],
    }
    fetched = []

    def fake_run(cmd, **kw):
        if "fetch" in cmd:
            fetched.append(cmd[-1])
            if "pull/55" in cmd[-1]:
                raise Exception("no such ref")
            return None
        if "remote" in cmd:
            return None
        return None

    monkeypatch.setattr(tasks, "_host_run", fake_run)
    monkeypatch.setattr(tasks, "_host_check_output", lambda *a, **k: "c" * 40 + "\n")
    _, commit = tasks.ensure_source(cfg, commit="c" * 40)
    assert commit == "c" * 40
    assert fetched == ["+pull/55/head:pr-55", "+pulls/55/head:pr-55"]


def test_remove_pr_project_full_cleanup(tmp_path, fake_home, monkeypatch):
    from eglantine_launcher import tasks

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    install = tmp_path / "install"
    src = tmp_path / "src"
    (install / "pr-r-55-abcdef").mkdir(parents=True)
    (src / "pr-r-55-abcdef" / ".git").mkdir(parents=True)
    cfg = {
        "project": "pr-r-55-abcdef",
        "install_dir": str(install),
        "source_dir": str(src),
        "built_commits": ["a" * 40, "b" * 40],
        "last_source_commit": "a" * 40,
        "projects": [
            {
                "id": "pr-r-55-abcdef",
                "is_pr": True,
                "last_built_commit": "a" * 40,
                "pr_head_sha": "a" * 40,
            },
            {"id": "eglantine", "name": "Eglantine"},
        ],
    }
    monkeypatch.setattr(tasks, "_host_check_output", lambda *a, **k: "a" * 40 + "\n")
    assert tasks.remove_pr_project(cfg, "pr-r-55-abcdef") is True
    assert [p["id"] for p in cfg["projects"]] == ["eglantine"]
    assert cfg["project"] == "eglantine"
    assert cfg["built_commits"] == ["b" * 40]
    assert cfg["last_source_commit"] is None
    assert not (src / "pr-r-55-abcdef").exists()
    assert tasks.remove_pr_project(cfg, "missing") is False
