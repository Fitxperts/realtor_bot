"""Тексты бота платформы (узбекский / русский)."""
from __future__ import annotations

TEXTS: dict[str, dict[str, str]] = {
    "welcome": {
        "uz": "👋 <b>RieltorBot</b> — koʻchmas mulk agentligi uchun Telegram-bot: eʼlonlar bitta xabar bilan, "
              "kanalga chiroyli post, arizalar va CRM.\n\nUlash 5 daqiqa:\n1) @BotFather’da bot yarating\n"
              "2) kanal oching va botni kanalga administrator qiling\n3) shu yerga token va kanalni yuboring",
        "ru": "👋 <b>РиелторБот</b> — Telegram-бот для агентства недвижимости: объявления одним сообщением, "
              "красивые посты в канал, заявки и CRM.\n\nПодключение за 5 минут:\n1) создайте бота в @BotFather\n"
              "2) создайте канал и добавьте бота в канал администратором\n3) пришлите сюда токен и канал",
    },
    "btn_connect": {"uz": "🚀 Agentlikni ulash", "ru": "🚀 Подключить агентство"},
    "btn_launch": {"uz": "✅ Ishga tushirish", "ru": "✅ Запустить"},
    "btn_cancel": {"uz": "❌ Bekor qilish", "ru": "❌ Отмена"},
    "btn_skip": {"uz": "⏭ Oʻtkazib yuborish", "ru": "⏭ Пропустить"},
    "ask_name": {"uz": "Agentlik nomi (postlarda shunday chiqadi):", "ru": "Название агентства (так будет в постах):"},
    "ask_city": {"uz": "Qaysi shahar?", "ru": "Какой город?"},
    "ask_city_text": {"uz": "Shahar nomini yozing:", "ru": "Напишите название города:"},
    "ask_token": {
        "uz": "@BotFather bergan bot tokenini yuboring (masalan <code>1234567890:AAH…</code>).\n"
              "Xavfsizlik uchun token yozilgan xabarni darhol oʻchiraman.",
        "ru": "Пришлите токен бота от @BotFather (вида <code>1234567890:AAH…</code>).\n"
              "Для безопасности сообщение с токеном сразу удалю.",
    },
    "token_bad": {"uz": "Bu token emasga oʻxshaydi. @BotFather’dan nusxa oling.",
                  "ru": "Не похоже на токен. Скопируйте его из @BotFather."},
    "token_invalid": {"uz": "Telegram bu tokenni qabul qilmadi. Tekshirib, qayta yuboring.",
                      "ru": "Telegram не принял этот токен. Проверьте и пришлите снова."},
    "token_taken": {"uz": "Bu bot allaqachon ulangan.", "ru": "Этот бот уже подключён."},
    "token_ok": {"uz": "✅ Bot @{username} topildi. Token yozilgan xabar oʻchirildi.",
                 "ru": "✅ Бот @{username} найден. Сообщение с токеном удалено."},
    "ask_channel": {
        "uz": "Kanalingiz @username’ini yuboring. Bot kanalda «Xabar joylash» huquqi bilan administrator boʻlishi kerak.",
        "ru": "Пришлите @username вашего канала. Бот должен быть в нём администратором с правом «Публикация сообщений».",
    },
    "channel_bad": {
        "uz": "Kanal topilmadi yoki bot unda administrator emas. Kanal → Administratorlar → @{username} qoʻshing "
              "(«Xabar joylash» yoqilgan) va qayta yuboring.",
        "ru": "Канал не найден или бот в нём не администратор. Канал → Администраторы → добавьте @{username} "
              "(с правом «Публикация сообщений») и пришлите снова.",
    },
    "ask_contacts": {
        "uz": "Postlardagi kontaktlar: telefon va @username, har biri yangi qatorda.\nMasalan: +998901234567 @agent",
        "ru": "Контакты для постов: телефон и @username, каждый с новой строки.\nНапример: +998901234567 @agent",
    },
    "confirm": {
        "uz": "Tekshiring:\n🏢 {name} · {city}\n🤖 @{username}\n📢 {channel}\n📞 {contacts}\n\nHammasi toʻgʻrimi?",
        "ru": "Проверьте:\n🏢 {name} · {city}\n🤖 @{username}\n📢 {channel}\n📞 {contacts}\n\nВсё верно?",
    },
    "done": {
        "uz": "🎉 Tayyor! @{username} ishlayapti.\nUni oching va /start bosing — siz administratorsiz. "
              "Birinchi eʼlonni yuboring: bot uni tahlil qilib, {channel} kanaliga chiqaradi.",
        "ru": "🎉 Готово! @{username} работает.\nОткройте его и нажмите /start — вы администратор. "
              "Пришлите первое объявление: бот разберёт его и опубликует в {channel}.",
    },
    "unavailable": {"uz": "Ulash vaqtincha ishlamayapti — tez orada tuzatamiz.",
                    "ru": "Подключение временно недоступно — скоро починим."},
    "limit": {"uz": "Siz allaqachon {n} ta agentlik uladingiz. Yana kerak boʻlsa — yozing.",
              "ru": "Вы уже подключили {n} агентства. Нужно больше — напишите нам."},
    "cancelled": {"uz": "Bekor qilindi. /start — boshidan.", "ru": "Отменено. /start — начать заново."},
    "my_none": {"uz": "Sizda ulangan agentlik yoʻq.", "ru": "У вас нет подключённых агентств."},
    "my_line": {"uz": "🏢 {name} · @{username} · {status}", "ru": "🏢 {name} · @{username} · {status}"},
    "status_active": {"uz": "ishlayapti", "ru": "работает"},
    "status_disabled": {"uz": "oʻchirilgan", "ru": "отключено"},
}

CITIES = ("Фергана", "Ташкент", "Самарканд", "Наманган", "Андижан", "Бухара", "Карши", "Нукус", "Коканд",
          "Маргилан")


def t(key: str, lang: str, **kwargs) -> str:
    variants = TEXTS[key]
    text = variants.get(lang) or variants["ru"]
    return text.format(**kwargs) if kwargs else text
