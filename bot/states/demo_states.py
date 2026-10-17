"""FSM-состояние демо-бота: «Хочу такой бот» — ждём телефон."""
from aiogram.fsm.state import State, StatesGroup


class DemoForm(StatesGroup):
    phone = State()
