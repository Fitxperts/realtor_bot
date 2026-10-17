"""Отправка бэкапа в Telegram и тревоги, если бэкап не сделан."""
from __future__ import annotations

import os
import time

import pytest

from bot.services import backups


class _Bot:
    def __init__(self) -> None:
        self.docs: list = []
        self.messages: list = []

    async def send_document(self, chat_id, document, caption=None, **kwargs):
        self.docs.append((chat_id, document.path, caption, kwargs))

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


def _dump(directory, name: str, *, size: int = 2048, age_hours: float = 1.0):
    path = directory / name
    path.write_bytes(b"PGDMP" + b"\0" * (size - 5))
    ts = time.time() - age_hours * 3600
    os.utime(path, (ts, ts))
    return path


@pytest.fixture
def dirs(tmp_path):
    backup_dir, marker_dir = tmp_path / "backups", tmp_path / "media"
    backup_dir.mkdir()
    return backup_dir, marker_dir


async def test_sends_latest_once(dirs):
    backup_dir, marker_dir = dirs
    _dump(backup_dir, "realtor-2026-10-16_0300.dump", age_hours=25)
    newest = _dump(backup_dir, "realtor-2026-10-17_0300.dump", age_hours=1)
    bot = _Bot()

    assert await backups.ship_latest_backup(bot, directory=backup_dir, marker_dir=marker_dir) == "sent"
    assert len(bot.docs) == 1
    chat_id, path, caption, kwargs = bot.docs[0]
    assert chat_id == 1                       # BACKUP_CHAT_ID пуст → первый админ
    assert path == str(newest)
    assert "2026-10-17_0300" in caption and "2 шт." in caption
    assert kwargs["request_timeout"] == backups.UPLOAD_TIMEOUT
    assert bot.messages == []

    # Повторный запуск в тот же день — файл уже отправлен
    assert await backups.ship_latest_backup(bot, directory=backup_dir, marker_dir=marker_dir) == "already"
    assert len(bot.docs) == 1

    # Появился новый дамп → снова отправляем
    _dump(backup_dir, "realtor-2026-10-18_0300.dump", age_hours=0.5)
    assert await backups.ship_latest_backup(bot, directory=backup_dir, marker_dir=marker_dir) == "sent"
    assert len(bot.docs) == 2


async def test_manual_backup_goes_to_chat_and_keeps_nightly_copy(dirs):
    backup_dir, marker_dir = dirs
    _dump(backup_dir, "realtor-2026-10-17_0300.dump")
    bot = _Bot()

    # /backup от админа: в его чат, даже без нового файла, отметку не ставит
    for _ in range(2):
        assert await backups.ship_latest_backup(
            bot, directory=backup_dir, marker_dir=marker_dir, chat_id=555, force=True) == "sent"
    assert [d[0] for d in bot.docs] == [555, 555]
    # Ночная отправка штатному получателю всё равно состоится
    assert await backups.ship_latest_backup(bot, directory=backup_dir, marker_dir=marker_dir) == "sent"
    assert bot.docs[-1][0] == 1


async def test_failed_backup_alerts_admins(dirs):
    backup_dir, marker_dir = dirs
    _dump(backup_dir, "realtor-2026-10-16_0300.dump", age_hours=25)
    (backup_dir / "last_error.txt").write_text("2026-10-17 03:00 pg_dump: connection refused", encoding="utf-8")
    bot = _Bot()

    assert await backups.ship_latest_backup(bot, directory=backup_dir, marker_dir=marker_dir) == "error"
    assert bot.docs == []
    assert len(bot.messages) == 1 and "connection refused" in bot.messages[0][1]


async def test_stale_or_missing_backup_alerts(dirs):
    backup_dir, marker_dir = dirs
    bot = _Bot()
    assert await backups.ship_latest_backup(bot, directory=backup_dir, marker_dir=marker_dir) == "stale"
    assert "ни одного бэкапа" in bot.messages[-1][1]

    _dump(backup_dir, "realtor-2026-10-14_0300.dump", age_hours=72)
    assert await backups.ship_latest_backup(bot, directory=backup_dir, marker_dir=marker_dir) == "stale"
    assert "72 ч назад" in bot.messages[-1][1]
    assert bot.docs == []


async def test_too_big_for_telegram(dirs, monkeypatch):
    backup_dir, marker_dir = dirs
    _dump(backup_dir, "realtor-2026-10-17_0300.dump", size=4096)
    monkeypatch.setattr(backups, "TELEGRAM_LIMIT", 1024)
    bot = _Bot()

    assert await backups.ship_latest_backup(bot, directory=backup_dir, marker_dir=marker_dir) == "too_big"
    assert bot.docs == [] and "больше лимита" in bot.messages[0][1]
    # Предупреждаем один раз на файл, а не каждое утро
    assert await backups.ship_latest_backup(bot, directory=backup_dir, marker_dir=marker_dir) == "already"
    assert len(bot.messages) == 1


async def test_disabled_without_backup_folder(tmp_path):
    bot = _Bot()
    result = await backups.ship_latest_backup(bot, directory=tmp_path / "nope", marker_dir=tmp_path)
    assert result == "disabled"
    assert bot.docs == [] and bot.messages == []


def test_latest_backup_by_name(tmp_path):
    assert backups.latest_backup(tmp_path) is None
    _dump(tmp_path, "realtor-2026-10-17_0300.dump", age_hours=50)   # старый mtime, но новее по имени
    _dump(tmp_path, "realtor-2026-10-09_0300.dump", age_hours=1)
    (tmp_path / ".realtor-2026-10-18_0300.part").write_bytes(b"x")  # недописанный — игнор
    assert backups.latest_backup(tmp_path).name == "realtor-2026-10-17_0300.dump"
