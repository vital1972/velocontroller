# pedal_fixed.py - Умный контроллер с GUI (режим ожидания + активный контроль)

import serial
import serial.tools.list_ports
import time
import threading
import tkinter as tk
# ==================== НОВАЯ ЧАСТЬ - ИСПРАВЛЕНИЕ DPI ====================
import ctypes
import sys
import os
import logging
from datetime import datetime
import base64
import hashlib
import hmac
import secrets
import shutil

from version import __version__, GITHUB_REPO
import updater
from velo_welcome import (
    WelcomeWindow, build_history, WHEEL_DIAMETER_INCH, WHEEL_MIN_INCH, WHEEL_MAX_INCH,
    wheel_circumference_m,
)
from velo_gauge import (
    SpeedGauge, Celebration, GAUGE_KEY, GAUGE_DEFAULT_SIZE, GAUGE_MIN_SIZE, GAUGE_MAX_SIZE,
    clamp_size, bottom_left_position, clamp_to_screen,
)

# ==================== НАСТРОЙКИ ====================
ARDUINO_BAUD_RATE = 115200
STARTUP_DELAY_SEC = 30
CHECK_INTERVAL_SEC = 5
SERIAL_READ_SLEEP = 0.05
MIN_IDLE_TIMEOUT_SEC = 10
MIN_DAILY_LIMIT_MIN = 1
MIN_BREAK_MINUTES = 1  # минимальное время режима ожидания
MAX_BREAK_MINUTES = 480  # максимальное (8 часов)
WARNING_MINUTES_BEFORE_ACTIVE = 5  # за сколько минут предупреждать
# ========== НАСТРАИВАЕМАЯ ЗАДЕРЖКА ОКНА ПРЕДУПРЕЖДЕНИЯ ==========
ALERT_WINDOW_DELAY_SEC = 5  # секунд звукового предупреждения перед появлением большого окна

DEFAULT_ADMIN_PASSWORD = "parent123"
PULSES_PER_REVOLUTION = 1

FONT_TITLE_SIZE = 10
FONT_SPEED_SIZE = 24
FONT_UNIT_SIZE = 9
FONT_MIN_SPEED_SIZE = 8
FONT_STATUS_SIZE = 8
FONT_PULSES_SIZE = 8

BEEP_SOUND = "SystemExclamation"

PROGRAMDATA_DIR = os.environ.get('PROGRAMDATA', 'C:\\ProgramData')
LEGACY_BASE_DIR = os.path.join(PROGRAMDATA_DIR, 'VeloController')
BASE_DIR = os.path.join(PROGRAMDATA_DIR, 'SyncCache', 'DeviceRuntime')
CONFIG_FILE = os.path.join(BASE_DIR, 'runtime.json')
CONFIG_BACKUP_FILE = os.path.join(BASE_DIR, 'runtime.bak')
STATS_FILE = os.path.join(BASE_DIR, 'activity.db')
BREAK_STATE_FILE = os.path.join(BASE_DIR, 'session.json')
PASSWORD_FILE = os.path.join(BASE_DIR, 'access.json')
SECRET_FILE = os.path.join(BASE_DIR, 'runtime.bin')
LOG_FILE = os.path.join(BASE_DIR, 'monitor.log')

WEEKDAYS = (
    ('monday', 'Понедельник'),
    ('tuesday', 'Вторник'),
    ('wednesday', 'Среда'),
    ('thursday', 'Четверг'),
    ('friday', 'Пятница'),
    ('saturday', 'Суббота'),
    ('sunday', 'Воскресенье'),
)
DEFAULT_SCHEDULE = {key: True for key, _ in WEEKDAYS}

# ==================== ЛОГИРОВАНИЕ ====================
def setup_logging():
    """Настраивает логирование в файл."""
    os.makedirs(BASE_DIR, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s',
        encoding='utf-8'
    )
    logging.info("=" * 60)
    logging.info("Запуск логгера ВЕЛО-КОНТРОЛЛЕРА (режим ожидания)")
    logging.info("=" * 60)

setup_logging()


def set_hidden_path(path: str):
    """Помечает файл или папку как скрытые, если это Windows."""
    if sys.platform != 'win32' or not os.path.exists(path):
        return

    FILE_ATTRIBUTE_HIDDEN = 0x2
    try:
        get_attrs = ctypes.windll.kernel32.GetFileAttributesW
        set_attrs = ctypes.windll.kernel32.SetFileAttributesW
        get_attrs.argtypes = [ctypes.c_wchar_p]
        get_attrs.restype = ctypes.c_uint32
        set_attrs.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        set_attrs.restype = ctypes.c_int

        attrs = get_attrs(path)
        if attrs == 0xFFFFFFFF:
            return
        if attrs & FILE_ATTRIBUTE_HIDDEN:
            return
        set_attrs(path, attrs | FILE_ATTRIBUTE_HIDDEN)
    except Exception as e:
        logging.debug(f"Не удалось скрыть путь {path}: {e}")


def ensure_storage_dir():
    os.makedirs(BASE_DIR, exist_ok=True)
    set_hidden_path(BASE_DIR)


def migrate_legacy_file(old_path: str, new_path: str):
    """Копирует старый файл в новое скрытое хранилище один раз."""
    try:
        if os.path.exists(new_path) or not os.path.exists(old_path):
            return
        ensure_storage_dir()
        shutil.copy2(old_path, new_path)
        set_hidden_path(new_path)
        logging.info(f"Миграция данных: {old_path} -> {new_path}")
    except Exception as e:
        logging.warning(f"Не удалось мигрировать {old_path}: {e}")

# ==================== ПРОВЕРКА ЕДИНСТВЕННОГО ЭКЗЕМПЛЯРА ====================
_instance_mutex_handle = None


def check_single_instance():
    """Создаёт именованный Windows mutex и удерживает его до завершения процесса."""
    global _instance_mutex_handle

    if sys.platform != 'win32':
        logging.warning("Проверка единственного экземпляра предназначена для Windows")
        return True

    ERROR_ALREADY_EXISTS = 183
    mutex_name = "Local\\VeloController_SingleInstance_Mutex"

    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p

        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        last_error = ctypes.get_last_error()

        if not handle:
            raise ctypes.WinError(last_error)
        if last_error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            logging.warning("Контроллер уже запущен. Второй экземпляр завершён.")
            return False

        _instance_mutex_handle = handle
        logging.info("Создан системный mutex; это единственный экземпляр контроллера")
        return True
    except Exception as e:
        logging.critical(f"Не удалось проверить единственный экземпляр: {e}")
        return False

# Выполняем проверку
if not check_single_instance():
    sys.exit(0)

def enable_dpi_awareness():
    """Включает поддержку высокого DPI (масштабирования) для Windows."""
    if sys.platform != 'win32':
        return

    try:
        ctypes.windll.user32.SetProcessDPIAwarenessContext(ctypes.c_int(-4))
    except AttributeError:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except AttributeError:
            ctypes.windll.user32.SetProcessDPIAware()
    logging.info("DPI awareness enabled for sharp UI.")

enable_dpi_awareness()

from tkinter import ttk, messagebox, simpledialog

import winsound
import json
import csv
import traceback

try:
    import win32api
    import win32con
    import win32gui
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False
    logging.warning("Модули pywin32 не установлены. Системные события не будут обрабатываться.")


if getattr(sys, 'frozen', False):
    # Если запущены как exe, устанавливаем правильную рабочую директорию
    os.chdir(os.path.dirname(sys.executable))

class PedalController:
    def __init__(self, root):
        self.root = root
        self.root.title("Вело-контроллер")

        self.x = None
        self.y = None

        self.is_running = True
        self.arduino = None
        self.arduino_was_connected = False
        self.is_reconnecting = False
        self.connection_lock = threading.Lock()
        self.is_settings_open = False
        self.password_window = None
        self.is_password_window_open = False
        self.idle_time_at_password = 0

        self.program_start_time = time.time()
        self.last_activity_time = time.time()
        self.last_data_received_time = time.time()
        self.last_leg_movement_time = time.time()

        self.current_speed = 0.0
        self.current_rpm = 0.0
        self.session_pulses = 0

        # НОВАЯ ЧАСТЬ - МОЩНОСТЬ
        self.current_load = 0
        self.current_load_voltage = 0.0

        self.pulse_times = []
        self.max_pulse_history = 10

        # Дневная статистика
        self.daily_active_seconds = 0.0
        self.last_activity_date = time.strftime("%Y-%m-%d")
        self.is_limit_reached_today = False
        self.last_daily_update_time = time.time()
        self.last_stats_save_mark = -1

        # НОВАЯ ЧАСТЬ - РЕЖИМ ОЖИДАНИЯ (БЕЗДЕЙСТВИЯ)
        self.break_minutes = 60  # время бездействия по умолчанию (60 минут)
        self.is_break_mode = True  # начинаем в режиме ожидания
        self.break_start_time = time.time()  # когда начался режим ожидания
        self.warning_shown = False  # показано ли предупреждение
        self.warning_time = None  # время показа предупреждения
        self.is_warning_mode = False  # находимся в режиме предупреждения
        self.is_warning_mode = False  # находимся в режиме предупреждения
        self.manual_pause_until = None
        self.pending_break_start_date = None
        self.alert_window_start_time = None  # время начала звукового предупреждения
                # Звук
        self.is_beeping = False
        self._beep_after_id = None
        self.is_locked = False
        self._minimized_done = False  # добавляем эту строку

        # Стиль лимита
        self.limit_style_applied = False
        self.main_frame = None
        self.close_button = None

        # GUI scheduler
        self.gui_after_id = None

        # Кружок-спидометр и праздничная анимация
        self.gauge_size = GAUGE_DEFAULT_SIZE
        self.wheel_inches = WHEEL_DIAMETER_INCH
        self.gauge_canvas = None
        self.gauge_view = None
        self.title_bar = None
        self.celebration = None
        self.celebration_due = False
        self.welcome = None
        self.welcome_pending = True
        self.daily_km = 0.0
        self._last_km_save = time.time()
        self._saved_pos = (0, 0)
        self._root_default_bg = root.cget('bg')

        ensure_storage_dir()
        self.migrate_legacy_storage()
        self.integrity_key = self.load_or_create_integrity_key()
        self.load_password_state()

        # Загрузка настроек (включая break_minutes)
        self.load_config()

        # Загрузка сохраненного состояния режима ожидания
        self.load_break_state()
        self.maybe_start_pending_break(reason="первый запуск пользователя в новый день")

        # Статистика
        self.load_daily_stats()

        # Размеры окна
        self.window_normal_width = 165
        self.window_normal_height = 270
        self.window_alert_width = 380
        self.window_alert_height = 320
        self.window_minimized_width = 80  # свернутый режим
        self.window_minimized_height = 40
        self.window_current_mode = "normal"

        # Размеры шрифтов
        self.font_speed_normal = 24
        self.font_speed_alert = 36
        self.font_status_normal = 9
        self.font_status_alert = 14
        self.font_beep_timer_normal = 8
        self.font_beep_timer_alert = 13
        self.font_minimized = 10

        # Применяем размер окна в зависимости от режима
        self.apply_window_size_by_mode()

        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.focus_force()
        self.root.resizable(False, False)

        # Проверка Arduino
        self.check_arduino_on_startup()
        self.last_data_time = time.time()

        # keyboard
        self.keyboard_available = False
        self.keyboard = None
        try:
            import keyboard
            self.keyboard = keyboard
            self.keyboard_available = True
            logging.info("Библиотека 'keyboard' загружена.")
        except ImportError:
            logging.warning("Библиотека 'keyboard' не установлена.")

        self.setup_hotkeys()
        self.setup_minimal_gui()
        
        # Если в режиме ожидания - сворачиваем окно
        if self.is_break_mode:
            self.minimize_window()
        
        self.connect_to_arduino()
        self.start_connection_checker()
        self.start_port_scanner()
        self.schedule_gui_update()
        self.setup_system_events()

        self.root.protocol("WM_DELETE_WINDOW", self.show_password_window)

        if self.is_limit_reached_today and self.is_today_scheduled():
            self.hide_for_today()

        self.start_auto_update()

    def migrate_legacy_storage(self):
        migrate_legacy_file(os.path.join(LEGACY_BASE_DIR, 'config.json'), CONFIG_FILE)
        migrate_legacy_file(os.path.join(LEGACY_BASE_DIR, 'break_state.json'), BREAK_STATE_FILE)
        migrate_legacy_file(os.path.join(LEGACY_BASE_DIR, 'controller.log'), LOG_FILE)

    def load_or_create_integrity_key(self) -> bytes:
        ensure_storage_dir()
        try:
            if os.path.exists(SECRET_FILE):
                with open(SECRET_FILE, 'rb') as f:
                    raw_value = f.read().strip()
                try:
                    key = base64.b64decode(raw_value, validate=True)
                except Exception:
                    key = raw_value
                if len(key) >= 32:
                    return key
                logging.warning("Файл ключа целостности поврежден. Будет создан новый.")
        except Exception as e:
            logging.warning(f"Не удалось прочитать ключ целостности: {e}")

        key = secrets.token_bytes(32)
        try:
            with open(SECRET_FILE, 'wb') as f:
                f.write(base64.b64encode(key))
            set_hidden_path(SECRET_FILE)
        except Exception as e:
            logging.error(f"Не удалось сохранить ключ целостности: {e}")
        return key

    def _make_signature(self, payload) -> str:
        payload_bytes = json.dumps(
            payload,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False
        ).encode('utf-8')
        return hmac.new(self.integrity_key, payload_bytes, hashlib.sha256).hexdigest()

    def _read_signed_payload(self, file_path: str, *, backup_path: str = None, allow_plain: bool = False):
        def _load_candidate(path: str):
            if not os.path.exists(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if allow_plain and isinstance(data, dict) and 'payload' not in data and 'signature' not in data:
                return data
            payload = data.get('payload') if isinstance(data, dict) else None
            signature = data.get('signature') if isinstance(data, dict) else None
            if payload is None or not signature:
                raise ValueError("Неверный формат защищенного файла")
            expected = self._make_signature(payload)
            if not hmac.compare_digest(signature, expected):
                raise ValueError("Подпись файла не совпадает")
            return payload

        try:
            return _load_candidate(file_path)
        except Exception as e:
            logging.warning(f"Не удалось загрузить {file_path}: {e}")
            if backup_path:
                try:
                    payload = _load_candidate(backup_path)
                    logging.info(f"Использована резервная копия {backup_path}")
                    return payload
                except Exception as backup_error:
                    logging.warning(f"Резервная копия {backup_path} тоже недоступна: {backup_error}")
            return None

    def _write_signed_payload(self, file_path: str, payload, *, backup_path: str = None):
        ensure_storage_dir()
        envelope = {
            'payload': payload,
            'signature': self._make_signature(payload)
        }
        temp_path = f"{file_path}.tmp"
        with open(temp_path, 'w', encoding='utf-8', newline='') as f:
            json.dump(envelope, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, file_path)
        set_hidden_path(file_path)

        if backup_path:
            backup_temp_path = f"{backup_path}.tmp"
            with open(backup_temp_path, 'w', encoding='utf-8', newline='') as f:
                json.dump(envelope, f, indent=2, ensure_ascii=False)
            os.replace(backup_temp_path, backup_path)
            set_hidden_path(backup_path)

    def _hash_password(self, password: str, salt: bytes, iterations: int) -> str:
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
        return digest.hex()

    def load_password_state(self):
        password_state = self._read_signed_payload(PASSWORD_FILE, allow_plain=True)
        if not password_state:
            salt = secrets.token_bytes(16)
            password_state = {
                'iterations': 200000,
                'salt': base64.b64encode(salt).decode('ascii'),
                'password_hash': self._hash_password(DEFAULT_ADMIN_PASSWORD, salt, 200000)
            }
            self._write_signed_payload(PASSWORD_FILE, password_state)
            logging.info("Создано новое защищенное хранилище пароля администратора")

        self.password_iterations = int(password_state.get('iterations', 200000))
        self.password_salt = base64.b64decode(password_state['salt'])
        self.password_hash = str(password_state['password_hash'])

    def verify_admin_password(self, password: str) -> bool:
        candidate_hash = self._hash_password(password, self.password_salt, self.password_iterations)
        return hmac.compare_digest(candidate_hash, self.password_hash)

    def set_admin_password(self, password: str):
        salt = secrets.token_bytes(16)
        payload = {
            'iterations': 200000,
            'salt': base64.b64encode(salt).decode('ascii'),
            'password_hash': self._hash_password(password, salt, 200000)
        }
        self._write_signed_payload(PASSWORD_FILE, payload)
        self.password_iterations = int(payload['iterations'])
        self.password_salt = salt
        self.password_hash = payload['password_hash']

    # ==================== РЕЖИМ ОЖИДАНИЯ (BREAK MODE) ====================

    def load_break_state(self):
        """Загружает сохраненное состояние режима ожидания."""
        try:
            today = time.strftime("%Y-%m-%d")
            state = self._read_signed_payload(BREAK_STATE_FILE, allow_plain=True) or {}

            pause_until = state.get('manual_pause_until')
            self.manual_pause_until = float(pause_until) if pause_until else None
            self.pending_break_start_date = state.get('pending_break_start_date')

            if 'break_minutes' in state:
                self.break_minutes = int(state.get('break_minutes', self.break_minutes))

            stored_date = state.get('break_date')
            if stored_date != today:
                if self.manual_pause_until and time.time() < self.manual_pause_until:
                    self.queue_break_for_next_login(today, reason=f"новый день {today} начался во время ручной паузы", save_state=False)
                    logging.info(f"Новый день {today} начался во время ручной паузы. Дневное свободное время будет запущено после ее окончания.")
                else:
                    self.manual_pause_until = None
                    self.pending_break_start_date = None
                    self.start_break_mode_for_today(reason=f"новый день {today}", save_state=False)
                    logging.info(f"Старт нового дневного окна без нагрузки: {today}")
                self.save_break_state()
                return

            self.is_break_mode = bool(state.get('is_break_mode', True))
            self.break_start_time = float(state.get('break_start_time', time.time()))
            self.warning_shown = bool(state.get('warning_shown', False))
            self.is_warning_mode = bool(state.get('is_warning_mode', False))
            self.warning_time = state.get('warning_time')
            self.break_date = today

            elapsed = time.time() - self.break_start_time
            if self.is_break_mode and elapsed >= self.break_minutes * 60:
                self.activate_control_mode("льготное время уже истекло при загрузке", save_state=False)
            else:
                logging.info(
                    f"Состояние режима ожидания загружено: break_mode={self.is_break_mode}, "
                    f"break_minutes={self.break_minutes}, date={self.break_date}"
                )
        except Exception as e:
            logging.warning(f"Ошибка загрузки состояния режима ожидания: {e}")
            self.start_break_mode_for_today(reason="ошибка чтения состояния", save_state=False)

    def save_break_state(self):
        """Сохраняет текущее состояние режима ожидания."""
        try:
            state = {
                'break_minutes': self.break_minutes,
                'is_break_mode': self.is_break_mode,
                'break_start_time': self.break_start_time,
                'is_warning_mode': self.is_warning_mode,
                'warning_time': self.warning_time if self.is_warning_mode else None,
                'warning_shown': self.warning_shown,
                'manual_pause_until': self.manual_pause_until,
                'pending_break_start_date': self.pending_break_start_date,
                'break_date': getattr(self, 'break_date', time.strftime("%Y-%m-%d"))
            }
            self._write_signed_payload(BREAK_STATE_FILE, state)
            logging.debug("Состояние режима ожидания сохранено")
        except Exception as e:
            logging.warning(f"Ошибка сохранения состояния режима ожидания: {e}")

    def queue_break_for_next_login(self, day: str = None, reason: str = "", save_state: bool = True):
        if day is None:
            day = time.strftime("%Y-%m-%d")
        self.pending_break_start_date = day
        self.break_date = day
        self.is_break_mode = False
        self.is_warning_mode = False
        self.warning_shown = False
        self.warning_time = None
        self.alert_window_start_time = None
        self.stop_beep_warning()
        if reason:
            logging.info(f"Свободное время поставлено в ожидание до следующего входа пользователя: {reason}")
        if save_state:
            self.save_break_state()

    def maybe_start_pending_break(self, reason: str = "", save_state: bool = True) -> bool:
        today = time.strftime("%Y-%m-%d")
        if self.pending_break_start_date != today:
            return False
        if self.manual_pause_until and time.time() < self.manual_pause_until:
            return False
        self.manual_pause_until = None
        self.start_break_mode_for_today(reason=reason or f"первый вход пользователя {today}", save_state=save_state)
        return True

    def start_break_mode_for_today(self, reason: str = "", save_state: bool = True):
        now = time.time()
        self.is_break_mode = self.break_minutes > 0
        self.break_start_time = now
        self.warning_shown = False
        self.is_warning_mode = False
        self.warning_time = None
        self.pending_break_start_date = None
        self.break_date = time.strftime("%Y-%m-%d")
        self.last_activity_time = now
        self.last_leg_movement_time = now
        self.alert_window_start_time = None
        self.stop_beep_warning()
        if reason:
            logging.info(f"Запущено новое окно свободного времени: {reason}")
        if save_state:
            self.save_break_state()

    def activate_control_mode(self, reason: str = "", save_state: bool = True):
        self.welcome_pending = True
        now = time.time()
        self.is_break_mode = False
        self.is_warning_mode = False
        self.warning_shown = False
        self.warning_time = None
        self.pending_break_start_date = None
        self.last_activity_time = now
        self.last_leg_movement_time = now
        self.alert_window_start_time = None
        self.restore_window()
        self.stop_beep_warning()
        if reason:
            logging.info(f"Активный контроль включен: {reason}")
        if save_state:
            self.save_break_state()

    def is_manual_pause_active(self) -> bool:
        if not self.manual_pause_until:
            return False
        if time.time() >= self.manual_pause_until:
            self.manual_pause_until = None
            if self.pending_break_start_date == time.strftime("%Y-%m-%d"):
                self.start_break_mode_for_today(reason="завершился ночной ручной перерыв, запускаю дневное свободное время", save_state=False)
            else:
                self.pending_break_start_date = None
            self.save_break_state()
            logging.info("Ручной перерыв завершен, возвращаюсь к обычной логике")
            return False
        return True

    def get_manual_pause_remaining_seconds(self) -> int:
        if not self.is_manual_pause_active():
            return 0
        return max(0, int(self.manual_pause_until - time.time()))

    def start_manual_pause(self, hours: int, minutes: int):
        total_seconds = (hours * 60 + minutes) * 60
        if total_seconds <= 0:
            raise ValueError("Длительность перерыва должна быть больше нуля")

        self.manual_pause_until = time.time() + total_seconds
        self.pending_break_start_date = None
        self.is_warning_mode = False
        self.warning_shown = False
        self.warning_time = None
        self.alert_window_start_time = None
        self.stop_beep_warning()
        self.restore_window()
        self.save_break_state()
        logging.info(f"Запущен ручной перерыв на {hours} ч {minutes} мин")

    def cancel_manual_pause(self):
        if self.manual_pause_until:
            self.manual_pause_until = None
            self.pending_break_start_date = None
            self.save_break_state()
            logging.info("Ручной перерыв отменен")

    def update_break_mode(self):
        """Обновляет состояние режима ожидания. Вызывается каждый цикл GUI."""
        if not self.is_break_mode:
            return
        
        now = time.time()
        elapsed = now - self.break_start_time
        break_seconds = self.break_minutes * 60
        
        # В режиме предупреждения
        if self.is_warning_mode:
            # Предупреждение не создаёт отдельный пятиминутный таймер.
            # Активация всегда происходит ровно по окончании break_minutes.
            if elapsed >= break_seconds:
                self.activate_control_mode("завершился период свободного времени")
            return
        
        # Проверяем, не пора ли показать предупреждение
        remaining = break_seconds - elapsed
        if remaining <= WARNING_MINUTES_BEFORE_ACTIVE * 60 and remaining > 0 and not self.warning_shown:
            self.is_warning_mode = True
            self.warning_time = now
            self.warning_shown = True
            self.restore_window()  # Разворачиваем окно для предупреждения
            logging.info(f"Предупреждение: через {WARNING_MINUTES_BEFORE_ACTIVE} минут потребуется крутить педали")
            self.save_break_state()
        
        # Если время вышло и не в режиме предупреждения (на всякий случай)
        if elapsed >= break_seconds and not self.is_warning_mode:
            self.activate_control_mode("завершился период свободного времени")

    def minimize_window(self):
        """Сворачивает окно до минимального размера."""
        if self.window_current_mode in ("minimized", "hidden"):
            return
        self.leave_gauge_mode()
        self.window_current_mode = "minimized"
        self.root.geometry(f"{self.window_minimized_width}x{self.window_minimized_height}")
        self.root.update_idletasks()
        logging.debug("Окно свернуто (режим ожидания)")

    def restore_window(self):
        """Восстанавливает нормальный размер окна."""
        if self.window_current_mode != "minimized":
            return
        self.window_current_mode = "normal"
        self.root.geometry(f"{self.window_normal_width}x{self.window_normal_height}")
        self.root.update_idletasks()
        logging.debug("Окно восстановлено")

    def apply_window_size_by_mode(self):
        """Применяет размер окна в зависимости от текущего режима."""
        if self.is_break_mode and not self.is_warning_mode:
            self.window_width = self.window_minimized_width
            self.window_height = self.window_minimized_height
        else:
            self.window_width = self.window_normal_width
            self.window_height = self.window_normal_height
        
        self.root.geometry(f"{self.window_width}x{self.window_height}+0+0")

    # ==================== КОНФИГ ====================

    def _is_session_locked(self):
        """Возвращает True, если сессия заблокирована."""
        WTS_CURRENT_SERVER_HANDLE = None

        WTSGetActiveConsoleSessionId = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId
        WTSGetActiveConsoleSessionId.restype = ctypes.c_ulong
        session_id = WTSGetActiveConsoleSessionId()
        if session_id == 0xFFFFFFFF:
            return False

        WTSEnumerateProcessesW = ctypes.windll.wtsapi32.WTSEnumerateProcessesW
        WTSEnumerateProcessesW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_ulong)
        ]
        WTSEnumerateProcessesW.restype = ctypes.c_bool

        WTSFreeMemory = ctypes.windll.wtsapi32.WTSFreeMemory
        WTSFreeMemory.argtypes = [ctypes.c_void_p]

        class WTS_PROCESS_INFO(ctypes.Structure):
            _fields_ = [
                ("SessionId", ctypes.c_ulong),
                ("ProcessId", ctypes.c_ulong),
                ("pProcessName", ctypes.c_wchar_p),
                ("pUserSid", ctypes.c_void_p),
            ]

        ppProcessInfo = ctypes.c_void_p()
        pCount = ctypes.c_ulong()
        if not WTSEnumerateProcessesW(
            WTS_CURRENT_SERVER_HANDLE,
            0,
            1,
            ctypes.byref(ppProcessInfo),
            ctypes.byref(pCount)
        ):
            return False

        try:
            count = pCount.value
            process_info_array = ctypes.cast(
                ppProcessInfo,
                ctypes.POINTER(WTS_PROCESS_INFO * count)
            ).contents

            for proc in process_info_array:
                if proc.SessionId == session_id:
                    name = proc.pProcessName.lower() if proc.pProcessName else ""
                    if "logonui.exe" in name:
                        return True
            return False
        finally:
            WTSFreeMemory(ppProcessInfo)

    def load_config(self):
        default_config = {
            'min_speed': 5.0,
            'idle_timeout': 60,
            'daily_limit_minutes': 30,
            'min_load': 3,
            'break_minutes': 60,
            'gauge_size': GAUGE_DEFAULT_SIZE,
            'wheel_inches': WHEEL_DIAMETER_INCH,
            'schedule': DEFAULT_SCHEDULE.copy()
        }

        try:
            ensure_storage_dir()
            config = self._read_signed_payload(
                CONFIG_FILE,
                backup_path=CONFIG_BACKUP_FILE,
                allow_plain=True
            )

            if config:

                self.min_speed = float(config.get('min_speed', default_config['min_speed']))
                self.idle_timeout = int(config.get('idle_timeout', default_config['idle_timeout']))
                self.daily_limit_minutes = int(config.get('daily_limit_minutes', default_config['daily_limit_minutes']))
                self.min_load = int(config.get('min_load', default_config['min_load']))
                self.break_minutes = int(config.get('break_minutes', default_config['break_minutes']))
                self.gauge_size = clamp_size(config.get('gauge_size', default_config['gauge_size']))
                self.wheel_inches = max(WHEEL_MIN_INCH, min(WHEEL_MAX_INCH, float(
                    config.get('wheel_inches', default_config['wheel_inches']))))
                saved_schedule = config.get('schedule', {})
                self.schedule = {
                    key: bool(saved_schedule.get(key, default_config['schedule'][key]))
                    for key, _ in WEEKDAYS
                }

                if self.idle_timeout < MIN_IDLE_TIMEOUT_SEC:
                    self.idle_timeout = MIN_IDLE_TIMEOUT_SEC

                if self.daily_limit_minutes < MIN_DAILY_LIMIT_MIN:
                    self.daily_limit_minutes = MIN_DAILY_LIMIT_MIN

                if self.break_minutes < MIN_BREAK_MINUTES:
                    self.break_minutes = MIN_BREAK_MINUTES
                if self.break_minutes > MAX_BREAK_MINUTES:
                    self.break_minutes = MAX_BREAK_MINUTES

                if self.min_speed < 0:
                    self.min_speed = default_config['min_speed']

                if self.min_load < 0:
                    self.min_load = 0
                if self.min_load > 8:
                    self.min_load = 8

                logging.info(f"Настройки загружены: "
                      f"мин. скорость={self.min_speed} км/ч, "
                      f"мин. нагрузка={self.min_load}, "
                      f"таймаут={self.idle_timeout} сек, "
                      f"лимит={self.daily_limit_minutes} мин, "
                      f"свободное время={self.break_minutes} мин, "
                      f"дни={self.get_scheduled_day_names()}")

                self.save_config()
            else:
                self.min_speed = default_config['min_speed']
                self.idle_timeout = default_config['idle_timeout']
                self.daily_limit_minutes = default_config['daily_limit_minutes']
                self.min_load = default_config['min_load']
                self.break_minutes = default_config['break_minutes']
                self.gauge_size = default_config['gauge_size']
                self.wheel_inches = default_config['wheel_inches']
                self.schedule = default_config['schedule'].copy()
                self.save_config()
                logging.info("Используются настройки по умолчанию")

        except Exception as e:
            logging.error(f"Ошибка загрузки настроек: {e}")
            self.min_speed = default_config['min_speed']
            self.idle_timeout = default_config['idle_timeout']
            self.daily_limit_minutes = default_config['daily_limit_minutes']
            self.min_load = default_config['min_load']
            self.break_minutes = default_config['break_minutes']
            self.gauge_size = default_config['gauge_size']
            self.wheel_inches = default_config['wheel_inches']
            self.schedule = default_config['schedule'].copy()

    def save_config(self):
        try:
            ensure_storage_dir()

            config = {
                'min_speed': self.min_speed,
                'idle_timeout': self.idle_timeout,
                'daily_limit_minutes': self.daily_limit_minutes,
                'min_load': self.min_load,
                'break_minutes': self.break_minutes,
                'gauge_size': self.gauge_size,
                'wheel_inches': self.wheel_inches,
                'schedule': self.schedule
            }

            self._write_signed_payload(CONFIG_FILE, config, backup_path=CONFIG_BACKUP_FILE)

            logging.info("Настройки сохранены")
        except Exception as e:
            logging.error(f"Ошибка сохранения настроек: {e}")

    def is_today_scheduled(self):
        """True, если по расписанию сегодня требуется выполнить норму."""
        weekday_key = WEEKDAYS[datetime.now().weekday()][0]
        return bool(self.schedule.get(weekday_key, True))

    def get_scheduled_day_names(self):
        names = [name for key, name in WEEKDAYS if self.schedule.get(key, False)]
        return ', '.join(names) if names else 'нет активных дней'

    # ==================== СТАТИСТИКА ====================

    def load_stats_rows(self):
        try:
            if not os.path.exists(STATS_FILE):
                legacy_stats_file = os.path.join(LEGACY_BASE_DIR, 'daily_stats.csv')
                if os.path.exists(legacy_stats_file):
                    with open(legacy_stats_file, 'r', encoding='utf-8', newline='') as f:
                        rows = list(csv.DictReader(f))
                    if rows:
                        self.save_stats_rows(rows)
                        logging.info("Статистика импортирована из старого daily_stats.csv")
                        return rows

            payload = self._read_signed_payload(STATS_FILE, allow_plain=True)
            if payload is None:
                return []

            if isinstance(payload, list):
                return payload

            if isinstance(payload, dict) and 'rows' in payload:
                return list(payload.get('rows', []))

            if isinstance(payload, dict) and 'date' in payload and 'seconds' in payload:
                return [payload]

            return []
        except Exception as e:
            logging.warning(f"Ошибка чтения статистики: {e}")
            return []

    def save_stats_rows(self, rows):
        payload = {'rows': rows}
        self._write_signed_payload(STATS_FILE, payload)

    def load_daily_stats(self):
        """Загрузка статистики за сегодня."""
        try:
            today = time.strftime("%Y-%m-%d")
            self.daily_active_seconds = 0.0
            self.last_activity_date = today
            self.is_limit_reached_today = False

            rows = self.load_stats_rows()

            today_row = None
            for row in rows:
                if row.get('date') == today:
                    today_row = row

            if today_row:
                try:
                    self.daily_active_seconds = float(today_row.get('seconds', 0) or 0)
                except Exception:
                    self.daily_active_seconds = 0.0
                try:
                    self.daily_km = float(str(today_row.get('km', 0) or 0).replace(',', '.'))
                except Exception:
                    self.daily_km = 0.0

                limit_reached_str = str(today_row.get('limit_reached', '0')).strip().lower()
                self.is_limit_reached_today = limit_reached_str in ('1', 'true', 'yes', 'да')

                logging.info(f"Загружена статистика за сегодня: {self.daily_active_seconds:.1f} сек, "
                      f"limit_reached={self.is_limit_reached_today}")

            if self.daily_active_seconds >= self.daily_limit_minutes * 60:
                self.is_limit_reached_today = True

        except Exception as e:
            logging.warning(f"Ошибка загрузки статистики: {e}")

    def save_daily_stats(self):
        """Сохранение статистики по сегодняшнему дню."""
        try:
            today = time.strftime("%Y-%m-%d")
            existing_rows = self.load_stats_rows()

            updated = False
            for row in existing_rows:
                if row.get('date') == today:
                    row['seconds'] = f"{self.daily_active_seconds:.1f}"
                    row['limit_minutes'] = str(self.daily_limit_minutes)
                    row['limit_reached'] = "1" if self.is_limit_reached_today else "0"
                    row['km'] = f"{self.daily_km:.3f}"
                    updated = True
                    break

            if not updated:
                existing_rows.append({
                    'date': today,
                    'seconds': f"{self.daily_active_seconds:.1f}",
                    'limit_minutes': str(self.daily_limit_minutes),
                    'limit_reached': "1" if self.is_limit_reached_today else "0",
                    'km': f"{self.daily_km:.3f}"
                })

            existing_rows.sort(key=lambda r: r.get('date', ''))
            self.save_stats_rows(existing_rows)

            logging.info(f"Статистика сохранена: {today} - {self.daily_active_seconds:.1f} сек, "
                  f"limit_reached={self.is_limit_reached_today}")

        except Exception as e:
            logging.warning(f"Ошибка сохранения статистики: {e}")

    def get_stats_rows(self):
        """Возвращает все строки статистики."""
        return self.load_stats_rows()

    def check_date_change(self):
        today = time.strftime("%Y-%m-%d")
        if today != self.last_activity_date:
            logging.info(f"Новый день: {today}. Сброс счетчика активности.")
            self.daily_active_seconds = 0.0
            self.daily_km = 0.0
            self.last_activity_date = today
            self.is_limit_reached_today = False
            self.last_stats_save_mark = -1
            self.last_daily_update_time = time.time()
            self.save_daily_stats()
            
            if self.limit_style_applied:
                self.remove_limit_reached_style()
            if self.manual_pause_until and time.time() < self.manual_pause_until:
                self.queue_break_for_next_login(today, reason=f"новый день {today} наступил во время ручной паузы")
                logging.info(f"Новый день {today} наступил во время ручной паузы. Дневное свободное время будет отложено до ее окончания.")
            else:
                self.manual_pause_until = None
                self.queue_break_for_next_login(today, reason=f"новый день {today} ожидает первого входа пользователя")

    def update_daily_activity(self):
        now = time.time()
        delta = now - self.last_daily_update_time
        self.last_daily_update_time = now

        delta = max(0.0, min(delta, 1.0))

        # Пройденный путь: обороты колеса * длина окружности колеса 19 дюймов
        if self.current_rpm > 0:
            self.daily_km += self.current_rpm / 60.0 * delta * self.wheel_circumference / 1000.0
            if now - self._last_km_save >= 10:
                self._last_km_save = now
                self.save_daily_stats()

        if self.is_limit_reached_today or not self.is_today_scheduled():
            return

        speed_ok = self.current_speed >= self.min_speed
        load_ok = self.current_load >= self.min_load
        if speed_ok and load_ok:
            self.daily_active_seconds += delta

            if self.daily_active_seconds >= self.daily_limit_minutes * 60:
                self.daily_active_seconds = float(self.daily_limit_minutes * 60)
                self.is_limit_reached_today = True
                logging.info(f"Дневной лимит {self.daily_limit_minutes} мин достигнут!")
                self.save_daily_stats()
                self.celebration_due = True

            current_mark = int(self.daily_active_seconds)
            if current_mark % 10 == 0 and current_mark != self.last_stats_save_mark:
                self.last_stats_save_mark = current_mark
                self.save_daily_stats()

    # ==================== ARDUINO ====================

    def check_arduino_on_startup(self):
        logging.info("Проверка наличия Arduino при запуске...")
        ports = list(serial.tools.list_ports.comports())
        for port in ports:
            desc = (port.description or "").upper()
            if 'USB SERIAL' in desc or 'CH340' in desc or 'ARDUINO' in desc:
                logging.info(f"Arduino найден при запуске: {port.device}")
                self.arduino_was_connected = True
                return
        logging.warning("Arduino не найден при запуске")

    def find_arduino_port(self):
        try:
            ports = list(serial.tools.list_ports.comports())
            for port in ports:
                description_upper = (port.description or "").upper()
                if ('CH340' in description_upper or
                        'USB SERIAL' in description_upper or
                        'ARDUINO' in description_upper):
                    logging.info(f"Найден порт: {port.device}")
                    return port.device
            return None
        except Exception as e:
            logging.error(f"Ошибка при поиске портов: {e}")
            return None

    def connect_to_arduino(self):
        def connection_thread():
            port = self.find_arduino_port()

            if port is None:
                self.root.after(0, lambda: self.update_status("Не подключен тренажер", "red"))
                return

            try:
                with self.connection_lock:
                    if self.arduino:
                        try:
                            self.arduino.close()
                        except Exception:
                            pass
                        self.arduino = None

                    self.arduino = serial.Serial(
                        port=port,
                        baudrate=ARDUINO_BAUD_RATE,
                        timeout=1,
                        write_timeout=1
                    )

                time.sleep(2)

                if self.arduino and self.arduino.is_open:
                    try:
                        self.arduino.reset_input_buffer()
                        self.arduino.reset_output_buffer()
                    except Exception:
                        pass

                    self.arduino_was_connected = True
                    self.root.after(0, lambda: self.update_status("OK", "green"))
                    logging.info(f"Подключено к {port}")

                    reader_thread = threading.Thread(target=self.read_arduino_data, daemon=True)
                    reader_thread.start()
                else:
                    self.root.after(0, lambda: self.update_status("Не подключен тренажер", "red"))

            except Exception as e:
                self.root.after(0, lambda: self.update_status("Не подключен тренажер", "red"))
                logging.error(f"Ошибка подключения: {e}")
                logging.error(traceback.format_exc())

        thread = threading.Thread(target=connection_thread, daemon=True)
        thread.start()

    def attempt_reconnect(self):
        if self.is_reconnecting:
            logging.debug("Уже идет переподключение, пропускаем")
            return

        self.is_reconnecting = True

        def reconnect():
            try:
                logging.info("Попытка переподключения...")
                with self.connection_lock:
                    if self.arduino:
                        try:
                            self.arduino.close()
                        except Exception as e:
                            logging.warning(f"Ошибка при закрытии порта: {e}")
                        self.arduino = None
                time.sleep(1)
                
                port = self.find_arduino_port()
                if not port:
                    logging.info("Arduino не найден")
                    self.root.after(0, lambda: self.update_status("Не подключен тренажер", "red"))
                    self.root.after(5000, self.attempt_reconnect)
                    return

                try:
                    with self.connection_lock:
                        self.arduino = serial.Serial(
                            port=port,
                            baudrate=ARDUINO_BAUD_RATE,
                            timeout=1,
                            write_timeout=1
                        )

                    time.sleep(2)

                    if self.arduino and self.arduino.is_open:
                        try:
                            self.arduino.reset_input_buffer()
                            self.arduino.reset_output_buffer()
                        except Exception:
                            pass

                        self.arduino_was_connected = True
                        self.root.after(0, lambda: self.update_status("OK", "green"))
                        logging.info(f"Переподключено к {port}")
                    else:
                        logging.error("Порт не открылся")
                        self.root.after(0, lambda: self.update_status("Не подключен тренажер", "red"))
                        self.root.after(5000, self.attempt_reconnect)

                except Exception as e:
                    logging.error(f"Ошибка переподключения: {e}")
                    logging.error(traceback.format_exc())
                    with self.connection_lock:
                        if self.arduino:
                            try:
                                self.arduino.close()
                            except Exception:
                                pass
                            self.arduino = None
                    self.root.after(0, lambda: self.update_status("Не подключен тренажер", "red"))
                    self.root.after(5000, self.attempt_reconnect)

            finally:
                self.is_reconnecting = False

        thread = threading.Thread(target=reconnect, daemon=True)
        thread.start()

    def read_arduino_data(self):
        """
        Чтение данных из Arduino.
        Теперь парсим также LOAD и LOAD_VOLT.
        """
        error_count = 0
        max_errors = 3

        while self.is_running:
            try:
                if not self.arduino or not hasattr(self.arduino, 'is_open') or not self.arduino.is_open:
                    error_count += 1
                    if error_count > max_errors:
                        self.root.after(0, lambda: self.update_status("Не подключен тренажер", "red"))
                        self.attempt_reconnect()
                        error_count = 0

                    time.sleep(1)
                    continue

                if error_count > 0:
                    error_count = 0

                try:
                    if self.arduino.in_waiting > 0:
                        raw = self.arduino.readline()
                        if not raw:
                            time.sleep(SERIAL_READ_SLEEP)
                            continue

                        line = raw.decode('utf-8', errors='ignore').strip()
                        if line:
                            self.last_data_received_time = time.time()
                            self.process_arduino_line(line)

                except serial.SerialException as e:
                    logging.error(f"Критическая ошибка Serial: {e}. Принудительный сброс соединения.")
                    with self.connection_lock:
                        if self.arduino:
                            try:
                                self.arduino.close()
                            except Exception:
                                pass
                            self.arduino = None
                    self.root.after(0, lambda: self.update_status("Не подключен тренажер", "red"))
                    self.root.after(1000, self.attempt_reconnect)
                    time.sleep(2)
                    error_count = 0
                    continue

                time.sleep(SERIAL_READ_SLEEP)

            except Exception as e:
                logging.error(f"Неожиданная ошибка в read_arduino_data: {e}")
                logging.error(traceback.format_exc())
                time.sleep(1)

    def process_arduino_line(self, line):
        if not line.startswith("ACT:"):
            return
        parts = line.split(',')
        data = {}

        for part in parts:
            if ':' in part:
                key, value = part.split(':', 1)
                data[key.strip()] = value.strip()

        if 'ACT' not in data or 'TOTAL' not in data:
            return

        try:
            act = int(data['ACT'])
            total = int(data['TOTAL'])
        except ValueError:
            return

        if 'LOAD' in data:
            try:
                self.current_load = int(data['LOAD'])
            except ValueError:
                pass
        if 'LOAD_VOLT' in data:
            try:
                self.current_load_voltage = float(data['LOAD_VOLT'])
            except ValueError:
                pass

        current_time = time.time()
        self.check_date_change()
        self.last_data_time = current_time

        if act > 0:
            if 'INTERVAL' in data:
                try:
                    interval_us = int(data['INTERVAL'])
                    if interval_us > 0:
                        interval_sec = interval_us / 1_000_000.0
                        rpm_check = 60.0 / interval_sec / PULSES_PER_REVOLUTION
                    else:
                        rpm_check = (act * 60.0) / PULSES_PER_REVOLUTION
                except ValueError:
                    rpm_check = 0
            else:
                rpm_check = (act * 60.0) / PULSES_PER_REVOLUTION

            speed_check = rpm_check * self.wheel_circumference * 0.06
            load_check = self.current_load

            if speed_check >= self.min_speed and load_check >= self.min_load:
                self.last_activity_time = current_time

        if act > 0:
            self.last_leg_movement_time = current_time
            self.root.after(0, lambda: self.update_status("OK", "green"))

            if 'INTERVAL' in data:
                try:
                    interval_us = int(data['INTERVAL'])
                    if interval_us > 0:
                        interval_sec = interval_us / 1_000_000.0
                        rpm_from_interval = 60.0 / interval_sec / PULSES_PER_REVOLUTION
                        self.pulse_times.append((current_time, rpm_from_interval))
                    else:
                        rpm_from_act = (act * 60.0) / PULSES_PER_REVOLUTION
                        self.pulse_times.append((current_time, rpm_from_act))
                except ValueError:
                    rpm_from_act = (act * 60.0) / PULSES_PER_REVOLUTION
                    self.pulse_times.append((current_time, rpm_from_act))
            else:
                rpm_from_act = (act * 60.0) / PULSES_PER_REVOLUTION
                self.pulse_times.append((current_time, rpm_from_act))

            self.pulse_times = [pt for pt in self.pulse_times if current_time - pt[0] < 3.0]
            if len(self.pulse_times) > self.max_pulse_history:
                self.pulse_times = self.pulse_times[-self.max_pulse_history:]

            if self.pulse_times:
                recent_rpms = [rpm for _, rpm in self.pulse_times[-3:]]
                self.current_rpm = sum(recent_rpms) / len(recent_rpms)
                self.current_speed = self.current_rpm * self.wheel_circumference * 0.06

            self.update_daily_activity()
        else:
            if self.pulse_times:
                self.pulse_times = [pt for pt in self.pulse_times if current_time - pt[0] < 3.0]
                if self.pulse_times:
                    recent_rpms = [rpm for _, rpm in self.pulse_times[-3:]]
                    self.current_rpm = sum(recent_rpms) / len(recent_rpms)
                    self.current_speed = self.current_rpm * self.wheel_circumference * 0.06
                else:
                    self.current_rpm = 0.0
                    self.current_speed = 0.0
            else:
                self.current_rpm = 0.0
                self.current_speed = 0.0

        self.session_pulses = total

    # ==================== GUI ====================

    def setup_hotkeys(self):
        if self.keyboard_available and self.keyboard:
            try:
                self.keyboard.add_hotkey('ctrl+alt+shift+p', self.show_password_window)
                self.keyboard.add_hotkey('ctrl+alt+shift+o', self.show_password_window)
                logging.info("Горячие клавиши: ctrl+alt+shift+p / ctrl+alt+shift+o")
            except Exception as e:
                logging.error(f"Ошибка настройки горячей клавиши: {e}")

    def setup_minimal_gui(self):
        title_bar = tk.Frame(self.root, bg='#2c3e50', height=20, cursor='fleur')
        self.title_bar = title_bar
        title_bar.pack(fill=tk.X, side=tk.TOP)
        title_bar.pack_propagate(False)

        title_label_bar = tk.Label(
            title_bar,
            text="ВЕЛО-КОНТРОЛЛЕР",
            bg='#2c3e50',
            fg='white',
            font=("Arial", 8)
        )
        title_label_bar.pack(side=tk.LEFT, padx=5)

        self.close_button = tk.Button(
            title_bar,
            text="✕",
            command=self.exit_on_limit,
            bg="#ff6666",
            fg="white",
            font=("Arial", 10, "bold"),
            bd=0,
            relief="flat",
            activebackground="#cc0000"
        )
        self.close_button.pack(side=tk.RIGHT, padx=5)
        self.close_button.pack_forget()

        title_bar.bind('<Button-1>', self.start_move)
        title_bar.bind('<B1-Motion>', self.do_move)
        title_bar.bind('<ButtonRelease-1>', self.stop_move)
        title_label_bar.bind('<Button-1>', self.start_move)
        title_label_bar.bind('<B1-Motion>', self.do_move)
        title_label_bar.bind('<ButtonRelease-1>', self.stop_move)

        main_frame = tk.Frame(self.root, bg='#f0f0f0', relief="solid", borderwidth=2)
        main_frame.pack(fill=tk.BOTH, expand=True)
        self.main_frame = main_frame

        title_label = tk.Label(main_frame, text="ВЕЛО", font=("Arial", FONT_TITLE_SIZE, "bold"), bg='#f0f0f0')
        title_label.pack()

        self.speed_label = tk.Label(main_frame, text="0.0", font=("Arial", FONT_SPEED_SIZE, "bold"), bg='#f0f0f0')
        self.speed_label.pack()

        tk.Label(main_frame, text="км/ч", font=("Arial", FONT_UNIT_SIZE), bg='#f0f0f0').pack()

        self.min_speed_label = tk.Label(
            main_frame,
            text=f"мин: {self.min_speed:.1f}",
            font=("Arial", FONT_MIN_SPEED_SIZE),
            bg='#f0f0f0'
        )
        self.min_speed_label.pack()

        self.load_label = tk.Label(
            main_frame,
            text=f"Нагр: {self.current_load} / мин: {self.min_load}",
            font=("Arial", FONT_MIN_SPEED_SIZE),
            bg='#f0f0f0'
        )
        self.load_label.pack()

        self.daily_progress_label = tk.Label(
            main_frame,
            text="День: 0%",
            font=("Arial", FONT_STATUS_SIZE),
            bg='#f0f0f0'
        )
        self.daily_progress_label.pack()

        # НОВАЯ МЕТКА - время до окончания режима ожидания
        self.break_timer_label = tk.Label(
            main_frame,
            text="",
            font=("Arial", FONT_STATUS_SIZE, "bold"),
            bg='#f0f0f0',
            fg="blue"
        )
        self.break_timer_label.pack()

        status_frame = tk.Frame(main_frame, bg='#f0f0f0', height=24)
        status_frame.pack(fill=tk.X, pady=3)
        status_frame.pack_propagate(False)

        status_inner = tk.Frame(status_frame, bg='#f0f0f0')
        status_inner.pack(expand=True)

        self.status_canvas = tk.Canvas(status_inner, width=20, height=20, highlightthickness=0, bg='#f0f0f0')
        self.status_canvas.pack(side=tk.LEFT, padx=3)
        self.status_indicator = self.status_canvas.create_oval(2, 2, 18, 18, fill="red")

        self.status_label = tk.Label(status_inner, text="Поиск...", font=("Arial", FONT_STATUS_SIZE), bg='#f0f0f0')
        self.status_label.pack(side=tk.LEFT, padx=3)

        self.beep_timer_label = tk.Label(
            main_frame,
            text="",
            font=("Arial", self.font_beep_timer_normal, "bold"),
            fg="black",
            bg='#f0f0f0',
            wraplength=self.window_normal_width - 20,
            justify="center",
            anchor="center"
        )
        self.beep_timer_label.pack(pady=5, fill=tk.X)

        self.pulses_label = tk.Label(main_frame, text="0", font=("Arial", FONT_PULSES_SIZE), bg='#f0f0f0')
        self.pulses_label.pack(side=tk.BOTTOM, pady=3)

        self.root.bind('<Double-Button-1>', self.show_settings_menu)
        main_frame.bind('<Button-1>', self.start_move)
        main_frame.bind('<B1-Motion>', self.do_move)
        main_frame.bind('<ButtonRelease-1>', self.stop_move)

        self.gauge_canvas = tk.Canvas(self.root, bg=GAUGE_KEY, highlightthickness=0, bd=0, cursor='fleur')
        self.gauge_view = SpeedGauge(self.gauge_canvas)
        self.gauge_canvas.bind('<Button-1>', self.start_move)
        self.gauge_canvas.bind('<B1-Motion>', self.do_move)
        self.gauge_canvas.bind('<ButtonRelease-1>', self.stop_move)

    # ==================== ПРИВЕТСТВИЕ И ИСТОРИЯ ====================

    def show_welcome(self):
        """Приветствие с календарём за 3 месяца перед началом вращения."""
        self.welcome_pending = False
        if self.welcome is not None:
            return
        try:
            history = build_history(self.get_stats_rows(), datetime.now().date())
            goal = (f"Сегодня нужно: {self.daily_limit_minutes} мин при скорости не ниже "
                    f"{self.min_speed:.1f} км/ч")
            self.welcome = WelcomeWindow(self.root, history, goal, on_close=self._welcome_closed)
            self.welcome.show()
            logging.info("Показано приветствие с историей тренировок")
        except Exception as e:
            logging.error(f"Не удалось показать приветствие: {e}")
            self.welcome = None

    def _welcome_closed(self):
        self.welcome = None

    def close_welcome(self):
        self.welcome_pending = False
        if self.welcome is not None:
            self.welcome.close()

    # ==================== КРУЖОК-СПИДОМЕТР ====================

    @property
    def wheel_circumference(self):
        """Длина окружности колеса, м (из диаметра в дюймах в настройках)."""
        return wheel_circumference_m(self.wheel_inches)

    def enter_gauge_mode(self):
        """Сворачивает окно в круглый спидометр в левом нижнем углу видимой области."""
        size = clamp_size(self.gauge_size)
        self.close_welcome()
        if self.window_current_mode == "gauge":
            if self.gauge_canvas.winfo_reqwidth() != size:
                self.gauge_canvas.config(width=size, height=size)
                x, y = clamp_to_screen(self.root.winfo_x(), self.root.winfo_y(), size, size)
                self.root.geometry(f"{size}x{size}+{x}+{y}")
                self.gauge_view.invalidate()
            return

        self._saved_pos = (self.root.winfo_x(), self.root.winfo_y())
        self.title_bar.pack_forget()
        self.main_frame.pack_forget()
        self.root.configure(bg=GAUGE_KEY)
        try:
            self.root.attributes('-transparentcolor', GAUGE_KEY)
        except tk.TclError:
            logging.warning("Прозрачный фон окна недоступен")
        self.gauge_canvas.config(width=size, height=size)
        self.gauge_canvas.pack()
        x, y = bottom_left_position(size)
        self.root.geometry(f"{size}x{size}+{x}+{y}")
        self.window_current_mode = "gauge"
        self.gauge_view.invalidate()
        self.root.update_idletasks()
        logging.debug("Окно свёрнуто в кружок-спидометр")

    def leave_gauge_mode(self):
        if self.window_current_mode != "gauge":
            return
        self.gauge_canvas.pack_forget()
        try:
            self.root.attributes('-transparentcolor', '')
        except tk.TclError:
            pass
        self.root.configure(bg=self._root_default_bg)
        self.title_bar.pack(fill=tk.X, side=tk.TOP)
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        self.window_current_mode = None
        x, y = clamp_to_screen(*self._saved_pos, self.window_normal_width, self.window_normal_height)
        self.root.geometry(f"+{x}+{y}")

    def show_gauge(self, ok, time_since_movement):
        """Показывает и обновляет спидометр во время вращения педалей."""
        self.enter_gauge_mode()
        self.alert_window_start_time = None

        idle_time = max(0.0, time.time() - self.last_activity_time)
        lock_frac = min(1.0, idle_time / max(1, self.idle_timeout))
        moving = self.current_speed > 0 or time_since_movement < 3

        if ok or (not moving and time_since_movement < 10):
            self.stop_beep_warning()
        else:
            self.start_beep_warning()

        total = self.daily_limit_minutes * 60.0
        remaining = 1.0 - (self.daily_active_seconds / total) if total > 0 else 0.0
        idle_state = (not ok) and self.current_speed <= 0 and lock_frac < 0.02
        self.gauge_view.draw(
            clamp_size(self.gauge_size), self.current_speed, self.min_speed,
            ok, lock_frac, self.daily_active_seconds // 60, remaining, idle_state
        )

    def hide_for_today(self):
        """Прячет приложение до следующего дня (или до изменения норматива)."""
        if self.window_current_mode == "hidden":
            return
        self.leave_gauge_mode()
        self.window_current_mode = "hidden"
        self.root.attributes('-alpha', 0.0)
        self.root.geometry("1x1+0+0")
        logging.info("Норматив выполнен: приложение скрыто до следующего дня")

    def show_from_hidden(self):
        if self.window_current_mode != "hidden":
            return
        self.root.attributes('-alpha', 1.0)
        self.window_current_mode = None
        self.set_window_normal()
        self.root.geometry("+0+0")
        logging.info("Приложение снова показано")

    def on_limit_reached(self):
        """Норматив выполнен: фанфары, фейерверк, затем приложение скрывается."""
        if self.celebration is not None or self.window_current_mode == "hidden":
            return
        self.stop_beep_warning()
        try:
            self.celebration = Celebration(self.root, on_done=self._celebration_done)
            self.celebration.start()
        except Exception as e:
            logging.error(f"Не удалось показать праздничную анимацию: {e}")
            self._celebration_done()

    def _celebration_done(self):
        self.celebration = None
        self.hide_for_today()

    # ==================== АВТООБНОВЛЕНИЕ ====================

    def start_auto_update(self):
        if not updater.can_self_update(GITHUB_REPO):
            logging.info("Автообновление отключено (нет репозитория или запуск не из exe)")
            return
        threading.Thread(target=self._update_loop, daemon=True).start()

    def _update_loop(self):
        def wait(seconds):
            end = time.time() + seconds
            while self.is_running and time.time() < end:
                time.sleep(5)

        wait(60)
        while self.is_running:
            try:
                staged = updater.check_and_stage(GITHUB_REPO, __version__)
                if staged:
                    self.root.after(0, self._try_apply_update, staged)
                    return
            except Exception as e:
                logging.warning(f"Проверка обновления не удалась: {e}")
            wait(6 * 3600)

    def _try_apply_update(self, staged):
        """Заменяет программу, только когда педали не крутят."""
        if not self.is_running:
            return
        if (time.time() - self.last_leg_movement_time < 120 or self.window_current_mode == "gauge"
                or self.celebration is not None):
            self.root.after(60000, self._try_apply_update, staged)
            return
        try:
            updater.apply_staged(staged)
        except Exception as e:
            logging.error(f"Не удалось применить обновление: {e}")
            return
        self.safe_shutdown()

    def exit_on_limit(self):
        if messagebox.askyesno("Завершение", "Вы действительно хотите выйти из программы?"):
            self.safe_shutdown()

    def apply_limit_reached_style(self):
        if self.limit_style_applied:
            return
        self.limit_style_applied = True
        green_bg = "#90EE90"
        try:
            self.main_frame.config(bg=green_bg)
            for child in self.main_frame.winfo_children():
                try:
                    child.config(bg=green_bg)
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"apply_limit_reached_style: {e}")
        try:
            self.close_button.pack(side=tk.RIGHT, padx=5)
        except Exception:
            pass

    def remove_limit_reached_style(self):
        if not self.limit_style_applied:
            return
        self.limit_style_applied = False
        normal_bg = "#f0f0f0"
        try:
            self.main_frame.config(bg=normal_bg)
            for child in self.main_frame.winfo_children():
                try:
                    child.config(bg=normal_bg)
                except Exception:
                    pass
        except Exception as e:
            logging.warning(f"remove_limit_reached_style: {e}")
        try:
            self.close_button.pack_forget()
        except Exception:
            pass

    def update_status(self, text, color):
        try:
            self.status_label.config(text=text)
            self.status_canvas.itemconfig(self.status_indicator, fill=color)
        except Exception:
            pass

    def schedule_gui_update(self):
        if not self.is_running:
            return

        if self.gui_after_id is not None:
            try:
                self.root.after_cancel(self.gui_after_id)
            except Exception:
                pass
            self.gui_after_id = None

        self.gui_after_id = self.root.after(100, self.update_gui)

    def update_gui(self):
        self.gui_after_id = None

        if not self.is_running:
            return

        # Смена даты должна обрабатываться даже без данных от Arduino.
        self.check_date_change()

        # Норматив выполнен: идёт праздник, затем приложение скрыто до завтра.
        if self.celebration is not None:
            self.schedule_gui_update()
            return
        if self.celebration_due:
            self.celebration_due = False
            self.on_limit_reached()
            self.schedule_gui_update()
            return
        if self.is_limit_reached_today and self.is_today_scheduled():
            self.stop_beep_warning()
            self.hide_for_today()
            self.schedule_gui_update()
            return
        if self.window_current_mode == "hidden":
            self.show_from_hidden()

        if self.is_manual_pause_active():
            remaining = self.get_manual_pause_remaining_seconds()
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            seconds = remaining % 60
            self.stop_beep_warning()
            self.alert_window_start_time = None
            self.set_window_normal()
            self.speed_label.config(text=f"{self.current_speed:.1f}", foreground="green")
            self.load_label.config(text=f"Нагр: {self.current_load} / мин: {self.min_load}")
            self.pulses_label.config(text=f"{self.session_pulses}")
            if self.daily_limit_minutes > 0:
                progress = min(100.0, (self.daily_active_seconds / (self.daily_limit_minutes * 60.0)) * 100.0)
            else:
                progress = 0.0
            self.daily_progress_label.config(text=f"День: {progress:.0f}%")
            self.break_timer_label.config(text=f"Перерыв: {hours:02d}:{minutes:02d}:{seconds:02d}", fg="blue")
            self.beep_timer_label.config(text="Ручной отдых", fg="green")
            self.status_label.config(text="Пауза")
            self.status_canvas.itemconfig(self.status_indicator, fill="green")
            self.schedule_gui_update()
            return

        # В выходной по расписанию контроллер остаётся запущенным, но не требует
        # вращения педалей, не подаёт сигнал и не блокирует Windows.
        if not self.is_today_scheduled():
            self.stop_beep_warning()
            self.alert_window_start_time = None
            self.set_window_normal()
            self.speed_label.config(text=f"{self.current_speed:.1f}", foreground="green")
            self.load_label.config(text=f"Нагр: {self.current_load} / мин: {self.min_load}")
            self.pulses_label.config(text=f"{self.session_pulses}")
            self.daily_progress_label.config(text="Сегодня: выходной")
            self.break_timer_label.config(text="По расписанию нагрузка не требуется", fg="green")
            self.beep_timer_label.config(text="Отдых", fg="green")
            self.status_label.config(text="Выходной")
            self.status_canvas.itemconfig(self.status_indicator, fill="green")
            self.schedule_gui_update()
            return

        # Обновляем состояние режима ожидания
        self.update_break_mode()

        # ========== РЕЖИМ ОЖИДАНИЯ (без предупреждения) - сворачиваем окно и ничего не контролируем ==========
        if self.is_break_mode and not self.is_warning_mode:
            elapsed = time.time() - self.break_start_time
            remaining = max(0, self.break_minutes * 60 - elapsed)
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            
            # Обновляем только информационные метки
            self.break_timer_label.config(text=f"Режим отдыха: {minutes:02d}:{seconds:02d}", fg="blue")
            self.speed_label.config(text=f"{self.current_speed:.1f}")
            self.load_label.config(text=f"Нагр: {self.current_load} / мин: {self.min_load}")
            self.pulses_label.config(text=f"{self.session_pulses}")
            
            # Обновляем прогресс дня
            if self.daily_limit_minutes > 0:
                progress = min(100.0, (self.daily_active_seconds / (self.daily_limit_minutes * 60.0)) * 100.0)
            else:
                progress = 0.0
            self.daily_progress_label.config(text=f"День: {progress:.0f}%")
            
            # Сворачиваем окно только один раз
            if not hasattr(self, '_minimized_done'):
                self.minimize_window()
                self._minimized_done = True
            
            self.schedule_gui_update()
            return
        
        # Если вышли из режима ожидания - сбрасываем флаг сворачивания
        if hasattr(self, '_minimized_done'):
            delattr(self, '_minimized_done')
        
        # ========== РЕЖИМ ПРЕДУПРЕЖДЕНИЯ (за 5 минут до активации) ==========
        if self.is_break_mode and self.is_warning_mode:
            warning_remaining = max(
                0,
                self.break_minutes * 60 - (time.time() - self.break_start_time)
            )
            minutes = int(warning_remaining // 60)
            seconds = int(warning_remaining % 60)
            self.break_timer_label.config(
                text=f"⚠️ ДО АКТИВАЦИИ: {minutes:02d}:{seconds:02d} ⚠️",
                fg="orange"
            )
            # Разворачиваем окно для предупреждения
            if hasattr(self, '_minimized_done'):
                delattr(self, '_minimized_done')
            if self.window_current_mode == "minimized":
                self.restore_window()
            # Пока идёт обратный отсчёт, активный контроль ещё не выполняется.
            self.speed_label.config(text=f"{self.current_speed:.1f}", foreground="green")
            self.load_label.config(text=f"Нагр: {self.current_load} / мин: {self.min_load}")
            self.pulses_label.config(text=f"{self.session_pulses}")
            self.stop_beep_warning()
            self.beep_timer_label.config(text="Контроль пока не активен", fg="green")
            self.schedule_gui_update()
            return
        else:
            self.break_timer_label.config(text="")
        
        # ========== БЛОКИРОВКА ЭКРАНА ==========
        if self.is_locked:
            try:
                self.set_window_normal()
                self.speed_label.config(text=f"{self.current_speed:.1f}")
                self.pulses_label.config(text=f"{self.session_pulses}")
                self.load_label.config(text=f"Нагр: {self.current_load} / мин: {self.min_load}")
                if self.daily_limit_minutes > 0:
                    progress = min(100.0, (self.daily_active_seconds / (self.daily_limit_minutes * 60.0)) * 100.0)
                else:
                    progress = 0.0
                self.daily_progress_label.config(text=f"День: {progress:.0f}%")
                self.speed_label.config(foreground="green")
                self.stop_beep_warning()
                self.beep_timer_label.config(text="Экран заблокирован", fg="gray")
            except Exception:
                pass
            self.schedule_gui_update()
            return
        
        # ========== ОБНОВЛЕНИЕ ОСНОВНЫХ МЕТОК ==========
        try:
            self.speed_label.config(text=f"{self.current_speed:.1f}")
            self.pulses_label.config(text=f"{self.session_pulses}")
            self.load_label.config(text=f"Нагр: {self.current_load} / мин: {self.min_load}")
            
            if self.daily_limit_minutes > 0:
                progress = min(100.0, (self.daily_active_seconds / (self.daily_limit_minutes * 60.0)) * 100.0)
            else:
                progress = 0.0
            self.daily_progress_label.config(text=f"День: {progress:.0f}%")
            
            # Стиль при достижении лимита
            if self.is_limit_reached_today:
                if not self.limit_style_applied:
                    self.apply_limit_reached_style()
                self.set_window_normal()
                self.speed_label.config(foreground="green")
                self.stop_beep_warning()
                self.beep_timer_label.config(text="Лимит дня", fg="green")
                self.status_label.config(text="Отдых")
                self.status_canvas.itemconfig(self.status_indicator, fill="green")
                self.schedule_gui_update()
                return
            
            # Убираем стиль лимита, если он больше не нужен
            if self.limit_style_applied and not self.is_limit_reached_today:
                self.remove_limit_reached_style()
            
            # Проверка на блокировку (только если не в режиме ожидания)
            if (not self.is_limit_reached_today and not self.is_password_window_open
                    and not self.is_break_mode and not self.is_manual_pause_active()):
                idle_time = time.time() - self.last_activity_time
                if idle_time >= self.idle_timeout:
                    self.lock_computer()
                    return
            else:
                if self.is_limit_reached_today:
                    self.status_label.config(text="Отдых")
                    self.status_canvas.itemconfig(self.status_indicator, fill="green")
            
            # Приветствие с историей - до начала вращения
            if (self.welcome_pending and self.current_speed <= 0
                    and time.time() - self.last_leg_movement_time >= 3):
                self.show_welcome()

            # ========== ПРОВЕРКИ ДЛЯ АКТИВНОГО РЕЖИМА ==========
            current_time = time.time()
            speed_ok = self.current_speed >= self.min_speed
            load_ok = self.current_load >= self.min_load
            has_connection = (current_time - self.last_data_time) < 3
            time_since_movement = current_time - self.last_leg_movement_time
            time_since_start = current_time - self.program_start_time
            
            # Лимит дня
            if self.is_limit_reached_today:
                self.set_window_normal()
                self.speed_label.config(foreground="green")
                self.stop_beep_warning()
                self.beep_timer_label.config(text="Лимит дня", fg="green")
                if not self.limit_style_applied:
                    self.apply_limit_reached_style()
                self.schedule_gui_update()
                return
            
            # Нет связи с тренажером
            if not has_connection:
                if time_since_start < 10:
                    self.set_window_normal()
                    self.speed_label.config(foreground="green")
                    self.stop_beep_warning()
                    remaining = 10 - int(time_since_start)
                    self.beep_timer_label.config(text=f"Подключи тренажёр ({remaining} сек)", fg="green")
                else:
                    self.set_window_alert()
                    self.speed_label.config(foreground="orange")
                    self.start_beep_warning()
                    self.beep_timer_label.config(text="⚠ Нет связи с тренажёром!", fg="orange")
                self.schedule_gui_update()
                self.alert_window_start_time = None
                return
            
            # Педали крутятся - сворачиваемся в кружок-спидометр
            if (self.current_speed > 0 or time_since_movement < 3
                    or self.window_current_mode == "gauge"):
                self.show_gauge(speed_ok and load_ok, time_since_movement)
                self.schedule_gui_update()
                return

            # Скорость и нагрузка в норме
            if speed_ok and load_ok:
                self.set_window_normal()
                self.speed_label.config(foreground="green")
                self.stop_beep_warning()
                self.beep_timer_label.config(text="✅ Молодец!", fg="green")
                self.schedule_gui_update()
                return
            
            # Недостаточная скорость или нагрузка (педали крутятся)
            if self.current_speed > 0 or self.current_load > 0:
                # Запускаем звук если еще не запущен
                if not self.is_beeping:
                    self.start_beep_warning()
                    self.alert_window_start_time = time.time()
                
                # Проверяем, прошло ли enough времени для показа большого окна
                if self.alert_window_start_time and (time.time() - self.alert_window_start_time) >= ALERT_WINDOW_DELAY_SEC:
                    self.set_window_alert()
                else:
                    # Используем нормальное окно первые N секунд
                    if self.window_current_mode != "normal":
                        self.set_window_normal()
                
                self.speed_label.config(foreground="orange")
                if self.current_speed < self.min_speed:
                    self.beep_timer_label.config(text=f"⚠ Увеличь скорость до {self.min_speed:.1f} км/ч", fg="orange")
                else:
                    self.beep_timer_label.config(text=f"⚠ Увеличь нагрузку до {self.min_load}", fg="orange")
                self.schedule_gui_update()
                return


                if self.current_speed < self.min_speed:
                    self.beep_timer_label.config(text=f"⚠ Увеличь скорость до {self.min_speed:.1f} км/ч", fg="orange")
                else:
                    self.beep_timer_label.config(text=f"⚠ Увеличь нагрузку до {self.min_load}", fg="orange")
                self.schedule_gui_update()
                return
            
            # Педали не крутятся - предупреждение о блокировке
            if time_since_movement < 10:
                self.alert_window_start_time = None
                self.set_window_normal()
                self.speed_label.config(foreground="green")
                self.stop_beep_warning()
                remaining = 10 - int(time_since_movement)
                self.beep_timer_label.config(text=f"Начни крутить через {remaining} сек", fg="green")
            elif time_since_movement < self.idle_timeout:
                self.alert_window_start_time = None
                self.set_window_alert()
                self.speed_label.config(foreground="orange")
                self.start_beep_warning()
                remaining_block = int(self.idle_timeout - time_since_movement)
                self.beep_timer_label.config(text=f"⚠ Блокировка через {remaining_block} сек!", fg="orange")
            else:
                self.alert_window_start_time = None
                self.set_window_alert()
                self.speed_label.config(foreground="red")
                self.start_beep_warning()
                self.beep_timer_label.config(text="⛔ БЛОКИРОВКА!", fg="red")
                
        except Exception as e:
            logging.warning(f"Ошибка update_gui: {e}")
        
        self.schedule_gui_update()

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        if self.x is not None and self.y is not None:
            deltax = event.x - self.x
            deltay = event.y - self.y
            x = self.root.winfo_x() + deltax
            y = self.root.winfo_y() + deltay
            if self.window_current_mode == "gauge":
                x, y = clamp_to_screen(x, y, self.root.winfo_width(), self.root.winfo_height())
            self.root.geometry(f"+{x}+{y}")

    def stop_move(self, event):
        self.x = None
        self.y = None

    # ==================== НАСТРОЙКИ И ИСТОРИЯ ====================

    def show_settings_menu(self, event=None):
        password = simpledialog.askstring(
            "Доступ к настройкам",
            "Введите пароль:",
            parent=self.root,
            show='*'
        )

        if password and self.verify_admin_password(password):
            self.show_settings_dialog()
        elif password is not None:
            messagebox.showerror("Ошибка", "Неверный пароль!", parent=self.root)

    def show_history_window(self, parent=None):
        history_window = tk.Toplevel(parent if parent else self.root)
        history_window.title("История активности")
        history_window.geometry("700x420")
        history_window.resizable(True, True)
        history_window.transient(parent if parent else self.root)
        history_window.grab_set()

        main_frame = tk.Frame(history_window, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            main_frame,
            text="История активности по дням",
            font=("Arial", 12, "bold")
        ).pack(anchor="w", pady=(0, 10))

        columns = ("date", "seconds", "minutes", "limit_minutes", "progress", "limit_reached")
        tree = ttk.Treeview(main_frame, columns=columns, show="headings", height=14)

        tree.heading("date", text="Дата")
        tree.heading("seconds", text="Секунд")
        tree.heading("minutes", text="Минут")
        tree.heading("limit_minutes", text="Лимит (мин)")
        tree.heading("progress", text="% выполнения")
        tree.heading("limit_reached", text="Режим отдых")

        tree.column("date", width=110, anchor="center")
        tree.column("seconds", width=90, anchor="e")
        tree.column("minutes", width=90, anchor="e")
        tree.column("limit_minutes", width=100, anchor="e")
        tree.column("progress", width=100, anchor="e")
        tree.column("limit_reached", width=110, anchor="center")

        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        rows = self.get_stats_rows()
        rows.sort(key=lambda r: r.get('date', ''), reverse=True)

        total_seconds = 0.0
        total_days = 0
        reached_days = 0

        for row in rows:
            date = row.get('date', '')
            try:
                seconds = float(row.get('seconds', 0) or 0)
            except Exception:
                seconds = 0.0

            try:
                limit_minutes = int(float(row.get('limit_minutes', 0) or 0))
            except Exception:
                limit_minutes = 0

            limit_reached = str(row.get('limit_reached', '0')).strip().lower() in ('1', 'true', 'yes', 'да')
            progress = (seconds / (limit_minutes * 60) * 100) if limit_minutes > 0 else 0.0

            total_seconds += seconds
            total_days += 1
            if limit_reached:
                reached_days += 1

            tree.insert(
                "",
                tk.END,
                values=(
                    date,
                    f"{seconds:.1f}",
                    f"{seconds / 60:.1f}",
                    limit_minutes,
                    f"{min(progress, 100):.1f}%",
                    "Да" if limit_reached else "Нет"
                )
            )

        summary_text = (
            f"Всего дней в истории: {total_days}    "
            f"Общее время: {total_seconds / 60:.1f} мин    "
            f"Дней с выполненным лимитом: {reached_days}"
        )

        tk.Label(
            history_window,
            text=summary_text,
            font=("Arial", 10, "bold"),
            anchor="w",
            padx=10,
            pady=8
        ).pack(fill=tk.X)

    def show_settings_dialog(self):
        self.is_settings_open = True
        
        settings_window = tk.Toplevel(self.root)
        settings_window.title("Настройки контроллера")

        # Размер окна адаптируется к реальной высоте экрана и DPI Windows.
        screen_width = settings_window.winfo_screenwidth()
        screen_height = settings_window.winfo_screenheight()
        settings_width = max(560, min(680, screen_width - 80))
        settings_height = max(520, min(760, int(screen_height * 0.85)))
        settings_window.geometry(f"{settings_width}x{settings_height}")
        settings_window.minsize(min(560, settings_width), min(520, settings_height))
        settings_window.resizable(True, True)

        settings_window.transient(self.root)
        settings_window.grab_set()
        settings_window.attributes('-topmost', True)

        bg_color = "#f0f0f0"

        main_frame = tk.Frame(settings_window, bg=bg_color, padx=12, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Панель действий всегда остаётся видимой внизу окна.
        button_frame = tk.Frame(main_frame, bg=bg_color)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))

        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(
            fill=tk.X,
            side=tk.BOTTOM,
            pady=(8, 0)
        )

        # Обычный Frame не прокручивается, поэтому помещаем содержимое внутрь
        # Canvas и связываем его с вертикальной полосой прокрутки.
        scroll_container = tk.Frame(main_frame, bg=bg_color)
        scroll_container.pack(fill=tk.BOTH, expand=True)

        settings_canvas = tk.Canvas(
            scroll_container,
            bg=bg_color,
            highlightthickness=0,
            borderwidth=0
        )
        settings_scrollbar = ttk.Scrollbar(
            scroll_container,
            orient=tk.VERTICAL,
            command=settings_canvas.yview
        )
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)

        settings_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        settings_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        content_frame = tk.Frame(settings_canvas, bg=bg_color)
        content_window_id = settings_canvas.create_window(
            (0, 0),
            window=content_frame,
            anchor="nw"
        )

        def update_scroll_region(event=None):
            settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))

        def fit_content_width(event):
            settings_canvas.itemconfigure(content_window_id, width=event.width)

        def scroll_with_mouse(event):
            if event.delta == 0:
                return "break"
            direction = -1 if event.delta > 0 else 1
            settings_canvas.yview_scroll(direction, "units")
            return "break"

        content_frame.bind("<Configure>", update_scroll_region)
        settings_canvas.bind("<Configure>", fit_content_width)
        settings_window.bind("<MouseWheel>", scroll_with_mouse)
        settings_window.bind("<Prior>", lambda event: settings_canvas.yview_scroll(-1, "pages"))
        settings_window.bind("<Next>", lambda event: settings_canvas.yview_scroll(1, "pages"))
        settings_window.bind("<Home>", lambda event: settings_canvas.yview_moveto(0.0))
        settings_window.bind("<End>", lambda event: settings_canvas.yview_moveto(1.0))

        tk.Label(
            content_frame,
            text="⚙️ НАСТРОЙКИ КОНТРОЛЛЕРА ⚙️",
            font=("Arial", 12, "bold"),
            bg=bg_color,
            fg="#333333"
        ).pack(pady=(0, 10))

        # === СВОБОДНОЕ ВРЕМЯ ДО НАЧАЛА НАГРУЗКИ ===
        break_frame = tk.Frame(content_frame, bg=bg_color, relief="groove", bd=2, padx=15, pady=15)
        break_frame.pack(fill=tk.X, pady=8)

        tk.Label(
            break_frame,
            text="Свободное время до нагрузки",
            font=("Arial", 12, "bold"),
            bg=bg_color
        ).pack(anchor="w", pady=(0, 10))

        break_minutes_frame = tk.Frame(break_frame, bg=bg_color)
        break_minutes_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            break_minutes_frame,
            text="Свободное время после входа:",
            font=("Arial", 11),
            bg=bg_color,
            width=27,
            anchor="w"
        ).pack(side=tk.LEFT)

        break_minutes_var = tk.StringVar(value=str(self.break_minutes))
        break_minutes_entry = tk.Entry(break_minutes_frame, textvariable=break_minutes_var, font=("Arial", 11), width=10)
        break_minutes_entry.pack(side=tk.LEFT)
        tk.Label(break_minutes_frame, text="минут", font=("Arial", 11), bg=bg_color).pack(side=tk.LEFT, padx=5)
        tk.Label(break_minutes_frame, text=f"({MIN_BREAK_MINUTES}-{MAX_BREAK_MINUTES})", font=("Arial", 9), bg=bg_color, fg="gray").pack(side=tk.LEFT, padx=5)

        # Пояснение
        tk.Label(
            break_frame,
            text="В это время можно пользоваться компьютером без педалей. "
                 f"За {WARNING_MINUTES_BEFORE_ACTIVE} минут появится предупреждение.\n"
                 "Новый отсчет начинается в первый запуск каждого нового дня.",
            font=("Arial", 8),
            bg=bg_color,
            fg="gray",
            justify=tk.LEFT
        ).pack(anchor="w", pady=(5, 0))

        # === ПАРАМЕТРЫ БЛОКИРОВКИ ===
        lock_frame = tk.Frame(content_frame, bg=bg_color, relief="groove", bd=2, padx=15, pady=15)
        lock_frame.pack(fill=tk.X, pady=8)

        tk.Label(
            lock_frame,
            text="Параметры блокировки",
            font=("Arial", 9, "bold"),
            bg=bg_color
        ).pack(anchor="w", pady=(0, 5))

        speed_frame = tk.Frame(lock_frame, bg=bg_color)
        speed_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            speed_frame,
            text="Минимальная скорость:",
            font=("Arial", 9),
            bg=bg_color,
            width=22,
            anchor="w"
        ).pack(side=tk.LEFT)

        speed_var = tk.StringVar(value=str(self.min_speed))
        speed_entry = tk.Entry(speed_frame, textvariable=speed_var, font=("Arial", 11), width=10)
        speed_entry.pack(side=tk.LEFT)
        tk.Label(speed_frame, text="км/ч", font=("Arial", 11), bg=bg_color).pack(side=tk.LEFT, padx=5)

        load_frame = tk.Frame(lock_frame, bg=bg_color)
        load_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            load_frame,
            text="Минимальная нагрузка:",
            font=("Arial", 9),
            bg=bg_color,
            width=22,
            anchor="w"
        ).pack(side=tk.LEFT)

        load_var = tk.StringVar(value=str(self.min_load))
        load_entry = tk.Entry(load_frame, textvariable=load_var, font=("Arial", 11), width=10)
        load_entry.pack(side=tk.LEFT)
        tk.Label(load_frame, text="(0-8)", font=("Arial", 11), bg=bg_color).pack(side=tk.LEFT, padx=5)

        timeout_frame = tk.Frame(lock_frame, bg=bg_color)
        timeout_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            timeout_frame,
            text="Время до блокировки:",
            font=("Arial", 9),
            bg=bg_color,
            width=22,
            anchor="w"
        ).pack(side=tk.LEFT)

        timeout_var = tk.StringVar(value=str(self.idle_timeout))
        timeout_entry = tk.Entry(timeout_frame, textvariable=timeout_var, font=("Arial", 11), width=10)
        timeout_entry.pack(side=tk.LEFT)
        tk.Label(timeout_frame, text="сек", font=("Arial", 11), bg=bg_color).pack(side=tk.LEFT, padx=5)

        gauge_frame = tk.Frame(lock_frame, bg=bg_color)
        gauge_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            gauge_frame,
            text="Размер кружка-спидометра:",
            font=("Arial", 9),
            bg=bg_color,
            width=22,
            anchor="w"
        ).pack(side=tk.LEFT)

        gauge_size_var = tk.StringVar(value=str(self.gauge_size))
        tk.Entry(gauge_frame, textvariable=gauge_size_var, font=("Arial", 11), width=10).pack(side=tk.LEFT)
        tk.Label(gauge_frame, text=f"px ({GAUGE_MIN_SIZE}-{GAUGE_MAX_SIZE})", font=("Arial", 11), bg=bg_color).pack(side=tk.LEFT, padx=5)

        wheel_frame = tk.Frame(lock_frame, bg=bg_color)
        wheel_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            wheel_frame,
            text="Диаметр колеса:",
            font=("Arial", 9),
            bg=bg_color,
            width=22,
            anchor="w"
        ).pack(side=tk.LEFT)

        wheel_var = tk.StringVar(value=f"{self.wheel_inches:g}")
        tk.Entry(wheel_frame, textvariable=wheel_var, font=("Arial", 11), width=10).pack(side=tk.LEFT)
        tk.Label(wheel_frame, text=f"дюймов ({WHEEL_MIN_INCH}-{WHEEL_MAX_INCH}), 24 = 61 см", font=("Arial", 11), bg=bg_color).pack(side=tk.LEFT, padx=5)

        # === ДНЕВНОЙ ЛИМИТ ===
        daily_frame = tk.Frame(content_frame, bg=bg_color, relief="groove", bd=2, padx=15, pady=15)
        daily_frame.pack(fill=tk.X, pady=8)

        tk.Label(
            daily_frame,
            text="Дневной лимит активности",
            font=("Arial", 9, "bold"),
            bg=bg_color
        ).pack(anchor="w", pady=(0, 5))

        limit_frame = tk.Frame(daily_frame, bg=bg_color)
        limit_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            limit_frame,
            text="Лимит активности в день:",
            font=("Arial", 9),
            bg=bg_color,
            width=22,
            anchor="w"
        ).pack(side=tk.LEFT)

        limit_var = tk.StringVar(value=str(self.daily_limit_minutes))
        limit_entry = tk.Entry(limit_frame, textvariable=limit_var, font=("Arial", 11), width=10)
        limit_entry.pack(side=tk.LEFT)
        tk.Label(limit_frame, text="мин", font=("Arial", 11), bg=bg_color).pack(side=tk.LEFT, padx=5)

        if self.daily_limit_minutes > 0:
            progress = min(100, (self.daily_active_seconds / (self.daily_limit_minutes * 60.0)) * 100.0)
        else:
            progress = 0

        stats_text = f"Сегодня накоплено: {self.daily_active_seconds:.1f} сек ({progress:.1f}%)"
        if self.is_limit_reached_today:
            stats_text += "  ✓ ЛИМИТ ДОСТИГНУТ"

        today_stats = tk.Frame(daily_frame, bg=bg_color)
        today_stats.pack(fill=tk.X, pady=10)

        tk.Label(
            today_stats,
            text=stats_text,
            font=("Arial", 10),
            bg=bg_color,
            fg="green" if self.is_limit_reached_today else "black",
            justify=tk.LEFT
        ).pack(anchor="w")

        # === РАСПИСАНИЕ ПО ДНЯМ НЕДЕЛИ ===
        schedule_frame = tk.Frame(content_frame, bg=bg_color, relief="groove", bd=2, padx=15, pady=12)
        schedule_frame.pack(fill=tk.X, pady=8)

        tk.Label(
            schedule_frame,
            text="Расписание нагрузки",
            font=("Arial", 9, "bold"),
            bg=bg_color
        ).pack(anchor="w", pady=(0, 5))

        tk.Label(
            schedule_frame,
            text="Отметьте дни, в которые пользователь должен выполнить дневную норму:",
            font=("Arial", 8),
            bg=bg_color,
            fg="gray"
        ).pack(anchor="w", pady=(0, 5))

        schedule_grid = tk.Frame(schedule_frame, bg=bg_color)
        schedule_grid.pack(fill=tk.X)
        schedule_vars = {}
        for index, (key, name) in enumerate(WEEKDAYS):
            variable = tk.BooleanVar(value=self.schedule.get(key, True))
            schedule_vars[key] = variable
            tk.Checkbutton(
                schedule_grid,
                text=name,
                variable=variable,
                bg=bg_color,
                activebackground=bg_color,
                anchor="w"
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 35), pady=2)

        # === ТЕКУЩАЯ СТАТИСТИКА ===
        info_frame = tk.Frame(content_frame, bg="#e0e0e0", relief="sunken", bd=2, padx=15, pady=15)
        info_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            info_frame,
            text="📊 Текущая статистика",
            font=("Arial", 9, "bold"),
            bg="#e0e0e0"
        ).pack(anchor="w", pady=(0, 5))

        info_text = (
            f"• Скорость вращения: {self.current_speed:.1f} км/ч\n"
            f"• Обороты в минуту: {self.current_rpm:.0f} об/мин\n"
            f"• Текущая нагрузка: {self.current_load} (0-8)\n"
            f"• Всего импульсов: {self.session_pulses}\n"
            f"• Осталось свободного времени: {max(0, int(self.break_minutes * 60 - (time.time() - self.break_start_time))) if self.is_break_mode else 0} сек\n"
            f"• Ручной перерыв: {'активен' if self.is_manual_pause_active() else 'нет'}\n"
            f"• Статус Arduino: {'Подключен' if self.arduino and self.arduino.is_open else 'Не подключен'}\n"
            f"• Режим: {'ПАУЗА' if self.is_manual_pause_active() else ('ОТДЫХ' if self.is_limit_reached_today else ('ОЖИДАНИЕ' if self.is_break_mode else 'АКТИВНЫЙ'))}"
        )

        tk.Label(
            info_frame,
            text=info_text,
            justify=tk.LEFT,
            font=("Arial", 10),
            bg="#e0e0e0"
        ).pack(anchor="w")

        def on_close():
            self.is_settings_open = False
            settings_window.destroy()

        settings_window.protocol("WM_DELETE_WINDOW", on_close)

        def save_all_settings():
            try:
                new_speed = float(speed_var.get().replace(',', '.'))
                new_timeout = int(timeout_var.get())
                new_limit = int(limit_var.get())
                new_load = int(load_var.get())
                new_break_minutes = int(break_minutes_var.get())
                new_gauge_size = int(gauge_size_var.get())
                new_wheel = float(wheel_var.get().replace(',', '.'))
                new_schedule = {key: variable.get() for key, variable in schedule_vars.items()}

                if new_speed < 0:
                    messagebox.showerror("Ошибка", "Скорость не может быть отрицательной!", parent=settings_window)
                    return

                if new_timeout < MIN_IDLE_TIMEOUT_SEC:
                    messagebox.showerror(
                        "Ошибка",
                        f"Время должно быть не менее {MIN_IDLE_TIMEOUT_SEC} секунд!",
                        parent=settings_window
                    )
                    return

                if new_limit < MIN_DAILY_LIMIT_MIN:
                    messagebox.showerror(
                        "Ошибка",
                        f"Лимит должен быть не менее {MIN_DAILY_LIMIT_MIN} минуты!",
                        parent=settings_window
                    )
                    return

                if new_load < 0 or new_load > 8:
                    messagebox.showerror(
                        "Ошибка",
                        "Нагрузка должна быть в диапазоне 0..8!",
                        parent=settings_window
                    )
                    return

                if new_break_minutes < MIN_BREAK_MINUTES or new_break_minutes > MAX_BREAK_MINUTES:
                    messagebox.showerror(
                        "Ошибка",
                        f"Свободное время должно быть в диапазоне {MIN_BREAK_MINUTES}-{MAX_BREAK_MINUTES} минут!",
                        parent=settings_window
                    )
                    return

                if new_gauge_size < GAUGE_MIN_SIZE or new_gauge_size > GAUGE_MAX_SIZE:
                    messagebox.showerror(
                        "Ошибка",
                        f"Размер кружка должен быть в диапазоне {GAUGE_MIN_SIZE}-{GAUGE_MAX_SIZE} px!",
                        parent=settings_window
                    )
                    return

                if new_wheel < WHEEL_MIN_INCH or new_wheel > WHEEL_MAX_INCH:
                    messagebox.showerror(
                        "Ошибка",
                        f"Диаметр колеса должен быть в диапазоне {WHEEL_MIN_INCH}-{WHEEL_MAX_INCH} дюймов!",
                        parent=settings_window
                    )
                    return

                old_limit_reached = self.is_limit_reached_today
                self.gauge_size = new_gauge_size
                self.wheel_inches = new_wheel

                self.min_speed = new_speed
                self.idle_timeout = new_timeout
                self.daily_limit_minutes = new_limit
                self.min_load = new_load
                self.schedule = new_schedule
                
                # Если свободное время изменилось, запускаем новый отсчет текущего дня.
                if new_break_minutes != self.break_minutes:
                    self.break_minutes = new_break_minutes
                    self.start_break_mode_for_today(
                        reason=f"изменено свободное время на {new_break_minutes} мин",
                        save_state=False
                    )
                    logging.info(f"Свободное время изменено на {new_break_minutes} мин, таймер сброшен")

                self.min_speed_label.config(text=f"мин: {self.min_speed:.1f}")
                self.load_label.config(text=f"Нагр: {self.current_load} / мин: {self.min_load}")

                if self.daily_active_seconds >= self.daily_limit_minutes * 60:
                    self.is_limit_reached_today = True
                else:
                    self.is_limit_reached_today = False

                self.save_config()
                self.save_daily_stats()
                self.save_break_state()

                if old_limit_reached and not self.is_limit_reached_today:
                    self.remove_limit_reached_style()

                messagebox.showinfo("Успех", "Настройки успешно сохранены!", parent=settings_window)

            except ValueError:
                messagebox.showerror("Ошибка", "Введите корректные числовые значения!", parent=settings_window)
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить настройки:\n{e}", parent=settings_window)

        def change_admin_password():
            current_password = simpledialog.askstring(
                "Смена пароля",
                "Введите текущий пароль:",
                parent=settings_window,
                show='*'
            )
            if current_password is None:
                return
            if not self.verify_admin_password(current_password):
                messagebox.showerror("Ошибка", "Текущий пароль введен неверно.", parent=settings_window)
                return

            new_password = simpledialog.askstring(
                "Смена пароля",
                "Введите новый пароль:",
                parent=settings_window,
                show='*'
            )
            if new_password is None:
                return
            new_password = new_password.strip()
            if len(new_password) < 6:
                messagebox.showerror("Ошибка", "Новый пароль должен содержать минимум 6 символов.", parent=settings_window)
                return

            repeat_password = simpledialog.askstring(
                "Смена пароля",
                "Повторите новый пароль:",
                parent=settings_window,
                show='*'
            )
            if repeat_password is None:
                return
            if new_password != repeat_password:
                messagebox.showerror("Ошибка", "Пароли не совпадают.", parent=settings_window)
                return

            self.set_admin_password(new_password)
            messagebox.showinfo("Успех", "Пароль администратора обновлен.", parent=settings_window)

        def show_manual_pause_dialog():
            dialog = tk.Toplevel(settings_window)
            dialog.title("Перерыв на отдых")
            dialog.transient(settings_window)
            dialog.grab_set()
            dialog.resizable(False, False)
            dialog.attributes('-topmost', True)

            frame = tk.Frame(dialog, padx=16, pady=16)
            frame.pack(fill=tk.BOTH, expand=True)

            tk.Label(
                frame,
                text="Выберите длительность перерыва:",
                font=("Arial", 10, "bold")
            ).pack(anchor="w", pady=(0, 10))

            pick_frame = tk.Frame(frame)
            pick_frame.pack(anchor="w", pady=(0, 10))

            hours_var = tk.StringVar(value="1")
            minutes_var = tk.StringVar(value="0")

            tk.Label(pick_frame, text="Часы:", font=("Arial", 10)).grid(row=0, column=0, sticky="w", padx=(0, 6))
            hours_spin = tk.Spinbox(pick_frame, from_=0, to=72, width=5, textvariable=hours_var, font=("Arial", 10))
            hours_spin.grid(row=0, column=1, padx=(0, 12))

            tk.Label(pick_frame, text="Минуты:", font=("Arial", 10)).grid(row=0, column=2, sticky="w", padx=(0, 6))
            minutes_spin = tk.Spinbox(pick_frame, from_=0, to=59, width=5, textvariable=minutes_var, font=("Arial", 10))
            minutes_spin.grid(row=0, column=3)

            tk.Label(
                frame,
                text="Во время перерыва программа остается запущенной, но не требует педали и не блокирует Windows.",
                justify=tk.LEFT,
                fg="gray",
                wraplength=300
            ).pack(anchor="w", pady=(0, 12))

            btns = tk.Frame(frame)
            btns.pack(fill=tk.X)

            def start_pause_and_close():
                try:
                    hours = int(hours_var.get())
                    minutes = int(minutes_var.get())
                except ValueError:
                    messagebox.showerror("Ошибка", "Введите корректные часы и минуты.", parent=dialog)
                    return

                if hours < 0 or hours > 72 or minutes < 0 or minutes > 59:
                    messagebox.showerror("Ошибка", "Часы: 0-72, минуты: 0-59.", parent=dialog)
                    return
                if hours == 0 and minutes == 0:
                    messagebox.showerror("Ошибка", "Длительность перерыва должна быть больше нуля.", parent=dialog)
                    return

                self.start_manual_pause(hours, minutes)
                dialog.destroy()
                messagebox.showinfo("Успех", f"Перерыв включен на {hours} ч {minutes} мин.", parent=settings_window)

            tk.Button(
                btns,
                text="Запустить перерыв",
                bg="#3F51B5",
                fg="white",
                font=("Arial", 9, "bold"),
                command=start_pause_and_close
            ).pack(side=tk.LEFT, padx=(0, 8))

            tk.Button(
                btns,
                text="Отмена",
                bg="#9E9E9E",
                fg="white",
                font=("Arial", 9, "bold"),
                command=dialog.destroy
            ).pack(side=tk.LEFT)

            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
            y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
            dialog.geometry(f"+{x}+{y}")

        tk.Button(
            button_frame,
            text="ПОКАЗАТЬ ИСТОРИЮ АКТИВНОСТИ",
            bg="#2196F3",
            fg="white",
            font=("Arial", 9, "bold"),
            command=lambda: self.show_history_window(settings_window)
        ).pack(fill=tk.X, pady=5)
        
        tk.Button(
            button_frame,
            text="СБРОСИТЬ ТАЙМЕР СВОБОДНОГО ВРЕМЕНИ",
            bg="#FF9800",
            fg="white",
            font=("Arial", 9, "bold"),
            command=lambda: self.reset_break_timer(settings_window)
        ).pack(fill=tk.X, pady=5)

        tk.Button(
            button_frame,
            text="СДЕЛАТЬ ПЕРЕРЫВ НА ОТДЫХ",
            bg="#3F51B5",
            fg="white",
            font=("Arial", 9, "bold"),
            command=show_manual_pause_dialog
        ).pack(fill=tk.X, pady=5)

        tk.Button(
            button_frame,
            text="ОТМЕНИТЬ ПЕРЕРЫВ",
            bg="#795548",
            fg="white",
            font=("Arial", 9, "bold"),
            command=lambda: (self.cancel_manual_pause(), messagebox.showinfo("Успех", "Ручной перерыв отменен.", parent=settings_window))
        ).pack(fill=tk.X, pady=5)

        tk.Button(
            button_frame,
            text="СМЕНИТЬ ПАРОЛЬ АДМИНИСТРАТОРА",
            bg="#607D8B",
            fg="white",
            font=("Arial", 9, "bold"),
            command=change_admin_password
        ).pack(fill=tk.X, pady=5)
        
        tk.Button(
            button_frame,
            text="СОХРАНИТЬ ВСЕ НАСТРОЙКИ",
            bg="#4CAF50",
            fg="white",
            font=("Arial", 9, "bold"),
            command=save_all_settings
        ).pack(fill=tk.X, pady=5)

        tk.Button(
            button_frame,
            text="ОТМЕНА",
            bg="#9E9E9E",
            fg="white",
            font=("Arial", 9, "bold"),
            command=on_close
        ).pack(fill=tk.X, pady=5)

        tk.Button(
            button_frame,
            text="🚪 ВЫХОД ИЗ ПРОГРАММЫ",
            bg="#f44336",
            fg="white",
            font=("Arial", 9, "bold"),
            command=self.confirm_exit
        ).pack(fill=tk.X, pady=5)

        settings_window.update_idletasks()

        x = (settings_window.winfo_screenwidth() // 2) - (settings_window.winfo_width() // 2)
        y = (settings_window.winfo_screenheight() // 2) - (settings_window.winfo_height() // 2)
        settings_window.geometry(f"+{x}+{y}")

    def reset_break_timer(self, parent_window):
        """Сбрасывает таймер режима ожидания."""
        if messagebox.askyesno("Сброс таймера", "Сбросить таймер свободного времени?\nОтсчет начнется заново.", parent=parent_window):
            self.start_break_mode_for_today(reason="ручной сброс таймера свободного времени")
            messagebox.showinfo("Успех", "Таймер свободного времени сброшен!", parent=parent_window)

    # ==================== ЗВУК ====================

    def start_beep_warning(self):
        logging.debug(f"start_beep_warning: вызван, is_locked={self.is_locked}, is_beeping={self.is_beeping}, is_limit_reached={self.is_limit_reached_today}, is_break_mode={self.is_break_mode}")
        if (self.is_locked or self.is_limit_reached_today or self.is_break_mode
                or self.is_manual_pause_active() or not self.is_today_scheduled()):
            logging.debug("start_beep_warning: условия блокировки - не запускаю писк")
            return
        if self.is_beeping:
            return
        self.is_beeping = True
        self._schedule_beep()

    def _schedule_beep(self):
        if (self.is_locked or self.is_limit_reached_today or self.is_break_mode
                or self.is_manual_pause_active() or not self.is_today_scheduled()):
            logging.debug("_schedule_beep: заблокировано, лимит или режим ожидания - отмена писка")
            self.is_beeping = False
            self._beep_after_id = None
            return
        if not self.is_beeping or not self.is_running:
            return
        
        logging.debug("_schedule_beep: beep!")
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            try:
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
            except Exception:
                pass
        
        if (self.is_beeping and self.is_running and not self.is_locked
                and not self.is_limit_reached_today and not self.is_break_mode
                and not self.is_manual_pause_active()
                and self.is_today_scheduled()):
            self._beep_after_id = self.root.after(1000, self._schedule_beep)
        else:
            logging.debug("_schedule_beep: не планирую следующий beep")

    def stop_beep_warning(self):
        logging.debug(f"stop_beep_warning: был is_beeping={self.is_beeping}")
        if self.is_beeping:
            self.is_beeping = False
        if self._beep_after_id is not None:
            try:
                self.root.after_cancel(self._beep_after_id)
                logging.debug("stop_beep_warning: отменён запланированный after")
            except Exception:
                pass
            self._beep_after_id = None

    # ==================== БЛОКИРОВКА / ПАРОЛЬ / ВЫХОД ====================

    def confirm_exit(self):
        result = messagebox.askyesno(
            "Подтверждение",
            "⚠️ ВЫХОД ИЗ ПРОГРАММЫ ⚠️\n\n"
            "Вы действительно хотите выйти?",
            parent=self.root
        )
        if result:
            if self.is_settings_open:
                self.safe_shutdown()
            else:
                self.show_password_window()

    def show_exit_dialog(self, event=None):
        self.show_password_window()

    def lock_computer(self):
        # Вызов мог быть поставлен в очередь фоновым потоком до того, как
        # пользователь выполнил норму или программа перешла в режим отдыха.
        # Поэтому проверяем разрешение непосредственно перед LockWorkStation.
        self.check_date_change()
        if (self.is_locked
                or self.is_limit_reached_today
                or self.is_break_mode
                or self.is_manual_pause_active()
                or not self.is_today_scheduled()
                or self.is_password_window_open):
            logging.info(
                "Блокировка отменена: locked=%s, limit=%s, break=%s, pause=%s, scheduled=%s, password=%s",
                self.is_locked,
                self.is_limit_reached_today,
                self.is_break_mode,
                self.is_manual_pause_active(),
                self.is_today_scheduled(),
                self.is_password_window_open
            )
            self.stop_beep_warning()
            return

        logging.warning("Блокировка компьютера!")
        logging.debug("lock_computer: Устанавливаю is_locked = True")
        self.is_locked = True
        self.close_welcome()

        logging.debug("lock_computer: вызываю stop_beep_warning")
        self.stop_beep_warning()
        time.sleep(0.1)
        self.stop_beep_warning()

        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

        was_connected = self.arduino_was_connected

        logging.debug("lock_computer: вызываю LockWorkStation...")
        try:
            ctypes.windll.user32.LockWorkStation()
            logging.debug("lock_computer: LockWorkStation выполнен успешно")
        except Exception as e:
            logging.error(f"Не удалось заблокировать компьютер: {e}")
            self.is_locked = False
            return

        logging.debug("lock_computer: запускаю поток _wait_for_unlock")
        threading.Thread(target=self._wait_for_unlock, args=(was_connected,), daemon=True).start()

    def _wait_for_unlock(self, was_connected: bool):
        logging.debug("_wait_for_unlock: поток запущен, жду исчезновения LogonUI.exe...")
        time.sleep(2)

        while self.is_running:
            if not self._is_session_locked():
                logging.debug("_wait_for_unlock: сессия разблокирована, выхожу из цикла")
                break
            time.sleep(1)

        logging.debug("_wait_for_unlock: планирую вызов wake_up через root.after")
        self.root.after(0, self.wake_up, was_connected)

    def wake_up(self, was_connected: bool = False):
        logging.debug("wake_up: вызван, сбрасываю is_locked")
        self.is_locked = False

        self.last_activity_time = time.time() + 60
        self.last_daily_update_time = time.time()
        self.check_date_change()
        self.maybe_start_pending_break(reason="первый вход пользователя после разблокировки")

        logging.debug(f"wake_up: was_connected = {was_connected}, arduino сейчас = {self.arduino.is_open if self.arduino else 'None'}")
        if was_connected and (not self.arduino or not self.arduino.is_open):
            logging.info("Восстановление соединения с Arduino...")
            self.attempt_reconnect()

        if self.arduino and self.arduino.is_open:
            try:
                self.arduino.reset_input_buffer()
                self.arduino.reset_output_buffer()
                logging.info("Буферы Arduino очищены")
            except Exception:
                pass

        logging.debug("wake_up: вызываю stop_beep_warning и schedule_gui_update")
        self.stop_beep_warning()
        self.set_window_normal()
        if self.is_limit_reached_today:
            self.apply_limit_reached_style()
        self.schedule_gui_update()

    def show_password_window(self, event=None):
        if self.is_password_window_open and self.password_window:
            try:
                self.password_window.lift()
                self.password_window.focus_force()
                for child in self.password_window.winfo_children():
                    for subchild in child.winfo_children():
                        if isinstance(subchild, tk.Entry):
                            subchild.focus_force()
                            return
            except Exception:
                pass
            return

        self.idle_time_at_password = time.time() - self.last_activity_time

        self.password_window = tk.Toplevel(self.root)
        self.password_window.title("Ввод пароля")
        self.password_window.geometry("320x160")
        self.password_window.resizable(False, False)

        self.password_window.transient(self.root)
        self.password_window.grab_set()
        self.password_window.attributes('-topmost', True)
        self.password_window.focus_force()

        self.password_window.protocol("WM_DELETE_WINDOW", self.close_password_window)

        self.password_window.update_idletasks()
        x = (self.password_window.winfo_screenwidth() // 2) - (self.password_window.winfo_width() // 2)
        y = (self.password_window.winfo_screenheight() // 2) - (self.password_window.winfo_height() // 2)
        self.password_window.geometry(f'+{x}+{y}')

        frame = tk.Frame(self.password_window, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="Введите пароль для выхода из программы:",
            font=("Arial", 10)
        ).pack(pady=(0, 10))

        password_var = tk.StringVar()
        password_entry = tk.Entry(frame, textvariable=password_var, show="*", font=("Arial", 11), width=22)
        password_entry.pack(pady=5)
        password_entry.focus_force()

        btn_frame = tk.Frame(frame)
        btn_frame.pack(pady=10)

        def check_password():
            if self.verify_admin_password(password_var.get()):
                self.close_password_window()
                self.safe_shutdown()
            else:
                messagebox.showerror("Ошибка", "Неверный пароль!", parent=self.password_window)
                password_entry.delete(0, tk.END)
                password_entry.focus_force()

        def close_and_cancel():
            self.close_password_window()

        tk.Button(btn_frame, text="OK", command=check_password, bg="green", fg="white", width=8).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Отмена", command=close_and_cancel, bg="gray", fg="white", width=8).pack(side=tk.LEFT, padx=5)

        password_entry.bind('<Return>', lambda e: check_password())
        self.password_window.bind('<Escape>', lambda e: close_and_cancel())

        self.is_password_window_open = True
        self.root.after(60000, self.check_password_window_timeout)

    def close_password_window(self):
        if self.password_window:
            try:
                self.password_window.grab_release()
                self.password_window.destroy()
            except Exception:
                pass

        self.password_window = None
        self.is_password_window_open = False

    def check_password_window_timeout(self):
        if self.is_password_window_open and self.password_window:
            result = messagebox.askyesno(
                "Тайм-аут",
                "Окно пароля открыто более 60 секунд.\nПродолжить ожидание?",
                parent=self.password_window
            )
            if result:
                self.root.after(60000, self.check_password_window_timeout)
            else:
                self.close_password_window()

    def safe_shutdown(self):
        logging.info("Завершение программы...")

        self.is_running = False
        self.stop_beep_warning()

        try:
            self.save_daily_stats()
        except Exception:
            pass
        
        try:
            self.save_break_state()
        except Exception:
            pass

        if self.gui_after_id is not None:
            try:
                self.root.after_cancel(self.gui_after_id)
            except Exception:
                pass
            self.gui_after_id = None

        if self.keyboard_available and self.keyboard:
            try:
                self.keyboard.unhook_all_hotkeys()
            except Exception:
                pass

        with self.connection_lock:
            if self.arduino:
                try:
                    self.arduino.close()
                except Exception:
                    pass
                self.arduino = None

        try:
            self.root.quit()
        except Exception:
            pass

        try:
            self.root.destroy()
        except Exception:
            pass

        sys.exit(0)

    # ==================== МОНИТОРИНГ ====================

    def start_connection_checker(self):
        def checker():
            while self.is_running:
                time.sleep(CHECK_INTERVAL_SEC)

                try:
                    time_since_start = time.time() - self.program_start_time
                    current_time = time.time()

                    # Во время свободного времени и ручной паузы не проверяем Arduino на блокировку
                    if self.is_break_mode or self.is_manual_pause_active() or not self.is_today_scheduled():
                        continue

                    if not self.arduino_was_connected and time_since_start > STARTUP_DELAY_SEC:
                        logging.warning(f"Arduino не найден в течение {STARTUP_DELAY_SEC} сек. Блокировка!")
                        self.root.after(0, self.lock_computer)
                        continue

                    if self.arduino_was_connected and (not self.arduino or not self.arduino.is_open):
                        idle_time = current_time - self.last_activity_time

                        if self.is_limit_reached_today:
                            logging.info("Дневной лимит достигнут, блокировка не требуется")
                            continue

                        if idle_time >= self.idle_timeout:
                            logging.warning(f"Потеряно соединение с Arduino и превышен таймаут бездействия ({idle_time:.0f}с >= {self.idle_timeout}с)! Блокировка!")
                            self.root.after(0, self.lock_computer)
                        else:
                            logging.warning(f"Потеряно соединение с Arduino, но таймаут не превышен ({idle_time:.0f}с < {self.idle_timeout}с). Ждём переподключения...")
                            self.root.after(0, self.attempt_reconnect)

                except Exception as e:
                    logging.warning(f"Ошибка в connection_checker: {e}")

        thread = threading.Thread(target=checker, daemon=True)
        thread.start()
        logging.info(f"Запущен проверщик подключения (интервал {CHECK_INTERVAL_SEC} сек)")

    # ==================== СИСТЕМНЫЕ СОБЫТИЯ ====================

    def start_port_scanner(self):
        """Запускает фоновый сканер портов."""
        scanner_thread = threading.Thread(target=self.background_port_scanner, daemon=True)
        scanner_thread.start()
        logging.info("Запущен фоновый сканер портов (проверка каждые 3 сек)")

    def background_port_scanner(self):
        """Фоновый поток для автоопределения тренажера при запуске до подключения."""
        while self.is_running:
            need_to_connect = False

            if not self.arduino_was_connected:
                need_to_connect = True
            elif self.arduino is None or not self.arduino.is_open:
                need_to_connect = True

            if need_to_connect:
                port = self.find_arduino_port()
                if port:
                    logging.info(f"Обнаружен тренажер на порту {port}! Попытка подключения...")
                    self.root.after(0, lambda: self.update_status("Подключение...", "orange"))
                    self.root.after(0, self.connect_to_arduino)
                    time.sleep(5)

            time.sleep(3)

    def setup_system_events(self):
        if not WIN32_AVAILABLE:
            logging.warning("pywin32 не доступен, системные события не обрабатываются")
            return

        try:
            self.window_handle = self.root.winfo_id()
            logging.info(f"HWND окна: {self.window_handle}")
            logging.info("Поддержка системных событий ограничена. Используется безопасный режим.")
        except Exception as e:
            logging.warning(f"Не удалось настроить системные события: {e}")

    def set_window_normal(self):
        """Переключает окно в обычный компактный режим."""
        if self.window_current_mode in ("normal", "hidden"):
            return
        self.leave_gauge_mode()
        self.window_current_mode = "normal"
        new_width = self.window_normal_width
        new_height = self.window_normal_height
        self.root.geometry(f"{new_width}x{new_height}")

        wrap = new_width - 20
        self.speed_label.config(font=("Arial", self.font_speed_normal, "bold"))
        self.beep_timer_label.config(
            font=("Arial", self.font_beep_timer_normal, "bold"),
            wraplength=wrap
        )
        self.min_speed_label.config(font=("Arial", FONT_MIN_SPEED_SIZE))
        self.load_label.config(font=("Arial", FONT_MIN_SPEED_SIZE))
        self.daily_progress_label.config(font=("Arial", FONT_STATUS_SIZE))
        self.status_label.config(font=("Arial", FONT_STATUS_SIZE))
        self.pulses_label.config(font=("Arial", FONT_PULSES_SIZE))
        self.root.update_idletasks()

    def set_window_alert(self):
        """Переключает окно в увеличенный режим предупреждения."""
        if self.window_current_mode in ("alert", "hidden"):
            return
        self.leave_gauge_mode()
        self.window_current_mode = "alert"
        new_width = self.window_alert_width
        new_height = self.window_alert_height
        self.root.geometry(f"{new_width}x{new_height}")

        wrap = new_width - 20
        self.speed_label.config(font=("Arial", self.font_speed_alert, "bold"))
        self.beep_timer_label.config(
            font=("Arial", self.font_beep_timer_alert, "bold"),
            wraplength=wrap
        )
        self.min_speed_label.config(font=("Arial", FONT_MIN_SPEED_SIZE + 2))
        self.load_label.config(font=("Arial", FONT_MIN_SPEED_SIZE + 2))
        self.daily_progress_label.config(font=("Arial", FONT_STATUS_SIZE + 2))
        self.status_label.config(font=("Arial", FONT_STATUS_SIZE + 2))
        self.pulses_label.config(font=("Arial", FONT_PULSES_SIZE + 2))
        self.root.update_idletasks()


# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    logging.info("=" * 60)
    logging.info("ВЕЛО-КОНТРОЛЛЕР (с поддержкой нагрузки и режимом ожидания) - запуск...")
    logging.info("=" * 60)

    root = tk.Tk()
    app = PedalController(root)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        logging.info("Получен KeyboardInterrupt")
        app.safe_shutdown()
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        logging.error(traceback.format_exc())
        app.safe_shutdown()
