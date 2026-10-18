"""Оплата подписки агентства: Payme (Merchant API) и Click (SHOP-API).

Счёт (Payment) выставляет бот платформы (/pay): тариф × месяцы → сумма в сумах.
Ссылки ведут на страницы оплаты Payme / Click; те присылают серверу запросы:

- Payme: POST /billing/payme, JSON-RPC, авторизация Basic «Paycom:<ключ кассы>»,
  суммы в тийинах; методы CheckPerformTransaction, CreateTransaction,
  PerformTransaction, CancelTransaction, CheckTransaction, GetStatement.
  Состояния 1 / 2 / -1 / -2, тайм-аут неоплаченной транзакции — 12 часов.
- Click: POST /billing/click/prepare и /billing/click/complete (форма),
  подпись sign_string = md5(click_trans_id + service_id + SECRET_KEY +
  merchant_trans_id [+ merchant_prepare_id] + amount + action + sign_time).

Оплачено → agencies.paid_until продлевается (billing.extend), тариф меняется,
реестр настроек агентства обновляется на ходу, админам агентства — сообщение.
Payme отменил оплаченную транзакцию (возврат) → срок откатывается обратно.

Повторные запросы от платёжных систем безопасны: ответ повторяется, срок
продлевается ровно один раз.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.models import Agency, Payment
from bot.services import billing

logger = logging.getLogger(__name__)

PAYME_TIMEOUT_MS = 12 * 60 * 60 * 1000
MONTH_OPTIONS = (1, 3, 6, 12)

# Состояния счёта (совпадают с состояниями транзакции Payme)
NEW, CREATED, PERFORMED, CANCELLED, CANCELLED_AFTER_PERFORM = 0, 1, 2, -1, -2
# Причина отмены у Payme: 4 — тайм-аут, 5 — возврат средств
REASON_TIMEOUT = 4


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Настройки (.env)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PaymeConfig:
    merchant_id: str
    key: str
    test: bool

    @property
    def checkout_url(self) -> str:
        return "https://checkout.test.paycom.uz" if self.test else "https://checkout.paycom.uz"


@dataclass(frozen=True)
class ClickConfig:
    service_id: str
    merchant_id: str
    secret_key: str


def payme_config() -> PaymeConfig | None:
    merchant_id, key = os.getenv("PAYME_MERCHANT_ID", "").strip(), os.getenv("PAYME_KEY", "").strip()
    if not (merchant_id and key):
        return None
    return PaymeConfig(merchant_id, key, os.getenv("PAYME_TEST", "").strip().lower() in ("1", "true", "yes"))


def click_config() -> ClickConfig | None:
    values = [os.getenv(n, "").strip() for n in ("CLICK_SERVICE_ID", "CLICK_MERCHANT_ID", "CLICK_SECRET_KEY")]
    return ClickConfig(*values) if all(values) else None


def money(uzs: int) -> str:
    return f"{uzs:,} сум".replace(",", " ")


def price(plan: str, months: int) -> int:
    return billing.PLANS[plan]["uzs"] * months


# ---------------------------------------------------------------------------
# Счёт и ссылки на оплату
# ---------------------------------------------------------------------------
async def create_invoice(session: AsyncSession, agency_id: int, plan: str, months: int,
                         payer_id: int | None = None) -> Payment:
    if plan not in billing.PLANS or months not in MONTH_OPTIONS:
        raise ValueError(f"Неизвестный тариф/срок: {plan} × {months}")
    payment = Payment(agency_id=agency_id, plan=plan, months=months, amount=price(plan, months),
                      payer_id=payer_id, state=NEW)
    session.add(payment)
    await session.commit()
    return payment


async def record_manual(session: AsyncSession, agency_id: int, plan: str, months: int,
                        payer_id: int | None = None) -> Agency | None:
    """Оплата наличными / переводом: владелец платформы отмечает её сам (/paid в боте платформы)."""
    if plan not in billing.PLANS or not 1 <= months <= 24:
        raise ValueError(f"Неизвестный тариф/срок: {plan} × {months}")
    payment = Payment(agency_id=agency_id, plan=plan, months=months, amount=price(plan, months), payer_id=payer_id,
                      provider="manual", state=PERFORMED, create_time=now_ms(), perform_time=now_ms())
    session.add(payment)
    agency = await _apply_paid(session, payment)
    if agency is None:
        await session.rollback()
        return None
    await session.commit()
    await _after_commit(agency, payment)
    return agency


def payme_link(payment: Payment, return_url: str = "") -> str | None:
    """Ссылка на оплату Payme: base64 от «m=<касса>;ac.order_id=<счёт>;a=<тийины>;c=<возврат>»."""
    config = payme_config()
    if config is None:
        return None
    params = f"m={config.merchant_id};ac.order_id={payment.id};a={payment.amount * 100}"
    if return_url:
        params += f";c={return_url}"
    return f"{config.checkout_url}/{base64.b64encode(params.encode()).decode()}"


def click_link(payment: Payment, return_url: str = "") -> str | None:
    config = click_config()
    if config is None:
        return None
    from urllib.parse import urlencode

    query = {"service_id": config.service_id, "merchant_id": config.merchant_id,
             "amount": f"{payment.amount}.00", "transaction_param": payment.id}
    if return_url:
        query["return_url"] = return_url
    return f"https://my.click.uz/services/pay?{urlencode(query)}"


# ---------------------------------------------------------------------------
# Оплачено / возврат — общее для Payme и Click
# ---------------------------------------------------------------------------
async def _apply_paid(session: AsyncSession, payment: Payment) -> Agency | None:
    agency = await session.get(Agency, payment.agency_id)
    if agency is None:
        return None
    agency.paid_until = billing.extend(agency.paid_until, payment.months)
    agency.plan = payment.plan
    agency.billing_notice = None           # следующее напоминание — уже про новый срок
    return agency


async def _apply_refund(session: AsyncSession, payment: Payment) -> Agency | None:
    agency = await session.get(Agency, payment.agency_id)
    if agency is not None and agency.paid_until is not None:
        agency.paid_until -= dt.timedelta(days=30 * payment.months)
        agency.billing_notice = None
    return agency


async def _after_commit(agency: Agency | None, payment: Payment, refunded: bool = False) -> None:
    """Обновить настройки агентства на ходу и сообщить: агентству — его ботом, платформе — основным."""
    if agency is None:
        return
    from bot import bots
    from bot.agency_config import from_row, register
    from bot.services.notify import notify_admins, notify_platform
    from bot.tenancy import DEFAULT_AGENCY_ID, agency_scope

    register(from_row(agency))
    plan = billing.PLANS.get(payment.plan, {}).get("name", payment.plan)
    until = f"{agency.paid_until:%d.%m.%Y}" if agency.paid_until else "—"
    if refunded:
        to_agency = f"↩️ Оплата подписки возвращена. Подписка действует до {until}."
        to_platform = f"↩️ Возврат: агентство #{agency.id} {agency.name}, счёт №{payment.id}, {money(payment.amount)}"
    else:
        to_agency = f"✅ Оплата получена: тариф «{plan}», {payment.months} мес. Подписка действует до {until}."
        to_platform = (f"💰 Оплата ({payment.provider}): агентство #{agency.id} {agency.name}, «{plan}» × "
                       f"{payment.months} мес, {money(payment.amount)}, до {until}")
    agency_bot = bots.bot_for(agency.id)
    if agency_bot is not None:
        with agency_scope(agency.id):
            await notify_admins(agency_bot, to_agency)
    main_bot = bots.bot_for(DEFAULT_AGENCY_ID)
    if main_bot is not None:
        await notify_platform(main_bot, to_platform)


async def _get_locked(session: AsyncSession, payment_id: Any) -> Payment | None:
    try:
        pid = int(str(payment_id))
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    # FOR UPDATE: повторный запрос платёжной системы ждёт первый — продление ровно одно
    return (await session.scalars(select(Payment).where(Payment.id == pid).with_for_update())).first()


# ---------------------------------------------------------------------------
# Payme Merchant API
# ---------------------------------------------------------------------------
class PaymeError(Exception):
    def __init__(self, code: int, ru: str, uz: str = "", en: str = "", data: str | None = None) -> None:
        super().__init__(ru)
        self.code, self.data = code, data
        self.message = {"ru": ru, "uz": uz or ru, "en": en or ru}


def _payme_err_account(text_ru: str = "Счёт не найден", uz: str = "Hisob topilmadi",
                       en: str = "Order not found") -> PaymeError:
    return PaymeError(-31050, text_ru, uz, en, data="order_id")


def payme_authorized(header: str | None, key: str) -> bool:
    if not header or not header.strip().lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.strip()[6:].strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return False
    return hmac.compare_digest(decoded, f"Paycom:{key}")


async def _payme_order(session: AsyncSession, params: dict) -> Payment:
    """Счёт из params.account.order_id + проверка суммы (CheckPerform / Create)."""
    order_id = (params.get("account") or {}).get("order_id")
    payment = await _get_locked(session, order_id)
    if payment is None:
        raise _payme_err_account()
    if not isinstance(params.get("amount"), int | float) or int(params["amount"]) != payment.amount * 100:
        raise PaymeError(-31001, "Неверная сумма", "Notoʻgʻri summa", "Incorrect amount")
    return payment


def _payme_tx_view(payment: Payment) -> dict:
    return {"create_time": payment.create_time or 0, "perform_time": payment.perform_time or 0,
            "cancel_time": payment.cancel_time or 0, "transaction": str(payment.id),
            "state": payment.state, "reason": payment.reason}


async def _payme_find_tx(session: AsyncSession, params: dict) -> Payment:
    ext_id = params.get("id")
    payment = None
    if isinstance(ext_id, str) and ext_id:
        payment = (await session.scalars(select(Payment).where(
            Payment.provider == "payme", Payment.ext_id == ext_id).with_for_update())).first()
    if payment is None:
        raise PaymeError(-31003, "Транзакция не найдена", "Tranzaksiya topilmadi", "Transaction not found")
    return payment


def _expired(payment: Payment) -> bool:
    return payment.state == CREATED and now_ms() - (payment.create_time or 0) > PAYME_TIMEOUT_MS


def _cancel(payment: Payment, reason: int | None) -> None:
    payment.state = CANCELLED_AFTER_PERFORM if payment.state == PERFORMED else CANCELLED
    payment.reason = reason
    payment.cancel_time = now_ms()


async def payme_dispatch(session: AsyncSession, method: str, params: dict) -> dict:
    """Результат метода Merchant API или PaymeError."""
    if method == "CheckPerformTransaction":
        payment = await _payme_order(session, params)
        if payment.state != NEW:
            # счёт уже в работе / оплачен / отменён — новая оплата по нему невозможна
            raise PaymeError(-31008, "Счёт уже оплачивается или закрыт", "Hisob band yoki yopilgan",
                             "Order is busy or closed")
        return {"allow": True}

    if method == "CreateTransaction":
        ext_id = params.get("id")
        existing = None
        if isinstance(ext_id, str) and ext_id:
            existing = (await session.scalars(select(Payment).where(
                Payment.provider == "payme", Payment.ext_id == ext_id).with_for_update())).first()
        if existing is not None:                                   # повтор того же запроса
            if existing.state != CREATED:
                raise PaymeError(-31008, "Транзакция не активна", "Tranzaksiya faol emas", "Transaction is not active")
            if _expired(existing):
                _cancel(existing, REASON_TIMEOUT)
                await session.commit()
                raise PaymeError(-31008, "Время ожидания оплаты истекло", "Toʻlov vaqti tugadi", "Transaction expired")
            return {"create_time": existing.create_time, "transaction": str(existing.id), "state": existing.state}
        payment = await _payme_order(session, params)
        if payment.state in (CANCELLED, CANCELLED_AFTER_PERFORM):
            raise PaymeError(-31008, "Счёт закрыт", "Hisob yopilgan", "Order is closed")
        if payment.state != NEW:                                   # по счёту уже другая транзакция
            raise _payme_err_account("Счёт уже оплачивается или закрыт", "Hisob band yoki yopilgan",
                                     "Order is busy or closed")
        if not isinstance(ext_id, str) or not ext_id or not isinstance(params.get("time"), int | float):
            raise PaymeError(-32600, "Неверные параметры", en="Invalid params")
        if now_ms() - int(params["time"]) >= PAYME_TIMEOUT_MS:
            raise PaymeError(-31008, "Время ожидания оплаты истекло", "Toʻlov vaqti tugadi", "Transaction expired")
        payment.provider, payment.ext_id, payment.ext_time = "payme", ext_id, int(params["time"])
        payment.state, payment.create_time = CREATED, now_ms()
        await session.commit()
        return {"create_time": payment.create_time, "transaction": str(payment.id), "state": payment.state}

    if method == "PerformTransaction":
        payment = await _payme_find_tx(session, params)
        if payment.state == PERFORMED:                             # повтор — тот же ответ, без второго продления
            return {"transaction": str(payment.id), "perform_time": payment.perform_time, "state": payment.state}
        if payment.state != CREATED:
            raise PaymeError(-31008, "Транзакция отменена", "Tranzaksiya bekor qilingan", "Transaction cancelled")
        if _expired(payment):
            _cancel(payment, REASON_TIMEOUT)
            await session.commit()
            raise PaymeError(-31008, "Время ожидания оплаты истекло", "Toʻlov vaqti tugadi", "Transaction expired")
        payment.state, payment.perform_time = PERFORMED, now_ms()
        agency = await _apply_paid(session, payment)
        await session.commit()
        await _after_commit(agency, payment)
        return {"transaction": str(payment.id), "perform_time": payment.perform_time, "state": payment.state}

    if method == "CancelTransaction":
        payment = await _payme_find_tx(session, params)
        if payment.state in (CREATED, PERFORMED):
            was_paid = payment.state == PERFORMED
            reason = params.get("reason")
            _cancel(payment, int(reason) if isinstance(reason, int | float) else None)
            agency = await _apply_refund(session, payment) if was_paid else None
            await session.commit()
            if was_paid:
                await _after_commit(agency, payment, refunded=True)
        return {"transaction": str(payment.id), "cancel_time": payment.cancel_time, "state": payment.state}

    if method == "CheckTransaction":
        return _payme_tx_view(await _payme_find_tx(session, params))

    if method == "GetStatement":
        start, end = params.get("from"), params.get("to")
        if not isinstance(start, int | float) or not isinstance(end, int | float):
            raise PaymeError(-32600, "Неверный период", en="Invalid period")
        rows = await session.scalars(select(Payment).where(
            Payment.provider == "payme", Payment.ext_time >= int(start), Payment.ext_time <= int(end)
        ).order_by(Payment.ext_time))
        return {"transactions": [
            {"id": p.ext_id, "time": p.ext_time, "amount": p.amount * 100, "account": {"order_id": str(p.id)},
             **_payme_tx_view(p), "receivers": None} for p in rows]}

    if method == "ChangePassword":
        # ключ кассы хранится в .env (PAYME_KEY) — меняется там, вручную
        raise PaymeError(-32400, "Смена ключа через API не поддерживается: обновите PAYME_KEY",
                         en="Change PAYME_KEY manually")

    raise PaymeError(-32601, "Метод не найден", "Metod topilmadi", "Method not found", data=method)


async def payme_handle(maker: async_sessionmaker, auth_header: str | None, body: bytes) -> dict:
    """Полный ответ JSON-RPC (HTTP всегда 200 — так требует Payme)."""
    request_id = None
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError
        request_id = payload.get("id")
    except (ValueError, UnicodeDecodeError):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": {"ru": "Ошибка разбора JSON", "uz": "JSON xato",
                                                      "en": "Parse error"}}}
    config = payme_config()
    try:
        if config is None or not payme_authorized(auth_header, config.key):
            raise PaymeError(-32504, "Недостаточно прав", "Ruxsat yoʻq", "Insufficient privilege")
        method, params = payload.get("method"), payload.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            raise PaymeError(-32600, "Неверный запрос JSON-RPC", en="Invalid JSON-RPC object")
        async with maker() as session:
            result = await payme_dispatch(session, method, params)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except PaymeError as exc:
        error: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.data:
            error["data"] = exc.data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}
    except Exception:  # noqa: BLE001
        logger.exception("Payme: сбой обработки запроса")
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32400, "message": {"ru": "Системная ошибка", "uz": "Tizim xatosi",
                                                      "en": "Internal error"}}}


# ---------------------------------------------------------------------------
# Click SHOP-API
# ---------------------------------------------------------------------------
CLICK_PREPARE, CLICK_COMPLETE = 0, 1


def click_sign(form: dict, secret_key: str) -> str:
    parts = [form.get("click_trans_id", ""), form.get("service_id", ""), secret_key, form.get("merchant_trans_id", "")]
    if str(form.get("action")) == str(CLICK_COMPLETE):
        parts.append(form.get("merchant_prepare_id", ""))
    parts += [form.get("amount", ""), form.get("action", ""), form.get("sign_time", "")]
    return hashlib.md5("".join(str(p) for p in parts).encode()).hexdigest()


def _click_reply(form: dict, error: int, note: str, **extra: Any) -> dict:
    return {"click_trans_id": form.get("click_trans_id"), "merchant_trans_id": form.get("merchant_trans_id"),
            "error": error, "error_note": note, **extra}


async def click_dispatch(session: AsyncSession, form: dict, action: int) -> dict:
    config = click_config()
    if config is None or str(form.get("service_id", "")) != config.service_id or \
            not hmac.compare_digest(click_sign(form, config.secret_key), str(form.get("sign_string", ""))):
        return _click_reply(form, -1, "SIGN CHECK FAILED!")
    if str(form.get("action")) != str(action):
        return _click_reply(form, -3, "Action not found")
    payment = await _get_locked(session, form.get("merchant_trans_id"))
    if payment is None:
        return _click_reply(form, -5, "User does not exist")
    try:
        amount_ok = abs(float(form.get("amount", "")) - payment.amount) < 0.01
    except ValueError:
        amount_ok = False
    if not amount_ok:
        return _click_reply(form, -2, "Incorrect parameter amount")
    click_trans_id = str(form.get("click_trans_id", ""))

    if action == CLICK_PREPARE:
        if payment.state == PERFORMED:
            return _click_reply(form, -4, "Already paid")
        if payment.state in (CANCELLED, CANCELLED_AFTER_PERFORM):
            return _click_reply(form, -9, "Transaction cancelled")
        if payment.state == CREATED and not (payment.provider == "click" and payment.ext_id == click_trans_id):
            return _click_reply(form, -4, "Already paid")       # счёт уже оплачивается другой транзакцией
        payment.provider, payment.ext_id, payment.state = "click", click_trans_id, CREATED
        payment.create_time = payment.create_time or now_ms()
        await session.commit()
        return _click_reply(form, 0, "Success", merchant_prepare_id=payment.id)

    # Complete
    if str(form.get("merchant_prepare_id", "")) != str(payment.id) or payment.provider != "click" \
            or payment.ext_id != click_trans_id:
        return _click_reply(form, -6, "Transaction does not exist")
    if payment.state == PERFORMED:
        return _click_reply(form, -4, "Already paid")
    if payment.state in (CANCELLED, CANCELLED_AFTER_PERFORM):
        return _click_reply(form, -9, "Transaction cancelled")
    try:
        click_error = int(form.get("error", 0))
    except ValueError:
        click_error = 0
    if click_error < 0:                                          # Click сообщает: оплата не прошла
        _cancel(payment, None)
        await session.commit()
        return _click_reply(form, -9, "Transaction cancelled")
    payment.state, payment.perform_time = PERFORMED, now_ms()
    agency = await _apply_paid(session, payment)
    await session.commit()
    await _after_commit(agency, payment)
    return _click_reply(form, 0, "Success", merchant_confirm_id=payment.id)


# ---------------------------------------------------------------------------
# HTTP-маршруты
# ---------------------------------------------------------------------------
def register_routes(app: web.Application, maker_factory) -> None:
    """maker_factory() → async_sessionmaker (берётся при запросе — удобно для тестов)."""

    async def payme(request: web.Request) -> web.Response:
        body = await request.read()
        return web.json_response(await payme_handle(maker_factory(), request.headers.get("Authorization"), body))

    def click(action: int):
        async def handler(request: web.Request) -> web.Response:
            form = dict(await request.post())
            try:
                async with maker_factory()() as session:
                    reply = await click_dispatch(session, {k: str(v) for k, v in form.items()}, action)
            except Exception:  # noqa: BLE001
                logger.exception("Click: сбой обработки запроса")
                reply = _click_reply(form, -7, "Failed to update user")
            return web.json_response(reply)
        return handler

    app.router.add_post("/billing/payme", payme)
    app.router.add_post("/billing/click/prepare", click(CLICK_PREPARE))
    app.router.add_post("/billing/click/complete", click(CLICK_COMPLETE))
