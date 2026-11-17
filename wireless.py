"""PortPicker wireless transfer module: SSH/SFTP connection, probe, atomic transfer, and hooks.

Trimmed for muOS on Anbernic H700 / supported handhelds (mDNS muos.local + manual IP).
Handles mDNS discovery, DPAPI credential storage, read-only SSH probe,
atomic SFTP push (.part -> rename), runtime MD5 skip, and muOS catalogue refresh.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import socket
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import paramiko

from i18n import t as _tr
import core
from profiles import CFWS, CFW, Device, DEVICES, MUOS

# --------------------------------------------------------------------------
# Types and Constants
# --------------------------------------------------------------------------

Log = Callable[[str], None]
Progress = Callable[[int, int], None]

ONE_GB = 1024 * 1024 * 1024
SIZE_SUGGEST_CARD_READER = int(1.5 * ONE_GB)  # 1.5 GB

KNOWN_MDNS_HOSTS = [
    ("muos.local", "muos"),
]

# Default credentials for muOS: list of (user, password, port)
CFW_DEFAULT_CREDENTIALS: dict[str, list[tuple[str, str, int]]] = {
    "muos": [
        ("root", "root", 22),
    ],
}

GENERIC_DEFAULT_CREDENTIALS = [
    ("root", "root", 22),
]


# --------------------------------------------------------------------------
# DPAPI / Secure Credential Storage
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


def forget_host(host: str, port: int = 22) -> bool:
    """Clear saved host keys and credentials for host.

    Used when host key changes due to OS reinstall or device reset.
    """
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
# Wireless Connection Wrapper
# --------------------------------------------------------------------------

class WirelessConnection:
    """Encapsulates paramiko SSH & SFTP connections with TOFU host-key verification, keepalive, and helpers."""

    def __init__(self, client: paramiko.SSHClient | None = None):
        self.client: paramiko.SSHClient | None = client
        self.sftp: paramiko.SFTPClient | None = None
        self.host: str = ""
        self.port: int = 22
        self.username: str = ""
        self.is_default_password: bool = False
        self.host_key_fingerprint: str = ""
        self.is_new_host_key: bool = False

    def connect(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        password: str = "root",
        timeout: float = 8.0,
        keepalive: int = 15,
    ) -> None:
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
            raise ConnectionError(
                f"Host key verification failed for {host}!\n"
                f"Expected: {exp_fp}\n"
                f"Received: {got_fp}\n\n"
                f"The device's host key has changed. This could indicate an attacker on the network (MITM), "
                f"or the OS was reinstalled on the handheld.\n"
                f"If you reinstalled muOS, click 'Forget Device' and reconnect."
            ) from e

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
                self.client.close()
            except Exception:
                pass
            self.client = None

    def exec(self, cmd: str, timeout: float = 15.0) -> tuple[int, str, str]:
        """Execute a shell command over SSH."""
        if not self.client:
            raise ConnectionError("SSH not connected")
        stdin, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return exit_code, out, err

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
        """Recursively remove a directory and all its contents over SFTP."""
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
        """Upload file via SFTP using atomic write (<remote_path>.part then rename)."""
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

    def remote_md5(self, remote_path: str) -> str | None:
        """Compute MD5 of a remote file via md5sum command or reading chunks."""
        code, out, _ = self.exec(f"md5sum {shlex.quote(remote_path)} 2>/dev/null", timeout=5.0)
        if code == 0 and out.strip():
            return out.strip().split()[0].lower()
        # Fallback: stream MD5 over SFTP
        try:
            if not self.sftp:
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
        """Get (free_bytes, total_bytes) for mount containing path."""
        code, out, _ = self.exec(f"df -k {shlex.quote(path)} 2>/dev/null", timeout=5.0)
        if code == 0:
            lines = [line.strip() for line in out.splitlines() if line.strip()]
            if len(lines) >= 2:
                parts = lines[-1].split()
                if len(parts) >= 4:
                    try:
                        total_k = int(parts[1])
                        avail_k = int(parts[3])
                        return avail_k * 1024, total_k * 1024
                    except ValueError:
                        pass
        return None

    def sync(self) -> None:
        self.exec("sync", timeout=10.0)

    def chmod_x(self, remote_path: str) -> None:
        self.exec(f"chmod +x {shlex.quote(remote_path)}", timeout=5.0)


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
    # Check exact/substring matches
    if "rg35xxsp" in s:
        return next((d for d in DEVICES if d.id == "rg35xxsp"), None)
    if "brickpro" in s or "tg5040" in s:
        return next((d for d in DEVICES if d.id == "brickpro"), None)
    if "smartpro" in s:
        return next((d for d in DEVICES if d.id == "smartpro"), None)
    if "brick" in s:
        return next((d for d in DEVICES if d.id == "brick"), None)
    if "rg40xxh" in s or "rg35xxh" in s:
        return next((d for d in DEVICES if d.id in ("rg40xxh", "h700-sticks")), None)
    if "rg35xxplus" in s or "rg28xx" in s or "rg40xxv" in s:
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


def probe_remote(conn: WirelessConnection) -> RemoteProbe:
    """Inspect remote handheld over SSH (read-only) to detect CFW, Device, and mounts.

    Scoped for muOS on Anbernic H700 / supported handhelds.
    """
    # 1. Detect muOS CFW
    muos_ver = _read_probe_file(conn, ["/opt/muos/config/system/version", "/opt/muos/version"])
    if not muos_ver:
        if conn.isdir("/opt/muos"):
            muos_ver = "unknown"
        else:
            raise ValueError("Unsupported firmware: only muOS is currently supported for wireless transfer.")

    cfw_id = "muos"
    cfw_obj = MUOS
    cfw_ver = muos_ver

    # 2. Detect Device Model (Anbernic H700 focus)
    board_str = _read_probe_file(conn, [
        "/opt/muos/device/config/board/name",
        "/opt/muos/config/device.txt",
        "/proc/device-tree/model",
        "/etc/device_model",
    ])
    if not board_str:
        # Fallback to uname -m
        _, uname_m, _ = conn.exec("uname -m")
        board_str = uname_m.strip() or "aarch64"

    matched_dev = match_device_from_string(board_str)
    if not matched_dev:
        # Fallback default device (RG35XX SP)
        matched_dev = next((d for d in DEVICES if d.id == "rg35xxsp"), DEVICES[0])

    # 3. Mounts & Storage Layout (muOS dual-SD or single-SD)
    # Check if SD2 is mounted at /mnt/sdcard
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

    # Validate that layout paths are never /tmp or rootfs
    for p in (layout.ports_dir, layout.scripts_dir, layout.libs_dir):
        if not p or p.startswith("/tmp") or p.startswith("/root") or p == "/":
            raise ValueError(f"Dangerous/invalid remote target path: {p}")

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
    port: int = 22,
    username: str | None = None,
    password: str | None = None,
    cfw_hint: str | None = None,
    timeout: float = 8.0,
) -> WirelessConnection:
    """Connect to handheld SSH. If credentials are not provided, tries saved credentials or muOS defaults."""
    conn = WirelessConnection()

    # 1. Try saved credentials if available and none provided explicitly
    if not username and not password:
        saved = get_saved_credential(host)
        if saved:
            u, p = saved
            try:
                conn.connect(host, port=port, username=u, password=p, timeout=timeout)
                conn.is_default_password = False
                return conn
            except Exception:
                pass

    # 2. If explicit credentials provided, try them
    if username is not None and password is not None:
        conn.connect(host, port=port, username=username, password=password, timeout=timeout)
        conn.is_default_password = False
        return conn

    # 3. Try muOS default credentials once
    creds_to_try: list[tuple[str, str, int]] = []
    if "muos" in CFW_DEFAULT_CREDENTIALS:
        creds_to_try.extend(CFW_DEFAULT_CREDENTIALS["muos"])
    for item in GENERIC_DEFAULT_CREDENTIALS:
        if item not in creds_to_try:
            creds_to_try.append(item)

    last_err: Exception | None = None
    for u, p, p_port in creds_to_try:
        use_port = port if port != 22 else p_port
        try:
            conn.connect(host, port=use_port, username=u, password=p, timeout=timeout)
            conn.is_default_password = True
            return conn
        except (paramiko.AuthenticationException, paramiko.SSHException, socket.error) as e:
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


# --------------------------------------------------------------------------
# Wireless Transfer Engine
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
    Skips runtimes already present on device (MD5 verified).
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
        elif port.needs_files:
            log(_tr("log.needs_game_files", "  ⚠ This game requires additional game files: {inst}",
                    inst=port.inst or "see port page on portmaster.games"))

        # Clean remote port directories if requested
        if clean:
            for d in port.dirs:
                if not d or not isinstance(d, str) or not d.strip():
                    continue
                d_name = d.strip()
                # Reject empty, absolute, .., or /-containing names
                if d_name.startswith("/") or d_name.startswith("\\") or ".." in d_name or "/" in d_name or "\\" in d_name:
                    log(f"  ⚠ Skipping unsafe port directory name: {d!r}")
                    continue
                # Build path with posixpath and check it starts with ports_dir + '/'
                remote_d = posixpath.normpath(posixpath.join(layout.ports_dir, d_name))
                expected_prefix = layout.ports_dir.rstrip("/") + "/"
                if not remote_d.startswith(expected_prefix):
                    log(f"  ⚠ Skipping unsafe port directory outside ports: {remote_d!r}")
                    continue
                # Prefer SFTP removal over shell command
                try:
                    conn.rmtree(remote_d)
                except Exception:
                    conn.exec(f"rm -rf {shlex.quote(remote_d)}", timeout=5.0)

        # 3. SFTP Upload Port directories
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
                        # Launch script: destination is scripts_dir
                        remote_sh = f"{layout.scripts_dir}/{f}"
                        conn.put_file_atomic(local_f, remote_sh, progress=progress, cancel=cancel)
                        uploaded_scripts.append(remote_sh)
                        # Also put in ports_dir if not muOS/builtin_pm
                        if not MUOS.builtin_pm or layout.ports_dir != layout.scripts_dir:
                            conn.put_file_atomic(local_f, f"{layout.ports_dir}/{f}", progress=progress, cancel=cancel)
                    else:
                        conn.put_file_atomic(local_f, f"{layout.ports_dir}/{f}", progress=progress, cancel=cancel)
            else:
                for f in files:
                    if cancel and cancel():
                        raise InterruptedError("cancelled")
                    local_f = Path(root_p) / f
                    rel_f = os.path.join(rel, f).replace("\\", "/")
                    remote_f = f"{layout.ports_dir}/{rel_f}"
                    conn.put_file_atomic(local_f, remote_f, progress=progress, cancel=cancel)

        # 4. Handle Runtimes (Skip if already present and MD5 matches)
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
            expected_md5 = u.get("md5")

            # Check if runtime already exists on handheld
            if conn.exists(remote_runtime_path):
                # Verify MD5 if expected_md5 is provided
                if expected_md5:
                    r_md5 = conn.remote_md5(remote_runtime_path)
                    if r_md5 and r_md5 == expected_md5.lower():
                        log(f"  ✔ Runtime {runtime_name} already present (MD5 match) — skipped upload")
                        continue
                else:
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
                # Stage converted png locally
                local_png = stage_dir / f"{script_stem}.png"
                core.copy_box_art(img, local_png)
                conn.put_file_atomic(local_png, remote_art, cancel=cancel)

        # 6. Set execute permissions on scripts
        for sh_path in uploaded_scripts:
            conn.chmod_x(sh_path)

        log(_tr("log.installed_pm", "  ✔ Installed port over wireless"))

    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def finalize_wireless_session(conn: WirelessConnection, cfw_id: str, log: Log) -> None:
    """Run sync on handheld filesystem."""
    log("  ⇢ Syncing filesystem on handheld…")
    conn.sync()
    log(_tr("wireless.reopen_menu_tip", "  💡 Reopen the Ports menu on your handheld to see newly added games."))
    log("  ✔ Finished wireless transfer.")
