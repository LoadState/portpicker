import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="pp-test-")

import core
from profiles import (DEVICES, NEXTUI, MUOS, KNULLI, ROCKNIX, ARKOS,
                      SPRUCE, CROSSMIX, CFW, Device)

BRICK = next(d for d in DEVICES if d.id == "brickpro")
RG35XXSP = next(d for d in DEVICES if d.id == "rg35xxsp")


def make_port(stem, **kw):
    base = dict(
        name=f"{stem}.zip", title=stem, desc="", inst="", genres=[], rtr=False,
        availability="free", reqs=[], runtime=[], arch=["aarch64"], min_glibc="",
        items=[f"{stem}.sh", stem], url="", md5="", size=0, screenshot="", cover="",
        date_updated=""
    )
    base.update(kw)
    return core.Port(**base)


# --------------------------------------------------------------------------
# 1. safe_extract hardening
# --------------------------------------------------------------------------

def test_safe_extract_rejects_path_traversal(tmp_path):
    dest = tmp_path / "target"
    dest.mkdir()

    # Zip with path traversal member
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("../evil.txt", "evil data")
    zip_buf.seek(0)

    with zipfile.ZipFile(zip_buf) as zf:
        with pytest.raises(ValueError, match="path traversal"):
            core.safe_extract(zf, dest)


def test_safe_extract_normal_and_cancel(tmp_path):
    dest = tmp_path / "target"
    dest.mkdir()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("game/file1.txt", "hello")
        zf.writestr("game/file2.txt", "world")
    zip_buf.seek(0)

    # Normal extraction
    with zipfile.ZipFile(zip_buf) as zf:
        core.safe_extract(zf, dest)
    assert (dest / "game" / "file1.txt").read_text() == "hello"

    # Cancel extraction
    zip_buf.seek(0)
    with zipfile.ZipFile(zip_buf) as zf:
        with pytest.raises(InterruptedError):
            core.safe_extract(zf, dest, cancel=lambda: True)


# --------------------------------------------------------------------------
# 2. Cancel stops runtime downloads and copy
# --------------------------------------------------------------------------

def test_install_runtime_cancel(tmp_path):
    t = core.Target(cfw=MUOS, root=tmp_path)
    (tmp_path / "MUOS").mkdir(parents=True)
    utils = {"glibc49": {"url": "http://example.com/r.zip", "size": 1000}}

    # Cancelled immediately raises InterruptedError
    with pytest.raises(InterruptedError):
        core.install_runtime("glibc49", utils, BRICK, t, cancel=lambda: True)


def test_install_runtime_counts_only_new_copies(tmp_path):
    t = core.Target(cfw=MUOS, root=tmp_path)
    (tmp_path / "MUOS" / "PortMaster" / "libs").mkdir(parents=True)
    existing_lib = tmp_path / "MUOS" / "PortMaster" / "libs" / "glibc49.squashfs"
    existing_lib.write_bytes(b"x" * 1000)

    utils = {"glibc49": {"runtime_name": "glibc49.squashfs", "url": "http://example.com/r.zip", "size": 1000}}

    # When file already exists with same size, it returns False (not counted as newly copied)
    res = core.install_runtime("glibc49", utils, BRICK, t)
    assert res is False

    # When file does not exist, it downloads and returns True
    existing_lib.unlink()
    cache_file = tmp_path / "downloaded_lib"
    cache_file.write_bytes(b"x" * 1000)
    with patch("core.download", return_value=cache_file):
        res = core.install_runtime("glibc49", utils, BRICK, t)
        assert res is True


# --------------------------------------------------------------------------
# 3. Disk-space estimate includes runtimes and local files
# --------------------------------------------------------------------------

def test_estimate_ports_size_includes_local_and_runtimes(tmp_path):
    local_dir = tmp_path / "local_game"
    local_dir.mkdir()
    (local_dir / "data.bin").write_bytes(b"x" * 2000)

    p1 = make_port("g1", size=5000, runtime=["mono6.12"])
    p1.local_dir = local_dir

    utils = {"mono6.12": {"size": 3000}}
    (tmp_path / "MUOS").mkdir(parents=True)
    t = core.Target(cfw=MUOS, root=tmp_path)

    port_est = core.estimate_ports_size([p1])
    assert port_est == int(5000 * 2.2) + 2000

    runtime_est = core.estimate_runtimes_size(t, [p1], utils)
    assert runtime_est == int(3000 * 1.1)

    with patch("core.get_free_space", return_value=10000):  # less than total needed
        warns = core.check_disk_space(t, [p1], utils)
        assert any("not have enough space" in w for w in warns)


# --------------------------------------------------------------------------
# 4. FAT32 checks
# --------------------------------------------------------------------------

def test_fat32_limits_warns_large_files(tmp_path):
    t = core.Target(cfw=MUOS, root=tmp_path)
    large_size = core.FAT32_FILE_LIMIT + 100

    # Port archive > 4GB
    p_large = make_port("large_game", size=large_size)
    with patch("core.is_fat32_filesystem", return_value=True):
        warns = core.check_fat32_limits(t, [p_large], {})
        assert any("FAT32" in w for w in warns)

    # Local file > 4GB (sparse file)
    local_dir = tmp_path / "local_large"
    local_dir.mkdir()
    p_local = make_port("local_game", size=100)
    p_local.local_dir = local_dir
    fake_file = local_dir / "big.bin"
    with open(fake_file, "wb") as f:
        f.truncate(large_size)

    with patch("core.is_fat32_filesystem", return_value=True):
        warns = core.check_fat32_limits(t, [p_local], {})
        assert any("FAT32" in w and "big.bin" in w for w in warns)

    # Runtime > 4GB
    p_rt = make_port("rt_game", size=100, runtime=["large_runtime"])
    utils = {"large_runtime": {"size": large_size}}
    with patch("core.is_fat32_filesystem", return_value=True):
        warns = core.check_fat32_limits(t, [p_rt], utils)
        assert any("FAT32" in w and "large_runtime" in w for w in warns)


# --------------------------------------------------------------------------
# 5. Drive detection and blocking PC drives
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX drive path checks")
def test_is_pc_drive_posix():
    assert core.is_pc_drive(Path("/")) is True
    assert core.is_pc_drive(Path("/usr")) is True
    assert core.is_pc_drive(Path("/home")) is True
    assert core.is_pc_drive(Path("/media/card")) is False
    assert core.is_pc_drive(Path("/mnt/card")) is False
    assert core.is_pc_drive(Path("/tmp/test_card")) is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only test simulating Windows behavior")
def test_is_pc_drive_windows_simulated(monkeypatch):
    import ntpath
    from unittest.mock import MagicMock
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os, "path", ntpath)
    monkeypatch.setenv("SystemDrive", "C:")

    # 1. System drive
    p_sys = MagicMock()
    p_sys.resolve.return_value = "C:\\Windows"
    assert core.is_pc_drive(p_sys) is True

    # 2. Removable drive
    monkeypatch.setattr(core, "_get_drive_type", lambda r: 2)
    p_card = MagicMock()
    p_card.resolve.return_value = "E:\\sdcard"
    assert core.is_pc_drive(p_card) is False

    # 3. Fixed drive without card match
    monkeypatch.setattr(core, "_get_drive_type", lambda r: 3)
    assert core.is_pc_drive(p_card, None) is True

    # 4. Fixed drive with card match
    monkeypatch.setattr(core, "card_matches", lambda cfw, path: True)
    assert core.is_pc_drive(p_card, MUOS) is False

    # 5. Remote drive
    monkeypatch.setattr(core, "_get_drive_type", lambda r: 4)
    assert core.is_pc_drive(p_card) is True

    # 6. Exception
    def err(r):
        raise RuntimeError("fail")
    monkeypatch.setattr(core, "_get_drive_type", err)
    assert core.is_pc_drive(p_card) is True


@pytest.mark.skipif(os.name != "nt", reason="Windows drive checks require Windows environment")
def test_is_pc_drive_windows(monkeypatch):
    import ctypes
    monkeypatch.setenv("SystemDrive", "C:")

    # 1. System drive (C:) is blocked
    assert core.is_pc_drive(Path("C:/Windows")) is True
    assert core.is_pc_drive(Path("C:/Users/test")) is True

    # 2. DRIVE_REMOVABLE (dt == 2) on non-system drive is allowed
    monkeypatch.setattr(ctypes.windll.kernel32, "GetDriveTypeW", lambda root: 2)
    assert core.is_pc_drive(Path("E:/sdcard")) is False
    assert core.is_pc_drive(Path("E:/")) is False

    # 3. DRIVE_FIXED (dt == 3) without matching CFW card -> blocked
    monkeypatch.setattr(ctypes.windll.kernel32, "GetDriveTypeW", lambda root: 3)
    assert core.is_pc_drive(Path("E:/not_a_card"), MUOS) is True
    assert core.is_pc_drive(Path("E:/not_a_card"), None) is True

    # 4. DRIVE_FIXED (dt == 3) with matching CFW card -> allowed
    monkeypatch.setattr(core, "card_matches", lambda cfw, p: True)
    assert core.is_pc_drive(Path("E:/sdcard"), MUOS) is False

    # 5. Other drive types (e.g. Remote = 4, CDROM = 5) -> blocked
    monkeypatch.setattr(ctypes.windll.kernel32, "GetDriveTypeW", lambda root: 4)
    assert core.is_pc_drive(Path("E:/remote_share")) is True

    # 6. Exception during drive check -> blocked (returns True on Windows)
    def err_drive_type(root):
        raise RuntimeError("Drive query failed")
    monkeypatch.setattr(ctypes.windll.kernel32, "GetDriveTypeW", err_drive_type)
    assert core.is_pc_drive(Path("E:/sdcard")) is True


# --------------------------------------------------------------------------
# 6. Device info detection from cards
# --------------------------------------------------------------------------

def test_detect_card_device(tmp_path):
    # NextUI
    nxt = tmp_path / "nextui_card"
    nxt.mkdir()
    (nxt / ".system" / "tg5040").mkdir(parents=True)
    assert core.detect_card_device(NEXTUI, nxt) == "tg5040"

    # muOS
    mu = tmp_path / "muos_card"
    (mu / "MUOS" / "info").mkdir(parents=True)
    (mu / "MUOS" / "info" / "device.txt").write_text("rg35xx-h\n")
    assert core.detect_card_device(MUOS, mu) == "rg35xx-h"

    # KNULLI
    kn = tmp_path / "knulli_card"
    (kn / "system").mkdir(parents=True)
    (kn / "system" / "batocera.conf").write_text("system.device=rg35xx-plus\n")
    assert core.detect_card_device(KNULLI, kn) == "rg35xx-plus"

    # ROCKNIX
    rk = tmp_path / "rocknix_card"
    rk.mkdir()
    (rk / "rocknix.board").write_text("rk3566\n")
    assert core.detect_card_device(ROCKNIX, rk) == "rk3566"

    # spruce
    sp = tmp_path / "spruce_card"
    (sp / "spruce" / "bin").mkdir(parents=True)
    (sp / "spruce" / "bin" / "device").write_text("miyoomini\n")
    assert core.detect_card_device(SPRUCE, sp) == "miyoomini"


# --------------------------------------------------------------------------
# 7. Device matching check
# --------------------------------------------------------------------------

def test_check_device_card_match():
    # TrimUI Brick Pro
    ok, _ = core.check_device_card_match(BRICK, "tg5040")
    assert ok is True

    ok, _ = core.check_device_card_match(BRICK, "tg5050")
    assert ok is False

    # Anbernic RG35XX SP
    ok, _ = core.check_device_card_match(RG35XXSP, "rg35xx-sp")
    assert ok is True

    ok, _ = core.check_device_card_match(RG35XXSP, "brickpro")
    assert ok is False

    # Unknown device string
    ok, _ = core.check_device_card_match(RG35XXSP, "unknown_custom_board")
    assert ok is None

    # Short string should not falsely match
    ok, _ = core.check_device_card_match(RG35XXSP, "sp")
    assert ok is None

    # Card has no device info
    ok, _ = core.check_device_card_match(RG35XXSP, None)
    assert ok is None


# --------------------------------------------------------------------------
# 8. CFW version checks
# --------------------------------------------------------------------------

def test_read_cfw_version_and_portmaster_version(tmp_path):
    mu = tmp_path / "muos"
    (mu / "MUOS" / "PortMaster").mkdir(parents=True)
    (mu / "MUOS" / "theme" / "MustardOS").mkdir(parents=True)
    (mu / "MUOS" / "theme" / "MustardOS" / "version.txt").write_text("2606.0 Banana\n")
    (mu / "MUOS" / "PortMaster" / "version").write_text("2024.10.15\n")

    assert core.read_cfw_version(MUOS, mu) == "2606.0 Banana"
    assert core.read_portmaster_version(MUOS, mu) == "2024.10.15"


def test_check_cfw_version_minimum():
    # muOS: min_cfw_version is 2606.0
    ok, msg = core.check_cfw_version_minimum(MUOS, "2606.0")
    assert ok is True
    assert msg is None

    ok, msg = core.check_cfw_version_minimum(MUOS, "2607.1")
    assert ok is True

    ok, msg = core.check_cfw_version_minimum(MUOS, "2405.0")
    assert ok is False
    assert msg is not None
    assert "below the minimum" in msg

    # None or unknown version does not block
    ok, msg = core.check_cfw_version_minimum(MUOS, None)
    assert ok is True


# --------------------------------------------------------------------------
# 9. Pak update helper preservation & space check
# --------------------------------------------------------------------------

def test_copy_tree_merge_preserves_new_libs(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    (src / "file_new.txt").write_text("new")
    (src / "file_common.txt").write_text("src_version")

    (dst / "file_old.txt").write_text("old")
    (dst / "file_common.txt").write_text("dst_version")

    # With overwrite=False, destination file is preserved (not overwritten by source)
    core._copy_tree_merge(src, dst, overwrite=False)
    assert (dst / "file_new.txt").read_text() == "new"
    assert (dst / "file_old.txt").read_text() == "old"
    assert (dst / "file_common.txt").read_text() == "dst_version"


def test_update_pak_insufficient_space(tmp_path):
    t = core.Target(cfw=NEXTUI, root=tmp_path)
    (tmp_path / "Emus" / "tg5040").mkdir(parents=True)
    pak_dir = tmp_path / "Emus" / "tg5040" / "PORTS.pak"
    pak_dir.mkdir()

    # Mock get_free_space returning tiny space (1 MB)
    with patch("core.get_free_space", return_value=1024 * 1024):
        with pytest.raises(RuntimeError, match="Not enough free space"):
            core.update_pak(t)


# --------------------------------------------------------------------------
# 10. Card matching blank or wrong CFW
# --------------------------------------------------------------------------

def test_card_matches_blank_or_wrong_cfw(tmp_path):
    blank = tmp_path / "blank"
    blank.mkdir()
    assert core.card_matches(MUOS, blank) is False

    wrong = tmp_path / "wrong"
    (wrong / ".system").mkdir(parents=True)  # NextUI marker
    assert core.card_matches(MUOS, wrong) is False


# --------------------------------------------------------------------------
# 11. UI and validation checks
# --------------------------------------------------------------------------

def test_cfw_page_ready_validations(tmp_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    import app as pp_app
    page = pp_app.CfwPage()
    page.dev = BRICK
    page.cfw = MUOS

    card_dir = tmp_path / "card"

    # Mock is_pc_drive for card_dir so it acts as a removable drive rather than
    # being blocked by the system drive check (on Windows tmp_path is on C:)
    real_is_pc = core.is_pc_drive
    def mock_is_pc_drive(p, cfw=None):
        try:
            if Path(p).resolve() == card_dir.resolve():
                return False
        except Exception:
            pass
        return real_is_pc(p, cfw)
    monkeypatch.setattr(core, "is_pc_drive", mock_is_pc_drive)

    # 1. Non-existent root
    page.sd.setEditText(str(tmp_path / "non_existent"))
    assert page.ready() is False

    # 2. PC drive
    page.sd.setEditText("C:\\Windows" if os.name == "nt" else "/usr")
    assert page.ready() is False

    # 3. Blank card
    card_dir.mkdir()
    page.sd.setEditText(str(card_dir))
    assert page.ready() is False

    # 4. Valid card structure
    (card_dir / "MUOS" / "info").mkdir(parents=True)
    (card_dir / "ROMS").mkdir(parents=True)
    (card_dir / "MUOS" / "theme" / "MustardOS").mkdir(parents=True)
    (card_dir / "MUOS" / "theme" / "MustardOS" / "version.txt").write_text("2606.0\n")
    (card_dir / "MUOS" / "info" / "device.txt").write_text("brickpro\n")
    page.sd.setEditText(str(card_dir))
    assert page.ready() is True

    # 5. Device mismatch
    (card_dir / "MUOS" / "info" / "device.txt").write_text("tg5050\n")
    page.sd.setEditText(str(card_dir))
    assert page.ready() is False

    # 6. Version too low
    (card_dir / "MUOS" / "info" / "device.txt").write_text("brickpro\n")
    (card_dir / "MUOS" / "theme" / "MustardOS" / "version.txt").write_text("2405.0\n")
    page.sd.setEditText(str(card_dir))
    assert page.ready() is False


def test_ports_page_preserves_selection(tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    import app as pp_app
    ports_page = pp_app.PortsPage(pp_app.Images())

    p1 = make_port("game1")
    p2 = make_port("game2")
    ports_page.set_ports([p1, p2])

    # Select game1
    ports_page.model.selected.add("game1.zip")

    # Refresh ports passing keep_selection
    ports_page.set_ports([p1, p2], keep_selection=["game1.zip"])
    assert "game1.zip" in ports_page.model.selected
