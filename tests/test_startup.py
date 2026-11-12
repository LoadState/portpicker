"""Build the main window offscreen, like the exe does on start. Catches crashes in widget setup (e.g. a signal
firing before self.model exists), which the logic-only tests miss. Runs in a child process so Qt's shutdown
can't affect pytest."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
ROOT = Path(__file__).resolve().parents[1]

CHILD = r"""
import os, sys
errors = []
sys.excepthook = lambda *e: errors.append(repr(e[1]))
from PySide6.QtWidgets import QApplication
import app
sys.excepthook = lambda *e: errors.append(repr(e[1]))
q = QApplication([])
w = app.Main()
w.show()
q.processEvents()
w.p_ports.apply_filter()
for d in app.DEVICES:  # paint every device picture
    art = app.DeviceArt(d)
    art.resize(260, 210)
    art.grab()
q.processEvents()
print("ERRORS:" + " | ".join(errors) if errors else "STARTUP_OK", flush=True)
os._exit(0)
"""


def test_main_window_builds():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-c", CHILD], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert "STARTUP_OK" in r.stdout, r.stdout + r.stderr
