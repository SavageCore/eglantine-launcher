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
    assert infer_releases_url("not a url") is None


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
    monkeypatch.setattr(tasks, "ensure_source", lambda cfg, status_cb=None, commit=None: (src, "abc123"))
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
    monkeypatch.setattr(tasks, "ensure_source", lambda cfg, status_cb=None, commit=None: (src, "abc123"))
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


def _rel_cfg(monkeypatch, patch_urlopen):
    patch_urlopen(
        [
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
    )
    return {"project": "t", "pgo": False, "projects": [{"id": "t", "releases_url": "https://x/y"}]}


def test_find_release_for_commit_full_sha(patch_urlopen, monkeypatch):
    from eglantine_launcher.tasks import find_release_for_commit

    cfg = _rel_cfg(monkeypatch, patch_urlopen)
    assert find_release_for_commit(cfg, "a42b4d4c744e12ce5262648eee50daecd1ca456") == "build-99"


def test_find_release_for_commit_short_sha_in_tag(patch_urlopen, monkeypatch):
    import urllib.request

    from eglantine_launcher import tasks

    class Resp:
        def read(self_inner):
            import json

            return json.dumps(
                [
                    {
                        "tag_name": "v1.0-abc1234",
                        "name": "",
                        "body": "",
                        "prerelease": False,
                        "assets": [
                            {"name": "app-linux-x86_64.AppImage", "browser_download_url": "https://x"}
                        ],
                    }
                ]
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resp())
    cfg = {"project": "t", "pgo": False, "projects": [{"id": "t", "releases_url": "https://x/y"}]}
    assert tasks.find_release_for_commit(cfg, "abc1234def5678") == "v1.0-abc1234"
    assert tasks.find_release_for_commit(cfg, "0000000000000") is None
    assert tasks.find_release_for_commit(cfg, "") is None
