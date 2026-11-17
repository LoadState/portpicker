"""Box art must be a real PNG: muOS shows nothing for a JPEG saved as <name>.png (Block Attack, muOS test 2026-11-12)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core  # noqa: E402

QtGui = pytest.importorskip("PySide6.QtGui")


def test_jpeg_is_converted_to_png(tmp_path):
    src = tmp_path / "screenshot.jpg"
    img = QtGui.QImage(32, 24, QtGui.QImage.Format_RGB32)
    img.fill(0x3366CC)
    assert img.save(str(src), "JPG")
    dest = tmp_path / "Block Attack.png"
    core.copy_box_art(src, dest)
    assert dest.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_png_is_copied_as_is(tmp_path):
    src = tmp_path / "cover.png"
    img = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
    img.fill(0)
    img.save(str(src), "PNG")
    dest = tmp_path / "Abuse.png"
    core.copy_box_art(src, dest)
    assert dest.read_bytes() == src.read_bytes()


def _png(path, w, h):
    img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
    img.fill(0x55AA33)
    assert img.save(str(path), "PNG")


def test_big_art_is_scaled_to_fit_h700_screen(tmp_path):
    # Open Surge art came out too big on RG35XX SP (muOS test 2026-11-17)
    src = tmp_path / "cover.png"
    _png(src, 1920, 1080)
    dest = tmp_path / "Open Surge.png"
    core.copy_box_art(src, dest)  # unknown device -> 640x480
    out = QtGui.QImage(str(dest))
    max_w, max_h = core.box_art_limits(None)
    assert out.width() <= max_w and out.height() <= max_h
    assert abs(out.width() / out.height() - 1920 / 1080) < 0.02  # aspect kept


def test_tall_art_is_scaled_by_height(tmp_path):
    src = tmp_path / "cover.png"
    _png(src, 600, 900)
    dest = tmp_path / "Tall.png"
    core.copy_box_art(src, dest)
    out = QtGui.QImage(str(dest))
    assert out.height() == core.box_art_limits(None)[1]


def test_limits_follow_device_resolution():
    class Dev:
        screen = "3.95\" 1024×768"
    assert core.screen_resolution(Dev()) == (1024, 768)
    assert core.screen_resolution(None) == (640, 480)
    assert core.box_art_limits(Dev())[0] > core.box_art_limits(None)[0]


def test_small_art_is_not_upscaled(tmp_path):
    src = tmp_path / "small.jpg"
    img = QtGui.QImage(100, 80, QtGui.QImage.Format_RGB32)
    img.fill(0)
    assert img.save(str(src), "JPG")
    dest = tmp_path / "Small.png"
    core.copy_box_art(src, dest)
    out = QtGui.QImage(str(dest))
    assert (out.width(), out.height()) == (100, 80)
