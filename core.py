"""PortPicker core: PortMaster catalog, recommendations, local scan, install.

No Qt imports here so it can be tested headless.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import string
import time
import urllib.request
import zipfile
from fnmatch import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from i18n import t as _tr
import steam
from profiles import CFW, DEVICES, MUOS, TESTED_BROKEN, Device

PORTS_JSON_URL = "https://github.com/PortsMaster/PortMaster-New/releases/latest/download/ports.json"
STATUS_WIKI_URL = "https://raw.githubusercontent.com/wiki/ben16w/minui-portmaster/Ports-Status.md"
IMAGE_URL = "https://raw.githubusercontent.com/PortsMaster/PortMaster-New/main/ports/{stem}/{file}"
NEXTUI_PAK_URL = "https://github.com/ben16w/minui-portmaster/releases/latest/download/PORTS.pak.zip"
PAK_RELEASE_API_URL = "https://api.github.com/repos/ben16w/minui-portmaster/releases/latest"
UA = {"User-Agent": "PortPicker/1.0 (+https://portmaster.games)"}

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "PortPicker"
CACHE_DIR = APP_DIR / "cache"
IMG_DIR = CACHE_DIR / "img"
DL_DIR = CACHE_DIR / "downloads"
for _d in (CACHE_DIR, IMG_DIR, DL_DIR):
    _d.mkdir(parents=True, exist_ok=True)

Log = Callable[[str], None]
Progress = Callable[[int, int], None]

# --------------------------------------------------------------------------
# Network helpers
# --------------------------------------------------------------------------

def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download(url: str, dest: Path, progress: Progress | None = None,
             md5: str | None = None, cancel: Callable[[], bool] | None = None) -> Path:
    """Download to dest (via .part). Skips if dest exists and md5 matches."""
    if dest.exists() and (not md5 or file_md5(dest) == md5):
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancel and cancel():
                raise InterruptedError("cancelled")
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if md5 and file_md5(tmp) != md5:
        tmp.unlink(missing_ok=True)
        raise IOError(f"MD5 mismatch for {dest.name}")
    tmp.replace(dest)
    return dest


def file_md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cached_json(url: str, name: str, max_age_h: float = 12) -> dict:
    p = CACHE_DIR / name
    if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return json.loads(p.read_text("utf-8"))
    try:
        data = fetch_bytes(url)
        p.write_bytes(data)
        return json.loads(data)
    except Exception:
        if p.exists():  # offline fallback
            return json.loads(p.read_text("utf-8"))
        raise


BETA_SHEET_ID = "1mCB3_TGnqV9uTfqH1Hb3hCX-ZUmwkuyOgNPx61Jv0CA"
BETA_RESULTS_URL = f"https://docs.google.com/spreadsheets/d/{BETA_SHEET_ID}/gviz/tq?tqx=out:csv&sheet=Results"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def parse_beta_results(text: str) -> list[dict]:
    """Rows of the beta sheet's Results tab (CSV). Columns are found by their header, so the sheet can grow."""
    import csv
    import io
    rows = list(csv.reader(io.StringIO(text or "")))
    if not rows or not rows[0] or rows[0][0].lstrip().startswith("<"):  # not shared -> Google sign-in HTML
        return []
    head = [h.strip().lower() for h in rows[0]]

    def col(pred):
        return next((i for i, h in enumerate(head) if pred(h)), None)
    cols = {
        "date": col(lambda h: h == "date"),
        "tester": col(lambda h: h.startswith("tester")),
        "device": col(lambda h: h == "device"),
        "cfw": col(lambda h: h == "cfw"),
        "setup": col(lambda h: h.startswith("card setup")),
        "port": col(lambda h: h.startswith("port name")),
        "install": col(lambda h: h.startswith("install result")),
        "runs": col(lambda h: h.startswith("runs on device")),
        "artwork": col(lambda h: h.startswith("artwork")),
    }
    if cols["port"] is None or cols["device"] is None or cols["cfw"] is None:
        return []
    out = []
    for r in rows[1:]:
        rec = {k: (r[i].strip() if i is not None and i < len(r) else "") for k, i in cols.items()}
        if rec["port"] and rec["device"] and rec["cfw"]:
            out.append(rec)
    return out


def load_beta_results(max_age_h: float = 1) -> list[dict]:
    """Beta test results from the shared sheet. [] when offline or the sheet isn't shared."""
    try:
        return parse_beta_results(cached_text(BETA_RESULTS_URL, "beta_results.csv", max_age_h))
    except Exception:  # noqa: BLE001 - optional info, never block the port list
        return []


def beta_results_for(port: "Port", dev, cfw, rows: list[dict]) -> list[dict]:
    """Results for this port on this device + CFW ("muOS (2 cards)" counts as muOS)."""
    if dev is None or cfw is None:
        return []
    dn, did, cn = _norm(dev.name), _norm(dev.id), _norm(cfw.name)
    title, stem = _norm(port.title), _norm(port.stem)
    hits = []
    for r in rows:
        rd, rc, rp = _norm(r["device"]), _norm(r["cfw"]), _norm(r["port"])
        if dev.id.startswith("h700-"):  # generic H700 card: results from any H700 model apply
            if not any(k in rd for k in ("rg35xx", "rg34xx", "rg28xx", "rg40xx", "rgcubexx", "h700")):
                continue
        elif rd not in (dn, did) and not (len(rd) >= 4 and (rd in dn or dn in rd)):
            continue
        if not rc.startswith(cn):
            continue
        if rp in (title, stem) or (len(rp) >= 5 and (rp in title or title in rp)):
            hits.append(r)
    return hits


def cached_text(url: str, name: str, max_age_h: float = 12) -> str:
    p = CACHE_DIR / name
    if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
        return p.read_text("utf-8")
    try:
        data = fetch_bytes(url).decode("utf-8", "replace")
        p.write_text(data, "utf-8")
        return data
    except Exception:
        return p.read_text("utf-8") if p.exists() else ""


def image_path(stem: str, file: str) -> Path | None:
    """Return cached image path, downloading if needed. None on failure."""
    if not file:
        return None
    safe = re.sub(r"[^\w.\-]", "_", f"{stem}__{file}")
    p = IMG_DIR / safe
    if p.exists() and p.stat().st_size > 0:
        return p
    try:
        p.write_bytes(fetch_bytes(IMAGE_URL.format(stem=stem, file=file), timeout=20))
        return p
    except Exception:
        return None

# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class Port:
    name: str            # zip name e.g. celeste.zip
    title: str
    desc: str
    inst: str
    genres: list[str]
    rtr: bool            # ready to run (no extra files)
    availability: str    # free/paid/full/demo/""
    reqs: list[str]
    runtime: list[str]
    arch: list[str]
    min_glibc: str
    items: list[str]
    url: str
    md5: str
    size: int
    screenshot: str
    cover: str
    date_updated: str
    stores: list[dict] = field(default_factory=list)
    # computed
    tier: str = "ok"     # must, ok, heavy, broken, incompatible
    reasons: list[str] = field(default_factory=list)
    local_dir: Path | None = None
    local_only: bool = False
    local_script: Path | None = None
    local_note: str = ""
    steam: "steam.Match | None" = None   # set by attach_steam()
    beta: list[dict] = field(default_factory=list)  # beta sheet rows for the chosen device + CFW

    @property
    def stem(self) -> str:
        return self.name[:-4] if self.name.endswith(".zip") else self.name

    @property
    def dirs(self) -> list[str]:
        return [i.rstrip("/") for i in self.items if not i.lower().endswith(".sh")]

    @property
    def scripts(self) -> list[str]:
        return [i for i in self.items if i.lower().endswith(".sh")]

    @property
    def needs_files(self) -> bool:
        return not self.rtr

    @property
    def steam_ready(self) -> bool:
        return bool(self.steam and self.steam.usable)

    @property
    def has_files(self) -> bool:
        """Game files will be supplied (local folder or Steam)."""
        return bool(self.local_dir or self.steam_ready)

    @property
    def is_paid(self) -> bool:
        return self.availability.lower() == "paid" or (not self.rtr and self.availability.lower() not in ("free", "full", "demo"))

    @property
    def is_free(self) -> bool:
        """Port is free and ready-to-run (v1.0 free filter). Excludes demos and paid ports."""
        return bool(self.rtr and self.availability.lower() not in ("demo", "paid"))


def load_catalog(force: bool = False) -> tuple[list[Port], dict]:
    data = cached_json(PORTS_JSON_URL, "ports.json", max_age_h=0 if force else 12)
    ports: list[Port] = []
    for name, v in data.get("ports", {}).items():
        a = v.get("attr", {})
        img = a.get("image") or {}
        src = v.get("source", {})
        ports.append(Port(
            name=name,
            title=a.get("title") or name,
            desc=a.get("desc") or "",
            inst=a.get("inst") or "",
            genres=a.get("genres") or [],
            rtr=bool(a.get("rtr")),
            availability=(a.get("availability") or ""),
            reqs=[r for r in (a.get("reqs") or []) if r],
            runtime=a.get("runtime") or [],
            arch=a.get("arch") or [],
            min_glibc=a.get("min_glibc") or "",
            items=v.get("items") or [],
            url=src.get("url", ""),
            md5=src.get("md5", ""),
            size=int(src.get("size") or 0),
            screenshot=img.get("screenshot") or "",
            cover=(img.get("covers") or [""])[0] or "",
            date_updated=src.get("date_updated", ""),
            stores=a.get("store") or [],
        ))
    return ports, data.get("utils", {})


# --------------------------------------------------------------------------
# NextUI status wiki (live) + bundled fallback
# --------------------------------------------------------------------------

FALLBACK_BROKEN = [
    "2048", "Alien vs Predator", "Braid", "Blossom Tales II", "Death Road to Canada",
    "Dicey Dungeons", "Doom 2 rpg", "Doom rpg", "DoomRL", "Doukutsu-rs", "Garden Story",
    "Gato Roboto", "Jazz2", "Luftrausers", "Mina the Hollower", "Revenge of the Titans",
    "Rocks N Diamonds", "smb1", "smbII", "smworld", "Sonic 1", "Sonic 2", "Sonic CD",
    "SRB2", "SRB2 Kart", "SteelAssault", "Steamworld Heist", "Super Metroid", "Tunics!",
    "Void Call",
]


def parse_status(md: str) -> dict[str, set[str]]:
    out = {"broken": set(), "fixed": set(), "issues": set()}
    section = None
    for line in md.splitlines():
        if line.startswith("## "):
            h = line.lower()
            section = ("broken" if "not running" in h else
                       "fixed" if "now running" in h else
                       "issues" if "issue" in h else None)
            continue
        m = re.match(r"^\* (.+)$", line)  # top-level bullets only
        if m and section:
            name = re.split(r"\s+-\s+", m.group(1))[0].strip()
            name = re.sub(r"\(.*?\)", "", name).strip()
            out[section].add(norm(name))
    if not out["broken"]:
        out["broken"] = {norm(x) for x in FALLBACK_BROKEN}
    return out


def load_status(cfw: CFW) -> dict[str, set[str]]:
    if not cfw.status_wiki:
        return {"broken": set(), "fixed": set(), "issues": set()}
    return parse_status(cached_text(cfw.status_wiki, f"status_{cfw.id}.md"))


# local launcher names → the names the status wiki uses
LOCAL_ALIASES = {"sm": "supermetroid", "smredux": "supermetroid", "smbll": "smbii"}
# local-only ports known to be heavy on A133P-class devices
LOCAL_HEAVY = {"grandtheftauto3", "grandtheftautovicecity", "saltandsanctuary", "sm64", "wipeout"}


def status_hit(port: Port, names: set[str]) -> bool:
    keys = {norm(port.title), norm(port.stem)} | {norm(d) for d in port.dirs}
    keys |= {LOCAL_ALIASES[k] for k in list(keys) if k in LOCAL_ALIASES}
    return bool(keys & names)

# --------------------------------------------------------------------------
# Recommendation
# --------------------------------------------------------------------------

def _ver(s: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in s.split("."))
    except ValueError:
        return ()


def classify(ports: Iterable[Port], dev: Device, cfw: CFW, status: dict) -> None:
    for p in ports:
        p.reasons = []
        if p.local_note:
            p.reasons.append(p.local_note)
        tier = "ok"
        if p.local_only:
            p.tier = "local"
            p.reasons.append(_tr("reason.local_missing_pm", "No longer in PortMaster — copying from local folder as is"))
            if status_hit(p, status["broken"]):
                p.tier = "broken"
                p.reasons.append(_tr("reason.cfw_broken", "Reported broken on {cfw}", cfw=cfw.name))
            elif norm(p.title) in LOCAL_HEAVY:
                p.tier = "heavy"
                p.reasons.append(_tr("reason.heavy", "Demanding on this device — may stutter or take long to load"))
            continue
        arch = p.arch or [dev.arch]          # older entries omit arch → aarch64
        if dev.arch not in arch:
            if set(arch) & dev.extra_arch:
                p.reasons.append(_tr("reason.armhf", "32-bit (armhf) version"))
            else:
                p.tier = "incompatible"; p.reasons.append(_tr("reason.no_arch", "No {arch} build available", arch=dev.arch)); continue
        cfw_tags = {cfw.id.lower(), cfw.name.lower()}
        bad = []
        for r in p.reqs:
            rl = r.lower()
            if rl in ("aarch64", "armhf", "4:3", "!lowres"):
                continue
            if rl in dev.unsupported_reqs:
                bad.append(r)
            elif rl.startswith("!") and (rl[1:] in dev.tags or rl[1:] in cfw_tags):
                bad.append(r)
            elif rl in ("arkos", "rocknix", "muos", "knulli", "retrodeck") and rl not in cfw_tags:
                bad.append(r)
            elif rl.startswith("analog_"):
                need = min(int(x[-1]) for x in rl.split("|"))
                if dev.sticks < need:
                    bad.append(r)
        if bad:
            p.tier = "incompatible"; p.reasons.append(_tr("reason.needs_reqs", "Requires: {reqs}", reqs=", ".join(bad))); continue
        cap = cfw.max_glibc if cfw.max_glibc is not None else dev.max_glibc
        if p.min_glibc and cap and _ver(p.min_glibc) > _ver(cap):
            p.tier = "incompatible"; p.reasons.append(_tr("reason.min_glibc", "Requires glibc {glibc}", glibc=p.min_glibc)); continue
        tested = TESTED_BROKEN.get((dev.id, cfw.id), {}).get(p.stem)
        if tested:
            p.tier = "broken"; p.reasons.append(tested); continue
        if status_hit(p, status["broken"]) and not status_hit(p, status["fixed"]):
            p.tier = "broken"; p.reasons.append(_tr("reason.cfw_broken", "Reported broken on {cfw}", cfw=cfw.name)); continue
        if status_hit(p, status["issues"]):
            p.reasons.append(_tr("reason.minor_issues", "Minor reported issues"))
        if p.stem in dev.heavy or "power" in p.reqs:
            tier = "heavy"; p.reasons.append(_tr("reason.heavy", "Demanding on this device — may stutter or take long to load"))
        elif p.stem in dev.must_have:
            tier = "must"
        if any("weston" in r for r in p.runtime):
            if tier != "heavy":
                tier = "heavy"
            p.reasons.append(_tr("reason.weston", "Uses Weston (high resource usage)"))
        p.tier = tier


TIER_ORDER = {"must": 0, "local": 1, "ok": 2, "heavy": 3, "broken": 4, "incompatible": 5}


def sort_key(p: Port):
    have = 0 if p.has_files else 1
    ready = 0 if (p.rtr or p.has_files) else 1
    return (TIER_ORDER.get(p.tier, 9), have, ready, p.title.lower())

def attach_steam(ports: Iterable[Port], apps: dict) -> int:
    """Mark ports whose game is installed in Steam. Run after classify(),
    which resets reasons. Returns how many can be filled from Steam."""
    n = 0
    for p in ports:
        p.steam = None
        if p.local_only or p.rtr or p.tier == "incompatible":
            continue
        m = steam.match(p.stem, apps)
        if not m:
            continue
        p.steam = m
        if m.usable and p.local_dir:
            p.reasons.append(_tr("reason.also_steam", "🎮 Available on Steam (will use files from selected local folder)"))
        else:
            p.reasons.append(m.note)
        n += m.usable
    return n

# --------------------------------------------------------------------------
# Local folder scan (e.g. D:\port)
# --------------------------------------------------------------------------

GAMEDIR_RE = re.compile(r'^\s*(?:export\s+)?GAMEDIR\s*=\s*"?([^"\n]+)"?', re.I | re.M)


def script_gamedir(sh: Path) -> str | None:
    try:
        txt = sh.read_text("utf-8", "replace")
    except OSError:
        return None
    m = GAMEDIR_RE.search(txt)
    if not m:
        return None
    return m.group(1).strip().rstrip("/").split("/")[-1] or None


# A local folder named after one port may really hold files for another port.
# (port stem the folder matched, port stem it belongs to, file that proves it, note)
LOCAL_REDIRECTS = [
    ("stardewvalley", "stardewvalleymainline", "gamedata/Stardew Valley.deps.json",
     _tr("reason.stardew_16", "Local files are Stardew Valley 1.6 — use with 'Stardew Valley' port, not Compatibility")),
]


def scan_local(root: Path, ports: list[Port]) -> list[Port]:
    """Attach local_dir to matching ports. Returns extra 'local only' ports."""
    if not root or not root.is_dir():
        return []
    dirs = {e.name.lower(): e for e in root.iterdir() if e.is_dir()}
    matched_dirs: set[str] = set()
    for p in ports:
        p.local_dir = None
        p.local_note = ""
        for d in p.dirs:
            hit = dirs.get(d.lower())
            if hit:
                p.local_dir = hit
                matched_dirs.add(d.lower())
                break
    by_stem = {p.stem: p for p in ports}
    for src, dst, proof, note in LOCAL_REDIRECTS:
        a, b = by_stem.get(src), by_stem.get(dst)
        if a and b and a.local_dir and (a.local_dir / proof).is_file() and not b.local_dir:
            b.local_dir, a.local_dir = a.local_dir, None
            b.local_note = note
    # local-only: .sh in root whose GAMEDIR folder exists but isn't in PortMaster
    extras: list[Port] = []
    by_script = {Path(s).name.lower(): p for p in ports for s in p.scripts}
    for sh in sorted(root.glob("*.sh")):
        gd = script_gamedir(sh)
        pm = by_script.get(sh.name.lower())
        if pm:
            # same launcher as a PortMaster port but an older folder name (e.g. devilution)
            if not pm.local_dir and gd and gd.lower() in dirs:
                pm.local_dir = dirs[gd.lower()]
                matched_dirs.add(gd.lower())
            continue
        # skip if the folder belongs to a PortMaster port (it gets merged there)
        if not gd or gd.lower() in matched_dirs or gd.lower() not in dirs:
            continue
        d = dirs[gd.lower()]
        extras.append(Port(
            name=f"local:{sh.stem}", title=sh.stem, desc=_tr("port.local_desc", "Port from local folder (not currently in PortMaster)"),
            inst="", genres=["local"], rtr=True, availability="local", reqs=[], runtime=[],
            arch=[], min_glibc="", items=[sh.name, d.name], url="", md5="", size=0,
            screenshot="", cover="", date_updated="", local_dir=d, local_only=True, local_script=sh,
        ))
    return extras


def folder_size(p: Path, limit_files: int = 200000) -> int:
    total = 0
    for i, (dp, _, fs) in enumerate(os.walk(p)):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
        if i > limit_files:
            break
    return total

# --------------------------------------------------------------------------
# Target (SD card / custom folder)
# --------------------------------------------------------------------------

def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def find_nextui_platform(root: Path) -> str:
    """Detect NextUI/MinUI platform directory on card (.system/<tg*>, Emus/<tg*>).
    Defaults to 'tg5040' for a fresh card."""
    for parent_name in (".system", "Emus"):
        parent = root / parent_name
        if parent.is_dir():
            try:
                for entry in sorted(parent.iterdir()):
                    if entry.is_dir() and entry.name.lower().startswith("tg"):
                        return entry.name
            except OSError:
                pass
    return "tg5040"


@dataclass
class Target:
    cfw: CFW
    root: Path            # SD root for CFWs, or the chosen ports folder for custom
    custom: bool = False
    system_root: Path | None = None

    @property
    def pak_marker(self) -> str:
        if not self.custom and self.cfw.id == "nextui":
            base = self.system_root if self.system_root is not None else self.root
            plat = find_nextui_platform(base)
            return f"Emus/{plat}/PORTS.pak/launch.sh"
        return self.cfw.pak_marker

    @property
    def scripts_dir(self) -> Path:
        return self.root if self.custom else self.root / self.cfw.scripts_dir

    @property
    def ports_dir(self) -> Path:
        return self.root if self.custom else self.root / self.cfw.ports_dir

    def _opt(self, rel: str, base: Path | None = None) -> Path | None:
        """CFW path; with optional_dirs, only if its top folder exists (SD2 has no MUOS/)."""
        if not rel:
            return None
        b = base if base is not None else (self.system_root if self.system_root is not None else self.root)
        if self.cfw.optional_dirs and not (b / Path(rel).parts[0]).is_dir():
            return None
        return b / rel

    @property
    def libs_dir(self) -> Path | None:
        if self.custom:
            base = self.system_root if self.system_root is not None else self.root
            cand = base / "PortMaster" / "libs"
            return cand if cand.parent.is_dir() else None
        if self.cfw.id == "nextui":
            base = self.system_root if self.system_root is not None else self.root
            plat = find_nextui_platform(base)
            return self._opt(f"Emus/{plat}/PORTS.pak/PortMaster/libs", base=base)
        return self._opt(self.cfw.libs_dir)

    @property
    def media_dir(self) -> Path | None:
        return None if self.custom else self._opt(self.cfw.media_dir)

    @property
    def pak_dir(self) -> Path | None:
        if self.custom or self.cfw.builtin_pm or not self.pak_marker:
            return None
        base = self.system_root if self.system_root is not None else self.root
        return base / Path(self.pak_marker).parent

    def pak_installed(self) -> bool:
        if self.custom or self.cfw.builtin_pm or not self.pak_marker:
            return True
        check_root = self.system_root if self.system_root is not None else self.root
        return (check_root / self.pak_marker).exists()


def get_installed_pak_version(t: Target) -> str | None:
    """Return installed pak version string (e.g. '2.14.0', 'unknown'), or None if pak is not installed."""
    if not t.pak_installed():
        return None
    pak_dir = t.pak_dir
    if not pak_dir or not pak_dir.is_dir():
        return None
    # 1. pak.json first
    pak_json = pak_dir / "pak.json"
    if pak_json.exists():
        try:
            data = json.loads(pak_json.read_text("utf-8"))
            ver = str(data.get("version", "")).strip()
            if ver:
                return ver
        except Exception:
            pass
    # 2. .portpicker-version
    pp_ver = pak_dir / ".portpicker-version"
    if pp_ver.exists():
        try:
            ver = pp_ver.read_text("utf-8").strip()
            if ver:
                return ver
        except Exception:
            pass
    return "unknown"


def write_pak_version(pak_dir: Path, tag: str) -> None:
    """Write release tag to PORTS.pak/.portpicker-version."""
    if not tag or tag == "unknown":
        return
    try:
        (pak_dir / ".portpicker-version").write_text(tag.strip() + "\n", "utf-8")
    except OSError:
        pass


def clean_old_pak_zips(keep_tag: str | None = None) -> None:
    """Keep PORTS.pak-<keep_tag>.zip in DL_DIR and remove older or untagged ones."""
    keep_name = f"PORTS.pak-{keep_tag}.zip" if keep_tag and keep_tag != "unknown" else None
    for p in DL_DIR.glob("PORTS.pak*.zip"):
        if keep_name is None or p.name != keep_name:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def parse_version(v: str) -> tuple[int, ...]:
    """Parse semver-like version string into a tuple of ints for comparison."""
    if not v:
        return ()
    v = v.lstrip("vV").strip()
    parts = []
    for chunk in re.split(r"[.\-_]", v):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts)


_latest_pak_cache: tuple[str, str] | None = None


def is_pak_update_available(installed: str | None, latest: str) -> bool:
    """Return True if an update button should be shown:
    when latest is known and installed is None/unknown, or latest is newer than installed.
    Returns False when latest is unknown (e.g. offline)."""
    if not latest or latest.lower() == "unknown":
        return False
    if installed is None or installed.lower() == "unknown":
        return True
    return parse_version(latest) > parse_version(installed)


def get_latest_pak_info(force: bool = False) -> tuple[str, str]:
    """Return (tag_name, download_url) for minui-portmaster pak.
    Cached 12 hours like the catalog. Defaults to ('unknown', NEXTUI_PAK_URL) on error."""
    global _latest_pak_cache
    if not force and _latest_pak_cache is not None:
        return _latest_pak_cache
    try:
        data = cached_json(PAK_RELEASE_API_URL, "pak_latest_release.json", max_age_h=0 if force else 12)
        tag = data.get("tag_name", "").strip()
        url = NEXTUI_PAK_URL
        for asset in data.get("assets", []):
            if asset.get("name") == "PORTS.pak.zip":
                url = asset.get("browser_download_url") or url
                break
        res = (tag or "unknown", url)
        if res[0] != "unknown":
            _latest_pak_cache = res
        return res
    except Exception:
        return ("unknown", NEXTUI_PAK_URL)


def card_matches(cfw: CFW, root: Path) -> bool:
    try:
        if any((root / m).exists() for m in cfw.sd_exclude):
            return False
        for markers in [cfw.sd_markers] + cfw.sd_alt_markers:
            if markers and all((root / m).exists() for m in markers):
                return True
    except OSError:
        pass
    return False


def _probe_card(cfw: CFW, root: Path, timeout: float = 1.5) -> bool:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        f = ex.submit(card_matches, cfw, root)
        try:
            return f.result(timeout=timeout)
        except Exception:
            return False


def detect_sd_cards(cfw: CFW) -> list[Path]:
    roots: list[Path] = []
    if os.name == "nt":
        import ctypes
        removable: list[Path] = []
        fixed: list[Path] = []
        for c in string.ascii_uppercase:
            if c in "AB":
                continue
            path_str = f"{c}:\\"
            try:
                dt = ctypes.windll.kernel32.GetDriveTypeW(path_str)
                # DRIVE_REMOVABLE = 2, DRIVE_FIXED = 3
                if dt == 2:
                    removable.append(Path(path_str))
                elif dt == 3:
                    fixed.append(Path(path_str))
            except Exception:
                fixed.append(Path(path_str))
        roots = removable + fixed
    else:
        candidates = list(Path("/media").glob("*/*")) + list(Path("/mnt").glob("*")) + list(Path("/run/media").glob("*/*"))
        roots = [p for p in candidates if p.is_dir()]

    matched = [r for r in roots if _probe_card(cfw, r)]
    if cfw.id == "muos" and len(matched) > 1:
        matched.sort(key=lambda r: 0 if is_muos_card2(r) else 1)
    return matched


def is_muos_card1(root: Path) -> bool:
    """Check if root is a muOS card 1 (has MUOS folder)."""
    try:
        return any((root / name).is_dir() for name in ("MUOS", "muos"))
    except OSError:
        return False


def is_muos_card2(root: Path) -> bool:
    """Check if root is a muOS card 2 (has ROMS + ports and NO MUOS folder)."""
    try:
        if is_muos_card1(root):
            return False
        has_roms = any((root / name).is_dir() for name in ("ROMS", "roms"))
        has_ports = any((root / name).is_dir() for name in ("ports", "PORTS"))
        return has_roms and has_ports
    except OSError:
        return False


def build_target(cfw: CFW, root: Path, system_root: Path | None = None,
                 available_roots: list[Path] | None = None) -> Target:
    """Build Target, auto-pairing muOS card 2 with card 1 if available."""
    if cfw.id == "muos" and system_root is None and is_muos_card2(root):
        candidates = available_roots if available_roots is not None else detect_sd_cards(cfw)
        card1 = next((c for c in candidates if c != root and is_muos_card1(c)), None)
        if card1 is not None:
            system_root = card1
    return Target(cfw=cfw, root=root, system_root=system_root)


def get_free_space(path: Path) -> int | None:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def estimate_ports_size(ports: list[Port]) -> int:
    total = 0
    for p in ports:
        total += int(p.size * 2.2)
        if p.local_dir and p.local_dir.is_dir():
            total += folder_size(p.local_dir)
    return total


def estimate_runtimes_size(t: Target, ports: list[Port], utils: dict | None = None) -> int:
    if not t.libs_dir or not utils:
        return 0
    needed_names = set()
    for p in ports:
        for r in p.runtime:
            needed_names.add(r)
    total = 0
    for name in needed_names:
        candidates = [u for k, u in utils.items() if k == name or u.get("runtime_name") == name]
        if candidates:
            u = candidates[0]
            dest_name = u.get("runtime_name") or name
            dest = t.libs_dir / dest_name
            size = u.get("size", 0)
            if not (dest.exists() and dest.stat().st_size == size):
                total += size
    return int(total * 1.1)


FAT32_FILE_LIMIT = 4 * 1024 * 1024 * 1024 - 1  # 4 GiB - 1 byte (4,294,967,295 bytes)


def is_fat32_filesystem(path: Path) -> bool:
    """Check whether path resides on a FAT32 filesystem."""
    try:
        p = path.resolve()
        while not p.exists() and p != p.parent:
            p = p.parent
        if os.name == "nt":
            import ctypes
            drive = os.path.splitdrive(str(p))[0] + "\\"
            fs_buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.kernel32.GetVolumeInformationW(drive, None, 0, None, None, None, fs_buf, len(fs_buf)):
                return fs_buf.value.upper() in ("FAT32", "FAT")
            return False
        else:
            with open("/proc/mounts", "r") as f:
                best_mount = ""
                best_fstype = ""
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        m_point, fstype = parts[1], parts[2]
                        if str(p).startswith(m_point) and len(m_point) > len(best_mount):
                            best_mount = m_point
                            best_fstype = fstype
                if best_fstype.lower() in ("vfat", "msdos", "fat"):
                    return True
    except Exception:
        pass
    return False


def check_fat32_limits(t: Target, ports: list[Port], utils: dict | None = None) -> list[str]:
    """Warn before copying any file >= 4 GB to a FAT32 card."""
    warnings = []
    root_fat = is_fat32_filesystem(t.root)
    sys_fat = is_fat32_filesystem(t.system_root) if t.system_root else False

    if not (root_fat or sys_fat):
        return warnings

    if root_fat:
        for p in ports:
            if p.size >= FAT32_FILE_LIMIT:
                warnings.append(_tr("warn.fat32_port_size", "Port '{title}' archive ({size}) exceeds FAT32 4 GB limit",
                                    title=p.title, size=human(p.size)))
            if p.local_dir and p.local_dir.is_dir():
                try:
                    for item in p.local_dir.rglob("*"):
                        if item.is_file() and item.stat().st_size >= FAT32_FILE_LIMIT:
                            warnings.append(_tr("warn.fat32_local_file", "Game file '{name}' ({size}) in {title} exceeds FAT32 4 GB limit",
                                                name=item.name, size=human(item.stat().st_size), title=p.title))
                except OSError:
                    pass

    check_sys = sys_fat or (root_fat and not t.system_root)
    if check_sys and utils:
        needed_runtimes = {r for p in ports for r in p.runtime}
        for name in needed_runtimes:
            candidates = [u for k, u in utils.items() if k == name or u.get("runtime_name") == name]
            if candidates and candidates[0].get("size", 0) >= FAT32_FILE_LIMIT:
                warnings.append(_tr("warn.fat32_runtime_size", "Runtime '{name}' ({size}) exceeds FAT32 4 GB limit",
                                    name=name, size=human(candidates[0].get("size", 0))))

    return warnings


def check_disk_space(t: Target, ports: list[Port], utils: dict | None = None) -> list[str]:
    """Check disk space per root and FAT32 limits. Returns list of warning messages."""
    warnings = []
    port_need = estimate_ports_size(ports)
    runtime_need = estimate_runtimes_size(t, ports, utils)

    root_free = get_free_space(t.root)
    if t.system_root:
        sys_free = get_free_space(t.system_root)
        if root_free is not None and root_free < port_need:
            warnings.append(
                _tr("warn.card2_space", "Card 2 ({root}) may not have enough space: {free} free, ~{need} needed",
                  root=t.root, free=human(root_free), need=human(port_need))
            )
        if sys_free is not None and sys_free < runtime_need:
            warnings.append(
                _tr("warn.card1_space", "Card 1 ({root}) may not have enough space for runtime: {free} free, ~{need} needed",
                  root=t.system_root, free=human(sys_free), need=human(runtime_need))
            )
    else:
        total_need = port_need + runtime_need
        if root_free is not None and root_free < total_need:
            warnings.append(
                _tr("warn.card_space", "Card ({root}) may not have enough space: {free} free, ~{need} needed",
                  root=t.root, free=human(root_free), need=human(total_need))
            )
    warnings.extend(check_fat32_limits(t, ports, utils))
    return warnings


def _get_drive_type(drive_root: str) -> int:
    """Return GetDriveTypeW for a drive root on Windows, or 2 (REMOVABLE) fallback."""
    import ctypes
    windll = getattr(ctypes, "windll", None)
    if windll is not None:
        k32 = getattr(windll, "kernel32", None)
        if k32 and hasattr(k32, "GetDriveTypeW"):
            return k32.GetDriveTypeW(drive_root)
    return 2


def is_pc_drive(path: Path, cfw: CFW | None = None) -> bool:
    """Refuse host PC system drive and non-removable drives.
    On Windows: refuse system drive (C:) and FIXED drives unless card_matches(cfw, path) is True.
    On Linux: refuse system root paths (/, /usr, /home, etc.) unless under removable mountpoints.
    """
    try:
        p = path.resolve()
    except Exception:
        return True

    if os.name == "nt":
        try:
            sys_drive = os.environ.get("SystemDrive", "C:").upper().rstrip("\\")
            path_drive = os.path.splitdrive(str(p))[0].upper().rstrip("\\")
            if not path_drive or path_drive == sys_drive:
                return True
            drive_root = path_drive + "\\"
            dt = _get_drive_type(drive_root)
            if dt == 2:  # DRIVE_REMOVABLE
                return False
            if dt == 3:  # DRIVE_FIXED
                # USB card readers sometimes report FIXED, allow only if card matches
                if cfw and (card_matches(cfw, p) or card_matches(cfw, Path(drive_root))):
                    return False
                return True
            # Other drive types (remote, cdrom, etc.) are blocked
            return True
        except Exception:
            return True
    else:
        str_p = str(p)
        removable_prefixes = ("/media/", "/mnt/", "/run/media/", "/Volumes/")
        if any(str_p.startswith(pref) for pref in removable_prefixes):
            return False
        # Allow /tmp for automated testing
        if str_p.startswith("/tmp"):
            return False
        system_prefixes = ("/", "/usr", "/etc", "/var", "/home", "/root", "/boot", "/bin", "/sbin", "/opt")
        for pref in system_prefixes:
            if str_p == pref or str_p.startswith(pref.rstrip("/") + "/"):
                return True
        return False


def detect_card_device(cfw: CFW, root: Path, system_root: Path | None = None) -> str | None:
    """Read the device or platform id from the card if available."""
    base = system_root if system_root is not None else root
    if cfw.id == "nextui":
        return find_nextui_platform(base)
    elif cfw.id == "muos":
        candidates = [
            base / "MUOS" / "device",
            base / "MUOS" / "device.txt",
            base / "MUOS" / "info" / "device.txt",
            base / "MUOS" / "info" / "device",
            base / "MUOS" / "config" / "board" / "name",
            base / "MUOS" / "config" / "device" / "board" / "name",
            base / "sysdiag.txt",
        ]
        for c in candidates:
            try:
                if c.is_file():
                    txt = c.read_text(encoding="utf-8", errors="ignore").strip()
                    if txt:
                        if c.name == "sysdiag.txt":
                            for line in txt.splitlines():
                                if ":" in line and any(k in line.lower() for k in ("device", "board")):
                                    return line.split(":", 1)[1].strip()
                        return txt.splitlines()[0].strip()
            except OSError:
                pass
    elif cfw.id == "knulli":
        conf = base / "system" / "batocera.conf"
        try:
            if conf.is_file():
                for line in conf.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("system.architecture=") or line.startswith("system.device="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            pass
        board = base / "boot" / "batocera.board"
        try:
            if board.is_file():
                return board.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            pass
    elif cfw.id == "rocknix":
        board = base / "rocknix.board"
        try:
            if board.is_file():
                return board.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            pass
    elif cfw.id == "spruce":
        for p in (base / "spruce" / "bin" / "device", base / "Persistent" / "spruce" / "device", base / "spruce" / "device"):
            try:
                if p.is_file():
                    return p.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                pass
    return None


def check_device_card_match(dev: Device, card_device_str: str | None) -> tuple[bool | None, str]:
    """Returns (matched, reason):
    True -> match confirmed
    False -> known mismatch (block install)
    None -> unknown (prompt confirmation dialog)
    """
    if not card_device_str:
        return (None, "Card has no device info")

    s = card_device_str.lower().strip()
    dev_id = dev.id.lower()
    dev_name = dev.name.lower()

    # NextUI platform checks (e.g. tg5040, tg5050, tg4040, h700)
    if s.startswith("tg") or s == "h700":
        if dev_id == "brickpro":
            if "tg5040" in s or "brick" in s:
                return (True, f"Matched platform '{card_device_str}' for {dev.name}")
            return (False, f"Device mismatch: card has platform '{card_device_str}', but selected device is {dev.name} (requires tg5040)")
        elif "h700" in s:
            if "h700" in dev.chip.lower() or "rg35xx" in dev_id or "rg40xx" in dev_id or "rg28xx" in dev_id:
                return (True, f"Matched platform '{card_device_str}' for {dev.name}")
            return (False, f"Device mismatch: card is for H700 handhelds, but selected device is {dev.name}")

    # Handheld model name checks
    if "brick" in s or "tg5040" in s or "a133" in s:
        if dev_id == "brickpro":
            return (True, f"Matched {card_device_str}")
        return (False, f"Device mismatch: card is for TrimUI Brick Pro, but selected device is {dev.name}")

    # Anbernic H700 family
    h700_devices = ("rg35xx", "rg28xx", "rg40xx", "rgcubexx")
    card_is_h700 = any(h in s for h in h700_devices) or "h700" in s
    dev_is_h700 = any(h in dev_id for h in h700_devices) or "h700" in dev.chip.lower()

    if card_is_h700 and not dev_is_h700:
        return (False, f"Device mismatch: card is for {card_device_str}, but selected device is {dev.name}")
    if dev_is_h700 and not card_is_h700 and ("brick" in s or "rk3566" in s or "rk3326" in s):
        return (False, f"Device mismatch: card is for {card_device_str}, but selected device is {dev.name}")

    if dev_id.startswith("h700-") and card_is_h700:  # generic H700 cards accept any H700 model
        return (True, f"Matched {card_device_str} (H700)")

    if dev_id in s or dev_name in s or (len(s) >= 4 and s in dev_name):
        return (True, f"Matched {card_device_str}")

    s_clean = s.replace("-", "").replace("_", "").replace(" ", "")
    dev_id_clean = dev_id.replace("-", "").replace("_", "").replace(" ", "")
    dev_name_clean = dev_name.replace("-", "").replace("_", "").replace(" ", "")
    if dev_id_clean in s_clean or (len(s_clean) >= 4 and (s_clean in dev_id_clean or s_clean in dev_name_clean)):
        return (True, f"Matched {card_device_str}")

    return (None, f"Device info '{card_device_str}' on card is unverified for {dev.name}")


def read_cfw_version(cfw: CFW, root: Path, system_root: Path | None = None) -> str | None:
    """Read the CFW release version from the card if available."""
    base = system_root if system_root is not None else root
    candidates: list[Path] = []
    if cfw.id == "nextui":
        candidates = [base / ".system" / "version.txt", base / "version.txt", base / ".system" / "version"]
    elif cfw.id == "muos":
        theme_ver = base / "MUOS" / "theme" / "MustardOS" / "version.txt"
        if theme_ver.is_file():
            candidates = [theme_ver]
        else:
            candidates = list((base / "MUOS" / "theme").glob("*/version.txt")) if (base / "MUOS" / "theme").is_dir() else []
        candidates.append(base / "opt" / "muos" / "config" / "system" / "version")
    elif cfw.id == "knulli":
        candidates = [base / "system" / "version", base / "batocera.version", base / "system" / "batocera.version", base / "version"]
    elif cfw.id == "rocknix":
        candidates = [base / "tools" / "version", base / "rocknix.version", base / "version"]
    elif cfw.id == "spruce":
        candidates = [base / "spruce" / "version", base / "spruce" / "version.txt", base / "Persistent" / "version"]
    elif cfw.id in ("arkos", "darkos"):
        candidates = [base / "tools" / "version", base / "version"]
    elif cfw.id == "crossmix":
        candidates = [base / "Apps" / "CrossMix" / "version", base / "trimui.version"]
    else:
        candidates = [base / "version.txt", base / "version"]

    for c in candidates:
        try:
            if c.is_file():
                txt = c.read_text(encoding="utf-8", errors="ignore").strip()
                if txt:
                    for line in txt.splitlines():
                        line = line.strip()
                        if line:
                            return line
        except OSError:
            pass
    return None


def check_cfw_version_minimum(cfw: CFW, installed_version: str | None) -> tuple[bool, str | None]:
    """Check if the installed CFW version meets the profile's minimum version requirement."""
    if not cfw.min_cfw_version or not installed_version:
        return (True, None)
    inst_v = parse_version(installed_version)
    min_v = parse_version(cfw.min_cfw_version)
    if inst_v < min_v:
        msg = _tr("err.cfw_version_too_low",
                  "Update your CFW first. {cfw} {installed} is below the minimum required version ({min_ver}).",
                  cfw=cfw.name, installed=installed_version, min_ver=cfw.min_cfw_version)
        return (False, msg)
    return (True, None)


def read_portmaster_version(cfw: CFW, root: Path, system_root: Path | None = None) -> str | None:
    """Read the PortMaster version string from the card if available."""
    t = Target(cfw=cfw, root=root, system_root=system_root)
    if cfw.id == "nextui":
        return get_installed_pak_version(t)
    base = system_root if system_root is not None else root
    candidates: list[Path] = []
    if cfw.id == "muos":
        candidates = [base / "MUOS" / "PortMaster" / "version", base / "PortMaster" / "version"]
    elif cfw.id == "knulli":
        candidates = [base / "system" / ".local" / "share" / "PortMaster" / "version", base / "roms" / "ports" / "PortMaster" / "version"]
    elif cfw.id == "rocknix":
        candidates = [base / "roms" / "ports" / "PortMaster" / "version", base / "ports" / "PortMaster" / "version"]
    elif cfw.id == "spruce":
        candidates = [base / "Persistent" / "portmaster" / "PortMaster" / "version", base / "Roms" / "ports" / "PortMaster" / "version"]
    elif cfw.id in ("arkos", "darkos"):
        candidates = [base / "tools" / "PortMaster" / "version", base / "ports" / "PortMaster" / "version"]
    elif cfw.id == "crossmix":
        candidates = [base / "Apps" / "PortMaster" / "PortMaster" / "version", base / "Data" / "ports" / "PortMaster" / "version"]
    else:
        candidates = [base / "PortMaster" / "version"]

    if t.libs_dir:
        candidates.append(t.libs_dir.parent / "version")

    for c in candidates:
        try:
            if c.is_file():
                txt = c.read_text(encoding="utf-8", errors="ignore").strip()
                if txt:
                    for line in txt.splitlines():
                        line = line.strip()
                        if line:
                            return line
        except OSError:
            pass
    return None

# --------------------------------------------------------------------------
# Install
# --------------------------------------------------------------------------

def safe_extract(zf: zipfile.ZipFile, dest: Path, log: Log | None = None,
                 cancel: Callable[[], bool] | None = None) -> list[str]:
    dest = dest.resolve()
    names = []
    for m in zf.infolist():
        if cancel and cancel():
            raise InterruptedError("cancelled")
        target = (dest / m.filename).resolve()
        try:
            if not target.is_relative_to(dest) or target == dest:
                raise ValueError(f"unsafe path in zip (path traversal): {m.filename}")
        except AttributeError:
            if os.path.commonpath([str(dest), str(target)]) != str(dest) or target == dest:
                raise ValueError(f"unsafe path in zip (path traversal): {m.filename}")
        if m.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".pp-part")
        with zf.open(m) as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        os.replace(tmp, target)
        names.append(m.filename)
    return names


# --------------------------------------------------------------------------
# Placing local game files into a (newer) PortMaster port
# --------------------------------------------------------------------------
# A local folder is either a whole old port (game files in gamedata/ or the
# root, plus old loaders/libs/patches) or a raw game folder. New ports often
# moved the data folder (gamedata -> assets), so files are re-mapped to the
# folder the new port expects and old-port leftovers are dropped.

DATA_DIRS = ("assets", "gamedata", "data", "game")
_OLD_PORT_SIGNS = re.compile(
    r"^(port\.json|gameinfo\.xml|.*\.gptk|gmloader.*|.*\.apk|.*\.port|libs?(\.\w+)?|lib|patch|tools|box86|box64|"
    r"licen[cs]es?|.*\.so(\.\d+)*)$", re.I)
_ARTIFACT = re.compile(
    r"^(port\.json|gameinfo\.xml|.*\.gptk(\.\w+)?|gmloader[\w\.\-]*|.*\.apk|.*\.port|.*\.md|cover\.(png|jpg)|"
    r"screenshot\.(png|jpg)|.*\.sh|.*\.so(\.\d+)*|.*\.aarch64|.*\.armhf|xdelta3|log\.txt|patchlog\.txt|"
    r"install_completed|libs?(\.\w+)?|lib|tools|patch|patches|licen[cs]es?|box86|box64|bin|utils|dlls|conf|config|"
    r"saves?|savedata|.*\.port\.json|.*\.screenshot\.\w+|.*\.cover\.(png|jpg)|license.*|.*\.xdelta|oga_controls.*|"
    r"gamecontrollerdb.*\.txt|install_log\.txt|console_history\.txt|svloader\.exe)$", re.I)
_PLACEHOLDER = re.compile(r"^(\.gitkeep|.*put.*here.*|.*place.*here.*|.*copy.*here.*|.*game.?files.*)$", re.I)


def expected_data_dir(root: str, zip_names: list[str], inst: str) -> str | None:
    """Folder directly under the port root where the new port wants game files."""
    rp = root.rstrip("/") + "/"
    for n in zip_names:
        if n.startswith(rp):
            parts = n[len(rp):].split("/")
            if len(parts) == 2 and parts[0].lower() in DATA_DIRS and _PLACEHOLDER.match(parts[1]):
                return parts[0]
    m = re.search(r"ports/" + re.escape(root.rstrip("/")) + r"/(assets|gamedata|data|game)\b", inst or "", re.I)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:in|into|to|inside)\s+(?:the\s+)?[\"'`]?(assets|gamedata)[\"'`]?\s+(?:folder|directory|dir)\b",
                  inst or "", re.I)
    return m.group(1) if m else None


@dataclass
class FilePlan:
    mode: str                      # "mirror", "gamedata->assets", "root->assets"
    want: str | None               # data folder the new port expects
    copy: list[tuple[str, str]]    # (path in local folder, path in port folder)
    warn: str = ""


def plan_local_files(root: str, zip_names: list[str], inst: str, local_files: list[str]) -> FilePlan:
    rp = root.rstrip("/") + "/"
    shipped = {n[len(rp):] for n in zip_names if n.startswith(rp) and not n.endswith("/")}
    tops = {f.split("/", 1)[0] for f in local_files if "/" in f}
    want = expected_data_dir(root, zip_names, inst)
    local_data = sorted(t for t in tops if t.lower() in DATA_DIRS)
    remap: dict[str, str] = {}
    mode = "mirror"
    if want and not any(t.lower() == want.lower() for t in tops):
        src = local_data[0] if local_data else ""
        remap[src] = want
        mode = f"{src or 'root'}->{want}"
    copy = []
    for f in local_files:
        name = f.rsplit("/", 1)[-1]
        top = f.split("/", 1)[0]
        if _PLACEHOLDER.match(name):
            continue
        if "" in remap:
            if _ARTIFACT.match(top):           # old-port file or folder at the root
                continue
            dest = f"{remap['']}/{f}"
        elif "/" in f and top in remap:
            dest = remap[top] + f[len(top):]
        else:
            if _ARTIFACT.match(top):
                continue
            dest = f
        if dest in shipped:                    # never replace files that come with the port
            continue
        copy.append((f, dest))
    names = {f.rsplit("/", 1)[-1].lower() for f in local_files}
    warn = ""
    if not copy:
        warn = _tr("warn.no_game_files", "Local folder has no game files")
    elif "game.droid" in names and not names & {"data.win", "game.unx", "game.ios"}:
        warn = _tr("warn.patched_droid", "Game files are patched for an older port (game.droid) — new port may require original files")
    return FilePlan(mode, want, copy, warn)


def list_files(folder: Path) -> list[str]:
    out = []
    for dp, _, files in os.walk(folder):
        rel = Path(dp).relative_to(folder)
        for f in files:
            out.append((rel / f).as_posix() if str(rel) != "." else f)
    return out


def copy_planned(src_root: Path, dst_root: Path, plan: FilePlan, overwrite: bool,
                 cancel: Callable[[], bool] | None = None) -> tuple[int, int]:
    copied = skipped = 0
    for rel_src, rel_dst in plan.copy:
        if cancel and cancel():
            raise InterruptedError("cancelled")
        s = src_root / rel_src
        d = dst_root / rel_dst
        if d.exists() and not overwrite and d.stat().st_size == s.stat().st_size:
            skipped += 1
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        tmp = d.with_name(d.name + ".pp-part")
        shutil.copy2(s, tmp)
        os.replace(tmp, d)
        copied += 1
    return copied, skipped


_KEEP_ON_CLEAN = re.compile(r"^(saves?|savedata)$|\.sav$", re.I)


def clean_port_dir(port_dir: Path, log: Log) -> None:
    """Remove an installed port folder before a fresh install, keeping save data."""
    if not port_dir.is_dir():
        return
    removed = 0
    for entry in list(port_dir.iterdir()):
        if _KEEP_ON_CLEAN.search(entry.name):
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        removed += 1
    log(_tr("log.clean_old", "  🧹 Cleaned old files in {name} ({count} items, saves preserved)",
          name=port_dir.name, count=removed))


def merge_tree(src: Path, dst: Path, overwrite: bool, log: Log,
               cancel: Callable[[], bool] | None = None) -> tuple[int, int]:
    copied = skipped = 0
    for dp, _, files in os.walk(src):
        rel = Path(dp).relative_to(src)
        for f in files:
            if cancel and cancel():
                raise InterruptedError("cancelled")
            s = Path(dp) / f
            d = dst / rel / f
            if copy_file(s, d, overwrite):
                copied += 1
            else:
                skipped += 1
    return copied, skipped


def copy_file(s: Path, d: Path, overwrite: bool) -> bool:
    """Copy via .pp-part. Returns False if skipped (already complete)."""
    # skip only files that are already complete (same size); a copy cut off
    # by closing the app leaves a short file, which gets copied again here
    if d.exists() and not overwrite and d.stat().st_size == s.stat().st_size:
        return False
    d.parent.mkdir(parents=True, exist_ok=True)
    tmp = d.with_name(d.name + ".pp-part")
    shutil.copy2(s, tmp)
    os.replace(tmp, d)
    return True


def copy_from_steam(p: Port, t: Target, overwrite: bool, log: Log,
                    cancel: Callable[[], bool] | None = None) -> tuple[int, int]:
    r, src = p.steam.recipe, p.steam.app.path
    dst = t.ports_dir / r.dest
    log(_tr("log.copy_steam", "  🎮 Copying game files from Steam: {src}", src=src))
    if r.files == ("*",):
        return merge_tree(src, dst, overwrite, log, cancel)
    entries = list(src.iterdir())
    copied = skipped = 0
    missing = []
    for pat in r.files:
        want_dir = pat.endswith("/")
        name = pat.rstrip("/")
        literal = not any(ch in name for ch in "*?[")
        hits = [e for e in entries if fnmatch(e.name.lower(), name.lower()) and e.is_dir() == want_dir]
        if not hits:
            missing.append(pat)
        for e in hits:
            out = dst / (name if literal else e.name)
            if want_dir:
                c, s = merge_tree(e, out, overwrite, log, cancel)
            else:
                if cancel and cancel():
                    raise InterruptedError("cancelled")
                c, s = (1, 0) if copy_file(e, out, overwrite) else (0, 1)
            copied += c
            skipped += s
    if missing:
        log(_tr("log.missing_steam", "  ⚠ Not found in Steam folder: {files}", files=", ".join(missing)))
    return copied, skipped


def ensure_pak(t: Target, log: Log, progress: Progress | None = None,
               cancel: Callable[[], bool] | None = None) -> None:
    if t.cfw.builtin_pm and not t.custom and t.libs_dir is None:
        log(_tr("log.card2_no_libs",
              "⚠ Card has no {dir} folder (likely Card 2) — runtimes will not be copied; launch game via on-device PortMaster to download runtime",
              dir=Path(t.cfw.libs_dir).parts[0]))
    if t.pak_installed():
        return
    c = t.cfw
    pak_dir = t.pak_dir
    if not pak_dir:
        return
    latest_tag, download_url = get_latest_pak_info()
    url = download_url if download_url else c.pak_url
    log(_tr("log.install_pak", "Installing PortMaster pak for {name} ({tag}) ...",
          name=c.name, tag=latest_tag if latest_tag != 'unknown' else ''))
    zip_name = f"PORTS.pak-{latest_tag}.zip" if latest_tag != "unknown" else "PORTS.pak.zip"
    z = download(url, DL_DIR / zip_name, progress, cancel=cancel)
    clean_old_pak_zips(keep_tag=latest_tag if latest_tag != "unknown" else None)
    pak_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(z) as zf:
        safe_extract(zf, pak_dir, cancel=cancel)
    write_pak_version(pak_dir, latest_tag)
    t.scripts_dir.mkdir(parents=True, exist_ok=True)
    marker = t.scripts_dir / "Portmaster.sh"
    if not marker.exists():
        marker.write_text("# Portmaster.sh\n# This file will signal to open the PortMaster GUI when placed in the Roms directory.\n")
    log(_tr("log.pak_installed", "✔ PortMaster pak installed"))


def _copy_tree_merge(src: Path, dst: Path, cancel: Callable[[], bool] | None = None,
                     overwrite: bool = True) -> None:
    """Recursively copy all contents from src into dst. If overwrite=False, preserve existing files in dst."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if cancel and cancel():
            raise InterruptedError("cancelled")
        target = dst / item.name
        if item.is_dir():
            if target.is_file():
                if overwrite:
                    target.unlink()
                else:
                    continue
            _copy_tree_merge(item, target, cancel=cancel, overwrite=overwrite)
        else:
            if target.exists() and not overwrite:
                continue
            if target.is_dir():
                shutil.rmtree(target)
            shutil.copy2(item, target)


def update_pak(t: Target, log: Log = print, progress: Progress | None = None,
               cancel: Callable[[], bool] | None = None) -> None:
    c = t.cfw
    if t.custom or t.cfw.builtin_pm or not t.pak_marker:
        log(_tr("log.pak_not_used", "⚠ {name} does not use PortMaster pak", name=c.name))
        return

    pak_dir = t.pak_dir
    if not pak_dir:
        return
    bak_dir = pak_dir.with_name(pak_dir.name + ".bak")

    # Check free space before starting pak update
    free_bytes = get_free_space(pak_dir)
    old_libs = (pak_dir / "PortMaster" / "libs") if pak_dir.exists() else None
    needed_bytes = 100 * 1024 * 1024 + (folder_size(old_libs) if old_libs and old_libs.exists() else 0) + 20 * 1024 * 1024
    if free_bytes is not None and free_bytes < needed_bytes:
        raise RuntimeError(_tr("err.pak_update_space", "Not enough free space on card for PortMaster pak update ({free} free, ~{need} needed)",
                          free=human(free_bytes), need=human(needed_bytes)))

    latest_tag, download_url = get_latest_pak_info()
    url = download_url if download_url else c.pak_url
    zip_name = f"PORTS.pak-{latest_tag}.zip" if latest_tag != "unknown" else "PORTS.pak.zip"
    zip_path = DL_DIR / zip_name

    log(_tr("log.download_pak", "Downloading PortMaster pak ({tag}) ...",
          tag=latest_tag if latest_tag != 'unknown' else ''))
    z = download(url, zip_path, progress, cancel=cancel)

    log(_tr("log.updating_pak", "Updating PortMaster pak..."))
    if bak_dir.exists():
        shutil.rmtree(bak_dir)

    had_old_pak = pak_dir.exists()
    if had_old_pak:
        pak_dir.rename(bak_dir)

    pak_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(z) as zf:
            safe_extract(zf, pak_dir, cancel=cancel)
        write_pak_version(pak_dir, latest_tag)

        # Preserve user runtimes, configs, and themes from bak_dir by copying (never moving)
        # Helper libs from the new pak win over old same-named files (overwrite=False)
        if had_old_pak and bak_dir.exists():
            old_pm = bak_dir / "PortMaster"
            new_pm = pak_dir / "PortMaster"
            if old_pm.is_dir():
                new_pm.mkdir(parents=True, exist_ok=True)
                # 1. libs (all squashfs runtimes and any helper libraries; prefer new pak libs)
                old_libs_dir = old_pm / "libs"
                if old_libs_dir.is_dir():
                    _copy_tree_merge(old_libs_dir, new_pm / "libs", cancel=cancel, overwrite=False)
                # 2. config folder
                old_config = old_pm / "config"
                if old_config.is_dir():
                    _copy_tree_merge(old_config, new_pm / "config", cancel=cancel)
                # 3. themes folder
                old_themes = old_pm / "themes"
                if old_themes.is_dir():
                    _copy_tree_merge(old_themes, new_pm / "themes", cancel=cancel)
                # 4. config.json at PortMaster root if present
                old_cfg_json = old_pm / "config.json"
                if old_cfg_json.is_file():
                    shutil.copy2(old_cfg_json, new_pm / "config.json")

        t.scripts_dir.mkdir(parents=True, exist_ok=True)
        marker = t.scripts_dir / "Portmaster.sh"
        if not marker.exists():
            marker.write_text("# Portmaster.sh\n# This file will signal to open the PortMaster GUI when placed in the Roms directory.\n")

        try:
            clean_old_pak_zips(keep_tag=latest_tag if latest_tag != "unknown" else None)
        except Exception:
            pass
        log(_tr("log.pak_updated", "✔ PortMaster pak updated successfully"))
    except Exception as e:
        log(_tr("log.pak_update_failed_restoring", "✖ Pak update failed ({err}) — restoring original version...", err=e))
        if pak_dir.exists():
            shutil.rmtree(pak_dir, ignore_errors=True)
        if had_old_pak and bak_dir.exists():
            bak_dir.rename(pak_dir)
        raise

    # Clean up .bak outside the rollback block so cleanup failure cannot revert successful update
    if bak_dir.exists():
        try:
            shutil.rmtree(bak_dir)
        except Exception as e:
            log(f"  ⚠ Could not remove backup folder {bak_dir}: {e}")


def install_runtime(name: str, utils: dict, dev: Device, t: Target, log: Log = print,
                    progress: Progress | None = None,
                    cancel: Callable[[], bool] | None = None) -> bool:
    if cancel and cancel():
        raise InterruptedError("cancelled")
    libs = t.libs_dir
    if not libs:
        log(_tr("log.libs_unknown_skip",
              "  ⚠ Libs location unknown — skipping runtime {name} (PortMaster on handheld will download it when launched)",
              name=name))
        return False
    candidates = [u for k, u in utils.items()
                  if k == name or u.get("runtime_name") == name]
    candidates = [u for u in candidates if u.get("runtime_arch", dev.arch) == dev.arch] or candidates
    if not candidates:
        log(_tr("log.runtime_not_found", "  ⚠ Runtime {name} not found in catalog", name=name))
        return False
    u = candidates[0]
    dest = libs / (u.get("runtime_name") or name)
    if dest.exists() and dest.stat().st_size == u.get("size", -1):
        return False
    libs.mkdir(parents=True, exist_ok=True)
    log(f"  ↓ runtime {dest.name} ({u.get('size', 0) / 1e6:.0f} MB)")
    if cancel and cancel():
        raise InterruptedError("cancelled")
    cache = download(u["url"], DL_DIR / dest.name, progress, md5=u.get("md5"), cancel=cancel)
    if cancel and cancel():
        raise InterruptedError("cancelled")
    shutil.copy2(cache, dest)
    return True


def install_port(p: Port, t: Target, dev: Device, utils: dict, log: Log,
                 progress: Progress | None = None, overwrite_local: bool = False,
                 cancel: Callable[[], bool] | None = None, clean: bool = False) -> None:
    t.ports_dir.mkdir(parents=True, exist_ok=True)
    t.scripts_dir.mkdir(parents=True, exist_ok=True)

    if p.local_only:
        log(_tr("log.port_from_pc", "▶ {title} (from PC)", title=p.title))
        dst = t.ports_dir / p.local_dir.name
        if clean:
            clean_port_dir(dst, log)
        c, s = merge_tree(p.local_dir, dst, overwrite_local, log, cancel)
        for d in ({t.scripts_dir} if t.cfw.builtin_pm else {t.ports_dir, t.scripts_dir}):
            shutil.copy2(p.local_script, d / p.local_script.name)
        log(_tr("log.copied_files", "  ✔ Copied {count} files (skipped {skipped})", count=c, skipped=s))
        return

    log(f"▶ {p.title}")
    z = download(p.url, DL_DIR / p.name, progress, md5=p.md5, cancel=cancel)
    if clean:
        for d in p.dirs:
            clean_port_dir(t.ports_dir / d, log)
    with zipfile.ZipFile(z) as zf:
        zip_names = zf.namelist()
        safe_extract(zf, t.ports_dir, cancel=cancel)
    for sh in p.scripts:
        src = t.ports_dir / sh
        if src.exists() and t.scripts_dir != t.ports_dir:
            shutil.copy2(src, t.scripts_dir / Path(sh).name)
            if t.cfw.builtin_pm:  # muOS keeps launchers only in ROMS/Ports
                src.unlink()
    log(_tr("log.installed_pm", "  ✔ Installed port from PortMaster"))

    for r in p.runtime:
        install_runtime(r, utils, dev, t, log, progress, cancel=cancel)

    if p.local_dir:
        root = p.dirs[0]
        plan = plan_local_files(root, zip_names, p.inst, list_files(p.local_dir))
        where = f"{root}/{plan.want}" if plan.want else root
        how = "" if plan.mode == "mirror" else f" (move {plan.mode})"
        log(_tr("log.game_files_from", "  ⇢ Game files from {src} → {dst}{how}",
              src=p.local_dir, dst=where, how=how))
        if p.local_note:
            log(f"  ℹ {p.local_note}")
        c, s = copy_planned(p.local_dir, t.ports_dir / root, plan, overwrite_local, cancel)
        log(_tr("log.copied_existing", "  ✔ Copied {count} files (skipped existing {skipped})",
              count=c, skipped=s))
        if plan.warn:
            log(f"  ⚠ {plan.warn}")
    elif p.steam_ready:
        c, s = copy_from_steam(p, t, overwrite_local, log, cancel)
        log(_tr("log.copied_steam", "  ✔ Copied {count} files from Steam (skipped existing {skipped})",
              count=c, skipped=s))
    elif p.needs_files:
        log(_tr("log.needs_game_files", "  ⚠ This game requires additional game files: {inst}",
              inst=p.inst or "see port page on portmaster.games"))

    if t.media_dir and p.scripts:
        img = image_path(p.stem, p.cover) or image_path(p.stem, p.screenshot)
        if img:
            t.media_dir.mkdir(parents=True, exist_ok=True)
            copy_box_art(img, t.media_dir / (Path(p.scripts[0]).stem + ".png"))


def copy_box_art(src: Path, dest: Path) -> None:
    """Copy a cover/screenshot as box art. Front-ends (muOS) load box/<name>.png by extension, so a JPEG saved
    under a .png name shows no picture. Convert real JPEGs to PNG; plain copy if it is already PNG or Qt is missing."""
    data = src.read_bytes()[:8]
    if dest.suffix.lower() == ".png" and not data[1:4] == b"PNG":
        try:
            from PySide6.QtGui import QImage  # only the UI ships Qt; core stays importable without it
            img = QImage(str(src))
            if not img.isNull() and img.save(str(dest), "PNG"):
                return
        except ImportError:
            pass
    shutil.copy2(src, dest)


def copy_runtimes_and_media_to_card1(
    card1_root: Path,
    ports: list[Port],
    dev: Device,
    utils: dict,
    log: Log = print,
    progress: Progress | None = None,
    cancel: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    """Copy runtimes and box art for ports to muOS Card 1 (swap flow).
    Returns (runtimes_copied, media_copied).
    """
    if not is_muos_card1(card1_root):
        raise ValueError(_tr("err.not_muos_card1", "{path} is not muOS Card 1 (MUOS folder not found)", path=card1_root))

    libs_dir = card1_root / "MUOS" / "PortMaster" / "libs"
    media_dir = card1_root / "MUOS" / "info" / "catalogue" / "External - Ports" / "box"

    runtimes_copied = 0
    needed_runtimes = set()
    for p in ports:
        for r in p.runtime:
            needed_runtimes.add(r)

    if needed_runtimes:
        libs_dir.mkdir(parents=True, exist_ok=True)
        swap_target = Target(cfw=MUOS, root=card1_root, system_root=card1_root)
        for r in sorted(needed_runtimes):
            if cancel and cancel():
                raise InterruptedError("cancelled")
            if install_runtime(r, utils, dev, swap_target, log, progress, cancel=cancel):
                runtimes_copied += 1

    media_copied = 0
    for p in ports:
        if cancel and cancel():
            raise InterruptedError("cancelled")
        if p.scripts:
            img = image_path(p.stem, p.cover) or image_path(p.stem, p.screenshot)
            if img:
                media_dir.mkdir(parents=True, exist_ok=True)
                dest = media_dir / (Path(p.scripts[0]).stem + ".png")
                copy_box_art(img, dest)
                media_copied += 1

    return runtimes_copied, media_copied


def get_device(dev_id: str) -> Device:
    return next(d for d in DEVICES if d.id == dev_id)
