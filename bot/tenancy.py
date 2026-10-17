"""Мультиаренда (white-label): каждое агентство видит только свои данные.

Как устроено:
- у таблиц с данными агентства есть ``agency_id`` (миксин AgencyScoped);
- текущее агентство — в contextvar: его ставит middleware по боту, получившему
  апдейт, и планировщик — перед задачей каждого агентства (agency_scope);
- SQLAlchemy сам добавляет «WHERE agency_id = текущее» во ВСЕ SELECT, UPDATE и
  DELETE по этим таблицам (событие do_orm_execute), а при INSERT сам проставляет
  agency_id — запрос, «забывший» фильтр, не может увидеть чужие данные;
- без явного агентства — агентство №1 (однобот-режим, как было до SaaS);
- запросы на всю платформу (супер-админка, генерация ID) — явно, через
  ``.execution_options(**ALL_AGENCIES)``.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import BigInteger, ForeignKey, event
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    declared_attr,
    mapped_column,
    with_loader_criteria,
)

DEFAULT_AGENCY_ID = 1
ALL_AGENCIES = {"all_agencies": True}

_current: ContextVar[int | None] = ContextVar("current_agency", default=None)


def current_agency_id() -> int:
    return _current.get() or DEFAULT_AGENCY_ID


def set_agency(agency_id: int | None):
    """Поставить агентство для текущего контекста (возвращает токен для reset)."""
    return _current.set(agency_id)


def reset_agency(token) -> None:
    _current.reset(token)


@contextmanager
def agency_scope(agency_id: int) -> Iterator[None]:
    token = _current.set(agency_id)
    try:
        yield
    finally:
        _current.reset(token)


class AgencyScoped:
    """Миксин для таблиц с данными агентства."""

    @declared_attr
    def agency_id(cls) -> Mapped[int]:  # noqa: N805 - declared_attr
        return mapped_column(BigInteger, ForeignKey("agencies.id"), index=True, default=current_agency_id)


@event.listens_for(Session, "do_orm_execute")
def _scope_to_agency(state: ORMExecuteState) -> None:
    if state.execution_options.get("all_agencies"):
        return
    if state.is_select or state.is_update or state.is_delete:
        agency_id = current_agency_id()
        state.statement = state.statement.options(
            with_loader_criteria(AgencyScoped, lambda cls: cls.agency_id == agency_id, include_aliases=True)
        )
