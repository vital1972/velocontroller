# velo_welcome.py - приветственное окно с историей тренировок за 3 месяца

import calendar
import logging
import math
import tkinter as tk
from datetime import date, datetime

WHEEL_DIAMETER_INCH = 24     # стандартное детское колесо, около 61 см
WHEEL_MIN_INCH = 10
WHEEL_MAX_INCH = 32


def wheel_circumference_m(inches):
    """Путь за один оборот колеса диаметром `inches` дюймов, в метрах."""
    return math.pi * inches * 0.0254

HISTORY_MONTHS = 3
MONTH_NAMES = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
               "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")
WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _to_float(value):
    try:
        return float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        return 0.0


def _is_done(row):
    flag = str(row.get('limit_reached', '0')).strip().lower() in ('1', 'true', 'yes', 'да')
    if flag:
        return True
    limit_min = _to_float(row.get('limit_minutes'))
    return limit_min > 0 and _to_float(row.get('seconds')) >= limit_min * 60


def _month_shift(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def build_history(rows, today, months=HISTORY_MONTHS):
    """Готовит данные календаря за последние `months` календарных месяцев (включая текущий).

    Возвращает dict: months (список месяцев с неделями), days_done, km_total.
    """
    by_date = {}
    for row in rows:
        try:
            d = datetime.strptime(str(row.get('date', '')), "%Y-%m-%d").date()
        except ValueError:
            continue
        by_date[d] = row

    result = {"months": [], "days_done": 0, "km_total": 0.0}
    for offset in range(-(months - 1), 1):
        year, month = _month_shift(today.year, today.month, offset)
        weeks = []
        for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
            cells = []
            for day in week:
                if day == 0:
                    cells.append(None)
                    continue
                d = date(year, month, day)
                row = by_date.get(d)
                done = bool(row) and _is_done(row) and d <= today
                if done:
                    result["days_done"] += 1
                if row and d <= today:
                    result["km_total"] += _to_float(row.get('km'))
                cells.append({"day": day, "done": done, "today": d == today, "future": d > today})
            weeks.append(cells)
        result["months"].append({"year": year, "month": month,
                                 "title": f"{MONTH_NAMES[month - 1]} {year}", "weeks": weeks})
    return result


def plural_days(n):
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "дня"
    return "дней"


class WelcomeWindow:
    """Окно-приветствие с календарём. Не блокирует работу, закрывается кнопкой или программно."""

    BG = "#f7f9fc"
    DONE_BG = "#1f3a5f"
    LIKE_COLOR = "#FFC400"
    STAT_COLOR = "#1e9e3a"
    TODAY_BORDER = "#2b78b8"

    def __init__(self, root, history, goal_text, on_close=None):
        self.root = root
        self.history = history
        self.goal_text = goal_text
        self.on_close = on_close
        self.win = None

    def show(self):
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("Вело-контроллер")
        win.configure(bg=self.BG)
        win.attributes('-topmost', True)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self.close)

        tk.Label(win, text="Привет! Пора на тренировку", font=("Arial", 20, "bold"),
                 bg=self.BG, fg="#1f3a5f").pack(padx=24, pady=(18, 2))
        tk.Label(win, text=self.goal_text, font=("Arial", 11), bg=self.BG, fg="#444").pack(padx=24)

        days = self.history["days_done"]
        km = self.history["km_total"]
        line = tk.Frame(win, bg=self.BG)
        line.pack(padx=24, pady=(10, 8))
        small = ("Arial", 12, "bold")
        big = ("Arial", 24, "bold")
        parts = (
            ("За последние 3 месяца: тренировочных дней выполнено — ", small, "#1f3a5f"),
            (str(days), big, self.STAT_COLOR),
            (", пройдено — ", small, "#1f3a5f"),
            (f"{km:.1f}", big, self.STAT_COLOR),
            (" км", small, "#1f3a5f"),
        )
        for text, font, color in parts:
            tk.Label(line, text=text, font=font, bg=self.BG, fg=color).pack(side=tk.LEFT, anchor="s")

        row = tk.Frame(win, bg=self.BG)
        row.pack(padx=18, pady=4)
        for month in self.history["months"]:
            self._month_frame(row, month).pack(side=tk.LEFT, padx=8, anchor="n")

        tk.Label(win, text="👍 — норма в этот день выполнена", font=("Segoe UI Emoji", 11),
                 bg=self.BG, fg="#d99a00").pack(pady=(6, 2))
        tk.Button(win, text="Поехали!", font=("Arial", 12, "bold"), command=self.close,
                  bg="#2b78b8", fg="white", activebackground="#1f5f94", bd=0,
                  padx=24, pady=6).pack(pady=(6, 16))

        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_width()) // 2
        y = max(0, (win.winfo_screenheight() - win.winfo_height()) // 3)
        win.geometry(f"+{x}+{y}")

    def _month_frame(self, parent, month):
        frame = tk.Frame(parent, bg="white", bd=1, relief="solid")
        tk.Label(frame, text=month["title"], font=("Arial", 11, "bold"), bg="white").grid(
            row=0, column=0, columnspan=7, pady=(4, 2))
        for col, name in enumerate(WEEKDAY_NAMES):
            tk.Label(frame, text=name, font=("Arial", 9), bg="white",
                     fg="#c0392b" if col >= 5 else "#666", width=4).grid(row=1, column=col)
        for r, week in enumerate(month["weeks"], start=2):
            for c, cell in enumerate(week):
                if cell is None:
                    tk.Frame(frame, bg="white", width=38, height=44).grid(row=r, column=c, padx=1, pady=1)
                    continue
                self._day_cell(frame, r, c, cell)
        tk.Frame(frame, bg="white", height=4).grid(row=99, column=0)
        return frame

    def _day_cell(self, frame, r, c, cell):
        done = cell["done"]
        bg = self.DONE_BG if done else "white"
        box = tk.Frame(frame, bg=bg, width=38, height=44,
                       highlightthickness=2 if cell["today"] else 0,
                       highlightbackground=self.TODAY_BORDER, highlightcolor=self.TODAY_BORDER)
        box.grid(row=r, column=c, padx=1, pady=1)
        box.grid_propagate(False)
        box.pack_propagate(False)
        fg = "#bbb" if cell["future"] else ("white" if done else "#222")
        tk.Label(box, text=str(cell["day"]), font=("Arial", 9), bg=bg, fg=fg).pack(pady=(1, 0))
        if done:
            tk.Label(box, text="👍", font=("Segoe UI Emoji", 15), bg=bg, fg=self.LIKE_COLOR).pack()

    def close(self):
        win, self.win = self.win, None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        if self.on_close:
            try:
                self.on_close()
            except Exception as e:
                logging.error(f"Ошибка закрытия приветствия: {e}")
