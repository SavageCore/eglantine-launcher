"""Config file load/save behaviour (no display, no network)."""

import json

from eglantine_launcher import config


def _read():
    return json.loads(config.config_file().read_text())


def test_save_keeps_token_written_by_another_caller(fake_home):
    cfg = config.load()
    cfg["github_token"] = "ghp_example"
    config.save(cfg)

    # A stale snapshot without the token must not erase it.
    config.save({"project": "eglantine"})

    assert _read()["github_token"] == "ghp_example"


def test_save_writes_back_to_the_loaded_file(fake_home, monkeypatch, tmp_path):
    cfg = config.load()
    original = config.config_file()
    other = tmp_path / "elsewhere"

    monkeypatch.setenv("HOME", str(other))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(other / ".config"))
    cfg["project"] = "pokedinner"
    config.save(cfg)

    assert json.loads(original.read_text())["project"] == "pokedinner"
    assert not (other / ".config" / "eglantine-launcher" / "config.json").exists()


def test_internal_path_is_never_stored(fake_home):
    cfg = config.load()
    cfg["project"] = "pokedinner"
    config.save(cfg)

    assert config._CONFIG_PATH_KEY not in _read()


def test_cleared_rejected_flag_sticks(fake_home):
    cfg = config.load()
    cfg["github_token"] = "ghp_example"
    cfg["github_token_rejected"] = True
    config.save(cfg)

    cfg["github_token_rejected"] = False
    config.save(cfg)

    assert _read()["github_token_rejected"] is False
    assert _read()["github_token"] == "ghp_example"


def test_tmp_tree_paths_never_persist(fake_home, tmp_path):
    cfg = config.load()
    cfg["install_dir"] = "/tmp/pytest-of-savagecore/pytest-1/home/.local/share/eglantine-launcher"
    cfg["source_dir"] = "/tmp/pytest-of-savagecore/pytest-1/home/.local/share/eglantine-launcher/src"
    config.save(cfg)

    stored = _read()
    assert stored["install_dir"] == config.default_config()["install_dir"]
    assert stored["source_dir"] == config.default_config()["source_dir"]


def test_tmp_tree_paths_prefer_on_disk_values(fake_home, tmp_path):
    cfg = config.load()
    cfg["install_dir"] = str(fake_home / "real-install")
    config.save(cfg)

    stale = config.load()
    stale["install_dir"] = "/tmp/pytest-of-savagecore/pytest-1/home/.local/share/eglantine-launcher"
    config.save(stale)

    assert _read()["install_dir"] == str(fake_home / "real-install")


def test_load_heals_clobbered_file(fake_home):
    healed = config.load()
    assert healed["install_dir"] == config.default_config()["install_dir"]

    path = config.config_file()
    raw = json.loads(path.read_text())
    raw["install_dir"] = "/tmp/pytest-of-savagecore/pytest-1/home/x"
    raw["source_dir"] = "/tmp/pytest-of-savagecore/pytest-1/home/y"
    path.write_text(json.dumps(raw))

    healed = config.load()
    assert healed["install_dir"] == config.default_config()["install_dir"]
    assert healed["source_dir"] == config.default_config()["source_dir"]


def _old_style_projects():
    """Pre-origins config: two shipped defaults, one custom, no stamps."""
    return [
        {
            "id": "eglantine",
            "name": "Eglantine",
            "source_url": "https://github.com/Djihads80/Eglantine.git",
            "branch": "custom-branch",
            "releases_url": "https://api.github.com/repos/Djihads80/Eglantine/releases",
            "include_prerelease": True,
        },
        {
            "id": "pokedinner",
            "name": "PokeDinner (Pokopia)",
            "source_url": "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork.git",
            "branch": "master",
            "releases_url": "https://git.eden-emu.dev/api/v1/repos/Franseven/PokeDinner-a-Eglantine-Fork/releases",
            "include_prerelease": True,
        },
        {
            "id": "mine",
            "name": "Mine",
            "source_url": "https://example.com/mine.git",
            "branch": "main",
        },
    ]


def test_ensure_adopts_preexisting_defaults_without_clobbering(fake_home):
    cfg = config.load()
    cfg["projects"] = _old_style_projects()
    config.save(cfg)

    changed = config.ensure_default_sources(cfg)

    assert changed
    by_id = {p["id"]: p for p in cfg["projects"]}
    # adopted entries gain curation but keep the user's custom branch
    assert by_id["eglantine"]["origin"] == "wichforks"
    assert by_id["eglantine"]["branch"] == "custom-branch"
    assert by_id["eglantine"]["best_for"]
    assert by_id["pokedinner"]["best_for"]
    # missing defaults are added, custom entries stamped manual and untouched
    assert "smiley" in by_id
    assert by_id["mine"]["origin"] == "manual"
    assert "best_for" not in by_id["mine"]
    # second run refreshes adopted entries fully
    assert config.ensure_default_sources(cfg)
    assert by_id["eglantine"]["branch"] == "nx2"


def test_ensure_respects_hidden_and_manual(fake_home):
    cfg = config.load()
    cfg["projects"] = _old_style_projects()
    cfg["hidden_sources"] = ["smiley"]
    config.save(cfg)

    config.ensure_default_sources(cfg)
    by_id = {p["id"]: p for p in cfg["projects"]}
    assert "smiley" not in by_id
    assert "orchid" in by_id

    # a manual origin on a shipped id is never managed
    by_id["orchid"]["origin"] = "manual"
    by_id["orchid"]["branch"] = "mine"
    before = list(by_id["orchid"]["best_for"])
    config.ensure_default_sources(cfg)
    assert by_id["orchid"]["branch"] == "mine"
    assert by_id["orchid"]["best_for"] == before


def test_project_tooltip_has_sections(fake_home):
    tip = config.project_tooltip(
        {
            "best_for": [{"title_id": "0400C3F00006E000", "name": "Mario Kart World", "note": "Fast"}],
            "tags": [{"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"}],
            "confidence": "documented",
        }
    )
    assert "Best for:\n- Mario Kart World: Fast" in tip
    assert "Tags:\n- fix: Fixes mods on NUP" in tip
    assert "\n\n" in tip
    assert config.project_tooltip({}) == ""


def test_effective_overlay_user_wins_and_merges():
    project = {
        "best_for": [{"title_id": "0400C3F00006E000", "name": "Mario Kart World", "note": "Up"}],
        "tags": [{"id": "amd", "label": "AMD", "tooltip": "Up"}],
        "user_best_for": [
            {"title_id": "0400C3F00006E000", "name": "Mario Kart World", "note": "Mine"},
            {"title_id": "040074A01BF12000", "name": "Donkey Kong Bananza"},
        ],
        "user_tags": [
            {"id": "amd", "label": "AMD", "tooltip": "Mine"},
            {"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"},
        ],
    }
    best = config.effective_best_for(project)
    assert [b["title_id"] for b in best] == ["0400C3F00006E000", "040074A01BF12000"]
    assert best[0]["note"] == "Mine"
    tags = config.effective_tags(project)
    assert [t["id"] for t in tags] == ["amd", "fix"]
    assert tags[0]["tooltip"] == "Mine"
    assert "Donkey Kong Bananza" in config.best_for_summary(project)
    assert "- fix: Fixes mods on NUP" in config.project_tooltip(project)


def test_project_tooltip_can_hide_tags(fake_home):
    project = {
        "best_for": [{"title_id": "0400C3F00006E000", "name": "Mario Kart World", "note": "Fast"}],
        "tags": [{"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"}],
        "confidence": "documented",
    }
    tip = config.project_tooltip(project, include_tags=False)
    assert "Mario Kart World" in tip
    assert "fix" not in tip
    assert "Confidence: documented" in tip


def test_merge_remote_preserves_user_overlay(fake_home):
    cfg = config.load()
    target = next(p for p in cfg["projects"] if p["id"] == "eglantine")
    target["user_best_for"] = [{"title_id": "040074A01BF12000", "name": "Donkey Kong Bananza"}]
    target["user_tags"] = [{"id": "fix", "label": "fix", "tooltip": "t"}]
    remote = [
        {
            "id": "eglantine",
            "name": "Eglantine!",
            "source_url": target["source_url"],
            "branch": target["branch"],
            "releases_url": target["releases_url"],
            "origin": "wichforks",
            "best_for": [{"title_id": "0400C3F00006E000", "name": "Mario Kart World"}],
            "tags": [],
            "confidence": "documented",
        }
    ]
    assert config.merge_remote_sources(cfg, remote)
    target = next(p for p in cfg["projects"] if p["id"] == "eglantine")
    assert target["name"] == "Eglantine!"
    assert target["user_best_for"] == [{"title_id": "040074A01BF12000", "name": "Donkey Kong Bananza"}]
    assert target["user_tags"] == [{"id": "fix", "label": "fix", "tooltip": "t"}]


def test_normalize_title_id():
    assert config.normalize_title_id("0400c3f00006e000") == "0400C3F00006E000"
    assert config.normalize_title_id(" 0400C3F00006E000 ") == "0400C3F00006E000"
    assert config.normalize_title_id("0x0400C3F00006E000") == "0400C3F00006E000"
    assert config.normalize_title_id("xyz") is None
    assert config.normalize_title_id("0400C3F00006E0") is None
    assert config.normalize_title_id("") is None
    assert config.normalize_title_id(None) is None


def test_game_catalog_merges_custom_games():
    catalog = config.game_catalog({"custom_games": [{"title_id": "0400999900000000", "name": "Mine"}]})
    assert catalog["0400999900000000"] == "Mine"
    assert catalog["0400C3F00006E000"] == "Mario Kart World"
    catalog = config.game_catalog({"custom_games": [{"title_id": "0400c3f00006e000", "name": "MKW Custom"}]})
    assert catalog["0400C3F00006E000"] == "MKW Custom"


def test_game_catalog_sorts_remote_only_games_into_place():
    """Simulates an older build: remote-only ids must not trail at the end."""
    remote = [
        {"title_id": "0400C3F00006E000", "name": "Mario Kart World"},
        {"title_id": "040032B023CA4000", "name": "Orbitals"},
        {"title_id": "040074A01BF12000", "name": "Donkey Kong Bananza"},
    ]
    names = list(config.game_catalog({"remote_games": remote}).values())
    assert names == sorted(names, key=config._sort_key)
    assert names.index("Orbitals") < names.index("Pok\u00e9mon Legends: Z-A")
    assert names.index("Nintendo Switch Sports Resort") < names.index("Orbitals")


def test_game_catalog_remote_overrides_shipped_custom_wins():
    remote = [{"title_id": "0400C3F00006E000", "name": "Remote Name"}]
    custom = [{"title_id": "0400C3F00006E000", "name": "My Name"}]
    assert config.game_catalog({"remote_games": remote})["0400C3F00006E000"] == "Remote Name"
    assert config.game_catalog({"remote_games": remote, "custom_games": custom})["0400C3F00006E000"] == (
        "My Name"
    )


def test_merge_remote_games_sanitizes_and_stores():
    cfg = {"projects": []}
    remote = [
        {"title_id": "0400999900000000", "name": " Good "},
        {"title_id": "xyz", "name": "Bad"},
        {"title_id": "", "name": "Empty"},
        {"title_id": "0400999900000000", "name": "Dupe"},
        "junk",
        {"title_id": "0400c3f00006e000", "name": "Lower"},
        {"title_id": "0400111111111111", "name": "  "},
    ]
    assert config.merge_remote_games(cfg, remote) is True
    assert cfg["remote_games"] == [
        {"title_id": "0400999900000000", "name": "Good"},
        {"title_id": "0400C3F00006E000", "name": "Lower"},
    ]
    assert config.merge_remote_games(cfg, remote) is False
    assert config.merge_remote_games(cfg, []) is True
    assert cfg["remote_games"] == []


def test_games_json_matches_builtins():
    import json
    from pathlib import Path

    shipped = {
        g["title_id"]: g["name"]
        for g in json.loads((Path(__file__).resolve().parents[1] / "data" / "games.json").read_text())[
            "games"
        ]
    }
    assert shipped == config.GAMES


def test_save_unions_disk_and_memory_projects(fake_home):
    """A stale writer keeps entries added elsewhere instead of dropping them."""
    cfg = config.load()
    assert not any(p.get("is_pr") for p in cfg["projects"])
    disk_pr = {"id": "pr-x", "name": "X", "is_pr": True}
    on_disk = config.load()
    on_disk["projects"].append(disk_pr)
    config.save(on_disk)
    config.save(cfg)
    ids = [p.get("id") for p in config.load()["projects"]]
    assert "pr-x" in ids
    assert "eglantine" in ids


def test_save_respects_tombstones(fake_home):
    """Explicit deletes stay deleted even when a stale writer saves."""
    cfg = config.load()
    cfg["projects"].append({"id": "mine", "name": "Mine"})
    config.save(cfg)
    assert "mine" in [p.get("id") for p in config.load()["projects"]]
    cfg = config.load()
    cfg["projects"] = [p for p in cfg["projects"] if p.get("id") != "mine"]
    cfg.setdefault("deleted_projects", []).append("mine")
    config.save(cfg)
    assert "mine" not in [p.get("id") for p in config.load()["projects"]]
    stale = config.load()
    stale["projects"].append({"id": "mine", "name": "Resurrected"})
    stale.pop("deleted_projects", None)
    config.save(stale)
    assert "mine" not in [p.get("id") for p in config.load()["projects"]]
    assert "mine" in config.load().get("deleted_projects", [])


def test_readded_id_clears_tombstone(fake_home):
    cfg = config.load()
    cfg["projects"].append({"id": "mine", "name": "Mine"})
    config.save(cfg)
    cfg = config.load()
    assert "mine" in [p.get("id") for p in cfg["projects"]]
    cfg["projects"] = [p for p in cfg["projects"] if p.get("id") != "mine"]
    cfg.setdefault("deleted_projects", []).append("mine")
    config.save(cfg)
    cfg = config.load()
    assert "mine" not in [p.get("id") for p in cfg["projects"]]
    cfg["projects"].append({"id": "mine", "name": "Back"})
    config.save(cfg)
    assert "mine" in [p.get("id") for p in config.load()["projects"]]
    assert "mine" not in config.load().get("deleted_projects", [])
