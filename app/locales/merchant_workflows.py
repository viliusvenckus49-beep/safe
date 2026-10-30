"""Localized directory and direct administration texts (LT / EN / RU)."""

ROWS = {
    "privacy_notice": (
        "Atsiliepimų autoriai pagal nutylėjimą neviešinami. Įjunk jų rodymą tik "
        "pagal taikomą privatumo politiką ir turint teisėtą pagrindą.",
        "Review authors are anonymous by default. Enable their publication only "
        "under your privacy policy and with a lawful basis.",
        "По умолчанию авторы отзывов не публикуются. Включайте публикацию только в "
        "соответствии с политикой конфиденциальности и при наличии законного "
        "основания.",
    ),
    "list_title": ("🏪 REDSAFE PREKEIVIAI", "🏪 REDSAFE MERCHANTS", "🏪 ПРОДАВЦЫ REDSAFE"),
    "count": (
        "Registruoti prekeiviai: {count}",
        "Registered merchants: {count}",
        "Зарегистрировано продавцов: {count}",
    ),
    "profile_title": ("👤 REDSAFE PREKEIVIS", "👤 REDSAFE MERCHANT", "👤 ПРОДАВЕЦ REDSAFE"),
    "name": ("Pavadinimas", "Name", "Название"),
    "telegram": ("Telegram", "Telegram", "Telegram"),
    "city": ("Miestas", "City / region", "Город / регион"),
    "description": ("Aprašymas", "Description", "Описание"),
    "about": ("▤ APIE PREKEIVĮ", "▤ ABOUT THE MERCHANT", "▤ О ПРОДАВЦЕ"),
    "security_title": ("🛡 SAUGUMO STATUSAS", "🛡 SECURITY STATUS", "🛡 СТАТУС БЕЗОПАСНОСТИ"),
    "catalog_approved": (
        "✓ Katalogo įrašas patvirtintas",
        "✓ Directory listing approved",
        "✓ Запись в каталоге одобрена",
    ),
    "rep_title": (
        "⭐ REPUTACIJOS STATISTIKA",
        "⭐ REPUTATION STATISTICS",
        "⭐ СТАТИСТИКА РЕПУТАЦИИ",
    ),
    "score": ("Bendra reputacija", "Overall reputation", "Общая репутация"),
    "positive": ("+REP", "+REP", "+REP"),
    "negative": ("−REP", "−REP", "−REP"),
    "status": ("Įrašo būsena", "Listing status", "Статус записи"),
    "trusted": ("✓ TRUSTED", "✓ TRUSTED", "✓ TRUSTED"),
    "disclaimer": (
        "Reputacijos rezultatai negarantuoja patikimumo.",
        "Reputation results do not guarantee trustworthiness.",
        "Результаты репутации не гарантируют надёжность.",
    ),
    "limited": (
        "Telegram ID nesusietas; tapatybės patvirtinimas ribotas.",
        "Telegram ID is not linked; identity assurance is limited.",
        "Telegram ID не связан; подтверждение личности ограничено.",
    ),
    "reviews": ("⭐ SKAITYTI ATSILIEPIMUS", "⭐ READ REVIEWS", "⭐ ЧИТАТЬ ОТЗЫВЫ"),
    "reviews_title": ("⭐ REDSAFE REPUTACIJA", "⭐ REDSAFE REPUTATION", "⭐ РЕПУТАЦИЯ REDSAFE"),
    "history": ("▤ ATSILIEPIMŲ ISTORIJA", "▤ REVIEW HISTORY", "▤ ИСТОРИЯ ОТЗЫВОВ"),
    "no_reviews": (
        "Patvirtintų atsiliepimų kol kas nėra.",
        "There are no approved reviews yet.",
        "Одобренных отзывов пока нет.",
    ),
    "review_author": ("Įvertino: {author}", "Reviewed by: {author}", "Оценил: {author}"),
    "anonymous": ("Autorius neviešinamas", "Author not published", "Автор не публикуется"),
    "legacy_review": (
        "Istorinis patvirtintas įvertinimas be komentaro.",
        "Historical accepted rating without a comment.",
        "Историческая принятая оценка без комментария.",
    ),
    "corrections": (
        "Statistika apima administratoriaus korekcijas; istorinių atsiliepimų skaičius "
        "gali skirtis.",
        "Statistics include administrative corrections; historical review counts may differ.",
        "Статистика учитывает корректировки администратора; число исторических отзывов "
        "может отличаться.",
    ),
    "filter_all": ("▤ VISI", "▤ ALL", "▤ ВСЕ"),
    "filter_pos": ("＋ REP", "＋ REP", "＋ REP"),
    "filter_neg": ("− REP", "− REP", "− REP"),
    "profile_back": ("👤 PREKEIVIO PROFILIS", "👤 MERCHANT PROFILE", "👤 ПРОФИЛЬ ПРОДАВЦА"),
    "check": ("🔎 REDSAFE PATIKRA", "🔎 REDSAFE CHECK", "🔎 ПРОВЕРКА REDSAFE"),
    "activity": ("👤 REDSAFE PROFILIS", "👤 REDSAFE PROFILE", "👤 ПРОФИЛЬ REDSAFE"),
    "city_prompt": (
        "Pasirink prekeivio miestą arba regioną.",
        "Choose the merchant city or region.",
        "Выберите город или регион продавца.",
    ),
    "name_prompt": (
        "Įvesk viešą prekeivio pavadinimą (2–64 simboliai).",
        "Enter the public merchant name (2–64 characters).",
        "Введите публичное название продавца (2–64 символа).",
    ),
    "description_prompt": (
        "Įvesk trumpą teisėtų prekių ar paslaugų aprašymą (10–600 simbolių). "
        "Nerašyk privačių kontaktų.",
        "Describe the lawful goods or services (10–600 characters). Do not "
        "include private contacts.",
        "Кратко опишите законные товары или услуги (10–600 символов). Не "
        "указывайте личные контакты.",
    ),
    "not_set": ("Nenustatyta", "Not set", "Не указано"),
    "edit_name": ("✎ PAVADINIMAS", "✎ NAME", "✎ НАЗВАНИЕ"),
    "edit_description": ("✎ APRAŠYMAS", "✎ DESCRIPTION", "✎ ОПИСАНИЕ"),
    "edit_city": ("📍 PAKEISTI MIESTĄ", "📍 CHANGE CITY", "📍 ИЗМЕНИТЬ ГОРОД"),
    "change": ("✎ REDAGUOTI PREKEIVĮ", "✎ EDIT MERCHANT", "✎ РЕДАКТИРОВАТЬ ПРОДАВЦА"),
    "confirm": ("✓ PATVIRTINTI", "✓ CONFIRM", "✓ ПОДТВЕРДИТЬ"),
    "reason": ("Priežastis", "Reason", "Причина"),
    "target_prompt": (
        "Įvesk jau botui žinomo prekeivio skaitinį Telegram ID. Nauja, nepatvirtinta "
        "tapatybė nebus kuriama.",
        "Enter a merchant Telegram numeric ID already known to the bot. An unknown "
        "identity will not be created.",
        "Введите числовой Telegram ID продавца, уже известного боту. Неизвестная "
        "личность не будет создана.",
    ),
    "admin_list": ("▤ PREKEIVIŲ SĄRAŠAS", "▤ MERCHANT LIST", "▤ СПИСОК ПРОДАВЦОВ"),
    "add": ("＋ PRIDĖTI PREKEIVĮ", "＋ ADD MERCHANT", "＋ ДОБАВИТЬ ПРОДАВЦА"),
    "settings": ("⚙ KATALOGO NUSTATYMAI", "⚙ DIRECTORY SETTINGS", "⚙ НАСТРОЙКИ КАТАЛОГА"),
    "admin_back": ("◀ ADMINISTRAVIMAS", "◀ ADMINISTRATION", "◀ АДМИНИСТРИРОВАНИЕ"),
    "manage_back": ("◀ PREKEIVIŲ VALDYMAS", "◀ MERCHANT MANAGEMENT", "◀ УПРАВЛЕНИЕ ПРОДАВЦАМИ"),
    "suspend": ("⏸ SUSTABDYTI", "⏸ SUSPEND", "⏸ ПРИОСТАНОВИТЬ"),
    "restore": ("▶ ATNAUJINTI", "▶ RESUME", "▶ ВОЗОБНОВИТЬ"),
    "remove": ("− PAŠALINTI ĮRAŠĄ", "− REMOVE LISTING", "− УДАЛИТЬ ЗАПИСЬ"),
    "empty": ("Įrašų kol kas nėra.", "No records yet.", "Записей пока нет."),
    "enabled": ("Katalogas", "Directory", "Каталог"),
    "publish_authors": (
        "Viešinti atsiliepimų autorius",
        "Publish review authors",
        "Публиковать авторов отзывов",
    ),
    "setting_title": ("Antraštė", "Title", "Заголовок"),
    "setting_intro": ("Aprašas", "Introduction", "Описание"),
    "title_prompt": (
        "Įvesk katalogo antraštę dabartine kalba (2–64 simboliai).",
        "Enter the directory title in the current language (2–64 characters).",
        "Введите заголовок каталога на текущем языке (2–64 символа).",
    ),
    "intro_prompt": (
        "Įvesk katalogo aprašą dabartine kalba (2–300 simbolių).",
        "Enter the directory introduction in the current language (2–300 characters).",
        "Введите описание каталога на текущем языке (2–300 символов).",
    ),
    "saved": ("✓ IŠSAUGOTA", "✓ SAVED", "✓ СОХРАНЕНО"),
    "status_APPROVED": ("PATVIRTINTA", "APPROVED", "ОДОБРЕНО"),
    "status_SUSPENDED": ("SUSTABDYTA", "SUSPENDED", "ПРИОСТАНОВЛЕНО"),
    "status_REMOVED": ("PAŠALINTA", "REMOVED", "УДАЛЕНО"),
    "err_invalid": (
        "Netinkama įvestis. Patikrink teksto ilgį ir pasirinktą reikšmę.",
        "Invalid input. Check the text length and selected value.",
        "Некорректные данные. Проверьте длину текста и выбранное значение.",
    ),
    "err_disabled": (
        "Katalogas šiuo metu išjungtas.",
        "The directory is currently disabled.",
        "Каталог сейчас отключён.",
    ),
    "err_missing": (
        "Įrašas nepasiekiamas, pašalintas arba nepatvirtintas.",
        "This listing is unavailable, removed or unapproved.",
        "Запись недоступна, удалена или не одобрена.",
    ),
    "err_identity": (
        "Reikia jau žinomo skaitinio Telegram naudotojo ID.",
        "An already known numeric Telegram user ID is required.",
        "Требуется уже известный числовой Telegram ID пользователя.",
    ),
    "err_blocked": (
        "Dėl aktyvaus SCAM įrašo šio profilio skelbti negalima.",
        "An active SCAM record prevents publishing this listing.",
        "Активная запись SCAM не позволяет опубликовать этот профиль.",
    ),
    "err_duplicate": (
        "Ši Telegram paskyra jau turi katalogo įrašą. Redaguok esamą įrašą.",
        "This Telegram account already has a listing. Edit the existing listing.",
        "У этого аккаунта Telegram уже есть запись. Отредактируйте существующую запись.",
    ),
    "err_stale": (
        "Šis valdiklis paseno. Atidaryk dabartinį įrašą arba meniu.",
        "This control is outdated. Open the current listing or menu.",
        "Этот элемент устарел. Откройте актуальную запись или меню.",
    ),
    "err_expired": (
        "Redagavimo arba įvesties sesija baigėsi. Atidaryk redaktorių iš naujo.",
        "The editing or input session expired. Reopen the editor.",
        "Сессия редактирования или ввода истекла. Откройте редактор заново.",
    ),
    "err_rate": (
        "Per daug užklausų. Pabandyk vėliau.",
        "Too many requests. Please try later.",
        "Слишком много запросов. Попробуйте позже.",
    ),
    "err_private": (
        "Katalogą atidaryk asmeniniame pokalbyje su botu.",
        "Open the directory in a private chat with the bot.",
        "Откройте каталог в личном чате с ботом.",
    ),
    "manage_title": ("PREKEIVIO VALDYMAS", "MERCHANT MANAGEMENT", "УПРАВЛЕНИЕ ПРОДАВЦОМ"),
    "editor_title": ("PREKEIVIO REDAGAVIMAS", "EDIT MERCHANT", "РЕДАКТИРОВАНИЕ ПРОДАВЦА"),
    "cancel_edit": ("× ATŠAUKTI", "× CANCEL", "× ОТМЕНА"),
    "step_back": ("◀ ATGAL", "◀ BACK", "◀ НАЗАД"),
    "delete_confirm": (
        "Pašalinti prekeivį „<b>{name}</b>“ iš viešo katalogo? Informacija ir "
        "reputacijos istorija bus išsaugota.",
        "Remove merchant “<b>{name}</b>” from the public directory? Information and "
        "reputation history will be retained.",
        "Удалить продавца «<b>{name}</b>» из публичного каталога? Информация и "
        "история репутации сохранятся.",
    ),
    "current_value": ("Dabartinė reikšmė", "Current value", "Текущее значение"),
    "contact": ("Kontaktas", "Contact", "Контакт"),
    "edit_contact": ("✎ KONTAKTAS", "✎ CONTACT", "✎ КОНТАКТ"),
    "contact_default": (
        "Susieta Telegram paskyra",
        "Linked Telegram account",
        "Связанный аккаунт Telegram",
    ),
    "contact_prompt": (
        "Įvesk viešą @username arba HTTPS nuorodą (iki 200 simbolių). „-“ grąžina "
        "susietos Telegram paskyros kontaktą; paskyros savininkas nesikeičia.",
        "Enter a public @username or HTTPS link (up to 200 characters). “-” restores "
        "the linked Telegram account contact; ownership stays unchanged.",
        "Введите публичный @username или HTTPS-ссылку (до 200 символов). «-» "
        "возвращает контакт связанного аккаунта Telegram; владелец не меняется.",
    ),
    "status_UNPUBLISHED": ("NEVIEŠINAMA", "NOT PUBLIC", "НЕ ОПУБЛИКОВАНО"),
}
