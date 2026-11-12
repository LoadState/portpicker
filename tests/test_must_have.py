"""T-031: the v1.0 must-have picks must be real PortMaster ports that are free (ready-to-run, not demo/paid)."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from profiles import A133P_MUST  # noqa: E402

V1_FREE_PICKS = {
    "cave.story-evo", "cceleste", "devilutionx", "opentyrian", "quake", "spelunky", "supertux", "uqm", "vvvvvv",
    "openjazz", "commander.genius", "abuse", "rick.dangerous", "sonic1sms", "sonic2sms", "sonictimetwisted", "opensurge",
    "alephone-marathon", "descent", "flare", "freedink", "snkrx", "angband", "openttd", "wesnoth", "openfodder",
    "chromium_bsu", "digger", "cromagrally", "apotris", "block.attack",
}


def test_v1_picks_are_in_must_have():
    assert V1_FREE_PICKS <= A133P_MUST
    assert len(V1_FREE_PICKS) == 31


def test_v1_picks_are_free_ports_in_catalog():
    cache = Path(os.environ.get("PORTS_JSON", "ports.json"))
    if not cache.exists():
        pytest.skip("set PORTS_JSON to a downloaded ports.json to run this")
    data = json.loads(cache.read_text("utf-8"))["ports"]
    missing, not_free = [], []
    for stem in sorted(V1_FREE_PICKS):
        v = data.get(stem + ".zip")
        if not v:
            missing.append(stem)
            continue
        a = v.get("attr", {})
        if not a.get("rtr") or str(a.get("availability", "")).lower() in ("demo", "paid"):
            not_free.append(stem)
    assert not missing, f"not in ports.json: {missing}"
    assert not not_free, f"not free (rtr / availability): {not_free}"
