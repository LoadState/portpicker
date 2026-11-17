"""Steam QR code authentication and secure credential persistence for PortPicker.

Implements the official Steam authentication flow for T-023:
1. DepotDownloader QR flow (-qr -remember-password) for SteamKit2 session persistence.
   One login path only: DepotDownloader -qr.
2. Renders DepotDownloader terminal block QR codes into crisp, scannable QPixmaps.
3. Windows DPAPI (CryptProtectData / CryptUnprotectData) for credential storage.
   Refuses to save plaintext on non-Windows.
   Stores only the account name (which -username needs) — never stores passwords or tokens.
4. Complete logout functionality that purges DPAPI storage and DepotDownloader account.config.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "PortPicker"
STEAM_AUTH_FILE = APP_DIR / "steam_auth.dat"

# Test mode hook enabled only during non-Windows unit test execution
_TEST_MODE = False


# --------------------------------------------------------------------------
# DPAPI Secure Token Storage (Windows Only)
# --------------------------------------------------------------------------

def _win_dpapi_protect(data: bytes) -> bytes:
    """Encrypt data using Windows DPAPI (CryptProtectData).
    
    Refuses plaintext fallback on non-Windows platforms.
    """
    if sys.platform != "win32":
        if _TEST_MODE or os.environ.get("PORTPICKER_TEST_MODE"):
            return b"TEST_ENC:" + base64.b64encode(data)
        raise NotImplementedError("DPAPI credential storage is only supported on Windows")

    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB()
    in_blob.cbData = len(data)
    in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_byte))

    out_blob = DATA_BLOB()
    res = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "PortPicker Steam Auth",
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not res:
        raise OSError("CryptProtectData failed")

    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return raw
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _win_dpapi_unprotect(data: bytes) -> bytes:
    """Decrypt data using Windows DPAPI (CryptUnprotectData)."""
    if sys.platform != "win32":
        if (_TEST_MODE or os.environ.get("PORTPICKER_TEST_MODE")) and data.startswith(b"TEST_ENC:"):
            return base64.b64decode(data[9:])
        raise NotImplementedError("DPAPI credential storage is only supported on Windows")

    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB()
    in_blob.cbData = len(data)
    in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_byte))

    out_blob = DATA_BLOB()
    res = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not res:
        raise OSError("CryptUnprotectData failed")

    try:
        raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return raw
    finally:
        kernel32.LocalFree(out_blob.pbData)


def save_steam_credentials(account_name: str) -> None:
    """Persist Steam account name securely using DPAPI. Never stores password or tokens.
    
    Refuses on non-Windows unless test mode is active.
    """
    APP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "account_name": account_name,
        "updated_at": int(time.time()),
    }
    raw = json.dumps(payload).encode("utf-8")
    encrypted = _win_dpapi_protect(raw)
    STEAM_AUTH_FILE.write_bytes(encrypted)


def get_steam_credentials() -> dict | None:
    """Retrieve and decrypt saved Steam credentials, or None if not authenticated."""
    if not STEAM_AUTH_FILE.exists():
        return None
    try:
        encrypted = STEAM_AUTH_FILE.read_bytes()
        raw = _win_dpapi_unprotect(encrypted)
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("account_name"):
            return data
    except Exception:
        pass
    return None


def clear_depot_downloader_sessions() -> int:
    """Purge DepotDownloader's cached account.config sessions in IsolatedStorage."""
    cleared = 0
    search_dirs: list[Path] = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        search_dirs.append(Path(local_appdata) / "IsolatedStorage")
    search_dirs.append(Path.home() / ".local" / "share" / "IsolatedStorage")
    search_dirs.append(Path.home() / ".config" / "IsolatedStorage")

    for base in search_dirs:
        if base.is_dir():
            try:
                for p in base.glob("**/account.config"):
                    try:
                        p.unlink()
                        cleared += 1
                    except OSError:
                        pass
            except Exception:
                pass
    return cleared


def forget_local_login() -> None:
    """Forget PortPicker's saved account name so the UI asks for a QR login again."""
    try:
        STEAM_AUTH_FILE.unlink()
    except OSError:
        pass


def logout_steam() -> bool:
    """Delete saved Steam credentials and clear DepotDownloader remembered session."""
    deleted_local = False
    if STEAM_AUTH_FILE.exists():
        try:
            STEAM_AUTH_FILE.unlink()
            deleted_local = True
        except OSError:
            pass

    cleared_dd = clear_depot_downloader_sessions()
    return deleted_local or (cleared_dd > 0)


def is_steam_authenticated() -> bool:
    """Check if valid Steam credentials are saved on this PC."""
    return get_steam_credentials() is not None


# --------------------------------------------------------------------------
# QR Block Text to Module Grid & QPixmap Conversion
# --------------------------------------------------------------------------

QR_QUIET_ZONE = 4  # light modules around the code; phone scanners need it


def qr_fullblock_lines_to_modules(lines: list[str]) -> list[list[bool]]:
    """Parse DepotDownloader's QR text (QRCoder AsciiQRCode.GetLineByLineGraphic(1)): one text line per module
    row, two characters per module, dark = "██", light = two spaces.

    Any non-space character counts as dark, so it still works when the console code page turns "█" into "?"
    (e.g. Thai Windows, cp874). Fully light border rows/columns are trimmed and a fresh quiet zone is added.
    """
    rows = [l.rstrip("\r\n") for l in lines]
    width = max((len(r) for r in rows), default=0)
    if width == 0:
        return []
    cols = (width + 1) // 2
    grid = []
    for r in rows:
        r = r.ljust(cols * 2)
        grid.append([not r[2 * c:2 * c + 2].isspace() for c in range(cols)])
    while grid and not any(grid[0]):
        grid.pop(0)
    while grid and not any(grid[-1]):
        grid.pop()
    if not grid:
        return []
    left = min(row.index(True) for row in grid if any(row))
    right = max(len(row) - 1 - row[::-1].index(True) for row in grid if any(row))
    core = [row[left:right + 1] for row in grid]
    n = len(core[0])
    q = QR_QUIET_ZONE
    blank = [False] * (n + 2 * q)
    return [blank[:] for _ in range(q)] + [[False] * q + row + [False] * q for row in core] + [blank[:] for _ in range(q)]


def qr_ascii_to_modules(lines: list[str]) -> list[list[bool]]:
    if not any(ch in l for l in lines for ch in "▀▄"):
        return qr_fullblock_lines_to_modules(lines)
    """Convert ASCII/Unicode block-character QR lines into a 2D boolean module grid.

    Each character cell represents up to two vertical modules using half-blocks:
      - ' ' or '\xa0' (space): light/light
      - '█' (full block): dark/dark
      - '▀' (upper half block): dark top, light bottom
      - '▄' (lower half block): light top, dark bottom

    Automatically detects terminal inversion:
      - When inverted (e.g. black background terminal where quiet zone is drawn with '█'):
        '█' represents light (quiet zone) and ' ' represents dark.
      - When normal:
        ' ' represents light and '█' represents dark.

    Returns:
      A 2D list of bools [row][col], where True represents a dark module (black)
      and False represents a light module (white).
    """
    cleaned = [l.rstrip("\r\n") for l in lines]
    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    if not cleaned:
        return []

    width = max(len(l) for l in cleaned)
    padded = [l.ljust(width) for l in cleaned]

    # Detect inverted rendering (quiet zone = '█', or finder pattern = '▄▄▄▄▄')
    first_line_blocks = sum(1 for ch in padded[0] if ch == "█")
    has_inverted_finder = any("▄▄▄▄▄" in l for l in padded[:5])
    is_inverted = (first_line_blocks > width / 2) or has_inverted_finder

    modules: list[list[bool]] = []
    for line in padded:
        row_top: list[bool] = []
        row_bot: list[bool] = []
        for ch in line:
            if not is_inverted:
                # Normal mode: True = dark/black, False = light/white
                if ch in (" ", "\xa0"):
                    top, bot = False, False
                elif ch == "█":
                    top, bot = True, True
                elif ch == "▀":
                    top, bot = True, False
                elif ch == "▄":
                    top, bot = False, True
                else:
                    top, bot = False, False
            else:
                # Inverted mode: '█' is white/light (False), ' ' is black/dark (True)
                if ch == "█":
                    top, bot = False, False
                elif ch in (" ", "\xa0"):
                    top, bot = True, True
                elif ch == "▀":
                    top, bot = False, True
                elif ch == "▄":
                    top, bot = True, False
                else:
                    top, bot = False, False
            row_top.append(top)
            row_bot.append(bot)
        modules.append(row_top)
        modules.append(row_bot)

    return modules


def qr_modules_to_pixmap(modules: list[list[bool]], target_size: int = 240):
    """Render a 2D boolean module grid as a crisp QPixmap."""
    if not modules or not modules[0]:
        return None
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
        from PySide6.QtWidgets import QApplication

        # Ensure QApplication instance exists for QPixmap operations
        _ = QApplication.instance() or QApplication([])

        h = len(modules)
        w = len(modules[0])
        cell_size = max(1, target_size // max(h, w))
        img_w = w * cell_size
        img_h = h * cell_size

        img = QImage(img_w, img_h, QImage.Format_RGB32)
        img.fill(QColor("white"))

        painter = QPainter(img)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("black"))
        for r in range(h):
            for c in range(w):
                if modules[r][c]:
                    painter.drawRect(c * cell_size, r * cell_size, cell_size, cell_size)
        painter.end()

        pix = QPixmap.fromImage(img)
        if target_size > 0 and (img_w != target_size or img_h != target_size):
            pix = pix.scaled(
                target_size,
                target_size,
                Qt.KeepAspectRatio,
                Qt.FastTransformation,  # Crisp nearest-neighbor scaling for barcode/QR readability
            )
        return pix
    except Exception:
        return None


def qr_ascii_to_pixmap(lines: list[str], target_size: int = 240):
    """Convert DepotDownloader block-character QR lines directly into a QPixmap."""
    modules = qr_ascii_to_modules(lines)
    if not modules:
        return None
    return qr_modules_to_pixmap(modules, target_size=target_size)
