"""Тесты цепочки ИИ-провайдеров: автопереключение, паузы, сборка цепочки."""
import time
from types import SimpleNamespace

from bot.services import llm_providers
from bot.services.llm_providers import LLMRouter, ProviderSpec, build_chain


def _fake(answer=None, error=None):
    class C:
        async def create(self, **kwargs):
            if error:
                raise error
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer))])

    return SimpleNamespace(chat=SimpleNamespace(completions=C()))


def _router(*clients) -> LLMRouter:
    specs = [ProviderSpec(f"p{i}", f"m{i}", "k", "https://example.invalid/v1") for i in range(len(clients))]
    r = LLMRouter(specs)
    for live, client in zip(r.providers, clients, strict=True):
        live.client = client
    return r


async def test_failover_to_next_provider():
    r = _router(_fake(error=RuntimeError("404 model no longer available")), _fake(answer="ок"))
    assert await r.complete([{"role": "user", "content": "x"}]) == "ок"
    dead = r.providers[0]
    # «постоянная» ошибка (модель снята) → длинная пауза, больше не дёргаем каждый раз
    assert dead.cooldown_until - time.monotonic() > 1000
    assert dead.fail_count == 1


async def test_transient_error_short_cooldown():
    r = _router(_fake(error=TimeoutError("timed out")), _fake(answer="ок"))
    await r.complete([{"role": "user", "content": "x"}])
    assert 0 < r.providers[0].cooldown_until - time.monotonic() <= 120


async def test_all_fail_returns_none():
    r = _router(_fake(error=RuntimeError("boom")), _fake(error=RuntimeError("boom")))
    assert await r.complete([{"role": "user", "content": "x"}]) is None


async def test_empty_answer_counts_as_failure():
    r = _router(_fake(answer=""), _fake(answer="второй"))
    assert await r.complete([{"role": "user", "content": "x"}]) == "второй"


def _settings(**kw):
    base = {"llm_chain": [], "llm_base_url": "", "llm_api_key": "", "anthropic_api_key": "", "ai_model": "m"}
    base.update(kw)
    return SimpleNamespace(**base)


def test_build_chain_auto_from_keys(monkeypatch):
    for _name, (_, env, _) in llm_providers.KNOWN_PROVIDERS.items():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", "m")
    names = [s.name for s in build_chain(_settings())]
    assert names == ["groq", "gemini"]


def test_build_chain_legacy_primary_first_without_duplicate(monkeypatch):
    for _name, (_, env, _) in llm_providers.KNOWN_PROVIDERS.items():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", "m")
    chain = build_chain(_settings(llm_base_url="https://api.groq.com/openai/v1/", ai_model="openai/gpt-oss-120b"))
    assert [s.name for s in chain] == ["groq", "gemini"]  # groq не дублируется
    assert chain[0].model == "openai/gpt-oss-120b"


def test_build_chain_explicit_order_and_model(monkeypatch):
    for _name, (_, env, _) in llm_providers.KNOWN_PROVIDERS.items():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", "m")
    chain = build_chain(_settings(llm_chain=["gemini:gemini-3.8-flash", "groq", "openrouter"]))
    # openrouter без ключа — пропущен
    assert [(s.name, s.model) for s in chain] == [("gemini", "gemini-3.8-flash"),
                                                  ("groq", "openai/gpt-oss-120b")]
