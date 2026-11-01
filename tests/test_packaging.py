"""Packaging smoke tests: required files exist and agree with each other."""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ID = "dev.savagecore.EglantineLauncher"


def test_desktop_file():
    text = (REPO / "packaging" / f"{APP_ID}.desktop").read_text()
    assert "Exec=eglantine-launcher" in text
    assert "Icon=eglantine-launcher" in text


def test_wrapper_references_lib_dir():
    text = (REPO / "packaging" / "eglantine-launcher.sh").read_text()
    assert "/usr/lib/eglantine-launcher" in text
    assert "eglantine_launcher.main" in text


def test_icon_and_metainfo_exist():
    assert (REPO / "data" / "icons" / "eglantine-launcher.svg").exists()
    meta = REPO / "data" / f"{APP_ID}.metainfo.xml"
    assert meta.exists()
    assert APP_ID in meta.read_text()


def test_nfpm_config():
    text = (REPO / "packaging" / "nfpm.yaml").read_text()
    assert "eglantine-launcher" in text
    assert "python3-gi" in text  # deb deps
    assert "python3-gobject" in text  # rpm deps


def test_flatpak_manifest():
    manifest = json.loads((REPO / "flatpak" / f"{APP_ID}.json").read_text())
    assert manifest["app-id"] == APP_ID
    assert manifest["command"] == "eglantine-launcher"
    assert "--share=network" in manifest["finish-args"]
    assert "--filesystem=home" in manifest["finish-args"]
    module = manifest["modules"][0]
    assert "metainfo" in " ".join(module["build-commands"])


def test_flatpakref():
    text = (REPO / "flatpak" / "eglantine-launcher.flatpakref").read_text()
    assert f"Name={APP_ID}" in text
    assert "savagecore.github.io/eglantine-launcher/flatpak/" in text


def test_app_id_matches_code():
    init = (REPO / "eglantine_launcher" / "__init__.py").read_text()
    assert f'APP_ID = "{APP_ID}"' in init


def test_readme_images_exist():
    import re

    for md in ["README.md", "docs/screenshots/README.md"]:
        text = (REPO / md).read_text()
        for src in re.findall(r"src=\"([^\"]+\.(?:png|svg))\"", text):
            assert (REPO / src).exists(), f"{md}: missing {src}"
