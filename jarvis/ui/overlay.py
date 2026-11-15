"""Interfaz superpuesta de J.A.R.V.I.S.

Modos: hidden | focus (pantalla oscurecida + núcleo grande) | corner (núcleo en la esquina + panel) |
setup (instalación) | intro (presentación cinematográfica del primer arranque).

Rendimiento: solo se redibuja la zona que cambia (núcleo, textos, panel, ratón); el fondo completo
solo mientras cambia el oscurecido. Los elementos fijos (rejilla hexagonal, anillo de texto) se
pre-renderizan en QPixmap.
"""
from __future__ import annotations

import ctypes
import math
import random
import time

import psutil
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import QWidget

from ..bus import bus
from ..config import VERSION, settings

CYAN = QColor(64, 210, 255)
AMBER = QColor(255, 176, 60)
RED = QColor(255, 80, 80)
WHITE = QColor(235, 250, 255)
STATE_COLOR = {"listening": CYAN, "thinking": AMBER, "acting": QColor(90, 180, 255), "speaking": QColor(120, 230, 255),
               "setup": CYAN, "idle": CYAN, "error": RED, "intro": CYAN}
STATE_LABEL = {"listening": "ESCUCHANDO", "thinking": "PROCESANDO", "acting": "EN CURSO", "speaking": "HABLANDO",
               "setup": "INSTALANDO", "error": "ERROR", "idle": "EN ESPERA", "intro": "INICIANDO"}
RING_TEXT = "JUST A RATHER VERY INTELLIGENT SYSTEM  ·  J.A.R.V.I.S.  ·  STARK INDUSTRIES  ·  "

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
        self.bars = [0.0] * 72
        self.rot = [0.0, 0.0, 0.0, 0.0]
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
        self.shock = -1.0            # onda expansiva al activarse (0..1)
        self.intro_t0 = 0.0
        self.intro_lines: list[tuple[float, str]] = []
        self.intro_title = 0.0
        self.particles = [(random.uniform(0, 6.283), random.uniform(1.05, 1.75), random.uniform(0.2, 0.9),
                           random.uniform(1.0, 2.6)) for _ in range(54)]
        self.telemetry = ("", "", "")
        self._tele_ts = 0.0
        self._full_dirty = True
        self._last_topmost = 0.0
        self._ring_pm: QPixmap | None = None
        self._ring_r = 0
        self._hex_pm: QPixmap | None = None

        self.f_title = QFont("Segoe UI", 15, QFont.DemiBold)
        self.f_title.setLetterSpacing(QFont.AbsoluteSpacing, 7)
        self.f_big = QFont("Segoe UI Light", 44)
        self.f_big.setLetterSpacing(QFont.AbsoluteSpacing, 22)
        self.f_status = QFont("Segoe UI", 12)
        self.f_status.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        self.f_sub = QFont("Segoe UI", 17)
        self.f_small = QFont("Segoe UI", 10)
        self.f_mono = QFont("Consolas", 10)
        self.f_mono.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
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
        bus.intro.connect(self.set_intro)
        bus.capture.connect(lambda on: self.setWindowOpacity(0.0 if on else 1.0))

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
        m = 82
        c = settings["corner"]
        x = self.width() - m if "right" in c else m
        y = m if "top" in c else self.height() - m
        return QPointF(x, y)

    def set_mode(self, mode: str) -> None:
        prev = self.mode
        self.mode = mode
        W, H = self.width(), self.height()
        big = min(W, H) * 0.12
        if mode == "focus":
            self.dim_target = float(settings["dim_opacity"])
            self.pos_t = QPointF(W / 2, H / 2 - 40)
            self.r_t = big
            self.alpha_t = 1.0
            self.plan, self.plan_state = [], {}
            if prev in ("hidden", "corner"):
                self.shock = 0.0
        elif mode in ("setup", "intro"):
            self.dim_target = 1.0 if mode == "intro" else 0.94
            self.pos_t = QPointF(W / 2, H / 2 - 40)
            self.r_t = big * (1.15 if mode == "intro" else 1.0)
            self.alpha_t = 1.0
        elif mode == "corner":
            self.dim_target = 0.0
            self.pos_t = self.corner_center()
            self.r_t = 46
            self.alpha_t = 1.0
        else:  # hidden
            self.dim_target = 0.0
            self.alpha_t = 0.0
            self.r_t = self.r * 0.6
        if mode != "hidden" and not self.isVisible():
            if self.alpha < 0.05:
                self.pos_c = QPointF(self.pos_t)
                self.r = self.r_t * (0.05 if mode == "intro" else 0.3)
            self.show()
        self._full_dirty = True

    def set_intro(self, phase: str) -> None:
        if phase == "start":
            self.intro_t0 = time.time()
            self.intro_lines = []
            self.intro_title = 0.0
            self.set_mode("intro")
            self.state = "intro"
        elif phase.startswith("line:"):
            self.intro_lines.append((time.time(), phase[5:]))
            self.intro_lines = self.intro_lines[-9:]
        elif phase == "title":
            self.intro_title = time.time()
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
        k = 0.11
        self.dim = _lerp(self.dim, self.dim_target, 0.09)
        self.alpha = _lerp(self.alpha, self.alpha_t, 0.12)
        self.pos_c = QPointF(_lerp(self.pos_c.x(), self.pos_t.x(), k), _lerp(self.pos_c.y(), self.pos_t.y(), k))
        rk = 0.035 if self.mode == "intro" else k
        self.r = _lerp(self.r, self.r_t, rk)
        self.level = _lerp(self.level, self.level_raw, 0.35 if self.level_raw > self.level else 0.08)
        # ecualizador circular: cada barra sigue al nivel con su propio ruido
        t = now - self.t0
        for i in range(len(self.bars)):
            target = self.level * (0.45 + 0.55 * abs(math.sin(i * 1.7 + t * 9.0 + math.sin(i * 0.37 + t * 3))))
            self.bars[i] = _lerp(self.bars[i], target, 0.4 if target > self.bars[i] else 0.12)
        target_c = STATE_COLOR.get(self.state, CYAN)
        self.color = _mix(self.color, target_c, 0.08)
        speed = {"thinking": 2.8, "acting": 1.8, "setup": 1.5, "intro": 1.3}.get(self.state, 1.0)
        self.rot[0] += 0.25 * speed
        self.rot[1] -= 0.55 * speed
        self.rot[2] += 0.9 * speed
        self.rot[3] += 0.12 * speed
        if self.shock >= 0:
            self.shock += 0.022
            if self.shock > 1:
                self.shock = -1.0
            self._full_dirty = True
        acting = self.mode == "corner" and self.state in ("acting", "thinking")
        edge_t = 1.0 if (acting or self.mouse_on) else 0.0
        edge_changing = abs(self.edge - edge_t) > 0.01
        self.edge = _lerp(self.edge, edge_t, 0.06)
        if self.mouse_on:
            p = QCursor.pos()
            g = self.geometry()
            self.mouse_trail.append((p.x() - g.x(), p.y() - g.y(), now))
        self.mouse_trail = [m for m in self.mouse_trail if now - m[2] < 0.5]
        if self.mode in ("focus", "intro") and now - self._tele_ts > 1.0:
            self._tele_ts = now
            try:
                self.telemetry = (time.strftime("%H:%M:%S"), f"CPU {psutil.cpu_percent():4.0f}%",
                                  f"RAM {psutil.virtual_memory().percent:4.0f}%")
            except Exception:
                pass
        if self.mode == "hidden" and self.alpha < 0.02 and self.dim < 0.01 and self.isVisible():
            self.hide()
            return
        if not self.isVisible():
            return
        if now - self._last_topmost > 2:
            self._last_topmost = now
            ctypes.windll.user32.SetWindowPos(int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
        dim_changing = abs(self.dim - self.dim_target) > 0.003
        if dim_changing or edge_changing or self._full_dirty or self.mode == "intro":
            self._full_dirty = dim_changing or edge_changing
            self.update()
        else:
            self.update(self._dirty_rect())

    def _dirty_rect(self) -> QRect:
        R = self.r * 2.7 + 50
        rect = QRectF(self.pos_c.x() - R, self.pos_c.y() - R, 2 * R, 2 * R)
        rect = rect.united(self._panel_rect())
        if self.mode in ("focus", "setup"):
            rect = rect.united(QRectF(0, self.pos_c.y() + self.r, self.width(), self.r * 0.6 + 320))
            rect = rect.united(self._tele_rect())
        for x, y, _ in self.mouse_trail:
            rect = rect.united(QRectF(x - 60, y - 60, 120, 120))
        if self.mouse_target:
            rect = rect.united(QRectF(self.mouse_target.x() - 60, self.mouse_target.y() - 60, 120, 120))
        return rect.toAlignedRect().adjusted(-4, -4, 4, 4)

    # ------------------------------------------------------------------ recursos pre-renderizados
    def _hex(self) -> QPixmap:
        if self._hex_pm is None or self._hex_pm.size() != self.size():
            pm = QPixmap(self.size())
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            s = 34.0
            rho = s / 2
            dx, dy = math.sqrt(3) * rho, 1.5 * rho
            cx, cy = self.width() / 2, self.height() / 2
            maxd = math.hypot(cx, cy)
            y = 0.0
            row = 0
            while y < self.height() + s:
                x = -s + (dx / 2 if row % 2 else 0)
                while x < self.width() + s:
                    d = math.hypot(x - cx, y - cy) / maxd
                    a = int(max(0, 26 * (1 - d * 1.25)))
                    if a > 2:
                        p.setPen(QPen(QColor(64, 210, 255, a), 1))
                        path = QPainterPath()
                        for k in range(7):
                            ang = math.radians(60 * k + 30)
                            pt = QPointF(x + s / 2 * math.cos(ang), y + s / 2 * math.sin(ang))
                            path.lineTo(pt) if k else path.moveTo(pt)
                        p.drawPath(path)
                    x += dx
                y += dy
                row += 1
            p.end()
            self._hex_pm = pm
        return self._hex_pm

    def _ring(self, R: float) -> QPixmap | None:
        r = int(R * 1.34)
        if r < 60:
            return None
        if self._ring_pm is None or abs(self._ring_r - r) > 2:
            if abs(self.r - self.r_t) > 1.5:  # aún animando el tamaño: no regenerar cada fotograma
                return self._ring_pm if self._ring_pm is not None else None
            size = 2 * r + 40
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.TextAntialiasing)
            f = QFont("Segoe UI", max(7, int(r / 26)), QFont.DemiBold)
            p.setFont(f)
            fm = QFontMetrics(f)
            p.setPen(QColor(64, 210, 255, 150))
            text = RING_TEXT
            circ = 2 * math.pi * r
            tw = fm.horizontalAdvance(text)
            reps = max(1, int(circ / tw))
            text = text * reps
            ang = 0.0
            c = size / 2
            for ch in text:
                w = fm.horizontalAdvance(ch)
                a = ang + (w / 2) / r
                p.save()
                p.translate(c + r * math.cos(a), c + r * math.sin(a))
                p.rotate(math.degrees(a) + 90)
                p.drawText(QPointF(-w / 2, 0), ch)
                p.restore()
                ang += (w / r) * (circ / (tw * reps))
            p.end()
            self._ring_pm, self._ring_r = pm, r
        return self._ring_pm

    # ------------------------------------------------------------------ pintura
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(e.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self.dim > 0.004:
            p.fillRect(self.rect(), QColor(0, 3, 8, int(self.dim * 255)))
            if self.mode in ("focus", "intro", "setup"):
                p.setOpacity(min(1.0, self.dim * 1.1))
                p.drawPixmap(0, 0, self._hex())
                p.setOpacity(1.0)
            g = QRadialGradient(self.pos_c, max(self.width(), self.height()) * 0.55)
            c = QColor(self.color)
            c.setAlpha(int(40 * self.dim))
            g.setColorAt(0, c)
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), QBrush(g))
        if self.edge > 0.01:
            self._paint_edge(p)
        if self.shock >= 0:
            self._paint_shock(p)
        if self.alpha > 0.01:
            self._paint_core(p)
            if self.mode in ("focus", "setup"):
                self._paint_focus_text(p)
                self._paint_telemetry(p)
            elif self.mode == "intro":
                self._paint_intro(p)
            elif self.mode == "corner":
                self._paint_panel(p)
        if self.mouse_on or self.mouse_trail:
            self._paint_mouse(p)
        p.end()

    def _col(self, a: float, c: QColor | None = None) -> QColor:
        c = QColor(c or self.color)
        c.setAlphaF(max(0.0, min(1.0, a * self.alpha)))
        return c

    def _paint_shock(self, p: QPainter) -> None:
        s = self.shock
        rad = self.r_t * (0.6 + 5.0 * s)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self._col(0.6 * (1 - s), CYAN), 3 * (1 - s) + 1))
        p.drawEllipse(self.pos_t, rad, rad)
        p.setPen(QPen(self._col(0.3 * (1 - s), CYAN), 1))
        p.drawEllipse(self.pos_t, rad * 0.85, rad * 0.85)

    def _paint_core(self, p: QPainter) -> None:
        c, R = self.pos_c, self.r
        t = time.time() - self.t0
        breath = 0.5 + 0.5 * math.sin(t * (3.2 if self.state == "listening" else 2.0))
        lv = self.level
        big = R > 70
        # halo
        g = QRadialGradient(c, R * 2.5)
        g.setColorAt(0, self._col(0.30 + 0.25 * lv))
        g.setColorAt(0.45, self._col(0.10 + 0.10 * lv))
        g.setColorAt(1, self._col(0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, R * 2.5, R * 2.5)
        # anillo de texto giratorio
        if big:
            pm = self._ring(R)
            if pm is not None:
                p.save()
                p.translate(c)
                p.rotate(-self.rot[3])
                p.setOpacity(0.85 * self.alpha)
                p.drawPixmap(QPointF(-pm.width() / 2, -pm.height() / 2), pm)
                p.setOpacity(1.0)
                p.restore()
        # ecualizador circular (voz)
        n = len(self.bars)
        rb = R * 1.08
        p.save()
        p.translate(c)
        for i, b in enumerate(self.bars):
            a = math.radians(i * 360 / n + self.rot[0] * 0.2)
            ln = 2 + b * R * 0.42
            p.setPen(QPen(self._col(0.35 + 0.6 * b), max(1.5, R / 55), Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(math.cos(a) * rb, math.sin(a) * rb), QPointF(math.cos(a) * (rb + ln), math.sin(a) * (rb + ln)))
        p.restore()
        # partículas en órbita
        for i, (ang, rr, sp, sz) in enumerate(self.particles):
            a = ang + t * sp * (1.6 if self.state == "thinking" else 0.6) * (1 if i % 2 else -1)
            rad = R * (rr + 0.08 * math.sin(t * 2 + i))
            p.setPen(Qt.NoPen)
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
            p.drawLine(QPointF(math.cos(ang) * (R - 2), math.sin(ang) * (R - 2)),
                       QPointF(math.cos(ang) * (R - 2 - ln), math.sin(ang) * (R - 2 - ln)))
        p.restore()
        # barrido de radar al pensar
        if self.state in ("thinking", "acting", "setup", "intro"):
            p.save()
            p.translate(c)
            p.rotate(self.rot[2] * 2.2)
            for k in range(14):
                p.setPen(Qt.NoPen)
                p.setBrush(self._col(0.10 * (1 - k / 14)))
                p.drawPie(QRectF(-R * 0.95, -R * 0.95, R * 1.9, R * 1.9), int(-k * 3 * 16), int(-3 * 16))
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
        # acento naranja (detalle Stark)
        p.setPen(QPen(self._col(0.8, AMBER), max(1.5, R / 45), Qt.SolidLine, Qt.FlatCap))
        p.drawArc(QRectF(-rr * 1.12, -rr * 1.12, 2.24 * rr, 2.24 * rr), int(200 * 16), int(26 * 16))
        p.restore()
        # anillo interior discontinuo
        p.save()
        p.translate(c)
        p.rotate(self.rot[2])
        r3 = R * 0.66
        pen = QPen(self._col(0.6), max(1.0, R / 55))
        pen.setDashPattern([2, 4])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r3, r3)
        p.restore()
        # corchetes HUD
        if big:
            p.save()
            p.translate(c)
            p.setPen(QPen(self._col(0.55), 1.5))
            s = R * 1.62
            L = R * 0.22
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p.drawLine(QPointF(sx * s, sy * s), QPointF(sx * (s - L), sy * s))
                p.drawLine(QPointF(sx * s, sy * s), QPointF(sx * s, sy * (s - L)))
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
        # progreso (instalación/descargas)
        if self.progress > -1.5 and self.mode in ("setup", "corner", "focus") and self.progress_text:
            rp = R * 1.22
            p.setPen(QPen(self._col(0.18), max(2.0, R / 30)))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, rp, rp)
            p.setPen(QPen(self._col(0.95), max(3.0, R / 26), Qt.SolidLine, Qt.RoundCap))
            rect = QRectF(c.x() - rp, c.y() - rp, 2 * rp, 2 * rp)
            if self.progress >= 0:
                p.drawArc(rect, 90 * 16, int(-360 * 16 * min(1.0, self.progress)))
            else:
                p.drawArc(rect, int((-t * 220) % 360 * 16), 70 * 16)

    def _paint_focus_text(self, p: QPainter) -> None:
        c, R = self.pos_c, self.r
        W = self.width()
        y = c.y() + R * 1.75 + 14
        p.setFont(self.f_title)
        p.setPen(self._col(0.95))
        p.drawText(QRectF(0, y, W, 34), Qt.AlignHCenter | Qt.AlignVCenter, "J.A.R.V.I.S.")
        y += 38
        st = self.progress_text if self.mode == "setup" else self.status
        if not st:
            st = {"listening": f"Te escucho, {settings['address']}", "thinking": "Procesando…",
                  "acting": "Trabajando…"}.get(self.state, "")
        if st:
            p.setFont(self.f_status)
            p.setPen(self._col(0.75, WHITE))
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

    def _tele_rect(self) -> QRectF:
        c, R = self.pos_c, self.r
        return QRectF(c.x() - R * 3.4, c.y() - R * 0.7, R * 6.8, R * 1.4)

    def _paint_telemetry(self, p: QPainter) -> None:
        if self.r < 70:
            return
        c, R = self.pos_c, self.r
        p.setFont(self.f_mono)
        hour, cpu, ram = self.telemetry
        left = [("HORA", hour), ("ESTADO", STATE_LABEL.get(self.state, "")), ("NÚCLEO", f"v{VERSION}")]
        right = [("SISTEMA", cpu), ("MEMORIA", ram), ("ENLACE", "LOCAL · SEGURO")]
        for side, items in ((-1, left), (1, right)):
            x0 = c.x() + side * R * 1.95
            for i, (k, v) in enumerate(items):
                y = c.y() - R * 0.45 + i * 26
                align = (Qt.AlignRight if side < 0 else Qt.AlignLeft) | Qt.AlignVCenter
                rect = QRectF(x0 - (220 if side < 0 else 0), y, 220, 22)
                p.setPen(self._col(0.45))
                p.drawText(rect, align, f"{k}  {v}" if side > 0 else f"{v}  {k}")
            # línea guía hacia el núcleo
            p.setPen(QPen(self._col(0.25), 1))
            p.drawLine(QPointF(c.x() + side * R * 1.25, c.y()), QPointF(x0 - side * 6, c.y()))

    # ------------------------------------------------------------------ presentación
    def _paint_intro(self, p: QPainter) -> None:
        W, H = self.width(), self.height()
        now = time.time()
        # registro de arranque (abajo a la izquierda)
        p.setFont(self.f_mono)
        y = H - 40 - 22 * len(self.intro_lines)
        for ts, line in self.intro_lines:
            age = now - ts
            shown = line[: int(age * 70)]
            p.setPen(self._col(0.75 if age < 2 else 0.45))
            p.drawText(QPointF(48, y), "> " + shown + ("_" if len(shown) < len(line) and int(now * 6) % 2 else ""))
            y += 22
        # título
        if self.intro_title:
            a = min(1.0, (now - self.intro_title) / 2.0)
            c, R = self.pos_c, self.r
            ty = c.y() + R * 1.8 + 10
            p.setFont(self.f_big)
            letters = "J.A.R.V.I.S."
            visible = letters[: max(0, int((now - self.intro_title) * 9))]
            glow = QColor(CYAN)
            glow.setAlphaF(0.35 * a)
            p.setPen(glow)
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                p.drawText(QRectF(dx, ty + dy, W, 70), Qt.AlignHCenter | Qt.AlignVCenter, visible)
            p.setPen(self._col(a, WHITE))
            p.drawText(QRectF(0, ty, W, 70), Qt.AlignHCenter | Qt.AlignVCenter, visible)
            if now - self.intro_title > 1.6:
                b = min(1.0, (now - self.intro_title - 1.6) / 1.5)
                p.setFont(self.f_status)
                p.setPen(self._col(0.7 * b))
                p.drawText(QRectF(0, ty + 70, W, 26), Qt.AlignHCenter, "JUST  A  RATHER  VERY  INTELLIGENT  SYSTEM")
        if self.subtitle and now - self.subtitle_ts < 9:
            fade = min(1.0, (9 - (now - self.subtitle_ts)) / 1.5)
            p.setFont(self.f_sub)
            p.setPen(self._col(0.9 * fade, WHITE))
            p.drawText(QRectF(W * 0.18, H - 170, W * 0.64, 110), Qt.AlignHCenter | Qt.TextWordWrap, self.subtitle)

    # ------------------------------------------------------------------ panel de la esquina
    def _panel_rect(self) -> QRectF:
        if self.mode != "corner":
            return QRectF()
        w = 400
        h = 96 + 24 * len(self.plan) + (64 if self.subtitle else 0)
        c = self.corner_center()
        x = self.width() - w - 22 if "right" in settings["corner"] else 22
        y = c.y() + 72 if "top" in settings["corner"] else c.y() - 72 - h
        return QRectF(x, y, w, h)

    def _paint_panel(self, p: QPainter) -> None:
        r = self._panel_rect()
        if r.isEmpty():
            return
        a = min(1.0, max(0.0, 1 - abs(self.r - self.r_t) / 30))
        if a < 0.05:
            return
        # caja con esquinas recortadas (estilo HUD)
        cut = 14
        path = QPainterPath()
        path.moveTo(r.x() + cut, r.y())
        path.lineTo(r.right(), r.y())
        path.lineTo(r.right(), r.bottom() - cut)
        path.lineTo(r.right() - cut, r.bottom())
        path.lineTo(r.x(), r.bottom())
        path.lineTo(r.x(), r.y() + cut)
        path.closeSubpath()
        p.setPen(QPen(self._col(0.5 * a), 1))
        p.setBrush(QColor(3, 12, 22, int(232 * a * self.alpha)))
        p.drawPath(path)
        p.setPen(QPen(self._col(0.95 * a), 2))
        p.drawLine(QPointF(r.x() + cut, r.y()), QPointF(r.x() + 110, r.y()))
        p.setPen(QPen(self._col(0.9 * a, AMBER), 2))
        p.drawLine(QPointF(r.right() - 40, r.bottom()), QPointF(r.right() - cut, r.bottom()))
        x, y, w = r.x() + 18, r.y() + 14, r.width() - 36
        p.setFont(self.f_panel_t)
        p.setPen(self._col(0.95 * a))
        p.drawText(QRectF(x, y, w, 20), Qt.AlignLeft | Qt.AlignVCenter, "J.A.R.V.I.S.")
        blink = 0.6 + 0.4 * math.sin(time.time() * 5)
        p.setPen(self._col(blink * a))
        p.drawText(QRectF(x, y, w, 20), Qt.AlignRight | Qt.AlignVCenter, STATE_LABEL.get(self.state, ""))
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
