"""Find games installed through Steam and map them to PortMaster ports.

Only games with a hand-checked recipe are copied automatically. Each recipe was
checked against the port's own install instructions in ports.json, not on a
device. No Qt imports, so this is testable headless.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Recipe:
    appid: str
    dest: str                       # relative to the CFW's ports dir
    files: tuple[str, ...] = ("*",)  # "*" = whole install; "name/" = folder; globs ok
    beta: str = ""                  # required Steam beta branch, if any


# port stem -> recipe. "files" names are matched case-insensitively; a literal
# name is written in the case given here (Linux on the device is case-sensitive).
RECIPES: dict[str, Recipe] = {
    "undertale":        Recipe("391540", "undertale/assets"),
    "downwell":         Recipe("360740", "downwell/assets"),
    "balatro":          Recipe("2379780", "balatro", ("Balatro.exe",)),
    "minit":            Recipe("609490", "minit/assets", ("data.win", "*.ogg", "*.csv")),
    "riskofrain":       Recipe("248820", "riskofrain/gamedata"),
    "fallout1":         Recipe("38400", "fallout1", ("critter.dat", "master.dat", "data/")),
    "fallout2":         Recipe("38410", "fallout2", ("critter.dat", "master.dat", "patch000.dat", "data/")),
    "deltarune":        Recipe("1671210", "deltarune/assets/install"),
    "godstone":         Recipe("1715030", "godstone/assets"),
    "fancypants":       Recipe("1668460", "fancypants/gamedata"),
    "submachinelegacy": Recipe("1564790", "submachinelegacy/assets"),
    "carriesorderup":   Recipe("522490", "carriesorderup/assets"),
    "elecdude":         Recipe("1907980", "elecdude/gamedata"),
    "nidhogg":          Recipe("94400", "nidhogg/assets", beta="before_may_2017"),
}

# ports whose instructions say the Steam (Windows) build will not work
NO_STEAM: dict[str, tuple[str, str]] = {
    "celeste":            ("504230", "ต้องใช้เวอร์ชัน DRM-free (itch.io / Epic)"),
    "axiom.verge":        ("332200", "ต้องใช้เวอร์ชัน DRM-free จาก Epic"),
    "iconoclasts":        ("393520", "ต้องใช้เวอร์ชัน Linux"),
    "shovel.knight":      ("250760", "ต้องใช้เวอร์ชัน Linux"),
    "timespinner":        ("368620", "ต้องใช้เวอร์ชัน Linux"),
    "freedom.planet":     ("248310", "ต้องใช้เวอร์ชัน Linux"),
    "luck.be.a.landlord": ("1404850", "ต้องใช้รุ่นเฉพาะที่โหลดด้วย DepotDownloader"),
}


# --------------------------------------------------------------------------
# VDF (Valve KeyValues text) — enough for libraryfolders.vdf and *.acf
# --------------------------------------------------------------------------

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*')


def parse_vdf(text: str) -> dict:
    root: dict = {}
    stack = [root]
    key: str | None = None
    for m in _TOKEN.finditer(text):
        s, brace = m.group(1), m.group(2)
        if s is None and brace is None:
            continue  # comment
        if brace == "{":
            child: dict = {}
            stack[-1][key or ""] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = re.sub(r"\\(.)", r"\1", s)
            if key is None:
                key = s
            else:
                stack[-1][key] = s
                key = None
    return root


def _ci(d: dict, name: str):
    """Case-insensitive dict lookup (Valve files mix 'AppState'/'appstate')."""
    for k, v in d.items():
        if k.lower() == name.lower():
            return v
    return None


# --------------------------------------------------------------------------
# Library discovery
# --------------------------------------------------------------------------

@dataclass
class SteamApp:
    appid: str
    name: str
    path: Path
    complete: bool
    beta: str = ""


def steam_roots() -> list[Path]:
    roots: list[Path] = []
    if os.name == "nt":
        try:
            import winreg
            for hive, sub, val in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        roots.append(Path(winreg.QueryValueEx(k, val)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
        roots.append(Path(r"C:\Program Files (x86)\Steam"))
    else:
        roots += [Path.home() / ".steam/steam", Path.home() / ".local/share/Steam"]
    seen, out = set(), []
    for r in roots:
        key = os.path.normcase(str(r))
        if key not in seen and (r / "steamapps").is_dir():
            seen.add(key)
            out.append(r)
    return out


def library_dirs(root: Path) -> list[Path]:
    libs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        data = _ci(parse_vdf(vdf.read_text("utf-8", "replace")), "libraryfolders") or {}
    except OSError:
        data = {}
    for v in data.values():
        p = v.get("path") if isinstance(v, dict) else v if isinstance(v, str) and ("\\" in v or "/" in v) else None
        if p:
            libs.append(Path(p))
    seen, out = set(), []
    for l in libs:
        key = os.path.normcase(str(l))
        if key not in seen and (l / "steamapps").is_dir():
            seen.add(key)
            out.append(l)
    return out


def installed_apps(roots: list[Path] | None = None) -> dict[str, SteamApp]:
    apps: dict[str, SteamApp] = {}
    for root in (steam_roots() if roots is None else roots):
        for lib in library_dirs(root):
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                try:
                    st = _ci(parse_vdf(acf.read_text("utf-8", "replace")), "AppState") or {}
                except OSError:
                    continue
                appid, inst = _ci(st, "appid"), _ci(st, "installdir")
                if not appid or not inst:
                    continue
                path = lib / "steamapps" / "common" / inst
                try:
                    flags = int(_ci(st, "StateFlags") or 0)
                except ValueError:
                    flags = 0
                beta = ""
                for sect in ("UserConfig", "MountedConfig"):
                    beta = beta or (_ci(_ci(st, sect) or {}, "BetaKey") or "")
                apps[appid] = SteamApp(appid, _ci(st, "name") or inst, path,
                                       complete=bool(flags & 4) and path.is_dir(), beta=beta)
    return apps


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

@dataclass
class Match:
    app: SteamApp
    recipe: Recipe | None = None
    usable: bool = False
    note: str = ""


def match(stem: str, apps: dict[str, SteamApp]) -> Match | None:
    r = RECIPES.get(stem)
    if r and r.appid in apps:
        a = apps[r.appid]
        if not a.complete:
            return Match(a, r, False, "มีใน Steam แต่ยังติดตั้งไม่เสร็จ — ให้ Steam โหลดให้ครบก่อน")
        if r.beta and a.beta != r.beta:
            return Match(a, r, False, f"มีใน Steam แต่ต้องเปลี่ยน Beta เป็น “{r.beta}” ใน Properties → Betas ก่อน")
        return Match(a, r, True, "🎮 มีใน Steam — ก๊อปไฟล์เกมให้อัตโนมัติ")
    if stem in NO_STEAM and NO_STEAM[stem][0] in apps:
        return Match(apps[NO_STEAM[stem][0]], None, False,
                     "มีใน Steam แต่พอร์ตนี้ใช้เวอร์ชัน Steam ไม่ได้ — " + NO_STEAM[stem][1])
    return None
