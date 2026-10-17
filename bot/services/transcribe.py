"""Транскрипция голосовых сообщений — цепочка с резервом.

Порядок (первый доступный отвечает, упавший — пропускается):
1. ElevenLabs Scribe (ELEVENLABS_API_KEY) — по их бенчмаркам заметно сильнее
   Whisper на узбекском; рекомендуется для узб. голосовых.
2. Groq Whisper (GROQ_API_KEY) — бесплатный, хорош на русском, слабее на узб.

Язык не фиксируем (автоопределение uz/ru). Без ключей функция выключена.
"""
from __future__ import annotations

import logging
import os

from bot.config import get_settings

logger = logging.getLogger(__name__)

_ELEVEN_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class TranscribeService:
    def __init__(self) -> None:
        settings = get_settings()
        self._eleven_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        self._eleven_model = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2").strip()
        self._whisper_model = settings.whisper_model

        key, base = settings.groq_api_key, settings.groq_base_url
        if not key and "api.groq.com" in settings.llm_base_url:
            key, base = settings.llm_api_key, settings.llm_base_url
        self._groq = None
        if key:
            try:
                from openai import AsyncOpenAI

                self._groq = AsyncOpenAI(api_key=key, base_url=base, timeout=60, max_retries=1)
            except Exception:  # noqa: BLE001
                logger.warning("Whisper SDK недоступен")
        chain = [n for n, ok in (("elevenlabs", self._eleven_key), ("groq-whisper", self._groq)) if ok]
        logger.info("Транскрипция: %s", " → ".join(chain) if chain else "выключена")

    def is_enabled(self) -> bool:
        return bool(self._eleven_key) or self._groq is not None

    async def _eleven(self, audio: bytes, filename: str) -> str | None:
        import httpx

        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                _ELEVEN_URL,
                headers={"xi-api-key": self._eleven_key},
                data={"model_id": self._eleven_model},
                files={"file": (filename, audio, "audio/ogg")},
            )
            resp.raise_for_status()
            return (resp.json().get("text") or "").strip() or None

    async def _whisper(self, audio: bytes, filename: str) -> str | None:
        resp = await self._groq.audio.transcriptions.create(model=self._whisper_model, file=(filename, audio))
        return (getattr(resp, "text", "") or "").strip() or None

    async def transcribe(self, audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
        """Расшифровать аудио. None — если всё выключено/упало/пусто."""
        backends = []
        if self._eleven_key:
            backends.append(("elevenlabs", self._eleven))
        if self._groq is not None:
            backends.append(("groq-whisper", self._whisper))
        for name, fn in backends:
            try:
                text = await fn(audio_bytes, filename)
                if text:
                    return text
            except Exception as exc:  # noqa: BLE001 - пробуем следующий
                logger.warning("Транскрипция %s упала: %s — пробую следующий", name, str(exc)[:200])
        return None


_service: TranscribeService | None = None


def get_transcribe_service() -> TranscribeService:
    global _service
    if _service is None:
        _service = TranscribeService()
    return _service
