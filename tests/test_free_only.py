"""Tests for FREE_ONLY mode and i18n support in PortPicker (card T-010)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tempfile
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="pp-test-")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import core
import i18n


def make_port(stem: str, **kw) -> core.Port:
    base = dict(
        name=f"{stem}.zip",
        title=stem,
        desc="",
        inst="",
        genres=[],
        rtr=False,
        availability="paid",
        reqs=[],
        runtime=[],
        arch=["aarch64"],
        min_glibc="",
        items=[f"{stem}.sh", stem],
        url="",
        md5="",
        size=0,
        screenshot="",
        cover="",
        date_updated="",
    )
    base.update(kw)
    return core.Port(**base)


# -----------------------------------------------------------------------------
# 1. Port.is_free logic tests
# -----------------------------------------------------------------------------
def test_port_is_free():
    # Ready to run with non-demo, non-paid availability is free
    p1 = make_port("free_port", rtr=True, availability="full")
    assert p1.is_free is True

    # RTR with blank availability is free
    p2 = make_port("free_blank", rtr=True, availability="")
    assert p2.is_free is True

    # RTR with demo availability is NOT free
    p3 = make_port("demo_port", rtr=True, availability="demo")
    assert p3.is_free is False
    p3_upper = make_port("demo_port_upper", rtr=True, availability="Demo")
    assert p3_upper.is_free is False

    # RTR with paid availability is NOT free (e.g. dokimon, omnispeak)
    p4 = make_port("paid_port", rtr=True, availability="paid")
    assert p4.is_free is False

    # Non-RTR is NOT free even if availability is full
    p5 = make_port("non_rtr", rtr=False, availability="full")
    assert p5.is_free is False


# -----------------------------------------------------------------------------
# 2. i18n tests
# -----------------------------------------------------------------------------
def test_i18n_strings():
    # Save original language
    orig = i18n.get_language()
    try:
        i18n.set_language("en")
        assert i18n.t("tier.must") == "⭐ Must-Have"
        assert i18n.t("tier.local") == "📁 From PC"
        assert i18n.t("tier.ok") == "✅ Playable"

        # Thai switch
        i18n.set_language("th")
        assert i18n.t("tier.must") == "⭐ ต้องมี"
        assert i18n.t("tier.local") == "📁 จากเครื่อง"

        # Fallback to default
        assert i18n.t("non.existent.key", default="Default Val") == "Default Val"

        # Fallback to key
        assert i18n.t("non.existent.key") == "non.existent.key"

        # Interpolation with placeholder
        i18n.set_language("en")
        assert i18n.t("reason.no_arch", arch="armhf") == "No armhf build available"
        i18n.set_language("th")
        assert i18n.t("reason.no_arch", arch="armhf") == "ไม่มีเวอร์ชัน armhf"
    finally:
        i18n.set_language(orig)


# -----------------------------------------------------------------------------
# 3. FREE_ONLY config and UI hiding tests
# -----------------------------------------------------------------------------
def test_free_only_constants():
    import app

    assert app.FREE_ONLY is True
    # In FREE_ONLY mode, DEFAULT_LOCAL must be None (no default scan folder)
    assert app.DEFAULT_LOCAL is None


def test_free_only_ui_hiding():
    from PySide6.QtWidgets import QApplication
    import app

    _ = QApplication.instance() or QApplication([])

    ports_page = app.PortsPage(app.Images())

    # Local game file picking elements must be hidden
    assert ports_page.local_label.isHidden() is True
    assert ports_page.local.isHidden() is True
    assert ports_page.local_pick_btn.isHidden() is True
    assert ports_page.scan_btn.isHidden() is True

    # Detail box local pick elements must be hidden
    assert ports_page.fbox.isHidden() is True
    assert ports_page.d_pick.isHidden() is True
    assert ports_page.d_clear.isHidden() is True

    # "From PC" tier filter box must be hidden
    assert ports_page.tier_boxes["local"].isHidden() is True

    # Ready-to-run checkbox must be hidden and checked
    assert ports_page.ready_only.isHidden() is True
    assert ports_page.ready_only.isChecked() is True
