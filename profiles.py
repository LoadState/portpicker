"""Device and CFW profiles. Add new devices/CFWs here."""
from __future__ import annotations

from dataclasses import dataclass, field
from i18n import t


@dataclass
class CFW:
    id: str
    name: str
    blurb: str
    enabled: bool = True
    sd_markers: list[str] = field(default_factory=list)   # paths that identify the SD card
    sd_alt_markers: list[list[str]] = field(default_factory=list)  # other accepted marker sets (e.g. SD2)
    sd_exclude: list[str] = field(default_factory=list)   # paths that mean "this is a different CFW"
    scripts_dir: str = ""     # where .sh launchers appear in the menu
    ports_dir: str = ""       # where port folders (and copies of .sh) live
    libs_dir: str = ""        # PortMaster runtimes (*.squashfs)
    media_dir: str = ""       # box art for the launcher
    pak_marker: str = ""      # file proving PortMaster is installed
    pak_url: str = ""
    status_wiki: str = ""     # live list of ports known broken on this CFW
    optional_dirs: bool = False  # libs/media only used when their top folder exists (SD2 cards)
    builtin_pm: bool = False  # PortMaster ships with the CFW (nothing to install)
    after_install: list[str] = field(default_factory=list)  # tips shown when done
    max_glibc: str | None = None  # glibc of the CFW rootfs; None = use the device value, "" = no limit
    min_cfw_version: str | None = None  # minimum supported CFW release
    accent: str = "#7c5cff"


@dataclass
class Device:
    id: str
    name: str
    chip: str
    ram: str
    screen: str
    enabled: bool
    arch: str = "aarch64"
    extra_arch: set[str] = field(default_factory=lambda: {"armhf"})
    sticks: int = 0
    tags: set[str] = field(default_factory=set)              # matched against "!tag" reqs
    unsupported_reqs: set[str] = field(default_factory=set)
    max_glibc: str = ""
    cfws: list[str] = field(default_factory=list)
    must_have: set[str] = field(default_factory=set)         # ports.json stems
    heavy: set[str] = field(default_factory=set)
    # drawing hints for the device art
    body: str = "#2b2f3a"
    accent: str = "#ff5d73"
    aspect: float = 4 / 3                                    # screen w/h
    tall: bool = True
    clam: bool = False                                       # clamshell (draws a hinge)


NEXTUI = CFW(
    id="nextui",
    name="NextUI",
    blurb=t("cfw.nextui.blurb", "Lightweight & fast · Uses ben16w PortMaster pak"),
    sd_markers=[".system", "Roms"],
    sd_alt_markers=[[".system", "Emus"]],
    sd_exclude=["MUOS", "tools"],
    scripts_dir="Roms/Ports (PORTS)",
    ports_dir="Roms/Ports (PORTS)/.ports",
    libs_dir="Emus/tg5040/PORTS.pak/PortMaster/libs",
    media_dir="Roms/Ports (PORTS)/.media",
    pak_marker="Emus/tg5040/PORTS.pak/launch.sh",
    pak_url="https://github.com/ben16w/minui-portmaster/releases/latest/download/PORTS.pak.zip",
    status_wiki="https://raw.githubusercontent.com/wiki/ben16w/minui-portmaster/Ports-Status.md",
    max_glibc="2.33",
    after_install=[
        t("cfw.nextui.after_1", "Safely eject card → Insert into handheld → Open Ports → Launch PortMaster once (it will patch runtimes and download box art) → Ready to play"),
        t("cfw.common.first_launch_slow", "First launch of some games may take several minutes — this is normal"),
    ],
    accent="#ffb347",
)

# muOS 2606.0 Andromeda+ supports Brick Pro. PortMaster is built in.
# SD1 (big exFAT partition, label "MustardOS") = /mnt/mmc ; SD2 = /mnt/sdcard (ROMS + ports only)
MUOS = CFW(
    id="muos",
    name="muOS",
    blurb=t("cfw.muos.blurb", "Andromeda 2606+ · Built-in PortMaster"),
    sd_markers=["MUOS", "ROMS"],
    sd_alt_markers=[["ROMS", "ports"]],
    sd_exclude=[".system", "Emus"],
    scripts_dir="ROMS/Ports",
    ports_dir="ports",
    libs_dir="MUOS/PortMaster/libs",
    media_dir="MUOS/info/catalogue/External - Ports/box",
    optional_dirs=True,
    builtin_pm=True,
    after_install=[
        t("cfw.muos.after_1", "Safely eject card → Insert into handheld → Explore Content → Ports → Select game to play"),
        t("cfw.muos.after_2", "If games do not appear, open PortMaster on the handheld once or restart"),
        t("cfw.common.first_launch_slow", "First launch of some games may take several minutes — this is normal"),
    ],
    max_glibc="",
    min_cfw_version="2606.0",
    accent="#f5c542",
)

# Paths below come from PortMaster's own harbourmaster/config.py path table
# (HM_DEFAULT_TOOLS_DIR / PORTS_DIR / SCRIPTS_DIR), mapped to the partition Windows sees.
ES_REFRESH = t("cfw.common.es_refresh", "If games do not appear: START → Game Settings → Update Gamelists")

# Knulli: /userdata = SHARE partition. ports+scripts /userdata/roms/ports,
# tools = XDG_DATA_HOME = /userdata/system/.local/share
KNULLI = CFW(
    id="knulli",
    name="KNULLI",
    blurb=t("cfw.knulli.blurb", "Batocera fork for handhelds · Built-in PortMaster"),
    sd_markers=["roms", "bios", "system"],
    sd_exclude=[".system", "Emus", "MUOS", "tools"],
    scripts_dir="roms/ports",
    ports_dir="roms/ports",
    libs_dir="system/.local/share/PortMaster/libs",
    optional_dirs=True,
    builtin_pm=True,
    max_glibc="",
    min_cfw_version="20240530",
    after_install=[
        t("cfw.common.eject_open_ports", "Safely eject card → Insert into handheld → Open Ports section"),
        ES_REFRESH,
        t("cfw.knulli.after_exfat", "If using exFAT: games requiring symlinks may not launch (see KNULLI exFAT guide)"),
        t("cfw.common.first_launch_slow", "First launch of some games may take several minutes — this is normal"),
    ],
    accent="#a78bfa",
)

# ArkOS / dArkOS: EASYROMS partition = /roms. tools /roms/tools, ports+scripts /roms/ports
_ARK = dict(
    sd_markers=["tools", "ports", "bios"],
    sd_exclude=[".system", "Emus", "MUOS"],
    scripts_dir="ports",
    ports_dir="ports",
    libs_dir="tools/PortMaster/libs",
    optional_dirs=True,
    builtin_pm=True,
    min_cfw_version="2.0",
    after_install=[
        t("cfw.common.eject_open_ports", "Safely eject card → Insert into handheld → Open Ports section"),
        t("cfw.common.es_restart", "If games do not appear: START → Quit → Restart EmulationStation"),
        t("cfw.common.first_launch_slow", "First launch of some games may take several minutes — this is normal"),
    ],
)
ARKOS = CFW(id="arkos", name="ArkOS", blurb=t("cfw.arkos.blurb", "Popular CFW for R36S · Built-in PortMaster"),
            enabled=False, max_glibc="2.30", accent="#60a5fa", **_ARK)   # Ubuntu 19.10 base
DARKOS = CFW(id="darkos", name="dArkOS", blurb=t("cfw.darkos.blurb", "Newer ArkOS (Debian) · Built-in PortMaster"),
             enabled=False, max_glibc="", accent="#38bdf8", **_ARK)

# ROCKNIX: games partition = /storage/roms. tools = ports = scripts = /storage/roms/ports
ROCKNIX = CFW(
    id="rocknix",
    name="ROCKNIX",
    blurb=t("cfw.rocknix.blurb", "Successor to JELOS · Built-in PortMaster"),
    sd_markers=["roms/ports"],
    sd_exclude=[".system", "Emus", "MUOS", "tools", "system", "Persistent"],
    scripts_dir="roms/ports",
    ports_dir="roms/ports",
    libs_dir="roms/ports/PortMaster/libs",
    optional_dirs=True,
    builtin_pm=True,
    max_glibc="",
    min_cfw_version="20240401",
    after_install=[
        t("cfw.common.eject_open_ports", "Safely eject card → Insert into handheld → Open Ports section"),
        ES_REFRESH,
        t("cfw.common.first_launch_slow", "First launch of some games may take several minutes — this is normal"),
    ],
    accent="#f97316",
)

# spruceOS: tools Persistent/portmaster, ports+scripts Roms/ports (config.py row 1)
SPRUCE = CFW(
    id="spruce",
    name="spruceOS",
    blurb=t("cfw.spruce.blurb", "For Miyoo / TrimUI / Anbernic · Built-in PortMaster"),
    enabled=True,
    sd_markers=["Persistent", "Roms"],
    sd_alt_markers=[["Persistent/portmaster", "Roms/ports"]],
    sd_exclude=[".system", "Emus", "MUOS", "tools"],
    scripts_dir="Roms/ports",
    ports_dir="Roms/ports",
    libs_dir="Persistent/portmaster/PortMaster/libs",
    builtin_pm=True,
    optional_dirs=True,
    min_cfw_version="2.0.0",
    after_install=[
        t("cfw.common.eject_open_ports", "Safely eject card → Insert into handheld → Open Ports section"),
        t("cfw.spruce.after_unofficial", "PortMaster on spruceOS is unofficial; compatibility varies by game (hit or miss)"),
        t("cfw.common.first_launch_slow", "First launch of some games may take several minutes — this is normal"),
    ],
    accent="#4ecdc4",
)

# CrossMix: tools Apps/PortMaster, ports Data/ports, scripts Roms/PORTS (config.py row 3 + wiki)
CROSSMIX = CFW(
    id="crossmix",
    name="CrossMix",
    blurb=t("cfw.crossmix.blurb", "For TrimUI (unofficial on Brick Pro) · Built-in PortMaster"),
    enabled=True,
    sd_markers=["Apps/PortMaster", "Data/ports"],
    sd_alt_markers=[["Roms/PORTS", "Data/ports"]],
    sd_exclude=[".system", "MUOS"],
    scripts_dir="Roms/PORTS",
    ports_dir="Data/ports",
    libs_dir="Apps/PortMaster/PortMaster/libs",
    builtin_pm=True,
    optional_dirs=True,
    min_cfw_version="1.0.0",
    after_install=[
        t("cfw.crossmix.after_refresh", "Safely eject card → Insert into handheld → At main menu press MENU → Refresh Roms to update game list"),
        t("cfw.crossmix.after_unofficial", "CrossMix does not officially support TrimUI Brick Pro"),
        t("cfw.common.first_launch_slow", "First launch of some games may take several minutes — this is normal"),
    ],
    accent="#f472b6",
)

CFWS = [
    NEXTUI,
    MUOS,
    KNULLI,
    ARKOS,
    DARKOS,
    ROCKNIX,
    SPRUCE,
    CROSSMIX,
]

# Hardware-verified device + CFW combinations
TESTED_PAIRS: set[tuple[str, str]] = {
    ("rg35xxsp", "muos"),  # 2026-11-12 dev test, 2 cards: 6 ports installed and all start
    ("brickpro", "muos"),  # 2026-11-13 dev test: works
}


def is_tested(device: Device | str, cfw: CFW | str) -> bool:
    dev_id = device.id if isinstance(device, Device) else device
    cfw_id = cfw.id if isinstance(cfw, CFW) else cfw
    return (dev_id, cfw_id) in TESTED_PAIRS

A133P_MUST = {
    # platformers / metroidvania
    "cave.story-evo", "cceleste", "celeste", "vvvvvv", "axiom.verge", "timespinner",
    "iconoclasts", "shovel.knight", "freedom.planet", "hurrican", "spelunky", "supertux",
    # story / rpg
    "undertale", "rakuen", "finding_paradise", "anodyne", "uqm",
    # roguelike / quick
    "balatro", "downwell", "minit", "riskofrain", "luck.be.a.landlord",
    # action / arcade
    "sorr", "cannonball-st", "opentyrian",
    # 90s fps
    "duke.nukem.3d", "blood", "shadow.warrior", "quake", "exhumed",
    # pc classics
    "devilutionx", "fallout1", "fallout2", "fheroes2", "space.cadet.pinball", "worldofgoo",
    # v1.0 free picks (T-031, 2026-11-12, from AG's free candidates; v1.0 shows only free ports)
    "openjazz", "commander.genius", "abuse", "rick.dangerous", "sonic1sms", "sonic2sms", "sonictimetwisted", "opensurge",
    "alephone-marathon", "descent",
    "flare", "freedink", "snkrx", "angband",
    "openttd", "wesnoth", "openfodder",
    "chromium_bsu", "digger", "cromagrally",
    "apotris", "block.attack",
}
A133P_HEAVY = {
    "owlboy", "sonic.mania", "stardewvalley", "stardewvalleymainline", "half-life", "vcmi",
    "quake3", "bleed2", "forager", "supertuxkart", "tmntsr", "openrct2", "salt.and.sanctuary",
}

# Ports we tested on real hardware and found not working: (device id, cfw id) -> {port stem: note}
TESTED_BROKEN: dict[tuple[str, str], dict[str, str]] = {
    # blazeofstorm was reported broken on 2026-11-02, but the cause was game files
    # placed outside assets/ (fixed by plan_local_files) — re-test before listing again.
}

LOW_RAM = {"2gb", "4gb", "opengl", "ultra"}   # 1 GB, GLES-only devices
MID_RAM = {"4gb", "opengl", "ultra"}          # 2 GB devices

DEVICES = [
    # --- TrimUI (Allwinner A133P) ---
    Device(
        id="brickpro", name="TrimUI Brick Pro", chip="Allwinner A133P", ram="1 GB",
        screen='3.95" 1024×768', enabled=True, sticks=2,
        tags={"trimui", "lowpower"}, unsupported_reqs=LOW_RAM,
        cfws=["nextui", "muos", "knulli", "spruce", "crossmix"],
        must_have=A133P_MUST, heavy=A133P_HEAVY,
        body="#30343f", accent="#ff5d73", aspect=4 / 3, tall=True,
    ),
    Device(
        id="brick", name="TrimUI Brick", chip="Allwinner A133P", ram="1 GB",
        screen='3.2" 1024×768', enabled=False, sticks=0,
        tags={"trimui", "lowpower"}, unsupported_reqs=LOW_RAM,
        cfws=["nextui", "muos", "knulli"],
        must_have=A133P_MUST, heavy=A133P_HEAVY,
        body="#e8e4da", accent="#8a8a8a",
    ),
    Device(
        id="smartpro", name="TrimUI Smart Pro", chip="Allwinner A133P", ram="1 GB",
        screen='4.96" 1280×720', enabled=False, sticks=2,
        tags={"trimui", "lowpower"}, unsupported_reqs=LOW_RAM,
        cfws=["nextui", "muos", "knulli"],
        must_have=A133P_MUST, heavy=A133P_HEAVY,
        body="#1f2229", accent="#5ab0ff", aspect=16 / 9, tall=False,
    ),
    # --- Anbernic H700 ---
    Device(
        id="rg35xxsp", name="Anbernic RG35XX SP", chip="Allwinner H700", ram="1 GB",
        screen='3.5" 640×480', enabled=True, sticks=0,
        tags={"lowpower"}, unsupported_reqs=LOW_RAM,
        cfws=["muos", "knulli", "rocknix", "spruce"],
        must_have=A133P_MUST, heavy=A133P_HEAVY,
        body="#7b8794", accent="#e11d48", clam=True,
    ),
    # Any other Allwinner H700 handheld (RG35XX Plus/H/2024, RG34XX, RG28XX, RG40XX H/V, RG CubeXX ...).
    # Same chip, RAM and CFW folder layout as the SP, so installs work; pick by stick count so the
    # recommendations fit. Results in the beta sheet tell us which models to add as their own cards.
    Device(
        id="h700-nosticks", name="Other H700 (no sticks)", chip="Allwinner H700", ram="1 GB",
        screen="RG35XX Plus / 2024, RG34XX, RG28XX …", enabled=True, sticks=0,
        tags={"lowpower"}, unsupported_reqs=LOW_RAM,
        cfws=["muos", "knulli", "rocknix"],
        must_have=A133P_MUST, heavy=A133P_HEAVY,
        body="#4b5160", accent="#fbbf24",
    ),
    Device(
        id="h700-sticks", name="Other H700 (with sticks)", chip="Allwinner H700", ram="1 GB",
        screen="RG35XX H, RG40XX H / V, RG CubeXX …", enabled=True, sticks=2,
        tags={"lowpower"}, unsupported_reqs=LOW_RAM,
        cfws=["muos", "knulli", "rocknix"],
        must_have=A133P_MUST, heavy=A133P_HEAVY,
        body="#2f3440", accent="#38bdf8", tall=False,
    ),
    Device(
        id="rg40xxh", name="Anbernic RG40XX H", chip="Allwinner H700", ram="1 GB",
        screen='4" 640×480', enabled=False, sticks=2,
        tags={"lowpower"}, unsupported_reqs=LOW_RAM,
        cfws=["muos", "knulli"],
        must_have=A133P_MUST, heavy=A133P_HEAVY,
        body="#3b3f4a", accent="#a3e635", aspect=4 / 3, tall=False,
    ),
    # --- Rockchip ---
    Device(
        id="r36s", name="R36S", chip="Rockchip RK3326", ram="1 GB",
        screen='3.5" 640×480', enabled=False, sticks=2,
        tags={"lowpower"}, unsupported_reqs=LOW_RAM,
        cfws=["arkos", "darkos", "rocknix"],
        must_have=A133P_MUST, heavy=A133P_HEAVY,
        body="#1e1b2e", accent="#c084fc",
    ),
    Device(
        id="rg353v", name="Anbernic RG353V", chip="Rockchip RK3566", ram="2 GB",
        screen='3.5" 640×480', enabled=False, sticks=2,
        unsupported_reqs=MID_RAM,
        cfws=["arkos", "rocknix"],
        must_have=A133P_MUST, heavy=A133P_HEAVY - {"stardewvalley", "half-life", "vcmi"},
        body="#d6d3d1", accent="#ef4444",
    ),
]
