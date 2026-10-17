"""Фото объекта для отправки: file_id Telegram или локальный файл.

Фото из бота хранятся как file_id, а из авто-репоста и демо — как пути к файлам
на общем томе. Telegram принимает путь только через FSInputFile, поэтому сырой
путь в send_photo/answer_photo даёт ошибку. Все отправки идут через photo_input.
"""
from __future__ import annotations

import os

from aiogram.types import FSInputFile


def photo_input(photo: str):
    """Локальный путь → FSInputFile, иначе file_id как есть."""
    return FSInputFile(photo) if os.path.exists(photo) else photo
