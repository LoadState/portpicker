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
