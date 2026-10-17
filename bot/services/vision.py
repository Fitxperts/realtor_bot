"""Распознавание объявлений со скриншотов (Vision) — с резервом.

Риелторы часто получают объявления партнёров картинкой. Vision извлекает из
изображения ТЕКСТ объявления, который дальше разбирается тем же parse_listing.

Цепочка: Gemini (VISION_MODEL) → OpenRouter (если задан OPENROUTER_VISION_MODEL).
Без ключей функция выключена (is_enabled() == False) — бот работает как раньше.
"""
from __future__ import annotations

import base64
import logging
import os

from bot.config import get_settings
from bot.services.llm_providers import LLMRouter, ProviderSpec

logger = logging.getLogger(__name__)

_PROMPT = (
    "На изображении — объявление о недвижимости (скриншот из чата, канала или "
    "фото листовки). Извлеки ВЕСЬ относящийся к объявлению текст: тип сделки "
    "(аренда/продажа), цену с валютой, комнаты, район/массив, адрес или ориентир, "
    "площадь, этаж, телефон, описание. Верни просто связный текст объявления как "
    "есть, без своих комментариев. Если на картинке нет объявления о недвижимости — "
    "верни пустую строку."
)


def _vision_specs(settings) -> list[ProviderSpec]:
    specs: list[ProviderSpec] = []
    key, base = settings.gemini_api_key, settings.gemini_base_url
    if not key and "generativelanguage.googleapis.com" in settings.llm_base_url:
        key, base = settings.llm_api_key, settings.llm_base_url
    if key:
        specs.append(ProviderSpec("gemini", settings.vision_model, key, base))
    or_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    or_model = os.getenv("OPENROUTER_VISION_MODEL", "").strip()
    if or_key and or_model:
        specs.append(ProviderSpec("openrouter", or_model, or_key, "https://openrouter.ai/api/v1"))
    return specs


class VisionService:
    def __init__(self) -> None:
        self.router = LLMRouter(_vision_specs(get_settings()), label="vision")

    def is_enabled(self) -> bool:
        return self.router.enabled

    async def extract_listing_text(self, image_bytes: bytes, mime: str = "image/jpeg") -> str | None:
        """Вернуть текст объявления с картинки или None (выключено/ошибка/пусто)."""
        if not self.router.enabled:
            return None
        data_uri = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }]
        text = await self.router.complete(messages, max_tokens=800, openai_only=True)
        return (text or "").strip() or None


_service: VisionService | None = None


def get_vision_service() -> VisionService:
    global _service
    if _service is None:
        _service = VisionService()
    return _service
