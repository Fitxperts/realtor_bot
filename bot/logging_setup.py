"""Настройка логирования (текст/JSON) и опциональная интеграция с Sentry."""
from __future__ import annotations

import json
import logging
import os
import sys


class JsonFormatter(logging.Formatter):
    """Структурные логи в JSON — удобно для сбора в ELK/Loki/etc."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    fmt = os.getenv("LOG_FORMAT", "plain").lower()

    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # Понижаем шум чужих логгеров
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def init_sentry(service: str = "bot") -> bool:
    """Инициализировать Sentry/GlitchTip, если задан SENTRY_DSN и установлен sentry-sdk.

    service — тег события (bot / userbot): видно, какой процесс упал.
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception:  # noqa: BLE001 - sentry-sdk не установлен
        logging.getLogger(__name__).warning("SENTRY_DSN задан, но sentry-sdk недоступен")
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENV", "production"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0.0")),
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
    )
    sentry_sdk.set_tag("service", service)
    logging.getLogger(__name__).info("Sentry инициализирован (%s)", service)
    return True
