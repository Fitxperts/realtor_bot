"""SQLAlchemy-модели РиелторБота.

Пять таблиц:
- properties    — объекты недвижимости (PK вида ``APT_1001``)
- clients       — заявки клиентов (PK вида ``CLT_001``); заявка = подписка на поиск
- meetings      — встречи и просмотры
- bot_users     — пользователи бота
- search_alerts — какие объекты уже присланы по подпискам (без повторов, лимит в день)
- events        — события для аналитики (просмотры, клики «заявка», заявки, публикации)
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from bot.utils import timeutils


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Перечисления
# ---------------------------------------------------------------------------
class PropertyType(str, enum.Enum):
    rent = "rent"
    sale = "sale"


class PropertyStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    rented = "rented"
    sold = "sold"
    archived = "archived"


class PropertyKind(str, enum.Enum):
    apartment = "apartment"
    house = "house"
    land = "land"
    commercial = "commercial"


class ClientDealType(str, enum.Enum):
    rent = "rent"
    buy = "buy"


class ClientStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    showing_set = "showing_set"
    showing_done = "showing_done"
    deal = "deal"
    closed = "closed"


class MeetingStatus(str, enum.Enum):
    planned = "planned"
    done = "done"
    cancelled = "cancelled"


class UserRole(str, enum.Enum):
    client = "client"
    owner = "owner"
    admin = "admin"


# ---------------------------------------------------------------------------
# Таблицы
# ---------------------------------------------------------------------------
class Property(Base):
    """Объект недвижимости."""

    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # APT_1001
    type: Mapped[PropertyType] = mapped_column(Enum(PropertyType))
    status: Mapped[PropertyStatus] = mapped_column(
        Enum(PropertyStatus), default=PropertyStatus.pending, index=True
    )
    property_kind: Mapped[PropertyKind] = mapped_column(Enum(PropertyKind))

    owner_name: Mapped[str | None] = mapped_column(String(255))
    owner_phone: Mapped[str | None] = mapped_column(String(32))
    owner_telegram_id: Mapped[int | None] = mapped_column(BigInteger, index=True)

    district: Mapped[str | None] = mapped_column(String(128), index=True)
    address: Mapped[str | None] = mapped_column(String(255))

    rooms: Mapped[int | None] = mapped_column(Integer)
    area: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    floors: Mapped[int | None] = mapped_column(Integer)
    renovation: Mapped[str | None] = mapped_column(String(128))

    furniture: Mapped[bool] = mapped_column(Boolean, default=False)
    appliances: Mapped[bool] = mapped_column(Boolean, default=False)
    gas: Mapped[bool] = mapped_column(Boolean, default=False)
    water: Mapped[bool] = mapped_column(Boolean, default=False)
    electricity: Mapped[bool] = mapped_column(Boolean, default=False)
    internet: Mapped[bool] = mapped_column(Boolean, default=False)
    docs: Mapped[bool] = mapped_column(Boolean, default=False)
    mortgage: Mapped[bool] = mapped_column(Boolean, default=False)

    price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8), default="₽")
    negotiable: Mapped[bool] = mapped_column(Boolean, default=False)

    description: Mapped[str | None] = mapped_column(Text)

    photos: Mapped[str | None] = mapped_column(Text)  # file_id фото через запятую
    video: Mapped[str | None] = mapped_column(String(255))  # file_id видео

    channel_post_id: Mapped[int | None] = mapped_column(Integer)
    last_bump: Mapped[dt.datetime | None] = mapped_column(DateTime())
    # Проверка актуальности: когда спросили «ещё актуально?» и когда подтвердили
    freshness_asked_at: Mapped[dt.datetime | None] = mapped_column(DateTime())
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime())
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(), default=timeutils.now
    )

    meetings: Mapped[list[Meeting]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )

    @property
    def photo_list(self) -> list[str]:
        return [p for p in (self.photos or "").split(",") if p]


class Client(Base):
    """Заявка клиента на подбор недвижимости."""

    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # CLT_001
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)

    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))

    deal_type: Mapped[ClientDealType] = mapped_column(Enum(ClientDealType))
    district: Mapped[str | None] = mapped_column(String(128))
    rooms: Mapped[int | None] = mapped_column(Integer)
    budget: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8), default="₽")
    residents: Mapped[str | None] = mapped_column(String(128))
    move_date: Mapped[dt.date | None] = mapped_column(Date)

    status: Mapped[ClientStatus] = mapped_column(
        Enum(ClientStatus), default=ClientStatus.new, index=True
    )
    # Присылать ли новые подходящие объекты (подписка на поиск; «Отписаться» → False)
    notify: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(), default=timeutils.now
    )

    meetings: Mapped[list[Meeting]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class Meeting(Base):
    """Встреча / просмотр объекта клиентом."""

    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    property_id: Mapped[str] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )

    datetime: Mapped[dt.datetime] = mapped_column(DateTime(), index=True)
    status: Mapped[MeetingStatus] = mapped_column(
        Enum(MeetingStatus), default=MeetingStatus.planned, index=True
    )

    reminded_1d: Mapped[bool] = mapped_column(Boolean, default=False)
    reminded_2h: Mapped[bool] = mapped_column(Boolean, default=False)
    reminded_30m: Mapped[bool] = mapped_column(Boolean, default=False)

    client: Mapped[Client] = relationship(back_populates="meetings")
    property: Mapped[Property] = relationship(back_populates="meetings")


class BotUser(Base):
    """Пользователь Telegram-бота."""

    __tablename__ = "bot_users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.client)
    role_chosen: Mapped[bool] = mapped_column(Boolean, default=False)  # выбрал ли роль явно
    language: Mapped[str | None] = mapped_column(String(2))  # uz | ru | en (None → ещё не выбран)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(), default=timeutils.now
    )


class SearchAlert(Base):
    """Объект, присланный пользователю по подписке: без повторов и с лимитом в сутки."""

    __tablename__ = "search_alerts"
    __table_args__ = (UniqueConstraint("telegram_id", "property_id", name="uq_alert_user_property"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=timeutils.now, index=True)


class Event(Base):
    """Событие для аналитики агентства. Без внешних ключей: статистика переживает удаление объекта."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    property_id: Mapped[str | None] = mapped_column(String(20), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(), default=timeutils.now, index=True)
