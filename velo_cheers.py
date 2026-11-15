# velo_cheers.py - мотивационные поздравления героев в конце успешной тренировки

import logging
import os
import random
import sys
import tkinter as tk

DEFAULT_CHILD_NAME = "Лёва"
CHEER_SECONDS = 25

# (имя героя, файл картинки)
HEROES = (
    ("Человек-паук", "01_spiderman.png"),
    ("Халк", "02_hulk.png"),
    ("Железный человек", "03_ironman.png"),
    ("Капитан Америка", "04_captain_america.png"),
    ("Тор", "05_thor.png"),
    ("Чёрная Пантера", "06_black_panther.png"),
    ("Грут", "07_groot.png"),
    ("Супермен", "08_superman.png"),
    ("Бэтмен", "09_batman.png"),
    ("Флэш", "10_flash.png"),
    ("Аквамен", "11_aquaman.png"),
    ("Пикачу", "12_pikachu.png"),
    ("Соник", "13_sonic.png"),
    ("Тейлз", "14_tails.png"),
    ("Наклз", "15_knuckles.png"),
    ("Рыцарь", "16_knight.png"),
    ("Хорнет", "17_hornet.png"),
    ("Зот Могучий", "18_zote.png"),
    ("Квиррел", "19_quirrel.png"),
    ("Мастер Гримм", "20_grimm.png"),
)

# По два поздравления на каждого героя: CHEERS[2*i], CHEERS[2*i + 1] принадлежат HEROES[i].
CHEERS = (
    "{имя}, ты просто человек-паук на велосипеде!!! Продолжай и станешь Халком!!!",
    "{имя}, паутина не нужна: ты и так цепляешься за асфальт, как Человек-паук!",
    "{имя}, Халк увидел твои педали и сказал: «Хочу так же!» Продолжай и станешь ещё зеленее!",
    "{имя}, от такой нагрузки даже Халк вспотел бы. Норма выполнена, не ругайся!",
    "{имя}, Тони Старк выдал тебе новую броню за выполненную норму! Завтра ещё прокачаем.",
    "{имя}, Железный человек говорит: «Мощность реактора: 100%». А это всё твои ноги!",
    "{имя}, Капитан Америка отдаёт честь: щит у него есть, а такой выносливости ещё нет!",
    "{имя}, ты сегодня бросил вызов лени и победил. Капитан Америка гордится тобой!",
    "{имя}, молот Тора мечтает о таких мышцах. Норма выполнена, достоин!",
    "{имя}, Тор уронил молот от восхищения. Ты сегодня гремел как гром!",
    "{имя}, Ваканда навсегда! Чёрная Пантера бесшумно подкралась к твоей норме и добила её!",
    "{имя}, Чёрная Пантера в восторге: ты быстр и ловок, как настоящий король Ваканды!",
    "{имя}, я есть Грут! (Перевод: ты сегодня молодец!)",
    "{имя}, даже Грут за сегодня вырос на сантиметр, а ты на целый уровень!",
    "{имя}, Супермен вызывает тебя в Лигу: летать он умеет, а крутить так, как ты, нет!",
    "{имя}, криптонит тебе не страшен: норма выполнена, силы только прибавилось!",
    "{имя}, Бэтмен кивнул из темноты. Для него это высшая похвала!",
    "{имя}, Бэтмен придумал для тебя новый гаджет: педали сильнее, чем у Бэтмобиля!",
    "{имя}, Флэш просил передать: «Я быстрый, но ты настойчивее». Норма взята!",
    "{имя}, лёгкий ветерок по ногам: ты сегодня был быстрее Флэша на разминке!",
    "{имя}, Аквамен вызвал рыб на аплодисменты: ты покорил даже морские глубины!",
    "{имя}, Аквамен сказал: «Такой темп и океан бы пересёк!» Норма выполнена!",
    "{имя}, Пикачу заряжен на 100%, и ты тоже. Молния! Пика-пика, ты чемпион!",
    "{имя}, Пикачу даёт тебе пять и оставляет разряд на завтра. Так держать!",
    "{имя}, Соник сказал: «Ого, это я бегаю, а ты крутишь так же!» Норма сделана!",
    "{имя}, колец собрано достаточно: ты быстрее синего ёжика!",
    "{имя}, Тейлз говорит: «Мои хвосты вертятся, как твои педали!» Отличный результат!",
    "{имя}, Тейлз посчитал: сегодня ты рекордно эффективен. Инженер одобряет!",
    "{имя}, Наклз сжал кулаки от гордости: «Вот это сила, достойная настоящего стража!»",
    "{имя}, Наклз ищет изумруд, а ты сегодня нашёл его: норма выполнена!",
    "{имя}, Рыцарь Полого королевства склонил голову: ты прошёл свой день без единого удара!",
    "{имя}, пустота довольна: в Королевстве вырос новый герой, и это ты!",
    "{имя}, Хорнет метнула иглу, но промахнулась: она ожидала тебя уставшим. А ты и не устал!",
    "{имя}, Хорнет сказала: «Неплохо», а это у неё самая высокая похвала. Норма выполнена!",
    "{имя}, Зот Могучий объявляет: «Я победил сегодняшнюю норму!» Ладно, это ты победил. Ты тоже могучий!",
    "{имя}, сам Зот Могучий признал твоё могущество, хотя он такого раньше не говорил!",
    "{имя}, Квиррел записал в дневник: «Сегодня встретил путника с невероятной выносливостью!»",
    "{имя}, Квиррел бы присел отдохнуть, а ты ещё и на одной ноге крутил! Норма выполнена!",
    "{имя}, Мастер Гримм аплодирует: «Достойное представление, юный танцор!»",
    "{имя}, огонь Гримма горит в твоих ногах, но не поджигает кухню! Продолжай!",
)


def resource_path(relative):
    """Путь к файлу ресурса: рядом с исходниками или внутри собранного exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def pick_cheer(last_hero=None, rng=random):
    """Случайное поздравление; герой не повторяется подряд. Возвращает (индекс героя, текст)."""
    heroes = [i for i in range(len(HEROES)) if i != last_hero]
    hero = rng.choice(heroes)
    text = CHEERS[hero * 2 + rng.randint(0, 1)]
    return hero, text


def format_cheer(text, child_name):
    return text.replace("{имя}", (child_name or DEFAULT_CHILD_NAME).strip() or DEFAULT_CHILD_NAME)


class CheerWindow:
    """Окно с героем и поздравлением. Закрывается кнопкой или само."""

    BG = "#fff8e1"

    def __init__(self, root, hero_index, text, on_close=None, auto_close_sec=CHEER_SECONDS):
        self.root = root
        self.hero_index = hero_index
        self.text = text
        self.on_close = on_close
        self.auto_close_sec = auto_close_sec
        self.win = None
        self._img = None
        self._left = auto_close_sec
        self._label = None
        self._after_id = None

    def show(self):
        hero_name, filename = HEROES[self.hero_index]
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("Вело-контроллер")
        win.configure(bg=self.BG)
        win.attributes('-topmost', True)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self.close)

        try:
            image = tk.PhotoImage(file=resource_path(os.path.join("heroes", filename)))
            self._img = image.subsample(2, 2)      # 512 -> 256 px
            tk.Label(win, image=self._img, bg=self.BG).pack(side=tk.LEFT, padx=(18, 8), pady=14)
        except Exception as e:
            logging.warning(f"Не удалось загрузить картинку героя {filename}: {e}")

        right = tk.Frame(win, bg=self.BG)
        right.pack(side=tk.LEFT, padx=(8, 22), pady=14)
        tk.Label(right, text=self.text, font=("Arial", 18, "bold"), bg=self.BG, fg="#1f3a5f",
                 wraplength=360, justify=tk.LEFT).pack(anchor="w")
        tk.Label(right, text=f"— {hero_name}", font=("Arial", 12, "italic"), bg=self.BG,
                 fg="#a05a00").pack(anchor="e", pady=(8, 12))
        tk.Button(right, text="Ура!", font=("Arial", 13, "bold"), command=self.close,
                  bg="#2b9e3a", fg="white", activebackground="#1f7a2c", bd=0,
                  padx=28, pady=6).pack(anchor="w")
        self._label = tk.Label(right, text="", font=("Arial", 9), bg=self.BG, fg="#777")
        self._label.pack(anchor="w", pady=(6, 0))

        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_width()) // 2
        y = max(0, (win.winfo_screenheight() - win.winfo_height()) // 3)
        win.geometry(f"+{x}+{y}")
        if self.auto_close_sec:
            self._tick()

    def _tick(self):
        if self.win is None:
            return
        if self._left <= 0:
            self.close()
            return
        self._label.config(text=f"Окно закроется само через {self._left} с")
        self._left -= 1
        self._after_id = self.win.after(1000, self._tick)

    def close(self):
        win, self.win = self.win, None
        if win is not None:
            try:
                if self._after_id is not None:
                    win.after_cancel(self._after_id)
                win.destroy()
            except Exception:
                pass
        if self.on_close:
            try:
                self.on_close()
            except Exception as e:
                logging.error(f"Ошибка закрытия поздравления: {e}")
