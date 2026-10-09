"""Editor chrome is separate from the editable SAFECheck catalogs."""

TEXTS = {
    "too_long": (
        "Šio ekrano tekstas viršija Telegram 4096 simbolių ribą. Sutrumpink pasirinktus tekstus.",
        "This screen exceeds Telegram's 4096-character limit. Shorten the selected texts.",
        "Текст экрана превышает лимит Telegram в 4096 символов. Сократите выбранные тексты.",
    ),
    "title": ("🛡 CRIMSON SAFECHECK™ · UI STUDIO",) * 3,
    "intro": (
        "Keisk dizainą ir peržiūrėk jį čia. Visi vartotojų duomenys peržiūrose yra pavyzdiniai.",
        "Edit the design and preview it here. All user data in previews are samples.",
        "Меняйте дизайн и смотрите результат здесь. Все данные пользователей — примеры.",
    ),
    "buttons": ("🔘 Mygtukai", "🔘 Buttons", "🔘 Кнопки"),
    "texts": ("📝 Tekstai", "📝 Texts", "📝 Тексты"),
    "order": ("↕️ Mygtukų tvarka", "↕️ Button layout", "↕️ Порядок кнопок"),
    "preview": ("👀 Peržiūra", "👀 Preview", "👀 Просмотр"),
    "language": ("🌐 Kalba", "🌐 Language", "🌐 Язык"),
    "export": ("📦 Atsisiųsti dizainą", "📦 Download design", "📦 Скачать дизайн"),
    "back": ("◀ Atgal", "◀ Back", "◀ Назад"),
    "editor": ("◀ UI redaktorius", "◀ UI editor", "◀ Редактор UI"),
    "search": ("🔍 Rasti", "🔍 Search", "🔍 Найти"),
    "search_prompt": (
        "Parašyk žodį iš ieškomo teksto arba mygtuko.",
        "Type a word from the text or button you want to find.",
        "Введите слово из нужного текста или кнопки.",
    ),
    "empty": (
        "Nieko nerasta. Pabandyk kitą žodį.",
        "No matches. Try another word.",
        "Ничего не найдено. Попробуйте другое слово.",
    ),
    "edit": ("✏️ Keisti", "✏️ Edit", "✏️ Изменить"),
    "reset": ("↻ Grąžinti numatytą", "↻ Restore default", "↻ Вернуть исходный"),
    "reset_layout": ("↻ Grąžinti tvarką", "↻ Restore layout", "↻ Вернуть порядок"),
    "current": ("Dabartinė reikšmė", "Current value", "Текущее значение"),
    "prompt": (
        "Atsiųsk naują tekstą. Emoji gali kopijuoti tiesiai į tekstą.",
        "Send the new text. You can paste emoji directly into it.",
        "Отправьте новый текст. Эмодзи можно вставить прямо в него.",
    ),
    "placeholders": (
        "Išsaugok šiuos kintamuosius: {fields}",
        "Keep these placeholders: {fields}",
        "Сохраните эти переменные: {fields}",
    ),
    "saved": (
        "✓ Išsaugota testiniame dizaine.",
        "✓ Saved in the test design.",
        "✓ Сохранено в тестовом дизайне.",
    ),
    "conflict": (
        "Šis tekstas jau pakeistas. Atidaryk jį iš naujo.",
        "This text changed while you were editing. Open it again.",
        "Этот текст уже изменён. Откройте его заново.",
    ),
    "invalid": (
        "Tekstas netinkamas. Mygtukas: iki 64 simbolių, viena eilutė. Tekstas: iki 3500. Išsaugok kintamuosius ir patikrink HTML žymas.",
        "Invalid text. Button: up to 64 characters, one line. Text: up to 3500. Keep placeholders and check HTML tags.",
        "Неверный текст. Кнопка: до 64 символов, одна строка. Текст: до 3500. Сохраните переменные и проверьте HTML.",
    ),
    "layout_prompt": (
        "Parašyk visų mygtukų numerius norima tvarka. Viena teksto eilutė = viena mygtukų eilutė; iki 3 mygtukų eilutėje. Kiekvieną numerį naudok tik kartą.\n\nDabartinis išdėstymas:\n{rows}",
        "Send all button numbers in the desired order. One line = one button row; up to 3 per row. Use every number exactly once.\n\nCurrent layout:\n{rows}",
        "Отправьте все номера кнопок в нужном порядке. Одна строка = ряд кнопок, до 3 в ряду. Каждый номер ровно один раз.\n\nТекущий порядок:\n{rows}",
    ),
    "layout_invalid": (
        "Patikrink numerius: visi turi būti panaudoti po vieną, iki 3 vienoje eilutėje.",
        "Check the numbers: use each exactly once, up to 3 per row.",
        "Проверьте номера: каждый ровно один раз, до 3 в строке.",
    ),
    "home": ("Pagrindinis meniu", "Main menu", "Главное меню"),
    "home_user": ("Pagrindinis · vartotojas", "Main · user", "Главное · пользователь"),
    "home_admin": (
        "Pagrindinis · administratorius",
        "Main · administrator",
        "Главное · администратор",
    ),
    "admin": ("Administravimas", "Administration", "Администрирование"),
    "info": ("Informacija", "Information", "Информация"),
    "scam": ("SCAM · ID žinomas", "SCAM · known ID", "SCAM · ID известен"),
    "scam_unknown": ("SCAM · ID nežinomas", "SCAM · unknown ID", "SCAM · ID неизвестен"),
    "trusted": ("TRUSTED",) * 3,
    "profile": ("Profilis / tikrinimas", "Profile / lookup", "Профиль / проверка"),
    "demo": (
        "Tai tik dizaino peržiūra. Veiksmas nevykdomas.",
        "This is a design preview. No action is performed.",
        "Это просмотр дизайна. Действие не выполняется.",
    ),
    "denied": (
        "Šis testinis botas prieinamas tik administratoriams privačiai.",
        "This test bot is available only to administrators in private.",
        "Тестовый бот доступен только администраторам в личном чате.",
    ),
    "stale": (
        "Atidaryk redaktorių iš naujo per /ui.",
        "Reopen the editor with /ui.",
        "Откройте редактор заново: /ui.",
    ),
    "error": (
        "Išsaugoti ar parodyti nepavyko. Pabandyk dar kartą.",
        "Could not save or display. Please try again.",
        "Не удалось сохранить или показать. Попробуйте снова.",
    ),
}


def text(key: str, lang: str = "lt", **values) -> str:
    return TEXTS[key][{"lt": 0, "en": 1, "ru": 2}.get(lang, 0)].format(**values)
