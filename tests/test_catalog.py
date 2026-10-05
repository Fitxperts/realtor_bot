"""Тесты запросов каталога: счётчик и пагинация активных объявлений."""
from bot.database import crud
from bot.database.models import PropertyKind, PropertyStatus, PropertyType


async def _mk(session, **kw):
    kw.setdefault("property_kind", PropertyKind.apartment)
    kw.setdefault("currency", "сум")
    return await crud.create_property(session, **kw)


async def test_count_and_page_only_active(session):
    await _mk(session, type=PropertyType.rent, status=PropertyStatus.active, price=1, district="Центр")
    await _mk(session, type=PropertyType.sale, status=PropertyStatus.active, price=2, district="Киргули")
    await _mk(session, type=PropertyType.rent, status=PropertyStatus.pending, price=3)  # не в каталоге

    assert await crud.count_active_properties(session) == 2
    assert await crud.count_active_properties(session, prop_type=PropertyType.rent) == 1
    assert await crud.count_active_properties(session, prop_type=PropertyType.sale) == 1

    page = await crud.active_properties_page(session, limit=10)
    assert len(page) == 2
    assert all(p.status == PropertyStatus.active for p in page)


async def test_page_offset(session):
    for i in range(5):
        await _mk(session, type=PropertyType.rent, status=PropertyStatus.active, price=i + 1)
    first = await crud.active_properties_page(session, offset=0, limit=3)
    second = await crud.active_properties_page(session, offset=3, limit=3)
    assert len(first) == 3 and len(second) == 2
    ids = {p.id for p in first} | {p.id for p in second}
    assert len(ids) == 5  # без пересечений
