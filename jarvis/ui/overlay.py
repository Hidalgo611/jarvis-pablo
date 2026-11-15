"""Interfaz superpuesta: pantalla oscurecida, núcleo palpitante, panel en la esquina y anillo del ratón."""
from __future__ import annotations

import ctypes
import math
import random
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QWidget

from ..bus import bus
from ..config import settings

CYAN = QColor(64, 210, 255)
AMBER = QColor(255, 186, 72)
RED = QColor(255, 80, 80)
WHITE = QColor(235, 250, 255)
STATE_COLOR = {"listening": CYAN, "thinking": AMBER, "acting": QColor(90, 180, 255), "speaking": QColor(120, 230, 255),
               "setup": CYAN, "idle": CYAN, "error": RED}
STATE_TEXT = {"listening": "Le escucho", "thinking": "Procesando", "acting": "Trabajando", "speaking": "",
              "setup": "Iniciando sistemas", "idle": "", "error": "Error"}

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x80000, 0x20, 0x80, 0x8000000, 0x8
HWND_TOPMOST = -1
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _mix(c: QColor, a: QColor, t: float) -> QColor:
    return QColor(int(_lerp(c.red(), a.red(), t)), int(_lerp(c.green(), a.green(), t)), int(_lerp(c.blue(), a.blue(), t)))


class Overlay(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        scr = QGuiApplication.primaryScreen()
        self.dpr = scr.devicePixelRatio()
        self.setGeometry(scr.geometry())
        W, H = self.width(), self.height()

        self.mode = "hidden"
        self.state = "idle"
        self.color = QColor(CYAN)
        self.dim = 0.0
        self.dim_target = 0.0
        self.pos_c = QPointF(W / 2, H / 2)
        self.pos_t = QPointF(W / 2, H / 2)
        self.r = 10.0
        self.r_t = 10.0
        self.alpha = 0.0
        self.alpha_t = 0.0
        self.level = 0.0
        self.level_raw = 0.0
        self.rot = [0.0, 0.0, 0.0]
        self.t0 = time.time()
        self.status = ""
        self.subtitle = ""
        self.subtitle_ts = 0.0
        self.transcript = ""
        self.plan: list[str] = []
        self.plan_state: dict[int, str] = {}
        self.progress = -2.0
        self.progress_text = ""
        self.mouse_on = False
        self.mouse_trail: list[tuple[float, float, float]] = []
        self.mouse_target: QPointF | None = None
        self.edge = 0.0
        self.particles = [(random.uniform(0, 6.283), random.uniform(1.05, 1.6), random.uniform(0.2, 0.9),
                           random.uniform(1.0, 2.6)) for _ in range(46)]
        self._full_dirty = True
        self._last_topmost = 0.0

        self.f_title = QFont("Segoe UI", 15, QFont.DemiBold)
        self.f_title.setLetterSpacing(QFont.AbsoluteSpacing, 7)
        self.f_status = QFont("Segoe UI", 12)
        self.f_status.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        self.f_sub = QFont("Segoe UI", 17)
        self.f_small = QFont("Segoe UI", 10)
        self.f_panel_t = QFont("Segoe UI", 10, QFont.DemiBold)
        self.f_panel_t.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        self.f_mouse = QFont("Segoe UI", 8, QFont.DemiBold)
        self.f_mouse.setLetterSpacing(QFont.AbsoluteSpacing, 3)

        bus.state.connect(self.set_state)
        bus.mode.connect(self.set_mode)
        bus.status.connect(self.set_status)
        bus.subtitle.connect(self.set_subtitle)
        bus.transcript.connect(self.set_transcript)
        bus.level.connect(self.set_level)
        bus.plan.connect(self.set_plan)
        bus.step.connect(self.set_step)
        bus.progress.connect(self.set_progress)
        bus.mouse.connect(self.set_mouse)
        bus.mouse_target.connect(self.set_mouse_target)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    # ------------------------------------------------------------------ Win32
    def _apply_exstyle(self) -> None:
        hwnd = int(self.winId())
        u = ctypes.windll.user32
        st = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, st | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
        u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._apply_exstyle()

    # ------------------------------------------------------------------ slots
    def corner_center(self) -> QPointF:
        m = 78
        c = settings["corner"]
        x = self.width() - m if "right" in c else m
        y = m if "top" in c else self.height() - m
        return QPointF(x, y)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        W, H = self.width(), self.height()
        if mode == "focus":
            self.dim_target = float(settings["dim_opacity"])
            self.pos_t = QPointF(W / 2, H / 2 - 40)
            self.r_t = min(W, H) * 0.12
            self.alpha_t = 1.0
            self.plan, self.plan_state = [], {}
        elif mode == "setup":
            self.dim_target = 0.92
            self.pos_t = QPointF(W / 2, H / 2 - 40)
            self.r_t = min(W, H) * 0.12
            self.alpha_t = 1.0
        elif mode == "corner":
            self.dim_target = 0.0
            self.pos_t = self.corner_center()
            self.r_t = 44
            self.alpha_t = 1.0
        else:  # hidden
            self.dim_target = 0.0
            self.alpha_t = 0.0
            self.r_t = self.r * 0.6
        if mode != "hidden" and not self.isVisible():
            if self.alpha < 0.05:
                self.pos_c = QPointF(self.pos_t)
                self.r = self.r_t * 0.3
            self.show()
        self._full_dirty = True

    def set_state(self, s: str) -> None:
        self.state = s
        if s in ("listening", "thinking"):
            self.status = ""

    def set_status(self, s: str) -> None:
        self.status = s

    def set_subtitle(self, s: str) -> None:
        if settings["show_subtitles"]:
            self.subtitle = s
            self.subtitle_ts = time.time()

    def set_transcript(self, s: str) -> None:
        if settings["show_transcript"]:
            self.transcript = s

    def set_level(self, v: float) -> None:
        self.level_raw = max(0.0, min(1.0, v))

    def set_plan(self, steps: list) -> None:
        self.plan = list(steps)
        self.plan_state = {}

    def set_step(self, i: int, st: str) -> None:
        self.plan_state[i] = st

    def set_progress(self, v: float, text: str) -> None:
        self.progress = v
        self.progress_text = text

    def set_mouse(self, on: bool) -> None:
        self.mouse_on = on
        if not on:
            self.mouse_target = None

    def set_mouse_target(self, x: int, y: int) -> None:
        self.mouse_target = QPointF(x / self.dpr, y / self.dpr)

    # ------------------------------------------------------------------ animación
    def tick(self) -> None:
        now = time.time()
        k = 0.12
        self.dim = _lerp(self.dim, self.dim_target, 0.10)
        self.alpha = _lerp(self.alpha, self.alpha_t, 0.12)
        self.pos_c = QPointF(_lerp(self.pos_c.x(), self.pos_t.x(), k), _lerp(self.pos_c.y(), self.pos_t.y(), k))
        self.r = _lerp(self.r, self.r_t, k)
        self.level = _lerp(self.level, self.level_raw, 0.35 if self.level_raw > self.level else 0.08)
        target_c = STATE_COLOR.get(self.state, CYAN)
        self.color = _mix(self.color, target_c, 0.08)
        speed = {"thinking": 2.6, "acting": 1.8, "setup": 1.5}.get(self.state, 1.0)
        self.rot[0] += 0.25 * speed
        self.rot[1] -= 0.55 * speed
        self.rot[2] += 0.9 * speed
        acting = self.mode == "corner" and self.state in ("acting", "thinking")
        edge_t = 1.0 if (acting or self.mouse_on) else 0.0
        edge_changing = abs(self.edge - edge_t) > 0.01
        self.edge = _lerp(self.edge, edge_t, 0.06)
        if self.mouse_on:
            p = QCursor.pos()
            g = self.geometry()
            self.mouse_trail.append((p.x() - g.x(), p.y() - g.y(), now))
        self.mouse_trail = [t for t in self.mouse_trail if now - t[2] < 0.5]
        if self.mode == "hidden" and self.alpha < 0.02 and self.dim < 0.01 and self.isVisible():
            self.hide()
            return
        if not self.isVisible():
            return
        if now - self._last_topmost > 2:
            self._last_topmost = now
            ctypes.windll.user32.SetWindowPos(int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
        dim_changing = abs(self.dim - self.dim_target) > 0.003
        if dim_changing or edge_changing or self._full_dirty:
            self._full_dirty = dim_changing or edge_changing
            self.update()
        else:
            self.update(self._dirty_rect())

    def _dirty_rect(self) -> QRect:
        R = self.r * 2.6 + 40
        rect = QRectF(self.pos_c.x() - R, self.pos_c.y() - R, 2 * R, 2 * R)
        rect = rect.united(self._panel_rect())
        if self.mode in ("focus", "setup"):
            rect = rect.united(QRectF(0, self.pos_c.y() + self.r, self.width(), self.r * 0.6 + 320))
        for x, y, _ in self.mouse_trail:
            rect = rect.united(QRectF(x - 60, y - 60, 120, 120))
        if self.mouse_target:
            rect = rect.united(QRectF(self.mouse_target.x() - 60, self.mouse_target.y() - 60, 120, 120))
        return rect.toAlignedRect().adjusted(-4, -4, 4, 4)

    # ------------------------------------------------------------------ pintura
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(e.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self.dim > 0.004:
            p.fillRect(self.rect(), QColor(0, 3, 8, int(self.dim * 255)))
            g = QRadialGradient(self.pos_c, max(self.width(), self.height()) * 0.55)
            c = QColor(self.color)
            c.setAlpha(int(34 * self.dim))
            g.setColorAt(0, c)
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), QBrush(g))
        if self.edge > 0.01:
            self._paint_edge(p)
        if self.alpha > 0.01:
            self._paint_core(p)
            if self.mode in ("focus", "setup"):
                self._paint_focus_text(p)
            elif self.mode == "corner":
                self._paint_panel(p)
        if self.mouse_on or self.mouse_trail:
            self._paint_mouse(p)
        p.end()

    def _col(self, a: float, c: QColor | None = None) -> QColor:
        c = QColor(c or self.color)
        c.setAlphaF(max(0.0, min(1.0, a * self.alpha)))
        return c

    def _paint_core(self, p: QPainter) -> None:
        c, R = self.pos_c, self.r
        t = time.time() - self.t0
        breath = 0.5 + 0.5 * math.sin(t * (3.2 if self.state == "listening" else 2.0))
        lv = self.level
        # halo
        g = QRadialGradient(c, R * 2.4)
        g.setColorAt(0, self._col(0.30 + 0.25 * lv))
        g.setColorAt(0.45, self._col(0.10 + 0.10 * lv))
        g.setColorAt(1, self._col(0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, R * 2.4, R * 2.4)
        # partículas en órbita
        for i, (ang, rr, sp, sz) in enumerate(self.particles):
            a = ang + t * sp * (1.6 if self.state == "thinking" else 0.6) * (1 if i % 2 else -1)
            rad = R * (rr + 0.08 * math.sin(t * 2 + i))
            p.setBrush(self._col(0.25 + 0.5 * ((math.sin(t * 3 + i) + 1) / 2)))
            p.drawEllipse(QPointF(c.x() + math.cos(a) * rad, c.y() + math.sin(a) * rad), sz * R / 110 + 0.6, sz * R / 110 + 0.6)
        # anillo exterior con marcas
        p.save()
        p.translate(c)
        p.rotate(self.rot[0])
        p.setPen(QPen(self._col(0.55), max(1.0, R / 70)))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), R, R)
        for i in range(72):
            ln = R * (0.075 if i % 6 == 0 else 0.035)
            p.setPen(QPen(self._col(0.75 if i % 6 == 0 else 0.4), max(1.0, R / 90)))
            ang = math.radians(i * 5)
            p.drawLine(QPointF(math.cos(ang) * (R + 4), math.sin(ang) * (R + 4)),
                       QPointF(math.cos(ang) * (R + 4 + ln), math.sin(ang) * (R + 4 + ln)))
        p.restore()
        # arcos medios
        p.save()
        p.translate(c)
        p.rotate(self.rot[1])
        rr = R * 0.82
        p.setPen(QPen(self._col(0.85), max(2.0, R / 22), Qt.SolidLine, Qt.FlatCap))
        for k in range(3):
            p.drawArc(QRectF(-rr, -rr, 2 * rr, 2 * rr), int((k * 120) * 16), int((62 + 18 * lv) * 16))
        p.setPen(QPen(self._col(0.35), max(1.0, R / 60), Qt.SolidLine, Qt.FlatCap))
        for k in range(3):
            p.drawArc(QRectF(-rr, -rr, 2 * rr, 2 * rr), int((k * 120 + 75) * 16), int(30 * 16))
        p.restore()
        # anillo interior discontinuo
        p.save()
        p.translate(c)
        p.rotate(self.rot[2])
        r3 = R * 0.66
        pen = QPen(self._col(0.6), max(1.0, R / 55))
        pen.setDashPattern([2, 4])
        p.setPen(pen)
        p.drawEllipse(QPointF(0, 0), r3, r3)
        p.restore()
        # segmentos del reactor
        p.save()
        p.translate(c)
        n = 10
        for k in range(n):
            a0 = k * 360 / n + self.rot[0] * 0.3
            wave = 0.5 + 0.5 * math.sin(t * 4 - k * 0.7)
            path = QPainterPath()
            r_in, r_out = R * 0.44, R * 0.58
            for ang, rad in ((a0 + 4, r_in), (a0 + 32, r_in), (a0 + 30, r_out), (a0 + 6, r_out)):
                pt = QPointF(math.cos(math.radians(ang)) * rad, math.sin(math.radians(ang)) * rad)
                path.lineTo(pt) if path.elementCount() else path.moveTo(pt)
            path.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(self._col(0.25 + 0.55 * wave * (0.5 + lv)))
            p.drawPath(path)
        p.restore()
        # núcleo
        cr = R * (0.33 + 0.04 * breath + 0.16 * lv)
        g2 = QRadialGradient(c, cr * 1.6)
        g2.setColorAt(0, self._col(1.0, WHITE))
        g2.setColorAt(0.35, self._col(0.95))
        g2.setColorAt(0.7, self._col(0.35))
        g2.setColorAt(1, self._col(0))
        p.setBrush(g2)
        p.setPen(Qt.NoPen)
        p.drawEllipse(c, cr * 1.6, cr * 1.6)
        # barra de progreso (instalación/descargas)
        if self.progress > -1.5 and self.mode in ("setup", "corner", "focus") and self.progress_text:
            rp = R * 1.18
            p.setPen(QPen(self._col(0.18), max(2.0, R / 30)))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, rp, rp)
            pen = QPen(self._col(0.95), max(3.0, R / 26), Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            rect = QRectF(c.x() - rp, c.y() - rp, 2 * rp, 2 * rp)
            if self.progress >= 0:
                p.drawArc(rect, 90 * 16, int(-360 * 16 * min(1.0, self.progress)))
            else:
                p.drawArc(rect, int((-t * 220) % 360 * 16), 70 * 16)

    def _paint_focus_text(self, p: QPainter) -> None:
        c, R = self.pos_c, self.r
        W = self.width()
        y = c.y() + R * 1.55 + 18
        p.setFont(self.f_title)
        p.setPen(self._col(0.95))
        p.drawText(QRectF(0, y, W, 34), Qt.AlignHCenter | Qt.AlignVCenter, "J.A.R.V.I.S.")
        y += 38
        st = self.progress_text if self.mode == "setup" else (self.status or STATE_TEXT.get(self.state, ""))
        if self.state == "listening" and not self.status:
            st = f"Le escucho, {settings['address']}"
        if st:
            p.setFont(self.f_status)
            p.setPen(self._col(0.7, WHITE))
            p.drawText(QRectF(0, y, W, 26), Qt.AlignHCenter | Qt.AlignVCenter, st)
            y += 30
        if self.mode == "setup" and self.progress >= 0:
            p.setFont(self.f_status)
            p.setPen(self._col(0.9))
            p.drawText(QRectF(0, y, W, 26), Qt.AlignHCenter, f"{self.progress * 100:.0f} %")
            y += 30
        if self.transcript:
            p.setFont(self.f_small)
            p.setPen(self._col(0.5, WHITE))
            p.drawText(QRectF(W * 0.2, y, W * 0.6, 50), Qt.AlignHCenter | Qt.TextWordWrap, "« " + self.transcript + " »")
            y += 50
        if self.subtitle and time.time() - self.subtitle_ts < 9:
            fade = min(1.0, (9 - (time.time() - self.subtitle_ts)) / 1.5)
            p.setFont(self.f_sub)
            p.setPen(self._col(0.92 * fade, WHITE))
            p.drawText(QRectF(W * 0.18, y + 16, W * 0.64, 120), Qt.AlignHCenter | Qt.TextWordWrap, self.subtitle)

    def _panel_rect(self) -> QRectF:
        if self.mode != "corner":
            return QRectF()
        w = 400
        h = 96 + 24 * len(self.plan) + (64 if self.subtitle else 0)
        c = self.corner_center()
        x = self.width() - w - 22 if "right" in settings["corner"] else 22
        y = c.y() + 66 if "top" in settings["corner"] else c.y() - 66 - h
        return QRectF(x, y, w, h)

    def _paint_panel(self, p: QPainter) -> None:
        r = self._panel_rect()
        if r.isEmpty():
            return
        a = min(1.0, max(0.0, 1 - abs(self.r - self.r_t) / 30))
        if a < 0.05:
            return
        p.setPen(QPen(self._col(0.45 * a), 1))
        p.setBrush(QColor(3, 12, 22, int(232 * a * self.alpha)))
        p.drawRoundedRect(r, 14, 14)
        p.setPen(QPen(self._col(0.9 * a), 2))
        p.drawLine(QPointF(r.x() + 16, r.y()), QPointF(r.x() + 90, r.y()))
        x, y, w = r.x() + 18, r.y() + 14, r.width() - 36
        p.setFont(self.f_panel_t)
        p.setPen(self._col(0.95 * a))
        p.drawText(QRectF(x, y, w, 20), Qt.AlignLeft | Qt.AlignVCenter, "J.A.R.V.I.S.")
        stt = {"thinking": "PROCESANDO", "acting": "EN CURSO", "speaking": "HABLANDO", "listening": "ESCUCHANDO"}.get(self.state, "")
        p.drawText(QRectF(x, y, w, 20), Qt.AlignRight | Qt.AlignVCenter, stt)
        y += 30
        p.setFont(self.f_status)
        p.setPen(self._col(0.95 * a, WHITE))
        fm = QFontMetrics(self.f_status)
        p.drawText(QRectF(x, y, w, 24), Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(self.status or "…", Qt.ElideRight, int(w)))
        y += 32
        p.setFont(self.f_small)
        fm2 = QFontMetrics(self.f_small)
        for i, s in enumerate(self.plan):
            st = self.plan_state.get(i, "")
            mark = {"done": "✓", "active": "▸", "error": "✕"}.get(st, "·")
            col = self._col((0.95 if st == "active" else 0.6) * a, WHITE if st == "active" else None)
            p.setPen(col)
            p.drawText(QRectF(x, y, 18, 20), Qt.AlignLeft | Qt.AlignVCenter, mark)
            p.drawText(QRectF(x + 20, y, w - 20, 20), Qt.AlignLeft | Qt.AlignVCenter, fm2.elidedText(s, Qt.ElideRight, int(w - 20)))
            y += 24
        if self.subtitle:
            y += 6
            p.setPen(self._col(0.75 * a, WHITE))
            p.drawText(QRectF(x, y, w, 56), Qt.AlignLeft | Qt.TextWordWrap, self.subtitle[:170])

    def _paint_edge(self, p: QPainter) -> None:
        e = self.edge
        W, H = self.width(), self.height()
        for i, al in enumerate((0.55, 0.22, 0.09)):
            p.setPen(QPen(self._col(al * e, CYAN), 2 + i * 3))
            p.setBrush(Qt.NoBrush)
            d = 1 + i * 3
            p.drawRect(QRectF(d, d, W - 2 * d, H - 2 * d))
        if self.mouse_on:
            p.setFont(self.f_mouse)
            p.setPen(self._col(0.8 * e, CYAN))
            p.drawText(QRectF(0, H - 34, W, 20), Qt.AlignHCenter, "JARVIS ESTÁ CONTROLANDO EL RATÓN")

    def _paint_mouse(self, p: QPainter) -> None:
        now = time.time()
        for x, y, ts in self.mouse_trail:
            age = (now - ts) / 0.5
            p.setPen(Qt.NoPen)
            p.setBrush(self._col(0.35 * (1 - age), CYAN))
            p.drawEllipse(QPointF(x, y), 6 * (1 - age) + 1, 6 * (1 - age) + 1)
        if self.mouse_target and self.mouse_on:
            tx = self.mouse_target
            pulse = 0.5 + 0.5 * math.sin(now * 8)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(self._col(0.5 + 0.3 * pulse, CYAN), 2))
            p.drawEllipse(tx, 18 + 4 * pulse, 18 + 4 * pulse)
        if self.mouse_on and self.mouse_trail:
            x, y, _ = self.mouse_trail[-1]
            c = QPointF(x, y)
            pulse = 0.5 + 0.5 * math.sin(now * 10)
            g = QRadialGradient(c, 34)
            g.setColorAt(0, self._col(0.35, CYAN))
            g.setColorAt(1, self._col(0, CYAN))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, 34, 34)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(self._col(0.95, CYAN), 2.2))
            p.drawEllipse(c, 15 + 3 * pulse, 15 + 3 * pulse)
            for ang in (0, 90, 180, 270):
                a = math.radians(ang + now * 120)
                p.drawLine(QPointF(x + math.cos(a) * 22, y + math.sin(a) * 22), QPointF(x + math.cos(a) * 30, y + math.sin(a) * 30))
            p.setFont(self.f_mouse)
            p.drawText(QPointF(x + 24, y + 34), "JARVIS")
