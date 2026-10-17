"""Цепочка ИИ-провайдеров с автоматическим переключением (failover).

Зачем: провайдеры регулярно снимают модели (Google снял gemini-2.0/2.5-flash,
Groq — llama-3.3-70b). С одним провайдером бот молча падал в офлайн-разбор.
Теперь запрос идёт по цепочке: первый живой провайдер отвечает, упавший
уходит на «паузу» (cooldown), и бот продолжает работать на следующем.

Конфигурация (всё через .env, все провайдеры — OpenAI-совместимые, кроме Anthropic):
- LLM_CHAIN=groq:openai/gpt-oss-120b,gemini:gemini-flash-latest,openrouter
  явный порядок «провайдер[:модель]». Если не задан — цепочка собирается сама:
  сначала старый основной (LLM_BASE_URL/AI_MODEL), затем все провайдеры, для
  которых есть ключ (GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, …).
- <NAME>_MODEL (например GROQ_MODEL) — переопределить модель по умолчанию.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# name: (base_url, env-переменная ключа, модель по умолчанию — по возможности АЛИАС,
# который провайдер сам держит на актуальной версии и не снимает)
KNOWN_PROVIDERS: dict[str, tuple[str, str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY",
               "gemini-flash-latest"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openrouter/auto"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY", "deepseek-chat"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "gpt-oss-120b"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistral-small-latest"),
}

# Паузы после ошибки: «постоянные» (нет модели/ключа) — дольше, временные — коротко.
_COOLDOWN_PERMANENT = 3600
_COOLDOWN_TRANSIENT = 120
_PERMANENT_MARKERS = ("404", "401", "403", "not found", "no longer available",
                      "invalid api key", "valid api key", "decommission", "deprecat")


@dataclass
class ProviderSpec:
    name: str
    model: str
    api_key: str
    base_url: str | None = None   # None → Anthropic
    backend: str = "openai"       # "openai" | "anthropic"


@dataclass
class _Live:
    spec: ProviderSpec
    client: object
    cooldown_until: float = 0.0
    last_error: str = ""
    ok_count: int = 0
    fail_count: int = 0


def _norm(url: str) -> str:
    return url.rstrip("/").lower()


def _name_for_url(url: str) -> str:
    for name, (base, _, _) in KNOWN_PROVIDERS.items():
        if _norm(base) == _norm(url):
            return name
    return "custom"


def _key_for(name: str) -> str:
    env = KNOWN_PROVIDERS[name][1]
    return os.getenv(env, "").strip()


def _model_for(name: str) -> str:
    return os.getenv(f"{name.upper()}_MODEL", "").strip() or KNOWN_PROVIDERS[name][2]


def build_chain(settings) -> list[ProviderSpec]:
    """Собрать упорядоченную цепочку провайдеров из настроек/окружения."""
    specs: list[ProviderSpec] = []

    if settings.llm_chain:  # явный порядок
        for entry in settings.llm_chain:
            name, _, model = entry.partition(":")
            name = name.strip().lower()
            if name == "anthropic":
                if settings.anthropic_api_key:
                    specs.append(ProviderSpec("anthropic", model or settings.ai_model,
                                              settings.anthropic_api_key, None, "anthropic"))
                continue
            if name not in KNOWN_PROVIDERS:
                logger.warning("LLM_CHAIN: неизвестный провайдер %r — пропуск", name)
                continue
            key = _key_for(name)
            if not key:
                logger.warning("LLM_CHAIN: для %s нет ключа (%s) — пропуск", name, KNOWN_PROVIDERS[name][1])
                continue
            specs.append(ProviderSpec(name, model.strip() or _model_for(name), key, KNOWN_PROVIDERS[name][0]))
        return specs

    # Совместимость: старый основной провайдер — первым.
    if settings.llm_base_url:
        name = _name_for_url(settings.llm_base_url)
        key = settings.llm_api_key or (_key_for(name) if name in KNOWN_PROVIDERS else "")
        if key:
            specs.append(ProviderSpec(name, settings.ai_model, key, settings.llm_base_url))
        else:
            logger.warning("LLM_BASE_URL задан, но ключа нет — основной провайдер пропущен")
    elif settings.anthropic_api_key:
        specs.append(ProviderSpec("anthropic", settings.ai_model, settings.anthropic_api_key, None, "anthropic"))

    # Резерв: все известные провайдеры, для которых есть ключ.
    used = {_norm(s.base_url) for s in specs if s.base_url}
    for name, (base, _, _) in KNOWN_PROVIDERS.items():
        key = _key_for(name)
        if key and _norm(base) not in used:
            specs.append(ProviderSpec(name, _model_for(name), key, base))
            used.add(_norm(base))
    return specs


def _make_client(spec: ProviderSpec):
    if spec.backend == "anthropic":
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=spec.api_key, timeout=30, max_retries=1)
    from openai import AsyncOpenAI

    # Короткий таймаут и 1 ретрай: при сбое быстрее уходим к следующему провайдеру.
    return AsyncOpenAI(api_key=spec.api_key, base_url=spec.base_url, timeout=30, max_retries=1)


def _is_permanent(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _PERMANENT_MARKERS)


class LLMRouter:
    """Отправляет запрос первому живому провайдеру цепочки."""

    def __init__(self, specs: list[ProviderSpec]) -> None:
        self._live: list[_Live] = []
        for spec in specs:
            try:
                self._live.append(_Live(spec, _make_client(spec)))
            except Exception as exc:  # noqa: BLE001 - нет SDK и т.п.
                logger.warning("LLM %s: клиент не создан (%s)", spec.name, exc)
        if self._live:
            logger.info("LLM-цепочка: %s",
                        " → ".join(f"{p.spec.name}:{p.spec.model}" for p in self._live))
        else:
            logger.warning("LLM не настроен — офлайн-режим (разбор регулярками)")

    @property
    def enabled(self) -> bool:
        return bool(self._live)

    @property
    def providers(self) -> list[_Live]:
        return list(self._live)

    def _order(self) -> list[_Live]:
        now = time.monotonic()
        ready = [p for p in self._live if p.cooldown_until <= now]
        # Если все на паузе — всё равно пробуем (лучше попытка, чем офлайн).
        return ready or list(self._live)

    async def _call(self, p: _Live, messages: list[dict], max_tokens: int) -> str:
        if p.spec.backend == "anthropic":
            system = next((m["content"] for m in messages if m["role"] == "system"), None)
            msgs = [m for m in messages if m["role"] != "system"]
            kwargs = {"model": p.spec.model, "max_tokens": max_tokens, "messages": msgs}
            if system:
                kwargs["system"] = system
            resp = await p.client.messages.create(**kwargs)
            return "".join(getattr(b, "text", "") for b in resp.content).strip()
        resp = await p.client.chat.completions.create(
            model=p.spec.model, max_tokens=max_tokens, messages=messages,
        )
        return (resp.choices[0].message.content or "").strip()

    async def complete(self, messages: list[dict], *, max_tokens: int = 500,
                       openai_only: bool = False) -> str | None:
        """Текст ответа первого живого провайдера или None (все упали/не настроены)."""
        for p in self._order():
            if openai_only and p.spec.backend != "openai":
                continue
            try:
                text = await self._call(p, messages, max_tokens)
                if not text:
                    raise ValueError("пустой ответ")
                p.ok_count += 1
                p.last_error = ""
                p.cooldown_until = 0.0
                return text
            except Exception as exc:  # noqa: BLE001 - любой сбой → следующий провайдер
                p.fail_count += 1
                p.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                pause = _COOLDOWN_PERMANENT if _is_permanent(exc) else _COOLDOWN_TRANSIENT
                p.cooldown_until = time.monotonic() + pause
                logger.warning("LLM %s:%s упал (%s) → пауза %dс, пробую следующий",
                               p.spec.name, p.spec.model, p.last_error, pause)
        return None

    async def probe(self) -> list[dict]:
        """Проверить КАЖДОГО провайдера коротким запросом (для /ai_status)."""
        results = []
        for p in self._live:
            started = time.monotonic()
            try:
                text = await self._call(p, [{"role": "user", "content": "Reply with: OK"}], 10)
                results.append({"name": p.spec.name, "model": p.spec.model, "ok": bool(text),
                                "ms": int((time.monotonic() - started) * 1000), "error": ""})
                p.cooldown_until = 0.0
            except Exception as exc:  # noqa: BLE001
                results.append({"name": p.spec.name, "model": p.spec.model, "ok": False,
                                "ms": int((time.monotonic() - started) * 1000),
                                "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        return results

    async def list_models(self, name: str) -> list[str]:
        """Список доступных моделей у провайдера (GET /models) — чтобы не угадывать имена."""
        for p in self._live:
            if p.spec.name == name and p.spec.backend == "openai":
                page = await p.client.models.list()
                return sorted(m.id for m in page.data)
        # провайдер не в цепочке, но ключ есть — создадим временный клиент
        if name in KNOWN_PROVIDERS and _key_for(name):
            client = _make_client(ProviderSpec(name, _model_for(name), _key_for(name), KNOWN_PROVIDERS[name][0]))
            page = await client.models.list()
            return sorted(m.id for m in page.data)
        raise ValueError(f"Провайдер {name!r} не настроен (нет ключа)")
