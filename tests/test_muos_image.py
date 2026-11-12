"""Tests for muOS 2606.0 Andromeda (Anbernic RG35XX SP) image layout fixture.

Verifies PortPicker detection, target building, version discovery, and installation
against the real filesystem tree recorded from the official muOS release image.
"""

from pathlib import Path
import pytest
import zipfile

import core
import profiles


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "muos-2606.0-rg35xxsp.txt"


def build_tree_from_fixture(fixture_file: Path, target_dir: Path) -> None:
    """Build a directory tree and marker files from a path list fixture."""
    for line in fixture_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            rel_path, content = line.split(":", 1)
            rel_path = rel_path.strip()
            content = content.strip()
            full_path = target_dir / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
        elif line.endswith("/"):
            (target_dir / line.rstrip("/")).mkdir(parents=True, exist_ok=True)
        else:
            full_path = target_dir / line
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.touch()


def test_fixture_file_exists():
    assert FIXTURE_PATH.is_file(), f"Fixture missing: {FIXTURE_PATH}"


def test_muos_single_card_detection_and_versions(tmp_path):
    card1 = tmp_path / "card1"
    card1.mkdir()
    build_tree_from_fixture(FIXTURE_PATH, card1)

    # Detection & role
    assert core.card_matches(profiles.MUOS, card1)
    assert core.is_muos_card1(card1)
    assert not core.is_muos_card2(card1)

    # Version checks
    cfw_ver = core.read_cfw_version(profiles.MUOS, card1)
    assert cfw_ver == "2606.0"
    ok, err = core.check_cfw_version_minimum(profiles.MUOS, cfw_ver)
    assert ok and err is None

    pm_ver = core.read_portmaster_version(profiles.MUOS, card1)
    assert pm_ver == "2026.06.23-0015"


def test_muos_single_card_target_paths_and_install(tmp_path, monkeypatch):
    card1 = tmp_path / "card1"
    card1.mkdir()
    build_tree_from_fixture(FIXTURE_PATH, card1)

    t = core.build_target(profiles.MUOS, card1)
    assert t.root == card1
    assert t.system_root is None
    assert t.ports_dir == card1 / "ports"
    assert t.scripts_dir == card1 / "ROMS" / "Ports"
    assert t.libs_dir == card1 / "MUOS" / "PortMaster" / "libs"
    assert t.media_dir == card1 / "MUOS" / "info" / "catalogue" / "External - Ports" / "box"

    # Setup mock download and image cache
    cache_dir = tmp_path / "cache"
    img_dir = tmp_path / "images"
    cache_dir.mkdir(parents=True)
    img_dir.mkdir(parents=True)
    monkeypatch.setattr(core, "DL_DIR", cache_dir)
    monkeypatch.setattr(core, "IMG_DIR", img_dir)

    # Create mock port zip with game data and script
    port_zip = cache_dir / "testport.zip"
    with zipfile.ZipFile(port_zip, "w") as zf:
        zf.writestr("testport/data.bin", b"test_game_data")
        zf.writestr("Test Port.sh", b"#!/bin/bash\nGAMEDIR=/$directory/ports/testport\n")

    # Create mock cover image in IMG_DIR with stem__cover naming
    cover_file = img_dir / "testport__cover.png"
    cover_file.write_bytes(b"dummy_png_header")

    # Mock runtime squashfs
    rt_squashfs = cache_dir / "mono-6.12.0.squashfs"
    rt_squashfs.write_bytes(b"squashfs_data")

    port = core.Port(
        name="testport.zip",
        title="Test Port",
        desc="Test port for muOS",
        inst="",
        genres=[],
        rtr=True,
        availability="ready",
        reqs=[],
        runtime=["mono-6.12.0.squashfs"],
        arch=["aarch64"],
        min_glibc="",
        items=["Test Port.sh", "testport/"],
        url="",
        md5="",
        size=1024,
        screenshot="",
        cover="cover.png",
        date_updated="2026-06-23",
    )

    utils = {
        "mono-6.12.0.squashfs": {
            "name": "mono-6.12.0.squashfs",
            "url": "http://example.com/mono.zip",
            "size": 100,
        }
    }

    # Mock download to return the local mock squashfs or zip
    def mock_download(url, dest, progress=None, md5=None, cancel=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if str(dest).endswith(".zip"):
            dest.write_bytes(port_zip.read_bytes())
        else:
            dest.write_bytes(rt_squashfs.read_bytes())
        return dest

    monkeypatch.setattr(core, "download", mock_download)

    dev = next(d for d in profiles.DEVICES if "SP" in d.name)
    core.install_port(port, t, dev, utils, log=lambda *_: None)

    # Verify expected file landing locations
    assert (card1 / "ports" / "testport" / "data.bin").read_bytes() == b"test_game_data"
    assert (card1 / "ROMS" / "Ports" / "Test Port.sh").exists()
    assert (card1 / "MUOS" / "PortMaster" / "libs" / "mono-6.12.0.squashfs").exists()
    assert (card1 / "MUOS" / "info" / "catalogue" / "External - Ports" / "box" / "Test Port.png").exists()


def test_muos_two_card_detection_and_install(tmp_path, monkeypatch):
    card1 = tmp_path / "card1"
    card1.mkdir()
    build_tree_from_fixture(FIXTURE_PATH, card1)

    # Card 2 has ROMS, ports, ARCHIVE but NO MUOS/
    card2 = tmp_path / "card2"
    card2.mkdir()
    (card2 / "ROMS" / "Ports").mkdir(parents=True)
    (card2 / "ports").mkdir(parents=True)
    (card2 / "ARCHIVE").mkdir(parents=True)

    # Card 2 matches muOS and is detected as card 2
    assert core.card_matches(profiles.MUOS, card2)
    assert core.is_muos_card2(card2)
    assert not core.is_muos_card1(card2)

    # Target auto-pairs or pairs with system_root=card1
    t = core.build_target(profiles.MUOS, card2, system_root=card1)
    assert t.root == card2
    assert t.system_root == card1
    assert t.ports_dir == card2 / "ports"
    assert t.scripts_dir == card2 / "ROMS" / "Ports"
    assert t.libs_dir == card1 / "MUOS" / "PortMaster" / "libs"
    assert t.media_dir == card1 / "MUOS" / "info" / "catalogue" / "External - Ports" / "box"

    # Setup mock download and image cache
    cache_dir = tmp_path / "cache"
    img_dir = tmp_path / "images"
    cache_dir.mkdir(parents=True)
    img_dir.mkdir(parents=True)
    monkeypatch.setattr(core, "DL_DIR", cache_dir)
    monkeypatch.setattr(core, "IMG_DIR", img_dir)

    port_zip = cache_dir / "twocard.zip"
    with zipfile.ZipFile(port_zip, "w") as zf:
        zf.writestr("twocard/data.bin", b"two_card_game_data")
        zf.writestr("Two Card.sh", b"#!/bin/bash\nGAMEDIR=/$directory/ports/twocard\n")

    cover_file = img_dir / "twocard__cover2.png"
    cover_file.write_bytes(b"dummy_png_header")

    rt_squashfs = cache_dir / "frt_3.5.2.squashfs"
    rt_squashfs.write_bytes(b"frt_squashfs_data")

    port = core.Port(
        name="twocard.zip",
        title="Two Card",
        desc="Test two-card port for muOS",
        inst="",
        genres=[],
        rtr=True,
        availability="ready",
        reqs=[],
        runtime=["frt_3.5.2.squashfs"],
        arch=["aarch64"],
        min_glibc="",
        items=["Two Card.sh", "twocard/"],
        url="",
        md5="",
        size=1024,
        screenshot="",
        cover="cover2.png",
        date_updated="2026-06-23",
    )

    utils = {
        "frt_3.5.2.squashfs": {
            "name": "frt_3.5.2.squashfs",
            "url": "http://example.com/frt.zip",
            "size": 100,
        }
    }

    def mock_download2(url, dest, progress=None, md5=None, cancel=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        if str(dest).endswith(".zip"):
            dest.write_bytes(port_zip.read_bytes())
        else:
            dest.write_bytes(rt_squashfs.read_bytes())
        return dest

    monkeypatch.setattr(core, "download", mock_download2)

    dev = next(d for d in profiles.DEVICES if "SP" in d.name)
    core.install_port(port, t, dev, utils, log=lambda *_: None)

    # Ports & scripts land on card 2
    assert (card2 / "ports" / "twocard" / "data.bin").read_bytes() == b"two_card_game_data"
    assert (card2 / "ROMS" / "Ports" / "Two Card.sh").exists()
    assert not (card2 / "MUOS").exists(), "Card 2 must never have a MUOS directory created"

    # Runtime and box art land on card 1
    assert (card1 / "MUOS" / "PortMaster" / "libs" / "frt_3.5.2.squashfs").exists()
    assert (card1 / "MUOS" / "info" / "catalogue" / "External - Ports" / "box" / "Two Card.png").exists()
