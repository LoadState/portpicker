"""Beta sheet results shown on the Select Ports page."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402
from profiles import DEVICES, MUOS, CFWS  # noqa: E402

CSV = (
    'Date,Tester (nickname),Device,CFW,CFW version,PortMaster version,Card setup,PortPicker build,'
    '"Card found automatically?",Port name,Group,Install result,Runs on device?,Severity,What happened,Link,Notes,Artwork OK?\n'
    '2026-11-12,tawa,Anbernic RG35XX SP,muOS (2 cards),2606.0,,2 cards,,Yes,Cave Story-evo,,OK,Yes,,,,,Yes\n'
    '2026-11-12,tawa,Anbernic RG35XX SP,muOS,2606.0,,2 cards,,Yes,Block Attack,,OK,Yes,,,,,No - missing\n'
    '2026-11-12,bob,TrimUI Brick Pro,muOS,2606.0,,1 card,,Yes,Cave Story (Evo),,OK,No,,,,,\n'
)


def _port(title, name):
    return SimpleNamespace(title=title, stem=name[:-4])


def test_parse_and_match_device_cfw_port():
    rows = core.parse_beta_results(CSV)
    assert len(rows) == 3 and rows[0]["artwork"] == "Yes" and rows[0]["tester"] == "tawa"
    sp = next(d for d in DEVICES if d.id == "rg35xxsp")
    brick = next(d for d in DEVICES if d.id == "brickpro")
    cave = _port("Cave Story (Evo)", "cave.story-evo.zip")
    hits = core.beta_results_for(cave, sp, MUOS, rows)
    assert [h["tester"] for h in hits] == ["tawa"]  # Brick Pro row not mixed in
    assert [h["runs"] for h in core.beta_results_for(cave, brick, MUOS, rows)] == ["No"]
    other_cfw = next(c for c in CFWS if c.id != "muos")
    assert core.beta_results_for(cave, sp, other_cfw, rows) == []
    block = _port("Block Attack", "block.attack.zip")
    assert core.beta_results_for(block, sp, MUOS, rows)[0]["artwork"].startswith("No")


def test_unshared_sheet_gives_nothing():
    assert core.parse_beta_results("<!DOCTYPE html><html>sign in</html>") == []
    assert core.parse_beta_results("") == []


def test_generic_h700_sees_results_from_any_h700_model():
    rows = core.parse_beta_results(CSV)
    gen = next(d for d in DEVICES if d.id == "h700-nosticks")
    cave = _port("Cave Story (Evo)", "cave.story-evo.zip")
    assert [h["tester"] for h in core.beta_results_for(cave, gen, MUOS, rows)] == ["tawa"]  # SP yes, Brick Pro no
