"""Merchant directory text; each row provides LT, EN and RU together."""

from app.locales.merchant_workflows import ROWS as WORKFLOWS

ROWS = {
    "button": ("🏪 PREKEIVIAI", "🏪 MERCHANTS", "🏪 ПРОДАВЦЫ"),
    "admin": ("🏪 PREKEIVIŲ VALDYMAS", "🏪 MERCHANT MANAGEMENT", "🏪 УПРАВЛЕНИЕ ПРОДАВЦАМИ"),
    "title": ("🏪 𝑹𝑬𝑫𝑺𝑨𝑭𝑬 𝑷𝑹𝑬𝑲𝑬𝑰𝑽𝑰𝑨𝑰", "🏪 REDSAFE MERCHANTS", "🏪 ПРОДАВЦЫ REDSAFE"),
    "intro": (
        "Atrask registruotus prekeivius ir peržiūrėk jų reputaciją bei atsiliepimų istoriją.",
        "Discover registered merchants and explore their reputation and review history.",
        "Находите зарегистрированных продавцов, их репутацию и историю отзывов.",
    ),
    "choose": ("Pasirink veiksmą žemiau.", "Choose an action below.", "Выберите действие ниже."),
    "cities": ("📍 MIESTAI", "📍 CITIES", "📍 ГОРОДА"),
    "cities_title": ("📍 REDSAFE MIESTAI", "📍 REDSAFE CITIES", "📍 ГОРОДА REDSAFE"),
    "cities_intro": (
        "Pasirink miestą ir peržiūrėk registruotus prekeivius.",
        "Choose a city to see registered merchants.",
        "Выберите город для просмотра зарегистрированных продавцов.",
    ),
    "vilnius": ("VILNIUS", "VILNIUS", "ВИЛЬНЮС"),
    "kaunas": ("KAUNAS", "KAUNAS", "КАУНАС"),
    "klaipeda": ("KLAIPĖDA", "KLAIPĖDA", "КЛАЙПЕДА"),
    "siauliai": ("ŠIAULIAI", "ŠIAULIAI", "ШАУЛЯЙ"),
    "panevezys": ("PANEVĖŽYS", "PANEVĖŽYS", "ПАНЕВЕЖИС"),
    "pajurys": ("PAJŪRYS", "COASTAL REGION", "ПРИМОРСКИЙ РЕГИОН"),
    "all_merchants": ("🌐 VISI PREKEIVIAI", "🌐 ALL MERCHANTS", "🌐 ВСЕ ПРОДАВЦЫ"),
    "search": ("🔎 IEŠKOTI PREKEIVIO", "🔎 FIND A MERCHANT", "🔎 НАЙТИ ПРОДАВЦА"),
    "search_title": ("🔎 PREKEIVIO PAIEŠKA", "🔎 MERCHANT SEARCH", "🔎 ПОИСК ПРОДАВЦА"),
    "search_prompt": (
        "Įveskite registruoto prekeivio pavadinimą arba Telegram @username.",
        "Enter a registered merchant name or Telegram @username.",
        "Введите название продавца или Telegram @username.",
    ),
    "query": ("Ieškota: {query}", "Search: {query}", "Запрос: {query}"),
    "not_found": ("× PREKEIVIS NERASTAS", "× MERCHANT NOT FOUND", "× ПРОДАВЕЦ НЕ НАЙДЕН"),
    "no_results": (
        "Pagal pateiktą užklausą REDSAFE kataloge registruotų prekeivių nerasta.\n"
        "\n"
        "Tai nereiškia, kad naudotojas yra nepatikimas arba įtrauktas į SCAM registrą.",
        "No registered REDSAFE merchants match this query.\n"
        "\n"
        "This does not mean that the user is untrustworthy or listed in the SCAM "
        "registry.",
        "В каталоге REDSAFE нет продавцов по этому запросу.\n"
        "\n"
        "Это не означает, что пользователь ненадёжен или внесён в SCAM-реестр.",
    ),
    "empty_city": (
        "Šiame mieste registruotų prekeivių kol kas nėra.",
        "There are no registered merchants in this city yet.",
        "В этом городе пока нет зарегистрированных продавцов.",
    ),
    "back": ("◀ PREKEIVIŲ MENIU", "◀ MERCHANT MENU", "◀ МЕНЮ ПРОДАВЦОВ"),
    "home": ("◀ PAGRINDINIS MENIU", "◀ MAIN MENU", "◀ ГЛАВНОЕ МЕНЮ"),
    "list_back": ("◀ PREKEIVIŲ SĄRAŠAS", "◀ MERCHANT LIST", "◀ СПИСОК ПРОДАВЦОВ"),
    "cities_back": ("◀ MIESTAI", "◀ CITIES", "◀ ГОРОДА"),
    "mine": ("👤 MANO ĮRAŠAS", "👤 MY LISTING", "👤 МОЯ ЗАПИСЬ"),
}

ROWS.update(WORKFLOWS)
CATALOGS = {
    lang: {
        ("error.merchant_" + key[4:] if key.startswith("err_") else "mc." + key): values[i]
        for key, values in ROWS.items()
    }
    for i, lang in enumerate(("lt", "en", "ru"))
}
