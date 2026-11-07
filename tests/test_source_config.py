"""Per-source emulator config tests (no display, no network)."""

import subprocess

from eglantine_launcher import tasks


def test_source_key_github():
    info = {"source_url": "https://github.com/Djihads80/Eglantine.git"}
    assert tasks.source_key(info) == "github-djihads80-eglantine"


def test_source_key_forgejo():
    info = {"source_url": "https://git.eden-emu.dev/Franseven/PokeDinner-a-Eglantine-Fork.git"}
    assert tasks.source_key(info) == "forgejo-franseven-pokedinner-a-eglantine-fork"


def test_source_key_pr_entry_shares_base_repo():
    base = {"id": "eglantine", "source_url": "https://github.com/Djihads80/Eglantine.git"}
    pr = {
        "id": "pr-eglantine-366-abc123",
        "is_pr": True,
        "source_url": "https://github.com/Djihads80/Eglantine.git",
    }
    assert tasks.source_key(pr) == tasks.source_key(base)


def test_source_key_override_is_slugged():
    info = {"config_key": "My Fork/Config", "source_url": "https://github.com/o/r.git"}
    assert tasks.source_key(info) == "my-fork-config"


def test_source_key_falls_back_to_project_id():
    assert tasks.source_key({"id": "local-build"}) == "project-local-build"


def test_known_config_keys_merges_projects_and_disk(fake_home):
    (fake_home / ".config" / "eglantine-launcher" / "sources" / "on-disk-only").mkdir(parents=True)
    cfg = {
        "projects": [
            {"id": "a", "name": "Alpha", "source_url": "https://github.com/o/r.git"},
            {"id": "b", "name": "Beta", "source_url": "https://github.com/o/r.git"},
            {
                "id": "c",
                "name": "Gamma",
                "source_url": "https://github.com/o/other.git",
                "config_key": "shared",
            },
        ]
    }
    assert dict(tasks.known_config_keys(cfg)) == {
        "github-o-r": ["Alpha", "Beta"],
        "on-disk-only": [],
        "shared": ["Gamma"],
    }


def test_known_config_keys_excludes_edited_source_name(fake_home):
    cfg = {"projects": [{"id": "a", "name": "Alpha", "source_url": "https://github.com/o/r.git"}]}
    assert tasks.known_config_keys(cfg, exclude_id="a") == [("github-o-r", [])]
    assert tasks.known_config_keys(cfg) == [("github-o-r", ["Alpha"])]


def test_source_config_home_is_under_launcher_config(fake_home):
    home = tasks.source_config_home({"source_url": "https://github.com/o/r.git"})
    assert home == fake_home / ".config" / "eglantine-launcher" / "sources" / "github-o-r"
    assert tasks.source_config_dir({"source_url": "https://github.com/o/r.git"}) == home / "eglantine"


def test_seed_copies_legacy_config_once(fake_home):
    legacy = fake_home / ".config" / "eden"
    legacy.mkdir(parents=True)
    (legacy / "qt-config.ini").write_text("[UI]\n")
    cfg = {"projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}]}
    info = cfg["projects"][0]

    home = tasks.seed_source_config(cfg, info)

    assert (home / "eglantine" / "qt-config.ini").read_text() == "[UI]\n"
    assert info["config_seeded"] is True

    # A config deleted from Storage stays deleted instead of being restored.
    (home / "eglantine" / "qt-config.ini").unlink()
    tasks.seed_source_config(cfg, info)
    assert not (home / "eglantine" / "qt-config.ini").exists()


def test_seed_without_legacy_config_still_creates_home(fake_home):
    cfg = {"projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}]}
    home = tasks.seed_source_config(cfg, cfg["projects"][0])
    assert home.is_dir()
    assert cfg["projects"][0]["config_seeded"] is True


def _fake_popen(monkeypatch):
    seen = {}

    def fake(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake)
    return seen


def test_launch_sets_per_source_config_home(fake_home, monkeypatch, tmp_path):
    seen = _fake_popen(monkeypatch)
    binary = tmp_path / "eglantine"
    binary.write_text("")
    cfg = {"projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}]}

    assert tasks.launch(binary, cfg) is not None

    home = tasks.source_config_home(cfg["projects"][0])
    assert seen["cmd"] == [str(binary)]
    assert seen["env"]["XDG_CONFIG_HOME"] == str(home)
    assert home.is_dir()
    assert cfg["projects"][0]["config_seeded"] is True


def test_launch_forwards_config_home_through_flatpak(fake_home, monkeypatch, tmp_path):
    seen = _fake_popen(monkeypatch)
    monkeypatch.setenv("FLATPAK_ID", "dev.savagecore.EglantineLauncher")
    monkeypatch.setattr(
        tasks.shutil,
        "which",
        lambda name: "/usr/bin/flatpak-spawn" if name == "flatpak-spawn" else None,
    )
    binary = tmp_path / "eglantine"
    binary.write_text("")
    cfg = {"projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}]}

    tasks.launch(binary, cfg)

    home = tasks.source_config_home(cfg["projects"][0])
    assert seen["cmd"] == [
        "flatpak-spawn",
        "--host",
        f"--env=XDG_CONFIG_HOME={home}",
        str(binary),
    ]


def _cfg_with_env(**env):
    return {
        "project": "p",
        "projects": [{"id": "p", "source_url": "https://github.com/o/r.git", "custom_env": dict(env)}],
    }


def test_launch_applies_custom_env(fake_home, monkeypatch, tmp_path):
    seen = _fake_popen(monkeypatch)
    binary = tmp_path / "eglantine"
    binary.write_text("")
    cfg = _cfg_with_env(EDEN_MKW_MOVIE_UNPACED="1", EDEN_NX2_SWAP_INTERVAL="0")

    tasks.launch(binary, cfg)

    assert seen["env"]["EDEN_MKW_MOVIE_UNPACED"] == "1"
    assert seen["env"]["EDEN_NX2_SWAP_INTERVAL"] == "0"
    assert "XDG_CONFIG_HOME" in seen["env"]


def test_launch_forwards_custom_env_through_flatpak(fake_home, monkeypatch, tmp_path):
    seen = _fake_popen(monkeypatch)
    monkeypatch.setenv("FLATPAK_ID", "dev.savagecore.EglantineLauncher")
    monkeypatch.setattr(
        tasks.shutil,
        "which",
        lambda name: "/usr/bin/flatpak-spawn" if name == "flatpak-spawn" else None,
    )
    binary = tmp_path / "eglantine"
    binary.write_text("")
    cfg = _cfg_with_env(EDEN_MKW_MOVIE_UNPACED="1")

    tasks.launch(binary, cfg)

    assert "--env=EDEN_MKW_MOVIE_UNPACED=1" in seen["cmd"]
    assert seen["env"]["EDEN_MKW_MOVIE_UNPACED"] == "1"


def test_launch_env_is_per_source(fake_home, monkeypatch, tmp_path):
    seen = _fake_popen(monkeypatch)
    binary = tmp_path / "eglantine"
    binary.write_text("")
    cfg = {
        "project": "other",
        "projects": [
            {
                "id": "p",
                "source_url": "https://github.com/o/r.git",
                "custom_env": {"EDEN_MKW_MOVIE_UNPACED": "1"},
            },
            {"id": "other", "source_url": "https://github.com/o/other.git"},
        ],
    }

    tasks.launch(binary, cfg)

    assert "EDEN_MKW_MOVIE_UNPACED" not in seen["env"]


def test_launch_drops_invalid_and_reserved_env(fake_home, monkeypatch, tmp_path):
    seen = _fake_popen(monkeypatch)
    binary = tmp_path / "eglantine"
    binary.write_text("")
    cfg = _cfg_with_env(
        **{
            "EDEN_MKW_MOVIE_UNPACED": "1",
            "bad-name": "x",
            "": "y",
            "XDG_CONFIG_HOME": "/elsewhere",
        }
    )

    tasks.launch(binary, cfg)

    home = tasks.source_config_home(cfg["projects"][0])
    assert seen["env"]["EDEN_MKW_MOVIE_UNPACED"] == "1"
    assert "bad-name" not in seen["env"]
    assert seen["env"]["XDG_CONFIG_HOME"] == str(home)


def test_emulator_env_helpers():
    assert tasks.is_valid_env_name("EDEN_MKW_MOVIE_UNPACED")
    assert not tasks.is_valid_env_name("bad-name")
    assert not tasks.is_valid_env_name("1ABC")
    assert not tasks.is_valid_env_name("")
    assert tasks.emulator_env({}) == {}
    assert tasks.emulator_env({"project": "p", "projects": [{"id": "p"}]}) == {}
    assert tasks.emulator_env({"project": "p", "projects": [{"id": "p", "custom_env": "nope"}]}) == {}


def test_stale_save_preserves_project_custom_env(fake_home):
    from eglantine_launcher import config as config_module

    cfg = config_module.load()
    cfg["projects"][0]["custom_env"] = {"EDEN_MKW_MOVIE_UNPACED": "1"}
    config_module.save(cfg)

    # A writer that predates the feature has no custom_env key on its
    # projects; saving it must not wipe the user's variables.
    stale = config_module.default_config()
    assert "custom_env" not in stale["projects"][0]
    stale["window_width"] = 800
    config_module.save(stale)

    assert config_module.load()["projects"][0]["custom_env"] == {"EDEN_MKW_MOVIE_UNPACED": "1"}

    # A fresh writer that explicitly clears the list persists the clear.
    fresh = config_module.load()
    fresh["projects"][0]["custom_env"] = {}
    config_module.save(fresh)
    assert config_module.load()["projects"][0]["custom_env"] == {}


def test_project_storage_lists_and_deletes_config(fake_home, tmp_path):
    cfg = {
        "project": "p",
        "projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}],
        "install_dir": str(tmp_path / "install"),
        "source_dir": str(tmp_path / "src"),
    }
    home = tasks.source_config_home(cfg["projects"][0])
    (home / "eden").mkdir(parents=True)
    (home / "eden" / "qt-config.ini").write_text("x")

    rows = tasks.project_storage(cfg, "p")
    row = next(r for r in rows if r["kind"] == "config")
    assert row["path"] == str(home)
    assert row["size"] > 0
    assert row["in_use"] is False

    assert tasks.delete_storage_row(cfg, row) is None
    assert not home.exists()
    assert cfg["projects"][0]["config_seeded"] is True


def test_delete_config_refuses_foreign_path(fake_home, tmp_path):
    cfg = {
        "project": "p",
        "projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}],
    }
    row = {"kind": "config", "pid": "p", "path": str(tmp_path / "elsewhere")}
    (tmp_path / "elsewhere").mkdir()
    assert tasks.delete_storage_row(cfg, row) is not None
    assert (tmp_path / "elsewhere").exists()


def test_remove_project_data_keeps_config_shared_with_a_pr_build(fake_home, tmp_path):
    url = "https://github.com/o/r.git"
    cfg = {
        "install_dir": str(tmp_path / "install"),
        "source_dir": str(tmp_path / "src"),
        "projects": [
            {"id": "p", "source_url": url},
            {"id": "pr-p-1", "is_pr": True, "source_url": url},
        ],
    }
    home = tasks.source_config_home(cfg["projects"][0])
    (home / "eden").mkdir(parents=True)

    tasks.remove_project_data(cfg, "p")

    assert home.is_dir()


def test_remove_project_data_deletes_unshared_config(fake_home, tmp_path):
    cfg = {
        "install_dir": str(tmp_path / "install"),
        "source_dir": str(tmp_path / "src"),
        "projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}],
    }
    home = tasks.source_config_home(cfg["projects"][0])
    (home / "eden").mkdir(parents=True)

    tasks.remove_project_data(cfg, "p")

    assert not home.exists()


def test_move_source_config_renames_folder(fake_home):
    info = {"id": "p", "source_url": "https://github.com/o/r.git"}
    cfg = {"projects": [info]}
    old = tasks.source_config_home(info)
    (old / "eden").mkdir(parents=True)

    tasks.move_source_config(cfg, info, "renamed")

    assert not old.exists()
    assert (fake_home / ".config" / "eglantine-launcher" / "sources" / "renamed" / "eden").is_dir()


def test_move_source_config_leaves_shared_folders(fake_home):
    url = "https://github.com/o/r.git"
    cfg = {
        "projects": [
            {"id": "p", "source_url": url},
            {"id": "pr-p-1", "is_pr": True, "source_url": url},
        ]
    }
    old = tasks.source_config_home(cfg["projects"][0])
    (old / "eden").mkdir(parents=True)

    tasks.move_source_config(cfg, cfg["projects"][0], "renamed")

    assert old.is_dir()


def test_move_source_config_never_overwrites(fake_home):
    info = {"id": "p", "source_url": "https://github.com/o/r.git"}
    cfg = {"projects": [info]}
    old = tasks.source_config_home(info)
    (old / "eden").mkdir(parents=True)
    target = fake_home / ".config" / "eglantine-launcher" / "sources" / "taken"
    target.mkdir(parents=True)

    tasks.move_source_config(cfg, info, "taken")

    assert old.is_dir()


def _save_cfg(fake_home, tmp_path):
    install = tmp_path / "install"
    cfg = {"install_dir": str(install), "projects": []}
    saves = fake_home / ".local" / "share" / "eglantine" / "nand" / "user" / "save"
    (saves / "0000000000000000" / "user1" / "0100000000000000").mkdir(parents=True)
    (saves / "0000000000000000" / "user1" / "0100000000000000" / "file0").write_text("progress")
    return cfg


def test_backup_saves_creates_timestamped_copy(fake_home, tmp_path):
    cfg = _save_cfg(fake_home, tmp_path)

    dest = tasks.backup_saves(cfg)

    assert dest is not None
    assert (dest / "0000000000000000" / "user1" / "0100000000000000" / "file0").read_text() == "progress"


def test_backup_saves_missing_tree_returns_none(fake_home, tmp_path):
    cfg = {"install_dir": str(tmp_path / "install"), "projects": []}
    assert tasks.backup_saves(cfg) is None


def test_backup_saves_prunes_to_keep(fake_home, tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "SAVE_BACKUP_KEEP", 3)
    cfg = _save_cfg(fake_home, tmp_path)
    for _ in range(5):
        assert tasks.backup_saves(cfg) is not None

    assert len(tasks.list_save_backups(cfg)) == 3


def test_backup_failure_never_blocks_launch(fake_home, tmp_path, monkeypatch):
    import subprocess

    seen = {}

    def fake(cmd, **kwargs):
        seen["cmd"] = cmd
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.setattr(tasks.shutil, "copytree", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    binary = tmp_path / "eglantine"
    binary.write_text("")
    cfg = _save_cfg(fake_home, tmp_path)

    assert tasks.launch(binary, cfg) is not None
    assert seen["cmd"] == [str(binary)]


def test_launch_backs_up_before_starting(fake_home, tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: object())
    binary = tmp_path / "eglantine"
    binary.write_text("")
    cfg = _save_cfg(fake_home, tmp_path)

    tasks.launch(binary, cfg)

    assert len(tasks.list_save_backups(cfg)) == 1


def test_restore_and_delete_save_backup(fake_home, tmp_path):
    cfg = _save_cfg(fake_home, tmp_path)
    dest = tasks.backup_saves(cfg)
    name = dest.name

    live = tasks.emulator_save_dir(cfg)
    (live / "0000000000000000" / "user1" / "0100000000000000" / "file0").write_text("newer")
    assert tasks.restore_save_backup(cfg, name) is None
    assert (live / "0000000000000000" / "user1" / "0100000000000000" / "file0").read_text() == "progress"

    assert tasks.delete_save_backup(cfg, name) is None
    assert tasks.list_save_backups(cfg) == []
    assert tasks.restore_save_backup(cfg, name) is not None


def test_save_backup_guards_reject_foreign_paths(fake_home, tmp_path):
    cfg = _save_cfg(fake_home, tmp_path)
    assert tasks.delete_save_backup(cfg, "../elsewhere") is not None
    assert tasks.delete_save_backup(cfg, "") is not None
    assert tasks.restore_save_backup(cfg, "") is not None


def test_emulator_save_dir_uses_canonical_name(fake_home):
    assert str(tasks.emulator_save_dir({})).endswith("share/eglantine/nand/user/save")


def test_legacy_eden_saves_are_promoted_to_canonical(fake_home, tmp_path):
    base = fake_home / ".local" / "share"
    legacy_save = base / "eden" / "nand" / "user" / "save"
    (legacy_save / "user1").mkdir(parents=True)
    (legacy_save / "user1" / "file0").write_text("progress")
    cfg = {"install_dir": str(tmp_path / "install"), "projects": []}

    dest = tasks.backup_saves(cfg)

    assert dest is not None
    assert (base / "eglantine" / "nand" / "user" / "save" / "user1" / "file0").read_text() == "progress"
    assert (base / "eden").is_symlink()
    assert tasks.emulator_save_dir(cfg).is_relative_to(base / "eglantine")


def test_diverged_save_trees_merge_newer_wins(fake_home):
    import os
    import time

    base = fake_home / ".local" / "share"
    old_save = base / "eden" / "nand" / "user" / "save"
    new_save = base / "eglantine" / "nand" / "user" / "save"
    (old_save / "user1").mkdir(parents=True)
    (new_save / "user1").mkdir(parents=True)
    (old_save / "user1" / "old-only").write_text("kept")
    (new_save / "user1" / "new-only").write_text("kept")
    (old_save / "user1" / "shared").write_text("old-build")
    (new_save / "user1" / "shared").write_text("new-build")
    now = time.time()
    os.utime(old_save / "user1" / "shared", (now - 100, now - 100))

    tasks.migrate_emulator_dirs({})

    merged = base / "eglantine" / "nand" / "user" / "save" / "user1"
    assert (merged / "old-only").read_text() == "kept"
    assert (merged / "new-only").read_text() == "kept"
    assert (merged / "shared").read_text() == "new-build"
    assert (base / "eden").is_symlink()
    # Second run is a no-op and keeps content intact.
    tasks.migrate_emulator_dirs({})
    assert (merged / "shared").read_text() == "new-build"
    assert (base / "eden").is_symlink()


def test_legacy_eden_config_links_into_source_home(fake_home):
    cfg = {"projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}]}
    info = cfg["projects"][0]
    home = tasks.source_config_home(info)
    (home / "eden").mkdir(parents=True)
    (home / "eden" / "qt-config.ini").write_text("[UI]\n")

    tasks.seed_source_config(cfg, info)

    assert (home / "eglantine" / "qt-config.ini").read_text() == "[UI]\n"
    assert (home / "eden").is_symlink()


def test_seed_prefers_eglantine_shared_config(fake_home):
    new = fake_home / ".config" / "eglantine"
    new.mkdir(parents=True)
    (new / "qt-config.ini").write_text("[new]\n")
    legacy = fake_home / ".config" / "eden"
    legacy.mkdir(parents=True)
    (legacy / "qt-config.ini").write_text("[old]\n")
    cfg = {"projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}]}

    home = tasks.seed_source_config(cfg, cfg["projects"][0])

    assert (home / "eglantine" / "qt-config.ini").read_text() == "[new]\n"


def test_migrate_links_known_homes_for_all_projects(fake_home):
    base = fake_home / ".local" / "share"
    (base / "eden" / "nand").mkdir(parents=True)
    cfg = {
        "projects": [
            {"id": "a", "source_url": "https://github.com/o/a.git"},
            {"id": "b", "source_url": "https://github.com/o/a.git", "config_key": "shared"},
        ]
    }
    home = tasks.source_config_home(cfg["projects"][0])
    home.mkdir(parents=True)
    (home / "eden").mkdir()

    tasks.migrate_emulator_dirs(cfg)

    assert (base / "eden").is_symlink()
    assert (base / "lichii").is_symlink()
    assert (home / "eden").is_symlink()
    assert (home / "eglantine").is_dir()


def test_canonicalize_rewrites_legacy_data_paths(fake_home):
    config = fake_home / ".config" / "eglantine-launcher" / "sources" / "x" / "eglantine"
    config.mkdir(parents=True)
    old = str(fake_home / ".local" / "share" / "eden")
    new = str(fake_home / ".local" / "share" / "eglantine")
    (config / "qt-config.ini").write_text(
        "\n".join(
            [
                "nand_directory=" + old + "/nand",
                "nand_directory\\default=false",
                "sdmc_directory=" + old + "/sdmc",
                "screenshot_path=" + old + "/screenshots",
                "screenshot_path\\default=true",
                "save_directory=/mnt/bigdrive/saves",
                "romsPath=" + old + "/roms",
                "",
            ]
        )
    )

    tasks.canonicalize_emulator_config_paths([config])

    text = (config / "qt-config.ini").read_text()
    assert "nand_directory=" + new + "/nand" in text
    assert "nand_directory\\default=false" in text
    assert "sdmc_directory=" + new + "/sdmc" in text
    assert "screenshot_path=" + new + "/screenshots" in text
    # Custom locations elsewhere and unrelated keys are never touched.
    assert "save_directory=/mnt/bigdrive/saves" in text
    assert "romsPath=" + old + "/roms" in text
    # Second run changes nothing further.
    before = text
    tasks.canonicalize_emulator_config_paths([config])
    assert (config / "qt-config.ini").read_text() == before


def test_canonicalize_missing_config_is_a_noop(fake_home, tmp_path):
    tasks.canonicalize_emulator_config_paths([tmp_path / "nope"])
    tasks.canonicalize_emulator_config_paths([])


def test_migrate_rewrites_legacy_spellings_in_source_configs(fake_home):
    old = str(fake_home / ".local" / "share" / "eden")
    new = str(fake_home / ".local" / "share" / "eglantine")
    cfg = {"projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}]}
    home = tasks.source_config_home(cfg["projects"][0])
    (home / "eglantine").mkdir(parents=True)
    (home / "eglantine" / "qt-config.ini").write_text("nand_directory=" + old + "/nand\n")

    tasks.migrate_emulator_dirs(cfg)

    assert (home / "eglantine" / "qt-config.ini").read_text() == "nand_directory=" + new + "/nand\n"


def test_seed_canonicalizes_copied_legacy_config(fake_home):
    old = str(fake_home / ".local" / "share" / "eden")
    new = str(fake_home / ".local" / "share" / "eglantine")
    legacy = fake_home / ".config" / "eden"
    legacy.mkdir(parents=True)
    (legacy / "qt-config.ini").write_text("sdmc_directory=" + old + "/sdmc\n")
    cfg = {"projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}]}

    home = tasks.seed_source_config(cfg, cfg["projects"][0])

    assert (home / "eglantine" / "qt-config.ini").read_text() == "sdmc_directory=" + new + "/sdmc\n"


def test_format_backup_name_localizes_utc():
    text = tasks.format_backup_name("2026-11-04T19-26-07Z")
    assert "2026" in text
    assert not text.endswith("Z")
    assert tasks.format_backup_name("2026-11-04T19-26-07Z-2").endswith("-2")
    assert tasks.format_backup_name("not-a-backup") == "not-a-backup"
    assert tasks.format_backup_name("") == ""


def test_make_pr_project_keeps_config_fields():
    existing = {
        "id": "pr-o-r-1-abc",
        "config_key": "shared",
        "config_seeded": True,
        "last_built_commit": "deadbeef",
        "best_for": [{"title_id": "0400C3F00006E000", "name": "Mario Kart World"}],
        "tags": [{"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"}],
    }
    project = tasks.make_pr_project("PR", "https://github.com/o/r/pull/1", {}, existing=existing)
    assert project["config_key"] == "shared"
    assert project["config_seeded"] is True
    assert project["best_for"] == existing["best_for"]
    assert project["tags"] == existing["tags"]


def test_pr_edit_dialog_tags_round_trip(needs_display, fake_home):
    from eglantine_launcher.ui.pr_dialog import PREditDialog

    existing = {
        "id": "pr-x",
        "pr_url": "https://github.com/o/r/pull/9",
        "source_url": "https://github.com/o/r.git",
        "pr_head_sha": "abc123",
        "best_for": [{"title_id": "0400C3F00006E000", "name": "Mario Kart World"}],
        "tags": [{"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"}],
    }
    dialog = PREditDialog(None, existing, lambda *a: None, cfg={})
    try:
        # existing games come pre-checked, existing tags pre-filled
        assert dialog._game_checks["0400C3F00006E000"].get_active()
        assert not dialog._game_checks["040074A01BF12000"].get_active()
        assert len(dialog._tag_editor) == 1
        # check another game, drop the tag text to skip it
        dialog._game_checks["040074A01BF12000"].set_active(True)
        dialog._tag_editor._entries[0][0].set_text("  ")
        dialog._pending_tags = dialog._collect_tag_state()
        saved = {}
        dialog.on_save = lambda project, _old: saved.update(project=project)
        dialog._save_done({"id": "pr-x", "source_url": "https://github.com/o/r.git"})
        assert {b["title_id"] for b in saved["project"]["best_for"]} == {
            "0400C3F00006E000",
            "040074A01BF12000",
        }
        assert saved["project"]["tags"] == []
    finally:
        dialog.close()


def test_pr_edit_dialog_saves_tags_without_network(needs_display, fake_home):
    """Tag-only edits save offline: unchanged URL skips the PR lookup."""
    from eglantine_launcher.ui.pr_dialog import PREditDialog

    existing = {
        "id": "pr-x",
        "name": "Fix NUP mods (#366)",
        "pr_url": "https://github.com/o/r/pull/9",
        "source_url": "https://github.com/o/r.git",
        "branch": "pr-9",
        "pr_head_sha": "abc123",
        "tags": [],
    }
    dialog = PREditDialog(None, existing, lambda *a: None, cfg={})
    try:
        dialog._tag_editor.add_tag("fix", "Fixes mods on NUP")
        saved = {}
        dialog.on_save = lambda project, _old: saved.update(project=project)
        dialog._save()
        assert saved["project"]["tags"] == [{"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"}]
        assert saved["project"]["pr_head_sha"] == "abc123"
    finally:
        try:
            dialog.close()
        except Exception:
            pass


def test_pr_edit_dialog_sets_settings_folder(needs_display, fake_home):
    from eglantine_launcher.ui.pr_dialog import PREditDialog

    dialog = PREditDialog(None, None, lambda *a: None, cfg={})
    try:
        dialog.url_entry.set_text("https://github.com/o/r/pull/7")
        assert dialog.config_key_entry.get_text() == "github-o-r"
        saved = {}
        dialog.on_save = lambda project, _old: saved.update(project=project)
        dialog._save_done({"id": "pr-o-r-7-x", "source_url": "https://github.com/o/r.git"})
        assert saved["project"]["config_key"] == "github-o-r"
    finally:
        dialog.close()

    override = PREditDialog(
        None,
        {"pr_url": "https://github.com/o/r/pull/7", "config_key": "mine"},
        lambda *a: None,
        cfg={},
    )
    try:
        assert override.config_key_entry.get_text() == "mine"
    finally:
        override.close()


def test_edit_dialog_shows_and_saves_config_folder(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    cfg = {"projects": []}
    proj = {"id": "p", "name": "Eglantine", "source_url": "https://github.com/o/r.git"}
    saved = {}
    dialog = ProjectEditDialog(None, proj, lambda project, _old: saved.update(project=project), cfg=cfg)
    try:
        # The field shows the folder the source uses now.
        assert dialog.config_key_entry.get_text() == "github-o-r"
        dialog.config_key_entry.set_text("My Settings")
        dialog._save()
        assert saved["project"]["config_key"] == "my-settings"
    finally:
        dialog.close()

    override = ProjectEditDialog(None, {**proj, "config_key": "shared"}, lambda *a: None, cfg=cfg)
    try:
        assert override.config_key_entry.get_text() == "shared"
    finally:
        override.close()


def test_edit_dialog_tags_tab_saves_user_overlay(needs_display, fake_home):
    from eglantine_launcher.config import DEFAULT_PROJECTS
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    eglantine = next(p for p in DEFAULT_PROJECTS if p["id"] == "eglantine")
    cfg = {"projects": []}
    saved = {}
    dialog = ProjectEditDialog(
        None, dict(eglantine), lambda project, _old: saved.update(project=project), cfg=cfg
    )
    try:
        # Tags tab exists alongside Settings and Storage.
        assert dialog._stack is not None
        assert dialog._stack.get_child_by_name("tags") is not None
        # Curated games are not offered as user games.
        assert "0400C3F00006E000" not in dialog._user_game_checks
        check, note_entry, _name = dialog._user_game_checks["040074A01BF12000"]
        check.set_active(True)
        note_entry.set_text("Runs great here")
        dialog._user_tags_editor.add_tag("fix", "Fixes mods on NUP")
        dialog._save()
    finally:
        try:
            dialog.close()
        except Exception:
            pass
    assert saved["project"]["user_best_for"] == [
        {"title_id": "040074A01BF12000", "name": "Donkey Kong Bananza", "note": "Runs great here"}
    ]
    assert saved["project"]["user_tags"] == [{"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"}]
    # curated entries are preserved untouched
    assert saved["project"]["best_for"] == eglantine["best_for"]


def test_edit_dialog_tags_tab_add_game(needs_display, fake_home):
    from eglantine_launcher.config import DEFAULT_PROJECTS
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    eglantine = next(p for p in DEFAULT_PROJECTS if p["id"] == "eglantine")
    cfg = {"projects": []}
    saved = {}
    dialog = ProjectEditDialog(
        None, dict(eglantine), lambda project, _old: saved.update(project=project), cfg=cfg
    )
    try:
        assert "0400999900000000" not in dialog._user_game_checks
        dialog._new_game_id_entry.set_text("0400999900000000")
        dialog._new_game_name_entry.set_text("Mine")
        dialog._on_add_user_game()
        assert "0400999900000000" in dialog._user_game_checks
        assert cfg["custom_games"] == [{"title_id": "0400999900000000", "name": "Mine"}]
        # invalid IDs are rejected, not added
        dialog._new_game_id_entry.set_text("nope")
        dialog._on_add_user_game()
        assert "16-digit" in dialog._user_game_error.get_label()
    finally:
        try:
            dialog.close()
        except Exception:
            pass


def test_edit_dialog_env_round_trip(needs_display, fake_home):
    from eglantine_launcher.config import DEFAULT_PROJECTS
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    eglantine = next(p for p in DEFAULT_PROJECTS if p["id"] == "eglantine")
    eglantine = dict(eglantine, custom_env={"EDEN_NX2_SWAP_INTERVAL": "0"})
    cfg = {"projects": []}
    saved = {}
    dialog = ProjectEditDialog(
        None, dict(eglantine), lambda project, _old: saved.update(project=project), cfg=cfg
    )
    try:
        # invalid names and reserved keys are rejected, not staged
        dialog._env_name_entry.set_text("bad-name")
        dialog._env_value_entry.set_text("x")
        dialog._on_add_env_row()
        assert "underscore" in dialog._env_error.get_label()
        dialog._env_name_entry.set_text("XDG_CONFIG_HOME")
        dialog._env_value_entry.set_text("/elsewhere")
        dialog._on_add_env_row()
        assert "reserved" in dialog._env_error.get_label()
        # a valid entry stages alongside the existing one
        dialog._env_name_entry.set_text("EDEN_MKW_MOVIE_UNPACED")
        dialog._env_value_entry.set_text("1")
        dialog._on_add_env_row()
        assert dialog._env_error.get_label() == ""
        dialog._save()
    finally:
        try:
            dialog.close()
        except Exception:
            pass
    assert saved["project"]["custom_env"] == {
        "EDEN_NX2_SWAP_INTERVAL": "0",
        "EDEN_MKW_MOVIE_UNPACED": "1",
    }


def test_tags_tab_upstream_links_to_wichforks(needs_display, fake_home):
    from gi.repository import Gtk

    from eglantine_launcher.config import DEFAULT_PROJECTS
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    eglantine = next(p for p in DEFAULT_PROJECTS if p["id"] == "eglantine")
    dialog = ProjectEditDialog(None, dict(eglantine), lambda *a: None, cfg={"projects": []})
    try:
        dialog.show_page("tags")
        found = []

        def walk(widget):
            if isinstance(widget, Gtk.LinkButton):
                found.append(widget.get_uri())
                return
            child = widget.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(dialog._layout.stack)
        assert "https://github.com/riyadhHDA/wichforks" in found
    finally:
        try:
            dialog.close()
        except Exception:
            pass


def test_pr_dialog_lists_custom_games(needs_display, fake_home):
    from eglantine_launcher.ui.pr_dialog import PREditDialog

    cfg = {"custom_games": [{"title_id": "0400999900000000", "name": "Mine"}]}
    dialog = PREditDialog(None, None, lambda *a: None, cfg=cfg)
    try:
        assert "0400999900000000" in dialog._game_checks
        dialog._game_checks["0400999900000000"].set_active(True)
        games, _tags = dialog._collect_tag_state()
        assert {"title_id": "0400999900000000", "name": "Mine"} in games
    finally:
        dialog.close()


def test_tag_list_editor_collects_and_slugs(needs_display, fake_home):
    from eglantine_launcher.ui.taglist import TagListEditor, slugify

    assert slugify("Fix NUP Mods!") == "fix-nup-mods"
    assert slugify("  ") == ""
    editor = TagListEditor()
    editor.add_tag("fix", "Fixes mods on NUP")
    editor.add_tag("  ", "skipped")
    editor.add_tag("AMD", "")
    assert editor.collect() == [
        {"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"},
        {"id": "amd", "label": "AMD", "tooltip": ""},
    ]
    assert len(editor) == 3


def test_sidebar_layout_pages(needs_display, fake_home):
    from gi.repository import Gtk

    from eglantine_launcher.ui.sidebar import SidebarLayout

    layout = SidebarLayout([("a", "A"), ("b", "B")])
    layout.add_page("a", Gtk.Label(label="page a"))
    layout.add_page("b", Gtk.Label(label="page b"))
    assert layout.visible_page == "a"
    layout.show_page("b")
    assert layout.visible_page == "b"
    layout.show_page("nope")
    assert layout.visible_page == "b"


def test_source_editor_uses_sidebar(needs_display, fake_home):
    from eglantine_launcher.config import DEFAULT_PROJECTS
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    eglantine = next(p for p in DEFAULT_PROJECTS if p["id"] == "eglantine")
    dialog = ProjectEditDialog(None, dict(eglantine), lambda *a: None, cfg={"projects": []})
    try:
        assert dialog._layout is not None
        assert dialog._layout.visible_page == "settings"
        dialog.show_page("tags")
        assert dialog._layout.visible_page == "tags"
    finally:
        try:
            dialog.close()
        except Exception:
            pass


def test_pr_add_saves_offline_stub(needs_display, fake_home, monkeypatch):
    """Rate-limited/offline Add still saves a stub that refreshes later."""
    from gi.repository import GLib

    import eglantine_launcher.ui.pr_dialog as prmod

    def _offline(*a, **k):
        raise Exception("rate limited")

    monkeypatch.setattr(prmod, "fetch_pr_info", _offline)
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a, **k: fn(*a))
    dialog = prmod.PREditDialog(None, None, lambda *a: None, cfg={})
    try:
        saved = {}
        dialog.on_save = lambda project, _old: saved.update(project=project)
        dialog._tag_editor.add_tag("fix", "Fixes mods on NUP")
        dialog._pending_tags = dialog._collect_tag_state()
        dialog._save_lookup("Cool fix (#12)", "https://github.com/o/r/pull/12")
        assert saved["project"]["pr_number"] == 12
        assert saved["project"]["pr_owner"] == "o"
        assert saved["project"]["pr_head_sha"] == ""
        assert saved["project"]["tags"] == [{"id": "fix", "label": "fix", "tooltip": "Fixes mods on NUP"}]
    finally:
        try:
            dialog.close()
        except Exception:
            pass


def test_edit_dialog_config_picker_lists_and_applies(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    cfg = {
        "projects": [
            {"id": "a", "name": "Alpha", "source_url": "https://github.com/o/r.git"},
            {"id": "b", "name": "Beta", "source_url": "https://github.com/o/other.git"},
        ]
    }
    proj = {"id": "b", "name": "Beta", "source_url": "https://github.com/o/other.git"}
    dialog = ProjectEditDialog(None, proj, lambda *a: None, cfg=cfg)
    try:
        # The edited source's own name is left out; other sources show up.
        assert "github-o-r" in dialog.config_picker_keys
        assert "github-o-other" in dialog.config_picker_keys
        assert dialog.config_picker_row.get_visible()
        dialog.config_picker_row.set_selected(dialog.config_picker_keys.index("github-o-r"))
        assert dialog.config_key_entry.get_text() == "github-o-r"
    finally:
        try:
            dialog.close()
        except Exception:
            pass


def test_edit_dialog_config_picker_hidden_without_choice(needs_display, fake_home):
    from eglantine_launcher.ui.settings_window import ProjectEditDialog

    cfg = {"projects": []}
    proj = {"id": "p", "name": "Solo", "source_url": "https://github.com/o/r.git"}
    dialog = ProjectEditDialog(None, proj, lambda *a: None, cfg=cfg)
    try:
        assert dialog.config_picker_keys == ["github-o-r"]
        assert not dialog.config_picker_row.get_visible()
        # Free-typed names still save, slug-normalized.
        dialog.config_key_entry.set_text("My Settings")
        saved = {}
        dialog.on_save = lambda project, _old: saved.update(project=project)
        dialog._save()
        assert saved["project"]["config_key"] == "my-settings"
    finally:
        try:
            dialog.close()
        except Exception:
            pass


def test_pr_dialog_config_picker_marks_touched(needs_display, fake_home):
    from eglantine_launcher.ui.pr_dialog import PREditDialog

    cfg = {"projects": [{"id": "a", "name": "Alpha", "source_url": "https://github.com/o/r.git"}]}
    dialog = PREditDialog(None, None, lambda *a: None, cfg=cfg)
    try:
        dialog.url_entry.set_text("https://github.com/o/other/pull/7")
        assert dialog.config_key_entry.get_text() == "github-o-other"
        assert dialog.config_picker_row.get_visible()
        dialog.config_picker_row.set_selected(dialog.config_picker_keys.index("github-o-r"))
        assert dialog.config_key_entry.get_text() == "github-o-r"
        # A picked folder sticks: later URL edits no longer overwrite it.
        dialog.url_entry.set_text("https://github.com/o/third/pull/9")
        assert dialog.config_key_entry.get_text() == "github-o-r"
    finally:
        try:
            dialog.close()
        except Exception:
            pass
