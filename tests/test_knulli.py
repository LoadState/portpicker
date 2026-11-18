"""T-058: one Anbernic H700 card, muOS + KNULLI (SD card and Wi-Fi), KNULLI game list."""
from __future__ import annotations

from pathlib import Path

import pytest

import core
import wireless
from profiles import DEVICES, KNULLI, MUOS
from test_wireless import MockSFTPClient, MockSSHClient

H700 = next(d for d in DEVICES if d.id == "h700")

GAMEINFO = """<?xml version='1.0' encoding='utf-8'?>
<gameList>
  <game>
    <path>./OpenFodder.sh</path>
    <name>Open Fodder</name>
    <desc>Cannon Fodder engine</desc>
    <image>./openfodder/cover.png</image>
  </game>
</gameList>
"""


def knulli_env(version: bytes | None = b"Scarab 20250701 (knulli)\n"):
    sftp = MockSFTPClient()
    sftp.dirs = {"/", "/userdata", "/userdata/roms", "/userdata/roms/ports", "/userdata/system", "/usr",
                 "/usr/share", "/usr/share/batocera"}
    if version is not None:
        sftp.files["/usr/share/batocera/batocera.version"] = version
    ssh = MockSSHClient(sftp, allow_exec=False)
    conn = wireless.WirelessConnection()
    conn.client, conn.sftp, conn.host, conn.port, conn.username = ssh, sftp, "KNULLI", 22, "root"
    return conn, ssh, sftp


def test_one_h700_card_muos_and_knulli():
    assert [d.id for d in DEVICES if d.enabled] == ["h700"]
    assert H700.cfws == ["muos", "knulli"]
    assert core.screen_resolution(H700) == (640, 480)  # box art sized for the smallest H700 screen
    for model in ("RG35XX SP", "RG35XX H", "RG40XX V", "RG CubeXX", "RG34XX"):
        assert wireless.match_device_from_string(model).id == "h700"


def test_no_stick_filter():
    p = core.Port(name="x.zip", title="X", desc="", inst="", genres=[], rtr=True, availability="free",
                  reqs=["analog_2"], runtime=[], arch=["aarch64"], min_glibc="", items=["X.sh", "x"], url="",
                  md5="", size=1, screenshot="", cover="", date_updated="")
    core.classify([p], H700, MUOS, {"broken": set(), "fixed": set(), "issues": set()})
    assert p.tier != "incompatible"


def test_knulli_wifi_defaults():
    assert wireless.default_port_for("knulli") == 22
    assert wireless.default_port_for("muos") == 2022
    assert wireless.default_user_for("knulli") == ("root", "linux")
    assert ("root", "linux", 22) in wireless.CFW_DEFAULT_CREDENTIALS["knulli"]


def test_probe_knulli_layout():
    conn, ssh, _ = knulli_env()
    probe = wireless.probe_remote(conn, device_hint=H700)
    assert probe.cfw_id == "knulli" and probe.cfw_name == KNULLI.name
    assert probe.cfw_version == "20250701"
    assert probe.layout.ports_dir == probe.layout.scripts_dir == "/userdata/roms/ports"
    assert probe.layout.libs_dir == "/userdata/system/.local/share/PortMaster/libs"
    assert probe.layout.media_dir == ""
    assert probe.layout.dual_sd is False
    assert ssh.executed_commands == []
    assert core.check_cfw_version_minimum(KNULLI, probe.cfw_version)[0] is True


def test_probe_knulli_unknown_version_does_not_block():
    conn, _, _ = knulli_env(version=b"41-dev\n")
    probe = wireless.probe_remote(conn, device_hint=H700)
    assert probe.cfw_version == ""
    assert core.check_cfw_version_minimum(KNULLI, probe.cfw_version)[0] is True


def test_merge_gameinfo_adds_then_updates():
    first = core.merge_gameinfo(None, GAMEINFO)
    assert "<name>Open Fodder</name>" in first and "./openfodder/cover.png" in first
    other = "<gameList><game><path>./Other.sh</path><name>Other</name></game></gameList>"
    both = core.merge_gameinfo(first, other)
    again = core.merge_gameinfo(both, GAMEINFO.replace("Cannon Fodder engine", "new text"))
    assert again.count("<path>./OpenFodder.sh</path>") == 1
    assert "new text" in again and "<name>Other</name>" in again
    with pytest.raises(ValueError):
        core.merge_gameinfo("<gameList><game>", GAMEINFO)


def test_update_gamelist_file_on_sd_card(tmp_path: Path):
    ports = tmp_path / "roms" / "ports"
    (ports / "openfodder").mkdir(parents=True)
    (ports / "openfodder" / "gameinfo.xml").write_text(GAMEINFO, encoding="utf-8")
    (ports / "gamelist.xml").write_text("<gameList><game><path>./Old.sh</path></game></gameList>", encoding="utf-8")
    logs: list[str] = []
    core.update_gamelist_file(ports, ["openfodder"], logs.append)
    gl = (ports / "gamelist.xml").read_text(encoding="utf-8")
    assert "Open Fodder" in gl and "./Old.sh" in gl
    assert (ports / "gamelist.xml.bak").is_file()
    assert core.uses_gamelist(KNULLI) and not core.uses_gamelist(MUOS)


def test_broken_gamelist_is_left_alone(tmp_path: Path):
    ports = tmp_path / "ports"
    (ports / "g").mkdir(parents=True)
    (ports / "g" / "gameinfo.xml").write_text(GAMEINFO, encoding="utf-8")
    (ports / "gamelist.xml").write_text("<gameList><game>", encoding="utf-8")
    logs: list[str] = []
    core.update_gamelist_file(ports, ["g"], logs.append)
    assert (ports / "gamelist.xml").read_text(encoding="utf-8") == "<gameList><game>"
    assert any("Could not update" in l for l in logs)


def test_update_remote_gamelist(tmp_path: Path):
    conn, ssh, sftp = knulli_env()
    probe = wireless.probe_remote(conn, device_hint=H700)
    stage = tmp_path / "stage"
    (stage / "openfodder").mkdir(parents=True)
    (stage / "openfodder" / "gameinfo.xml").write_text(GAMEINFO, encoding="utf-8")
    sftp.files["/userdata/roms/ports/gamelist.xml"] = b"<gameList><game><path>./Old.sh</path></game></gameList>"
    logs: list[str] = []
    wireless.update_remote_gamelist(conn, probe.layout, stage, ["openfodder"], logs.append)
    gl = sftp.files["/userdata/roms/ports/gamelist.xml"].decode()
    assert "Open Fodder" in gl and "./Old.sh" in gl
    assert "/userdata/roms/ports/gamelist.xml.bak" in sftp.files
    assert ssh.executed_commands == []
