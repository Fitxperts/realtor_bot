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
    # /pay — оплата подписки
    "pay_none": {"uz": "Toʻlov uchun agentlik yoʻq. Avval ulang: /start",
                 "ru": "Нет агентства для оплаты. Сначала подключите: /start"},
    "pay_choose_agency": {"uz": "Qaysi agentlik uchun toʻlaysiz?", "ru": "За какое агентство платите?"},
    "pay_state_trial": {"uz": "🏢 {name}\nSinov davri {until} gacha.", "ru": "🏢 {name}\nПробный период до {until}."},
    "pay_state_active": {"uz": "🏢 {name}\nTarif «{plan}», {until} gacha toʻlangan.",
                         "ru": "🏢 {name}\nТариф «{plan}», оплачено до {until}."},
    "pay_state_grace": {"uz": "🏢 {name}\n⚠️ Obuna tugadi, {until} gacha ishlaydi.",
                        "ru": "🏢 {name}\n⚠️ Подписка закончилась, бот работает до {until}."},
    "pay_state_readonly": {"uz": "🏢 {name}\n⛔ Obuna toʻlanmagan: eʼlon qabul qilish toʻxtatilgan.",
                           "ru": "🏢 {name}\n⛔ Подписка не оплачена: приём объявлений остановлен."},
    "pay_choose_plan": {"uz": "Tarifni tanlang (narx — oyiga):", "ru": "Выберите тариф (цена за месяц):"},
    "pay_plan_btn": {"uz": "{name} — {price}", "ru": "{name} — {price}"},
    "pay_choose_months": {"uz": "«{plan}» — necha oyga?", "ru": "«{plan}» — на сколько месяцев?"},
    "pay_months_btn": {"uz": "{n} oy — {total}", "ru": "{n} мес — {total}"},
    "pay_invoice": {"uz": "🧾 Hisob №{id}\n{name}: «{plan}», {months} oy\nJami: <b>{total}</b>\n\n"
                          "Toʻlovdan soʻng obuna darhol uzayadi — bot xabar beradi.",
                    "ru": "🧾 Счёт №{id}\n{name}: «{plan}», {months} мес\nИтого: <b>{total}</b>\n\n"
                          "После оплаты подписка продлится сразу — бот пришлёт подтверждение."},
    # /districts — свои районы
    "dist_current": {"uz": "🏢 {name} — tumanlar/massivlar ({n}):\n<code>{list}</code>",
                     "ru": "🏢 {name} — районы/массивы ({n}):\n<code>{list}</code>"},
    "dist_empty": {"uz": "🏢 {name}: «{city}» uchun tumanlar roʻyxati hali yoʻq — katalog filtri va mijoz "
                         "arizasida tuman tanlab boʻlmaydi.",
                   "ru": "🏢 {name}: для города «{city}» списка районов пока нет — в фильтре каталога и в заявке "
                         "клиента район не выбрать."},
    "dist_howto": {"uz": "Yangi roʻyxatni yuboring: har qatorda bitta massiv (koʻpi bilan {max}). Yozilish "
                         "variantlari — ikki nuqtadan keyin:\n<code>Chilonzor: чиланзар, chilanzar\nSergeli</code>",
                   "ru": "Пришлите новый список: по строке на массив (до {max}). Варианты написания — через "
                         "двоеточие:\n<code>Чиланзар: chilonzor, чилонзор\nСергели</code>"},
    "dist_hint": {"uz": "📍 «{city}» tumanlari: katalog filtri va mijoz arizasi uchun massivlar roʻyxatini "
                        "yuboring — /districts",
                  "ru": "📍 Районы города «{city}»: пришлите список массивов — для фильтра каталога и заявок "
                        "клиентов: /districts"},
    "dist_reset_btn": {"uz": "↩️ «{city}» spravochnigi", "ru": "↩️ Справочник «{city}»"},
    "dist_saved": {"uz": "✅ Saqlandi — {n} ta massiv, bot darhol ishlatadi:\n<code>{list}</code>",
                   "ru": "✅ Сохранено — {n} массивов, бот агентства уже использует:\n<code>{list}</code>"},
    "pay_offline": {"uz": "Onlayn toʻlov hali ulanmagan — siz bilan bogʻlanamiz.",
                    "ru": "Онлайн-оплата пока не подключена — мы свяжемся с вами для оплаты."},
}

CITIES = ("Фергана", "Ташкент", "Самарканд", "Наманган", "Андижан", "Бухара", "Карши", "Нукус", "Коканд",
          "Маргилан")


def t(key: str, lang: str, **kwargs) -> str:
    variants = TEXTS[key]
    text = variants.get(lang) or variants["ru"]
    return text.format(**kwargs) if kwargs else text
