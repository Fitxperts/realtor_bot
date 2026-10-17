"""Тесты Vision (скрины) и транскрипции (голос): выключенное состояние и
рабочий путь с подставным клиентом (без сети)."""
from types import SimpleNamespace

from bot.services import transcribe, vision
from bot.services.llm_providers import LLMRouter, ProviderSpec


def _router_with(fake_client) -> LLMRouter:
    router = LLMRouter([ProviderSpec("gemini", "test-model", "k", "https://example.invalid/v1")])
    router.providers[0].client = fake_client
    return router


# --- Vision -----------------------------------------------------------------
async def test_vision_disabled_returns_none():
    svc = vision.VisionService()
    svc.router = LLMRouter([])  # без ключа
    assert svc.is_enabled() is False
    assert await svc.extract_listing_text(b"\x89PNG...") is None


async def test_vision_extracts_text_with_mock():
    class FakeCompletions:
        async def create(self, **kwargs):
            content = kwargs["messages"][0]["content"]
            assert any(part["type"] == "image_url" for part in content)  # картинка ушла
            msg = SimpleNamespace(content="2 хона ижара Киргули 450 минг")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    svc = vision.VisionService()
    svc.router = _router_with(SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())))
    assert svc.is_enabled() is True
    assert "Киргули" in await svc.extract_listing_text(b"imgbytes")


async def test_vision_empty_answer_is_none():
    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="   "))])

    svc = vision.VisionService()
    svc.router = _router_with(SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())))
    assert await svc.extract_listing_text(b"x") is None


# --- Transcription ----------------------------------------------------------
async def test_transcribe_disabled_returns_none():
    svc = transcribe.TranscribeService()
    svc._groq, svc._eleven_key = None, ""
    assert svc.is_enabled() is False
    assert await svc.transcribe(b"ogg") is None


async def test_transcribe_with_mock():
    class FakeTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(text="сотилади ховли участка")

    svc = transcribe.TranscribeService()
    svc._eleven_key = ""
    svc._groq = SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions()))
    assert svc.is_enabled() is True
    assert "ховли" in await svc.transcribe(b"audio")


async def test_transcribe_falls_back_from_elevenlabs_to_whisper():
    class FakeTranscriptions:
        async def create(self, **kwargs):
            return SimpleNamespace(text="запасной whisper")

    svc = transcribe.TranscribeService()
    svc._eleven_key = "bad"

    async def broken(*a, **k):
        raise RuntimeError("401 unauthorized")

    svc._eleven = broken
    svc._groq = SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions()))
    assert await svc.transcribe(b"audio") == "запасной whisper"
