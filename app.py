"""PortPicker — pick a handheld, pick a CFW, get PortMaster ports recommended & installed."""
from __future__ import annotations

import os
import shutil
import sys
import traceback
import webbrowser
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QPointF, QRectF, QRunnable, QSize,
                            Qt, QThread, QThreadPool, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPolygonF,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTableView,
                               QVBoxLayout, QWidget)

import core
from core import human
import steam
from i18n import t
from profiles import CFWS, DEVICES, CFW, Device, is_tested, MUOS

APP_NAME = "PortPicker"
FREE_ONLY = True
DEFAULT_LOCAL = None if FREE_ONLY else Path(r"D:\port")
# Your Ko-fi page, e.g. "https://ko-fi.com/yourname". Empty = donate buttons hidden.
KOFI_URL = "https://ko-fi.com/loadstate"
PORTMASTER_DONATE_URL = "https://opencollective.com/portmaster"
# Beta test results sheet. Empty = "Report test result" buttons hidden (set to "" for the public release).
BETA_SHEET_URL = "https://docs.google.com/spreadsheets/d/1mCB3_TGnqV9uTfqH1Hb3hCX-ZUmwkuyOgNPx61Jv0CA/edit"

TIER_META = {
    "must":         (t("tier.must", "⭐ Must-Have"),      "#ffcc4d"),
    "local":        (t("tier.local", "📁 From PC"),        "#7dd3fc"),
    "ok":           (t("tier.ok", "✅ Playable"),          "#6ee7a8"),
    "heavy":        (t("tier.heavy", "⚠️ Heavy"),          "#fb923c"),
    "broken":       (t("tier.broken", "⛔ Broken"),         "#f87171"),
    "incompatible": (t("tier.incompatible", "✖ Unsupported"), "#8b8fa3"),
}

STYLE = """
* { font-family: 'Segoe UI', 'Leelawadee UI', 'Noto Sans Thai', sans-serif; font-size: 13px; }
QMainWindow, QWidget#root, QDialog { background: #14161c; color: #e7e9ef; }
QLabel { color: #e7e9ef; }
QLabel.muted { color: #9aa0b2; }
QFrame#card { background: #1d2029; border: 2px solid #2a2e3a; border-radius: 16px; }
QFrame#card:hover { border-color: #4b5166; }
QFrame#card[selected="true"] { border-color: #ffb347; background: #232633; }
QFrame#card[disabled="true"] { background: #191b22; }
QFrame#panel { background: #1b1e26; border-radius: 12px; }
QPushButton { background: #2a2e3a; color: #e7e9ef; border: none; border-radius: 8px; padding: 8px 14px; }
QPushButton:hover { background: #363b4a; }
QPushButton:disabled { color: #5d6275; background: #20232c; }
QPushButton#primary { background: #ffb347; color: #1a1306; font-weight: 600; padding: 10px 22px; }
QPushButton#primary:hover { background: #ffc46b; }
QPushButton#primary:disabled { background: #4a4030; color: #8b7a5c; }
QLineEdit, QComboBox, QPlainTextEdit { background: #1d2029; border: 1px solid #2f3442; border-radius: 8px; padding: 6px 8px; color: #e7e9ef; }
QComboBox QAbstractItemView { background: #1d2029; color: #e7e9ef; selection-background-color: #363b4a; }
QTableView { background: #171920; alternate-background-color: #1a1c24; border: none; gridline-color: transparent; selection-background-color: #2c3142; color: #e7e9ef; }
QHeaderView::section { background: #1b1e26; color: #9aa0b2; border: none; padding: 6px; }
QCheckBox { spacing: 6px; color: #e7e9ef; }
QCheckBox::indicator, QTableView::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #4b5166; background: #1d2029; }
QCheckBox::indicator:checked, QTableView::indicator:checked { background: #ffb347; border-color: #ffb347; image: none; }
QProgressBar { background: #1d2029; border: none; border-radius: 6px; height: 12px; text-align: center; color: #e7e9ef; }
QProgressBar::chunk { background: #ffb347; border-radius: 6px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #333849; border-radius: 5px; min-height: 30px; }
"""



def donate_button(text: str, primary: bool = False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    b.setToolTip(KOFI_URL)
    b.clicked.connect(lambda: webbrowser.open(KOFI_URL))
    return b


def beta_button(primary: bool = False) -> QPushButton:
    b = QPushButton(t("beta.report_btn", "📝 Report test result"))
    if primary:
        b.setObjectName("primary")
    b.setToolTip(t("beta.report_tip", "Opens the beta test sheet: write which device, CFW and games you tried and whether they started"))
    b.clicked.connect(lambda: webbrowser.open(BETA_SHEET_URL))
    return b


def muted(text: str) -> QLabel:
    l = QLabel(text)
    l.setProperty("class", "muted")
    l.setWordWrap(True)
    return l

# ----------------------------------------------------------------------------
# Background work
# ----------------------------------------------------------------------------

class Worker(QThread):
    log = Signal(str)
    progress = Signal(int, int)       # overall step, total
    sub = Signal(int, int)            # bytes done, total
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args, self.cancelled = fn, args, False

    def run(self):
        try:
            self.finished_ok.emit(self.fn(self, *self.args))
        except InterruptedError:
            self.failed.emit(t("app.cancelled", "Cancelled"))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class _ImgSignals(QObject):
    done = Signal(str, str)   # key, path


class _ImgTask(QRunnable):
    def __init__(self, key: str, fn, signals: _ImgSignals):
        super().__init__()
        self.key, self.fn, self.signals = key, fn, signals

    def run(self):
        try:
            p = self.fn()
            if p:
                self.signals.done.emit(self.key, str(p))
        except Exception:  # noqa: BLE001
            pass


class Images(QObject):
    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.signals = _ImgSignals()
        self.signals.done.connect(self._on_done)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(4)
        self._pix: dict[str, QPixmap] = {}
        self._thumb: dict[str, QIcon] = {}
        self._in_flight: set[str] = set()

    def _on_done(self, key: str, path: str):
        self._in_flight.discard(key)
        pm = QPixmap(path)
        if not pm.isNull():
            self._pix[key] = pm
            self._thumb[key] = QIcon(pm.scaled(72, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.ready.emit(key)

    def request(self, p: core.Port) -> str | None:
        key = p.name
        img = p.cover or p.screenshot
        if not img:
            return None
        cached = core.image_path(p.stem, img)
        if cached:
            return str(cached)
        if key not in self._in_flight:
            self._in_flight.add(key)
            self.pool.start(_ImgTask(key, lambda: core.fetch_image(p.stem, img), self.signals))
        return None

    def thumb(self, p: core.Port) -> QIcon | None:
        if p.name in self._thumb:
            return self._thumb[p.name]
        self.request(p)
        return None

# ----------------------------------------------------------------------------
# Custom widgets
# ----------------------------------------------------------------------------

class Card(QFrame):
    clicked = Signal(object)

    def __init__(self, payload, enabled=True):
        super().__init__()
        self.payload = payload
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self.setProperty("disabled", not enabled)
        self.setProperty("selected", False)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(16, 16, 16, 16)
        self.v.setSpacing(6)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.property("disabled"):
            self.clicked.emit(self.payload)

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)


class DeviceArt(QWidget):
    """Draws a product-style picture of the handheld in one of three body shapes:
    tall (Brick, R36S, RG353V), wide (Smart Pro, RG40XX H) or an open clamshell (RG35XX SP).
    Shows the real control layout: D-pad, ABXY, sticks, start/select, shoulders, speaker."""
    def __init__(self, d: Device):
        super().__init__()
        self.d = d
        self.setMinimumHeight(120)

    # --- shape of the whole device (width : height) ---
    def _form(self) -> str:
        if self.d.clam:
            return "clam"
        return "tall" if self.d.tall else "wide"

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        form = self._form()
        ratio = {"tall": 1.42, "wide": 0.44, "clam": 1.62}[form]  # height / width
        # same visual size on every card: each shape covers the same area (side T squared)
        T = min(W - 24, H - 16, 160)
        bw, bh = T / ratio ** 0.5, T * ratio ** 0.5
        k = min(1.0, (W - 24) / bw, (H - 16) / bh)
        bw, bh = bw * k, bh * k
        bx, by = (W - bw) / 2, (H - bh) / 2
        getattr(self, "_draw_" + form)(p, bx, by, bw, bh)
        p.end()

    # --- colours ---
    def _body(self):
        return QColor(self.d.body)

    def _light_body(self) -> bool:
        return self._body().lightness() > 140

    def _btn(self):
        return QColor("#3a3e48") if self._light_body() else QColor("#121419")

    def _shell(self, p, r: QRectF, radius: float):
        c = self._body()
        g = QLinearGradient(r.left(), r.top(), r.right(), r.bottom())
        g.setColorAt(0, c.lighter(116))
        g.setColorAt(0.55, c)
        g.setColorAt(1, c.darker(122))
        p.setBrush(g)
        p.setPen(QPen(QColor(0, 0, 0, 170), 1.2))
        p.drawRoundedRect(r, radius, radius)
        # thin highlight just inside the top edge
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 45), 1))
        p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), radius - 1.5, radius - 1.5)

    def _shoulders(self, p, r: QRectF, inset: float):
        p.setPen(QPen(QColor(0, 0, 0, 150), 1))
        p.setBrush(self._body().darker(135))
        w, h = r.width() * 0.24, r.height() * 0.035 + 3
        p.drawRoundedRect(QRectF(r.left() + inset, r.top() - h * 0.55, w, h), 3, 3)
        p.drawRoundedRect(QRectF(r.right() - inset - w, r.top() - h * 0.55, w, h), 3, 3)

    def _screen(self, p, sx, sy, sw, sh, bezel):
        # black glass panel, then the lit display with a soft glare
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#08090c"))
        p.drawRoundedRect(QRectF(sx - bezel, sy - bezel, sw + bezel * 2, sh + bezel * 2), bezel * 0.9, bezel * 0.9)
        g = QLinearGradient(sx, sy, sx, sy + sh)
        g.setColorAt(0, QColor("#24324a"))
        g.setColorAt(1, QColor("#111a2b"))
        p.setBrush(g)
        p.drawRect(QRectF(sx, sy, sw, sh))
        # a few menu rows, like a CFW home screen
        p.setBrush(QColor(255, 255, 255, 38))
        rh = sh * 0.075
        for i in range(4):
            p.drawRoundedRect(QRectF(sx + sw * 0.08, sy + sh * (0.22 + i * 0.16), sw * (0.5 - i * 0.06), rh), 1.5, 1.5)
        p.setBrush(QColor(self.d.accent))
        p.drawRoundedRect(QRectF(sx + sw * 0.05, sy + sh * 0.20, sw * 0.02 + 2, rh + sh * 0.04), 1, 1)
        # glare
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawPolygon(QPolygonF([QPointF(sx + sw * 0.62, sy), QPointF(sx + sw * 0.86, sy),
                                 QPointF(sx + sw * 0.50, sy + sh), QPointF(sx + sw * 0.26, sy + sh)]))

    def _dpad(self, p, cx, cy, s):
        arm = s / 3
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 28))  # well around the pad
        p.drawEllipse(QPointF(cx, cy), s * 0.62, s * 0.62)
        p.setBrush(self._btn())
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        path = QPainterPath()
        path.addRoundedRect(QRectF(cx - arm / 2, cy - s / 2, arm, s), 2, 2)
        path.addRoundedRect(QRectF(cx - s / 2, cy - arm / 2, s, arm), 2, 2)
        p.drawPath(path.simplified())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 25))
        p.drawEllipse(QPointF(cx, cy), arm * 0.28, arm * 0.28)

    def _abxy(self, p, cx, cy, s):
        r = s * 0.17
        gap = s * 0.30
        for ox, oy in [(0, -gap), (gap, 0), (0, gap), (-gap, 0)]:
            c = QPointF(cx + ox, cy + oy)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 80))
            p.drawEllipse(c + QPointF(0, r * 0.18), r * 1.08, r * 1.08)
            p.setBrush(self._btn())
            p.setPen(QPen(QColor(255, 255, 255, 35), 1))
            p.drawEllipse(c, r, r)

    def _stick(self, p, cx, cy, r):
        p.setPen(QPen(QColor(0, 0, 0, 150), 1))
        p.setBrush(QColor("#0d0f13"))
        p.drawEllipse(QPointF(cx, cy), r, r)
        g = QLinearGradient(cx - r, cy - r, cx + r, cy + r)
        g.setColorAt(0, QColor("#3a3f4b"))
        g.setColorAt(1, QColor("#1a1d24"))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawEllipse(QPointF(cx, cy), r * 0.68, r * 0.68)

    def _pills(self, p, cx, cy, w, n=2, spread=None):
        spread = spread if spread is not None else w * 1.6
        p.setPen(Qt.NoPen)
        p.setBrush(self._btn())
        x0 = cx - spread * (n - 1) / 2
        for i in range(n):
            p.drawRoundedRect(QRectF(x0 + i * spread - w / 2, cy - w * 0.17, w, w * 0.34), w * 0.17, w * 0.17)

    def _speaker(self, p, cx, cy, s):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 110))
        d = s / 4
        for i in range(4):
            for j in range(3):
                p.drawEllipse(QPointF(cx + (i - 1.5) * d, cy + (j - 1) * d), d * 0.22, d * 0.22)

    def _led(self, p, x, y, r):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.d.accent))
        p.drawEllipse(QPointF(x, y), r, r)

    # --- body shapes ---
    def _draw_tall(self, p, bx, by, bw, bh):
        r = QRectF(bx, by, bw, bh)
        self._shoulders(p, r, bw * 0.06)
        self._shell(p, r, bw * 0.09)
        m = bw * 0.10
        sw = bw - m * 2
        sh = min(sw / self.d.aspect, bh * 0.44)
        sw = sh * self.d.aspect
        sx, sy = bx + (bw - sw) / 2, by + bh * 0.075
        self._screen(p, sx, sy, sw, sh, bw * 0.035)
        self._led(p, bx + bw * 0.07, sy + sh + bw * 0.06, bw * 0.012 + 0.8)
        top = sy + sh + bw * 0.05
        rest = by + bh - top
        s = bw * 0.27
        if self.d.sticks >= 2:
            cy = top + rest * 0.27
            self._dpad(p, bx + bw * 0.24, cy, s)
            self._abxy(p, bx + bw * 0.76, cy, s)
            self._stick(p, bx + bw * 0.36, top + rest * 0.68, bw * 0.095)
            self._stick(p, bx + bw * 0.64, top + rest * 0.68, bw * 0.095)
            self._pills(p, bx + bw / 2, top + rest * 0.40, bw * 0.10, spread=bw * 0.14)
            self._pills(p, bx + bw / 2, by + bh - rest * 0.08, bw * 0.08, n=3, spread=bw * 0.13)
        else:
            cy = top + rest * 0.40
            self._dpad(p, bx + bw * 0.26, cy, s * 1.1)
            self._abxy(p, bx + bw * 0.74, cy, s * 1.1)
            self._pills(p, bx + bw / 2, top + rest * 0.78, bw * 0.11, spread=bw * 0.17)
            self._speaker(p, bx + bw * 0.80, top + rest * 0.82, bw * 0.12)

    def _draw_wide(self, p, bx, by, bw, bh):
        r = QRectF(bx, by, bw, bh)
        self._shoulders(p, r, bw * 0.08)
        self._shell(p, r, bh * 0.30)
        sh = bh * 0.78
        sw = min(sh * self.d.aspect, bw * 0.58)
        sh = sw / self.d.aspect
        sx, sy = bx + (bw - sw) / 2, by + (bh - sh) / 2
        self._screen(p, sx, sy, sw, sh, bh * 0.05)
        side = (bw - sw) / 2
        lx, rx = bx + side * 0.50, bx + bw - side * 0.50
        s = min(side * 0.55, bh * 0.30)
        self._dpad(p, lx, by + bh * 0.30, s)
        self._abxy(p, rx, by + bh * 0.30, s)
        if self.d.sticks >= 1:
            self._stick(p, lx + side * 0.06, by + bh * 0.70, s * 0.36)
        if self.d.sticks >= 2:
            self._stick(p, rx - side * 0.06, by + bh * 0.70, s * 0.36)
        self._pills(p, bx + side * 0.80, by + bh * 0.90, s * 0.30, n=1)
        self._pills(p, bx + bw - side * 0.80, by + bh * 0.90, s * 0.30, n=1)
        self._led(p, bx + side * 0.18, by + bh * 0.50, 1.6)

    def _draw_clam(self, p, bx, by, bw, bh):
        lid_h, hinge_h = bh * 0.46, bh * 0.06
        lid = QRectF(bx + bw * 0.01, by, bw * 0.98, lid_h)
        base = QRectF(bx, by + lid_h + hinge_h, bw, bh - lid_h - hinge_h)
        self._shell(p, lid, bw * 0.07)
        # hinge barrel
        g = QLinearGradient(0, by + lid_h, 0, by + lid_h + hinge_h)
        g.setColorAt(0, self._body().darker(150))
        g.setColorAt(0.5, self._body().darker(110))
        g.setColorAt(1, self._body().darker(160))
        p.setBrush(g)
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.drawRoundedRect(QRectF(bx + bw * 0.06, by + lid_h - 1, bw * 0.88, hinge_h + 2), hinge_h / 2, hinge_h / 2)
        self._shell(p, base, bw * 0.07)
        # screen in the lid
        sw = bw * 0.80
        sh = sw / self.d.aspect
        if sh > lid_h * 0.80:
            sh = lid_h * 0.80
            sw = sh * self.d.aspect
        self._screen(p, bx + (bw - sw) / 2, by + (lid_h - sh) / 2, sw, sh, bw * 0.03)
        # controls in the base
        t, h = base.top(), base.height()
        s = bw * 0.26
        self._dpad(p, bx + bw * 0.25, t + h * 0.36, s)
        self._abxy(p, bx + bw * 0.75, t + h * 0.36, s)
        self._pills(p, bx + bw / 2, t + h * 0.74, bw * 0.10, spread=bw * 0.15)
        self._speaker(p, bx + bw * 0.80, t + h * 0.78, bw * 0.11)
        self._led(p, bx + bw * 0.10, t + h * 0.12, bw * 0.012 + 0.8)


class Badge(QLabel):
    def __init__(self, text: str, color: str):
        super().__init__(text)
        self.setStyleSheet(f"""
            background: {color}22;
            color: {color};
            border: 1px solid {color}55;
            border-radius: 6px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 600;
        """)

# ----------------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------------

class DevicePage(QWidget):
    chosen = Signal(object)

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(32, 24, 32, 24)
        t_label = QLabel(t("device.title", "Select Your Handheld"))
        t_label.setStyleSheet("font-size:24px;font-weight:700;")
        v.addWidget(t_label)
        v.addWidget(muted(t("device.subtitle", "PortPicker filters ports based on device specs, analog sticks, and CFW")))
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setSpacing(16)
        grid.setContentsMargins(0, 8, 0, 8)
        self.cards = []
        cols = 4
        for i, d in enumerate(DEVICES):
            c = Card(d, d.enabled)
            c.setFixedSize(250, 300)
            c.v.addWidget(DeviceArt(d), 1)
            name = QLabel(d.name)
            name.setStyleSheet("font-size:16px;font-weight:700;")
            c.v.addWidget(name)
            c.v.addWidget(muted(f"{d.chip} · RAM {d.ram}\n{d.screen} · {d.sticks} {t('device.sticks', 'sticks')}"))
            if not d.enabled:
                c.v.addWidget(Badge(t("device.coming_soon", "Coming Soon"), "#8b8fa3"))
            c.clicked.connect(self._pick)
            grid.addWidget(c, i // cols, i % cols)
            self.cards.append(c)
        grid.setColumnStretch(cols, 1)
        grid.setRowStretch(len(DEVICES) // cols + 1, 1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(holder)
        holder.setObjectName("devgrid")
        holder.setStyleSheet("QWidget#devgrid { background: transparent; }")
        sc.viewport().setStyleSheet("background: transparent;")
        v.addWidget(sc, 1)

    def _pick(self, d):
        for c in self.cards:
            c.set_selected(c.payload is d)
        self.chosen.emit(d)


class CfwPage(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.dev: Device | None = None
        self.cfw: CFW | None = None
        self.custom = False
        self._latest_pak_info: tuple[str, str] | None = None
        self._pak_worker: Worker | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(32, 24, 32, 24)
        t_label = QLabel(t("cfw.title", "Select CFW"))
        t_label.setStyleSheet("font-size:24px;font-weight:700;")
        v.addWidget(t_label)
        v.addWidget(muted(t("cfw.subtitle", "Select CFW so PortPicker knows the ports folder layout, or choose Custom to specify your own folder")))
        self.grid = QHBoxLayout()
        self.grid.setSpacing(14)
        v.addLayout(self.grid)
        self.cards: list[Card] = []

        self.panel = QFrame(objectName="panel")
        pv = QVBoxLayout(self.panel)
        pv.setContentsMargins(18, 16, 18, 16)
        self.panel_title = QLabel("")
        self.panel_title.setStyleSheet("font-weight:700;font-size:15px;")
        pv.addWidget(self.panel_title)
        row = QHBoxLayout()
        self.sd = QComboBox()
        self.sd.setEditable(True)
        self.sd.setMinimumWidth(320)
        self.sd.currentTextChanged.connect(self._refresh_status)
        row.addWidget(self.sd, 1)
        self.rescan = QPushButton(t("cfw.rescan", "🔄 Rescan Cards"))
        self.rescan.clicked.connect(self._detect)
        row.addWidget(self.rescan)
        self.browse = QPushButton(t("cfw.browse", "📂 Browse Folder…"))
        self.browse.clicked.connect(self._browse)
        row.addWidget(self.browse)
        pv.addLayout(row)

        self._system_root_manual: Path | None = None
        self.card1_widget = QWidget()
        c1_layout = QHBoxLayout(self.card1_widget)
        c1_layout.setContentsMargins(0, 4, 0, 0)
        c1_label = QLabel(t("cfw.card1_label", "Card 1 (System):"))
        c1_label.setStyleSheet("color:#a0a6b8;font-size:13px;")
        c1_layout.addWidget(c1_label)
        self.card1_combo = QComboBox()
        self.card1_combo.setEditable(True)
        self.card1_combo.setMinimumWidth(260)
        self.card1_combo.currentTextChanged.connect(self._on_card1_changed)
        c1_layout.addWidget(self.card1_combo, 1)
        self.card1_browse = QPushButton(t("cfw.card1_browse", "📂 Browse Card 1…"))
        self.card1_browse.clicked.connect(self._browse_card1)
        c1_layout.addWidget(self.card1_browse)
        pv.addWidget(self.card1_widget)
        self.card1_widget.hide()

        self.pak_widget = QWidget()
        pak_row = QHBoxLayout(self.pak_widget)
        pak_row.setContentsMargins(0, 4, 0, 0)
        self.pak_label = QLabel("")
        self.pak_label.setStyleSheet("color:#e2e8f0;font-size:13px;")
        pak_row.addWidget(self.pak_label)
        self.pak_update_btn = QPushButton(t("cfw.pak_update_btn", "Update PortMaster pak"))
        self.pak_update_btn.setStyleSheet("""
            QPushButton {
                background: #ffb347; color: #14161c; font-weight: bold;
                border-radius: 6px; padding: 4px 10px;
            }
            QPushButton:hover { background: #ffc266; }
        """)
        self.pak_update_btn.clicked.connect(self._on_update_pak)
        pak_row.addWidget(self.pak_update_btn)
        pak_row.addStretch()
        pv.addWidget(self.pak_widget)
        self.pak_widget.hide()

        self.status = muted("")
        pv.addWidget(self.status)
        v.addSpacing(10)
        v.addWidget(self.panel)
        v.addStretch()
        self.panel.hide()

    def _fetch_pak_info_async(self):
        if self._latest_pak_info is not None:
            return
        if self._pak_worker is not None and self._pak_worker.isRunning():
            return
        w = Worker(lambda _w: core.get_latest_pak_info())
        def _on_ok(res):
            self._latest_pak_info = res
            self._refresh_status()
        def _on_err(_err):
            self._latest_pak_info = ("unknown", core.NEXTUI_PAK_URL)
            self._refresh_status()
        w.finished_ok.connect(_on_ok)
        w.failed.connect(_on_err)
        self._pak_worker = w
        w.start()

    def set_device(self, dev: Device):
        self.dev = dev
        self._latest_pak_info = None
        self._fetch_pak_info_async()
        # clear everything, including the trailing stretch from the previous call
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.setParent(None)
        self.cards = []
        for cid in dev.cfws:
            cfw = next(c for c in CFWS if c.id == cid)
            tested = is_tested(dev, cfw)
            self._add_card(cfw, cfw.name, cfw.blurb, cfw.accent, cfw.enabled, tested=tested)
        self._add_card("custom", "Custom", t("cfw.custom_desc", "Specify ports folder manually\n(Other CFW / Testing)"), "#94a3b8", True, tested=True)
        self.grid.addStretch()

    def _add_card(self, payload, title, blurb, color, enabled, tested=True):
        c = Card(payload, enabled)
        c.setFixedSize(220, 185)
        logo = QLabel(title[:2].upper())
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(44, 44)
        logo.setStyleSheet(f"background:{color};color:#14161c;border-radius:22px;font-weight:800;font-size:16px;")
        c.v.addWidget(logo)
        n = QLabel(title)
        n.setStyleSheet("font-weight:700;font-size:15px;" + ("" if enabled else "color:#5d6275;"))
        c.v.addWidget(n)
        c.v.addWidget(muted(blurb))
        if payload != "custom" and enabled and not tested:
            c.v.addWidget(Badge("Beta — not tested on real hardware", "#f59e0b"))
        c.clicked.connect(self._pick)
        self.grid.addWidget(c)
        self.cards.append(c)

    def _pick(self, payload):
        for c in self.cards:
            c.set_selected(c.payload is payload)
        self.custom = payload == "custom"
        self.cfw = None if self.custom else payload
        self.panel.show()
        if self.custom:
            self.panel_title.setText(t("cfw.panel_ports", "Your ports folder"))
            self.sd.clear()
            self.rescan.hide()
        else:
            self.panel_title.setText(t("cfw.panel_sd", "SD card with {cfw}", cfw=self.cfw.name))
            self.rescan.show()
            self._detect()
        self._refresh_status()

    def _detect(self):
        if not self.cfw:
            return
        cards = core.detect_sd_cards(self.cfw)
        cur = self.sd.currentText()
        self.sd.clear()
        for r in cards:
            self.sd.addItem(str(r))
        if cur and cur not in [str(r) for r in cards]:
            self.sd.setEditText(cur)

        if self.cfw.id == "muos":
            cur_c1 = self.card1_combo.currentText()
            self.card1_combo.clear()
            detected_c1 = [c for c in cards if core.is_muos_card1(c)]
            for c in detected_c1:
                self.card1_combo.addItem(str(c))
            if cur_c1 and cur_c1 not in [str(c) for c in detected_c1]:
                self.card1_combo.setEditText(cur_c1)
        self._refresh_status()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, t("cfw.select_folder", "Select Folder"), self.sd.currentText() or "")
        if d:
            self.sd.setEditText(os.path.normpath(d))

    def _browse_card1(self):
        d = QFileDialog.getExistingDirectory(self, t("cfw.select_card1", "Select muOS Card 1"), self.card1_combo.currentText() or "")
        if d:
            self.card1_combo.setEditText(os.path.normpath(d))

    def _on_card1_changed(self, txt: str):
        txt = txt.strip()
        self._system_root_manual = Path(txt) if txt else None
        self._refresh_status()

    def target(self) -> core.Target | None:
        txt = self.sd.currentText().strip()
        if not txt:
            return None
        root = Path(txt)
        if self.custom:
            return core.Target(cfw=CFW(id="custom", name="Custom", blurb=""), root=root, custom=True)
        if not self.cfw:
            return None
        return core.build_target(self.cfw, root, system_root=self._system_root_manual)

    def _on_update_pak(self):
        t_target = self.target()
        if not t_target or not t_target.root.exists():
            return
        latest_tag = self._latest_pak_info[0] if self._latest_pak_info else "unknown"
        dlg = UpdatePakDialog(self, t_target, latest_tag=latest_tag)
        if dlg.exec() == QDialog.Accepted:
            self._latest_pak_info = None
            self._refresh_status()

    def _refresh_status(self, *_):
        t_target = self.target()
        if not t_target:
            self.card1_widget.hide()
            self.pak_widget.hide()
            msg = t("cfw.no_card", "No card detected — insert SD card and click 'Rescan Cards' or browse folder")
            if self.cfw and self.cfw.id == "rocknix":
                msg += t("cfw.note_rocknix", "\n💡 Note: Single-card ROCKNIX STORAGE partition is ext4 (not visible on Windows). Use games card (SD2) formatted as exFAT.")
            elif self.cfw and self.cfw.id == "knulli":
                msg += t("cfw.note_knulli", "\n💡 Note: Older KNULLI releases used ext4 for SHARE (not visible on Windows). Gladiator and newer use exFAT.")
            self.status.setText(msg)
            self.changed.emit()
            return

        if not t_target.root.exists():
            self.card1_widget.hide()
            self.pak_widget.hide()
            self.status.setText(t("cfw.folder_not_found", "⚠ Folder not found"))
            self.changed.emit()
            return

        # Check PC drive blocking
        if core.is_pc_drive(t_target.root, t_target.cfw if not t_target.custom else None) or (
            t_target.system_root and core.is_pc_drive(t_target.system_root, t_target.cfw)
        ):
            self.card1_widget.hide()
            self.pak_widget.hide()
            self.status.setText(t("cfw.pc_drive_blocked", "⛔ PC system drive or non-removable drive selected. Please select a removable SD card."))
            self.changed.emit()
            return

        if t_target.custom:
            self.card1_widget.hide()
            self.pak_widget.hide()
            libs = t_target.libs_dir
            self.status.setText(t("cfw.will_install_to", "✔ Ports will be installed to {root}\n", root=t_target.root)
                                + (t("cfw.runtime_to", "runtime → {libs}", libs=libs) if libs else
                                   t("cfw.no_pm_libs", "PortMaster/libs not found — runtimes will be downloaded by PortMaster on device")))
            self.changed.emit()
            return

        # Check CFW card match (block blank / wrong CFW cards)
        ok = core.card_matches(t_target.cfw, t_target.root)
        if not ok:
            self.card1_widget.hide()
            self.pak_widget.hide()
            msg = t("cfw.card_no_cfw", "⛔ The card has no {cfw}. Install {cfw} and boot the device once first.", cfw=t_target.cfw.name)
            if t_target.cfw.id == "rocknix":
                msg += t("cfw.note_rocknix", "\n💡 Note: Single-card ROCKNIX STORAGE partition is ext4 (not visible on Windows). Use games card (SD2) formatted as exFAT.")
            elif t_target.cfw.id == "knulli":
                msg += t("cfw.note_knulli", "\n💡 Note: Older KNULLI releases used ext4 for SHARE (not visible on Windows). Gladiator and newer use exFAT.")
            self.status.setText(msg)
            self.changed.emit()
            return

        # Check device match
        card_dev = core.detect_card_device(t_target.cfw, t_target.root, t_target.system_root)
        match_ok, match_reason = core.check_device_card_match(self.dev, card_dev)
        if match_ok is False:
            self.card1_widget.hide()
            self.pak_widget.hide()
            self.status.setText(t("cfw.device_mismatch", "⛔ Device mismatch: {reason}. This card cannot be used for {dev}.", reason=match_reason, dev=self.dev.name if self.dev else ""))
            self.changed.emit()
            return

        # Check CFW version minimum
        installed_ver = core.read_cfw_version(t_target.cfw, t_target.root, t_target.system_root)
        ver_ok, ver_msg = core.check_cfw_version_minimum(t_target.cfw, installed_ver)
        if not ver_ok:
            self.card1_widget.hide()
            self.pak_widget.hide()
            self.status.setText(t("cfw.version_too_low", "⛔ {msg}", msg=ver_msg))
            self.changed.emit()
            return

        pm_ver = core.read_portmaster_version(t_target.cfw, t_target.root, t_target.system_root)

        is_muos_c2 = t_target.cfw.id == "muos" and core.is_muos_card2(t_target.root)
        if is_muos_c2:
            self.card1_widget.show()
            if t_target.system_root and self.card1_combo.currentText() != str(t_target.system_root):
                self.card1_combo.blockSignals(True)
                self.card1_combo.setEditText(str(t_target.system_root))
                self.card1_combo.blockSignals(False)
        else:
            self.card1_widget.hide()

        if t_target.cfw.id == "nextui":
            installed = core.get_installed_pak_version(t_target)
            installed_str = installed if installed else "unknown"
            if self._latest_pak_info is None:
                self._fetch_pak_info_async()
                self.pak_label.setText(t("cfw.pak_checking", "PortMaster pak:  <b>{installed}</b>  (checking latest version...)", installed=installed_str))
                self.pak_update_btn.hide()
                self.pak_widget.show()
            else:
                latest, _ = self._latest_pak_info
                if not latest or latest == "unknown":
                    self.pak_label.setText(f"PortMaster pak:  <b>{installed_str}</b>  (offline)")
                    self.pak_update_btn.hide()
                elif core.is_pak_update_available(installed, latest):
                    self.pak_label.setText(f"PortMaster pak:  <b>{installed_str}</b>  →  <b>{latest}</b>")
                    self.pak_update_btn.show()
                else:
                    self.pak_label.setText(t("cfw.pak_latest", "PortMaster pak:  <b>{installed}</b>  (latest ✔)", installed=installed_str))
                    self.pak_update_btn.hide()
                self.pak_widget.show()
        else:
            self.pak_widget.hide()

        free_root = human(core.get_free_space(t_target.root) or 0)
        cfw_ver_info = f" ({t_target.cfw.name} {installed_ver})" if installed_ver else ""
        pm_ver_info = f" · PM {pm_ver}" if pm_ver else ""

        if is_muos_c2:
            if t_target.system_root:
                free_sys = human(core.get_free_space(t_target.system_root) or 0)
                status_text = t(
                    "cfw.dual_sd_detected",
                    "✔ Found muOS Dual-SD setup — cards matched automatically:\n  • SD2 (Ports/Games): {root} · {free_root} free\n  • SD1 (System/Runtimes/Box art): {sys_root} · {free_sys} free\nPorts will go to {ports}\nRuntimes will go to {libs}\nBox art will go to {media}",
                    root=t_target.root,
                    free_root=free_root,
                    sys_root=t_target.system_root,
                    free_sys=free_sys,
                    ports=t_target.ports_dir,
                    libs=t_target.libs_dir,
                    media=t_target.media_dir,
                )
            else:
                status_text = t(
                    "cfw.dual_sd_card2_only",
                    "✔ Found muOS Card 2 (SD2) at {root} · {free_root} free\n⚠ Card 1 (SD1) not yet detected — ports and scripts will install to SD2\n💡 You can select Card 1 above or swap in Card 1 after install (Swap flow)",
                    root=t_target.root,
                    free_root=free_root,
                )
            if installed_ver or pm_ver:
                status_text = f"✔ {t_target.cfw.name}{cfw_ver_info}{pm_ver_info}\n" + status_text
        else:
            if t_target.cfw.builtin_pm:
                pak = (t("cfw.pak_builtin", "Built into {cfw} ✔", cfw=t_target.cfw.name) if t_target.libs_dir else
                       t("cfw.pak_card2_notice", "Card 2 (no MUOS/) — runtimes downloaded via on-device PortMaster"))
                pak_line = f"PortMaster pak: {pak}\n"
                if pm_ver and core.parse_version(pm_ver) < (2024, 1, 1):
                    pak_line += t("cfw.pm_old_warn", "⚠ Old PortMaster ({ver}) detected. Connect WiFi and open Ports → PortMaster once to update it.", ver=pm_ver) + "\n"
            elif t_target.cfw.id == "nextui":
                pak_line = ""
            else:
                pak = t("cfw.pak_installed_status", "✔ Installed") if t_target.pak_installed() else t("cfw.pak_not_installed_status", "Not installed — PortPicker will install it")
                pak_line = f"PortMaster pak: {pak}\n"

            card_match_str = t("cfw.card_match", "✔ Found {cfw} card ({root}) · {free} free", cfw=t_target.cfw.name, root=t_target.root, free=free_root)
            card_match_str += f"{cfw_ver_info}{pm_ver_info}"
            status_text = (
                f"{card_match_str}\n"
                + pak_line + f"Ports will go to {t_target.ports_dir}"
            )

        self.status.setText(status_text)
        self.changed.emit()

    def ready(self) -> bool:
        t_target = self.target()
        if not t_target or not t_target.root.exists():
            return False
        if core.is_pc_drive(t_target.root, t_target.cfw if not t_target.custom else None):
            return False
        if t_target.system_root and core.is_pc_drive(t_target.system_root, t_target.cfw):
            return False
        if t_target.custom:
            return True
        if not core.card_matches(t_target.cfw, t_target.root):
            return False
        card_dev = core.detect_card_device(t_target.cfw, t_target.root, t_target.system_root)
        match_ok, _ = core.check_device_card_match(self.dev, card_dev)
        if match_ok is False:
            return False
        installed_ver = core.read_cfw_version(t_target.cfw, t_target.root, t_target.system_root)
        ver_ok, _ = core.check_cfw_version_minimum(t_target.cfw, installed_ver)
        if not ver_ok:
            return False
        return True


class PortModel(QAbstractTableModel):
    HEAD = ["", "", t("head.game", "Game"), t("head.tier", "Tier"), t("head.genre", "Genre"), t("head.files", "Files"), t("head.size", "Size")]
    selection_changed = Signal()

    def __init__(self, images: Images):
        super().__init__()
        self.images = images
        self.all: list[core.Port] = []
        self.rows: list[core.Port] = []
        self.row_of: dict[str, int] = {}
        self.selected: set[str] = set()
        images.ready.connect(self._img_ready)

    def set_ports(self, ports):
        self.beginResetModel()
        self.all = ports
        self.rows = list(ports)
        self.row_of = {p.name: i for i, p in enumerate(self.rows)}
        self.endResetModel()

    def filter(self, fn):
        self.beginResetModel()
        self.rows = [p for p in self.all if fn(p)]
        self.row_of = {p.name: i for i, p in enumerate(self.rows)}
        self.endResetModel()

    def rowCount(self, _=QModelIndex()):
        return len(self.rows)

    def columnCount(self, _=QModelIndex()):
        return len(self.HEAD)

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEAD[s]

    def flags(self, idx):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if idx.column() == 0 and self.rows[idx.row()].tier != "incompatible":
            f |= Qt.ItemIsUserCheckable
        return f

    def data(self, idx, role=Qt.DisplayRole):
        p = self.rows[idx.row()]
        c = idx.column()
        if role == Qt.CheckStateRole and c == 0:
            return Qt.Checked if p.name in self.selected else Qt.Unchecked
        if role == Qt.DecorationRole and c == 1:
            return self.images.thumb(p)
        if role == Qt.DisplayRole:
            if c == 2:
                return p.title
            if c == 3:
                return TIER_META[p.tier][0]
            if c == 4:
                return ", ".join(p.genres[:2])
            if c == 5:
                if FREE_ONLY:
                    return t("ports.files_ready", "Ready")
                if p.local_dir:
                    return f"📁 {p.local_dir.name}"
                if p.steam_ready:
                    return "🎮 Steam"
                if p.steam:
                    return "🎮 Details"
                return t("ports.files_ready", "Ready") if p.rtr else t("ports.files_needed", "Needs files")
            if c == 6:
                return human(p.size) if p.size else "-"
        if role == Qt.ForegroundRole:
            if c == 3:
                return QBrush(QColor(TIER_META[p.tier][1]))
            if c == 5:
                return QBrush(QColor("#7dd3fc" if p.has_files else "#6ee7a8" if p.rtr else "#fb923c"))
        if role == Qt.FontRole and c == 2:
            f = QFont()
            f.setBold(True)
            return f
        return None

    def setData(self, idx, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and idx.column() == 0:
            p = self.rows[idx.row()]
            if Qt.CheckState(value) == Qt.Checked:
                self.selected.add(p.name)
            else:
                self.selected.discard(p.name)
            self.dataChanged.emit(idx, idx)
            self.selection_changed.emit()
            return True
        return False

    def _img_ready(self, key):
        r = self.row_of.get(key)
        if r is not None:
            i = self.index(r, 1)
            self.dataChanged.emit(i, i)

    def refresh(self):
        self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(self.HEAD) - 1))


class PortsPage(QWidget):
    install = Signal()
    refresh_catalog = Signal()
    get_target = None  # set by Main: () -> core.Target | None

    def __init__(self, images: Images):
        super().__init__()
        self.images = images
        self.ports: list[core.Port] = []
        self.current: core.Port | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)

        top = QHBoxLayout()
        title = QLabel(t("ports.title", "Recommended Ports"))
        title.setStyleSheet("font-size:22px;font-weight:700;")
        top.addWidget(title)

        self.btn_refresh = QPushButton(t("ports.btn_refresh", "🔄 Refresh list"))
        self.btn_refresh.clicked.connect(self.refresh_catalog.emit)
        top.addWidget(self.btn_refresh)
        self.lbl_refresh_time = muted("")
        top.addWidget(self.lbl_refresh_time)
        top.addStretch()

        self.local_label = QLabel(t("ports.local_label", "Local game files:"))
        top.addWidget(self.local_label)
        self.local = QLineEdit("" if FREE_ONLY else (str(DEFAULT_LOCAL) if DEFAULT_LOCAL and DEFAULT_LOCAL.exists() else ""))
        self.local.setPlaceholderText(t("ports.local_placeholder", r"e.g. D:\port"))
        self.local.setMinimumWidth(240)
        top.addWidget(self.local)
        self.local_pick_btn = QPushButton("📂")
        self.local_pick_btn.clicked.connect(self._pick_local)
        top.addWidget(self.local_pick_btn)
        self.scan_btn = QPushButton(t("ports.scan_btn", "Scan"))
        top.addWidget(self.scan_btn)

        if FREE_ONLY:
            self.local_label.hide()
            self.local.hide()
            self.local_pick_btn.hide()
            self.scan_btn.hide()

        root.addLayout(top)
        self.summary = muted(t("ports.loading_catalog", "Loading PortMaster catalog…"))
        root.addWidget(self.summary)

        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("ports.search_placeholder", "🔍 Search game / genre"))
        self.search.textChanged.connect(self.apply_filter)
        filt.addWidget(self.search, 2)
        self.tier_boxes = {}
        for tier_key, on in [("must", True), ("local", True), ("ok", True), ("heavy", True), ("broken", False), ("incompatible", False)]:
            cb = QCheckBox(TIER_META[tier_key][0])
            cb.setChecked(on)
            cb.setStyleSheet(f"color:{TIER_META[tier_key][1]};")
            cb.toggled.connect(self.apply_filter)
            filt.addWidget(cb)
            self.tier_boxes[tier_key] = cb
            if FREE_ONLY and tier_key == "local":
                cb.hide()

        self.ready_only = QCheckBox(t("ports.ready_only", "Ready to play (no files needed)"))
        self.ready_only.toggled.connect(self.apply_filter)
        filt.addWidget(self.ready_only)
        if FREE_ONLY:
            self.ready_only.setChecked(True)
            self.ready_only.hide()

        root.addLayout(filt)

        split = QSplitter(Qt.Horizontal)
        self.model = PortModel(images)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(56)
        self.table.setIconSize(QSize(72, 48))
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Fixed)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        for col, wdt in {0: 34, 1: 84, 3: 110, 4: 130, 5: 150, 6: 70}.items():
            self.table.setColumnWidth(col, wdt)
        self.table.clicked.connect(self._row_clicked)
        self.table.selectionModel().currentRowChanged.connect(lambda c, _: self._show(c))
        self.model.selection_changed.connect(self._update_footer)
        split.addWidget(self.table)

        # detail
        det = QFrame(objectName="panel")
        det.setMinimumWidth(340)
        dv = QVBoxLayout(det)
        dv.setContentsMargins(16, 16, 16, 16)
        self.d_img = QLabel()
        self.d_img.setFixedHeight(190)
        self.d_img.setAlignment(Qt.AlignCenter)
        self.d_img.setStyleSheet("background:#111318;border-radius:10px;")
        dv.addWidget(self.d_img)
        self.d_title = QLabel(t("ports.detail_empty_title", "Select a game to view details"))
        self.d_title.setStyleSheet("font-size:17px;font-weight:700;")
        self.d_title.setWordWrap(True)
        dv.addWidget(self.d_title)
        self.d_badges = QHBoxLayout()
        dv.addLayout(self.d_badges)
        self.d_reasons = muted("")
        dv.addWidget(self.d_reasons)
        self.d_beta = QLabel("")
        self.d_beta.setWordWrap(True)
        self.d_beta.setStyleSheet("background:#161820;border-radius:8px;padding:8px;color:#c9cdd8;")
        self.d_beta.setVisible(False)
        dv.addWidget(self.d_beta)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        inner = QWidget()
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        self.d_desc = QLabel("")
        self.d_desc.setWordWrap(True)
        iv.addWidget(self.d_desc)
        self.d_inst = muted("")
        iv.addWidget(self.d_inst)
        iv.addStretch()
        sc.setWidget(inner)
        sc.viewport().setStyleSheet("background:transparent;")
        inner.setStyleSheet("background:transparent;")
        dv.addWidget(sc, 1)

        self.fbox = QFrame()
        self.fbox.setObjectName("fbox")
        self.fbox.setStyleSheet("QFrame#fbox { background:#161820; border-radius:10px; }")
        fv = QVBoxLayout(self.fbox)
        fv.addWidget(QLabel(t("ports.fbox_title", "Files to install")))
        self.d_local = muted("-")
        fv.addWidget(self.d_local)
        fr = QHBoxLayout()
        self.d_pick = QPushButton(t("ports.btn_pick_files", "📂 Browse game files…"))
        self.d_pick.clicked.connect(self._pick_game_files)
        fr.addWidget(self.d_pick)
        self.d_clear = QPushButton(t("ports.btn_clear_files", "Clear"))
        self.d_clear.clicked.connect(self._clear_game_files)
        fr.addWidget(self.d_clear)
        fv.addLayout(fr)
        dv.addWidget(self.fbox)

        if FREE_ONLY:
            self.fbox.hide()
            self.d_pick.hide()
            self.d_clear.hide()

        self.d_store = QPushButton(t("ports.btn_store", "🛒 Store / Game page"))
        self.d_store.clicked.connect(self._open_store)
        dv.addWidget(self.d_store)
        split.addWidget(det)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        foot = QHBoxLayout()
        b1 = QPushButton(t("ports.btn_select_rec", "⭐ Select Recommended"))
        b1.clicked.connect(self.select_recommended)
        foot.addWidget(b1)
        b2 = QPushButton(t("ports.btn_clear_sel", "Clear Selection"))
        b2.clicked.connect(self.clear_selection)
        foot.addWidget(b2)
        b3 = QPushButton(t("ports.btn_select_installed", "🔧 Select Installed Ports"))
        b3.setToolTip(t("ports.tooltip_select_installed", "Select all ports already installed on the card — use with 'Clean & Reinstall' to repair"))
        b3.clicked.connect(self.select_installed)
        foot.addWidget(b3)
        self.clean = QCheckBox(t("ports.chk_clean", "Clean existing port folder and reinstall (repair)"))
        self.clean.setToolTip(t("ports.tooltip_clean", "Delete old files in selected game folders before installing (preserves save files)\nUsed to fix ports where files were placed incorrectly in earlier versions"))
        foot.addWidget(self.clean)
        foot.addStretch()
        self.foot = QLabel("")
        self.foot.setStyleSheet("color:#9aa0b2;")
        foot.addWidget(self.foot)
        self.go = QPushButton(t("ports.btn_install", "Install ▶"), objectName="primary")
        self.go.setEnabled(False)
        self.go.clicked.connect(self.install.emit)
        foot.addWidget(self.go)
        root.addLayout(foot)

    # --- data
    def set_ports(self, ports: list[core.Port], keep_selection: set[str] | list[str] | None = None):
        prev_sel = set(keep_selection) if keep_selection is not None else set(self.model.selected)
        self.ports = sorted(ports, key=core.sort_key)
        self.model.set_ports(self.ports)
        if prev_sel:
            self.model.selected = {name for name in prev_sel if any(p.name == name for p in self.ports)}
        self.apply_filter()
        c = {}
        for p in self.ports:
            c[p.tier] = c.get(p.tier, 0) + 1
        txt = t("ports.summary_all", "Total {total} ports · ⭐ {must} · ✅ {ok} · ⚠️ {heavy} · ⛔ {broken}",
                total=len(self.ports), must=c.get("must", 0), ok=c.get("ok", 0),
                heavy=c.get("heavy", 0), broken=c.get("broken", 0))
        if not FREE_ONLY:
            have = sum(1 for p in self.ports if p.local_dir)
            st = sum(1 for p in self.ports if p.steam_ready and not p.local_dir)
            if have:
                txt += t("ports.summary_local_extra", " · 📁 Local game files {count}", count=have)
            if st:
                txt += t("ports.summary_steam_extra", " · 🎮 From Steam {count}", count=st)
        self.summary.setText(txt)

    def apply_filter(self, *_):
        if not hasattr(self, "model"):  # signals can fire while __init__ is still building widgets
            return
        q = self.search.text().strip().lower()
        tiers = {tier_key for tier_key, cb in self.tier_boxes.items() if cb.isChecked()}
        ready = self.ready_only.isChecked()

        def ok(p: core.Port):
            if p.tier not in tiers:
                return False
            if ready and not (p.rtr or p.has_files):
                return False
            if q and q not in p.title.lower() and not any(q in g for g in p.genres):
                return False
            return True
        self.model.filter(ok)
        self._update_footer()

    def selected_ports(self) -> list[core.Port]:
        return [p for p in self.ports if p.name in self.model.selected]

    def select_recommended(self):
        for p in self.ports:
            if FREE_ONLY:
                if p.tier == "must" and p.is_free:
                    self.model.selected.add(p.name)
            else:
                if p.tier in ("must", "local") and (p.rtr or p.has_files) or (p.tier == "ok" and p.has_files):
                    self.model.selected.add(p.name)
        self.model.refresh()
        self._update_footer()

    def select_installed(self):
        t_target = self.get_target() if self.get_target else None
        if not t_target:
            return
        for p in self.ports:
            if getattr(p, "local_only", False):
                hit = (t_target.ports_dir / p.local_dir.name).is_dir()
            else:
                hit = any((t_target.ports_dir / d).is_dir() for d in p.dirs)
            if hit:
                self.model.selected.add(p.name)
        self.model.refresh()
        self._update_footer()

    def clear_selection(self):
        self.model.selected.clear()
        self.model.refresh()
        self._update_footer()

    def _update_footer(self):
        sel = self.selected_ports()
        dl = sum(p.size for p in sel)
        txt = t("ports.footer_sel", "Selected {count} games · Total {size}",
                count=len(sel), size=human(dl))
        if not FREE_ONLY:
            need = sum(1 for p in sel if p.needs_files and not p.has_files and not p.local_only)
            if need:
                txt += f" · ⚠ {need} games need files"
        self.foot.setText(txt)
        self.go.setEnabled(bool(sel))

    # --- detail
    def _row_clicked(self, idx):
        self._show(idx)

    def _show(self, idx):
        if not idx.isValid() or idx.row() >= len(self.model.rows):
            return
        p = self.model.rows[idx.row()]
        self.current = p
        self.d_title.setText(p.title)
        while self.d_badges.count():
            w = self.d_badges.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        label, color = TIER_META[p.tier]
        self.d_badges.addWidget(Badge(label, color))
        if FREE_ONLY:
            kind = t("ports.badge_free_rtr", "Free · Ready to play")
            self.d_badges.addWidget(Badge(kind, "#6ee7a8"))
        else:
            kind = t("ports.files_ready", "Ready") if p.rtr else t("ports.badge_needs_files", "Requires game files")
            self.d_badges.addWidget(Badge(kind, "#6ee7a8" if p.rtr else "#fb923c"))
        if p.size:
            self.d_badges.addWidget(Badge(human(p.size), "#9aa0b2"))
        self.d_badges.addStretch()
        self.d_reasons.setText("\n".join("• " + r for r in p.reasons))
        self._show_beta(p)
        self.d_desc.setText(p.desc)
        self.d_inst.setText(("Instructions: " + p.inst) if p.inst else "")
        self._show_local(p)
        self.d_store.setVisible(bool(p.stores) or not p.local_only)
        self._load_big(p)

    def _show_beta(self, p):
        """Beta sheet results for this port on the chosen device + CFW."""
        if not BETA_SHEET_URL:
            self.d_beta.setVisible(False)
            return
        rows = getattr(p, "beta", []) or []
        if not rows:
            self.d_beta.setText(t("beta.none", "🧪 Beta test: no result yet for this game on your device + CFW."))
        else:
            def yes(v):
                return v.lower().startswith(("y", "ok", "✓", "✅"))
            lines = [t("beta.title", "🧪 Beta test on your device + CFW:")]
            for r in rows[-3:]:
                runs = r["runs"] or "?"
                mark = "✅" if yes(runs) else ("❌" if runs != "?" else "❔")
                parts = [f"{mark} Runs: {runs}"]
                if r["artwork"]:
                    parts.append(("🖼 Artwork OK" if yes(r["artwork"]) else f"🖼 Artwork: {r['artwork']}"))
                if r["setup"]:
                    parts.append(r["setup"])
                who = " · ".join(x for x in (r["tester"], r["date"]) if x)
                lines.append("  ".join(parts) + (f"  — {who}" if who else ""))
            if len(rows) > 3:
                lines.append(t("beta.more", "+{n} more in the sheet", n=len(rows) - 3))
            self.d_beta.setText("\n".join(lines))
        self.d_beta.setVisible(True)

    def _show_local(self, p):
        if FREE_ONLY:
            self.d_local.setText(t("ports.badge_free_rtr", "Free · Ready to play ✔"))
            self.d_pick.setEnabled(False)
            self.d_clear.setEnabled(False)
            return
        if p.local_dir:
            self.d_local.setText(f"📁 {p.local_dir}\nWill be merged into port folder (keeps PortMaster files)")
        elif p.steam_ready:
            self.d_local.setText(f"🎮 Steam: {p.steam.app.path}\nWill copy needed game files to {p.steam.recipe.dest}")
        elif p.steam:
            self.d_local.setText("🎮 " + p.steam.note)
        elif p.rtr:
            self.d_local.setText("No extra files needed ✔")
        else:
            self.d_local.setText("Not selected — select folder with game files, or add later on card")
        self.d_pick.setEnabled(not p.local_only)
        self.d_clear.setEnabled(bool(p.local_dir) and not p.local_only)

    def _load_big(self, p):
        path = self.images.request(p)
        if path:
            pm = QPixmap(path).scaled(self.d_img.width(), 190, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.d_img.setPixmap(pm)
        else:
            self.d_img.setPixmap(QPixmap())
            self.d_img.setText("🎮" if p.local_only else "Loading image…")

    def on_image(self, key):
        if self.current and self.current.name == key:
            self._load_big(self.current)

    def _pick_game_files(self):
        if not self.current:
            return
        d = QFileDialog.getExistingDirectory(self, t("ports.pick_files_dialog", "Select game files folder for {stem}", stem=self.current.title), self.local.text())
        if d:
            self.current.local_dir = Path(d)
            self.model.selected.add(self.current.name)
            self._show_local(self.current)
            self.model.refresh()
            self._update_footer()

    def _clear_game_files(self):
        if self.current and not self.current.local_only:
            self.current.local_dir = None
            self._show_local(self.current)
            self.model.refresh()

    def _open_store(self):
        p = self.current
        if not p:
            return
        url = (p.stores[0].get("gameurl") if p.stores else "") or f"https://portmaster.games/detail.html?name={p.stem}"
        webbrowser.open(url)

    def _pick_local(self):
        d = QFileDialog.getExistingDirectory(self, "Select game files folder", self.local.text())
        if d:
            self.local.setText(os.path.normpath(d))
            self.scan_btn.click()


class InstallPage(QWidget):
    back = Signal()
    show_failures = Signal()

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(32, 24, 32, 24)
        top = QHBoxLayout()
        self.title = QLabel(t("install.title_running", "Installing..."))
        self.title.setStyleSheet("font-size:22px;font-weight:700;")
        top.addWidget(self.title)
        self.fail_btn = QPushButton("")
        self.fail_btn.setObjectName("failbtn")
        self.fail_btn.setCursor(Qt.PointingHandCursor)
        self.fail_btn.setStyleSheet(
            "QPushButton#failbtn { background: rgba(248,113,113,40); color: #f87171; font-weight: 700;"
            " font-size: 15px; border-radius: 10px; padding: 6px 14px; }"
            "QPushButton#failbtn:hover { background: rgba(248,113,113,70); }")
        self.fail_btn.hide()
        top.addWidget(self.fail_btn)
        top.addStretch()
        v.addLayout(top)
        self.overall = QProgressBar()
        v.addWidget(self.overall)
        self.cur = muted("")
        v.addWidget(self.cur)
        self.sub = QProgressBar()
        self.sub.setMaximumHeight(8)
        self.sub.setTextVisible(False)
        v.addWidget(self.sub)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setStyleSheet("font-family:Consolas,'Segoe UI',monospace;font-size:12px;")
        v.addWidget(self.logbox, 1)
        self.note = muted("")
        v.addWidget(self.note)
        self.thanks = QFrame(objectName="panel")
        tl = QHBoxLayout(self.thanks)
        tl.setContentsMargins(16, 12, 16, 12)
        tl.addWidget(muted("Enjoying PortPicker? Support the developer ☕ · And remember to support the PortMaster team who made these ports free!"), 1)
        if KOFI_URL:
            tl.addWidget(donate_button(t("dialog.kofi_btn", "☕ Support (Ko-fi)"), primary=True))
        pm = QPushButton("❤ PortMaster")
        pm.clicked.connect(lambda: webbrowser.open(PORTMASTER_DONATE_URL))
        tl.addWidget(pm)
        if BETA_SHEET_URL:
            tl.addWidget(beta_button(primary=True))
        self.thanks.hide()
        v.addWidget(self.thanks)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel = QPushButton(t("install.btn_cancel", "Cancel"))
        row.addWidget(self.cancel)
        self.done = QPushButton(t("install.btn_back", "◀ Back to Ports"), objectName="primary")
        self.done.clicked.connect(self.back.emit)
        self.done.hide()
        row.addWidget(self.done)
        v.addLayout(row)
        self.fail_btn.clicked.connect(self.show_failures.emit)

    def log(self, s):
        self.logbox.appendPlainText(s)
        if s.startswith("▶"):
            self.cur.setText(s[2:])

# ----------------------------------------------------------------------------
# Dialogs
# ----------------------------------------------------------------------------

class UpdatePakDialog(QDialog):
    def __init__(self, parent, target: core.Target, latest_tag: str = "unknown"):
        super().__init__(parent)
        self.setWindowTitle(t("dialog.pak_title", "Update PortMaster pak"))
        self.resize(520, 260)
        self.target = target
        self.latest_tag = latest_tag
        self.worker: Worker | None = None

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        head = QLabel(t("dialog.pak_head", "Update PortMaster pak ({cfw})", cfw=target.cfw.name))
        head.setStyleSheet("font-size:16px;font-weight:700;color:#ffb347;")
        lay.addWidget(head)

        installed = core.get_installed_pak_version(target)
        installed_str = installed if installed else "unknown"
        latest = latest_tag if latest_tag != "unknown" else core.get_latest_pak_info()[0]

        info = QLabel(
            f"{t('dialog.pak_versions', 'Current version: <b>{current}</b><br>Latest version: <b>{latest}</b>', current=installed_str, latest=latest)}<br><br>"
            + t("dialog.pak_desc", "PortPicker will download the latest pak, safely preserve existing configs and runtimes, and update to the new version.")
        )
        info.setStyleSheet("color:#a0a6b8;font-size:13px;")
        lay.addWidget(info)

        self.log_label = QLabel("")
        self.log_label.setStyleSheet("color:#ffb347;font-size:12px;")
        lay.addWidget(self.log_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        lay.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = QPushButton(t("dialog.pak_btn_cancel", "Cancel"))
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.btn_cancel)

        self.btn_start = QPushButton(t("dialog.pak_btn_start", "Start Update"))
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start_update)
        btn_row.addWidget(self.btn_start)
        lay.addLayout(btn_row)

    def _start_update(self):
        self.btn_start.setEnabled(False)
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.log_label.setText(t("dialog.pak_prep_download", "Preparing download..."))

        def job(w: Worker):
            core.update_pak(
                self.target,
                log=w.log.emit,
                progress=lambda d, n: w.sub.emit(d, n),
                cancel=lambda: w.cancelled,
            )

        w = Worker(job)
        w.log.connect(self.log_label.setText)
        w.sub.connect(lambda d, n: (
            self.progress_bar.setMaximum(max(n, 1)),
            self.progress_bar.setValue(min(d, max(n, 1)))
        ))
        w.finished_ok.connect(self._on_success)
        w.failed.connect(self._on_failed)
        self.worker = w
        w.start()

    def _on_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancelled = True
            self.btn_cancel.setEnabled(False)
            self.log_label.setText(t("dialog.pak_cancelling", "Cancelling..."))
        else:
            self.reject()

    def _on_success(self, _):
        self.log_label.setText(t("dialog.pak_success", "✔ PortMaster pak updated successfully"))
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.btn_cancel.hide()
        self.btn_start.setText(t("dialog.pak_btn_close", "Close"))
        self.btn_start.setEnabled(True)
        try:
            self.btn_start.clicked.disconnect()
        except Exception:
            pass
        self.btn_start.clicked.connect(self.accept)

    def _on_failed(self, err: str):
        if "cancelled" in err.lower():
            self.log_label.setText(t("dialog.pak_cancelled", "Pak update cancelled"))
        else:
            self.log_label.setText(t("dialog.pak_error", "✖ Error: {err}", err=err.splitlines()[-1]))
        self.btn_cancel.setEnabled(True)
        self.btn_cancel.setText(t("dialog.pak_btn_close", "Close"))
        self.btn_start.hide()


class SwapCard1Dialog(QDialog):
    def __init__(self, parent, ports: list[core.Port], dev: Device, utils: dict):
        super().__init__(parent)
        self.setWindowTitle(t("dialog.swap_title", "Swap in Card 1 (muOS)"))
        self.resize(520, 260)
        self.ports = ports
        self.dev = dev
        self.utils = utils
        self.card1_path: Path | None = None
        self.runtimes_count = 0
        self.media_count = 0
        self._copy_worker: Worker | None = None

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        head = QLabel(t("dialog.swap_head", "Swap in Card 1 (muOS Dual-SD)"))
        head.setStyleSheet("font-size:17px;font-weight:700;color:#f5c542;")
        lay.addWidget(head)

        info = QLabel(t("dialog.swap_desc", "Remove Card 2 and insert Card 1 (the card with the MUOS folder) into your computer.\nPortPicker will copy runtimes and box art to Card 1."))
        info.setStyleSheet("color:#a0a6b8;font-size:13px;")
        lay.addWidget(info)

        row = QHBoxLayout()
        self.sd = QComboBox()
        self.sd.setEditable(True)
        self.sd.setMinimumWidth(280)
        self.sd.currentTextChanged.connect(self._check_card)
        row.addWidget(self.sd, 1)

        btn_rescan = QPushButton(t("dialog.swap_rescan", "🔄 Rescan"))
        btn_rescan.clicked.connect(self._detect)
        row.addWidget(btn_rescan)

        btn_browse = QPushButton(t("dialog.swap_browse", "📂 Browse Folder…"))
        btn_browse.clicked.connect(self._browse)
        row.addWidget(btn_browse)
        lay.addLayout(row)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#a0a6b8;font-size:13px;")
        lay.addWidget(self.status)

        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        lay.addWidget(self.progress_bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_skip = QPushButton(t("dialog.swap_skip", "Skip"))
        btn_skip.clicked.connect(self.reject)
        btn_row.addWidget(btn_skip)

        self.btn_copy = QPushButton(t("dialog.swap_copy", "Copy to Card 1"))
        self.btn_copy.setObjectName("primary")
        self.btn_copy.clicked.connect(self._start_copy)
        self.btn_copy.setEnabled(False)
        btn_row.addWidget(self.btn_copy)
        lay.addLayout(btn_row)

        self._detect()

    def _detect(self):
        self.sd.clear()
        detected = [c for c in core.detect_sd_cards(MUOS) if core.is_muos_card1(c)]
        for c in detected:
            self.sd.addItem(str(c))
        self._check_card()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, t("cfw.select_card1", "Select muOS Card 1"), self.sd.currentText() or "")
        if d:
            self.sd.setEditText(os.path.normpath(d))

    def _check_card(self):
        txt = self.sd.currentText().strip()
        if not txt:
            self.status.setText(t("dialog.swap_prompt", "Insert Card 1 and click Rescan, or browse to Card 1 folder"))
            self.btn_copy.setEnabled(False)
            return
        p = Path(txt)
        if core.is_muos_card1(p):
            self.status.setText(t("dialog.swap_found", "✔ Found muOS Card 1 at {path}", path=p))
            self.btn_copy.setEnabled(True)
        else:
            self.status.setText(t("dialog.swap_invalid", "⚠ Folder {path} is not muOS Card 1 (MUOS/ folder not found)", path=p))
            self.btn_copy.setEnabled(False)

    def _start_copy(self):
        txt = self.sd.currentText().strip()
        card1 = Path(txt)
        if not core.is_muos_card1(card1):
            return
        self.btn_copy.setEnabled(False)
        self.progress_bar.show()
        self.progress_bar.setMaximum(0)
        self.status.setText(t("dialog.swap_copying", "Copying runtimes and box art to Card 1..."))

        def job(w: Worker):
            def _prog(cur, tot, desc):
                if tot > 0:
                    w.progress.emit(cur, tot)
                if desc:
                    w.log.emit(desc)
            return core.copy_runtimes_and_media_to_card1(
                card1, self.ports, self.dev, self.utils,
                log=w.log.emit,
                progress=_prog,
                cancel=lambda: w.cancelled,
            )

        w = Worker(job)
        self._copy_worker = w

        def _on_prog(cur, tot):
            self.progress_bar.setMaximum(tot)
            self.progress_bar.setValue(cur)

        def _on_log(msg):
            self.status.setText(msg)

        def _on_ok(res):
            r_cnt, m_cnt = res
            self.card1_path = card1
            self.runtimes_count = r_cnt
            self.media_count = m_cnt
            self.accept()

        def _on_fail(err):
            if "Cancelled" not in err:
                QMessageBox.warning(self, APP_NAME, t("dialog.swap_error", "Error during copy: {err}", err=err.split("\n")[0]))
            self.btn_copy.setEnabled(True)
            self.progress_bar.hide()
            self._check_card()

        w.progress.connect(_on_prog)
        w.log.connect(_on_log)
        w.finished_ok.connect(_on_ok)
        w.failed.connect(_on_fail)
        w.start()

    def reject(self):
        if self._copy_worker and self._copy_worker.isRunning():
            self._copy_worker.cancelled = True
        super().reject()


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------

class Main(QMainWindow):
    STEPS = [t("step.device", "Device"), t("step.cfw", "CFW"), t("step.ports", "Select Ports"), t("step.install", "Install")]

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.images = Images()
        self.catalog: list[core.Port] | None = None
        self.utils: dict = {}
        self.extras: list[core.Port] = []
        self.dev: Device | None = None
        self.worker: Worker | None = None

        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        # header
        head = QFrame()
        head.setObjectName("bar")
        head.setStyleSheet("QFrame#bar { background:#101217; }")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 12, 24, 12)
        logo = QLabel("🕹️ PortPicker")
        logo.setStyleSheet("font-size:18px;font-weight:800;color:#ffb347;")
        hl.addWidget(logo)
        hl.addSpacing(30)
        self.step_lbls = []
        for i, s in enumerate(self.STEPS):
            l = QLabel(f"{i + 1}  {s}")
            hl.addWidget(l)
            self.step_lbls.append(l)
            if i < len(self.STEPS) - 1:
                a = QLabel("›")
                a.setStyleSheet("color:#4b5166;font-size:16px;")
                hl.addWidget(a)
        hl.addStretch()
        self.net = QLabel("")
        self.net.setStyleSheet("color:#9aa0b2;")
        hl.addWidget(self.net)
        if BETA_SHEET_URL:
            hl.addSpacing(12)
            hl.addWidget(beta_button())
        if KOFI_URL:
            hl.addSpacing(12)
            hl.addWidget(donate_button(t("dialog.kofi_btn", "☕ Support")))
        v.addWidget(head)

        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        self.p_dev = DevicePage()
        self.p_cfw = CfwPage()
        self.p_ports = PortsPage(self.images)
        self.p_inst = InstallPage()
        for w in (self.p_dev, self.p_cfw, self.p_ports, self.p_inst):
            self.stack.addWidget(w)

        nav = QFrame()
        nav.setObjectName("bar")
        nav.setStyleSheet("QFrame#bar { background:#101217; }")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(24, 10, 24, 10)
        self.b_back = QPushButton("◀ Back")
        self.b_back.clicked.connect(self.prev)
        nl.addWidget(self.b_back)
        nl.addStretch()
        self.b_next = QPushButton("Next ▶")
        self.b_next.setObjectName("primary")
        self.b_next.setMinimumWidth(140)
        self.b_next.clicked.connect(self.next)
        nl.addWidget(self.b_next)
        self.nav = nav
        v.addWidget(nav)

        self.p_dev.chosen.connect(self._device_chosen)
        self.p_cfw.changed.connect(self._update_nav)
        self.p_ports.scan_btn.clicked.connect(self._rescan)
        self.p_ports.install.connect(self._install)
        self.p_inst.back.connect(lambda: self.go(2))
        self.p_inst.cancel.clicked.connect(self._cancel)
        self.p_inst.show_failures.connect(self._show_failures)
        self.p_ports.get_target = self.p_cfw.target
        self.last_fail = []
        self._prepare_seq = 0
        self._prepare_worker: Worker | None = None
        self.images.ready.connect(self.p_ports.on_image)
        self.p_ports.refresh_catalog.connect(self._refresh_catalog)

        self.go(0)
        self._load_catalog()

    # --- navigation
    def go(self, i):
        self.stack.setCurrentIndex(i)
        for j, l in enumerate(self.step_lbls):
            l.setStyleSheet("font-weight:700;color:#ffb347;" if j == i else
                            "color:#e7e9ef;" if j < i else "color:#5d6275;")
        self.nav.setVisible(i < 3)  # Select Ports needs Back too; Install page has its own buttons
        self.b_next.setVisible(i < 2)  # Select Ports uses its own Install button
        self._update_nav()

    def _update_nav(self):
        i = self.stack.currentIndex()
        self.b_back.setEnabled(i > 0)
        self.b_next.setEnabled((i == 0 and self.dev is not None) or (i == 1 and self.p_cfw.ready()))

    def next(self):
        i = self.stack.currentIndex()
        if i == 1:
            t_target = self.p_cfw.target()
            if t_target and not t_target.custom:
                card_dev = core.detect_card_device(t_target.cfw, t_target.root, t_target.system_root)
                match_ok, _ = core.check_device_card_match(self.dev, card_dev)
                if match_ok is None and card_dev is not None:
                    res = QMessageBox.question(
                        self,
                        t("cfw.title", "Select CFW"),
                        t("cfw.confirm_device", "Could not verify device from card. Is this card for {dev}?", dev=self.dev.name if self.dev else ""),
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No
                    )
                    if res != QMessageBox.Yes:
                        return
            self.go(2)
            self._prepare_ports()
            return
        self.go(i + 1)

    def prev(self):
        self.go(max(0, self.stack.currentIndex() - 1))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.stack.currentIndex() in (1, 2):
            self.prev()

    def _device_chosen(self, d):
        self.dev = d
        self.b_next.setEnabled(True)
        self.p_cfw.set_device(d)
        self.go(1)

    # --- catalog & scan
    def _load_catalog(self):
        self.net.setText(t("ports.loading_catalog", "Loading PortMaster catalog…"))

        def job(w):
            return core.load_catalog()
        w = Worker(job)
        w.finished_ok.connect(self._catalog_ready)
        w.failed.connect(lambda e: (self.net.setText("⚠ Catalog load failed"),
                                    QMessageBox.warning(self, APP_NAME, "Failed to load ports.json\n" + e.split("\n")[0])))
        self._keep(w)
        w.start()

    def _catalog_ready(self, res):
        ports, self.utils = res
        if FREE_ONLY:
            self.catalog = [p for p in ports if p.is_free]
        else:
            self.catalog = ports
        self.net.setText(f"PortMaster: {len(self.catalog)} ports")
        if self.stack.currentIndex() == 2:
            self._prepare_ports()

    def _prepare_ports(self, keep_selection: list[str] | None = None):
        if self.catalog is None:
            self.p_ports.summary.setText(t("ports.loading_catalog", "Loading PortMaster catalog…"))
            return
        t_target = self.p_cfw.target()
        cfw = t_target.cfw if t_target else None
        dev = self.dev
        self.p_ports.summary.setText(t("ports.summary_scanning", "Scanning and sorting ports…"))

        self._prepare_seq += 1
        seq = self._prepare_seq

        if self._prepare_worker and self._prepare_worker.isRunning():
            self._prepare_worker.cancelled = True

        def job(w):
            status = core.load_status(cfw) if cfw else {"broken": set(), "fixed": set(), "issues": set()}
            if w.cancelled:
                raise InterruptedError("cancelled")
            beta_rows = core.load_beta_results() if BETA_SHEET_URL else []

            def attach_beta(ports):
                for p in ports:
                    p.beta = core.beta_results_for(p, dev, cfw, beta_rows)

            if FREE_ONLY:
                allp = [p for p in self.catalog if p.is_free]
                core.classify(allp, dev, cfw, status)
                attach_beta(allp)
                return allp

            local = self.p_ports.local.text().strip()
            extras = core.scan_local(Path(local), self.catalog) if local else []
            if not local:
                for p in self.catalog:
                    p.local_dir = None
            allp = self.catalog + extras
            if w.cancelled:
                raise InterruptedError("cancelled")
            core.classify(allp, dev, cfw, status)
            try:
                core.attach_steam(allp, steam.installed_apps())
            except Exception:  # noqa: BLE001 - Steam is optional; never block the list
                pass
            attach_beta(allp)
            return allp

        w = Worker(job)
        self._prepare_worker = w

        def on_done(res, run_seq=seq):
            if run_seq != self._prepare_seq:
                return
            self.p_ports.set_ports(res, keep_selection=keep_selection)

        def on_fail(e, run_seq=seq):
            if run_seq != self._prepare_seq:
                return
            if "Cancelled" not in e:
                self.p_ports.summary.setText("⚠ " + e.split("\n")[0])

        w.finished_ok.connect(on_done)
        w.failed.connect(on_fail)
        self._keep(w)
        w.start()

    def _refresh_catalog(self):
        self.p_ports.btn_refresh.setEnabled(False)
        self.p_ports.lbl_refresh_time.setText(t("ports.refreshing", "Refreshing..."))
        kept_sel = list(self.p_ports.model.selected)

        def job(w):
            ports, utils = core.load_catalog(force=True)
            t_target = self.p_cfw.target()
            cfw = t_target.cfw if t_target else None
            if cfw:
                core.load_status(cfw)
            return ports, utils

        def on_ok(res):
            self.p_ports.btn_refresh.setEnabled(True)
            self._catalog_ready(res)
            now_str = datetime.now().strftime("%H:%M")
            self.p_ports.lbl_refresh_time.setText(t("ports.updated_at", "Updated {time}", time=now_str))
            self._prepare_ports(keep_selection=kept_sel)

        def on_fail(err):
            self.p_ports.btn_refresh.setEnabled(True)
            cache_file = core.CACHE_DIR / "ports.json"
            if cache_file.exists():
                mtime = datetime.fromtimestamp(cache_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                self.p_ports.lbl_refresh_time.setText(t("ports.refresh_offline", "offline — showing list from {date}", date=mtime))
            else:
                self.p_ports.lbl_refresh_time.setText(t("ports.refresh_failed", "offline"))
            self._prepare_ports(keep_selection=kept_sel)

        w = Worker(job)
        w.finished_ok.connect(on_ok)
        w.failed.connect(on_fail)
        self._keep(w)
        w.start()

    def _rescan(self):
        if self.stack.currentIndex() == 2:
            self._prepare_ports()

    # --- install
    def _install(self, ports=None):
        t_target = self.p_cfw.target()
        ports = ports or self.p_ports.selected_ports()
        if not t_target or not ports:
            return
        bad = [p.title for p in ports if p.tier in ("broken", "incompatible")]
        if bad and QMessageBox.question(
                self, APP_NAME,
                t("dialog.broken_warning", "These games are reported as broken or unsupported. Do you want to proceed?\n\n{games}", games="\n".join(bad[:15]))
        ) != QMessageBox.Yes:
            return
        warns = core.check_disk_space(t_target, ports, self.utils)
        if warns:
            msg = "Space warning:\n\n" + "\n".join(warns) + "\n\nDo you want to proceed?"
            if QMessageBox.question(self, APP_NAME, msg) != QMessageBox.Yes:
                return

        self.go(3)
        pi = self.p_inst
        pi.logbox.clear()
        pi.title.setText(f"Installing {len(ports)} ports → {t_target.root}")
        pi.overall.setMaximum(len(ports) + 1)
        pi.overall.setValue(0)
        pi.done.hide()
        pi.fail_btn.hide()
        pi.thanks.hide()
        pi.cancel.show()
        pi.cancel.setEnabled(True)
        pi.note.setText("")
        dev, utils = self.dev, self.utils
        clean = self.p_ports.clean.isChecked()
        overwrite = clean

        def job(w: Worker):
            ok, fail = [], []
            core.ensure_pak(t_target, w.log.emit, lambda d, n: w.sub.emit(d, n), cancel=lambda: w.cancelled)
            w.progress.emit(1, len(ports) + 1)
            for i, p in enumerate(ports):
                if w.cancelled:
                    raise InterruptedError
                try:
                    core.install_port(p, t_target, dev, utils, w.log.emit, lambda d, n: w.sub.emit(d, n),
                                      overwrite_local=overwrite, cancel=lambda: w.cancelled, clean=clean)
                    ok.append(p)
                except InterruptedError:
                    raise
                except Exception as e:  # noqa: BLE001
                    w.log.emit(f"  ✖ Error: {e}")
                    fail.append((p, str(e)))
                w.progress.emit(i + 2, len(ports) + 1)
            return ok, fail

        w = Worker(job)
        w.log.connect(pi.log)
        w.progress.connect(lambda a, b: (pi.overall.setMaximum(b), pi.overall.setValue(a)))
        w.sub.connect(lambda d, n: (pi.sub.setMaximum(max(n, 1)), pi.sub.setValue(min(d, max(n, 1)))))
        w.finished_ok.connect(lambda r: self._done(t_target, *r))
        w.failed.connect(self._failed)
        self.worker = w
        self._keep(w)
        w.start()

    def _cancel(self):
        if self.worker:
            self.worker.cancelled = True
            self.p_inst.cancel.setEnabled(False)
            self.p_inst.log("… Cancelling")

    def _finish_ui(self):
        pi = self.p_inst
        pi.cancel.hide()
        pi.done.show()
        pi.sub.setValue(pi.sub.maximum())

    def _done(self, t_target: core.Target, ok, fail):
        self._finish_ui()
        pi = self.p_inst
        self.last_fail = fail
        pi.title.setText(f"Complete ✔ Installed {len(ok)} ports")
        pi.fail_btn.setText(t("install.btn_view_failed", "⚠ View {count} failed ports", count=len(fail)))
        pi.fail_btn.setVisible(bool(fail))
        need = [p.title for p in ok if p.needs_files and not p.has_files]
        notes = []
        if not t_target.custom:
            notes += [f"{i}) {tip}" for i, tip in enumerate(t_target.cfw.after_install, 1)]
        if need:
            notes.append("⚠ Game files required: " + ", ".join(need[:12]) + (" …" if len(need) > 12 else ""))

        if t_target.cfw.id == "muos" and core.is_muos_card2(t_target.root) and t_target.system_root is None and ok:
            ans = QMessageBox.question(
                self, APP_NAME,
                "Ports installed to Card 2 successfully!\n\n"
                "The target card is Card 2 (runtimes and box art are not on Card 1 yet).\n"
                "Do you want to swap in Card 1 now to copy runtimes and box art?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if ans == QMessageBox.Yes:
                dlg = SwapCard1Dialog(self, ok, self.dev, self.utils)
                if dlg.exec() == QDialog.Accepted and dlg.card1_path:
                    msg = f"✔ Copied runtimes ({dlg.runtimes_count}) and box art ({dlg.media_count}) to Card 1 ({dlg.card1_path})"
                    pi.log(msg)
                    notes.insert(0, msg)
                else:
                    notes.insert(0, "💡 Runtimes not copied to Card 1 — launch game via PortMaster on handheld to download runtime")
            else:
                notes.insert(0, "💡 You skipped Card 1 swap — launch game via PortMaster on handheld to download runtime")

        pi.note.setText("\n".join(notes))
        pi.thanks.setVisible(bool(ok))

    def _show_failures(self):
        fail = self.last_fail
        if not fail:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(t("dialog.fail_title", "Failed to install {count} games", count=len(fail)))
        dlg.resize(720, 460)
        lay = QVBoxLayout(dlg)
        head = QLabel(t("dialog.fail_title", "Failed to install {count} games", count=len(fail)))
        head.setStyleSheet("font-size:17px;font-weight:700;color:#f87171;")
        lay.addWidget(head)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        lines = []
        for p, err in fail:
            lines.append(f"• {p.title}")
            lines.append(f"    {t('dialog.fail_reason', 'Reason: {hint}', hint=failure_hint(err))}")
            lines.append(f"    {t('dialog.fail_actual', 'Actual error: {err}', err=err.strip().splitlines()[-1] if err.strip() else '-')}")
            lines.append("")
        text = "\n".join(lines)
        box.setPlainText(text)
        lay.addWidget(box, 1)
        row = QHBoxLayout()
        cp = QPushButton(t("dialog.fail_btn_copy", "📋 Copy Text"))
        cp.clicked.connect(lambda: QApplication.clipboard().setText(text))
        row.addWidget(cp)
        row.addStretch()
        close = QPushButton(t("dialog.fail_btn_close", "Close"))
        close.clicked.connect(dlg.reject)
        row.addWidget(close)
        retry = QPushButton(t("dialog.fail_btn_retry", "↻ Retry installing these {count} games", count=len(fail)))
        retry.setObjectName("primary")
        retry.clicked.connect(dlg.accept)
        row.addWidget(retry)
        lay.addLayout(row)
        if dlg.exec() == QDialog.Accepted:
            retry_ports = [p for p, _ in fail]
            if any("404" in e for _, e in fail):
                # stale catalog: fetch the newest ports.json and use its download URLs
                try:
                    fresh, self.utils = core.load_catalog(force=True)
                    by_name = {p.name: p for p in fresh}
                    for p in retry_ports:
                        q = by_name.get(p.name)
                        if q:
                            p.url, p.md5, p.size, p.runtime = q.url, q.md5, q.size, q.runtime
                except Exception:  # noqa: BLE001
                    pass
            self._install(retry_ports)

    def _failed(self, e):
        self._finish_ui()
        self.p_inst.title.setText(t("install.stopped", "Stopped"))
        self.p_inst.log("✖ " + e)

    def _keep(self, w):
        self._workers = getattr(self, "_workers", [])
        self._workers = [x for x in self._workers if x.isRunning()] + [w]

    def closeEvent(self, e):
        busy = bool(self.worker and self.worker.isRunning())
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        if busy:
            box.setIcon(QMessageBox.Warning)
            box.setText(t("dialog.quit_installing_text", "Installing in progress — if you exit now, currently installing games will be incomplete."))
            box.setInformativeText(t("dialog.quit_installing_info", "You can reopen and reinstall; incomplete files will be copied again."))
        else:
            box.setIcon(QMessageBox.Question)
            box.setText(t("dialog.quit_title", "Exit PortPicker?"))
        quit_btn = box.addButton(t("dialog.quit_btn_exit", "Exit Program"), QMessageBox.DestructiveRole)
        stay = box.addButton(t("dialog.quit_btn_stay", "Stay"), QMessageBox.RejectRole)
        box.setDefaultButton(stay)   # Enter / Esc keep the app open
        box.setEscapeButton(stay)
        box.exec()
        if box.clickedButton() is not quit_btn:
            e.ignore()
            return
        if busy:
            self.worker.cancelled = True
            self.worker.wait(5000)
        e.accept()


def failure_hint(err: str) -> str:
    """Plain-language cause for common install errors."""
    e = err.lower()
    rules = [
        (("md5",), t("hint.md5", "Download was incomplete or corrupted — click retry")),
        (("404", "not found"), t("hint.404", "PortMaster has updated this file — click retry (app will refresh catalog)")),
        (("timed out", "timeout", "urlopen error", "connection", "getaddrinfo", "11001", "10054"),
         t("hint.timeout", "Network disconnected or slow — click retry")),
        (("winerror 112", "no space", "disk full"), t("hint.disk_full", "Card is full — free up space by removing unused games")),
        (("winerror 5", "permission", "access is denied", "read-only", "winerror 19"),
         t("hint.permission", "Card is write-protected or locked — check physical lock switch or close programs using files")),
        (("winerror 32", "being used"), t("hint.in_use", "File is open in another program — close Explorer or that program and retry")),
        (("winerror 3", "winerror 2", "no such file", "cannot find the path"),
         t("hint.card_removed", "File or folder not found — card may have disconnected; re-insert and retry")),
        (("badzipfile", "not a zip", "crc"), t("hint.bad_zip", "Corrupted zip file — clear download cache and retry")),
        (("winerror 206", "filename or extension is too long"), t("hint.path_too_long", "File name or path too long for Windows")),
    ]
    for keys, hint in rules:
        if any(k in e for k in keys):
            return hint
    return t("hint.unknown", "Unknown cause — copy error text below and report")


def app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#ffb347"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(8, 2, 48, 60, 10, 10)
    p.setBrush(QColor("#14161c"))
    p.drawRoundedRect(14, 8, 36, 26, 4, 4)
    p.drawEllipse(18, 42, 10, 10)
    p.drawEllipse(36, 42, 10, 10)
    p.end()
    return QIcon(pm)


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PortPicker.App")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)

    def show_error(etype, value, tb):
        msg = "".join(traceback.format_exception(etype, value, tb))
        if sys.__stderr__:  # None under pythonw
            sys.__stderr__.write(msg)
        try:
            (core.APP_DIR / "error.log").write_text(msg, "utf-8")
        except OSError:
            pass
        QMessageBox.critical(None, APP_NAME, f"An error occurred (take a screenshot to report):\n\n{msg[-1800:]}")
    sys.excepthook = show_error  # Qt slots otherwise swallow exceptions silently

    app.setStyleSheet(STYLE)
    if "--render-devices" in sys.argv:  # dev aid: save every device picture as PNG and exit
        out = Path(sys.argv[sys.argv.index("--render-devices") + 1])
        out.mkdir(parents=True, exist_ok=True)
        for d in DEVICES:
            art = DeviceArt(d)
            art.resize(260, 210)
            art.grab().save(str(out / f"{d.id}.png"))
        return
    app.setWindowIcon(app_icon())
    w = Main()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
