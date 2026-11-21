# velo_trainer.py - обмен состоянием с тренажёром «Детектив Ошибкин» через JSON-файлы

import json
import logging
import os
import tempfile
from datetime import datetime

PROGRAMDATA_DIR = os.environ.get('PROGRAMDATA', 'C:\\ProgramData')
TRAINER_STATE_FILE = os.path.join(PROGRAMDATA_DIR, 'DetectiveOshibkin', 'state.json')
OWN_STATE_FILE = os.path.join(PROGRAMDATA_DIR, 'VeloController', 'state.json')

HEARTBEAT_MAX_AGE_SEC = 15      # старше - тренажёр считается закрытым (idle)
TRAINER_POLL_SEC = 1.0          # как часто читать файл тренажёра
OWN_STATE_WRITE_SEC = 2.0       # как часто обновлять свой heartbeat
OWN_STATES = ('idle', 'riding', 'blocking')

# Результат чтения файла тренажёра
NONE, BROKEN, IDLE, WARNING, SESSION, STALE = 'none', 'broken', 'idle', 'warning', 'session', 'stale'


def _parse_time(text, now):
    """ISO 8601 (с поясом или без) -> aware datetime; None, если не разобрать."""
    try:
        value = datetime.fromisoformat(str(text).strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.astimezone()          # без пояса - местное время
    return value


def read_trainer_state(path=TRAINER_STATE_FILE, now=None):
    """Читает файл тренажёра. Возвращает dict(status, session_id, phase).

    status: none - файла нет; broken - не разобрать; idle / warning / session - состояние
    при свежем heartbeat; stale - heartbeat старше 15 секунд (считать idle).
    Пауза нужна только при status == session.
    """
    now = now or datetime.now().astimezone()
    result = {'status': NONE, 'session_id': None, 'phase': None}
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except FileNotFoundError:
        return result
    except (OSError, ValueError):
        result['status'] = BROKEN
        return result
    if not isinstance(data, dict):
        result['status'] = BROKEN
        return result

    result['session_id'] = str(data.get('session_id')) if data.get('session_id') is not None else None
    result['phase'] = data.get('phase')

    heartbeat = _parse_time(data.get('heartbeat'), now)
    if heartbeat is None or abs((now - heartbeat).total_seconds()) > HEARTBEAT_MAX_AGE_SEC:
        result['status'] = STALE
        return result

    state = data.get('state')
    result['status'] = state if state in (IDLE, WARNING, SESSION) else IDLE
    return result


def write_own_state(state, path=OWN_STATE_FILE, now=None):
    """Пишет файл велоконтроллера атомарно (временный файл, затем переименование)."""
    if state not in OWN_STATES:
        raise ValueError(state)
    now = now or datetime.now().astimezone()
    payload = {'version': 1, 'state': state, 'heartbeat': now.isoformat(timespec='seconds')}
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='state.', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return payload
