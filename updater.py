# updater.py - автообновление Вело-контроллера из релизов GitHub

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

from version import RELEASE_ASSET_NAME

API_TIMEOUT_SEC = 20
DOWNLOAD_TIMEOUT_SEC = 120
MIN_EXE_SIZE = 1_000_000


def parse_version(text):
    """'v4.1.0' -> (4, 1, 0). Нечисловые хвосты игнорируются."""
    nums = re.findall(r"\d+", str(text).split("-")[0])
    return tuple(int(n) for n in nums) or (0,)


def can_self_update(repo):
    """Обновляться можно только собранной exe-программе с заданным репозиторием."""
    return bool(repo) and bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def _request(url):
    return urllib.request.Request(url, headers={
        "User-Agent": "VeloController-Updater",
        "Accept": "application/vnd.github+json",
    })


def find_latest_release(repo, current_version):
    """Возвращает dict(tag, exe_url, sha_url) для более новой версии или None."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    with urllib.request.urlopen(_request(url), timeout=API_TIMEOUT_SEC) as resp:
        release = json.load(resp)

    tag = release.get("tag_name", "")
    if parse_version(tag) <= parse_version(current_version):
        return None

    assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
    exe_url = assets.get(RELEASE_ASSET_NAME)
    sha_url = assets.get(RELEASE_ASSET_NAME + ".sha256")
    if not exe_url or not sha_url:
        logging.warning(f"Релиз {tag} без {RELEASE_ASSET_NAME} или .sha256 - пропущен")
        return None
    return {"tag": tag, "exe_url": exe_url, "sha_url": sha_url}


def _download(url, dest):
    with urllib.request.urlopen(_request(url), timeout=DOWNLOAD_TIMEOUT_SEC) as resp, open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


def check_and_stage(repo, current_version, target_exe=None):
    """Проверяет релиз; если он новее - скачивает рядом с exe и сверяет SHA-256.

    Возвращает путь к скачанному файлу или None.
    """
    release = find_latest_release(repo, current_version)
    if not release:
        return None

    target_exe = target_exe or sys.executable
    staged = os.path.join(os.path.dirname(target_exe), "VeloController.update.exe")
    logging.info(f"Найдена версия {release['tag']}, скачиваю...")

    with urllib.request.urlopen(_request(release["sha_url"]), timeout=API_TIMEOUT_SEC) as resp:
        expected = resp.read().decode("utf-8", "replace").split()[0].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Некорректный файл SHA-256 в релизе")

    _download(release["exe_url"], staged)

    h = hashlib.sha256()
    with open(staged, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    size = os.path.getsize(staged)
    with open(staged, "rb") as f:
        magic = f.read(2)

    if h.hexdigest() != expected or magic != b"MZ" or size < MIN_EXE_SIZE:
        os.remove(staged)
        raise ValueError("Скачанный файл не прошёл проверку целостности")

    logging.info(f"Версия {release['tag']} скачана и проверена")
    return staged


def apply_staged(staged, target_exe=None):
    """Запускает отдельный скрипт: после выхода программы подменяет exe и запускает новый."""
    target_exe = target_exe or sys.executable
    script = os.path.join(tempfile.gettempdir(), "velocontroller_update.bat")
    lines = [
        "@echo off",
        "set /a tries=0",
        ":retry",
        "set /a tries+=1",
        "if %tries% GTR 60 goto fail",
        "timeout /t 2 /nobreak >nul",
        f'move /Y "{staged}" "{target_exe}" >nul 2>&1',
        "if errorlevel 1 goto retry",
        f'start "" "{target_exe}"',
        "goto end",
        ":fail",
        f'del "{staged}" >nul 2>&1',
        ":end",
        '(goto) 2>nul & del "%~f0"',
    ]
    with open(script, "w", encoding="cp866", errors="replace") as f:
        f.write("\r\n".join(lines) + "\r\n")

    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(["cmd.exe", "/c", script], creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
                     close_fds=True)
    logging.info("Запущен скрипт замены exe, программа завершается для обновления")
