"""Tests for T-055: Steam games filter and owned/not-owned check.

Validates all T-055 requirements:
1. Appid parsing from ports.json store URLs and STEAM_DEPOT_RECIPES.
2. Ownership cache operations, JSON schema ({account: {appid: {"owned": bool, "checked": ts}}}), and 7-day expiry.
3. Fake DepotDownloader outputs for:
   - Owned (exits 0 with manifest)
   - Not owned (App not available ContentDownloader.cs:438 / Depot not available ContentDownloader.cs:565)
   - Login missing (password prompt / SteamUser.LogOn crash)
4. Deletion of temporary -dir after each check.
5. "🎮 Steam" filter showing paid Steam ports in FREE_ONLY mode (ready-only does not hide them).
6. "Owned only" filter showing only owned Steam games when authenticated.
"""
from __future__ import annotations

import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
import core
import steam_auth
import steam_downloader


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_steam_env(tmp_path):
    """Isolate steam auth and cache to a clean temp directory for every test."""
    orig_app_dir = steam_auth.APP_DIR
    orig_auth_file = steam_auth.STEAM_AUTH_FILE
    orig_test_mode = getattr(steam_auth, "_TEST_MODE", False)

    steam_auth.APP_DIR = tmp_path / "PortPicker"
    steam_auth.APP_DIR.mkdir(parents=True, exist_ok=True)
    steam_auth.STEAM_AUTH_FILE = steam_auth.APP_DIR / "steam_auth.dat"
    steam_auth._TEST_MODE = True

    yield

    steam_auth.APP_DIR = orig_app_dir
    steam_auth.STEAM_AUTH_FILE = orig_auth_file
    steam_auth._TEST_MODE = orig_test_mode


def make_port(stem: str, **kw) -> core.Port:
    base = dict(
        name=f"{stem}.zip",
        title=stem.replace("_", " ").title(),
        desc="",
        inst="",
        genres=["Action"],
        rtr=False,
        availability="paid",
        reqs=[],
        runtime=[],
        arch=["aarch64"],
        min_glibc="",
        items=[f"{stem}.sh", stem],
        url="",
        md5="",
        size=1024,
        screenshot="",
        cover="",
        date_updated="",
    )
    base.update(kw)
    return core.Port(**base)


# --------------------------------------------------------------------------
# 1. Appid Parsing Tests
# --------------------------------------------------------------------------

def test_parse_steam_appid_from_urls():
    # Standard Steam store URLs
    assert core.parse_steam_appid("https://store.steampowered.com/app/1336340/SPROUT/") == "1336340"
    assert core.parse_steam_appid("https://store.steampowered.com/app/413150/Stardew_Valley") == "413150"
    assert core.parse_steam_appid("http://store.steampowered.com/app/391540") == "391540"
    assert core.parse_steam_appid("https://store.steampowered.com/app/2379780/Balatro/") == "2379780"

    # URLs with extra parameters or fragments
    assert core.parse_steam_appid("https://store.steampowered.com/app/413150?l=thai") == "413150"

    # Non-Steam URLs or invalid input
    assert core.parse_steam_appid("https://www.gog.com/en/game/steamworld_dig") is None
    assert core.parse_steam_appid("https://itch.io/game/celeste") is None
    assert core.parse_steam_appid("") is None
    assert core.parse_steam_appid(None) is None


def test_port_steam_appid_auto_resolution():
    # From stores gameurl
    p1 = make_port("sprout", stores=[{"name": "Steam", "gameurl": "https://store.steampowered.com/app/1336340/SPROUT/"}])
    assert p1.steam_appid == "1336340"

    # From stores url (like Undertale in ports.json)
    p2 = make_port("custom_game", stores=[{"name": "Steam", "url": "https://store.steampowered.com/app/98765/Custom/"}])
    assert p2.steam_appid == "98765"

    # From STEAM_DEPOT_RECIPES (Balatro has no store entry in ports.json)
    p_balatro = make_port("balatro")
    assert p_balatro.steam_appid == "2379780"

    # Non-Steam port
    p_non = make_port("freedroid", stores=[{"name": "Website", "url": "https://example.com"}])
    assert p_non.steam_appid is None

    # Explicit steam_appid preserved
    p_exp = make_port("any_game", steam_appid="55555")
    assert p_exp.steam_appid == "55555"


# --------------------------------------------------------------------------
# 2. Ownership Cache Operations and Expiry Tests
# --------------------------------------------------------------------------

def test_cache_operations_and_7_day_expiry():
    # Initially empty
    assert steam_downloader.get_cached_ownership("tawa", "413150") is None
    assert steam_downloader.load_steam_owned_cache() == {}

    # Set owned = True
    now = int(time.time())
    steam_downloader.set_cached_ownership("tawa", "413150", True)

    cache = steam_downloader.load_steam_owned_cache()
    acct_hash = steam_downloader._account_hash("tawa")
    assert acct_hash in cache
    assert "413150" in cache[acct_hash]
    assert cache[acct_hash]["413150"]["owned"] is True
    assert abs(cache[acct_hash]["413150"]["checked"] - now) < 5

    assert steam_downloader.get_cached_ownership("tawa", "413150") is True
    # Other user or app
    assert steam_downloader.get_cached_ownership("other", "413150") is None
    assert steam_downloader.get_cached_ownership("tawa", "999999") is None

    # Test cache within 7 days (e.g. 6 days old)
    six_days_ago = now - int(6 * 86400)
    cache[acct_hash]["413150"]["checked"] = six_days_ago
    steam_downloader.save_steam_owned_cache(cache)
    assert steam_downloader.get_cached_ownership("tawa", "413150") is True

    # Test cache expired (> 7 days, e.g. 8 days old)
    eight_days_ago = now - int(8 * 86400)
    cache[acct_hash]["413150"]["checked"] = eight_days_ago
    steam_downloader.save_steam_owned_cache(cache)
    assert steam_downloader.get_cached_ownership("tawa", "413150") is None

    # Test legacy unhashed account key backward compatibility
    legacy_cache = {"old_user": {"12345": {"owned": True, "checked": now}}}
    steam_downloader.save_steam_owned_cache(legacy_cache)
    assert steam_downloader.get_cached_ownership("old_user", "12345") is True


# --------------------------------------------------------------------------
# 3. Fake DepotDownloader Outputs Tests
# --------------------------------------------------------------------------

def _create_fake_depot_downloader(tmp_path: Path, script_content: str) -> Path:
    script_file = tmp_path / "fake_dd.py"
    script_file.write_text(script_content, encoding="utf-8")
    launcher_file = tmp_path / ("fake_dd.bat" if sys.platform == "win32" else "fake_dd.sh")
    if sys.platform == "win32":
        launcher_file.write_text(f'@"{sys.executable}" "{script_file}" %*\n', encoding="utf-8")
    else:
        launcher_file.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script_file}" "$@"\n', encoding="utf-8")
        launcher_file.chmod(0o755)
    return launcher_file


def test_check_steam_ownership_owned(tmp_path):
    # Simulates DepotDownloader downloading manifest and exiting 0
    script = (
        "import sys, pathlib\n"
        "args = sys.argv[1:]\n"
        "print('Connecting to Steam3...', flush=True)\n"
        "print('Logging in...', flush=True)\n"
        "print('Got app info for ' + (args[args.index('-app') + 1] if '-app' in args else '?'), flush=True)\n"
        "print('Manifest download complete.', flush=True)\n"
        "sys.exit(0)\n"
    )
    fake_exe = _create_fake_depot_downloader(tmp_path, script)

    created_dirs = []
    real_mkdtemp = tempfile.mkdtemp
    def recording_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        created_dirs.append(d)
        return d

    with patch("tempfile.mkdtemp", side_effect=recording_mkdtemp):
        res = steam_downloader.check_steam_ownership(
            appid="413150",
            account_name="tawa",
            depot_downloader_exe=fake_exe,
        )
        assert res is True
        assert len(created_dirs) == 1
        assert not Path(created_dirs[0]).exists()


def test_check_steam_ownership_not_owned_app(tmp_path):
    # Simulates DepotDownloader ContentDownloader.cs:438
    # "App <id> (<name>) is not available from this account."
    script = (
        "import sys\n"
        "print('Connecting to Steam3...', flush=True)\n"
        "print('Logging in...', flush=True)\n"
        "print('App 1336340 (SPROUT) is not available from this account.', flush=True)\n"
        "sys.exit(1)\n"
    )
    fake_exe = _create_fake_depot_downloader(tmp_path, script)

    created_dirs = []
    real_mkdtemp = tempfile.mkdtemp
    def recording_mkdtemp(*args, **kwargs):
        d = real_mkdtemp(*args, **kwargs)
        created_dirs.append(d)
        return d

    with patch("tempfile.mkdtemp", side_effect=recording_mkdtemp):
        res = steam_downloader.check_steam_ownership(
            appid="1336340",
            account_name="tawa",
            depot_downloader_exe=fake_exe,
        )
        assert res is False
        assert len(created_dirs) == 1
        assert not Path(created_dirs[0]).exists()


def test_check_steam_ownership_not_owned_depot(tmp_path):
    # Simulates DepotDownloader ContentDownloader.cs:565
    # "Depot 2379781 is not available from this account."
    # When an app is owned, DepotDownloader prints depot unavailability for DLC/extra depots,
    # but the app itself is owned (ContentDownloader.cs:438 is NOT thrown).
    # Must return True (owned) per S2 Must Fix #2.
    script = (
        "import sys\n"
        "print('Connecting to Steam3...', flush=True)\n"
        "print('Logging in...', flush=True)\n"
        "print('Got app info for 2379780', flush=True)\n"
        "print('Depot 2379781 is not available from this account.', flush=True)\n"
        "print('Manifest download complete.', flush=True)\n"
        "sys.exit(0)\n"
    )
    fake_exe = _create_fake_depot_downloader(tmp_path, script)

    res = steam_downloader.check_steam_ownership(
        appid="2379780",
        account_name="tawa",
        depot_downloader_exe=fake_exe,
    )
    assert res is True


def test_check_steam_ownership_free_on_demand_license(tmp_path):
    # Simulates DepotDownloader ContentDownloader.cs:426-438
    # "Obtained FreeOnDemand license for app ..."
    script = (
        "import sys\n"
        "print('Connecting to Steam3...', flush=True)\n"
        "print('Logging in...', flush=True)\n"
        "print('Obtained FreeOnDemand license for app 1336340', flush=True)\n"
        "print('Manifest download complete.', flush=True)\n"
        "sys.exit(0)\n"
    )
    fake_exe = _create_fake_depot_downloader(tmp_path, script)
    logs = []
    res = steam_downloader.check_steam_ownership(
        appid="1336340",
        account_name="tawa",
        depot_downloader_exe=fake_exe,
        log=logs.append,
    )
    assert res is True
    assert any("Added to your library by Steam" in l for l in logs)


def test_check_steam_ownership_login_missing_password_prompt(tmp_path):
    # Simulates DepotDownloader prompting for password when token is missing/invalid
    script = (
        'import sys\n'
        'print("Logging into Steam3...", flush=True)\n'
        'print("Enter account password for \\"tawa\\": ", flush=True)\n'
        'sys.exit(0)\n'
    )
    fake_exe = _create_fake_depot_downloader(tmp_path, script)

    steam_auth.save_steam_credentials("tawa")
    assert steam_auth.is_steam_authenticated() is True

    with pytest.raises(RuntimeError, match="Steam login was not saved"):
        steam_downloader.check_steam_ownership(
            appid="413150",
            account_name="tawa",
            depot_downloader_exe=fake_exe,
        )

    # Must forget local login so user is prompted to QR scan again
    assert steam_auth.is_steam_authenticated() is False


def test_check_steam_ownership_login_missing_logon_crash(tmp_path):
    # Simulates DepotDownloader crash in SteamUser.LogOn
    script = (
        "import sys\n"
        "print('Logging in...', flush=True)\n"
        "print('Unhandled exception: SteamUser.LogOn failed with error', flush=True)\n"
        "sys.exit(1)\n"
    )
    fake_exe = _create_fake_depot_downloader(tmp_path, script)

    steam_auth.save_steam_credentials("tawa")
    with pytest.raises(RuntimeError, match="Steam login was not saved"):
        steam_downloader.check_steam_ownership(
            appid="413150",
            account_name="tawa",
            depot_downloader_exe=fake_exe,
        )


def test_check_steam_ownership_batch(tmp_path):
    # Batch check with 1 owned and 1 unowned app
    script = (
        "import sys\n"
        "args = sys.argv[1:]\n"
        "appid = args[args.index('-app') + 1] if '-app' in args else ''\n"
        "if appid == '413150':\n"
        "    print('Manifest downloaded', flush=True)\n"
        "    sys.exit(0)\n"
        "else:\n"
        "    print('App ' + appid + ' is not available from this account.', flush=True)\n"
        "    sys.exit(1)\n"
    )
    fake_exe = _create_fake_depot_downloader(tmp_path, script)

    progress_events = []
    results = steam_downloader.check_steam_ownership_batch(
        appids=["413150", "1336340"],
        account_name="tawa",
        recheck=False,
        depot_downloader_exe=fake_exe,
        progress=lambda cur, tot, appid: progress_events.append((cur, tot, appid)),
    )

    assert results == {"413150": True, "1336340": False}
    assert len(progress_events) == 2
    assert steam_downloader.get_cached_ownership("tawa", "413150") is True
    assert steam_downloader.get_cached_ownership("tawa", "1336340") is False


# --------------------------------------------------------------------------
# 4. Status Column, Filters, Detail Pane & Install Rules Tests (T-055)
# --------------------------------------------------------------------------

def test_status_column_all_cases():
    """Verify Files status column values for all 5 cases (T-055 scope update):
    1. Free ready-to-run -> ✅ Ready
    2. Local folder / local Steam supplied -> ✅ Ready
    3. Steam recipe + owned -> 🎮 Steam
    4. Steam recipe + logged in + not checked -> 🎮 Check
    5. Everything else -> 📁 Needs your files
    """
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    _ = QApplication.instance() or QApplication([])

    ports_page = app.PortsPage(app.Images())

    # Case 1: Free RTR port
    p1 = make_port("free_rtr", rtr=True, availability="full")

    # Case 2: Local folder supplied
    p2 = make_port("local_game", rtr=False, availability="paid", local_dir=Path("/fake/path"))

    # Case 3: Steam recipe + owned
    # Balatro has recipe in STEAM_DEPOT_RECIPES
    p3 = make_port("balatro", rtr=False, availability="paid", steam_owned=True)

    # Case 4: Steam recipe + logged in + not checked
    steam_auth.save_steam_credentials("tawa")
    p4 = make_port("undertale", rtr=False, availability="paid", steam_owned=None)

    # Case 5: Needs files (paid, no files supplied, unowned or no recipe)
    p5_no_recipe = make_port("custom_steam", rtr=False, availability="paid", steam_appid="99999", steam_owned=True)
    p5_unowned = make_port("balatro_unowned", rtr=False, availability="paid", steam_appid="2379780", steam_owned=False)

    ports_page.set_ports([p1, p2, p3, p4, p5_no_recipe, p5_unowned])
    model = ports_page.model

    def get_status_text(port_name: str) -> str:
        row = model.row_of[port_name]
        return model.data(model.index(row, 5), Qt.DisplayRole)

    assert "Ready" in get_status_text("free_rtr.zip")
    assert "Ready" in get_status_text("local_game.zip")
    assert "Steam" in get_status_text("balatro.zip")
    assert "Check" in get_status_text("undertale.zip")
    assert "Needs your files" in get_status_text("custom_steam.zip")
    assert "Needs your files" in get_status_text("balatro_unowned.zip")


def test_no_paid_port_hidden_by_default_and_all_filters_off():
    """Verify that FREE_ONLY is turned off, paid ports are visible by default,
    and with all filters off row count == total ports.
    """
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])

    ports_page = app.PortsPage(app.Images())

    p_free = make_port("free_game", rtr=True, availability="full", tier="ok")
    p_paid_steam = make_port("gravitycircuit", rtr=False, availability="paid", steam_appid="858710", tier="must")
    p_paid_other = make_port("paid_rpg", rtr=False, availability="paid", tier="heavy")
    p_unsupported = make_port("unsupported_game", rtr=True, availability="full", tier="incompatible")

    ports_page.set_ports([p_free, p_paid_steam, p_paid_other, p_unsupported])

    # Default view: must, ok, heavy are checked, broken & incompatible are unchecked
    visible_names = [p.name for p in ports_page.model.rows]
    assert "free_game.zip" in visible_names
    assert "gravitycircuit.zip" in visible_names  # Paid Steam port NOT hidden by default!
    assert "paid_rpg.zip" in visible_names       # Paid non-Steam port NOT hidden by default!
    assert "unsupported_game.zip" not in visible_names

    # Test Tawaphol requirement: all filters off -> row count == number of ports
    for cb in ports_page.tier_boxes.values():
        cb.setChecked(False)
    ports_page.steam_filter.setChecked(False)
    ports_page.owned_only.setChecked(False)
    ports_page.search.setText("")
    ports_page.apply_filter()

    assert ports_page.model.rowCount() == len(ports_page.ports)
    all_names = [p.name for p in ports_page.model.rows]
    assert "unsupported_game.zip" in all_names


def test_steam_filter_and_owned_filter():
    """Test Steam filter toggle and Owned only toggle."""
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])

    ports_page = app.PortsPage(app.Images())

    p_free = make_port("free_game", rtr=True, availability="full", tier="ok")
    p_steam_owned = make_port("balatro", rtr=False, availability="paid", steam_owned=True, tier="ok")
    p_steam_unowned = make_port("sprout", rtr=False, availability="paid", steam_appid="1336340", steam_owned=False, tier="ok")

    ports_page.set_ports([p_free, p_steam_owned, p_steam_unowned])

    # Turn on Steam filter
    ports_page.steam_filter.setChecked(True)
    visible_names = [p.name for p in ports_page.model.rows]
    assert "balatro.zip" in visible_names
    assert "sprout.zip" in visible_names
    assert "free_game.zip" not in visible_names

    # Turn on Owned only filter
    steam_auth.save_steam_credentials("tawa")
    ports_page.update_steam_auth_ui()
    ports_page.owned_only.setChecked(True)

    visible_names = [p.name for p in ports_page.model.rows]
    assert "balatro.zip" in visible_names
    assert "sprout.zip" not in visible_names


def test_store_links_detail_pane_and_owned_no_recipe():
    """Verify store buttons from attr.store[], PortMaster link, inst_md rendering,
    and note when owned on Steam but no automatic recipe exists.
    """
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])

    ports_page = app.PortsPage(app.Images())

    p = make_port(
        "celeste",
        stores=[
            {"name": "Steam", "gameurl": "https://store.steampowered.com/app/504230/Celeste/"},
            {"name": "Itch.io", "gameurl": "https://mattmakesgames.itch.io/celeste"},
        ],
        inst_md="Copy `Celeste.exe` into the `celeste` folder.",
        steam_appid="504230",
        steam_owned=True,  # Owned on Steam, but Celeste has no STEAM_DEPOT_RECIPES entry
    )

    ports_page.set_ports([p])
    # Select the row to trigger _show
    ports_page._show(ports_page.model.index(0, 0))

    # Store buttons in detail pane
    button_texts = [w.text() for w in ports_page.d_links_box.findChildren(app.QPushButton)]
    assert any("Steam" in text for text in button_texts)
    assert any("Itch" in text for text in button_texts)
    assert any("PortMaster" in text for text in button_texts)

    # Instructions rendered
    assert "Celeste.exe" in ports_page.d_inst.text()

    # Owned on Steam but no recipe note is visible
    assert ports_page.d_steam_owned_note.isHidden() is False
    assert "Automatic download isn't set up" in ports_page.d_steam_owned_note.text()


def test_install_blocked_for_unsupported_and_missing_files():
    """Verify that install stays blocked for Unsupported ports with reasons shown,
    and ports needing files prompt confirmation.
    """
    import profiles
    from PySide6.QtWidgets import QApplication, QMessageBox
    from unittest.mock import patch, MagicMock
    _ = QApplication.instance() or QApplication([])

    main_win = app.Main()
    main_win.dev = profiles.DEVICES[0]

    # Fake target
    mock_target = MagicMock()
    mock_target.is_wireless = False
    mock_target.ports_dir = Path("/fake/ports")
    main_win.p_cfw.target = lambda: mock_target

    # Case A: Unsupported port -> blocked
    p_unsupported = make_port("bad_game", title="Bad Game", tier="incompatible", reasons=["No aarch64 build available"])
    with patch.object(QMessageBox, "warning") as mock_warn:
        main_win._install(ports=[p_unsupported])
        assert mock_warn.called
        assert "No aarch64 build available" in mock_warn.call_args[0][2]
        assert "unsupported" in mock_warn.call_args[0][2].lower()

    # Case B: Missing files port -> confirmation asked
    p_needs_files = make_port("stardew", title="Stardew", tier="ok", rtr=False, availability="paid", inst="Needs game files")
    with patch.object(QMessageBox, "question", return_value=QMessageBox.No) as mock_q:
        main_win._install(ports=[p_needs_files])
        assert mock_q.called
        assert "Needs game files" in mock_q.call_args[0][2]
