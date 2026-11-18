"""Tests for PortPicker wireless transfer module (SSH/SFTP).

Trimmed for muOS on Anbernic H700 / supported handhelds via Secure File Transfer (SFTP).
Tests:
- SFTP virtual root detection (/Primary Storage, /Internal, /Catalogue)
- Dual SD (/Secondary Storage) vs Single SD (/Primary Storage)
- Path mapping /mnt/mmc → /Primary Storage, /mnt/sdcard → /Secondary Storage, catalogue → /Catalogue
- Forbidden paths protection (/Internal, /Boot, /tmp, /root)
- Pure SFTP with zero exec commands (fake SFTP-only server where exec raises)
- Default credentials (muos:muos on port 2022) and port 22 refused hint
- TOFU host-key verification, re-trust on service restart, and DPAPI
"""
from __future__ import annotations

import io
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import paramiko
import pytest

import core
from profiles import CFWS, DEVICES, MUOS, Device
import wireless


# --------------------------------------------------------------------------
# Mock SFTP and SSH Infrastructure
# --------------------------------------------------------------------------

class MockSFTPStat:
    def __init__(self, size: int = 1024, is_dir: bool = False, filename: str = ""):
        self.st_size = size
        self.st_mode = stat.S_IFDIR | 0o755 if is_dir else stat.S_IFREG | 0o644
        self.filename = filename


class MockSFTPClient:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {
            "/",
            "/Primary Storage",
            "/Secondary Storage",
            "/Internal",
            "/Boot",
            "/Catalogue",
            "/Collections",
            "/History",
            "/mnt",
            "/opt",
        }
        self.put_calls: list[tuple[str, str]] = []
        self.rename_calls: list[tuple[str, str]] = []
        self.removed_files: list[str] = []
        self.chmod_calls: list[tuple[str, int]] = []

    def listdir(self, remote_path: str) -> list[str]:
        rp = remote_path.rstrip("/")
        prefix = rp + "/"
        found = set()
        for d in sorted(self.dirs):
            if d.startswith(prefix):
                sub = d[len(prefix):].split("/")[0]
                if sub:
                    found.add(sub)
        for f in sorted(self.files):
            if f.startswith(prefix):
                sub = f[len(prefix):].split("/")[0]
                if sub:
                    found.add(sub)
        return sorted(list(found))

    def listdir_attr(self, remote_path: str) -> list[MockSFTPStat]:
        rp = remote_path.rstrip("/")
        entries = []
        prefix = rp + "/"
        found = set()
        for d in sorted(self.dirs):
            if d.startswith(prefix):
                sub = d[len(prefix):].split("/")[0]
                if sub and sub not in found:
                    found.add(sub)
                    entries.append(MockSFTPStat(0, is_dir=True, filename=sub))
        for f in sorted(self.files):
            if f.startswith(prefix):
                sub = f[len(prefix):].split("/")[0]
                if sub and sub not in found:
                    found.add(sub)
                    is_d = "/" in f[len(prefix):]
                    entries.append(MockSFTPStat(len(self.files[f]) if not is_d else 0, is_dir=is_d, filename=sub))
        return entries

    def rmdir(self, remote_path: str):
        rp = remote_path.rstrip("/")
        self.dirs.discard(rp)

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
            bio = io.BytesIO()
            orig_close = bio.close

            def on_close():
                self.files[rp] = bio.getvalue()
                orig_close()

            bio.close = on_close
            return bio
        else:
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

    def chmod(self, remote_path: str, mode: int):
        self.chmod_calls.append((remote_path, mode))

    def _request(self, cmd: int, *args):
        # Handle OpenSSH statvfs extension
        from paramiko.sftp_client import CMD_EXTENDED
        if cmd == CMD_EXTENDED and args and args[0] == "statvfs@openssh.com":
            msg = paramiko.Message()
            msg.add_int64(4096)      # f_bsize
            msg.add_int64(4096)      # f_frsize
            msg.add_int64(15000000)  # f_blocks (~60 GB)
            msg.add_int64(12000000)  # f_bfree
            msg.add_int64(12000000)  # f_bavail (~48 GB free)
            msg.add_int64(1000000)   # f_files
            msg.add_int64(900000)    # f_ffree
            msg.add_int64(900000)    # f_favail
            msg.add_int64(0)         # f_fsid
            msg.add_int64(0)         # f_flag
            msg.add_int64(255)       # f_namemax
            return 201, paramiko.Message(msg.asbytes())
        return 0, None

    def close(self):
        pass


class MockChannel:
    def __init__(self, exit_status: int = 0):
        self._exit_status = exit_status

    def recv_exit_status(self) -> int:
        return self._exit_status


class MockSSHClient:
    def __init__(self, sftp_client: MockSFTPClient, allow_exec: bool = False):
        self.sftp = sftp_client
        self.allow_exec = allow_exec
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
        if not self.allow_exec:
            raise paramiko.SSHException("Channel open failed: exec channel disabled (SFTP-only server)")
        self.executed_commands.append(cmd)
        stdout_bio = io.BytesIO()
        stderr_bio = io.BytesIO()
        channel = MockChannel(0)
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
    ssh = MockSSHClient(sftp, allow_exec=False)
    conn = wireless.WirelessConnection()
    conn.client = ssh
    conn.sftp = sftp
    conn.host = "192.168.1.100"
    conn.port = wireless.DEFAULT_WIRELESS_PORT
    conn.username = "muos"
    return conn, ssh, sftp


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_device_matching_from_strings():
    """Verify raw strings from handhelds match correct Device entries."""
    assert wireless.match_device_from_string("rg35xx-sp").id == "h700"
    assert wireless.match_device_from_string("Anbernic RG35XX SP").id == "h700"
    assert wireless.match_device_from_string("tg5040").id == "brickpro"
    assert wireless.match_device_from_string("TrimUI Brick Pro").id == "brickpro"
    assert wireless.match_device_from_string("TrimUI Smart Pro").id == "smartpro"
    assert wireless.match_device_from_string("TrimUI Brick").id == "brick"
    assert wireless.match_device_from_string("RG40XX H").id == "h700"
    assert wireless.match_device_from_string("rg35xx-h").id == "h700"
    assert wireless.match_device_from_string("RG35XX Plus").id == "h700"
    assert wireless.match_device_from_string("r36s").id == "r36s"
    assert wireless.match_device_from_string("rg353v").id == "rg353v"
    assert wireless.match_device_from_string("H700").id == "h700"
    assert wireless.match_device_from_string("H700-sticks").id == "h700"


def test_dpapi_credential_save_and_retrieve(tmp_path):
    """Test DPAPI credential store round-trip."""
    test_auth_file = tmp_path / "wireless_auth.dat"
    with patch("wireless.AUTH_FILE", test_auth_file):
        wireless.save_saved_credential("muos.local", "muos", "secret123")
        res = wireless.get_saved_credential("muos.local")
        assert res == ("muos", "secret123")

        res_upper = wireless.get_saved_credential("MUOS.LOCAL")
        assert res_upper == ("muos", "secret123")

        assert wireless.get_saved_credential("unknown.local") is None


def test_probe_muos_sftp_dual_sd(mock_remote_env):
    """Test muOS SFTP probing with virtual root (/Primary Storage and /Secondary Storage)."""
    conn, ssh, sftp = mock_remote_env
    sftp.files["/Internal/version"] = b"2606.0\n"
    sftp.files["/Primary Storage/MUOS/PortMaster/version"] = b"2024.11.01\n"
    sftp.dirs.add("/Primary Storage")
    sftp.dirs.add("/Secondary Storage")
    sftp.dirs.add("/Internal")
    sftp.dirs.add("/Catalogue")

    dev_hint = next(d for d in DEVICES if d.id == "h700")
    probe = wireless.probe_remote(conn, device_hint=dev_hint)

    assert probe.cfw_id == "muos"
    assert probe.cfw_version == "2606.0"
    assert probe.device_id == "h700"
    assert probe.pm_version == "2024.11.01"
    assert probe.layout.dual_sd is True
    # SD2 (Secondary Storage) is used for games, SD1 (Primary Storage) for runtimes, Catalogue for box art
    assert probe.layout.ports_dir == "/Secondary Storage/ports"
    assert probe.layout.scripts_dir == "/Secondary Storage/ROMS/Ports"
    assert probe.layout.libs_dir == "/Primary Storage/MUOS/PortMaster/libs"
    assert probe.layout.media_dir == "/Catalogue/External - Ports/box"
    # Zero exec commands executed!
    assert len(ssh.executed_commands) == 0


def test_probe_muos_sftp_single_sd(mock_remote_env):
    """Test muOS SFTP probing with single SD (/Primary Storage only)."""
    conn, ssh, sftp = mock_remote_env
    sftp.files["/Internal/version"] = b"2606.1\n"
    sftp.dirs.discard("/Secondary Storage")

    dev_hint = next(d for d in DEVICES if d.id == "h700")
    probe = wireless.probe_remote(conn, device_hint=dev_hint)

    assert probe.cfw_id == "muos"
    assert probe.layout.dual_sd is False
    assert probe.layout.ports_dir == "/Primary Storage/ports"
    assert probe.layout.scripts_dir == "/Primary Storage/ROMS/Ports"
    assert probe.layout.libs_dir == "/Primary Storage/MUOS/PortMaster/libs"
    assert probe.layout.media_dir == "/Catalogue/External - Ports/box"
    assert len(ssh.executed_commands) == 0


def test_translate_handheld_path_to_sftp():
    """Verify handheld path mapping to muOS SFTP virtual tree."""
    # SD1 mapping
    assert wireless.translate_handheld_path_to_sftp("/mnt/mmc/ports") == "/Primary Storage/ports"
    assert wireless.translate_handheld_path_to_sftp("/mnt/mmc/MUOS/PortMaster/libs") == "/Primary Storage/MUOS/PortMaster/libs"
    # SD2 mapping
    assert wireless.translate_handheld_path_to_sftp("/mnt/sdcard/ports") == "/Secondary Storage/ports"
    assert wireless.translate_handheld_path_to_sftp("/mnt/sdcard/ROMS/Ports") == "/Secondary Storage/ROMS/Ports"
    # Catalogue mapping
    assert (
        wireless.translate_handheld_path_to_sftp("/mnt/mmc/MUOS/info/catalogue/External - Ports/box")
        == "/Catalogue/External - Ports/box"
    )
    assert wireless.translate_handheld_path_to_sftp("/Catalogue/External - Ports/box") == "/Catalogue/External - Ports/box"


def test_validate_sftp_write_path_rejects_forbidden(mock_remote_env):
    """Verify validation strictly rejects /Internal, /Boot, /tmp, and paths outside allowed roots."""
    conn, _, _ = mock_remote_env
    dev_hint = next(d for d in DEVICES if d.id == "h700")
    probe = wireless.probe_remote(conn, device_hint=dev_hint)
    layout = probe.layout

    # Permitted paths
    wireless.validate_sftp_write_path("/Secondary Storage/ports/game/data.bin", layout)
    wireless.validate_sftp_write_path("/Secondary Storage/ROMS/Ports/game.sh", layout)
    wireless.validate_sftp_write_path("/Primary Storage/MUOS/PortMaster/libs/runtime.squashfs", layout)
    wireless.validate_sftp_write_path("/Catalogue/External - Ports/box/game.png", layout)

    # Forbidden paths: /Internal, /Boot
    with pytest.raises(ValueError, match="forbidden"):
        wireless.validate_sftp_write_path("/Internal/muos/system.bin", layout)

    with pytest.raises(ValueError, match="forbidden"):
        wireless.validate_sftp_write_path("/Boot/uImage", layout)

    with pytest.raises(ValueError, match="forbidden"):
        wireless.validate_sftp_write_path("/tmp/exploit", layout)

    with pytest.raises(ValueError, match="forbidden"):
        wireless.validate_sftp_write_path("/root/.ssh/authorized_keys", layout)

    # Outside allowed roots
    with pytest.raises(ValueError, match="outside allowed target roots"):
        wireless.validate_sftp_write_path("/Collections/Favorites", layout)


def test_probe_rejects_non_muos(mock_remote_env):
    """Verify that probe raises ValueError if remote is not muOS."""
    conn, ssh, sftp = mock_remote_env
    sftp.dirs.clear()
    sftp.dirs.add("/")
    with pytest.raises(ValueError, match="supports muOS and KNULLI"):
        wireless.probe_remote(conn)


def test_probe_default_password_warning(mock_remote_env):
    """Verify warning message when default password was used."""
    conn, ssh, sftp = mock_remote_env
    conn.is_default_password = True
    conn.username = "muos"

    probe = wireless.probe_remote(conn)
    assert probe.is_default_password is True
    assert probe.warning is not None
    assert "Default password (muos) was used" in probe.warning


def test_reject_dangerous_mounts(mock_remote_env):
    """Verify that layout paths pointing to /tmp or rootfs raise ValueError."""
    conn, ssh, sftp = mock_remote_env
    with patch("wireless.RemoteLayout") as mock_rl:
        mock_rl.return_value = wireless.RemoteLayout(
            ports_dir="/tmp/ports",
            scripts_dir="/Secondary Storage/ROMS/Ports",
            libs_dir="/Primary Storage/MUOS/libs",
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

    remote_dest = "/Secondary Storage/ports/game/game.exe"
    conn.put_file_atomic(local_file, remote_dest)

    assert len(sftp.put_calls) == 1
    assert sftp.put_calls[0][1] == remote_dest + ".part"
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


def test_fake_sftp_only_server_exec_raises(tmp_path, mock_remote_env):
    """Verify entire workflow succeeds with a fake SFTP-only server where exec raises SSHException."""
    conn, ssh, sftp = mock_remote_env
    # Confirm exec_command strictly raises
    ssh.allow_exec = False
    with pytest.raises(paramiko.SSHException, match="exec channel disabled"):
        ssh.exec_command("echo test")

    dev = next(d for d in DEVICES if d.id == "h700")
    probe = wireless.probe_remote(conn, device_hint=dev)

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

    # Pre-populate runtime on device with matching MD5
    runtime_content = b"RUNTIME_SQUASHFS_CONTENT"
    import hashlib
    runtime_md5 = hashlib.md5(runtime_content).hexdigest()
    remote_rt_path = "/Primary Storage/MUOS/PortMaster/libs/test-runtime.squashfs"
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
            clean=True,
        )

    # 1. Port files uploaded to /Secondary Storage
    assert "/Secondary Storage/ports/testport/data.bin" in sftp.files
    # 2. Script uploaded to /Secondary Storage/ROMS/Ports/testport.sh
    assert "/Secondary Storage/ROMS/Ports/testport.sh" in sftp.files
    # 3. chmod executed via pure SFTP chmod
    assert ("/Secondary Storage/ROMS/Ports/testport.sh", 0o755) in sftp.chmod_calls
    # 4. Runtime skipped by MD5 match over SFTP
    assert any("already present (MD5 match) — skipped" in l for l in logs)

    # Finalize session without shell sync
    wireless.finalize_wireless_session(conn, "muos", logs.append)
    assert any("Ports menu" in l for l in logs)

    # ZERO shell exec commands executed throughout the entire process!
    assert len(ssh.executed_commands) == 0


def test_muos_default_credentials_2022():
    """Verify muOS default credentials include muos:muos on port 2022."""
    muos_creds = wireless.CFW_DEFAULT_CREDENTIALS.get("muos", [])
    assert ("muos", "muos", 2022) in muos_creds
    assert wireless.DEFAULT_WIRELESS_PORT == 2022


def test_port_22_refused_error_hint():
    """Verify hint on port 22 connection refused."""
    with patch("paramiko.SSHClient") as mock_cls:
        client = mock_cls.return_value
        import socket
        err = socket.error(socket.errno.ECONNREFUSED, "Connection refused")
        client.connect.side_effect = err
        conn = wireless.WirelessConnection()
        with pytest.raises(ConnectionError, match="turn on Secure File Transfer"):
            conn.connect("192.168.1.10", port=22, username="root", password="pw")


def test_tofu_host_key_retrust(tmp_path, monkeypatch):
    """Verify Trust-On-First-Use (TOFU) host key persistence and re-trust on service restart."""
    known_hosts = tmp_path / "known_hosts"
    auth_file = tmp_path / "wireless_auth.dat"
    monkeypatch.setattr(wireless, "KNOWN_HOSTS_FILE", known_hosts)
    monkeypatch.setattr(wireless, "AUTH_FILE", auth_file)

    key1 = paramiko.RSAKey.generate(1024)
    key2 = paramiko.RSAKey.generate(1024)

    # 1. Missing key policy adds key on first connect
    client = paramiko.SSHClient()
    info = {"added": False}
    policy = wireless.TrustOnFirstUsePolicy(known_hosts, on_new_key=lambda h, k: info.update({"added": True}))
    policy.missing_host_key(client, "[muos.local]:2022", key1)

    assert info["added"] is True
    assert known_hosts.exists()
    hk = paramiko.HostKeys(str(known_hosts))
    assert "[muos.local]:2022" in hk

    # 2. Key mismatch without retrust raises ConnectionError with re-trust hint
    with patch("paramiko.SSHClient.connect", side_effect=paramiko.BadHostKeyException("[muos.local]:2022", key2, key1)):
        conn = wireless.WirelessConnection()
        with pytest.raises(ConnectionError, match="Trust New Key & Reconnect"):
            conn.connect("muos.local", port=2022, username="muos", password="pw", retrust=False)

    # 3. Retrust=True updates the host key in known_hosts and reconnects
    with patch("paramiko.SSHClient.connect") as mock_conn, patch("paramiko.SSHClient.open_sftp"):
        # First call raises BadHostKeyException, second succeeds
        mock_conn.side_effect = [paramiko.BadHostKeyException("[muos.local]:2022", key2, key1), None]
        conn = wireless.WirelessConnection()
        conn.connect("muos.local", port=2022, username="muos", password="pw", retrust=True)
        assert mock_conn.call_count == 2
        # Verify key was updated in known_hosts
        hk_updated = paramiko.HostKeys(str(known_hosts))
        assert "[muos.local]:2022" in hk_updated


def test_wireless_target_properties(mock_remote_env):
    """Verify WirelessTarget satisfies Target interface for core functions."""
    conn, _, _ = mock_remote_env
    dev = next(d for d in DEVICES if d.id == "h700")
    probe = wireless.probe_remote(conn, device_hint=dev)

    wt = wireless.WirelessTarget(MUOS, conn, probe)
    assert wt.is_wireless is True
    assert wt.ports_dir.as_posix() == "/Secondary Storage/ports"
    assert wt.scripts_dir.as_posix() == "/Secondary Storage/ROMS/Ports"
    assert wt.libs_dir.as_posix() == "/Primary Storage/MUOS/PortMaster/libs"
    assert wt.media_dir.as_posix() == "/Catalogue/External - Ports/box"
    assert wt.pak_installed() is True


def test_clean_port_dir_safety(tmp_path, mock_remote_env):
    """Verify clean=True uses SFTP removal and rejects unsafe dir names (path traversal)."""
    conn, ssh, sftp = mock_remote_env
    sftp.dirs.add("/Secondary Storage/ports")
    sftp.dirs.add("/Secondary Storage/ports/safegame")
    sftp.files["/Secondary Storage/ports/safegame/old.bin"] = b"old"
    dev = next(d for d in DEVICES if d.id == "h700")
    probe = wireless.probe_remote(conn, device_hint=dev)

    port = make_port(
        "testsafe",
        items=["testsafe.sh", "safegame", "", "/root", "../outside", "escape/sub"],
    )

    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("testsafe.sh", "#!/bin/sh\n")
        zf.writestr("safegame/game.bin", "data")
    zip_data = zip_bytes.getvalue()

    logs = []
    with patch("core.download", return_value=tmp_path / "testsafe.zip"):
        (tmp_path / "testsafe.zip").write_bytes(zip_data)
        wireless.transfer_port_wireless(
            conn=conn,
            port=port,
            probe=probe,
            dev=dev,
            utils={},
            log=logs.append,
            clean=True,
        )

    assert any("Skipping unsafe port directory" in l for l in logs)
    # Zero exec calls
    assert len(ssh.executed_commands) == 0
    # Safe directory should have been cleaned via pure SFTP
    assert "/Secondary Storage/ports/safegame/old.bin" not in sftp.files


def test_df_mount_and_remote_md5_pure_sftp(mock_remote_env):
    """Directly test df_mount and remote_md5 over pure SFTP without shell exec."""
    conn, ssh, sftp = mock_remote_env
    # 1. df_mount uses statvfs
    res = conn.df_mount("/Primary Storage")
    assert res is not None
    free_b, tot_b = res
    assert free_b == 12000000 * 4096
    assert tot_b == 15000000 * 4096

    # 2. remote_md5 streams over SFTP
    sftp.files["/Primary Storage/data.txt"] = b"HELLO_SFTP"
    import hashlib
    expected_hash = hashlib.md5(b"HELLO_SFTP").hexdigest()
    assert conn.remote_md5("/Primary Storage/data.txt") == expected_hash

    # 3. chmod_x calls sftp.chmod
    conn.chmod_x("/Primary Storage/test.sh")
    assert ("/Primary Storage/test.sh", 0o755) in sftp.chmod_calls

    # Zero exec commands
    assert len(ssh.executed_commands) == 0


def test_connect_with_credentials_or_defaults_muos():
    """Verify connect_with_credentials_or_defaults tries muos/muos on port 2022 first."""
    with patch("paramiko.SSHClient.connect") as mock_conn, patch("paramiko.SSHClient.open_sftp"):
        conn = wireless.connect_with_credentials_or_defaults("192.168.1.55")
        assert mock_conn.called
        call_kwargs = mock_conn.call_args[1]
        assert call_kwargs["hostname"] == "192.168.1.55"
        assert call_kwargs["port"] == 2022
        assert call_kwargs["username"] == "muos"
        assert call_kwargs["password"] == "muos"
        assert conn.is_default_password is True



def test_reconnects_when_session_dropped_before_upload(mock_remote_env, monkeypatch):
    # Hardware test 2026-11-18: the session dropped while Steam downloaded Stardew ("Server connection dropped")
    conn, ssh, sftp = mock_remote_env
    conn._reconnect_args = dict(host="192.168.1.100", port=2022, username="muos", password="muos", timeout=1, keepalive=15)
    dead = MagicMock()
    dead.is_active.return_value = False
    monkeypatch.setattr(ssh, "get_transport", lambda: dead)
    calls = []

    def fake_connect(**kw):
        calls.append(kw["host"])
        conn.client = MockSSHClient(sftp)
        conn.sftp = sftp

    monkeypatch.setattr(conn, "connect", fake_connect)
    logs = []
    conn.ensure_connected(logs.append)
    assert calls == ["192.168.1.100"] and conn.is_alive()
    assert any("reconnecting" in l for l in logs)


def test_upload_retries_after_drop(mock_remote_env, monkeypatch, tmp_path):
    conn, ssh, sftp = mock_remote_env
    conn._reconnect_args = dict(host="h", port=2022, username="muos", password="muos", timeout=1, keepalive=15)
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 10)
    real_once = conn._put_file_atomic_once
    state = {"n": 0}

    def flaky(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise paramiko.SSHException("Server connection dropped: ")
        return real_once(*a, **kw)

    monkeypatch.setattr(conn, "_put_file_atomic_once", flaky)
    monkeypatch.setattr(conn, "ensure_connected", lambda log=None: None)
    conn.put_file_atomic(f, "/Secondary Storage/ports/x/a.bin")
    assert state["n"] == 2
