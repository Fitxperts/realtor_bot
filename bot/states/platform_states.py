"""FSM подключения агентства в боте платформы."""
from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    name = State()
    city = State()
    token = State()
    channel = State()
    contacts = State()
    confirm = State()
