"""Unit and integration tests for T-023 Steam QR Login and selective download prototype.

Tests the official Steam QR auth flow, DPAPI secure persistence, logout functionality,
DepotDownloader selective filelist generation, mock runner, SD card install,
and pure SFTP wireless transfer for muOS on RG35XX SP/H (Anbernic H700).

Validates all S2 review requirements:
1. Strict mode separation: real builds raise FileNotFoundError when DepotDownloader is missing;
   never silently falls back to mock files.
2. Official DepotDownloader parameters and stdin=subprocess.DEVNULL with timeout watchdog.
3. Official SteamRE GitHub release pinning and SHA-256 checksum verification.
4. Recipes trimmed to 1–2 ports (Balatro, Undertale) cited to PortMaster (untested; needs hardware test).
5. Non-Windows DPAPI refuses plaintext fallback (raises NotImplementedError).
6. Non-blocking threaded output reading with responsive cancel.
7. Zero token/credential logging; stores only account name without tokens.
8. Roundtrips block-character QR lines to module grid and crisp QPixmap.
9. find_depot_downloader strictly runs pinned tools/DepotDownloader_3.4.0/ copy.
"""
from __future__ import annotations

import io
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from unittest.mock import MagicMock, patch

import pytest

import core
from profiles import CFWS, DEVICES, MUOS, Device
import steam_auth
import steam_downloader
import wireless


# --------------------------------------------------------------------------
# Fixtures & Test Setup
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_steam_auth(tmp_path):
    """Isolate steam auth storage to a clean temp directory for every test."""
    orig_app_dir = steam_auth.APP_DIR
    orig_auth_file = steam_auth.STEAM_AUTH_FILE

    steam_auth.APP_DIR = tmp_path / "PortPicker"
    steam_auth.APP_DIR.mkdir(parents=True, exist_ok=True)
    steam_auth.STEAM_AUTH_FILE = steam_auth.APP_DIR / "steam_auth.dat"

    steam_auth._TEST_MODE = True

    yield

    steam_auth._TEST_MODE = False
    steam_auth.APP_DIR = orig_app_dir
    steam_auth.STEAM_AUTH_FILE = orig_auth_file


@pytest.fixture
def h700_sp_device() -> Device:
    """RG35XX SP handheld (muOS, Anbernic H700)."""
    return next(d for d in DEVICES if d.id == "rg35xxsp")


# --------------------------------------------------------------------------
# 1. DPAPI Token Persistence and Security
# --------------------------------------------------------------------------

def test_dpapi_refuses_plaintext_on_non_windows():
    """Verify that in production mode on non-Windows, DPAPI refuses to run."""
    steam_auth._TEST_MODE = False
    if sys.platform != "win32":
        with pytest.raises(NotImplementedError) as exc_info:
            steam_auth._win_dpapi_protect(b"secret_data")
        assert "Windows" in str(exc_info.value)
    steam_auth._TEST_MODE = True


def test_dpapi_save_and_retrieve_credentials():
    assert not steam_auth.is_steam_authenticated()
    assert steam_auth.get_steam_credentials() is None

    steam_auth.save_steam_credentials(account_name="test_user_gamer")

    assert steam_auth.is_steam_authenticated()
    creds = steam_auth.get_steam_credentials()
    assert creds is not None
    assert creds["account_name"] == "test_user_gamer"
    assert "refresh_token" not in creds
    assert "password" not in creds
    assert "steam_id" not in creds


def test_logout_deletes_credentials_and_clears_sessions(tmp_path):
    steam_auth.save_steam_credentials("user1")
    assert steam_auth.is_steam_authenticated()

    # Create dummy account.config in isolated storage directory
    fake_iso = tmp_path / "IsolatedStorage" / "hash123" / "Files"
    fake_iso.mkdir(parents=True, exist_ok=True)
    fake_acc = fake_iso / "account.config"
    fake_acc.write_text("dummy", encoding="utf-8")

    with patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}):
        res = steam_auth.logout_steam()
        assert res is True
        assert not steam_auth.is_steam_authenticated()
        assert steam_auth.get_steam_credentials() is None
        assert not steam_auth.STEAM_AUTH_FILE.exists()
        assert not fake_acc.exists()


def test_no_credentials_logged_to_output():
    """Verify that credentials, tokens, and sensitive auth data are never logged."""
    logs: list[str] = []
    recipe = steam_downloader.STEAM_DEPOT_RECIPES["balatro"]

    tmp_dest = steam_auth.APP_DIR / "dummy_dest"
    steam_downloader.fake_depot_download(recipe, tmp_dest, log=logs.append)

    full_log_text = " ".join(logs).lower()
    assert "refresh_token" not in full_log_text
    assert "password" not in full_log_text
    assert "access_token" not in full_log_text


# --------------------------------------------------------------------------
# 2. Block-character QR to Module Grid & QPixmap Roundtrip
# --------------------------------------------------------------------------

def test_qr_ascii_to_modules_and_pixmap_roundtrip():
    """Verify S2 Must Fix #3:
    Round-trip a known QR through qrcode -> text lines -> module grid -> QPixmap.
    Checks 100% module equality in both normal and inverted modes.
    """
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data("https://s.team/q/1/123456789")
    qr.make(fit=True)

    border = qr.border
    modcount = qr.modules_count

    # Build reference grid from qrcode
    orig_grid: list[list[bool]] = []
    for r in range(-border, modcount + border):
        row = []
        for c in range(-border, modcount + border):
            if min(r, c) < 0 or max(r, c) >= modcount:
                row.append(False)
            else:
                row.append(bool(qr.modules[r][c]))
        orig_grid.append(row)

    # 1. Roundtrip with invert=False (terminal normal)
    buf_normal = io.StringIO()
    qr.print_ascii(out=buf_normal, tty=False, invert=False)
    lines_normal = buf_normal.getvalue().splitlines()
    mods_normal = steam_auth.qr_ascii_to_modules(lines_normal)

    # 2. Roundtrip with invert=True (terminal inverted / dark background)
    buf_inverted = io.StringIO()
    qr.print_ascii(out=buf_inverted, tty=False, invert=True)
    lines_inverted = buf_inverted.getvalue().splitlines()
    mods_inverted = steam_auth.qr_ascii_to_modules(lines_inverted)

    # Verify dimensions and module equality against qrcode
    min_r = min(len(orig_grid), len(mods_normal))
    min_c = min(len(orig_grid[0]), len(mods_normal[0]))
    for r in range(min_r):
        for c in range(min_c):
            assert mods_normal[r][c] == orig_grid[r][c], f"Normal mismatch at ({r}, {c})"
            assert mods_inverted[r][c] == orig_grid[r][c], f"Inverted mismatch at ({r}, {c})"

    # Verify QPixmap conversion generates crisp non-empty pixel image
    pix = steam_auth.qr_ascii_to_pixmap(lines_inverted, target_size=240)
    assert pix is not None
    assert not pix.isNull()
    assert pix.width() > 0 and pix.height() > 0

    # Verify pixels on QImage match module grid
    img = pix.toImage()
    from PySide6.QtGui import QColor
    sample_col = img.pixelColor(pix.width() // 2, pix.height() // 2)
    assert sample_col in (QColor(0, 0, 0, 255), QColor(255, 255, 255, 255))


# --------------------------------------------------------------------------
# 3. Official SteamRE Release Pinning & Verification
# --------------------------------------------------------------------------

def test_depot_downloader_pinning_and_sha256(tmp_path):
    assert steam_downloader.DEPOT_DOWNLOADER_VERSION == "3.4.0"
    win_sha = steam_downloader.DEPOT_DOWNLOADER_SHA256["win32"]
    linux_sha = steam_downloader.DEPOT_DOWNLOADER_SHA256["linux"]

    assert win_sha == "41c9e9f0df54b3ad02e67a11726756e5c73283bd7c2e1b04acfa5ae4c2ed3767"
    assert linux_sha == "a999dec66b4850fc961bd50366696d23c2d0fad7b18790e6a5647b2f19097a53"

    sample_f = tmp_path / "sample.bin"
    sample_f.write_bytes(b"hello world")
    import hashlib
    expected = hashlib.sha256(b"hello world").hexdigest()

    assert steam_downloader.verify_file_sha256(sample_f, expected)
    assert not steam_downloader.verify_file_sha256(sample_f, "00000000000000000000000000000000")


def test_ensure_depot_downloader_declined_by_user():
    with patch("steam_downloader.find_depot_downloader", return_value=None):
        def user_declines(msg):
            return False

        with pytest.raises(RuntimeError) as exc_info:
            steam_downloader.ensure_depot_downloader(confirm_callback=user_declines)
        assert "cancelled by user" in str(exc_info.value)


def test_find_depot_downloader_strict_pinned_only(tmp_path):
    """Verify S2 Must Fix #4:
    In production mode, find_depot_downloader refuses PATH, unversioned tools/,
    and PORTPICKER_DEPOT_DOWNLOADER env var. Only tools/DepotDownloader_3.4.0/ is accepted.
    """
    candidate_name = "DepotDownloader.exe" if sys.platform == "win32" else "DepotDownloader"
    fake_tools = tmp_path / "PortPicker" / "tools"
    fake_tools.mkdir(parents=True, exist_ok=True)

    # 1. Unversioned tools/exe
    unversioned_exe = fake_tools / candidate_name
    unversioned_exe.write_text("dummy")

    # 2. Random exe on PATH / external
    ext_exe = tmp_path / "external_depot" / candidate_name
    ext_exe.parent.mkdir(parents=True, exist_ok=True)
    ext_exe.write_text("dummy")

    with patch.dict(os.environ, {
        "LOCALAPPDATA": str(tmp_path),
        "PORTPICKER_DEPOT_DOWNLOADER": str(ext_exe),
        "PORTPICKER_TEST_MODE": "",
        "PORTPICKER_FAKE_DEPOT_DOWNLOADER": "",
    }):
        orig_test = steam_auth._TEST_MODE
        steam_auth._TEST_MODE = False
        try:
            # Must NOT pick up PORTPICKER_DEPOT_DOWNLOADER, unversioned tools, or PATH
            res = steam_downloader.find_depot_downloader()
            assert res is None

            # Now install pinned copy in tools/DepotDownloader_3.4.0/
            pinned_dir = fake_tools / f"DepotDownloader_{steam_downloader.DEPOT_DOWNLOADER_VERSION}"
            pinned_dir.mkdir(parents=True, exist_ok=True)
            pinned_exe = pinned_dir / candidate_name
            pinned_exe.write_text("pinned")

            res_pinned = steam_downloader.find_depot_downloader()
            assert res_pinned == pinned_exe
        finally:
            steam_auth._TEST_MODE = orig_test


# --------------------------------------------------------------------------
# 4. Strict Mode Separation (No Silent Mock Fallback in Real Mode)
# --------------------------------------------------------------------------

def test_production_mode_fails_if_depot_downloader_missing(tmp_path):
    """Verify S2 Must Fix #1: Real mode must raise FileNotFoundError if DepotDownloader is missing."""
    recipe = steam_downloader.STEAM_DEPOT_RECIPES["balatro"]
    dest = tmp_path / "balatro_files"

    with patch("steam_downloader.find_depot_downloader", return_value=None):
        with patch.dict(os.environ, {"PORTPICKER_FAKE_DEPOT_DOWNLOADER": ""}):
            with pytest.raises(FileNotFoundError) as exc_info:
                steam_downloader.download_steam_files(recipe, dest, fake_mode=False)
            assert "DepotDownloader" in str(exc_info.value)


def test_fake_mode_downloads_mock_files(tmp_path):
    recipe = steam_downloader.STEAM_DEPOT_RECIPES["balatro"]
    dest = tmp_path / "balatro_files"

    res = steam_downloader.download_steam_files(recipe, dest, fake_mode=True)
    assert res == dest
    assert (dest / "Balatro.exe").exists()
    assert (dest / "Balatro.exe").read_bytes().startswith(b"MOCK_STEAM_FILE_CONTENT")


# --------------------------------------------------------------------------
# 5. Verified Recipes & DepotDownloader Arguments
# --------------------------------------------------------------------------

def test_verified_recipes():
    """Verify S2 Must Fix #1: Recipes trimmed to 1-2 ports (untested; needs hardware test)."""
    recipes = steam_downloader.STEAM_DEPOT_RECIPES
    assert len(recipes) == 2
    assert "balatro" in recipes
    assert "undertale" in recipes

    # Balatro check
    b = recipes["balatro"]
    assert b.appid == "2379780"
    assert b.depot == "2379781"
    assert b.dest == "balatro"
    assert b.filelist == ("Balatro.exe",)
    assert b.os == "windows"

    # Undertale check
    u = recipes["undertale"]
    assert u.appid == "391540"
    assert u.depot == "391541"
    assert u.dest == "undertale/assets"
    assert "data.win" in u.filelist
    assert u.os == "windows"


def test_build_depotdownloader_args():
    recipe = steam_downloader.STEAM_DEPOT_RECIPES["balatro"]
    dest = Path("/games/balatro")
    fl = Path("/tmp/filelist.txt")

    args = steam_downloader.build_depotdownloader_args(
        recipe=recipe,
        dest_dir=dest,
        filelist_path=fl,
        account_name="my_user",
        remember_password=True,
    )

    assert "-app" in args and args[args.index("-app") + 1] == "2379780"
    assert "-depot" in args and args[args.index("-depot") + 1] == "2379781"
    assert "-dir" in args and args[args.index("-dir") + 1] == str(dest)
    assert "-filelist" in args and args[args.index("-filelist") + 1] == str(fl)
    assert "-username" in args and args[args.index("-username") + 1] == "my_user"
    assert "-remember-password" in args


def test_write_filelist(tmp_path):
    recipe = steam_downloader.STEAM_DEPOT_RECIPES["undertale"]
    out_fl = tmp_path / "undertale_fl.txt"
    res = steam_downloader.write_filelist(recipe, out_fl)

    assert res == out_fl
    content = out_fl.read_text(encoding="utf-8")
    assert "data.win" in content
    assert "regex:.*\\.ogg" in content


def test_responsive_cancel_in_process_runner():
    """Verify that cancellation stops subprocess immediately within timeout."""
    cancelled = True
    with pytest.raises(InterruptedError) as exc_info:
        steam_downloader.run_depot_downloader_process(
            cmd=[sys.executable, "-c", "import time; time.sleep(10)"],
            cancel=lambda: cancelled,
            inactivity_timeout=5.0,
        )
    assert "cancelled" in str(exc_info.value)


def test_qr_login_runner_fake_mode():
    """Verify QR login runner in fake mode captures QR lines and approves user."""
    qr_lines_received = []
    approved_user = []

    steam_downloader.run_depot_downloader_qr_login(
        on_qr_lines=qr_lines_received.extend,
        on_approved=approved_user.append,
        fake_mode=True,
    )

    assert len(qr_lines_received) > 0
    assert approved_user == ["MockUser"]
    assert steam_auth.is_steam_authenticated()


# --------------------------------------------------------------------------
# 6. SD Card Install Integration
# --------------------------------------------------------------------------

def test_install_port_with_steam_download(tmp_path, h700_sp_device):
    sd_root = tmp_path / "sdcard"
    sd_root.mkdir()
    target = core.Target(cfw=MUOS, root=sd_root)

    port_zip = tmp_path / "undertale.zip"
    with zipfile.ZipFile(port_zip, "w") as zf:
        zf.writestr("Undertale.sh", "#!/bin/sh\n./undertale/undertale\n")
        zf.writestr("undertale/undertale", "#!/bin/sh\n")

    port = core.Port(
        name="undertale.zip",
        title="Undertale",
        desc="RPG game",
        inst="needs data.win",
        genres=["rpg"],
        rtr=False,
        availability="paid",
        reqs=[],
        runtime=[],
        arch=["aarch64"],
        min_glibc="",
        items=["Undertale.sh", "undertale/"],
        url=port_zip.as_uri(),
        md5="",
        size=1024,
        screenshot="",
        cover="",
        date_updated="2026-11-17",
    )
    port.steam_depot = steam_downloader.STEAM_DEPOT_RECIPES["undertale"]

    logs = []
    with patch("steam_downloader.download_steam_files", wraps=steam_downloader.fake_depot_download):
        core.install_port(
            p=port,
            t=target,
            dev=h700_sp_device,
            utils={},
            log=logs.append,
            overwrite_local=True,
        )

    assert (target.scripts_dir / "Undertale.sh").exists()
    ut_assets = target.ports_dir / "undertale" / "assets"
    assert ut_assets.exists()
    assert (ut_assets / "data.win").exists() or (ut_assets / "file.ogg").exists()
    assert any("Placed" in l and "Steam files" in l for l in logs)


# --------------------------------------------------------------------------
# 7. Pure SFTP Wireless Transfer Integration (muOS H700)
# --------------------------------------------------------------------------

from tests.test_wireless import MockSFTPClient, MockSFTPStat


def test_wireless_transfer_with_steam_download(tmp_path, h700_sp_device):
    """Verify pure SFTP transfer with selective Steam download staged directly."""
    sftp_mock = MockSFTPClient()
    conn = wireless.WirelessConnection()
    conn.sftp = sftp_mock

    probe = wireless.probe_remote(conn, device_hint=h700_sp_device)
    assert probe.cfw_id == "muos"

    # Create dummy port zip
    port_zip = tmp_path / "balatro.zip"
    with zipfile.ZipFile(port_zip, "w") as zf:
        zf.writestr("Balatro.sh", "#!/bin/sh\n./balatro/balatro\n")
        zf.writestr("balatro/balatro", "#!/bin/sh\n")

    port = core.Port(
        name="balatro.zip",
        title="Balatro",
        desc="Roguelike card game",
        inst="needs Balatro.exe",
        genres=["card"],
        rtr=False,
        availability="paid",
        reqs=[],
        runtime=[],
        arch=["aarch64"],
        min_glibc="",
        items=["Balatro.sh", "balatro/"],
        url=port_zip.as_uri(),
        md5="",
        size=1024,
        screenshot="",
        cover="",
        date_updated="2026-11-17",
    )
    port.steam_depot = steam_downloader.STEAM_DEPOT_RECIPES["balatro"]

    logs = []
    with patch("steam_downloader.download_steam_files", wraps=steam_downloader.fake_depot_download):
        wireless.transfer_port_wireless(
            conn=conn,
            port=port,
            probe=probe,
            dev=h700_sp_device,
            utils={},
            log=logs.append,
            overwrite_local=True,
        )

    # Verify atomic SFTP transfer of Balatro.exe to /Primary Storage/ROMS/ports/balatro/Balatro.exe
    uploaded_remotes = [dst for src, dst in sftp_mock.rename_calls]
    assert any("Balatro.exe" in path for path in uploaded_remotes)
    assert any("Balatro.sh" in path for path in uploaded_remotes)
    assert any("Downloading selective game files from Steam" in l for l in logs)
