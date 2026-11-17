"""PortPicker wireless transfer module: SSH/SFTP connection, probe, atomic transfer, and hooks.

Supports muOS on Anbernic H700 / supported handhelds via Secure File Transfer (SFTP).
Default port: 2022, username/password: muos / muos (official doc: muos.dev/web/sftp).
Detection: SFTP virtual root with /Primary Storage, /Internal, /Catalogue.
Path mapping:
  /mnt/mmc    → /Primary Storage (SD1: system, runtimes, PortMaster libs)
  /mnt/sdcard → /Secondary Storage (SD2: ROMS/ports)
  catalogue   → /Catalogue (box art: /Catalogue/External - Ports/box)
Forbidden paths: /Internal, /Boot, /tmp, /root.
Pure SFTP only: zero shell exec commands (no uname, md5sum, df, chmod, rm, sync shell calls).
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import posixpath
import shutil
import socket
import time
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import paramiko

from i18n import t as _tr
import core
import steam_downloader
from profiles import CFW, Device, DEVICES, MUOS

# --------------------------------------------------------------------------
# Types and Constants
# --------------------------------------------------------------------------

Log = Callable[[str], None]
Progress = Callable[[int, int], None]

ONE_GB = 1024 * 1024 * 1024
SIZE_SUGGEST_CARD_READER = int(1.5 * ONE_GB)  # 1.5 GB
DEFAULT_WIRELESS_PORT = 2022

KNOWN_MDNS_HOSTS = [
    ("muos.local", "muos"),
]

# Default credentials for muOS: list of (user, password, port)
# Official muOS Secure File Transfer runs SFTP on port 2022 with muos/muos.
CFW_DEFAULT_CREDENTIALS: dict[str, list[tuple[str, str, int]]] = {
    "muos": [
        ("muos", "muos", 2022),
        ("root", "root", 22),
    ],
}

GENERIC_DEFAULT_CREDENTIALS = [
    ("muos", "muos", 2022),
    ("root", "root", 22),
]

FORBIDDEN_SFTP_PREFIXES = (
    "/Internal",
    "/Boot",
    "/tmp",
    "/root",
    "/proc",
    "/sys",
    "/dev",
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
)


# --------------------------------------------------------------------------
# Path Mapping and Safety Validation
# --------------------------------------------------------------------------

def translate_handheld_path_to_sftp(path: str) -> str:
    """Translate standard handheld mount paths (/mnt/mmc, /mnt/sdcard, catalogue) to muOS SFTP virtual tree paths."""
    p = path.replace("\\", "/")
    if "/MUOS/info/catalogue" in p:
        idx = p.find("/MUOS/info/catalogue")
        suffix = p[idx + len("/MUOS/info/catalogue"):]
        return f"/Catalogue{suffix}"
    if p.lower().startswith("/catalogue"):
        return p
    if p.startswith("/mnt/mmc"):
        return "/Primary Storage" + p[len("/mnt/mmc"):]
    if p.startswith("/mnt/sdcard"):
        return "/Secondary Storage" + p[len("/mnt/sdcard"):]
    return p


def validate_sftp_write_path(path: str, layout: RemoteLayout) -> None:
    """Ensure path is within permitted storage directories and never in /Internal, /Boot, or rootfs."""
    p = posixpath.normpath(path.replace("\\", "/"))
    # Check forbidden prefixes
    for forbidden in FORBIDDEN_SFTP_PREFIXES:
        if p == forbidden or p.startswith(forbidden + "/"):
            raise ValueError(f"Writing to protected or restricted path is forbidden: {p}")

    allowed_roots = [
        layout.ports_dir.rstrip("/"),
        layout.scripts_dir.rstrip("/"),
        layout.libs_dir.rstrip("/"),
    ]
    if layout.media_dir:
        allowed_roots.append(layout.media_dir.rstrip("/"))

    is_safe = False
    for root in allowed_roots:
        if p == root or p.startswith(root + "/"):
            is_safe = True
            break
    if not is_safe:
        raise ValueError(f"Path is outside allowed target roots: {p}")


# --------------------------------------------------------------------------
# DPAPI / Secure Credential Storage & Host Key Management
# --------------------------------------------------------------------------

AUTH_FILE = core.APP_DIR / "wireless_auth.dat"
KNOWN_HOSTS_FILE = core.APP_DIR / "known_hosts"


def format_fingerprint(key: paramiko.PKey) -> str:
    """Return standard OpenSSH SHA256 fingerprint: SHA256:<base64>."""
    sha256 = hashlib.sha256(key.asbytes()).digest()
    b64 = base64.b64encode(sha256).decode("ascii").rstrip("=")
    return f"SHA256:{b64}"


class TrustOnFirstUsePolicy(paramiko.MissingHostKeyPolicy):
    """Host key policy that trusts the host key on first connect and saves it to known_hosts.

    If a host key changes later, paramiko raises BadHostKeyException automatically
    because the host was already present in the client host keys with a different key.
    """

    def __init__(self, known_hosts_file: Path, on_new_key: Callable[[str, paramiko.PKey], None] | None = None):
        self.known_hosts_file = known_hosts_file
        self.on_new_key = on_new_key

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        self.known_hosts_file.parent.mkdir(parents=True, exist_ok=True)
        client.get_host_keys().add(hostname, key.get_name(), key)
        try:
            client.save_host_keys(str(self.known_hosts_file))
        except Exception:
            pass
        if self.on_new_key:
            self.on_new_key(hostname, key)


def update_host_key(host: str, port: int, key: paramiko.PKey) -> None:
    """Update or replace the saved host key for host:port in known_hosts (for TOFU re-trusting)."""
    try:
        KNOWN_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        hk = paramiko.HostKeys()
        if KNOWN_HOSTS_FILE.exists():
            try:
                hk.load(str(KNOWN_HOSTS_FILE))
            except Exception:
                pass
        entry_name = host if port == 22 else f"[{host}]:{port}"
        h_lower = host.lower()
        to_del = []
        for k in list(hk.keys()):
            k_norm = k.lower()
            if k_norm == h_lower or k_norm == entry_name.lower() or k_norm.startswith(f"[{h_lower}]:"):
                to_del.append(k)
        for k in to_del:
            del hk[k]
        hk.add(entry_name, key.get_name(), key)
        hk.save(str(KNOWN_HOSTS_FILE))
    except Exception:
        pass


def _win_dpapi_protect(data: bytes) -> bytes:
    """Encrypt data using Windows DPAPI (CryptProtectData)."""
    if sys.platform != "win32":
        return b"ENC:" + base64.b64encode(data)

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
        "PortPicker Wireless Auth",
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
        if data.startswith(b"ENC:"):
            return base64.b64decode(data[4:])
        return data

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


def save_saved_credential(host: str, username: str, password: str) -> None:
    """Save credentials for host encrypted via DPAPI."""
    try:
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        records = {}
        if AUTH_FILE.exists():
            try:
                dec = _win_dpapi_unprotect(AUTH_FILE.read_bytes())
                records = json.loads(dec.decode("utf-8"))
            except Exception:
                records = {}
        records[host.lower()] = {"u": username, "p": password}
        enc = _win_dpapi_protect(json.dumps(records).encode("utf-8"))
        AUTH_FILE.write_bytes(enc)
        if sys.platform != "win32":
            AUTH_FILE.chmod(0o600)
    except Exception:
        pass


def get_saved_credential(host: str) -> tuple[str, str] | None:
    """Retrieve saved credentials (username, password) for host, if any."""
    if not AUTH_FILE.exists():
        return None
    try:
        dec = _win_dpapi_unprotect(AUTH_FILE.read_bytes())
        records = json.loads(dec.decode("utf-8"))
        entry = records.get(host.lower())
        if entry and isinstance(entry, dict):
            return entry.get("u", ""), entry.get("p", "")
    except Exception:
        pass
    return None


def delete_saved_credential(host: str) -> bool:
    """Delete saved credential for host. Returns True if deleted."""
    if not AUTH_FILE.exists():
        return False
    try:
        dec = _win_dpapi_unprotect(AUTH_FILE.read_bytes())
        records = json.loads(dec.decode("utf-8"))
        h = host.lower()
        if h in records:
            del records[h]
            enc = _win_dpapi_protect(json.dumps(records).encode("utf-8"))
            AUTH_FILE.write_bytes(enc)
            if sys.platform != "win32":
                AUTH_FILE.chmod(0o600)
            return True
    except Exception:
        pass
    return False


def forget_host(host: str, port: int = DEFAULT_WIRELESS_PORT) -> bool:
    """Clear saved host keys and credentials for host."""
    deleted = False
    if KNOWN_HOSTS_FILE.exists():
        try:
            hk = paramiko.HostKeys(str(KNOWN_HOSTS_FILE))
            h_lower = host.lower()
            to_del = []
            for k in list(hk.keys()):
                k_norm = k.lower()
                if k_norm == h_lower or k_norm.startswith(f"[{h_lower}]:"):
                    to_del.append(k)
            for k in to_del:
                del hk[k]
                deleted = True
            if to_del:
                hk.save(str(KNOWN_HOSTS_FILE))
        except Exception:
            pass
    if delete_saved_credential(host):
        deleted = True
    return deleted


# --------------------------------------------------------------------------
# Wireless Connection Wrapper (Pure SFTP - No Shell Exec)
# --------------------------------------------------------------------------

class WirelessConnection:
    """Encapsulates paramiko SSH & SFTP connections with TOFU host-key verification, keepalive, and helpers.

    Pure SFTP: operates entirely over SFTP with zero remote shell exec calls.
    """

    def __init__(self, client: paramiko.SSHClient | None = None):
        self.client: paramiko.SSHClient | None = client
        self.sftp: paramiko.SFTPClient | None = None
        self.host: str = ""
        self.port: int = DEFAULT_WIRELESS_PORT
        self.username: str = ""
        self.is_default_password: bool = False
        self.host_key_fingerprint: str = ""
        self.is_new_host_key: bool = False
        self._reconnect_args: dict | None = None  # in memory only; lets us reopen a dropped session

    def connect(
        self,
        host: str,
        port: int = DEFAULT_WIRELESS_PORT,
        username: str = "muos",
        password: str = "muos",
        timeout: float = 8.0,
        keepalive: int = 15,
        retrust: bool = False,
    ) -> None:
        self.close()

        client = paramiko.SSHClient()
        if KNOWN_HOSTS_FILE.exists():
            try:
                client.load_host_keys(str(KNOWN_HOSTS_FILE))
            except Exception:
                pass

        new_key_meta = {"new": False, "fp": ""}

        def on_first_trust(h: str, key: paramiko.PKey):
            new_key_meta["new"] = True
            new_key_meta["fp"] = format_fingerprint(key)

        client.set_missing_host_key_policy(TrustOnFirstUsePolicy(KNOWN_HOSTS_FILE, on_new_key=on_first_trust))

        try:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
        except paramiko.BadHostKeyException as e:
            got_fp = format_fingerprint(e.key)
            exp_fp = format_fingerprint(e.expected_key)
            if retrust:
                update_host_key(host, port, e.key)
                client.close()
                client = paramiko.SSHClient()
                if KNOWN_HOSTS_FILE.exists():
                    try:
                        client.load_host_keys(str(KNOWN_HOSTS_FILE))
                    except Exception:
                        pass
                client.set_missing_host_key_policy(TrustOnFirstUsePolicy(KNOWN_HOSTS_FILE, on_new_key=on_first_trust))
                client.connect(
                    hostname=host,
                    port=port,
                    username=username,
                    password=password,
                    timeout=timeout,
                    banner_timeout=timeout,
                    auth_timeout=timeout,
                    look_for_keys=False,
                    allow_agent=False,
                )
            else:
                raise ConnectionError(
                    f"Host key verification failed for {host}:{port}!\n"
                    f"Expected: {exp_fp}\n"
                    f"Received: {got_fp}\n\n"
                    f"The device's host key has changed (common when muOS Secure File Transfer restarts).\n"
                    f"Click 'Trust New Key & Reconnect' to trust this device."
                ) from e
        except (socket.error, paramiko.SSHException) as e:
            err_str = str(e).lower()
            is_refused = "connection refused" in err_str or getattr(e, "errno", None) == socket.errno.ECONNREFUSED
            if port == 22 and is_refused:
                raise ConnectionError(
                    f"Connection refused on port 22 for {host}.\n"
                    f"Turn on Secure File Transfer in muOS (port 2022):\n"
                    f"Configuration > Connectivity > Web Services."
                ) from e
            raise

        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(keepalive)
            try:
                server_key = transport.get_remote_server_key()
                if server_key:
                    self.host_key_fingerprint = format_fingerprint(server_key)
            except Exception:
                pass
        if not self.host_key_fingerprint and new_key_meta["fp"]:
            self.host_key_fingerprint = new_key_meta["fp"]
        self.is_new_host_key = new_key_meta["new"]

        self.client = client
        self.host = host
        self.port = port
        self.username = username
        self.sftp = client.open_sftp()
        self._reconnect_args = dict(host=host, port=port, username=username, password=password,
                                    timeout=timeout, keepalive=keepalive)

    def ensure_connected(self, log: Callable[[str], None] | None = None) -> None:
        """Reopen the session if it dropped (e.g. idle during a long Steam download, or the handheld's Wi-Fi
        slept). Host-key checking still applies: a changed key fails instead of being trusted."""
        if self.is_alive() and self.sftp is not None:
            return  # a session that died mid-transfer is caught by put_file_atomic's retry
        if not self._reconnect_args:
            raise ConnectionError("Wi-Fi connection dropped; reconnect to the handheld and retry.")
        if log:
            log("  ↻ Wi-Fi connection dropped; reconnecting…")
        args = self._reconnect_args
        last: Exception | None = None
        for attempt in range(3):
            try:
                self.connect(**args)
                return
            except paramiko.BadHostKeyException:
                raise
            except ConnectionError as e:
                if "Host key" in str(e):
                    raise
                last = e
            except Exception as e:  # noqa: BLE001 - network errors vary; retry a few times
                last = e
            time.sleep(2 * (attempt + 1))
        raise ConnectionError(f"Could not reconnect to {args['host']}: {last}")

    def is_alive(self) -> bool:
        if not self.client:
            return False
        transport = self.client.get_transport()
        return transport is not None and transport.is_active()

    def close(self) -> None:
        if self.sftp:
            try:
                self.sftp.close()
            except Exception:
                pass
            self.sftp = None
        if self.client:
            try:
                t = self.client.get_transport()
                if t:
                    t.close()
            except Exception:
                pass
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def read_text(self, remote_path: str) -> str | None:
        """Read content of remote file, or None if missing/unreadable."""
        if not self.sftp:
            raise ConnectionError("SFTP not connected")
        try:
            with self.sftp.open(remote_path, "r") as f:
                return f.read().decode("utf-8", errors="replace")
        except (IOError, OSError):
            return None

    def exists(self, remote_path: str) -> bool:
        if not self.sftp:
            return False
        try:
            self.sftp.stat(remote_path)
            return True
        except (IOError, OSError):
            return False

    def isdir(self, remote_path: str) -> bool:
        if not self.sftp:
            return False
        try:
            import stat
            st = self.sftp.stat(remote_path)
            return stat.S_ISDIR(st.st_mode)
        except (IOError, OSError):
            return False

    def stat(self, remote_path: str):
        if not self.sftp:
            raise ConnectionError("SFTP not connected")
        return self.sftp.stat(remote_path)

    def mkdir_p(self, remote_dir: str) -> None:
        """Recursively create remote directory over SFTP."""
        if not self.sftp:
            raise ConnectionError("SFTP not connected")
        parts = remote_dir.strip("/").split("/")
        cur = "" if not remote_dir.startswith("/") else "/"
        for p in parts:
            if not p:
                continue
            cur = f"{cur.rstrip('/')}/{p}"
            try:
                self.sftp.stat(cur)
            except (IOError, OSError):
                try:
                    self.sftp.mkdir(cur)
                except (IOError, OSError):
                    pass

    def rmtree(self, remote_path: str) -> None:
        """Recursively remove a directory and all its contents over pure SFTP."""
        if not self.sftp:
            raise ConnectionError("SFTP not connected")
        import stat as pystat
        rp = remote_path.rstrip("/")
        try:
            entries = self.sftp.listdir_attr(rp)
        except (IOError, OSError):
            return
        for entry in entries:
            child = f"{rp}/{entry.filename}"
            if pystat.S_ISDIR(entry.st_mode):
                self.rmtree(child)
            else:
                try:
                    self.sftp.remove(child)
                except (IOError, OSError):
                    pass
        try:
            self.sftp.rmdir(rp)
        except (IOError, OSError):
            pass

    def put_file_atomic(
        self,
        local_path: Path,
        remote_path: str,
        progress: Callable[[int, int], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> None:
        """Upload via SFTP (.part then rename). If the session drops mid-file, reconnect and redo that file."""
        for attempt in range(3):
            try:
                return self._put_file_atomic_once(local_path, remote_path, progress, cancel)
            except InterruptedError:
                raise
            except (paramiko.SSHException, EOFError, socket.error) as e:
                if attempt == 2 or not self._reconnect_args:
                    raise ConnectionError(f"Wi-Fi upload failed after retries: {e}") from e
                self.ensure_connected()

    def _put_file_atomic_once(
        self,
        local_path: Path,
        remote_path: str,
        progress: Callable[[int, int], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> None:
        if not self.sftp:
            raise ConnectionError("SFTP not connected")
        parent = posixpath.dirname(remote_path)
        if parent:
            self.mkdir_p(parent)

        part_path = remote_path + ".part"
        total = local_path.stat().st_size

        def cb(transferred: int, _total: int):
            if cancel and cancel():
                raise InterruptedError("cancelled")
            if progress:
                progress(transferred, total)

        # Upload to .part
        self.sftp.put(str(local_path), part_path, callback=cb)

        # Atomic rename to final path (remove old destination first if exists on SFTP)
        try:
            self.sftp.remove(remote_path)
        except (IOError, OSError):
            pass
        self.sftp.rename(part_path, remote_path)

    def remote_md5(self, remote_path: str, max_size: int = 10 * 1024 * 1024) -> str | None:
        """Compute MD5 of a remote file via SFTP chunk streaming.

        Pure SFTP: never executes shell commands. Only streams if file size <= max_size.
        """
        if not self.sftp:
            return None
        try:
            st = self.sftp.stat(remote_path)
            if getattr(st, "st_size", 0) > max_size:
                return None
            h = hashlib.md5()
            with self.sftp.open(remote_path, "rb") as f:
                while True:
                    chunk = f.read(1 << 16)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest().lower()
        except Exception:
            return None

    def df_mount(self, path: str) -> tuple[int, int] | None:
        """Get (free_bytes, total_bytes) for mount containing path.

        Pure SFTP via OpenSSH statvfs extension (CMD_EXTENDED 200). Never executes shell commands.
        If unsupported by the SFTP server, returns None.
        """
        if not self.sftp:
            return None
        try:
            from paramiko.sftp_client import CMD_EXTENDED
            t, msg = self.sftp._request(CMD_EXTENDED, "statvfs@openssh.com", path)
            if t == 201 and msg:  # CMD_EXTENDED_REPLY
                bsize = msg.get_int64()
                frsize = msg.get_int64() or bsize or 4096
                blocks = msg.get_int64()
                bfree = msg.get_int64()
                bavail = msg.get_int64()
                free_bytes = bavail * frsize
                total_bytes = blocks * frsize
                if free_bytes > 0 or total_bytes > 0:
                    return free_bytes, total_bytes
        except Exception:
            pass
        return None

    def chmod_x(self, remote_path: str) -> None:
        """Set execute permissions (+x, 0o755) via pure SFTP."""
        if not self.sftp:
            return
        try:
            self.sftp.chmod(remote_path, 0o755)
        except Exception:
            pass

    def sync(self) -> None:
        """Filesystem sync. No-op on SFTP-only servers (no shell)."""
        pass


# --------------------------------------------------------------------------
# Remote Probe & Layout Structures
# --------------------------------------------------------------------------

@dataclass
class RemoteLayout:
    ports_dir: str
    scripts_dir: str
    libs_dir: str
    media_dir: str
    system_root: str
    games_root: str
    dual_sd: bool = False
    free_ports_bytes: int = 0
    free_libs_bytes: int = 0


@dataclass
class RemoteProbe:
    cfw_id: str
    cfw_name: str
    cfw_version: str
    device_id: str
    device_name: str
    pm_version: str | None
    layout: RemoteLayout
    is_default_password: bool = False
    warning: str | None = None
    host_key_fingerprint: str = ""
    is_new_host_key: bool = False


# --------------------------------------------------------------------------
# Device & CFW Probing Logic
# --------------------------------------------------------------------------

def _read_probe_file(conn: WirelessConnection, paths: list[str]) -> str | None:
    for p in paths:
        txt = conn.read_text(p)
        if txt and txt.strip():
            return txt.strip()
    return None


def match_device_from_string(raw: str) -> Device | None:
    """Match raw model string (e.g. 'rg35xx-sp', 'tg5040') to a PortPicker Device."""
    s = raw.lower().replace("-", "").replace("_", "").replace(" ", "")
    if "brickpro" in s or "tg5040" in s:
        return next((d for d in DEVICES if d.id == "brickpro"), None)
    if "smartpro" in s:
        return next((d for d in DEVICES if d.id == "smartpro"), None)
    if "brick" in s:
        return next((d for d in DEVICES if d.id == "brick"), None)
    # Anbernic H700: one card per stick count. Models whose stick count we have not confirmed
    # return None so the card the user picked is kept.
    if "rg34xxsp" in s or "rgcubexx" in s:
        return None
    if "rg40xxv" in s:
        return next((d for d in DEVICES if d.id == "h700-1stick"), None)
    if any(k in s for k in ("rg40xxh", "rg35xxh")):
        return next((d for d in DEVICES if d.id == "h700-sticks"), None)
    if any(k in s for k in ("rg35xxsp", "rg35xxplus", "rg35xx2024", "rg28xx", "rg34xx", "rg35xx")):
        return next((d for d in DEVICES if d.id == "h700-nosticks"), None)
    if "r36s" in s:
        return next((d for d in DEVICES if d.id == "r36s"), None)
    if "rg353v" in s:
        return next((d for d in DEVICES if d.id == "rg353v"), None)
    if "h700" in s:
        if "stick" in s:
            return next((d for d in DEVICES if d.id == "h700-sticks"), None)
        return next((d for d in DEVICES if d.id == "h700-nosticks"), None)
    return None


def probe_remote(conn: WirelessConnection, device_hint: Device | None = None) -> RemoteProbe:
    """Inspect remote handheld over SFTP (read-only) to detect CFW, Device, and mounts.

    Scoped for muOS SFTP on Anbernic H700 / supported handhelds.
    Detection rule: muOS SFTP = Primary Storage + Internal + Catalogue present at /
    Pure SFTP: never executes shell commands.
    """
    if not conn.sftp:
        raise ConnectionError("SFTP connection not available")

    # 1. Detect muOS CFW
    # Check muOS SFTP virtual tree listing at /
    is_muos_sftp = False
    root_entries: set[str] = set()
    try:
        entries = conn.sftp.listdir_attr("/")
        root_entries = {e.filename for e in entries}
        if {"Primary Storage", "Internal", "Catalogue"}.issubset(root_entries):
            is_muos_sftp = True
    except Exception:
        try:
            root_entries = set(conn.sftp.listdir("/"))
            if {"Primary Storage", "Internal", "Catalogue"}.issubset(root_entries):
                is_muos_sftp = True
        except Exception:
            root_entries = set()

    cfw_ver = "unknown"
    if is_muos_sftp:
        ver_txt = _read_probe_file(conn, [
            "/Internal/config/system/version",
            "/Internal/version",
            "/Internal/MUOS/config/system/version",
        ])
        cfw_ver = ver_txt or "muOS (SFTP)"
    else:
        # Fallback for traditional Linux rootfs if /opt/muos exists
        muos_ver = _read_probe_file(conn, ["/opt/muos/config/system/version", "/opt/muos/version"])
        if muos_ver:
            cfw_ver = muos_ver
        elif conn.isdir("/opt/muos"):
            cfw_ver = "unknown"
        else:
            raise ValueError("Unsupported firmware: only muOS is currently supported for wireless transfer.")

    cfw_id = "muos"
    cfw_obj = MUOS

    # 2. Device model: use device chosen in step 1 (device_hint) or probe
    matched_dev = None
    if device_hint:
        matched_dev = device_hint
    else:
        board_str = _read_probe_file(conn, [
            "/Internal/config/device.txt",
            "/opt/muos/device/config/board/name",
            "/opt/muos/config/device.txt",
        ])
        if board_str:
            matched_dev = match_device_from_string(board_str)
        if not matched_dev:
            matched_dev = next((d for d in DEVICES if d.id == "h700-nosticks"), DEVICES[0])

    # 3. Mounts & Storage Layout
    if is_muos_sftp:
        dual_sd = "Secondary Storage" in root_entries or conn.isdir("/Secondary Storage")
        system_root = "/Primary Storage"
        games_root = "/Secondary Storage" if dual_sd else "/Primary Storage"

        ports_dir = f"{games_root}/ports"
        scripts_dir = f"{games_root}/ROMS/Ports"
        libs_dir = f"{system_root}/MUOS/PortMaster/libs"
        media_dir = "/Catalogue/External - Ports/box"

        pm_ver = _read_probe_file(conn, [
            f"{system_root}/MUOS/PortMaster/version",
            f"{games_root}/MUOS/PortMaster/version",
        ])
    else:
        dual_sd = conn.isdir("/mnt/sdcard")
        system_root = "/mnt/mmc"
        games_root = "/mnt/sdcard" if dual_sd else "/mnt/mmc"

        ports_dir = f"{games_root}/ports"
        scripts_dir = f"{games_root}/ROMS/Ports"
        libs_dir = f"{system_root}/MUOS/PortMaster/libs"
        media_dir = f"{system_root}/MUOS/info/catalogue/External - Ports/box"

        pm_ver = _read_probe_file(conn, [
            f"{system_root}/MUOS/PortMaster/version",
            f"{games_root}/MUOS/PortMaster/version",
        ])

    df_ports = conn.df_mount(games_root)
    df_libs = conn.df_mount(system_root)

    layout = RemoteLayout(
        ports_dir=ports_dir,
        scripts_dir=scripts_dir,
        libs_dir=libs_dir,
        media_dir=media_dir,
        system_root=system_root,
        games_root=games_root,
        dual_sd=dual_sd,
        free_ports_bytes=df_ports[0] if df_ports else 0,
        free_libs_bytes=df_libs[0] if df_libs else 0,
    )

    # Validate that layout paths are never forbidden system paths
    for p in (layout.ports_dir, layout.scripts_dir, layout.libs_dir):
        if not p or p == "/":
            raise ValueError(f"Dangerous/invalid remote target path: {p}")
        for forbidden in FORBIDDEN_SFTP_PREFIXES:
            if p == forbidden or p.startswith(forbidden + "/"):
                raise ValueError(f"Dangerous/invalid remote target path under restricted system path {forbidden}: {p}")

    warn_msg = None
    if conn.is_default_password:
        warn_msg = _tr(
            "wireless.default_pass_warn",
            "Default password ({user}) was used. For security on public networks, consider changing it.",
            user=conn.username,
        )

    return RemoteProbe(
        cfw_id=cfw_id,
        cfw_name=cfw_obj.name,
        cfw_version=cfw_ver,
        device_id=matched_dev.id,
        device_name=matched_dev.name,
        pm_version=pm_ver,
        layout=layout,
        is_default_password=conn.is_default_password,
        warning=warn_msg,
        host_key_fingerprint=conn.host_key_fingerprint,
        is_new_host_key=conn.is_new_host_key,
    )


# --------------------------------------------------------------------------
# Auto-Discovery & Connection
# --------------------------------------------------------------------------

def resolve_host_ip(host: str, timeout: float = 1.0) -> str | None:
    """Resolve hostname (including .local) to an IP address, or None if unreachable."""
    try:
        orig = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            return socket.gethostbyname(host)
        finally:
            socket.setdefaulttimeout(orig)
    except Exception:
        return None


def discover_handheld_hosts(timeout: float = 1.0) -> list[tuple[str, str, str]]:
    """Discover reachable handhelds on local network via known mDNS names.

    Returns list of (display_name, host_name, ip_address).
    """
    results: list[tuple[str, str, str]] = []
    for host, cfw_tag in KNOWN_MDNS_HOSTS:
        ip = resolve_host_ip(host, timeout=timeout)
        if ip:
            display = f"{host} ({cfw_tag.upper()}) · {ip}"
            results.append((display, host, ip))
    return results


def connect_with_credentials_or_defaults(
    host: str,
    port: int = DEFAULT_WIRELESS_PORT,
    username: str | None = None,
    password: str | None = None,
    cfw_hint: str | None = None,
    timeout: float = 8.0,
    retrust: bool = False,
) -> WirelessConnection:
    """Connect to handheld SFTP. If credentials are not provided, tries saved credentials or muOS defaults."""
    conn = WirelessConnection()

    # 1. Try saved credentials if available and none provided explicitly
    if not username and not password:
        saved = get_saved_credential(host)
        if saved:
            u, p = saved
            try:
                conn.connect(host, port=port, username=u, password=p, timeout=timeout, retrust=retrust)
                conn.is_default_password = False
                return conn
            except Exception:
                conn.close()

    # 2. If explicit credentials provided, try them
    if username is not None and password is not None:
        try:
            conn.connect(host, port=port, username=username, password=password, timeout=timeout, retrust=retrust)
            conn.is_default_password = False
            return conn
        except Exception:
            conn.close()
            raise

    # 3. Try muOS default credentials (muos:muos on 2022, root:root on 22)
    creds_to_try: list[tuple[str, str, int]] = []
    if cfw_hint and cfw_hint in CFW_DEFAULT_CREDENTIALS:
        creds_to_try.extend(CFW_DEFAULT_CREDENTIALS[cfw_hint])
    elif "muos" in CFW_DEFAULT_CREDENTIALS:
        creds_to_try.extend(CFW_DEFAULT_CREDENTIALS["muos"])
    for item in GENERIC_DEFAULT_CREDENTIALS:
        if item not in creds_to_try:
            creds_to_try.append(item)

    last_err: Exception | None = None
    for u, p, p_port in creds_to_try:
        use_port = port if port != DEFAULT_WIRELESS_PORT else p_port
        try:
            conn.connect(host, port=use_port, username=u, password=p, timeout=timeout, retrust=retrust)
            conn.is_default_password = True
            return conn
        except Exception as e:
            conn.close()
            last_err = e
            continue

    if last_err:
        raise last_err
    raise ConnectionError(f"Could not connect to {host} with default credentials")


# --------------------------------------------------------------------------
# Wireless Target Adapter
# --------------------------------------------------------------------------

@dataclass
class WirelessTarget(core.Target):
    """Adapter for core.Target that routes operations over a WirelessConnection."""
    conn: WirelessConnection = field(default_factory=WirelessConnection)
    probe: RemoteProbe | None = None
    is_wireless: bool = True

    def __init__(self, cfw: CFW, conn: WirelessConnection, probe: RemoteProbe):
        super().__init__(
            cfw=cfw,
            root=Path(probe.layout.games_root),
            custom=False,
            system_root=Path(probe.layout.system_root),
        )
        self.conn = conn
        self.probe = probe
        self.is_wireless = True

    @property
    def scripts_dir(self) -> Path:
        return Path(self.probe.layout.scripts_dir) if self.probe else super().scripts_dir

    @property
    def ports_dir(self) -> Path:
        return Path(self.probe.layout.ports_dir) if self.probe else super().ports_dir

    @property
    def libs_dir(self) -> Path | None:
        return Path(self.probe.layout.libs_dir) if self.probe else super().libs_dir

    @property
    def media_dir(self) -> Path | None:
        if self.probe and self.probe.layout.media_dir:
            return Path(self.probe.layout.media_dir)
        return None

    def pak_installed(self) -> bool:
        """muOS has PortMaster built-in or verified during wireless probe."""
        return True


# --------------------------------------------------------------------------
# Wireless Transfer Engine (Pure SFTP)
# --------------------------------------------------------------------------

def transfer_port_wireless(
    conn: WirelessConnection,
    port: core.Port,
    probe: RemoteProbe,
    dev: Device,
    utils: dict,
    log: Log,
    progress: Progress | None = None,
    overwrite_local: bool = False,
    cancel: Callable[[], bool] | None = None,
    clean: bool = False,
) -> None:
    """Transfer a single port to the handheld over SFTP atomically.

    Stages files locally on PC first, then uploads files via .part atomic put.
    Skips runtimes already present on device (verified via size or SFTP MD5).
    Pure SFTP: never executes shell commands.
    """
    if cancel and cancel():
        raise InterruptedError("cancelled")

    layout = probe.layout
    log(f"▶ {port.title} (Wireless → {probe.cfw_name})")

    stage_dir = core.DL_DIR / f"_stage_{port.stem}"
    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Download Port zip and unpack to local staging
        z = core.download(port.url, core.DL_DIR / port.name, progress, md5=port.md5, cancel=cancel)
        with zipfile.ZipFile(z) as zf:
            zip_names = zf.namelist()
            core.safe_extract(zf, stage_dir, cancel=cancel)

        # 2. Local game files / Steam files staging
        if port.local_dir:
            root = port.dirs[0] if port.dirs else port.stem
            plan = core.plan_local_files(root, zip_names, port.inst, core.list_files(port.local_dir))
            log(_tr("log.game_files_from", "  ⇢ Game files from {src} → {dst}", src=port.local_dir, dst=root))
            core.copy_planned(port.local_dir, stage_dir / root, plan, overwrite_local, cancel)
        elif port.steam_ready:
            r, src = port.steam.recipe, port.steam.app.path
            dst = stage_dir / r.dest
            log(_tr("log.copy_steam", "  🎮 Staging game files from local Steam: {src}", src=src))
            core.merge_tree(src, dst, overwrite_local, log, cancel)
        elif getattr(port, "steam_download_ready", False) and port.steam_depot:
            recipe = port.steam_depot
            dst = stage_dir / recipe.dest
            log(_tr("log.download_steam", "  🎮 Downloading selective game files from Steam for {title}…", title=port.title))
            steam_downloader.download_steam_files(recipe, dst, log=log, progress=progress, cancel=cancel)
        elif port.needs_files:
            log(_tr("log.needs_game_files", "  ⚠ This game requires additional game files: {inst}",
                    inst=port.inst or "see port page on portmaster.games"))

        # Clean remote port directories if requested (pure SFTP removal)
        if clean:
            for d in port.dirs:
                if not d or not isinstance(d, str) or not d.strip():
                    continue
                d_name = d.strip()
                # Reject empty, absolute, .., or /-containing names
                if d_name.startswith("/") or d_name.startswith("\\") or ".." in d_name or "/" in d_name or "\\" in d_name:
                    log(f"  ⚠ Skipping unsafe port directory name: {d!r}")
                    continue
                remote_d = posixpath.normpath(posixpath.join(layout.ports_dir, d_name))
                expected_prefix = layout.ports_dir.rstrip("/") + "/"
                if not remote_d.startswith(expected_prefix):
                    log(f"  ⚠ Skipping unsafe port directory outside ports: {remote_d!r}")
                    continue
                try:
                    validate_sftp_write_path(remote_d, layout)
                    conn.rmtree(remote_d)
                except Exception as e:
                    log(f"  ⚠ Skipped removing remote directory {remote_d}: {e}")

        # 3. SFTP Upload Port directories. The steps above (Steam download) can take many minutes, long enough
        # for the handheld to drop an idle session, so check it first.
        conn.ensure_connected(log)
        log("  ⇢ Uploading port files over SFTP…")
        uploaded_scripts: list[str] = []

        for root_p, dirs, files in os.walk(stage_dir):
            if cancel and cancel():
                raise InterruptedError("cancelled")
            rel = os.path.relpath(root_p, stage_dir)
            if rel == ".":
                # Root level files in the zip: check if scripts
                for f in files:
                    local_f = Path(root_p) / f
                    if f in port.scripts or f.endswith(".sh"):
                        remote_sh = f"{layout.scripts_dir}/{f}"
                        validate_sftp_write_path(remote_sh, layout)
                        conn.put_file_atomic(local_f, remote_sh, progress=progress, cancel=cancel)
                        uploaded_scripts.append(remote_sh)
                        if not MUOS.builtin_pm or layout.ports_dir != layout.scripts_dir:
                            remote_p_sh = f"{layout.ports_dir}/{f}"
                            validate_sftp_write_path(remote_p_sh, layout)
                            conn.put_file_atomic(local_f, remote_p_sh, progress=progress, cancel=cancel)
                    else:
                        remote_dest = f"{layout.ports_dir}/{f}"
                        validate_sftp_write_path(remote_dest, layout)
                        conn.put_file_atomic(local_f, remote_dest, progress=progress, cancel=cancel)
            else:
                for f in files:
                    if cancel and cancel():
                        raise InterruptedError("cancelled")
                    local_f = Path(root_p) / f
                    rel_f = os.path.join(rel, f).replace("\\", "/")
                    remote_f = f"{layout.ports_dir}/{rel_f}"
                    validate_sftp_write_path(remote_f, layout)
                    conn.put_file_atomic(local_f, remote_f, progress=progress, cancel=cancel)

        # 4. Handle Runtimes (Skip if already present and size/MD5 matches)
        for r in port.runtime:
            if cancel and cancel():
                raise InterruptedError("cancelled")
            u = utils.get(r)
            if not u:
                continue
            arch = dev.arch
            url = u.get(arch) or u.get("url")
            if not url:
                continue

            runtime_name = Path(url).name
            remote_runtime_path = f"{layout.libs_dir}/{runtime_name}"
            validate_sftp_write_path(remote_runtime_path, layout)
            expected_md5 = u.get("md5")
            expected_size = u.get("size")

            # Check if runtime already exists on handheld
            if conn.exists(remote_runtime_path):
                remote_size = None
                try:
                    remote_size = conn.stat(remote_runtime_path).st_size
                except Exception:
                    pass

                # Match by size if known
                if expected_size and remote_size is not None and remote_size == expected_size:
                    log(f"  ✔ Runtime {runtime_name} already present (size match: {remote_size} bytes) — skipped upload")
                    continue

                # Match by MD5 if cheap streamable
                if expected_md5:
                    r_md5 = conn.remote_md5(remote_runtime_path)
                    if r_md5 and r_md5 == expected_md5.lower():
                        log(f"  ✔ Runtime {runtime_name} already present (MD5 match) — skipped upload")
                        continue

                # Fallback: if file exists and no expected metadata or size matches
                if not expected_md5 and not expected_size and remote_size and remote_size > 0:
                    log(f"  ✔ Runtime {runtime_name} already present — skipped upload")
                    continue

            # Need to download and upload runtime
            log(f"  ⇢ Uploading runtime {runtime_name}…")
            dl_path = core.DL_DIR / runtime_name
            cached_rt = core.download(url, dl_path, progress, md5=expected_md5, cancel=cancel)
            conn.put_file_atomic(cached_rt, remote_runtime_path, progress=progress, cancel=cancel)
            log(f"  ✔ Uploaded runtime {runtime_name}")

        # 5. Box art (if layout.media_dir exists)
        if layout.media_dir and port.scripts:
            img = core.image_path(port.stem, port.cover) or core.image_path(port.stem, port.screenshot)
            if img:
                script_stem = Path(port.scripts[0]).stem
                remote_art = f"{layout.media_dir}/{script_stem}.png"
                validate_sftp_write_path(remote_art, layout)
                local_png = stage_dir / f"{script_stem}.png"
                core.copy_box_art(img, local_png, dev)
                conn.put_file_atomic(local_png, remote_art, cancel=cancel)

        # 6. Set execute permissions on scripts via SFTP chmod
        for sh_path in uploaded_scripts:
            conn.chmod_x(sh_path)

        log(_tr("log.installed_pm", "  ✔ Installed port over wireless"))

    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def finalize_wireless_session(conn: WirelessConnection, cfw_id: str, log: Log) -> None:
    """Finalize wireless session (no shell exec commands)."""
    conn.sync()
    log(_tr("wireless.reopen_menu_tip", "  💡 Reopen the Ports menu on your handheld to see newly added games."))
    log("  ✔ Finished wireless transfer.")
