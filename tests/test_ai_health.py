"""Сторож ИИ: тревога при долгом сбое, «ожил», антиспам, тестовые запросы."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.services import ai_health
from bot.services.ai_health import OutageTracker, check_ai_outage
from bot.services.llm_providers import MIN_OUTPUT_TOKENS, LLMRouter, ProviderSpec

MIN = 60


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def at(self, minutes: float) -> None:
        self.t = 1000.0 + minutes * MIN


class _Client:
    """OpenAI-подобный клиент: ошибка или ответ, запоминает вызовы."""

    def __init__(self, error: Exception | None = None, answer: str = "OK") -> None:
        self.error, self.answer, self.calls = error, answer, []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.answer))])


def _router(client: _Client, label: str | None = "text", backend: str = "openai") -> LLMRouter:
    r = LLMRouter([ProviderSpec("groq", "openai/gpt-oss-120b", "k", "https://example.invalid/v1")], label=label)
    r.providers[0].client = client
    r.providers[0].spec.backend = backend
    return r


class _Bot:
    def __init__(self) -> None:
        self.messages: list = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


@pytest.fixture
def clock_tracker(monkeypatch):
    clock = _Clock()
    tracker = OutageTracker(clock=clock)
    monkeypatch.setattr(ai_health, "OUTAGES", tracker)  # туда же пишет LLMRouter
    return clock, tracker


def test_alert_after_threshold_then_recovery(clock_tracker):
    clock, tr = clock_tracker
    tr.record_fail("text", {"groq (m)": "NotFoundError: model decommissioned"})
    clock.at(5)
    assert tr.take_message("text") is None            # 5 минут — ещё рано
    clock.at(10)
    alert = tr.take_message("text")
    assert "не отвечает уже 10 мин" in alert and "decommissioned" in alert and "/ai_models" in alert
    clock.at(15)
    assert tr.take_message("text") is None            # по этому сбою уже сообщили
    clock.at(20)
    tr.record_ok("text")
    msg = tr.take_message("text")
    assert "снова работает" in msg and "20 мин" in msg
    assert tr.take_message("text") is None


def test_recovery_message_has_downtime(clock_tracker):
    clock, tr = clock_tracker
    tr.record_fail("vision", {"gemini (x)": "401"})
    clock.at(12)
    assert tr.take_message("vision")
    clock.at(32)
    tr.record_ok("vision")
    msg = tr.take_message("vision")
    assert "снова работает" in msg and "32 мин" in msg and "Vision" in msg


def test_short_glitch_is_silent(clock_tracker):
    clock, tr = clock_tracker
    tr.record_fail("text", {"groq (m)": "timeout"})
    clock.at(3)
    tr.record_ok("text")                               # ожил до порога — тишина
    clock.at(15)
    assert tr.take_message("text") is None


def test_flapping_alerts_at_most_hourly(clock_tracker):
    clock, tr = clock_tracker
    tr.record_fail("text", {"groq (m)": "429"})
    clock.at(10)
    assert tr.take_message("text")                     # первая тревога
    clock.at(11)
    tr.record_ok("text")
    assert tr.take_message("text")                     # «ожил»
    clock.at(12)
    tr.record_fail("text", {"groq (m)": "429"})
    clock.at(30)
    assert tr.take_message("text") is None             # снова лежит 18 мин, но час не прошёл
    clock.at(71)
    assert "не отвечает" in tr.take_message("text")    # час прошёл — тревожим
    clock.at(80)
    tr.record_ok("text")
    assert "снова работает" in tr.take_message("text")


def test_errors_are_html_escaped(clock_tracker):
    clock, tr = clock_tracker
    tr.record_fail("text", {"groq (m)": "Error <html> & stuff"})
    clock.at(10)
    assert "&lt;html&gt; &amp; stuff" in tr.take_message("text")


def test_needs_probe_idle_and_down(clock_tracker):
    clock, tr = clock_tracker
    assert tr.needs_probe("text")                      # ещё не знаем, жив ли ИИ
    tr.record_ok("text")
    clock.at(29)
    assert not tr.needs_probe("text")
    clock.at(31)
    assert tr.needs_probe("text")                      # 30 мин без запросов — проверить
    tr.record_fail("text", {})
    clock.at(35)
    assert not tr.needs_probe("text")                  # лежит 4 мин — ждём порога
    clock.at(41)
    assert tr.needs_probe("text")                      # 10 мин — подтверждаем


async def test_router_reports_to_tracker(clock_tracker):
    _, tr = clock_tracker
    r = _router(_Client(error=RuntimeError("404 model not found")))
    assert await r.complete([{"role": "user", "content": "x"}]) is None
    st = tr.state("text")
    assert st.down_since is not None
    assert "404 model not found" in st.errors["groq (openai/gpt-oss-120b)"]

    r.providers[0].client = _Client(answer="ок")
    assert await r.complete([{"role": "user", "content": "x"}]) == "ок"
    assert tr.state("text").down_since is None


async def test_no_attempt_is_not_outage(clock_tracker):
    _, tr = clock_tracker
    r = _router(_Client(error=RuntimeError("x")), backend="anthropic")
    assert await r.complete([{"role": "user", "content": "x"}], openai_only=True) is None
    assert tr.state("text").down_since is None


async def test_unlabelled_router_not_tracked(clock_tracker):
    _, tr = clock_tracker
    r = _router(_Client(error=RuntimeError("x")), label=None)
    await r.complete([{"role": "user", "content": "x"}])
    assert tr.state("text").down_since is None


async def test_watchdog_end_to_end(clock_tracker):
    clock, tr = clock_tracker
    client = _Client(error=RuntimeError("404 The model openai/gpt-oss-120b has been decommissioned"))
    routers = {"text": _router(client), "vision": LLMRouter([], label="vision")}  # vision не настроен
    bot = _Bot()

    await check_ai_outage(bot, routers=routers)        # старт: тестовый запрос → упал
    assert len(client.calls) == 1 and bot.messages == []
    clock.at(5)
    await check_ai_outage(bot, routers=routers)        # до порога не дёргаем провайдера
    assert len(client.calls) == 1 and bot.messages == []
    clock.at(10)
    await check_ai_outage(bot, routers=routers)        # подтвердили → тревога
    assert len(client.calls) == 2
    assert len(bot.messages) == 1 and "decommissioned" in bot.messages[0][1]
    assert bot.messages[0][0] == 1                     # ADMIN_TELEGRAM_ID из conftest
    clock.at(15)
    await check_ai_outage(bot, routers=routers)        # всё ещё лежит: проверяем, но без повторной тревоги
    assert len(client.calls) == 3 and len(bot.messages) == 1

    client.error = None                                # модель вернули / ключ заменили
    clock.at(20)
    await check_ai_outage(bot, routers=routers)
    assert len(bot.messages) == 2 and "снова работает" in bot.messages[1][1]
    clock.at(25)
    await check_ai_outage(bot, routers=routers)        # жив и недавно отвечал — не дёргаем
    assert len(client.calls) == 4 and len(bot.messages) == 2


async def test_max_tokens_floor_for_thinking_models():
    client = _Client()
    r = _router(client, label=None)
    await r.complete([{"role": "user", "content": "x"}], max_tokens=10)
    assert client.calls[0]["max_tokens"] == MIN_OUTPUT_TOKENS


async def test_probe_reports_empty_answer():
    r = _router(_Client(answer=""), label=None)
    result = (await r.probe())[0]
    assert result["ok"] is False and result["error"] == "пустой ответ"
