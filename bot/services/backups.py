"""Копия бэкапа базы вне сервера и контроль, что бэкапы вообще делаются.

Сервис `backup` (docker-compose, образ postgres:16) раз в сутки кладёт дамп в
общую папку. Бот каждое утро:
- отправляет свежий дамп в Telegram (BACKUP_CHAT_ID, по умолчанию первому
  админу) — если сервер умрёт целиком, данные останутся у тебя;
- сообщает админам, если бэкап упал (last_error.txt) или свежего нет > 26 ч.
Админ-команда /backup — то же самое вручную, файл приходит в текущий чат.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from bot.config import get_settings

logger = logging.getLogger(__name__)

TELEGRAM_LIMIT = 45 * 1024 * 1024  # бот может отправить файл до 50 МБ; берём с запасом
STALE_HOURS = 26                    # сутки + запас на время выполнения
UPLOAD_TIMEOUT = 300                # секунд на загрузку дампа в Telegram
_MARKER = ".backup_shipped"


def latest_backup(directory: Path) -> Path | None:
    """Самый свежий дамп по имени (realtor-ГГГГ-ММ-ДД_ЧЧММ.dump сортируется по времени)."""
    dumps = sorted(directory.glob("realtor-*.dump"))
    return dumps[-1] if dumps else None


def backup_status(directory: Path) -> dict:
    latest = latest_backup(directory)
    error_file = directory / "last_error.txt"
    return {
        "latest": latest,
        "age_hours": (time.time() - latest.stat().st_mtime) / 3600 if latest else None,
        "error": error_file.read_text(encoding="utf-8").strip() if error_file.exists() else None,
        "count": len(list(directory.glob("realtor-*.dump"))),
    }


def _mb(size: int) -> str:
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} КБ"
    return f"{size / 1024 / 1024:.1f} МБ"


async def _alert(bot: Bot, text: str) -> None:
    for admin_id in get_settings().admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001
            logger.debug("Не удалось уведомить админа %s", admin_id)


async def ship_latest_backup(bot: Bot, *, directory: Path | None = None,
                             marker_dir: Path | None = None, chat_id: int | None = None,
                             force: bool = False) -> str:
    """Отправить свежий бэкап. Возвращает итог: sent | already | stale | error | too_big | disabled.

    chat_id — куда слать вместо BACKUP_CHAT_ID (ручной /backup); force — даже если
    этот файл уже отправлялся. Отметка «отправлено» ставится только для штатного
    получателя, чтобы ручная выгрузка не отменяла ночную копию.
    """
    settings = get_settings()
    directory = directory or Path(settings.backup_dir)
    marker = (marker_dir or Path(settings.media_dir)) / _MARKER
    if not directory.is_dir():
        return "disabled"  # сервис backup не подключён

    status = backup_status(directory)
    if status["error"]:
        await _alert(bot, f"⚠️ <b>Бэкап базы не удался</b>\n<code>{status['error'][:500]}</code>\n"
                          "Логи: <code>docker compose logs backup</code>")
        return "error"
    latest: Path | None = status["latest"]
    if latest is None or status["age_hours"] > STALE_HOURS:
        age = "ни одного бэкапа" if latest is None else f"последний {status['age_hours']:.0f} ч назад"
        await _alert(bot, f"⚠️ <b>Свежего бэкапа базы нет</b> ({age}). Проверь сервис backup: "
                          "<code>docker compose logs backup</code>")
        return "stale"

    if not force and marker.exists() and marker.read_text(encoding="utf-8").strip() == latest.name:
        return "already"

    size = latest.stat().st_size
    if size > TELEGRAM_LIMIT:
        await _alert(bot, f"💾 Бэкап {latest.name} ({_mb(size)}) больше лимита Telegram — "
                          "лежит только на сервере. Нужно внешнее хранилище (S3/Backblaze).")
        result = "too_big"
    else:
        target = chat_id or settings.backup_chat_id or (settings.admin_ids[0] if settings.admin_ids else 0)
        if not target:
            return "disabled"
        await bot.send_document(
            target, FSInputFile(str(latest)),
            caption=(f"💾 Бэкап базы · {latest.stem.removeprefix('realtor-')}\n"
                     f"{_mb(size)} · на сервере хранится {status['count']} шт.\n"
                     "Восстановление: <code>sh scripts/restore_backup.sh &lt;файл&gt;</code>"),
            request_timeout=UPLOAD_TIMEOUT,
        )
        result = "sent"
    if chat_id is None:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(latest.name, encoding="utf-8")
    logger.info("Бэкап %s: %s", latest.name, result)
    return result
