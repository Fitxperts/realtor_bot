"""Мультиязычность РиелторБота: узбекский, русский, английский.

- ``LANGUAGES`` — поддерживаемые языки и их подписи для выбора;
- ``t(key, lang, **kwargs)`` — перевод сообщения с подстановкой параметров;
- ``btn(action, lang)`` — подпись кнопки;
- ``btn_variants(action)`` / ``all_menu_texts()`` — множества всех переводов
  подписи (нужны, чтобы ловить reply-кнопки на любом языке через ``F.text.in_``).

Тексты канала (публичное объявление) и админ-панель здесь НЕ переводятся:
объявление в канал одно на всех (узбекский шаблон), админку видит только
оператор-риелтор.
"""
from __future__ import annotations

# Порядок = порядок кнопок выбора языка
LANGUAGES: dict[str, str] = {
    "uz": "🇺🇿 O‘zbekcha",
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English",
}
DEFAULT_LANG = "ru"


def normalize(lang: str | None) -> str:
    return lang if lang in LANGUAGES else DEFAULT_LANG


# ---------------------------------------------------------------------------
# Подписи кнопок: action -> {lang: label}
# ---------------------------------------------------------------------------
BUTTONS: dict[str, dict[str, str]] = {
    # Reply-меню (матчатся по тексту — нужны все языки)
    "find_housing": {"uz": "🔎 Uy tanlash", "ru": "🔎 Подобрать жильё", "en": "🔎 Find housing"},
    "catalog": {"uz": "📋 Eʼlonlar", "ru": "📋 Объявления", "en": "📋 Listings"},
    "lead_cta": {"uz": "✍️ Ariza qoldirish", "ru": "✍️ Оставить заявку", "en": "✍️ Leave a request"},
    "cat_more": {"uz": "⬇️ Yana koʻrsatish", "ru": "⬇️ Показать ещё", "en": "⬇️ Show more"},
    "deal_all": {"uz": "Hammasi", "ru": "Все", "en": "All"},
    "add_object": {"uz": "➕ E’lon joylash", "ru": "➕ Разместить объект", "en": "➕ Post a listing"},
    "my_objects": {"uz": "📋 Mening e’lonlarim", "ru": "📋 Мои объекты", "en": "📋 My listings"},
    "change_role": {"uz": "↩️ Rolni almashtirish", "ru": "↩️ Сменить роль", "en": "↩️ Change role"},
    "language": {"uz": "🌐 Til", "ru": "🌐 Язык", "en": "🌐 Language"},
    # Роли (inline)
    "role_client": {"uz": "🔎 Uy qidiryapman", "ru": "🔎 Ищу жильё", "en": "🔎 Looking for housing"},
    "role_owner": {"uz": "🏠 Sotaman/ijaraga beraman", "ru": "🏠 Хочу сдать/продать", "en": "🏠 Sell / rent out"},
    # Клиент: сделка
    "deal_rent": {"uz": "🔑 Ijara", "ru": "🔑 Аренда", "en": "🔑 Rent"},
    "deal_buy": {"uz": "🏷 Sotib olish", "ru": "🏷 Покупка", "en": "🏷 Buy"},
    # Общие
    "district_other": {"uz": "✍️ Boshqa tuman", "ru": "✍️ Другой район", "en": "✍️ Other district"},
    "skip": {"uz": "Oʻtkazib yuborish", "ru": "Пропустить", "en": "Skip"},
    "send_phone": {"uz": "📱 Raqamimni yuborish", "ru": "📱 Отправить мой номер", "en": "📱 Share my number"},
    "confirm_send": {"uz": "✅ Yuborish", "ru": "✅ Отправить", "en": "✅ Submit"},
    "cancel": {"uz": "❌ Bekor qilish", "ru": "❌ Отмена", "en": "❌ Cancel"},
    "support": {"uz": "✍️ Savol / xatolik", "ru": "✍️ Задать вопрос / ошибка", "en": "✍️ Ask / report a bug"},
    "massif_other": {"uz": "✍️ Boshqa", "ru": "✍️ Свой вариант", "en": "✍️ Other"},
    "massif_skip": {"uz": "⏭ Oʻtkazib yuborish", "ru": "⏭ Пропустить", "en": "⏭ Skip"},
    "yes": {"uz": "Ha", "ru": "Да", "en": "Yes"},
    "no": {"uz": "Yoʻq", "ru": "Нет", "en": "No"},
    "done": {"uz": "Tayyor", "ru": "Готово", "en": "Done"},
    # --- Админ-панель: reply-меню ---
    "adm_new": {"uz": "➕ Yangi obyekt", "ru": "➕ Новый объект", "en": "➕ New listing"},
    "adm_objects": {"uz": "📋 Obyektlar", "ru": "📋 Объекты", "en": "📋 Listings"},
    "adm_clients": {"uz": "👥 Mijozlar", "ru": "👥 Клиенты", "en": "👥 Clients"},
    "adm_meetings": {"uz": "📆 Uchrashuvlar", "ru": "📆 Встречи", "en": "📆 Meetings"},
    "adm_publications": {"uz": "📢 Eʼlonlar", "ru": "📢 Публикации", "en": "📢 Publications"},
    "adm_stats": {"uz": "📊 Statistika", "ru": "📊 Статистика", "en": "📊 Statistics"},
    # --- Админ: inline-кнопки ---
    "adm_f_all": {"uz": "Barchasi", "ru": "Все", "en": "All"},
    "adm_f_active": {"uz": "Faol", "ru": "Активные", "en": "Active"},
    "adm_f_pending": {"uz": "Tekshiruvda", "ru": "На проверке", "en": "Pending"},
    "adm_f_sold": {"uz": "Sotilgan/Ijarada", "ru": "Сдано/Продано", "en": "Sold/Rented"},
    "adm_approve": {"uz": "✅ Tasdiqlash", "ru": "✅ Одобрить", "en": "✅ Approve"},
    "adm_edit": {"uz": "✏️ Tahrirlash", "ru": "✏️ Редактировать", "en": "✏️ Edit"},
    "adm_hold": {"uz": "⏸ Kechiktirish", "ru": "⏸ Отложить", "en": "⏸ Hold"},
    "adm_bump": {"uz": "📢 Koʻtarish", "ru": "📢 Поднять", "en": "📢 Bump"},
    "adm_sold_btn": {"uz": "⛔ Sotildi/Ijarada", "ru": "⛔ Сдано/Продано", "en": "⛔ Sold/Rented"},
    "adm_archive": {"uz": "🗄 Arxiv", "ru": "🗄 Архив", "en": "🗄 Archive"},
    "adm_matches": {"uz": "🔎 Mos obyektlar", "ru": "🔎 Подходящие объекты", "en": "🔎 Matches"},
    "adm_more": {"uz": "⬇️ Yana", "ru": "⬇️ Ещё", "en": "⬇️ More"},
    "adm_new_meeting": {"uz": "➕ Uchrashuv belgilash", "ru": "➕ Назначить встречу", "en": "➕ Schedule meeting"},
    "adm_m_done": {"uz": "✅ Boʻldi", "ru": "✅ Провёл", "en": "✅ Done"},
    "adm_m_cancel": {"uz": "❌ Bekor qilish", "ru": "❌ Отменить", "en": "❌ Cancel"},
    "adm_exp_props_xlsx": {"uz": "📄 Obyektlar Excel", "ru": "📄 Объекты Excel", "en": "📄 Listings Excel"},
    "adm_exp_props_csv": {"uz": "📄 Obyektlar CSV", "ru": "📄 Объекты CSV", "en": "📄 Listings CSV"},
    "adm_exp_cli_xlsx": {"uz": "👥 Mijozlar Excel", "ru": "👥 Клиенты Excel", "en": "👥 Clients Excel"},
    "adm_exp_cli_csv": {"uz": "👥 Mijozlar CSV", "ru": "👥 Клиенты CSV", "en": "👥 Clients CSV"},
}


def btn(action: str, lang: str) -> str:
    variants = BUTTONS.get(action, {})
    return variants.get(normalize(lang)) or variants.get(DEFAULT_LANG, action)


def btn_variants(action: str) -> set[str]:
    """Все языковые варианты подписи — для матчинга reply-кнопок."""
    return set(BUTTONS.get(action, {}).values())


# Тексты reply-меню, которые нужно исключать из catch-all (все языки)
_MENU_ACTIONS = ("find_housing", "add_object", "my_objects", "change_role", "language")


def all_menu_texts() -> set[str]:
    texts: set[str] = set()
    for action in _MENU_ACTIONS:
        texts |= btn_variants(action)
    return texts


# ---------------------------------------------------------------------------
# Сообщения: key -> {lang: text}
# ---------------------------------------------------------------------------
MESSAGES: dict[str, dict[str, str]] = {
    # --- Язык / роль / меню ---
    "choose_language": {
        "uz": "Tilni tanlang:",
        "ru": "Выберите язык:",
        "en": "Choose your language:",
    },
    "language_saved": {
        "uz": "✅ Til oʻzgartirildi: {name}",
        "ru": "✅ Язык изменён: {name}",
        "en": "✅ Language set: {name}",
    },
    "choose_role": {
        "uz": "👋 Assalomu alaykum! Sizni nima qiziqtiradi?",
        "ru": "👋 Здравствуйте! Что вас интересует?",
        "en": "👋 Hello! What are you interested in?",
    },
    "choose_role_short": {
        "uz": "Kim boʻlishni xohlaysiz?",
        "ru": "Кем вы хотите быть?",
        "en": "What would you like to do?",
    },
    "client_mode": {
        "uz": "🔎 Uy qidirish rejimi.",
        "ru": "🔎 Режим поиска жилья.",
        "en": "🔎 Housing search mode.",
    },
    "owner_mode": {
        "uz": "🏠 Egasi rejimi.",
        "ru": "🏠 Режим собственника.",
        "en": "🏠 Owner mode.",
    },
    "client_menu_title": {
        "uz": "🔎 Uy qidirish menyusi:",
        "ru": "🔎 Меню поиска жилья:",
        "en": "🔎 Housing search menu:",
    },
    "owner_menu_title": {
        "uz": "🏠 Egasi menyusi:",
        "ru": "🏠 Меню собственника:",
        "en": "🏠 Owner menu:",
    },
    "no_objects": {
        "uz": "Sizda hozircha joylangan obyektlar yoʻq.",
        "ru": "У вас пока нет размещённых объектов.",
        "en": "You have no listings yet.",
    },
    # --- Каталог (просмотр объявлений в боте) ---
    "catalog_pick": {
        "uz": "🏠 Nimani koʻrsatay?",
        "ru": "🏠 Что показать?",
        "en": "🏠 What should I show?",
    },
    "catalog_empty": {
        "uz": "Hozircha faol eʼlonlar yoʻq. Ariza qoldiring — rieltor siz uchun tanlab beradi 👇",
        "ru": "Пока нет активных объявлений. Оставьте заявку — риелтор подберёт под вас 👇",
        "en": "No active listings yet. Leave a request — an agent will find options for you 👇",
    },
    "catalog_footer": {
        "uz": "Koʻrsatildi: {shown}/{total}",
        "ru": "Показано {shown} из {total}",
        "en": "Shown {shown} of {total}",
    },
    "catalog_end": {
        "uz": "✅ Hammasi shu. Mos variant topilmadimi? Ariza qoldiring 👇",
        "ru": "✅ Это все объявления. Не нашли подходящее? Оставьте заявку 👇",
        "en": "✅ That’s all listings. Didn’t find a fit? Leave a request 👇",
    },
    "catalog_browse": {
        "uz": "📋 Eʼlonlarni koʻrish",
        "ru": "📋 Смотреть объявления",
        "en": "📋 Browse listings",
    },
    # --- Служебное ---
    "cancel_none": {
        "uz": "Faol amal yoʻq.",
        "ru": "Нет активного действия.",
        "en": "No active action.",
    },
    "cancel_done": {
        "uz": "✖️ Amal bekor qilindi. /start — menyu.",
        "ru": "✖️ Действие отменено. /start — в меню.",
        "en": "✖️ Action cancelled. /start — menu.",
    },
    # --- Помощь / поддержка ---
    "help_text": {
        "uz": (
            "ℹ️ <b>RieltorBot — Fargʻona</b>\n\n"
            "• /start — boshlash / rolni tanlash\n"
            "• /add — obyekt joylash (egalar uchun)\n"
            "• /cancel — joriy amalni bekor qilish\n\n"
            "Mijoz: ariza qoldiring — variant tanlaymiz va yangilaridan xabar beramiz.\n"
            "Egasi: obyektni bosqichma-bosqich yoki bitta xabarda joylang.\n\n"
            "Savol yoki xatolik bormi? Pastdagi tugmani bosing 👇"
        ),
        "ru": (
            "ℹ️ <b>РиелторБот — Фергана</b>\n\n"
            "• /start — начать / выбрать роль\n"
            "• /add — разместить объект (для собственника)\n"
            "• /cancel — отменить текущее действие\n\n"
            "Клиент: оставьте заявку — подберём варианты и сообщим о новых.\n"
            "Собственник: разместите объект по шагам или одним сообщением.\n\n"
            "Есть вопрос или нашли ошибку? Нажмите кнопку ниже 👇"
        ),
        "en": (
            "ℹ️ <b>RealtorBot — Fergana</b>\n\n"
            "• /start — begin / choose role\n"
            "• /add — post a listing (for owners)\n"
            "• /cancel — cancel current action\n\n"
            "Client: leave a request — we’ll find options and notify you of new ones.\n"
            "Owner: post a property step by step or in a single message.\n\n"
            "Have a question or found a bug? Tap the button below 👇"
        ),
    },
    "support_prompt": {
        "uz": "✍️ Savolingiz yoki xatolik haqida yozing — xabar administratorga yetkaziladi. Bekor qilish: /cancel",
        "ru": "✍️ Напишите ваш вопрос или опишите ошибку — сообщение уйдёт администратору. Отмена: /cancel",
        "en": "✍️ Write your question or describe the bug — it will be sent to the administrator. Cancel: /cancel",
    },
    "lead_intro": {
        "uz": "👋 Siz kanaldagi obyektga qiziqdingiz:",
        "ru": "👋 Вы заинтересовались объектом из канала:",
        "en": "👋 You’re interested in this listing from the channel:",
    },
    "lead_ask_phone": {
        "uz": "📞 Raqamingizni qoldiring — rieltor tez orada qoʻngʻiroq qiladi (yoki tugma bilan yuboring):",
        "ru": "📞 Оставьте номер — риелтор скоро перезвонит (или отправьте кнопкой):",
        "en": "📞 Leave your number — an agent will call you back (or use the button):",
    },
    "lead_thanks": {
        "uz": "✅ Rahmat! Arizangiz qabul qilindi, rieltor tez orada bogʻlanadi.",
        "ru": "✅ Спасибо! Заявка принята, риелтор скоро свяжется с вами.",
        "en": "✅ Thank you! Your request is received, an agent will contact you soon.",
    },
    "lead_not_found": {
        "uz": "Bu obyekt allaqachon mavjud emas. /start — boshqa variantlar.",
        "ru": "Этого объекта уже нет в наличии. /start — другие варианты.",
        "en": "This listing is no longer available. /start — other options.",
    },
    "support_sent": {
        "uz": "✅ Xabaringiz yuborildi. Tez orada javob beramiz.",
        "ru": "✅ Ваше сообщение отправлено. Мы скоро ответим.",
        "en": "✅ Your message has been sent. We’ll get back to you soon.",
    },
    "support_reply_delivered": {
        "uz": "💬 <b>Qoʻllab-quvvatlash javobi:</b>\n{text}",
        "ru": "💬 <b>Ответ поддержки:</b>\n{text}",
        "en": "💬 <b>Support reply:</b>\n{text}",
    },
    # --- Клиент: анкета ---
    "client_intro": {
        "uz": "Fargʻonada uy tanlashda yordam beraman.\nSiz <b>ijaraga olmoqchimisiz</b> yoki <b>sotib olmoqchimisiz</b>?",
        "ru": "Помогу подобрать недвижимость в Фергане.\nВы хотите <b>арендовать</b> или <b>купить</b>?",
        "en": "I’ll help you find property in Fergana.\nDo you want to <b>rent</b> or <b>buy</b>?",
    },
    "client_pick_deal": {
        "uz": "Tanlang: ijara yoki sotib olish.",
        "ru": "Выберите: аренда или покупка.",
        "en": "Choose: rent or buy.",
    },
    "ask_district": {
        "uz": "📍 Qaysi tumandan qidiryapsiz? Tanlang yoki oʻzingiznikini yozing:",
        "ru": "📍 В каком районе ищете? Выберите или напишите свой:",
        "en": "📍 Which district? Pick one or type your own:",
    },
    "district_write": {
        "uz": "Tuman nomini matn bilan yozing:",
        "ru": "Напишите название района текстом:",
        "en": "Type the district name:",
    },
    "ask_budget": {
        "uz": "💰 Byudjetingiz qancha? Summani yozing (masalan, 3000000):",
        "ru": "💰 Какой у вас бюджет? Напишите сумму (например, 3000000):",
        "en": "💰 What’s your budget? Enter an amount (e.g. 3000000):",
    },
    "budget_bad": {
        "uz": "Summa tushunarsiz. Raqam kiriting, masalan 3000000.",
        "ru": "Не понял сумму. Введите число, например 3000000.",
        "en": "Couldn’t read the amount. Enter a number, e.g. 3000000.",
    },
    "ask_phone": {
        "uz": "📞 Bogʻlanish uchun telefon qoldiring (yoki tugma bilan yuboring):",
        "ru": "📞 Оставьте телефон для связи (или отправьте номер кнопкой):",
        "en": "📞 Leave a contact phone (or share it with the button):",
    },
    "phone_bad": {
        "uz": "Raqam notoʻgʻriga oʻxshaydi. Qayta kiriting, masalan +998901234567.",
        "ru": "Похоже, номер некорректный. Введите ещё раз, например +998901234567.",
        "en": "That number looks invalid. Try again, e.g. +998901234567.",
    },
    "confirm_title": {
        "uz": "Arizani tekshiring:",
        "ru": "Проверьте заявку:",
        "en": "Review your request:",
    },
    "request_accepted": {
        "uz": "✅ Ariza qabul qilindi! Rieltor tez orada bogʻlanadi.",
        "ru": "✅ Заявка принята! Риелтор скоро свяжется с вами.",
        "en": "✅ Request received! An agent will contact you soon.",
    },
    "request_cancelled": {
        "uz": "Ariza bekor qilindi. Qaytadan boshlash uchun /start bosing.",
        "ru": "Заявка отменена. Нажмите /start, чтобы начать заново.",
        "en": "Request cancelled. Tap /start to begin again.",
    },
    "matches_found": {
        "uz": "🔎 Mos variantlar topildi:",
        "ru": "🔎 Нашлись подходящие варианты:",
        "en": "🔎 We found matching options:",
    },
    "no_matches": {
        "uz": "Hozircha mos obyekt yoʻq — paydo boʻlishi bilan xabar beramiz.",
        "ru": "Пока подходящих объектов нет — сообщим, как появятся.",
        "en": "No matches yet — we’ll notify you when they appear.",
    },
    "ask_call_time": {
        "uz": "🕐 Rieltor qachon qoʻngʻiroq qilsa qulay?",
        "ru": "🕐 Когда вам удобно, чтобы риелтор позвонил?",
        "en": "🕐 When is it convenient for the agent to call?",
    },
    "pick_time": {
        "uz": "🕐 {period} — vaqtni tanlang:",
        "ru": "🕐 {period} — выберите время:",
        "en": "🕐 {period} — choose a time:",
    },
    "call_scheduled": {
        "uz": "✅ Ajoyib! Rieltor sizga {slot} da qoʻngʻiroq qiladi.",
        "ru": "✅ Отлично! Риелтор позвонит вам в {slot}.",
        "en": "✅ Great! The agent will call you at {slot}.",
    },
    # Превью заявки клиента
    "preview_request": {"uz": "<b>Sizning arizangiz</b>", "ru": "<b>Ваша заявка</b>", "en": "<b>Your request</b>"},
    "preview_deal": {"uz": "Bitim", "ru": "Сделка", "en": "Deal"},
    "preview_district": {"uz": "Tuman", "ru": "Район", "en": "District"},
    "preview_budget": {"uz": "Byudjet", "ru": "Бюджет", "en": "Budget"},
    "preview_phone": {"uz": "Telefon", "ru": "Телефон", "en": "Phone"},
    "deal_rent_word": {"uz": "Ijara", "ru": "Аренда", "en": "Rent"},
    "deal_buy_word": {"uz": "Sotib olish", "ru": "Покупка", "en": "Buy"},
    # Состав проживающих (значения)
    # Периоды звонка
    "period_morning": {"uz": "Ertalab", "ru": "Утро", "en": "Morning"},
    "period_day": {"uz": "Kunduzi", "ru": "День", "en": "Day"},
    "period_evening": {"uz": "Kechqurun", "ru": "Вечер", "en": "Evening"},
    # --- Собственник ---
    "owner_intro": {
        "uz": (
            "🏠 <b>Obyekt joylash.</b>\n"
            "Eʼlonni <b>bitta xabarda</b> yuboring — oʻzingizniki yoki hamkasbingizdan "
            "tayyor eʼlonni. Men oʻzim maʼlumotlarni ajratib, agentlik uslubida "
            "rasmiylashtiraman va ortiqchasini olib tashlayman.\n\n"
            "<i>Masalan:</i>\n"
            "<code>Fargʻona markazida 2 xonali kvartira ijaraga, 5/9, taʼmirli, "
            "2500000 soʻm. Tel: +998901234567</code>\n\n"
            "Rasm(lar)ni izoh (matn) bilan birga ham yuborishingiz mumkin."
        ),
        "ru": (
            "🏠 <b>Размещение объекта.</b>\n"
            "Пришлите объявление <b>одним сообщением</b> — своё или готовое от партнёра. "
            "Я сам разберу данные, оформлю в стиле агентства и уберу лишнее.\n\n"
            "<i>Например:</i>\n"
            "<code>2-комн. квартира в центре Ферганы, аренда, 5/9, с ремонтом, "
            "2 500 000 сум. Тел: +998901234567</code>\n\n"
            "Можно прислать фото с текстом в подписи."
        ),
        "en": (
            "🏠 <b>Post a listing.</b>\n"
            "Send the ad in <b>one message</b> — yours or a ready one from a colleague. "
            "I’ll extract the data, format it in the agency style and trim the excess.\n\n"
            "<i>Example:</i>\n"
            "<code>2-room apartment in central Fergana, rent, 5/9, renovated, "
            "2,500,000 UZS. Tel: +998901234567</code>\n\n"
            "You may also send photo(s) with the text in the caption."
        ),
    },
    "owner_send_text": {
        "uz": "Iltimos, eʼlon <b>matnini</b> xabar bilan yuboring — men uni ajrataman.",
        "ru": "Пришлите <b>текст</b> объявления сообщением — я его разберу.",
        "en": "Please send the ad <b>text</b> as a message — I’ll parse it.",
    },
    "owner_parsing": {
        "uz": "🔎 Eʼlonni tahlil qilyapman…",
        "ru": "🔎 Разбираю объявление…",
        "en": "🔎 Parsing the listing…",
    },
    "owner_reading_image": {
        "uz": "🖼 Rasmydagi eʼlonni oʻqiyapman…",
        "ru": "🖼 Читаю объявление с картинки…",
        "en": "🖼 Reading the listing from the image…",
    },
    "owner_image_failed": {
        "uz": "Rasmda eʼlonni topa olmadim. Iltimos, eʼlon matnini yozib yuboring.",
        "ru": "Не смог распознать объявление на картинке. Пришлите текст объявления.",
        "en": "Couldn’t read the listing from the image. Please send the text.",
    },
    "owner_listening": {
        "uz": "🎧 Ovozli xabaringizni tinglayapman…",
        "ru": "🎧 Слушаю голосовое…",
        "en": "🎧 Listening to your voice message…",
    },
    "owner_voice_failed": {
        "uz": "Ovozli xabarni tushuna olmadim. Iltimos, matn bilan yuboring.",
        "ru": "Не смог разобрать голосовое. Пришлите текстом.",
        "en": "Couldn’t understand the voice message. Please send it as text.",
    },
    "owner_need_deal": {
        "uz": "Bitim turini aniqlashtiring:",
        "ru": "Уточните тип сделки:",
        "en": "Please specify the deal type:",
    },
    "owner_need_price": {
        "uz": "Narxni topa olmadim. Narxni raqam bilan kiriting (masalan, 2500000):",
        "ru": "Не нашёл цену. Укажите цену числом (например, 2500000):",
        "en": "Couldn’t find the price. Enter it as a number (e.g. 2500000):",
    },
    "owner_ask_massif": {
        "uz": "📍 Tuman/massivni tanlang (yoki «Boshqa» — qoʻlda yozing):",
        "ru": "📍 Выберите район/массив (или «Свой вариант» — впишите вручную):",
        "en": "📍 Choose the district/massif (or “Other” to type your own):",
    },
    "owner_massif_type": {
        "uz": "Tuman/massiv nomini yozing:",
        "ru": "Впишите название района/массива:",
        "en": "Type the district/massif name:",
    },
    "ask_media": {
        "uz": "📷 Obyekt rasm va videosini yuboring (agar yuqorida biriktirmagan boʻlsangiz) — video rasm bilan birga ham boʻladi. Soʻng «Tayyor».",
        "ru": "📷 Пришлите фото и видео объекта (если не приложили выше). Видео можно вместе с фото. Затем «Готово».",
        "en": "📷 Send photos and video of the property (if not attached above). Video may come together with photos. Then “Done”.",
    },
    "media_accepted": {
        "uz": "Qabul qilindi: {n} ta media. Yana yoki «Tayyor».",
        "ru": "Принято: {n} медиа. Ещё или «Готово».",
        "en": "Received: {n} media. More or “Done”.",
    },
    "owner_deal_rent": {"uz": "🔑 Ijaraga", "ru": "🔑 Аренда", "en": "🔑 Rent out"},
    "owner_deal_sale": {"uz": "🏷 Sotuv", "ru": "🏷 Продажа", "en": "🏷 Sale"},
    "owner_quick_ok": {
        "uz": "✅ Eʼlon matndan tanildi. Tekshiring:",
        "ru": "✅ Распознал объявление из текста. Проверьте:",
        "en": "✅ Parsed the listing from text. Please check:",
    },
    "price_bad": {"uz": "Narxni raqam bilan kiriting, masalan 45000000.", "ru": "Введите цену числом, например 45000000.", "en": "Enter the price as a number, e.g. 45000000."},
    "owner_ask_phone": {"uz": "📞 Egasining aloqa telefoni:", "ru": "📞 Контактный телефон собственника:", "en": "📞 Owner’s contact phone:"},
    "owner_phone_bad": {"uz": "Notoʻgʻri raqam. Masalan: +998901234567.", "ru": "Некорректный номер. Пример: +998901234567.", "en": "Invalid number. Example: +998901234567."},
    "owner_confirm_title": {"uz": "Eʼlonni tekshiring:", "ru": "Проверьте объявление:", "en": "Review the listing:"},
    "owner_cancelled": {
        "uz": "Joylash bekor qilindi. Qaytadan: /add.",
        "ru": "Размещение отменено. Команда /add — начать заново.",
        "en": "Posting cancelled. Use /add to start over.",
    },
    "dup_warning": {
        "uz": "⚠️ <b>Ehtimoliy dublikat!</b>\n\nQuyidagiga oʻxshaydi:\n{brief}\n\nBaribir qoʻshaymi?",
        "ru": "⚠️ <b>Возможный дубль!</b>\n\nПохож на:\n{brief}\n\nВсё равно добавить?",
        "en": "⚠️ <b>Possible duplicate!</b>\n\nSimilar to:\n{brief}\n\nAdd anyway?",
    },
    "dup_cancelled": {"uz": "Qoʻshish bekor qilindi.", "ru": "Добавление отменено.", "en": "Adding cancelled."},
    "sent_to_moderation": {
        "uz": "✅ {id} obyekti moderatsiyaga yuborildi. Eʼlon qilinishi haqida xabar beramiz.",
        "ru": "✅ Объект {id} отправлен на модерацию. Мы сообщим о публикации.",
        "en": "✅ Listing {id} sent for moderation. We’ll notify you when it’s published.",
    },
    # ======================= Админ-панель =======================
    "adm_panel_title": {"uz": "🛠 Administrator paneli", "ru": "🛠 Панель администратора", "en": "🛠 Admin panel"},
    "adm_objects_filter": {"uz": "📋 Obyektlar — filtrni tanlang:", "ru": "📋 Объекты — выберите фильтр:", "en": "📋 Listings — choose a filter:"},
    "adm_no_more": {"uz": "Boshqa obyekt yoʻq.", "ru": "Больше объектов нет.", "en": "No more listings."},
    "adm_nothing_found": {"uz": "Hech narsa topilmadi.", "ru": "Ничего не найдено.", "en": "Nothing found."},
    "adm_t_published": {"uz": "Eʼlon qilindi ✅", "ru": "Опубликовано ✅", "en": "Published ✅"},
    "adm_t_hold": {"uz": "Kechiktirildi ⏸", "ru": "Отложено ⏸", "en": "On hold ⏸"},
    "adm_t_sold": {"uz": "Sotildi/Ijarada ⛔", "ru": "Отмечено как сдано/продано ⛔", "en": "Marked sold/rented ⛔"},
    "adm_t_bumped": {"uz": "Koʻtarildi 📢", "ru": "Поднято 📢", "en": "Bumped 📢"},
    "adm_t_archived": {"uz": "Arxivda 🗄", "ru": "В архиве 🗄", "en": "Archived 🗄"},
    "adm_publish_error": {"uz": "Eʼlon xatosi. Kanal/bot huquqlarini tekshiring.", "ru": "Ошибка публикации. Проверьте канал/права бота.", "en": "Publish error. Check the channel / bot rights."},
    "adm_not_found": {"uz": "Obyekt topilmadi", "ru": "Объект не найден", "en": "Listing not found"},
    "adm_published_owner": {"uz": "✅ Sizning {id} obyektingiz eʼlon qilindi!", "ru": "✅ Ваш объект {id} опубликован!", "en": "✅ Your listing {id} is published!"},
    "adm_edit_title": {"uz": "✏️ {id} tahriri. Nimani oʻzgartiramiz?", "ru": "✏️ Редактирование {id}. Что изменить?", "en": "✏️ Editing {id}. What to change?"},
    "adm_edit_prompt": {"uz": "Yangi qiymatni kiriting — {field}:", "ru": "Введите новое значение — {field}:", "en": "Enter a new value — {field}:"},
    "adm_enter_number": {"uz": "Raqam kiriting.", "ru": "Введите число.", "en": "Enter a number."},
    "adm_enter_int": {"uz": "Butun son kiriting.", "ru": "Введите целое число.", "en": "Enter an integer."},
    "adm_field_updated": {"uz": "✅ «{field}» maydoni yangilandi.", "ru": "✅ Поле «{field}» обновлено.", "en": "✅ Field “{field}” updated."},
    "adm_obj_not_found": {"uz": "Obyekt topilmadi.", "ru": "Объект не найден.", "en": "Listing not found."},
    "adm_no_more_clients": {"uz": "Boshqa ariza yoʻq.", "ru": "Больше заявок нет.", "en": "No more requests."},
    "adm_no_clients": {"uz": "Hozircha ariza yoʻq.", "ru": "Заявок пока нет.", "en": "No requests yet."},
    "adm_status_updated": {"uz": "Status yangilandi", "ru": "Статус обновлён", "en": "Status updated"},
    "adm_client_matches": {"uz": "🔎 {id} uchun mos obyektlar:", "ru": "🔎 Подходящие объекты для {id}:", "en": "🔎 Matches for {id}:"},
    "adm_no_matches": {"uz": "Mos obyektlar yoʻq.", "ru": "Подходящих объектов нет.", "en": "No matching listings."},
    "adm_meetings_manage": {"uz": "Uchrashuvlarni boshqarish:", "ru": "Управление встречами:", "en": "Manage meetings:"},
    "adm_no_meetings": {"uz": "Rejalashtirilgan uchrashuvlar yoʻq.", "ru": "Запланированных встреч нет.", "en": "No scheduled meetings."},
    "adm_t_meeting_done": {"uz": "Uchrashuv oʻtkazildi ✅", "ru": "Встреча проведена ✅", "en": "Meeting completed ✅"},
    "adm_t_meeting_cancel": {"uz": "Uchrashuv bekor qilindi ❌", "ru": "Встреча отменена ❌", "en": "Meeting cancelled ❌"},
    "adm_no_clients_alert": {"uz": "Mijozlar yoʻq", "ru": "Нет клиентов", "en": "No clients"},
    "adm_pick_client": {"uz": "Mijozni tanlang:", "ru": "Выберите клиента:", "en": "Choose a client:"},
    "adm_no_active": {"uz": "Faol obyektlar yoʻq", "ru": "Нет активных объектов", "en": "No active listings"},
    "adm_pick_object": {"uz": "Obyektni tanlang:", "ru": "Выберите объект:", "en": "Choose a listing:"},
    "adm_meeting_date": {"uz": "Uchrashuv sanasi (KK.OO.YYYY):", "ru": "Дата встречи (ДД.ММ.ГГГГ):", "en": "Meeting date (DD.MM.YYYY):"},
    "adm_bad_date": {"uz": "Sana tushunarsiz. Format KK.OO.YYYY.", "ru": "Не понял дату. Формат ДД.ММ.ГГГГ.", "en": "Bad date. Format DD.MM.YYYY."},
    "adm_meeting_time": {"uz": "Uchrashuv vaqti (SS:DD):", "ru": "Время встречи (ЧЧ:ММ):", "en": "Meeting time (HH:MM):"},
    "adm_bad_time": {"uz": "Vaqt tushunarsiz. Format SS:DD, masalan 15:30.", "ru": "Не понял время. Формат ЧЧ:ММ, например 15:30.", "en": "Bad time. Format HH:MM, e.g. 15:30."},
    "adm_meeting_created": {"uz": "✅ {id} uchrashuvi {when} ga belgilandi.", "ru": "✅ Встреча {id} назначена на {when}.", "en": "✅ Meeting {id} scheduled for {when}."},
    "adm_pub_queue": {"uz": "🕒 <b>Eʼlon navbati:</b>", "ru": "🕒 <b>Очередь на публикацию:</b>", "en": "🕒 <b>Publish queue:</b>"},
    "adm_pub_queue_empty": {"uz": "Eʼlon navbati boʻsh.", "ru": "Очередь на публикацию пуста.", "en": "Publish queue is empty."},
    "adm_pub_published": {"uz": "📢 <b>Eʼlon qilinganlar:</b>", "ru": "📢 <b>Опубликованные:</b>", "en": "📢 <b>Published:</b>"},
    "adm_export_done": {"uz": "Tayyor ✅", "ru": "Готово ✅", "en": "Done ✅"},
    # Статистика
    "st_title": {"uz": "📊 <b>Statistika</b>", "ru": "📊 <b>Статистика</b>", "en": "📊 <b>Statistics</b>"},
    "st_total": {"uz": "🏠 Jami obyektlar", "ru": "🏠 Объектов всего", "en": "🏠 Total listings"},
    "st_active": {"uz": "faol", "ru": "активных", "en": "active"},
    "st_pending": {"uz": "tekshiruvda", "ru": "на проверке", "en": "pending"},
    "st_sold_rented": {"uz": "ijarada", "ru": "сдано", "en": "rented"},
    "st_sold": {"uz": "sotilgan", "ru": "продано", "en": "sold"},
    "st_archive": {"uz": "arxiv", "ru": "архив", "en": "archive"},
    "st_funnel": {"uz": "👥 <b>Mijozlar voronkasi:</b>", "ru": "👥 <b>Клиенты по воронке:</b>", "en": "👥 <b>Client funnel:</b>"},
    "st_need_bump": {"uz": "📢 Bugun koʻtarish kerak", "ru": "📢 Нужно поднять сегодня", "en": "📢 Need bumping today"},
    # Поля редактирования
    "ef_price": {"uz": "Narx", "ru": "Цена", "en": "Price"},
    "ef_district": {"uz": "Tuman", "ru": "Район", "en": "District"},
    "ef_address": {"uz": "Manzil", "ru": "Адрес", "en": "Address"},
    "ef_rooms": {"uz": "Xonalar", "ru": "Комнаты", "en": "Rooms"},
    "ef_area": {"uz": "Maydon", "ru": "Площадь", "en": "Area"},
    "ef_renovation": {"uz": "Taʼmir", "ru": "Ремонт", "en": "Renovation"},
    "ef_description": {"uz": "Tavsif", "ru": "Описание", "en": "Description"},
    # Статусы клиента (воронка)
    "cst_new": {"uz": "Yangi", "ru": "Новый", "en": "New"},
    "cst_contacted": {"uz": "Bogʻlanildi", "ru": "Связались", "en": "Contacted"},
    "cst_showing_set": {"uz": "Koʻrik belgilandi", "ru": "Показ назначен", "en": "Showing set"},
    "cst_showing_done": {"uz": "Koʻrik boʻldi", "ru": "Показ проведён", "en": "Showing done"},
    "cst_deal": {"uz": "Bitim", "ru": "Сделка", "en": "Deal"},
    "cst_closed": {"uz": "Yopildi", "ru": "Закрыт", "en": "Closed"},
    # Статусы объекта
    "pst_pending": {"uz": "Tekshiruvda", "ru": "На проверке", "en": "Pending"},
    "pst_active": {"uz": "Faol", "ru": "Активно", "en": "Active"},
    "pst_rented": {"uz": "Ijarada", "ru": "Сдано", "en": "Rented"},
    "pst_sold": {"uz": "Sotildi", "ru": "Продано", "en": "Sold"},
    "pst_archived": {"uz": "Arxiv", "ru": "Архив", "en": "Archive"},
    # Статусы встречи
    "mst_planned": {"uz": "Rejalashtirilgan", "ru": "Запланирована", "en": "Planned"},
    "mst_done": {"uz": "Oʻtkazilgan", "ru": "Проведена", "en": "Completed"},
    "mst_cancelled": {"uz": "Bekor qilingan", "ru": "Отменена", "en": "Cancelled"},
    # Виды объектов (слова для карточек)
    "kw_apartment": {"uz": "Kvartira", "ru": "Квартира", "en": "Apartment"},
    "kw_house": {"uz": "Hovli", "ru": "Дом", "en": "House"},
    "kw_land": {"uz": "Yer uchastka", "ru": "Участок", "en": "Land"},
    "kw_commercial": {"uz": "Tijorat", "ru": "Коммерческая", "en": "Commercial"},
    # Тип сделки (объект)
    "pt_rent": {"uz": "Ijara", "ru": "Аренда", "en": "Rent"},
    "pt_sale": {"uz": "Sotuv", "ru": "Продажа", "en": "Sale"},
    # Тип сделки (клиент)
    "cd_rent": {"uz": "Ijara", "ru": "Аренда", "en": "Rent"},
    "cd_buy": {"uz": "Sotib olish", "ru": "Покупка", "en": "Buy"},
    # Карточка объекта: подписи
    "card_neg": {"uz": "kelishiladi", "ru": "торг", "en": "negotiable"},
    "card_rooms_suffix": {"uz": "-xona", "ru": "-комн.", "en": "-room"},
    "card_area_unit": {"uz": "m²", "ru": "м²", "en": "m²"},
    "card_floor_suffix": {"uz": "-qavat", "ru": "эт.", "en": "fl."},
    "comm_furniture": {"uz": "mebel", "ru": "мебель", "en": "furniture"},
    "comm_appliances": {"uz": "texnika", "ru": "техника", "en": "appliances"},
    "comm_gas": {"uz": "gaz", "ru": "газ", "en": "gas"},
    "comm_water": {"uz": "suv", "ru": "вода", "en": "water"},
    "comm_electricity": {"uz": "svet", "ru": "свет", "en": "electricity"},
    "comm_internet": {"uz": "internet", "ru": "интернет", "en": "internet"},
    # Карточка клиента: подписи
    "cc_request": {"uz": "🆕 <b>Ariza {id}</b>", "ru": "🆕 <b>Заявка {id}</b>", "en": "🆕 <b>Request {id}</b>"},
    "cc_deal": {"uz": "Bitim", "ru": "Сделка", "en": "Deal"},
    "cc_district": {"uz": "Tuman", "ru": "Район", "en": "District"},
    "cc_rooms": {"uz": "Xonalar", "ru": "Комнат", "en": "Rooms"},
    "cc_budget": {"uz": "Byudjet", "ru": "Бюджет", "en": "Budget"},
    "cc_residents": {"uz": "Kim yashaydi", "ru": "Кто будет жить", "en": "Residents"},
    "cc_movein": {"uz": "Koʻchib oʻtish", "ru": "Заселение", "en": "Move-in"},
    "cc_any_district": {"uz": "istalgan tuman", "ru": "любой район", "en": "any district"},
    "cc_status": {"uz": "Status", "ru": "Статус", "en": "Status"},
    # Карточка встречи
    "mc_client": {"uz": "Mijoz", "ru": "Клиент", "en": "Client"},
    "mc_object": {"uz": "Obyekt", "ru": "Объект", "en": "Listing"},
    "mc_status": {"uz": "Status", "ru": "Статус", "en": "Status"},
}


def t(key: str, lang: str | None, **kwargs) -> str:
    lang = normalize(lang)
    variants = MESSAGES.get(key, {})
    text = variants.get(lang) or variants.get(DEFAULT_LANG) or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text
