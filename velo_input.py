# velo_input.py - сколько секунд пользователь не трогал клавиатуру и мышь

import ctypes
import sys


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def seconds_since_last_input():
    """Секунды с последнего нажатия клавиши или движения мыши в этом сеансе Windows.

    Берётся время последнего ввода от системы (GetLastInputInfo): сами нажатия
    не перехватываются и не записываются. Если узнать нельзя - возвращается большое
    число, чтобы поздравления не откладывались навсегда.
    """
    if sys.platform != 'win32':
        return 1e9
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 1e9
        tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 1e9
