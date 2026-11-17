"""Steam selective depot downloader for PortPicker.

Implements selective downloading for PortMaster ports from the user's Steam account
using the official SteamRE DepotDownloader release (v3.4.0 pinned with SHA-256 verification).
Meets T-023 card requirements:
1. Pinned official SteamRE GitHub release only, SHA-256 strictly checked before extraction,
   and requires explicit user confirmation before downloading.
2. Official DepotDownloader parameters (-app, -depot, -filelist, -dir, -username, -remember-password, -qr).
3. Always passes stdin=subprocess.DEVNULL to prevent interactive hangs.
4. Non-blocking threaded output reading with responsive cancellation and inactivity watchdog.
5. Strict mode separation: never silently falls back to mock files in production.
6. Recipes trimmed to 1–2 ports (Balatro, Undertale); untested, needs hardware test on RG35XX SP/H.
"""
from __future__ import annotations

import hashlib
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import urllib.request

import steam_auth

Log = Callable[[str], None]
Progress = Callable[[int, int], None]

# --------------------------------------------------------------------------
# Official SteamRE DepotDownloader Pinning & Verification
# --------------------------------------------------------------------------

DEPOT_DOWNLOADER_VERSION = "3.4.0"
DEPOT_DOWNLOADER_TAG = "DepotDownloader_3.4.0"

DEPOT_DOWNLOADER_RELEASE_URLS: dict[str, str] = {
    "win32": f"https://github.com/SteamRE/DepotDownloader/releases/download/{DEPOT_DOWNLOADER_TAG}/DepotDownloader-windows-x64.zip",
    "linux": f"https://github.com/SteamRE/DepotDownloader/releases/download/{DEPOT_DOWNLOADER_TAG}/DepotDownloader-linux-x64.zip",
}

DEPOT_DOWNLOADER_SHA256: dict[str, str] = {
    "win32": "41c9e9f0df54b3ad02e67a11726756e5c73283bd7c2e1b04acfa5ae4c2ed3767",
    "linux": "a999dec66b4850fc961bd50366696d23c2d0fad7b18790e6a5647b2f19097a53",
}


def verify_file_sha256(path: Path, expected_hash: str) -> bool:
    """Compute and verify SHA-256 hash of a file."""
    if not path.is_file():
        return False
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected_hash.lower()


def find_depot_downloader() -> Path | None:
    """Find the DepotDownloader executable path.

    Security: Only runs our own pinned copy in tools/DepotDownloader_3.4.0/.
    Refuses unversioned tools/ binaries and PATH binaries.
    PORTPICKER_DEPOT_DOWNLOADER environment variable is accepted ONLY during tests
    (when PORTPICKER_TEST_MODE, steam_auth._TEST_MODE, or PORTPICKER_FAKE_DEPOT_DOWNLOADER is set).
    """
    is_test = (
        getattr(steam_auth, "_TEST_MODE", False)
        or bool(os.environ.get("PORTPICKER_TEST_MODE"))
        or bool(os.environ.get("PORTPICKER_FAKE_DEPOT_DOWNLOADER"))
    )
    if is_test:
        env_p = os.environ.get("PORTPICKER_DEPOT_DOWNLOADER")
        if env_p and Path(env_p).is_file():
            return Path(env_p)

    tools_dir = steam_auth.APP_DIR / "tools" / f"DepotDownloader_{DEPOT_DOWNLOADER_VERSION}"
    candidate_name = "DepotDownloader.exe" if sys.platform == "win32" else "DepotDownloader"
    candidate = tools_dir / candidate_name
    if candidate.is_file():
        return candidate

    return None


def ensure_depot_downloader(
    confirm_callback: Callable[[str], bool] | None = None,
    log: Log | None = None,
) -> Path:
    """Ensure DepotDownloader is available, prompting user and verifying official SteamRE release."""
    existing = find_depot_downloader()
    if existing:
        return existing

    platform_key = "win32" if sys.platform == "win32" else "linux"
    url = DEPOT_DOWNLOADER_RELEASE_URLS.get(platform_key)
    expected_sha = DEPOT_DOWNLOADER_SHA256.get(platform_key)

    if not url or not expected_sha:
        raise RuntimeError(f"Official DepotDownloader binary is not available for platform {sys.platform}")

    prompt_msg = (
        f"PortPicker requires official SteamRE DepotDownloader v{DEPOT_DOWNLOADER_VERSION} "
        f"to download game files from your Steam account.\n\n"
        f"Source: {url}\n"
        f"SHA-256: {expected_sha}\n\n"
        "Do you want to download and install it now?"
    )

    if confirm_callback and not confirm_callback(prompt_msg):
        raise RuntimeError("DepotDownloader installation cancelled by user.")

    if log:
        log(f"  📥 Downloading official SteamRE DepotDownloader {DEPOT_DOWNLOADER_VERSION} from GitHub…")

    target_dir = steam_auth.APP_DIR / "tools" / f"DepotDownloader_{DEPOT_DOWNLOADER_VERSION}"
    target_dir.mkdir(parents=True, exist_ok=True)

    fd, tmp_zip = tempfile.mkstemp(prefix="depotdownloader_", suffix=".zip")
    os.close(fd)
    tmp_zip_p = Path(tmp_zip)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PortPicker"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(tmp_zip_p, "wb") as out_f:
            shutil.copyfileobj(resp, out_f)

        if log:
            log("  🔍 Verifying SHA-256 checksum…")

        if not verify_file_sha256(tmp_zip_p, expected_sha):
            raise ValueError(
                f"DepotDownloader download verification failed! SHA-256 does not match pinned hash: {expected_sha}"
            )

        if log:
            log("  📦 Extracting DepotDownloader…")

        with zipfile.ZipFile(tmp_zip_p, "r") as zf:
            zf.extractall(target_dir)

        candidate_name = "DepotDownloader.exe" if sys.platform == "win32" else "DepotDownloader"
        exe_path = target_dir / candidate_name
        if not exe_path.exists():
            raise FileNotFoundError(f"Extracted archive did not contain {candidate_name}")

        try:
            exe_path.chmod(0o755)
        except OSError:
            pass

        if log:
            log(f"  ✔ DepotDownloader installed to {exe_path}")
        return exe_path
    finally:
        if tmp_zip_p.exists():
            try:
                tmp_zip_p.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------
# Recipes for Ports needing Steam files (Trimmed to 1–2 ports; untested, needs hardware test)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SteamDepotRecipe:
    appid: str
    dest: str                            # relative to ports dir, e.g. "undertale/assets"
    depot: str = ""                      # target depot id
    branch: str = "public"               # branch, e.g. "public"
    filelist: tuple[str, ...] = ("*",)   # patterns passed to -filelist
    os: str = "windows"                  # target OS depot ("windows" or "linux")
    post_process: str = ""               # optional action
    note: str = ""


# Untested; needs hardware test (RG35XX SP/H Anbernic H700 with muOS)
STEAM_DEPOT_RECIPES: dict[str, SteamDepotRecipe] = {
    # 1. Balatro: requires Balatro.exe (Windows depot 2379781)
    # PortMaster: https://portmaster.games/detail.html?name=balatro
    # Placed in ports/balatro/
    "balatro": SteamDepotRecipe(
        appid="2379780",
        dest="balatro",
        depot="2379781",
        filelist=("Balatro.exe",),
        os="windows",
        note="Balatro executable (https://portmaster.games/detail.html?name=balatro)",
    ),
    # 2. Undertale: requires data.win and audio assets (*.ogg) in assets/
    # PortMaster: https://portmaster.games/detail.html?name=undertale
    # Undertale launcher script Undertale.sh moves data.win -> game.droid and zips with *.ogg
    "undertale": SteamDepotRecipe(
        appid="391540",
        dest="undertale/assets",
        depot="391541",
        filelist=("data.win", "*.ogg"),
        os="windows",
        note="Undertale assets (data.win, *.ogg) (https://portmaster.games/detail.html?name=undertale)",
    ),
}


# --------------------------------------------------------------------------
# Filelist & Argument Builder
# --------------------------------------------------------------------------

def write_filelist(recipe: SteamDepotRecipe, out_path: Path) -> Path | None:
    """Write selective -filelist for DepotDownloader.
    
    Cited to DepotDownloader README (-filelist <filename>).
    Supports regex prefix for pattern matching.
    """
    if recipe.filelist == ("*",):
        return None
    lines: list[str] = []
    for pattern in recipe.filelist:
        if any(ch in pattern for ch in "*?"):
            regex_str = pattern.replace(".", r"\.").replace("*", ".*").replace("?", ".")
            lines.append(f"regex:{regex_str}")
        else:
            lines.append(pattern)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def build_depotdownloader_args(
    recipe: SteamDepotRecipe,
    dest_dir: Path,
    filelist_path: Path | None = None,
    account_name: str | None = None,
    remember_password: bool = True,
) -> list[str]:
    """Construct command-line arguments for DepotDownloader.
    
    Citations from official DepotDownloader README:
      -app <id>: Specifies the Steam AppID to download.
      -depot <id>: Specifies the depot ID to download.
      -dir <path>: The directory to download files to.
      -filelist <path>: Selective file download filter.
      -os <os>: Target operating system depot.
      -username <user>: Steam account username.
      -remember-password: Uses persistent session token cached in IsolatedStorage.
    """
    args = ["-app", str(recipe.appid)]
    if recipe.depot:
        args += ["-depot", str(recipe.depot)]
    if recipe.branch and recipe.branch != "public":
        args += ["-beta", str(recipe.branch)]
    if recipe.os:
        args += ["-os", recipe.os]
    args += ["-dir", str(dest_dir)]
    if filelist_path:
        args += ["-filelist", str(filelist_path)]
    if account_name:
        args += ["-username", account_name]
    if remember_password:
        args += ["-remember-password"]
    return args


# --------------------------------------------------------------------------
# Process Runner with Watchdog and Non-Blocking Reading
# --------------------------------------------------------------------------

def run_depot_downloader_process(
    cmd: list[str],
    log: Log | None = None,
    progress: Progress | None = None,
    cancel: Callable[[], bool] | None = None,
    inactivity_timeout: float = 90.0,
) -> list[str]:
    """Execute DepotDownloader subprocess safely:
    
    - Always passes stdin=subprocess.DEVNULL to prevent blocking interactive prompts.
    - Reads stdout on a dedicated background thread with queue.Queue.
    - Responsive cancellation: checks cancel() every 0.2 seconds.
    - Inactivity watchdog: terminates if no output for inactivity_timeout seconds.
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    out_q: queue.Queue[str | None] = queue.Queue()

    def _reader():
        try:
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    out_q.put(line)
        finally:
            out_q.put(None)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    captured_lines: list[str] = []
    last_output_time = time.time()

    while True:
        if cancel and cancel():
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise InterruptedError("cancelled")

        # Inactivity watchdog
        if time.time() - last_output_time > inactivity_timeout:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise TimeoutError(f"DepotDownloader produced no output for {inactivity_timeout}s; terminated.")

        try:
            line = out_q.get(timeout=0.2)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue

        if line is None:
            # End of stream
            break

        last_output_time = time.time()
        cleaned = line.strip()
        captured_lines.append(cleaned)

        if log and ("%" in cleaned or "Download" in cleaned or "Depot" in cleaned):
            log(f"    {cleaned}")

        # Attempt to parse progress (e.g. 45% or 45/100)
        if progress:
            m = re.search(r"(\d+)%", cleaned)
            if m:
                progress(int(m.group(1)), 100)

    proc.wait()
    ret = proc.returncode
    if ret != 0:
        err_snippet = "\n".join(captured_lines[-5:]) if captured_lines else "Unknown error"
        raise RuntimeError(f"DepotDownloader failed with exit code {ret}:\n{err_snippet}")

    return captured_lines


# --------------------------------------------------------------------------
# QR Login Flow via DepotDownloader (-qr -remember-password)
# --------------------------------------------------------------------------

def run_depot_downloader_qr_login(
    on_qr_lines: Callable[[list[str]], None],
    on_approved: Callable[[str], None],
    cancel: Callable[[], bool] | None = None,
    fake_mode: bool | None = None,
    log: Log | None = None,
) -> None:
    """Run DepotDownloader in QR authentication mode.
    
    Citations from DepotDownloader README:
      -app 480 -depot 480 -manifest-only -qr -remember-password
    Uses App 480 (Spacewar) to perform official QR authentication and persist the
    token in IsolatedStorage (account.config) without downloading any game files.
    """
    is_fake = fake_mode if fake_mode is not None else bool(os.environ.get("PORTPICKER_FAKE_DEPOT_DOWNLOADER"))

    if is_fake:
        # Mock QR rendering & approval simulation with valid half-block QR lines
        mock_qr = [
            "█▀▀▀▀▀▀▀█▀█▀██▀█▀██▀▀▀▀▀▀▀█",
            "█ █▀▀▀█ ██▀▀██▄ █▄█ █▀▀▀█ █",
            "█ █   █ █ ▄ ▀▄▄▀▀▄█ █   █ █",
            "█ ▀▀▀▀▀ █ █▀▄▀█▀█ █ ▀▀▀▀▀ █",
            "█▀███▀█▀▀ ▄▀▄▄█▄▄ ▀▀▀▀▀██▀█",
            "█▄▀▄█▀▄▀██▀▄▄ █  ▄█▀   ▀▄██",
            "██ ▀▄▀▄▀▀  ▄  █▄▀█  ██▀  ▀█",
            "█▄██▀▀▀▀ ▀ ▀██▀  █▄█▀ ▀▀▄██",
            "█▀▀▄▀▄▀▀▀▄██▀▄█▄ ▀ ▀  ▀ ▄██",
            "█▀▀▀▀▀▀▀█ ▄ ▀  ▄█ █▀█ █████",
            "█ █▀▀▀█ █▀▄ ▄▀█▄█ ▀▀▀   ▀ █",
            "█ █   █ ██ ███▀▄ ▀  ▄█▀▄ ▄█",
            "█ ▀▀▀▀▀ █▀██▀ ▀  █▀▀▄▄▄  ▀█",
            "▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀",
        ]
        on_qr_lines(mock_qr)
        if log:
            log("  🎮 [Mock Steam] Displayed QR code; waiting for approval…")

        start = time.time()
        while time.time() - start < 1.0:
            if cancel and cancel():
                raise InterruptedError("cancelled")
            time.sleep(0.1)

        user = "MockUser"
        steam_auth.save_steam_credentials(user)
        on_approved(user)
        if log:
            log(f"  ✔ [Mock Steam] Approved as {user}")
        return

    exe = find_depot_downloader()
    if not exe:
        raise FileNotFoundError(
            "DepotDownloader is not installed. Please install official DepotDownloader release."
        )

    cmd = [
        str(exe),
        "-app", "480",
        "-depot", "480",
        "-manifest-only",
        "-qr",
        "-remember-password",
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    out_q: queue.Queue[str | None] = queue.Queue()

    def _reader():
        try:
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    out_q.put(line)
        finally:
            out_q.put(None)

    threading.Thread(target=_reader, daemon=True).start()

    qr_lines: list[str] = []
    in_qr_block = False

    while True:
        if cancel and cancel():
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise InterruptedError("cancelled")

        try:
            line = out_q.get(timeout=0.2)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue

        if line is None:
            break

        line_str = line.rstrip()

        if "Use the Steam Mobile App to sign in with this QR code:" in line_str:
            in_qr_block = True
            qr_lines = []
            continue
        elif "The QR code has changed:" in line_str:
            in_qr_block = True
            qr_lines = []
            continue

        if in_qr_block:
            # QR ascii art consists of block characters or spaces
            if any(ch in line_str for ch in "█▀▄▌▐░▒▓#"):
                qr_lines.append(line_str)
                on_qr_lines(list(qr_lines))
            elif qr_lines and line_str == "":
                in_qr_block = False

        # Match login success message
        # Format: "Success! Next time you can login with -username <user> -remember-password instead of -qr."
        m = re.search(r"login with -username\s+(\S+)\s+-remember-password", line_str)
        if m:
            account_name = m.group(1)
            steam_auth.save_steam_credentials(account_name)
            on_approved(account_name)
            proc.terminate()
            break


# --------------------------------------------------------------------------
# Mock DepotDownloader Runner for Automated Tests
# --------------------------------------------------------------------------

def fake_depot_download(
    recipe: SteamDepotRecipe,
    dest_dir: Path,
    log: Log | None = None,
    progress: Progress | None = None,
    cancel: Callable[[], bool] | None = None,
) -> list[Path]:
    """Emulate selective DepotDownloader execution strictly for unit & integration tests."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if log:
        log(f"  🎮 [Mock Steam] Connecting to Steam for App {recipe.appid} ({recipe.os})…")
        log(f"  🎮 [Mock Steam] Downloading selective files for {recipe.dest}…")

    patterns = recipe.filelist if recipe.filelist != ("*",) else ("game.data",)
    created: list[Path] = []
    total = len(patterns)

    for i, pat in enumerate(patterns):
        if cancel and cancel():
            raise InterruptedError("cancelled")
        clean_pat = pat.rstrip("/").replace("*", "file")
        if "/" in clean_pat:
            sub = dest_dir / clean_pat
            sub.parent.mkdir(parents=True, exist_ok=True)
            if pat.endswith("/"):
                sub.mkdir(parents=True, exist_ok=True)
                sample_file = sub / "asset.bin"
                sample_file.write_bytes(b"MOCK_STEAM_SUBDIR_CONTENT\n")
                created.append(sample_file)
            else:
                sub.write_bytes(b"MOCK_STEAM_FILE_CONTENT\n")
                created.append(sub)
        else:
            file_p = dest_dir / clean_pat
            file_p.write_bytes(b"MOCK_STEAM_FILE_CONTENT_" + clean_pat.encode() * 8)
            created.append(file_p)

        if progress:
            progress(i + 1, total)

    if log:
        log(f"  ✔ [Mock Steam] Finished downloading {len(created)} selective files.")
    return created


# --------------------------------------------------------------------------
# Download Entry Point (Strict Separation Between Fake and Real Modes)
# --------------------------------------------------------------------------

def download_steam_files(
    recipe: SteamDepotRecipe,
    dest_dir: Path,
    log: Log | None = None,
    progress: Progress | None = None,
    cancel: Callable[[], bool] | None = None,
    fake_mode: bool | None = None,
) -> Path:
    """Download selective Steam files according to recipe into dest_dir.
    
    Strictly enforces production safety:
    - If fake_mode is True or PORTPICKER_FAKE_DEPOT_DOWNLOADER is set, uses mock downloader.
    - In real mode (fake_mode is False/unset): MUST use real DepotDownloader.
      If DepotDownloader executable is missing, raises FileNotFoundError.
      NEVER silently falls back to mock files!
    """
    if cancel and cancel():
        raise InterruptedError("cancelled")

    dest_dir.mkdir(parents=True, exist_ok=True)
    is_fake = fake_mode if fake_mode is not None else bool(os.environ.get("PORTPICKER_FAKE_DEPOT_DOWNLOADER"))

    if is_fake:
        fake_depot_download(recipe, dest_dir, log=log, progress=progress, cancel=cancel)
        return dest_dir

    # Real DepotDownloader execution
    exe = find_depot_downloader()
    if not exe:
        raise FileNotFoundError(
            "DepotDownloader executable was not found. Please install the official SteamRE DepotDownloader release."
        )

    creds = steam_auth.get_steam_credentials()
    account_name = creds.get("account_name") if creds else None

    temp_fl: Path | None = None
    try:
        if recipe.filelist != ("*",):
            fd, fl_name = tempfile.mkstemp(prefix="pp_filelist_", suffix=".txt")
            os.close(fd)
            temp_fl = Path(fl_name)
            write_filelist(recipe, temp_fl)

        cmd = [str(exe)] + build_depotdownloader_args(
            recipe=recipe,
            dest_dir=dest_dir,
            filelist_path=temp_fl,
            account_name=account_name,
            remember_password=True,
        )

        if log:
            log(f"  🎮 Running DepotDownloader for App {recipe.appid} (selective files)…")

        run_depot_downloader_process(
            cmd=cmd,
            log=log,
            progress=progress,
            cancel=cancel,
        )

        if log:
            log("  ✔ DepotDownloader completed successfully.")
        return dest_dir

    finally:
        if temp_fl and temp_fl.exists():
            try:
                temp_fl.unlink()
            except OSError:
                pass
