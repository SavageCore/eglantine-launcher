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


def test_source_config_home_is_under_launcher_config(fake_home):
    home = tasks.source_config_home({"source_url": "https://github.com/o/r.git"})
    assert home == fake_home / ".config" / "eglantine-launcher" / "sources" / "github-o-r"
    assert tasks.source_config_dir({"source_url": "https://github.com/o/r.git"}) == home / "eden"


def test_seed_copies_legacy_config_once(fake_home):
    legacy = fake_home / ".config" / "eden"
    legacy.mkdir(parents=True)
    (legacy / "qt-config.ini").write_text("[UI]\n")
    cfg = {"projects": [{"id": "p", "source_url": "https://github.com/o/r.git"}]}
    info = cfg["projects"][0]

    home = tasks.seed_source_config(cfg, info)

    assert (home / "eden" / "qt-config.ini").read_text() == "[UI]\n"
    assert info["config_seeded"] is True

    # A config deleted from Storage stays deleted instead of being restored.
    (home / "eden" / "qt-config.ini").unlink()
    tasks.seed_source_config(cfg, info)
    assert not (home / "eden" / "qt-config.ini").exists()


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


def test_make_pr_project_keeps_config_fields():
    existing = {
        "id": "pr-o-r-1-abc",
        "config_key": "shared",
        "config_seeded": True,
        "last_built_commit": "deadbeef",
    }
    project = tasks.make_pr_project("PR", "https://github.com/o/r/pull/1", {}, existing=existing)
    assert project["config_key"] == "shared"
    assert project["config_seeded"] is True


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
