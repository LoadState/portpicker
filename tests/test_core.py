import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# core.py creates its cache dir on import; keep it out of the real profile
import tempfile  # noqa: E402
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="pp-test-")

import core  # noqa: E402
import steam  # noqa: E402
from profiles import (DEVICES, NEXTUI, MUOS, KNULLI, ROCKNIX, ARKOS, DARKOS,
                      SPRUCE, CROSSMIX, CFWS, TESTED_PAIRS, is_tested)  # noqa: E402

BRICK = next(d for d in DEVICES if d.id == "brickpro")


def port(stem, **kw):
    base = dict(name=f"{stem}.zip", title=stem, desc="", inst="", genres=[], rtr=False,
                availability="paid", reqs=[], runtime=[], arch=["aarch64"], min_glibc="",
                items=[f"{stem}.sh", stem], url="", md5="", size=0, screenshot="", cover="",
                date_updated="")
    base.update(kw)
    return core.Port(**base)


# --------------------------------------------------------------------------
# VDF + Steam discovery
# --------------------------------------------------------------------------

def test_parse_vdf_nested_escapes_comments():
    txt = r'''
    // comment
    "libraryfolders"
    {
        "0" { "path" "C:\\Program Files (x86)\\Steam" "apps" { "391540" "123" } }
        "1" { "path" "D:\\SteamLibrary" }
    }'''
    d = steam.parse_vdf(txt)
    assert d["libraryfolders"]["0"]["path"] == r"C:\Program Files (x86)\Steam"
    assert d["libraryfolders"]["0"]["apps"]["391540"] == "123"
    assert d["libraryfolders"]["1"]["path"] == r"D:\SteamLibrary"


def manifest(lib: Path, appid, installdir, flags=4, beta=""):
    sa = lib / "steamapps"
    (sa / "common" / installdir).mkdir(parents=True, exist_ok=True)
    cfg = f'"UserConfig" {{ "BetaKey" "{beta}" }}' if beta else ""
    (sa / f"appmanifest_{appid}.acf").write_text(
        f'"AppState" {{ "appid" "{appid}" "name" "{installdir}" "installdir" "{installdir}" '
        f'"StateFlags" "{flags}" {cfg} }}', "utf-8")
    return sa / "common" / installdir


@pytest.fixture
def fake_steam(tmp_path):
    root = tmp_path / "Steam"
    lib2 = tmp_path / "Lib2"
    (root / "steamapps").mkdir(parents=True)
    (lib2 / "steamapps").mkdir(parents=True)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders" { "0" { "path" "%s" } "1" { "path" "%s" } }'
        % (str(root).replace("\\", "\\\\"), str(lib2).replace("\\", "\\\\")), "utf-8")
    return root, lib2


def test_installed_apps_across_libraries(fake_steam):
    root, lib2 = fake_steam
    manifest(root, "391540", "Undertale")
    manifest(lib2, "609490", "Minit")
    manifest(lib2, "94400", "Nidhogg", beta="")
    manifest(root, "248820", "Risk of Rain", flags=6)        # 4|2: installed, update queued
    manifest(root, "38400", "Fallout", flags=2)              # not fully installed
    apps = steam.installed_apps([root])
    assert set(apps) == {"391540", "609490", "94400", "248820", "38400"}
    assert apps["609490"].path == lib2 / "steamapps" / "common" / "Minit"
    assert apps["248820"].complete and not apps["38400"].complete


def test_match_rules(fake_steam):
    root, _ = fake_steam
    manifest(root, "391540", "Undertale")
    manifest(root, "94400", "Nidhogg")                       # wrong (no) beta
    manifest(root, "504230", "Celeste")                      # Steam build unsupported
    manifest(root, "38400", "Fallout", flags=2)
    apps = steam.installed_apps([root])
    assert steam.match("undertale", apps).usable
    m = steam.match("nidhogg", apps)
    assert not m.usable and "before_may_2017" in m.note
    m = steam.match("celeste", apps)
    assert not m.usable and m.recipe is None and "DRM-free" in m.note
    assert not steam.match("fallout1", apps).usable
    assert steam.match("balatro", apps) is None              # not installed


def test_nidhogg_right_beta(fake_steam):
    root, _ = fake_steam
    manifest(root, "94400", "Nidhogg", beta="before_may_2017")
    assert steam.match("nidhogg", steam.installed_apps([root])).usable


def test_recipes_point_at_real_port_dirs():
    # each recipe's destination must start with a folder the port itself installs
    import json
    cache = Path(os.environ.get("PORTS_JSON", "ports.json"))
    if not cache.exists():
        pytest.skip("set PORTS_JSON to a downloaded ports.json to run this")
    data = json.loads(cache.read_text("utf-8"))["ports"]
    for stem, r in steam.RECIPES.items():
        items = [i.rstrip("/").lower() for i in data[stem + ".zip"]["items"]]
        assert r.dest.split("/")[0].lower() in items, stem


# --------------------------------------------------------------------------
# Copy from Steam
# --------------------------------------------------------------------------

def target(tmp_path):
    return core.Target(cfw=NEXTUI, root=tmp_path / "card", custom=True)


def test_copy_specific_files_case_insensitive(fake_steam, tmp_path):
    root, _ = fake_steam
    game = manifest(root, "38400", "Fallout")
    (game / "MASTER.DAT").write_bytes(b"m")
    (game / "critter.dat").write_bytes(b"c")
    (game / "DATA" / "SOUND").mkdir(parents=True)
    (game / "DATA" / "SOUND" / "a.acm").write_bytes(b"s")
    (game / "falloutw.exe").write_bytes(b"x")             # not part of the recipe
    p = port("fallout1", items=["Fallout 1.sh", "fallout1/"])
    core.classify([p], BRICK, NEXTUI, {"broken": set(), "fixed": set(), "issues": set()})
    assert core.attach_steam([p], steam.installed_apps([root])) == 1
    t = target(tmp_path)
    log = []
    c, s = core.copy_from_steam(p, t, False, log.append)
    out = t.ports_dir / "fallout1"
    assert (out / "master.dat").read_bytes() == b"m"       # written lower-case
    assert (out / "data" / "SOUND" / "a.acm").exists()
    assert not (out / "falloutw.exe").exists()
    assert c == 3 and s == 0
    # second run skips complete files
    assert core.copy_from_steam(p, t, False, log.append) == (0, 3)


def test_copy_globs_and_missing(fake_steam, tmp_path):
    root, _ = fake_steam
    game = manifest(root, "609490", "Minit")
    (game / "data.win").write_bytes(b"d")
    (game / "Music.OGG").write_bytes(b"o")
    p = port("minit")
    p.tier = "ok"
    core.attach_steam([p], steam.installed_apps([root]))
    log = []
    t = target(tmp_path)
    core.copy_from_steam(p, t, False, log.append)
    assert (t.ports_dir / "minit/assets/data.win").exists()
    assert (t.ports_dir / "minit/assets/Music.OGG").exists()   # glob keeps source name
    assert any("*.csv" in l for l in log)                      # reported, not fatal


def test_copy_whole_install(fake_steam, tmp_path):
    root, _ = fake_steam
    game = manifest(root, "391540", "Undertale")
    (game / "data.win").write_bytes(b"d")
    (game / "mus").mkdir()
    (game / "mus" / "x.ogg").write_bytes(b"o")
    p = port("undertale")
    p.tier = "ok"
    core.attach_steam([p], steam.installed_apps([root]))
    t = target(tmp_path)
    assert core.copy_from_steam(p, t, False, lambda _: None) == (2, 0)
    assert (t.ports_dir / "undertale/assets/mus/x.ogg").exists()


def test_local_folder_wins_over_steam(fake_steam, tmp_path):
    root, _ = fake_steam
    manifest(root, "391540", "Undertale")
    p = port("undertale")
    p.tier = "ok"
    p.local_dir = tmp_path
    core.attach_steam([p], steam.installed_apps([root]))
    assert p.steam_ready and p.has_files
    assert any("local folder" in r.lower() or "โฟลเดอร์ในเครื่อง" in r for r in p.reasons)


def test_attach_skips_incompatible_and_rtr(fake_steam):
    root, _ = fake_steam
    manifest(root, "391540", "Undertale")
    apps = steam.installed_apps([root])
    a, b = port("undertale"), port("undertale", rtr=True)
    a.tier = "incompatible"
    b.tier = "ok"
    assert core.attach_steam([a, b], apps) == 0
    assert a.steam is None and b.steam is None


# --------------------------------------------------------------------------
# Existing logic
# --------------------------------------------------------------------------

def test_classify_basics():
    st = {"broken": {"braid"}, "fixed": set(), "issues": set()}
    ps = [port("celeste"), port("x", reqs=["opengl"]), port("y", arch=["x86_64"]),
          port("z", arch=["armhf"]), port("braid"), port("owlboy"), port("w", min_glibc="2.35")]
    core.classify(ps, BRICK, NEXTUI, st)
    tiers = {p.stem: p.tier for p in ps}
    assert tiers == {"celeste": "must", "x": "incompatible", "y": "incompatible", "z": "ok",
                     "braid": "broken", "owlboy": "heavy", "w": "incompatible"}


def test_parse_status_sections_and_fallback():
    md = "## Not running\n* Braid - crashes\n* Super Metroid (sm)\n## Now running\n* Sonic 1\n"
    s = core.parse_status(md)
    assert s["broken"] == {"braid", "supermetroid"} and s["fixed"] == {"sonic1"}
    assert core.parse_status("")["broken"]                  # bundled fallback list


# --- placing local game files into newer ports (from real D:\port layouts) ---

def test_old_port_gamedata_moves_to_assets():
    zip_names = ["botvice/assets/.gitkeep", "botvice/gmloadernext.aarch64", "botvice/port.json", "Bot Vice.sh"]
    local = ["botvice.apk", "botvice.gptk", "gameinfo.xml", "gmloader", "port.json", "libc++_shared.so",
             "gamedata/game.unx", "gamedata/audiogroup1.dat", "libs/libzip.so.5", "patch/botvice.xdelta"]
    plan = core.plan_local_files("botvice", zip_names, "", local)
    assert plan.mode == "gamedata->assets"
    assert sorted(d for _, d in plan.copy) == ["assets/audiogroup1.dat", "assets/game.unx"]


def test_raw_game_folder_goes_into_assets():
    zip_names = ["blazeofstorm/assets/.gitkeep", "Blaze of Storm.sh"]
    local = ["blazeofstorm.exe", "data.win", "options.ini"]
    plan = core.plan_local_files("blazeofstorm", zip_names, "copy all game files into assets folder", local)
    assert plan.mode == "root->assets"
    assert ("data.win", "assets/data.win") in plan.copy


def test_old_port_root_data_goes_into_gamedata():
    zip_names = ["riskofrain/gamedata/PUT_GAME_FILES_HERE.txt", "riskofrain/port.json"]
    local = ["data.win", "musicStage1.ogg", "gmloadernext", "game.apk", "port.json", "gameinfo.xml"]
    plan = core.plan_local_files("riskofrain", zip_names, "", local)
    assert sorted(d for _, d in plan.copy) == ["gamedata/data.win", "gamedata/musicStage1.ogg"]


def test_same_layout_mirrors_and_never_replaces_port_files():
    zip_names = ["celeste/gamedata/.gitkeep", "celeste/dlls/FNA.dll", "celeste/celeste.gptk"]
    local = ["gamedata/Celeste.exe", "gamedata/Content/a.xnb", "dlls/FNA.dll", "celeste.gptk", "celeste-repacker"]
    plan = core.plan_local_files("celeste", zip_names, "", local)
    dests = sorted(d for _, d in plan.copy)
    assert dests == ["celeste-repacker", "gamedata/Celeste.exe", "gamedata/Content/a.xnb"]


def test_empty_local_folder_is_flagged():
    plan = core.plan_local_files("to_the_moon", ["to_the_moon/gamedata/.gitkeep"], "",
                                 ["gamedata/.gitkeep", "port.json", "libs/libruby.so"])
    assert plan.copy == [] and plan.warn


def test_stardew_16_files_go_to_mainline_port(tmp_path):
    (tmp_path / "stardewvalley" / "gamedata").mkdir(parents=True)
    (tmp_path / "stardewvalley" / "gamedata" / "Stardew Valley.deps.json").write_text("{}")
    mk = lambda stem, d: core.Port(name=stem + ".zip", title=stem, desc="", inst="", genres=[], rtr=False,
                                   availability="", reqs=[], runtime=[], arch=[], min_glibc="",
                                   items=[d + "/", d + ".sh"], url="", md5="", size=0, screenshot="",
                                   cover="", date_updated="")
    compat, main = mk("stardewvalley", "stardewvalley"), mk("stardewvalleymainline", "stardewvalleymainline")
    core.scan_local(tmp_path, [compat, main])
    assert compat.local_dir is None and main.local_dir == tmp_path / "stardewvalley" and main.local_note


# --------------------------------------------------------------------------
# T-007: CFW profiles, platform detection, and card matching tests
# --------------------------------------------------------------------------

def test_v1_device_and_cfw_configuration():
    # v1: Brick Pro, RG35XX SP and the two generic H700 cards (by stick count)
    enabled_devices = [d for d in DEVICES if d.enabled]
    assert {d.id for d in enabled_devices} == {"brickpro", "rg35xxsp", "h700-nosticks", "h700-sticks"}
    from core import check_device_card_match
    gen = next(d for d in DEVICES if d.id == "h700-sticks")
    assert gen.sticks == 2 and check_device_card_match(gen, "RG40XX-H")[0] is True
    assert check_device_card_match(gen, "TrimUI Brick")[0] is False

    # Other 5 devices remain present but disabled
    disabled_devices = [d for d in DEVICES if not d.enabled]
    assert {d.id for d in disabled_devices} == {"brick", "smartpro", "rg40xxh", "r36s", "rg353v"}

    # RG35XX SP has rocknix and spruce added; NextUI and CrossMix are not present
    rg_sp = next(d for d in DEVICES if d.id == "rg35xxsp")
    assert "rocknix" in rg_sp.cfws
    assert "spruce" in rg_sp.cfws
    assert "nextui" not in rg_sp.cfws
    assert "crossmix" not in rg_sp.cfws
    assert set(rg_sp.cfws) == {"muos", "knulli", "rocknix", "spruce"}

    # Brick Pro has nextui, muos, knulli, spruce, crossmix
    brickpro = next(d for d in DEVICES if d.id == "brickpro")
    assert set(brickpro.cfws) == {"nextui", "muos", "knulli", "spruce", "crossmix"}

    # ArkOS and dArkOS are not in any enabled device's CFWs and are disabled
    enabled_cfws = set().union(*(d.cfws for d in enabled_devices))
    assert "arkos" not in enabled_cfws
    assert "darkos" not in enabled_cfws
    assert not ARKOS.enabled
    assert not DARKOS.enabled

    # spruceOS and CrossMix are enabled as Beta
    assert SPRUCE.enabled
    assert SPRUCE.ports_dir == "Roms/ports"
    assert SPRUCE.scripts_dir == "Roms/ports"
    assert SPRUCE.libs_dir == "Persistent/portmaster/PortMaster/libs"
    assert "เร็วๆ นี้" not in SPRUCE.blurb
    assert any("unofficial" in tip.lower() or "hit or miss" in tip.lower() for tip in SPRUCE.after_install)

    assert CROSSMIX.enabled
    assert CROSSMIX.ports_dir == "Data/ports"
    assert CROSSMIX.scripts_dir == "Roms/PORTS"
    assert CROSSMIX.libs_dir == "Apps/PortMaster/PortMaster/libs"
    assert "เร็วๆ นี้" not in CROSSMIX.blurb
    assert any("Refresh Roms" in tip for tip in CROSSMIX.after_install)
    assert any("Brick Pro" in tip for tip in CROSSMIX.after_install)

    # Tested pairs: only rg35xxsp + muOS is tested on hardware so far (2026-11-12)
    assert TESTED_PAIRS == {("rg35xxsp", "muos"), ("brickpro", "muos")}
    assert is_tested("rg35xxsp", "muos")
    assert not is_tested("brickpro", "nextui")
    assert is_tested("brickpro", "muos")
    assert not is_tested("brickpro", "knulli")
    assert not is_tested("brickpro", "spruce")
    assert not is_tested("brickpro", "crossmix")
    assert not is_tested("rg35xxsp", "knulli")
    assert not is_tested("rg35xxsp", "rocknix")
    assert not is_tested("rg35xxsp", "spruce")

    # KNULLI after_install contains exFAT tip
    assert any("exFAT" in tip for tip in KNULLI.after_install)


def test_nextui_platform_detection_and_target(tmp_path):
    card = tmp_path / "card"
    card.mkdir()

    # Fresh card: defaults to tg5040
    (card / ".system").mkdir()
    (card / "Roms").mkdir()
    assert core.find_nextui_platform(card) == "tg5040"
    t_fresh = core.Target(cfw=NEXTUI, root=card)
    assert t_fresh.pak_marker == "Emus/tg5040/PORTS.pak/launch.sh"
    assert t_fresh.libs_dir == card / "Emus/tg5040/PORTS.pak/PortMaster/libs"

    # Card with .system/tg3040
    card_tg30 = tmp_path / "card_tg30"
    (card_tg30 / ".system" / "tg3040").mkdir(parents=True)
    assert core.find_nextui_platform(card_tg30) == "tg3040"
    t_tg30 = core.Target(cfw=NEXTUI, root=card_tg30)
    assert t_tg30.pak_marker == "Emus/tg3040/PORTS.pak/launch.sh"
    assert t_tg30.libs_dir == card_tg30 / "Emus/tg3040/PORTS.pak/PortMaster/libs"

    # Card with Emus/tg4040
    card_tg40 = tmp_path / "card_tg40"
    (card_tg40 / "Emus" / "tg4040").mkdir(parents=True)
    assert core.find_nextui_platform(card_tg40) == "tg4040"
    t_tg40 = core.Target(cfw=NEXTUI, root=card_tg40)
    assert t_tg40.pak_marker == "Emus/tg4040/PORTS.pak/launch.sh"
    assert t_tg40.libs_dir == card_tg40 / "Emus/tg4040/PORTS.pak/PortMaster/libs"


def test_card_matches_nextui(tmp_path):
    card = tmp_path / "nextui_sd1"
    card.mkdir(parents=True)
    (card / ".system").mkdir(parents=True)
    (card / "Roms").mkdir(parents=True)
    assert core.card_matches(NEXTUI, card)

    # With platform folder
    (card / ".system" / "tg5040").mkdir(parents=True)
    assert core.card_matches(NEXTUI, card)

    # Empty card does not match
    empty = tmp_path / "empty"
    empty.mkdir(parents=True)
    assert not core.card_matches(NEXTUI, empty)

    # Card from another CFW (e.g. muOS or tools) is rejected
    muos_card = tmp_path / "muos_card"
    (muos_card / ".system").mkdir(parents=True)
    (muos_card / "Roms").mkdir(parents=True)
    (muos_card / "MUOS").mkdir(parents=True)
    assert not core.card_matches(NEXTUI, muos_card)


def test_card_matches_muos(tmp_path):
    # SD1: MUOS + ROMS
    sd1 = tmp_path / "muos_sd1"
    (sd1 / "MUOS").mkdir(parents=True)
    (sd1 / "ROMS").mkdir(parents=True)
    assert core.card_matches(MUOS, sd1)

    # SD2: ROMS + ports
    sd2 = tmp_path / "muos_sd2"
    (sd2 / "ROMS").mkdir(parents=True)
    (sd2 / "ports").mkdir(parents=True)
    assert core.card_matches(MUOS, sd2)

    # Wrong CFW: NextUI card (.system excluded)
    wrong = tmp_path / "wrong_muos"
    (wrong / "MUOS").mkdir(parents=True)
    (wrong / "ROMS").mkdir(parents=True)
    (wrong / ".system").mkdir(parents=True)
    assert not core.card_matches(MUOS, wrong)


def test_card_matches_knulli(tmp_path):
    # SD1 / userdata: roms + bios + system
    card = tmp_path / "knulli_card"
    (card / "roms").mkdir(parents=True)
    (card / "bios").mkdir(parents=True)
    (card / "system").mkdir(parents=True)
    assert core.card_matches(KNULLI, card)

    # Wrong CFW: contains .system or MUOS
    wrong = tmp_path / "wrong_knulli"
    (wrong / "roms").mkdir(parents=True)
    (wrong / "bios").mkdir(parents=True)
    (wrong / "system").mkdir(parents=True)
    (wrong / ".system").mkdir(parents=True)
    assert not core.card_matches(KNULLI, wrong)


def test_card_matches_and_target_rocknix(tmp_path):
    # Layout 1 (games card): roms/ports
    card1 = tmp_path / "rocknix_games"
    (card1 / "roms" / "ports").mkdir(parents=True)
    assert core.card_matches(ROCKNIX, card1)
    t1 = core.Target(cfw=ROCKNIX, root=card1)
    assert t1.ports_dir == card1 / "roms" / "ports"
    assert t1.scripts_dir == card1 / "roms" / "ports"
    assert t1.libs_dir == card1 / "roms" / "ports" / "PortMaster" / "libs"

    # Layout 2: with PortMaster installed
    card2 = tmp_path / "rocknix_pm"
    (card2 / "roms" / "ports" / "PortMaster").mkdir(parents=True)
    assert core.card_matches(ROCKNIX, card2)
    t2 = core.Target(cfw=ROCKNIX, root=card2)
    assert t2.ports_dir == card2 / "roms" / "ports"
    assert t2.scripts_dir == card2 / "roms" / "ports"
    assert t2.libs_dir == card2 / "roms" / "ports" / "PortMaster" / "libs"

    # Plain roms folder on a non-card drive (e.g. D:\roms) must NOT match
    plain_roms = tmp_path / "plain_roms_drive"
    (plain_roms / "roms").mkdir(parents=True)
    assert not core.card_matches(ROCKNIX, plain_roms)

    # muOS SD2 card (ROMS + ports at root) must NOT match ROCKNIX
    muos_sd2 = tmp_path / "muos_sd2_card"
    (muos_sd2 / "ROMS").mkdir(parents=True)
    (muos_sd2 / "ports").mkdir(parents=True)
    assert not core.card_matches(ROCKNIX, muos_sd2)

    # Root ports layout was dropped: ports + bios alone without roms does NOT match
    old_root = tmp_path / "rocknix_old_root"
    (old_root / "ports").mkdir(parents=True)
    (old_root / "bios").mkdir(parents=True)
    assert not core.card_matches(ROCKNIX, old_root)

    # Excluded: cards with system (KNULLI), Persistent (spruce), .system (NextUI), MUOS, tools
    wrong_knulli = tmp_path / "rocknix_wrong_knulli"
    (wrong_knulli / "roms" / "ports").mkdir(parents=True)
    (wrong_knulli / "system").mkdir(parents=True)
    assert not core.card_matches(ROCKNIX, wrong_knulli)

    wrong_spruce = tmp_path / "rocknix_wrong_spruce"
    (wrong_spruce / "roms" / "ports").mkdir(parents=True)
    (wrong_spruce / "Persistent").mkdir(parents=True)
    assert not core.card_matches(ROCKNIX, wrong_spruce)

    wrong_nextui = tmp_path / "rocknix_wrong_nextui"
    (wrong_nextui / "roms" / "ports").mkdir(parents=True)
    (wrong_nextui / ".system").mkdir(parents=True)
    assert not core.card_matches(ROCKNIX, wrong_nextui)

    wrong_muos = tmp_path / "rocknix_wrong_muos"
    (wrong_muos / "roms" / "ports").mkdir(parents=True)
    (wrong_muos / "MUOS").mkdir(parents=True)
    assert not core.card_matches(ROCKNIX, wrong_muos)


def test_card_matches_and_target_spruce(tmp_path):
    card = tmp_path / "spruce_card"
    (card / "Persistent" / "portmaster").mkdir(parents=True)
    (card / "Roms" / "ports").mkdir(parents=True)
    assert core.card_matches(SPRUCE, card)
    t = core.Target(cfw=SPRUCE, root=card)
    assert t.ports_dir == card / "Roms" / "ports"
    assert t.scripts_dir == card / "Roms" / "ports"
    assert t.libs_dir == card / "Persistent" / "portmaster" / "PortMaster" / "libs"

    # Exclusions
    for excl in (".system", "Emus", "MUOS", "tools"):
        wrong = tmp_path / f"spruce_wrong_{excl.strip('.')}"
        (wrong / "Persistent").mkdir(parents=True)
        (wrong / "Roms").mkdir(parents=True)
        (wrong / excl).mkdir(parents=True)
        assert not core.card_matches(SPRUCE, wrong)


def test_card_matches_and_target_crossmix(tmp_path):
    card = tmp_path / "crossmix_card"
    (card / "Apps" / "PortMaster").mkdir(parents=True)
    (card / "Data" / "ports").mkdir(parents=True)
    (card / "Roms" / "PORTS").mkdir(parents=True)
    assert core.card_matches(CROSSMIX, card)
    t = core.Target(cfw=CROSSMIX, root=card)
    assert t.ports_dir == card / "Data" / "ports"
    assert t.scripts_dir == card / "Roms" / "PORTS"
    assert t.libs_dir == card / "Apps" / "PortMaster" / "PortMaster" / "libs"

    # Alt marker: Roms/PORTS + Data/ports
    alt = tmp_path / "crossmix_alt"
    (alt / "Roms" / "PORTS").mkdir(parents=True)
    (alt / "Data" / "ports").mkdir(parents=True)
    assert core.card_matches(CROSSMIX, alt)

    # Broad Apps + Data without PortMaster/ports does NOT match
    broad = tmp_path / "crossmix_broad"
    (broad / "Apps").mkdir(parents=True)
    (broad / "Data").mkdir(parents=True)
    assert not core.card_matches(CROSSMIX, broad)

    # Exclusions
    for excl in (".system", "MUOS"):
        wrong = tmp_path / f"crossmix_wrong_{excl.strip('.')}"
        (wrong / "Apps" / "PortMaster").mkdir(parents=True)
        (wrong / "Data" / "ports").mkdir(parents=True)
        (wrong / excl).mkdir(parents=True)
        assert not core.card_matches(CROSSMIX, wrong)


def test_is_muos_card1_and_card2(tmp_path):
    sd1 = tmp_path / "card1"
    (sd1 / "MUOS").mkdir(parents=True)
    (sd1 / "ROMS").mkdir(parents=True)

    sd2 = tmp_path / "card2"
    (sd2 / "ROMS").mkdir(parents=True)
    (sd2 / "ports").mkdir(parents=True)

    both = tmp_path / "card_both"
    (both / "MUOS").mkdir(parents=True)
    (both / "ROMS").mkdir(parents=True)
    (both / "ports").mkdir(parents=True)

    empty = tmp_path / "empty"
    empty.mkdir()

    assert core.is_muos_card1(sd1)
    assert not core.is_muos_card2(sd1)

    assert not core.is_muos_card1(sd2)
    assert core.is_muos_card2(sd2)

    assert core.is_muos_card1(both)
    assert not core.is_muos_card2(both)

    assert not core.is_muos_card1(empty)
    assert not core.is_muos_card2(empty)


def test_target_two_root_paths(tmp_path):
    sd1 = tmp_path / "card1"
    (sd1 / "MUOS").mkdir(parents=True)
    (sd1 / "ROMS").mkdir(parents=True)

    sd2 = tmp_path / "card2"
    (sd2 / "ROMS").mkdir(parents=True)
    (sd2 / "ports").mkdir(parents=True)

    # 1. Card 1 only (Single SD)
    t1 = core.Target(cfw=MUOS, root=sd1)
    assert t1.system_root is None
    assert t1.ports_dir == sd1 / "ports"
    assert t1.scripts_dir == sd1 / "ROMS/Ports"
    assert t1.libs_dir == sd1 / "MUOS/PortMaster/libs"
    assert t1.media_dir == sd1 / "MUOS/info/catalogue/External - Ports/box"

    # 2. Card 2 only (SD2 alone)
    t2 = core.Target(cfw=MUOS, root=sd2)
    assert t2.system_root is None
    assert t2.ports_dir == sd2 / "ports"
    assert t2.scripts_dir == sd2 / "ROMS/Ports"
    assert t2.libs_dir is None
    assert t2.media_dir is None

    # 3. Both cards paired (Dual SD: root=SD2, system_root=SD1)
    t_dual = core.Target(cfw=MUOS, root=sd2, system_root=sd1)
    assert t_dual.root == sd2
    assert t_dual.system_root == sd1
    assert t_dual.ports_dir == sd2 / "ports"
    assert t_dual.scripts_dir == sd2 / "ROMS/Ports"
    assert t_dual.libs_dir == sd1 / "MUOS/PortMaster/libs"
    assert t_dual.media_dir == sd1 / "MUOS/info/catalogue/External - Ports/box"


def test_build_target_pairing(tmp_path):
    sd1 = tmp_path / "sd1"
    (sd1 / "MUOS").mkdir(parents=True)
    (sd1 / "ROMS").mkdir(parents=True)

    sd2 = tmp_path / "sd2"
    (sd2 / "ROMS").mkdir(parents=True)
    (sd2 / "ports").mkdir(parents=True)

    # Automatic pairing when both are available
    t = core.build_target(MUOS, sd2, available_roots=[sd1, sd2])
    assert t.root == sd2
    assert t.system_root == sd1

    # Only card 2 available
    t_solo = core.build_target(MUOS, sd2, available_roots=[sd2])
    assert t_solo.root == sd2
    assert t_solo.system_root is None

    # Card 1 chosen as root
    t_c1 = core.build_target(MUOS, sd1, available_roots=[sd1, sd2])
    assert t_c1.root == sd1
    assert t_c1.system_root is None


def test_install_port_card1_only(tmp_path, monkeypatch):
    import zipfile
    cache_dir = tmp_path / "cache"
    img_dir = tmp_path / "images"
    cache_dir.mkdir(parents=True)
    img_dir.mkdir(parents=True)
    monkeypatch.setattr(core, "DL_DIR", cache_dir)
    monkeypatch.setattr(core, "IMG_DIR", img_dir)

    sd1 = tmp_path / "sd1"
    (sd1 / "MUOS").mkdir(parents=True)
    (sd1 / "ROMS").mkdir(parents=True)

    p = port("mygame", items=["mygame.sh", "mygame"], runtime=["mono-6.12.0"], cover="cover.png")
    # Prep cached zip
    zip_path = cache_dir / p.name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("mygame/data.bin", b"hello")
        zf.writestr("mygame.sh", "#!/bin/sh\n")

    # Prep cached runtime
    rt_file = cache_dir / "mono-6.12.0.squashfs"
    rt_file.write_bytes(b"SQUASHFS_DATA")

    # Prep cached image
    cover_file = img_dir / f"{p.stem}__cover.png"
    cover_file.write_bytes(b"PNG_DATA")

    utils = {
        "mono-6.12.0": {
            "name": "mono-6.12.0",
            "runtime_name": "mono-6.12.0.squashfs",
            "url": "http://example.com/mono.squashfs",
            "size": len(b"SQUASHFS_DATA"),
        }
    }

    t = core.Target(cfw=MUOS, root=sd1)
    core.install_port(p, t, BRICK, utils, log=lambda *_: None)

    assert (sd1 / "ports" / "mygame" / "data.bin").read_bytes() == b"hello"
    assert (sd1 / "ROMS" / "Ports" / "mygame.sh").exists()
    assert (sd1 / "MUOS" / "PortMaster" / "libs" / "mono-6.12.0.squashfs").exists()
    assert (sd1 / "MUOS" / "info" / "catalogue" / "External - Ports" / "box" / "mygame.png").exists()


def test_install_port_card2_only_and_swap_flow(tmp_path, monkeypatch):
    import zipfile
    cache_dir = tmp_path / "cache"
    img_dir = tmp_path / "images"
    cache_dir.mkdir(parents=True)
    img_dir.mkdir(parents=True)
    monkeypatch.setattr(core, "DL_DIR", cache_dir)
    monkeypatch.setattr(core, "IMG_DIR", img_dir)

    sd2 = tmp_path / "sd2"
    (sd2 / "ROMS").mkdir(parents=True)
    (sd2 / "ports").mkdir(parents=True)

    p = port("mygame", items=["mygame.sh", "mygame"], runtime=["mono-6.12.0"], cover="cover.png")
    zip_path = cache_dir / p.name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("mygame/data.bin", b"hello")
        zf.writestr("mygame.sh", "#!/bin/sh\n")

    rt_file = cache_dir / "mono-6.12.0.squashfs"
    rt_file.write_bytes(b"SQUASHFS_DATA")

    cover_file = img_dir / f"{p.stem}__cover.png"
    cover_file.write_bytes(b"PNG_DATA")

    utils = {
        "mono-6.12.0": {
            "name": "mono-6.12.0",
            "runtime_name": "mono-6.12.0.squashfs",
            "url": "http://example.com/mono.squashfs",
            "size": len(b"SQUASHFS_DATA"),
        }
    }

    t = core.Target(cfw=MUOS, root=sd2)
    core.install_port(p, t, BRICK, utils, log=lambda *_: None)

    # Ports & scripts installed on card 2
    assert (sd2 / "ports" / "mygame" / "data.bin").read_bytes() == b"hello"
    assert (sd2 / "ROMS" / "Ports" / "mygame.sh").exists()

    # Never write libs or MUOS to card 2
    assert not (sd2 / "MUOS").exists()
    assert t.libs_dir is None
    assert t.media_dir is None

    # Now test Swap Flow: insert Card 1
    sd1 = tmp_path / "sd1"
    (sd1 / "MUOS").mkdir(parents=True)
    (sd1 / "ROMS").mkdir(parents=True)

    r_cnt, m_cnt = core.copy_runtimes_and_media_to_card1(sd1, [p], BRICK, utils)
    assert r_cnt == 1
    assert m_cnt == 1

    assert (sd1 / "MUOS" / "PortMaster" / "libs" / "mono-6.12.0.squashfs").exists()
    assert (sd1 / "MUOS" / "info" / "catalogue" / "External - Ports" / "box" / "mygame.png").exists()

    # Card 2 still clean
    assert not (sd2 / "MUOS").exists()

    # Invalid card 1 raises ValueError
    with pytest.raises(ValueError, match=r"MUOS folder not found|ไม่พบโฟลเดอร์ MUOS"):
        core.copy_runtimes_and_media_to_card1(tmp_path / "not_card1", [p], BRICK, utils)


def test_install_port_both_paired(tmp_path, monkeypatch):
    import zipfile
    cache_dir = tmp_path / "cache"
    img_dir = tmp_path / "images"
    cache_dir.mkdir(parents=True)
    img_dir.mkdir(parents=True)
    monkeypatch.setattr(core, "DL_DIR", cache_dir)
    monkeypatch.setattr(core, "IMG_DIR", img_dir)

    sd1 = tmp_path / "sd1"
    (sd1 / "MUOS").mkdir(parents=True)
    (sd1 / "ROMS").mkdir(parents=True)

    sd2 = tmp_path / "sd2"
    (sd2 / "ROMS").mkdir(parents=True)
    (sd2 / "ports").mkdir(parents=True)

    p = port("mygame", items=["mygame.sh", "mygame"], runtime=["mono-6.12.0"], cover="cover.png")
    zip_path = cache_dir / p.name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("mygame/data.bin", b"hello")
        zf.writestr("mygame.sh", "#!/bin/sh\n")

    rt_file = cache_dir / "mono-6.12.0.squashfs"
    rt_file.write_bytes(b"SQUASHFS_DATA")

    cover_file = img_dir / f"{p.stem}__cover.png"
    cover_file.write_bytes(b"PNG_DATA")

    utils = {
        "mono-6.12.0": {
            "name": "mono-6.12.0",
            "runtime_name": "mono-6.12.0.squashfs",
            "url": "http://example.com/mono.squashfs",
            "size": len(b"SQUASHFS_DATA"),
        }
    }

    t = core.Target(cfw=MUOS, root=sd2, system_root=sd1)
    core.install_port(p, t, BRICK, utils, log=lambda *_: None)

    # Ports on card 2
    assert (sd2 / "ports" / "mygame" / "data.bin").read_bytes() == b"hello"
    assert (sd2 / "ROMS" / "Ports" / "mygame.sh").exists()
    assert not (sd2 / "MUOS").exists()

    # Runtimes and box art on card 1
    assert (sd1 / "MUOS" / "PortMaster" / "libs" / "mono-6.12.0.squashfs").exists()
    assert (sd1 / "MUOS" / "info" / "catalogue" / "External - Ports" / "box" / "mygame.png").exists()


def test_disk_space_check_per_root(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple
    Usage = namedtuple("Usage", ["total", "used", "free"])

    sd1 = tmp_path / "sd1"
    (sd1 / "MUOS").mkdir(parents=True)
    sd2 = tmp_path / "sd2"
    (sd2 / "ROMS").mkdir(parents=True)

    p = port("mygame", size=10_000_000, runtime=["mono-6.12.0"])
    utils = {
        "mono-6.12.0": {
            "runtime_name": "mono.squashfs",
            "size": 50_000_000,
        }
    }

    t_dual = core.Target(cfw=MUOS, root=sd2, system_root=sd1)

    # 1. Plenty of space on both
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(100_000_000_000, 0, 50_000_000_000))
    warns = core.check_disk_space(t_dual, [p], utils)
    assert warns == []

    # 2. Card 2 low on space
    def mock_usage_c2_low(path):
        if str(path).startswith(str(sd2)):
            return Usage(100_000_000, 95_000_000, 5_000_000)  # free 5MB < need 22MB
        return Usage(100_000_000_000, 0, 50_000_000_000)
    monkeypatch.setattr(shutil, "disk_usage", mock_usage_c2_low)
    warns = core.check_disk_space(t_dual, [p], utils)
    assert len(warns) == 1
    assert "Card 2" in warns[0]

    # 3. Card 1 low on space
    def mock_usage_c1_low(path):
        if str(path).startswith(str(sd1)):
            return Usage(100_000_000, 95_000_000, 5_000_000)  # free 5MB < runtime need 55MB
        return Usage(100_000_000_000, 0, 50_000_000_000)
    monkeypatch.setattr(shutil, "disk_usage", mock_usage_c1_low)
    warns = core.check_disk_space(t_dual, [p], utils)
    assert len(warns) == 1
    assert "Card 1" in warns[0]

    # 4. Both low on space
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(100_000_000, 99_000_000, 1_000_000))
    warns = core.check_disk_space(t_dual, [p], utils)
    assert len(warns) == 2
    assert any("Card 2" in w for w in warns)
    assert any("Card 1" in w for w in warns)

    # 5. Single root target low on space
    t_single = core.Target(cfw=MUOS, root=sd1)
    warns = core.check_disk_space(t_single, [p], utils)
    assert len(warns) == 1
    assert any(k in warns[0].lower() for k in ["space", "พื้นที่การ์ด"])


def test_target_pak_installed_and_edge_cases(tmp_path):
    sd1 = tmp_path / "sd1"
    (sd1 / "MUOS").mkdir(parents=True)
    sd2 = tmp_path / "sd2"
    (sd2 / "ROMS").mkdir(parents=True)
    (sd2 / "ports").mkdir(parents=True)

    t = core.Target(cfw=MUOS, root=sd2, system_root=sd1)
    # muOS is builtin_pm, pak_installed() is always True
    assert t.pak_installed()

    # NextUI custom system_root
    t_nextui = core.Target(cfw=NEXTUI, root=sd2, system_root=sd1)
    (sd1 / "Emus" / "tg5040" / "PORTS.pak").mkdir(parents=True)
    launch_sh = sd1 / "Emus" / "tg5040" / "PORTS.pak" / "launch.sh"
    assert not t_nextui.pak_installed()
    launch_sh.write_text("#!/bin/sh\n")
    assert t_nextui.pak_installed()


def test_disk_space_runtimes_already_installed(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple
    Usage = namedtuple("Usage", ["total", "used", "free"])

    sd1 = tmp_path / "sd1"
    (sd1 / "MUOS" / "PortMaster" / "libs").mkdir(parents=True)
    installed_rt = sd1 / "MUOS" / "PortMaster" / "libs" / "mono.squashfs"
    installed_rt.write_bytes(b"x" * 50_000_000)

    sd2 = tmp_path / "sd2"
    (sd2 / "ROMS").mkdir(parents=True)

    p = port("mygame", size=10_000_000, runtime=["mono-6.12.0"])
    utils = {
        "mono-6.12.0": {
            "runtime_name": "mono.squashfs",
            "size": 50_000_000,
        }
    }

    t_dual = core.Target(cfw=MUOS, root=sd2, system_root=sd1)
    # Since mono.squashfs is already installed with matching size, runtime need is 0!
    assert core.estimate_runtimes_size(t_dual, [p], utils) == 0

    # Even if Card 1 has very little space (e.g. 1MB), no warning should be raised for Card 1
    def mock_usage_c1_low(path):
        if str(path).startswith(str(sd1)):
            return Usage(100_000_000, 99_000_000, 1_000_000)
        return Usage(100_000_000_000, 0, 50_000_000_000)
    monkeypatch.setattr(shutil, "disk_usage", mock_usage_c1_low)
    warns = core.check_disk_space(t_dual, [p], utils)
    assert warns == []


def test_parse_version():
    assert core.parse_version("2.14.0") == (2, 14, 0)
    assert core.parse_version("v2.14.0") == (2, 14, 0)
    assert core.parse_version("2.14.0-1") == (2, 14, 0, 1)
    assert core.parse_version("") == ()
    assert core.parse_version("unknown") == ()


def test_is_pak_update_available():
    assert core.is_pak_update_available(None, "2.14.0") is True
    assert core.is_pak_update_available("unknown", "2.14.0") is True
    assert core.is_pak_update_available("2.13.0", "2.14.0") is True
    assert core.is_pak_update_available("2.14.0", "2.14.0") is False
    assert core.is_pak_update_available("2.15.0", "2.14.0") is False
    assert core.is_pak_update_available("2.14.0", "unknown") is False
    assert core.is_pak_update_available("unknown", "unknown") is False
    assert core.is_pak_update_available("2.14.0", "") is False
    assert core.is_pak_update_available(None, "unknown") is False


def test_get_installed_pak_version(tmp_path):
    card = tmp_path / "card"
    pak_dir = card / "Emus" / "tg5040" / "PORTS.pak"
    t = core.Target(cfw=NEXTUI, root=card)

    # 1. Not installed (no launch.sh)
    assert core.get_installed_pak_version(t) is None

    # 2. launch.sh exists but no version files -> unknown
    pak_dir.mkdir(parents=True)
    (pak_dir / "launch.sh").write_text("#!/bin/sh\n")
    assert core.get_installed_pak_version(t) == "unknown"

    # 3. .portpicker-version exists
    core.write_pak_version(pak_dir, "2.13.0")
    assert core.get_installed_pak_version(t) == "2.13.0"

    # 4. pak.json exists and takes priority over .portpicker-version
    (pak_dir / "pak.json").write_text('{"name": "PORTS", "version": "2.14.0"}')
    assert core.get_installed_pak_version(t) == "2.14.0"

    # 5. Detected platform folder (e.g. tg3040)
    card_tg30 = tmp_path / "card_tg30"
    pak_dir_tg30 = card_tg30 / "Emus" / "tg3040" / "PORTS.pak"
    pak_dir_tg30.mkdir(parents=True)
    (pak_dir_tg30 / "launch.sh").write_text("#!/bin/sh\n")
    (pak_dir_tg30 / "pak.json").write_text('{"version": "2.15.1"}')
    t_tg30 = core.Target(cfw=NEXTUI, root=card_tg30)
    assert core.get_installed_pak_version(t_tg30) == "2.15.1"


def test_clean_old_pak_zips(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DL_DIR", tmp_path)
    (tmp_path / "PORTS.pak.zip").write_text("old")
    (tmp_path / "PORTS.pak-2.13.0.zip").write_text("old 2.13")
    (tmp_path / "PORTS.pak-2.14.0.zip").write_text("new 2.14")
    (tmp_path / "other.zip").write_text("keep other")

    core.clean_old_pak_zips(keep_tag="2.14.0")

    assert not (tmp_path / "PORTS.pak.zip").exists()
    assert not (tmp_path / "PORTS.pak-2.13.0.zip").exists()
    assert (tmp_path / "PORTS.pak-2.14.0.zip").exists()
    assert (tmp_path / "other.zip").exists()


def test_ensure_pak_already_installed_and_fresh_install(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DL_DIR", tmp_path / "dl")
    (tmp_path / "dl").mkdir()

    card = tmp_path / "card"
    card.mkdir()
    t = core.Target(cfw=NEXTUI, root=card)

    # Create dummy fake zip
    zip_path = tmp_path / "fake_pak.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("launch.sh", "#!/bin/sh\n")
        zf.writestr("pak.json", '{"name": "PORTS", "version": "2.14.0"}')

    monkeypatch.setattr(core, "get_latest_pak_info", lambda force=False: ("2.14.0", "http://fake.test/pak.zip"))
    def mock_download(url, dest, progress=None, md5=None, cancel=None):
        shutil.copy2(zip_path, dest)
        return dest
    monkeypatch.setattr(core, "download", mock_download)

    logs = []
    # 1. Fresh install
    core.ensure_pak(t, log=logs.append)
    pak_dir = card / "Emus" / "tg5040" / "PORTS.pak"
    assert (pak_dir / "launch.sh").exists()
    assert (pak_dir / ".portpicker-version").read_text().strip() == "2.14.0"
    assert (card / "Roms" / "Ports (PORTS)" / "Portmaster.sh").exists()
    assert (tmp_path / "dl" / "PORTS.pak-2.14.0.zip").exists()
    assert any("PortMaster pak installed" in m or "ติดตั้ง PortMaster pak แล้ว" in m for m in logs)

    # 2. Already installed -> returns early without downloading again
    download_called = False
    def fail_download(*args, **kwargs):
        nonlocal download_called
        download_called = True
    monkeypatch.setattr(core, "download", fail_download)

    logs.clear()
    core.ensure_pak(t, log=logs.append)
    assert not download_called


def test_update_pak_replaces_and_preserves_squashfs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DL_DIR", tmp_path / "dl")
    (tmp_path / "dl").mkdir()

    card = tmp_path / "card"
    pak_dir = card / "Emus" / "tg5040" / "PORTS.pak"
    old_pm = pak_dir / "PortMaster"
    old_libs = old_pm / "libs"
    old_config = old_pm / "config"
    old_themes = old_pm / "themes" / "custom_dark"
    old_libs.mkdir(parents=True)
    old_config.mkdir(parents=True)
    old_themes.mkdir(parents=True)
    (pak_dir / "launch.sh").write_text("# old launch\n")
    (pak_dir / ".portpicker-version").write_text("2.13.0\n")
    # Existing runtime squashfs and other libs
    (old_libs / "mono-6.12.0.squashfs").write_text("squashfs runtime data")
    (old_libs / "glibc-2.33.squashfs").write_text("glibc runtime data")
    (old_libs / "libcustom.so").write_text("custom so data")
    # Existing configs and themes
    (old_config / "user_settings.json").write_text('{"theme": "dark"}')
    (old_pm / "config.json").write_text('{"favorite": "stardew"}')
    (old_themes / "theme.ini").write_text("accent=#ffb347\n")

    t = core.Target(cfw=NEXTUI, root=card)

    # New pak zip (2.14.0) with new files, but empty libs
    zip_path = tmp_path / "fake_214.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("launch.sh", "# new launch 2.14.0\n")
        zf.writestr("pak.json", '{"name": "PORTS", "version": "2.14.0"}')

    monkeypatch.setattr(core, "get_latest_pak_info", lambda force=False: ("2.14.0", "http://fake.test/pak.zip"))
    monkeypatch.setattr(core, "download", lambda url, dest, progress=None, md5=None, cancel=None: shutil.copy2(zip_path, dest) or dest)

    logs = []
    core.update_pak(t, log=logs.append)

    assert (pak_dir / "launch.sh").read_text() == "# new launch 2.14.0\n"
    assert (pak_dir / ".portpicker-version").read_text().strip() == "2.14.0"
    # Verify squashfs runtimes and all other libs preserved!
    assert (pak_dir / "PortMaster" / "libs" / "mono-6.12.0.squashfs").read_text() == "squashfs runtime data"
    assert (pak_dir / "PortMaster" / "libs" / "glibc-2.33.squashfs").read_text() == "glibc runtime data"
    assert (pak_dir / "PortMaster" / "libs" / "libcustom.so").read_text() == "custom so data"
    # Verify configs and themes preserved!
    assert (pak_dir / "PortMaster" / "config" / "user_settings.json").read_text() == '{"theme": "dark"}'
    assert (pak_dir / "PortMaster" / "config.json").read_text() == '{"favorite": "stardew"}'
    assert (pak_dir / "PortMaster" / "themes" / "custom_dark" / "theme.ini").read_text() == "accent=#ffb347\n"
    # Verify .bak deleted on success
    assert not (card / "Emus" / "tg5040" / "PORTS.pak.bak").exists()
    assert any("PortMaster pak updated successfully" in m or "อัปเดต PortMaster pak สำเร็จ" in m for m in logs)


def test_update_pak_rollback_preserves_runtimes_and_config_on_post_runtime_failure(tmp_path, monkeypatch):
    """Verify that if a failure occurs AFTER the runtime copying step,
    the rollback restores all squashfs runtimes, other libs, and configs without loss."""
    monkeypatch.setattr(core, "DL_DIR", tmp_path / "dl")
    (tmp_path / "dl").mkdir()

    card = tmp_path / "card"
    pak_dir = card / "Emus" / "tg5040" / "PORTS.pak"
    old_pm = pak_dir / "PortMaster"
    old_libs = old_pm / "libs"
    old_config = old_pm / "config"
    old_libs.mkdir(parents=True)
    old_config.mkdir(parents=True)
    (pak_dir / "launch.sh").write_text("# original launch\n")
    (pak_dir / ".portpicker-version").write_text("2.13.0\n")
    (old_libs / "mono-6.12.0.squashfs").write_text("squashfs runtime data")
    (old_libs / "libextra.so").write_text("extra so data")
    (old_config / "user.cfg").write_text("cfg data")

    t = core.Target(cfw=NEXTUI, root=card)

    zip_path = tmp_path / "fake_214.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("launch.sh", "# new launch 2.14.0\n")
        zf.writestr("pak.json", '{"name": "PORTS", "version": "2.14.0"}')

    monkeypatch.setattr(core, "get_latest_pak_info", lambda force=False: ("2.14.0", "http://fake.test/pak.zip"))
    monkeypatch.setattr(core, "download", lambda url, dest, progress=None, md5=None, cancel=None: shutil.copy2(zip_path, dest) or dest)

    # Force failure AFTER the runtime step by making marker write_text fail
    orig_write_text = Path.write_text
    def fail_on_portmaster(self, *args, **kwargs):
        if "Portmaster.sh" in str(self):
            raise RuntimeError("Forced post-runtime copy failure on Portmaster.sh!")
        return orig_write_text(self, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail_on_portmaster)

    logs = []
    import pytest
    with pytest.raises(RuntimeError, match="Forced post-runtime copy failure"):
        core.update_pak(t, log=logs.append)

    # Rollback must restore original pak WITH runtimes, libs, and config!
    assert (pak_dir / "launch.sh").read_text() == "# original launch\n"
    assert (pak_dir / ".portpicker-version").read_text().strip() == "2.13.0"
    assert (pak_dir / "PortMaster" / "libs" / "mono-6.12.0.squashfs").read_text() == "squashfs runtime data"
    assert (pak_dir / "PortMaster" / "libs" / "libextra.so").read_text() == "extra so data"
    assert (pak_dir / "PortMaster" / "config" / "user.cfg").read_text() == "cfg data"
    assert not (card / "Emus" / "tg5040" / "PORTS.pak.bak").exists()
    assert any("restoring original version" in m.lower() or "กู้คืนเวอร์ชันเดิม" in m for m in logs)


def test_update_pak_restores_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DL_DIR", tmp_path / "dl")
    (tmp_path / "dl").mkdir()

    card = tmp_path / "card"
    pak_dir = card / "Emus" / "tg5040" / "PORTS.pak"
    pak_dir.mkdir(parents=True)
    (pak_dir / "launch.sh").write_text("# original launch\n")
    (pak_dir / ".portpicker-version").write_text("2.13.0\n")

    t = core.Target(cfw=NEXTUI, root=card)

    # Invalid corrupted zip
    corrupt_zip = tmp_path / "corrupt.zip"
    corrupt_zip.write_text("not a zip file!")

    monkeypatch.setattr(core, "get_latest_pak_info", lambda force=False: ("2.14.0", "http://fake.test/pak.zip"))
    monkeypatch.setattr(core, "download", lambda url, dest, progress=None, md5=None, cancel=None: shutil.copy2(corrupt_zip, dest) or dest)

    logs = []
    import pytest
    with pytest.raises(Exception):
        core.update_pak(t, log=logs.append)

    # Verify original folder restored!
    assert (pak_dir / "launch.sh").read_text() == "# original launch\n"
    assert (pak_dir / ".portpicker-version").read_text().strip() == "2.13.0"
    assert not (card / "Emus" / "tg5040" / "PORTS.pak.bak").exists()
    assert any("restoring original version" in m.lower() or "กู้คืนเวอร์ชันเดิม" in m for m in logs)


def test_ensure_pak_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DL_DIR", tmp_path / "dl")
    (tmp_path / "dl").mkdir()

    card = tmp_path / "card"
    card.mkdir()
    t = core.Target(cfw=NEXTUI, root=card)

    monkeypatch.setattr(core, "get_latest_pak_info", lambda force=False: ("2.14.0", "http://fake.test/pak.zip"))

    def mock_download_with_cancel(url, dest, progress=None, md5=None, cancel=None):
        if cancel and cancel():
            raise InterruptedError("cancelled")
        return dest
    monkeypatch.setattr(core, "download", mock_download_with_cancel)

    import pytest
    with pytest.raises(InterruptedError):
        core.ensure_pak(t, log=lambda m: None, cancel=lambda: True)

