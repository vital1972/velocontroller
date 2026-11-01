# velo_gauge.py - круглый спидометр и праздничный фейерверк для Вело-контроллера

import colorsys
import ctypes
import logging
import math
import random
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

GAUGE_KEY = "#010101"          # цвет, который Windows делает прозрачным
GAUGE_DEFAULT_SIZE = 50
GAUGE_MIN_SIZE = 30
GAUGE_MAX_SIZE = 200
GAUGE_MARGIN = 6               # отступ от краёв рабочей области

CELEBRATION_SECONDS = 7.0
CELEBRATION_TEXT = "Ура! Норматив выполнен! Я супер!"


# ==================== ГЕОМЕТРИЯ ЭКРАНА ====================

def get_work_area():
    """Видимая область основного монитора без панели задач: (left, top, right, bottom)."""
    if sys.platform == 'win32':
        try:
            rect = wintypes.RECT()
            SPI_GETWORKAREA = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception as e:
            logging.debug(f"Не удалось получить рабочую область: {e}")
    return 0, 0, 1920, 1040


def get_virtual_screen():
    """Прямоугольник всех мониторов вместе: (left, top, right, bottom)."""
    if sys.platform == 'win32':
        try:
            u = ctypes.windll.user32
            left, top = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
            width, height = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
            if width > 0 and height > 0:
                return left, top, left + width, top + height
        except Exception as e:
            logging.debug(f"Не удалось получить виртуальный экран: {e}")
    return 0, 0, 1920, 1080


def clamp_size(size):
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = GAUGE_DEFAULT_SIZE
    return max(GAUGE_MIN_SIZE, min(GAUGE_MAX_SIZE, size))


def bottom_left_position(size):
    """Левый нижний угол видимой области (над панелью задач)."""
    left, top, right, bottom = get_work_area()
    return left + GAUGE_MARGIN, bottom - size - GAUGE_MARGIN


def clamp_to_screen(x, y, width, height):
    """Не даёт окну выйти за видимую часть экрана."""
    left, top, right, bottom = get_virtual_screen()
    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    return x, y


# ==================== ЦВЕТА ====================

def _hex(h, s, l):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360.0, l / 100.0, s / 100.0)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def gauge_palette(ok, lock_frac, idle):
    """Цвета кружка: серый - покой, зелёный - норма, жёлтый -> красный - приближение блокировки."""
    if idle:
        return {"face": _hex(0, 0, 86), "strong": _hex(0, 0, 55), "text": _hex(0, 0, 25), "hue": -1}
    if ok:
        hue = 130
    else:
        hue = 50 - 50 * max(0.0, min(1.0, lock_frac))
    return {
        "face": _hex(hue, 75, 72),
        "strong": _hex(hue, 80, 34),
        "text": _hex(hue, 70, 14),
        "hue": hue,
    }


# ==================== СПИДОМЕТР ====================

class SpeedGauge:
    """Рисует спидометр на tk.Canvas.

    Установленная скорость - стрелка на 12 часов, шкала 0..2*норма по дуге 270 градусов.
    Внешнее кольцо - остаток дневной нормы, число в центре - зачтённые минуты.
    """

    def __init__(self, canvas):
        self.canvas = canvas
        self._last_key = None

    def invalidate(self):
        self._last_key = None

    def draw(self, size, speed, min_speed, ok, lock_frac, minutes, remaining_frac, idle=False):
        min_speed = min_speed if min_speed > 0 else 1.0
        frac = max(0.0, min(1.0, speed / (2.0 * min_speed)))
        needle_deg = -135.0 + 270.0 * frac
        pal = gauge_palette(ok, lock_frac, idle)
        remaining_frac = max(0.0, min(1.0, remaining_frac))

        key = (size, round(needle_deg), int(pal["hue"]), bool(idle), int(minutes), int(remaining_frac * 720))
        if key == self._last_key:
            return
        self._last_key = key

        c = self.canvas
        k = size / 100.0
        c.delete("all")

        def pt(r, deg):
            a = math.radians(deg - 90)
            return (50 + r * math.cos(a)) * k, (50 + r * math.sin(a)) * k

        def circle(r, **kw):
            c.create_oval((50 - r) * k, (50 - r) * k, (50 + r) * k, (50 + r) * k, **kw)

        def arc(r, clock_from, clock_to, width, color):
            # tk: углы против часовой от 3 часов; наши градусы - по часовой от 12 часов
            c.create_arc((50 - r) * k, (50 - r) * k, (50 + r) * k, (50 + r) * k,
                         start=90 - clock_from, extent=-(clock_to - clock_from),
                         style=tk.ARC, outline=color, width=max(1, round(width * k)))

        # внешнее кольцо: остаток дневной нормы
        ring_w = max(2, round(5 * k))
        circle(46, outline="#cccccc", width=ring_w)
        if remaining_frac >= 0.995:
            circle(46, outline="#2b78b8", width=ring_w)
        elif remaining_frac > 0:
            arc(46, 0, 360 * remaining_frac, 5, "#2b78b8")

        # циферблат
        circle(40, fill=pal["face"], outline=pal["face"])
        arc(33, -135, 135, 4, _hex(0, 0, 66) if idle else _hex(max(pal["hue"], 0), 40, 62))
        if frac > 0.01:
            arc(33, -135, needle_deg, 4, pal["strong"])

        # метка нормы на 12 часах и стрелка
        x0, y0 = pt(35, 0)
        x1, y1 = pt(41, 0)
        lw = max(1, round(2.4 * k))
        c.create_line(x0, y0, x1, y1, fill=pal["text"], width=lw, capstyle=tk.ROUND)
        tx, ty = pt(32, needle_deg)
        bx, by = pt(10, needle_deg)
        c.create_line(bx, by, tx, ty, fill=pal["text"], width=lw, capstyle=tk.ROUND)

        # зачтённые минуты - поверх стрелки, 14 px при размере 50 px
        px = max(9, round(14 * size / 50.0))
        font = ("Arial", -px, "bold")
        text = str(int(minutes))
        cx, cy = 50 * k, 50 * k
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            c.create_text(cx + dx, cy + dy, text=text, font=font, fill=pal["face"])
        c.create_text(cx, cy, text=text, font=font, fill=pal["text"])

        if size >= 90:
            c.create_text(cx, 80 * k, text="мин", font=("Arial", -round(7 * k)), fill=pal["text"])


# ==================== ФЕЙЕРВЕРК ====================

def play_fanfare():
    """Приветственная мелодия в отдельном потоке."""
    def _play():
        try:
            import winsound
            notes = ((523, 130), (659, 130), (784, 130), (1047, 320), (0, 80),
                     (784, 130), (1047, 130), (1319, 520))
            for freq, ms in notes:
                if freq:
                    winsound.Beep(freq, ms)
                else:
                    time.sleep(ms / 1000.0)
        except Exception as e:
            logging.debug(f"Не удалось проиграть мелодию: {e}")

    threading.Thread(target=_play, daemon=True).start()


class Celebration:
    """Полноэкранный прозрачный фейерверк с надписью. По окончании вызывает on_done."""

    COLORS = ("#ff4d4d", "#ffd633", "#4dff88", "#4dc3ff", "#ff7de9", "#ffffff", "#ff9933")

    def __init__(self, root, on_done=None, duration=CELEBRATION_SECONDS, sound=True):
        self.root = root
        self.on_done = on_done
        self.duration = duration
        self.sound = sound
        self.rockets = []
        self.sparks = []
        self.win = None
        self.canvas = None
        self.started = None
        self._finished = False

    def start(self):
        left, top, right, bottom = get_virtual_screen()
        self.w, self.h = right - left, bottom - top
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.configure(bg=GAUGE_KEY)
        self.win.attributes('-topmost', True)
        try:
            self.win.attributes('-transparentcolor', GAUGE_KEY)
        except tk.TclError:
            pass
        self.win.geometry(f"{self.w}x{self.h}+{left}+{top}")
        self.canvas = tk.Canvas(self.win, bg=GAUGE_KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        font_px = max(28, min(int(self.w / 22), 90))
        cx, cy = self.w // 2, int(self.h * 0.42)
        font = ("Arial", -font_px, "bold")
        for dx in (-3, 0, 3):
            for dy in (-3, 0, 3):
                if dx or dy:
                    self.canvas.create_text(cx + dx, cy + dy, text=CELEBRATION_TEXT, font=font,
                                            fill="#202020", tags="txt")
        self.canvas.create_text(cx, cy, text=CELEBRATION_TEXT, font=font, fill="#ffd633", tags="txt")

        if self.sound:
            play_fanfare()
        self.started = time.time()
        self._last = self.started
        self._tick()

    def _launch(self):
        x = random.uniform(0.15, 0.85) * self.w
        self.rockets.append({
            "x": x, "y": float(self.h), "vy": -random.uniform(self.h * 0.55, self.h * 0.8) / 1.0,
            "target": random.uniform(0.12, 0.5) * self.h, "color": random.choice(self.COLORS)
        })

    def _explode(self, x, y, color):
        n = random.randint(45, 70)
        for i in range(n):
            ang = 2 * math.pi * i / n + random.uniform(-0.1, 0.1)
            speed = random.uniform(90, 330)
            self.sparks.append({
                "x": x, "y": y, "vx": math.cos(ang) * speed, "vy": math.sin(ang) * speed,
                "life": random.uniform(0.9, 1.7), "age": 0.0,
                "color": color if random.random() < 0.75 else random.choice(self.COLORS),
            })

    def _tick(self):
        if self._finished:
            return
        now = time.time()
        dt = min(0.05, now - self._last)
        self._last = now
        elapsed = now - self.started

        if elapsed >= self.duration:
            self.finish()
            return

        if elapsed < self.duration - 1.8 and random.random() < 0.12:
            self._launch()

        c = self.canvas
        c.delete("fw")
        for r in self.rockets[:]:
            r["y"] += r["vy"] * dt
            c.create_oval(r["x"] - 2, r["y"] - 2, r["x"] + 2, r["y"] + 2, fill="#ffe9a0", outline="", tags="fw")
            if r["y"] <= r["target"]:
                self.rockets.remove(r)
                self._explode(r["x"], r["y"], r["color"])
        for s in self.sparks[:]:
            s["age"] += dt
            if s["age"] >= s["life"]:
                self.sparks.remove(s)
                continue
            s["vy"] += 220 * dt
            s["vx"] *= 0.985
            s["x"] += s["vx"] * dt
            s["y"] += s["vy"] * dt
            rad = 4.8 * (1 - s["age"] / s["life"]) + 1.0
            c.create_oval(s["x"] - rad, s["y"] - rad, s["x"] + rad, s["y"] + rad,
                          fill=s["color"], outline="", tags="fw")
        c.tag_raise("txt")
        self.win.after(33, self._tick)

    def finish(self):
        if self._finished:
            return
        self._finished = True
        try:
            if self.win is not None:
                self.win.destroy()
        except Exception:
            pass
        if self.on_done:
            try:
                self.on_done()
            except Exception as e:
                logging.error(f"Ошибка после праздничной анимации: {e}")
