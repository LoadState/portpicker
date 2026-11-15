"""Tests for PortPicker wireless transfer module (SSH/SFTP).

Trimmed for muOS on Anbernic H700 / supported handhelds.
Tests mDNS discovery, DPAPI credential storage, SSH probing on muOS (single/dual SD),
atomic SFTP push (.part -> rename), runtime MD5 skipping, and muOS catalogue refresh hooks.
"""
from __future__ import annotations

import io
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core
from profiles import CFWS, DEVICES, MUOS, Device
import wireless


# --------------------------------------------------------------------------
# Mock SFTP and SSH Infrastructure
# --------------------------------------------------------------------------

class MockSFTPStat:
    def __init__(self, size: int = 1024, is_dir: bool = False):
        self.st_size = size
        self.st_mode = stat.S_IFDIR | 0o755 if is_dir else stat.S_IFREG | 0o644


class MockSFTPClient:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/", "/mnt", "/opt", "/usr", "/etc"}
        self.put_calls: list[tuple[str, str]] = []
        self.rename_calls: list[tuple[str, str]] = []
        self.removed_files: list[str] = []

    def stat(self, remote_path: str) -> MockSFTPStat:
        rp = remote_path.rstrip("/")
        if rp in self.dirs:
            return MockSFTPStat(0, is_dir=True)
        if rp in self.files:
            return MockSFTPStat(len(self.files[rp]), is_dir=False)
        raise IOError(f"No such file or directory: {remote_path}")

    def mkdir(self, remote_path: str):
        self.dirs.add(remote_path.rstrip("/"))

    def open(self, remote_path: str, mode: str = "r"):
        rp = remote_path.rstrip("/")
        if "w" in mode or "a" in mode:
            # For writing
            bio = io.BytesIO()
            orig_close = bio.close

            def on_close():
                self.files[rp] = bio.getvalue()
                orig_close()

            bio.close = on_close
            return bio
        else:
            # For reading
            if rp not in self.files:
                raise IOError(f"File not found: {remote_path}")
            return io.BytesIO(self.files[rp])

    def put(self, local_path: str, remote_path: str, callback=None):
        self.put_calls.append((local_path, remote_path))
        data = Path(local_path).read_bytes()
        if callback:
            callback(len(data), len(data))
        self.files[remote_path] = data

    def rename(self, src: str, dst: str):
        self.rename_calls.append((src, dst))
        if src in self.files:
            self.files[dst] = self.files.pop(src)
        elif src in self.dirs:
            self.dirs.remove(src)
            self.dirs.add(dst)
        else:
            raise IOError(f"Cannot rename {src}: not found")

    def remove(self, remote_path: str):
        self.removed_files.append(remote_path)
        self.files.pop(remote_path, None)

    def close(self):
        pass


class MockChannel:
    def __init__(self, exit_status: int = 0):
        self._exit_status = exit_status

    def recv_exit_status(self) -> int:
        return self._exit_status


class MockSSHClient:
    def __init__(self, sftp_client: MockSFTPClient):
        self.sftp = sftp_client
        self.executed_commands: list[str] = []
        self.keepalive_set = None

    def get_transport(self):
        m = MagicMock()
        m.is_active.return_value = True
        m.set_keepalive.side_effect = lambda sec: setattr(self, "keepalive_set", sec)
        return m

    def open_sftp(self) -> MockSFTPClient:
        return self.sftp

    def exec_command(self, cmd: str, timeout: float = 15.0):
        self.executed_commands.append(cmd)
        stdout_bio = io.BytesIO()
        stderr_bio = io.BytesIO()
        channel = MockChannel(0)

        # Handle df -k
        if "df -k" in cmd:
            out = "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/mmcblk0p1 62000000 1000000 61000000 2% /mnt/mmc\n"
            stdout_bio = io.BytesIO(out.encode("utf-8"))

        # Handle md5sum
        elif "md5sum" in cmd:
            parts = cmd.split("'")
            path = parts[1] if len(parts) > 1 else ""
            if path in self.sftp.files:
                import hashlib
                h = hashlib.md5(self.sftp.files[path]).hexdigest()
                stdout_bio = io.BytesIO(f"{h}  {path}\n".encode("utf-8"))
            else:
                channel = MockChannel(1)
                stderr_bio = io.BytesIO(b"md5sum: file not found\n")

        elif "uname -m" in cmd:
            stdout_bio = io.BytesIO(b"aarch64\n")

        mock_stdout = MagicMock()
        mock_stdout.channel = channel
        mock_stdout.read.side_effect = stdout_bio.getvalue
        mock_stderr = MagicMock()
        mock_stderr.read.side_effect = stderr_bio.getvalue

        return MagicMock(), mock_stdout, mock_stderr

    def close(self):
        pass


@pytest.fixture
def mock_remote_env():
    sftp = MockSFTPClient()
    ssh = MockSSHClient(sftp)
    conn = wireless.WirelessConnection()
    conn.client = ssh
    conn.sftp = sftp
    conn.host = "192.168.1.100"
    conn.username = "root"
    return conn, ssh, sftp


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_device_matching_from_strings():
    """Verify raw strings from handhelds match correct Device entries."""
    assert wireless.match_device_from_string("rg35xx-sp").id == "rg35xxsp"
    assert wireless.match_device_from_string("Anbernic RG35XX SP").id == "rg35xxsp"
    assert wireless.match_device_from_string("tg5040").id == "brickpro"
    assert wireless.match_device_from_string("TrimUI Brick Pro").id == "brickpro"
    assert wireless.match_device_from_string("TrimUI Smart Pro").id == "smartpro"
    assert wireless.match_device_from_string("TrimUI Brick").id == "brick"
    assert wireless.match_device_from_string("RG40XX H").id in ("rg40xxh", "h700-sticks")
    assert wireless.match_device_from_string("rg35xx-h").id in ("rg40xxh", "h700-sticks")
    assert wireless.match_device_from_string("RG35XX Plus").id == "h700-nosticks"
    assert wireless.match_device_from_string("r36s").id == "r36s"
    assert wireless.match_device_from_string("rg353v").id == "rg353v"
    assert wireless.match_device_from_string("H700").id == "h700-nosticks"
    assert wireless.match_device_from_string("H700-sticks").id == "h700-sticks"


def test_dpapi_credential_save_and_retrieve(tmp_path):
    """Test DPAPI credential store round-trip."""
    test_auth_file = tmp_path / "wireless_auth.dat"
    with patch("wireless.AUTH_FILE", test_auth_file):
        wireless.save_saved_credential("muos.local", "muos", "secret123")
        res = wireless.get_saved_credential("muos.local")
        assert res == ("muos", "secret123")

        # Case-insensitivity check
        res_upper = wireless.get_saved_credential("MUOS.LOCAL")
        assert res_upper == ("muos", "secret123")

        # Unknown host
        assert wireless.get_saved_credential("unknown.local") is None


def test_probe_muos_dual_sd(mock_remote_env):
    """Test SSH probing on muOS with 2 SD cards (/mnt/mmc and /mnt/sdcard)."""
    conn, ssh, sftp = mock_remote_env
    # Set up muOS files
    sftp.files["/opt/muos/config/system/version"] = b"2606.0\n"
    sftp.files["/opt/muos/device/config/board/name"] = b"rg35xx-sp\n"
    sftp.files["/mnt/mmc/MUOS/PortMaster/version"] = b"2024.11.01\n"
    sftp.dirs.add("/mnt/mmc")
    sftp.dirs.add("/mnt/sdcard")

    probe = wireless.probe_remote(conn)
    assert probe.cfw_id == "muos"
    assert probe.cfw_version == "2606.0"
    assert probe.device_id == "rg35xxsp"
    assert probe.pm_version == "2024.11.01"
    assert probe.layout.dual_sd is True
    # SD2 is used for games, SD1 for runtimes and media
    assert probe.layout.ports_dir == "/mnt/sdcard/ports"
    assert probe.layout.scripts_dir == "/mnt/sdcard/ROMS/Ports"
    assert probe.layout.libs_dir == "/mnt/mmc/MUOS/PortMaster/libs"
    assert probe.layout.media_dir == "/mnt/mmc/MUOS/info/catalogue/External - Ports/box"


def test_probe_muos_single_sd(mock_remote_env):
    """Test SSH probing on muOS with a single SD card (/mnt/mmc only)."""
    conn, ssh, sftp = mock_remote_env
    sftp.files["/opt/muos/config/system/version"] = b"2606.1\n"
    sftp.files["/opt/muos/device/config/board/name"] = b"rg35xx-sp\n"
    sftp.dirs.add("/mnt/mmc")
    # /mnt/sdcard is NOT in sftp.dirs

    probe = wireless.probe_remote(conn)
    assert probe.cfw_id == "muos"
    assert probe.layout.dual_sd is False
    assert probe.layout.ports_dir == "/mnt/mmc/ports"
    assert probe.layout.scripts_dir == "/mnt/mmc/ROMS/Ports"
    assert probe.layout.libs_dir == "/mnt/mmc/MUOS/PortMaster/libs"


def test_probe_rejects_non_muos(mock_remote_env):
    """Verify that probe raises ValueError if remote is not muOS."""
    conn, ssh, sftp = mock_remote_env
    with pytest.raises(ValueError, match="only muOS is currently supported"):
        wireless.probe_remote(conn)


def test_probe_default_password_warning(mock_remote_env):
    """Verify warning message when default password was used."""
    conn, ssh, sftp = mock_remote_env
    sftp.files["/opt/muos/config/system/version"] = b"2606.0\n"
    sftp.dirs.add("/mnt/mmc")
    conn.is_default_password = True
    conn.username = "root"

    probe = wireless.probe_remote(conn)
    assert probe.is_default_password is True
    assert probe.warning is not None
    assert "Default password (root) was used" in probe.warning


def test_reject_dangerous_mounts(mock_remote_env):
    """Verify that layout paths pointing to /tmp or rootfs raise ValueError."""
    conn, ssh, sftp = mock_remote_env
    sftp.files["/opt/muos/config/system/version"] = b"2606.0\n"
    with patch("wireless.RemoteLayout") as mock_rl:
        mock_rl.return_value = wireless.RemoteLayout(
            ports_dir="/tmp/ports",
            scripts_dir="/mnt/mmc/ROMS/Ports",
            libs_dir="/mnt/mmc/MUOS/libs",
            media_dir="",
            system_root="/",
            games_root="/tmp",
        )
        with pytest.raises(ValueError, match="Dangerous/invalid remote target path"):
            wireless.probe_remote(conn)


def test_put_file_atomic(tmp_path, mock_remote_env):
    """Verify put_file_atomic uploads to .part first, then renames to destination."""
    conn, ssh, sftp = mock_remote_env
    local_file = tmp_path / "game.exe"
    local_file.write_bytes(b"HELLO_WORLD_BINARY_DATA")

    remote_dest = "/mnt/sdcard/ports/game/game.exe"
    conn.put_file_atomic(local_file, remote_dest)

    # Verify put call had .part
    assert len(sftp.put_calls) == 1
    assert sftp.put_calls[0][1] == remote_dest + ".part"

    # Verify rename call from .part to final
    assert len(sftp.rename_calls) == 1
    assert sftp.rename_calls[0] == (remote_dest + ".part", remote_dest)
    assert sftp.files[remote_dest] == b"HELLO_WORLD_BINARY_DATA"


def make_port(stem: str, **kw) -> core.Port:
    base = dict(
        name=f"{stem}.zip", title=stem, desc="", inst="", genres=[], rtr=False,
        availability="free", reqs=[], runtime=[], arch=["aarch64"], min_glibc="",
        items=[f"{stem}.sh", stem], url="", md5="", size=1024, screenshot="", cover="",
        date_updated=""
    )
    base.update(kw)
    return core.Port(**base)


def test_transfer_port_wireless_with_runtime_skip(tmp_path, mock_remote_env):
    """Test transfer_port_wireless uploads files and skips existing MD5-matched runtimes."""
    conn, ssh, sftp = mock_remote_env

    # Setup muOS probe
    sftp.files["/opt/muos/config/system/version"] = b"2606.0\n"
    sftp.files["/opt/muos/device/config/board/name"] = b"rg35xx-sp\n"
    sftp.dirs.add("/mnt/mmc")
    sftp.dirs.add("/mnt/sdcard")
    probe = wireless.probe_remote(conn)

    # Create dummy port zip
    port_zip_path = tmp_path / "testport.zip"
    with zipfile.ZipFile(port_zip_path, "w") as zf:
        zf.writestr("testport/data.bin", b"DATA_BIN")
        zf.writestr("testport.sh", b"#!/bin/bash\n./data.bin\n")

    port = make_port(
        "testport",
        title="Test Port",
        url="https://example.com/testport.zip",
        md5=core.file_md5(port_zip_path),
        items=["testport", "testport.sh"],
        runtime=["test-runtime"],
    )

    dev = next(d for d in DEVICES if d.id == "rg35xxsp")

    # Set up runtime: exists on handheld with matching MD5
    runtime_content = b"RUNTIME_SQUASHFS_CONTENT"
    import hashlib
    runtime_md5 = hashlib.md5(runtime_content).hexdigest()

    # Pre-populate runtime on device
    remote_rt_path = "/mnt/mmc/MUOS/PortMaster/libs/test-runtime.squashfs"
    sftp.files[remote_rt_path] = runtime_content

    utils = {
        "test-runtime": {
            "url": "https://example.com/test-runtime.squashfs",
            "md5": runtime_md5,
        }
    }

    logs = []
    with patch("core.download", return_value=port_zip_path):
        wireless.transfer_port_wireless(
            conn=conn,
            port=port,
            probe=probe,
            dev=dev,
            utils=utils,
            log=logs.append,
        )

    # 1. Port files should be uploaded to /mnt/sdcard
    assert "/mnt/sdcard/ports/testport/data.bin" in sftp.files
    assert sftp.files["/mnt/sdcard/ports/testport/data.bin"] == b"DATA_BIN"

    # 2. Script should be uploaded to /mnt/sdcard/ROMS/Ports/testport.sh
    assert "/mnt/sdcard/ROMS/Ports/testport.sh" in sftp.files

    # 3. chmod +x should have been executed on the script
    assert any("chmod +x '/mnt/sdcard/ROMS/Ports/testport.sh'" in cmd for cmd in ssh.executed_commands)

    # 4. Runtime should be SKIPPED because MD5 matches
    assert any("already present (MD5 match) — skipped" in l for l in logs)


def test_finalize_wireless_session_hooks(mock_remote_env):
    """Test sync and muOS catalogue refresh commands."""
    conn, ssh, _ = mock_remote_env
    logs = []

    wireless.finalize_wireless_session(conn, "muos", logs.append)
    assert "sync" in ssh.executed_commands
    assert any("api/catalogue/refresh" in cmd for cmd in ssh.executed_commands)


def test_keepalive_set_on_connect():
    """Verify keepalive is set to 15s on paramiko transport."""
    with patch("paramiko.SSHClient") as mock_cls:
        client = mock_cls.return_value
        transport = MagicMock()
        client.get_transport.return_value = transport
        conn = wireless.WirelessConnection()
        conn.connect("192.168.1.50", port=22, username="root", password="pw", keepalive=15)
        transport.set_keepalive.assert_called_with(15)


def test_size_suggest_card_reader_constant():
    """Verify size recommendation constant is 1.5 GB."""
    assert wireless.SIZE_SUGGEST_CARD_READER == int(1.5 * 1024 * 1024 * 1024)


def test_discover_handheld_hosts_mock():
    """Verify discover_handheld_hosts resolves reachable muOS mDNS names."""
    def mock_gethostbyname(host):
        if host == "muos.local":
            return "192.168.1.105"
        raise OSError("Host not found")

    with patch("socket.gethostbyname", side_effect=mock_gethostbyname):
        hits = wireless.discover_handheld_hosts(timeout=0.1)
        assert len(hits) == 1
        hosts = [h[1] for h in hits]
        ips = [h[2] for h in hits]
        assert "muos.local" in hosts
        assert "192.168.1.105" in ips


def test_transfer_port_uploads_missing_runtime(tmp_path, mock_remote_env):
    """Verify that when a required runtime is NOT on device, it is downloaded and uploaded."""
    conn, ssh, sftp = mock_remote_env

    sftp.files["/opt/muos/config/system/version"] = b"2606.0\n"
    sftp.files["/opt/muos/device/config/board/name"] = b"rg35xx-sp\n"
    sftp.dirs.add("/mnt/mmc")
    probe = wireless.probe_remote(conn)

    port_zip_path = tmp_path / "port2.zip"
    with zipfile.ZipFile(port_zip_path, "w") as zf:
        zf.writestr("port2/game.bin", b"GAME_BIN")
        zf.writestr("port2.sh", b"#!/bin/bash\n./game.bin\n")

    port = make_port(
        "port2",
        title="Port 2",
        items=["port2", "port2.sh"],
        runtime=["missing-runtime"],
    )

    dev = next(d for d in DEVICES if d.id == "rg35xxsp")

    runtime_zip = tmp_path / "missing-runtime.squashfs"
    runtime_zip.write_bytes(b"SQUASHFS_DATA")

    utils = {
        "missing-runtime": {
            "url": "https://example.com/missing-runtime.squashfs",
            "md5": None,
        }
    }

    logs = []
    with patch("core.download", side_effect=[port_zip_path, runtime_zip]):
        wireless.transfer_port_wireless(
            conn=conn,
            port=port,
            probe=probe,
            dev=dev,
            utils=utils,
            log=logs.append,
        )

    # Runtime should be uploaded to libs_dir
    remote_rt = f"{probe.layout.libs_dir}/missing-runtime.squashfs"
    assert remote_rt in sftp.files
    assert sftp.files[remote_rt] == b"SQUASHFS_DATA"
    assert any("Uploading runtime" in l for l in logs)


def test_wifi_drop_error_handling(tmp_path, mock_remote_env):
    """Verify error raised when socket or SFTP drops during put."""
    conn, ssh, sftp = mock_remote_env
    sftp.files["/opt/muos/config/system/version"] = b"2606.0\n"
    sftp.dirs.add("/mnt/mmc")
    probe = wireless.probe_remote(conn)

    port_zip_path = tmp_path / "dropped.zip"
    with zipfile.ZipFile(port_zip_path, "w") as zf:
        zf.writestr("dropped/file.dat", b"123")
        zf.writestr("dropped.sh", b"#!/bin/sh")

    port = make_port("dropped", items=["dropped", "dropped.sh"])
    dev = next(d for d in DEVICES if d.id == "rg35xxsp")

    # Simulate socket drop during put
    def fail_put(*args, **kwargs):
        raise OSError("Connection reset by peer (Wi-Fi dropped)")

    sftp.put = fail_put

    with patch("core.download", return_value=port_zip_path):
        with pytest.raises(OSError, match="Connection reset by peer"):
            wireless.transfer_port_wireless(
                conn=conn,
                port=port,
                probe=probe,
                dev=dev,
                utils={},
                log=lambda _: None,
            )


def test_wireless_target_properties(mock_remote_env):
    """Verify WirelessTarget satisfies Target interface for core functions."""
    conn, _, sftp = mock_remote_env
    sftp.files["/opt/muos/config/system/version"] = b"2606.0\n"
    sftp.dirs.add("/mnt/mmc")
    sftp.dirs.add("/mnt/sdcard")
    probe = wireless.probe_remote(conn)

    wt = wireless.WirelessTarget(MUOS, conn, probe)
    assert wt.is_wireless is True
    assert str(wt.ports_dir) == "/mnt/sdcard/ports"
    assert str(wt.scripts_dir) == "/mnt/sdcard/ROMS/Ports"
    assert str(wt.libs_dir) == "/mnt/mmc/MUOS/PortMaster/libs"
    assert str(wt.media_dir) == "/mnt/mmc/MUOS/info/catalogue/External - Ports/box"
    assert wt.pak_installed() is True
