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
