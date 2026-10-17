"""Шифрование токенов ботов агентств (Fernet, ключ PLATFORM_SECRET_KEY в .env).

Токен бота = полный контроль над ботом агентства, поэтому в БД он лежит только
зашифрованным; ключ — только в .env сервера. Сгенерировать ключ:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class SecretKeyMissing(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.getenv("PLATFORM_SECRET_KEY", "").strip()
    if not key:
        raise SecretKeyMissing("PLATFORM_SECRET_KEY не задан в .env — без него токены агентств не сохранить")
    return Fernet(key.encode())


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Не удалось расшифровать токен: другой PLATFORM_SECRET_KEY?") from exc
