# velo_freetime.py - промежутки времени, когда вращение педалей не требуется

DAY_ALL = 'all'
DAY_KEYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
DAY_NAMES = ('Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье')
DAY_ALL_NAME = 'Каждый день'


def parse_hhmm(text):
    """'09:30' -> минуты от полуночи, иначе None."""
    try:
        hours, minutes = str(text).strip().replace('.', ':').split(':')
        hours, minutes = int(hours), int(minutes)
    except ValueError:
        return None
    if 0 <= hours <= 23 and 0 <= minutes <= 59:
        return hours * 60 + minutes
    return None


def format_hhmm(total_minutes):
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def normalize_windows(raw):
    """Оставляет только корректные промежутки: день, начало < конца (в пределах суток)."""
    result = []
    if not isinstance(raw, (list, tuple)):
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        day = item.get('day')
        start = parse_hhmm(item.get('start'))
        end = parse_hhmm(item.get('end'))
        if day not in DAY_KEYS and day != DAY_ALL:
            continue
        if start is None or end is None or start >= end:
            continue
        window = {'day': day, 'start': format_hhmm(start), 'end': format_hhmm(end)}
        if window not in result:
            result.append(window)
    return result


def day_name(day_key):
    if day_key == DAY_ALL:
        return DAY_ALL_NAME
    return DAY_NAMES[DAY_KEYS.index(day_key)]


def window_label(window):
    return f"{day_name(window['day'])}   {window['start']} – {window['end']}"


def _matching(windows, now):
    weekday_key = DAY_KEYS[now.weekday()]
    minutes = now.hour * 60 + now.minute
    for w in windows:
        if w['day'] not in (DAY_ALL, weekday_key):
            continue
        if parse_hhmm(w['start']) <= minutes < parse_hhmm(w['end']):
            yield w


def is_free_now(windows, now):
    return any(True for _ in _matching(windows, now))


def free_until(windows, now):
    """Текст 'HH:MM' конца текущего свободного промежутка (самого позднего из совпавших) или None."""
    ends = [parse_hhmm(w['end']) for w in _matching(windows, now)]
    return format_hhmm(max(ends)) if ends else None
