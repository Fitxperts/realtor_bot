"""Конфигурация приложения и глобальные константы."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

# --- Константы приложения ---
TIMEZONE = "Asia/Tashkent"          # часовой пояс планировщика и напоминаний
BUMP_INTERVAL_DAYS = 3              # интервал автоподнятия объявлений (дни)

# --- Оформление объявления (премиум-эмодзи) ---
# ВАЖНО: подставьте реальные ID кастомных (премиум) эмодзи вашего набора.
# Пока стоят заглушки — Telegram покажет базовые эмодзи из alt-текста.
PREMIUM_EMOJI = {
    "building": "0",   # 🏢 премиум-здание
    "siren": "0",      # 🚨 мигалка (#Срочно)
    "pin": "0",        # 📍 локация
    "door": "0",       # 🚪 дверь (комнаты)
    "tools": "0",      # 🛠 ремонт
    "lightning": "0",  # ⚡️ молния (коммуникации)
    "money": "0",      # 💰 деньги (цена)
    "phone": "0",      # 📞 телефон
}

# Контакты агентства (номер, username) — отображаются в спойлере
AGENCY_CONTACTS = [
    ("+998916993133", "Aziko_3133"),
    ("+998930866466", "Bek_6466"),
]

# Ссылки в подвале объявления
CHANNEL_URL = "https://t.me/fargona_UYJOY0"
BOT_PUBLISH_URL = "https://t.me/fargona_realtor_bot?start=add"

# Водяной знак на фото при публикации в канал (пусто → выключен).
# Можно переопределить переменной окружения WATERMARK_TEXT.
WATERMARK_TEXT = os.getenv("WATERMARK_TEXT", "Фарғона Уйлари")


class Settings:
    def __init__(self) -> None:
        # Telegram
        self.bot_token: str = os.environ["BOT_TOKEN"]
        self.channel_id: str = os.getenv("CHANNEL_ID", "")
        self.admin_ids: list[int] = self._parse_admin_ids(os.getenv("ADMIN_TELEGRAM_ID", ""))

        # Инфраструктура
        self.database_url: str = os.environ["DATABASE_URL"]
        self.redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

        # Webhook (опционально). Если WEBHOOK_URL задан — бот работает через
        # webhook (Telegram сам присылает апдейты, без пауз приёма); иначе — polling.
        self.webhook_url: str = os.getenv("WEBHOOK_URL", "").strip()
        self.webhook_path: str = os.getenv("WEBHOOK_PATH", "/webhook")
        self.webhook_secret: str = os.getenv("WEBHOOK_SECRET", "").strip()
        self.webhook_port: int = int(os.getenv("WEBHOOK_PORT", "8080"))

        # LLM (провайдеро-независимо). Приоритет:
        #  1) LLM_BASE_URL + LLM_API_KEY → любой OpenAI-совместимый провайдер
        #     (Groq / Google Gemini / DeepSeek / OpenRouter / OpenAI …);
        #  2) иначе ANTHROPIC_API_KEY → Anthropic;
        #  3) иначе — офлайн-режим (разбор регулярками, без ИИ).
        # Всё опционально: без ключей бот работает на офлайн-заглушках.
        self.llm_base_url: str = os.getenv("LLM_BASE_URL", "").strip()
        self.llm_api_key: str = os.getenv("LLM_API_KEY", "").strip()
        self.anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.ai_model: str = os.getenv("AI_MODEL", "claude-opus-4-8")
        # Явная цепочка резервных провайдеров «провайдер[:модель]» через запятую,
        # напр. groq:openai/gpt-oss-120b,gemini,openrouter. Пусто → собирается сама
        # из доступных ключей (см. bot/services/llm_providers.py).
        self.llm_chain: list[str] = [x.strip() for x in os.getenv("LLM_CHAIN", "").split(",") if x.strip()]

        # Vision (распознавание объявлений со скринов) — Google Gemini,
        # OpenAI-совместимый эндпоинт. Если ключ пуст — функция просто выключена.
        self.gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
        self.vision_model: str = os.getenv("VISION_MODEL", "gemini-flash-latest")
        self.gemini_base_url: str = os.getenv(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
        ).strip()

        # Транскрипция голосовых (узбекский и др.) — Groq Whisper,
        # OpenAI-совместимый эндпоинт. Пустой ключ → функция выключена.
        self.groq_api_key: str = os.getenv("GROQ_API_KEY", "").strip()
        self.whisper_model: str = os.getenv("WHISPER_MODEL", "whisper-large-v3")
        self.groq_base_url: str = os.getenv(
            "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
        ).strip()

        # Авто-репост из чужих каналов (юзербот Telethon). Пусто/выключено →
        # сервис не запускается. Логин один раз генерирует USERBOT_SESSION.
        self.userbot_enabled: bool = os.getenv("USERBOT_ENABLED", "").strip().lower() in ("1", "true", "yes")
        self.telegram_api_id: int = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
        self.telegram_api_hash: str = os.getenv("TELEGRAM_API_HASH", "").strip()
        self.userbot_session: str = os.getenv("USERBOT_SESSION", "").strip()
        self.source_channels: list[str] = self._parse_channels(os.getenv("SOURCE_CHANNELS", ""))
        # moderate — авто-импорт в модерацию (безопасно); auto — сразу в канал.
        self.autorepost_mode: str = os.getenv("AUTOREPOST_MODE", "moderate").strip().lower()
        self.autorepost_max_photos: int = int(os.getenv("AUTOREPOST_MAX_PHOTOS", "10") or 10)
        self.media_dir: str = os.getenv("MEDIA_DIR", "/app/media").strip()

        # Бэкапы: сервис backup (docker-compose) кладёт дампы в эту папку,
        # бот каждое утро отправляет свежий в Telegram (копия вне сервера).
        self.backup_dir: str = os.getenv("BACKUP_DIR", "/app/backups").strip()
        self.backup_chat_id: int = int(os.getenv("BACKUP_CHAT_ID", "0") or 0)

        # Константы (дублируем на объекте настроек для удобного доступа)
        self.timezone: str = TIMEZONE
        self.bump_interval_days: int = BUMP_INTERVAL_DAYS

    @staticmethod
    def _parse_channels(raw: str) -> list[str]:
        """@name / t.me/name / id → нормализованный список источников."""
        out: list[str] = []
        for part in raw.replace(" ", "").split(","):
            if not part:
                continue
            part = part.rsplit("/", 1)[-1]  # t.me/xxx → xxx
            if part.lstrip("-").isdigit():
                out.append(part)  # числовой id канала
            else:
                out.append(part if part.startswith("@") else "@" + part)
        return out

    @staticmethod
    def _parse_admin_ids(raw: str) -> list[int]:
        return [int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()]

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids


@lru_cache
def get_settings() -> Settings:
    return Settings()
