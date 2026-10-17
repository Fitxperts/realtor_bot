"""Фото-путь (авто-репост, демо) отправляется как файл, а не как сырой путь."""
from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from aiogram.types import FSInputFile

from bot.database import crud
from bot.database.models import ClientDealType, PropertyKind, PropertyStatus, PropertyType
from bot.handlers.admin import _show_property
from bot.services import publisher
from bot.utils.media import photo_input


def test_photo_input(tmp_path):
    path = tmp_path / "p.jpg"
    path.write_bytes(b"jpg")
    assert isinstance(photo_input(str(path)), FSInputFile)
    assert photo_input("AgACAgIAAxkBAAIB") == "AgACAgIAAxkBAAIB"  # file_id как есть


class _Target:
    """Message-заглушка: answer_photo падает, если ему дали не файл."""

    def __init__(self, fail_photo: bool = False) -> None:
        self.fail_photo = fail_photo
        self.photos: list = []
        self.texts: list = []

    async def answer_photo(self, photo, caption=None, reply_markup=None):
        if self.fail_photo:
            raise TelegramBadRequest(method=SendPhoto(chat_id=1, photo="x"), message="wrong file identifier")
        self.photos.append(photo)

    async def answer(self, text, reply_markup=None):
        self.texts.append(text)


async def _prop(session, photos: str):
    return await crud.create_property(
        session, property_kind=PropertyKind.apartment, type=PropertyType.rent,
        status=PropertyStatus.active, district="Киргули", rooms=2, price=300, currency="$", photos=photos,
    )


async def test_admin_card_sends_local_file(session, tmp_path):
    path = tmp_path / "cover.jpg"
    path.write_bytes(b"jpg")
    prop = await _prop(session, str(path))
    target = _Target()
    await _show_property(target, prop, None, "ru")
    assert isinstance(target.photos[0], FSInputFile) and target.texts == []


async def test_admin_card_falls_back_to_text(session):
    prop = await _prop(session, "broken-file-id")
    target = _Target(fail_photo=True)
    await _show_property(target, prop, None, "ru")
    assert len(target.texts) == 1 and "APT_1001" in target.texts[0]


async def test_matching_clients_get_local_photo(session, tmp_path):
    from tests.conftest import FakeBot

    await crud.create_client(session, telegram_id=777, name="Ali", phone="+998901112233",
                             deal_type=ClientDealType.rent, district="Киргули", rooms=2, budget=300, currency="$")
    path = tmp_path / "cover.jpg"
    path.write_bytes(b"jpg")
    prop = await _prop(session, str(path))

    sent = []

    class Bot(FakeBot):
        async def send_photo(self, chat, photo, caption=None, parse_mode=None, reply_markup=None):
            sent.append((chat, photo))

    await publisher._notify_matching_clients(Bot(), session, prop)
    assert sent and sent[0][0] == 777 and isinstance(sent[0][1], FSInputFile)
