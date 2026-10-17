"""Сторож ИИ: тревога админам, если ИИ не работает дольше 10 минут.

Провайдеры регулярно снимают модели, отзывают ключи, режут лимиты — раньше это
обнаруживалось через дни, по жалобам клиентов. Теперь:
- каждый запрос через LLMRouter отмечает здесь успех/провал всей цепочки;
- раз в 5 минут сторож смотрит: если цепочка не отвечает ≥ 10 минут
  (подтверждаем коротким тестовым запросом) — тревога админам с ошибкой
  каждого провайдера и подсказкой, что делать;
- если запросов давно не было — сам делает тестовый запрос раз в 30 минут,
  чтобы узнать о снятой модели раньше клиентов;
- когда ИИ ожил — сообщение «снова работает» с длительностью простоя.
Тревога по каждому направлению — не чаще раза в час («мигающий» сбой не спамит).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from html import escape

from aiogram import Bot

from bot.services.notify import notify_platform

logger = logging.getLogger(__name__)

THRESHOLD = 600      # сколько цепочка должна лежать, чтобы тревожить (сек)
ALERT_EVERY = 3600   # не чаще одной тревоги в час по направлению
IDLE_PROBE = 1800    # тестовый запрос, если реальных не было 30 мин
PING = [{"role": "user", "content": "Reply with: OK"}]

_LABELS = {
    "text": "Разбор объявлений (текстовый ИИ)",
    "vision": "Распознавание скринов (Vision)",
}
_IMPACT = {
    "text": "бот разбирает объявления без ИИ (регулярками): качество хуже, описания шаблонные.",
    "vision": "скрины объявлений не распознаются — владельцев просят прислать текст.",
}


@dataclass
class _State:
    down_since: float | None = None      # с какого момента вся цепочка не отвечает
    last_ok: float | None = None
    last_probe: float | None = None
    last_alert: float | None = None
    alerted: bool = False                # тревога по текущему сбою уже отправлена
    recovered_after: float | None = None  # длительность простоя → сообщить «ожил»
    errors: dict[str, str] = field(default_factory=dict)


class OutageTracker:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._states: dict[str, _State] = {}

    def now(self) -> float:
        return self._clock()

    def state(self, label: str) -> _State:
        return self._states.setdefault(label, _State())

    def record_ok(self, label: str) -> None:
        st, now = self.state(label), self.now()
        if st.down_since is not None and st.alerted:
            st.recovered_after = now - st.down_since
        st.down_since, st.alerted, st.errors, st.last_ok = None, False, {}, now

    def record_fail(self, label: str, errors: dict[str, str]) -> None:
        st = self.state(label)
        if st.down_since is None:
            st.down_since = self.now()
        st.errors = dict(errors)

    def needs_probe(self, label: str) -> bool:
        st, now = self.state(label), self.now()
        if st.down_since is not None:
            return now - st.down_since >= THRESHOLD  # подтвердить, что всё ещё лежит
        last = max((t for t in (st.last_ok, st.last_probe) if t is not None), default=None)
        return last is None or now - last >= IDLE_PROBE

    def take_message(self, label: str) -> str | None:
        """Что сообщить админам сейчас (или None). Отметки ставит сам."""
        st, now = self.state(label), self.now()
        if st.recovered_after is not None:
            minutes = max(1, round(st.recovered_after / 60))
            st.recovered_after = None
            return f"✅ <b>ИИ снова работает</b> — {_LABELS.get(label, label)}. Простой ≈ {minutes} мин."
        if (st.down_since is not None and not st.alerted and now - st.down_since >= THRESHOLD
                and (st.last_alert is None or now - st.last_alert >= ALERT_EVERY)):
            st.alerted, st.last_alert = True, now
            return _alert_text(label, now - st.down_since, st.errors)
        return None


def _alert_text(label: str, down_for: float, errors: dict[str, str]) -> str:
    lines = [
        f"🚨 <b>ИИ не отвечает уже {round(down_for / 60)} мин</b>",
        f"{_LABELS.get(label, label)}: {_IMPACT.get(label, '')}",
        "",
        "<b>Ошибки провайдеров:</b>",
    ]
    lines += [f"• {escape(name)}: <code>{escape(err[:200])}</code>" for name, err in errors.items()]
    lines += [
        "",
        "<b>Что делать:</b>",
        "• /ai_status — живая проверка каждого провайдера;",
        "• «not found», «decommissioned» — модель сняли: /ai_models groq (или gemini) → "
        "впиши новую в .env (GROQ_MODEL=…) и перезапусти бота;",
        "• 401/403 — ключ недействителен; 429 — кончился лимит. Надёжнее всего — "
        "резервный ключ (OPENROUTER_API_KEY и др.).",
    ]
    return "\n".join(lines)


OUTAGES = OutageTracker()


def _routers() -> dict:
    from bot.services.ai_service import get_ai_service
    from bot.services.vision import get_vision_service

    return {"text": get_ai_service().router, "vision": get_vision_service().router}


async def check_ai_outage(bot: Bot, *, routers: dict | None = None,
                          tracker: OutageTracker | None = None) -> None:
    """Задача планировщика (раз в 5 мин): проверить ИИ и сообщить админам."""
    tracker = tracker or OUTAGES
    for label, router in (routers if routers is not None else _routers()).items():
        if not router.enabled:
            continue  # не настроено — это не сбой, бот работает офлайн осознанно
        if tracker.needs_probe(label):
            tracker.state(label).last_probe = tracker.now()
            try:
                await router.complete(PING, max_tokens=16)  # сам отметит успех/провал
            except Exception:  # noqa: BLE001
                logger.exception("Сторож ИИ: тестовый запрос (%s) упал", label)
        message = tracker.take_message(label)
        if message:
            logger.warning("Сторож ИИ (%s): %s", label, message.splitlines()[0])
            await notify_platform(bot, message)
