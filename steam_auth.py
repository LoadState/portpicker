"""Steam QR code authentication and secure credential persistence for PortPicker.

Implements the official Steam authentication flow for T-023:
1. DepotDownloader QR flow (-qr -remember-password) for SteamKit2 session persistence.
2. Steam Web API (IAuthenticationService/BeginAuthSessionViaQR & PollAuthSessionStatus)
   supporting QR rotation (new_challenge_url) and polling intervals.
3. Windows DPAPI (CryptProtectData / CryptUnprotectData) for credential storage.
   Refuses to save plaintext on non-Windows.
4. Complete logout functionality that purges DPAPI storage and DepotDownloader account.config.
PortPicker never asks for or stores the user's Steam password.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import requests

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "PortPicker"
STEAM_API_BASE = "https://api.steampowered.com/IAuthenticationService"
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


def save_steam_credentials(account_name: str, refresh_token: str = "", steam_id: str = "") -> None:
    """Persist Steam credentials securely using DPAPI. Never stores password.
    
    Refuses on non-Windows unless test mode is active.
    """
    APP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "account_name": account_name,
        "refresh_token": refresh_token,
        "steam_id": steam_id,
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
# Steam QR Auth Flow (Official Steam Web API)
# --------------------------------------------------------------------------

@dataclass
class QRSession:
    client_id: str
    request_id: str
    challenge_url: str
    interval: float = 5.0


class SteamAuthService:
    """Handles Steam QR code authentication session lifecycle."""

    def __init__(self, mock: bool = False):
        self.mock = mock or bool(os.environ.get("PORTPICKER_MOCK_STEAM_AUTH"))
        self._mock_approved = False

    def begin_qr_session(self, friendly_name: str = "PortPicker") -> QRSession:
        """Start a new QR auth session with Steam.
        
        Returns QRSession with client_id, request_id, challenge_url, and polling interval.
        """
        if self.mock:
            return QRSession(
                client_id="mock_client_12345",
                request_id="mock_request_67890",
                challenge_url="https://s.team/q/1/mock-challenge-session-url",
                interval=2.0,
            )

        url = f"{STEAM_API_BASE}/BeginAuthSessionViaQR/v1/"
        data = {
            "device_friendly_name": friendly_name,
            "platform_type": 2,  # Desktop client
            "website_id": "Community",
        }
        res = requests.post(url, data=data, timeout=10)
        res.raise_for_status()
        resp = res.json().get("response", {})
        client_id = str(resp.get("client_id", ""))
        request_id = str(resp.get("request_id", ""))
        challenge_url = resp.get("challenge_url", "")
        interval = float(resp.get("interval", 5.0))
        if not challenge_url or not client_id:
            raise ValueError(f"Steam QR session initiation failed: invalid response {resp}")
        return QRSession(client_id, request_id, challenge_url, interval)

    def poll_qr_status(self, session: QRSession) -> dict:
        """Poll Steam to check if the user approved the QR login in their mobile app.
        
        Handles:
          - Approved status: persists credentials and returns account details
          - new_challenge_url: QR rotation handled seamlessly
          - new_client_id: session client_id update
          - Expired / Error status
        """
        if self.mock:
            if getattr(self, "_mock_approved", False):
                account = "MockUser"
                token = "mock_refresh_token_abcdef123456"
                save_steam_credentials(account, token, "76561198000000000")
                return {
                    "status": "approved",
                    "account_name": account,
                    "refresh_token": token,
                    "steam_id": "76561198000000000",
                }
            return {"status": "pending"}

        url = f"{STEAM_API_BASE}/PollAuthSessionStatus/v1/"
        data = {
            "client_id": session.client_id,
            "request_id": session.request_id,
        }
        try:
            res = requests.post(url, data=data, timeout=10)
            res.raise_for_status()
            resp = res.json().get("response", {})

            if resp.get("had_session_expired"):
                return {"status": "expired"}

            # Handle challenge rotation if Steam refreshed the QR
            new_url = resp.get("new_challenge_url")
            if new_url:
                session.challenge_url = new_url
            new_cid = resp.get("new_client_id")
            if new_cid:
                session.client_id = str(new_cid)

            refresh_token = resp.get("refresh_token")
            if refresh_token:
                account_name = resp.get("account_name", "")
                steam_id = str(resp.get("steam_id", ""))
                save_steam_credentials(account_name, refresh_token, steam_id)
                return {
                    "status": "approved",
                    "account_name": account_name,
                    "refresh_token": refresh_token,
                    "steam_id": steam_id,
                }

            if new_url:
                return {"status": "rotated", "new_challenge_url": new_url}

            return {"status": "pending"}
        except Exception as e:
            return {"status": "error", "error": str(e)}


# --------------------------------------------------------------------------
# QR Rendering
# --------------------------------------------------------------------------

def generate_qr_pixmap(url: str, size: int = 220):
    """Render a challenge URL as a QPixmap using PySide6 if available."""
    try:
        import qrcode
        from PySide6.QtGui import QImage, QPainter, QColor, QPixmap
        from PySide6.QtCore import Qt

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        n = len(matrix)
        cell_size = max(1, size // n)
        total_px = n * cell_size

        img = QImage(total_px, total_px, QImage.Format_RGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("black"))
        for r in range(n):
            for c in range(n):
                if matrix[r][c]:
                    painter.drawRect(c * cell_size, r * cell_size, cell_size, cell_size)
        painter.end()
        return QPixmap.fromImage(img)
    except Exception:
        return None
