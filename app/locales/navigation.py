"""Stage 2 navigation labels; stable callback identifiers are independent of text."""

import re

LABELS = {
    "button.lookup": ("🔎 TIKRINTI", "🔎 CHECK", "🔎 ПРОВЕРИТЬ"),
    "button.profile": ("👤 PROFILIS", "👤 PROFILE", "👤 ПРОФИЛЬ"),
    "button.report": ("📩 PRANEŠTI", "📩 REPORT", "📩 СООБЩИТЬ"),
    "button.top": ("🏆 TOP 10", "🏆 TOP 10", "🏆 ТОП 10"),
    "button.scams": ("🚩 SCAM REGISTRAS", "🚩 SCAM REGISTRY", "🚩 РЕЕСТР SCAM"),
    "button.rep": ("⭐ ĮVERTINTI", "⭐ RATE", "⭐ ОЦЕНИТЬ"),
    "button.info": ("ⓘ INFORMACIJA", "ⓘ INFORMATION", "ⓘ ИНФОРМАЦИЯ"),
    "button.language": ("🌐 KALBA", "🌐 LANGUAGE", "🌐 ЯЗЫК"),
    "button.admin": ("♛ ADMINISTRAVIMAS", "♛ ADMINISTRATION", "♛ АДМИНИСТРИРОВАНИЕ"),
    "button.close": ("× UŽDARYTI", "× CLOSE", "× ЗАКРЫТЬ"),
    "button.cancel": ("× Atšaukti", "× Cancel", "× Отмена"),
    "button.back": ("◀ Atgal", "◀ Back", "◀ Назад"),
    "button.home": ("◀ PAGRINDINIS MENIU", "◀ MAIN MENU", "◀ ГЛАВНОЕ МЕНЮ"),
    "button.back_admin": ("◀ ADMINISTRAVIMAS", "◀ ADMINISTRATION", "◀ АДМИНИСТРИРОВАНИЕ"),
    "button.approve": ("✓ Patvirtinti", "✓ Approve", "✓ Подтвердить"),
    "button.reject": ("× Atmesti", "× Reject", "× Отклонить"),
    "button.confirm_action": (
        "✓ Patvirtinti veiksmą",
        "✓ Confirm action",
        "✓ Подтвердить действие",
    ),
    "button.pending": ("📩 LAUKIANTYS PRANEŠIMAI", "📩 PENDING REPORTS", "📩 ОЖИДАЮЩИЕ ЖАЛОБЫ"),
    "button.registry": ("🚩 SCAM REGISTRAS", "🚩 SCAM REGISTRY", "🚩 РЕЕСТР SCAM"),
    "button.add_scam": ("＋ PRIDĖTI SCAM", "＋ ADD SCAM", "＋ ДОБАВИТЬ SCAM"),
    "button.remove_scam": ("− PAŠALINTI SCAM", "− REMOVE SCAM", "− УДАЛИТЬ SCAM"),
    "button.rep_pending": ("📥 LAUKIANTYS REP", "📥 PENDING REP", "📥 ОЖИДАЮЩИЕ REP"),
    "button.rep_add": ("＋ PRIDĖTI REP", "＋ ADD REP", "＋ ДОБАВИТЬ REP"),
    "button.rep_sub": ("− ATIMTI REP", "− SUBTRACT REP", "− УБАВИТЬ REP"),
    "button.rep_reset": ("× IŠVALYTI REPUTACIJĄ", "× CLEAR REPUTATION", "× ОЧИСТИТЬ РЕПУТАЦИЮ"),
    "button.top_include": ("＋ Įtraukti į TOP 10", "＋ Include in TOP 10", "＋ Включить в ТОП 10"),
    "button.top_exclude": (
        "− Pašalinti iš TOP 10",
        "− Exclude from TOP 10",
        "− Исключить из ТОП 10",
    ),
    "button.users": ("▤ NAUDOTOJŲ SĄRAŠAS", "▤ USER LIST", "▤ СПИСОК ПОЛЬЗОВАТЕЛЕЙ"),
    "button.stats": ("📊 STATISTIKA", "📊 STATISTICS", "📊 СТАТИСТИКА"),
    "button.status": ("◉ BOTO BŪSENA", "◉ BOT STATUS", "◉ СОСТОЯНИЕ БОТА"),
    "button.audit": (
        "▤ BENDROJI VEIKSMŲ ISTORIJA",
        "▤ GENERAL AUDIT HISTORY",
        "▤ ОБЩАЯ ИСТОРИЯ ДЕЙСТВИЙ",
    ),
    "button.search": ("🔎 Ieškoti", "🔎 Search", "🔎 Поиск"),
    "button.lookup_other": ("🔎 Tikrinti kitą", "🔎 Check another user", "🔎 Проверить другого"),
    "button.group_approve": ("✓ Patvirtinti grupę", "✓ Approve group", "✓ Одобрить группу"),
    "button.group_rights": (
        "✓ Patikrinti ir įjungti",
        "✓ Check and enable",
        "✓ Проверить и включить",
    ),
    "button.groups": ("👥 GRUPIŲ PATVIRTINIMAI", "👥 GROUP APPROVALS", "👥 ОДОБРЕНИЕ ГРУПП"),
    "ac.mine": ("📢 MANO REKLAMOS", "📢 MY ADS", "📢 МОЯ РЕКЛАМА"),
    "admin_access.button": ("♛ ADMINISTRATORIAI", "♛ ADMINISTRATORS", "♛ АДМИНИСТРАТОРЫ"),
    "admin_help.button": ("ⓘ INSTRUKCIJA", "ⓘ GUIDE", "ⓘ ИНСТРУКЦИЯ"),
    "tm.menu": ("✓ PATIKIMI NAUDOTOJAI", "✓ TRUSTED USERS", "✓ ДОВЕРЕННЫЕ ПОЛЬЗОВАТЕЛИ"),
    "group_help.button": ("📁 GROUP HELP", "📁 GROUP HELP", "📁 GROUP HELP"),
    "nav.scam": ("🛡 SCAM IR PATIKIMUMAS", "🛡 SCAM AND TRUST", "🛡 SCAM И ДОВЕРИЕ"),
    "nav.reputation": ("⭐ REPUTACIJA", "⭐ REPUTATION", "⭐ РЕПУТАЦИЯ"),
    "nav.group": ("📁 GROUP HELP", "📁 GROUP HELP", "📁 GROUP HELP"),
    "nav.ads": ("📢 AUTO ADS", "📢 AUTO ADS", "📢 AUTO ADS"),
    "nav.users": ("👥 VARTOTOJAI", "👥 USERS", "👥 ПОЛЬЗОВАТЕЛИ"),
    "nav.system": ("⚙ SISTEMA", "⚙ SYSTEM", "⚙ СИСТЕМА"),
    "nav.owner": ("♛ SAVININKO VALDYMAS", "♛ OWNER CONTROLS", "♛ УПРАВЛЕНИЕ ВЛАДЕЛЬЦА"),
    "nav.scam_history": ("▤ VEIKSMŲ ISTORIJA", "▤ ACTION HISTORY", "▤ ИСТОРИЯ ДЕЙСТВИЙ"),
    "nav.rep_history": ("▤ REP ISTORIJA", "▤ REP HISTORY", "▤ ИСТОРИЯ REP"),
    "nav.top": ("🏆 TOP 10 VALDYMAS", "🏆 TOP 10 MANAGEMENT", "🏆 УПРАВЛЕНИЕ ТОП 10"),
    "nav.search_user": ("🔎 IEŠKOTI NAUDOTOJO", "🔎 SEARCH USERS", "🔎 НАЙТИ ПОЛЬЗОВАТЕЛЯ"),
    "nav.view_profile": ("👤 PROFILIO PERŽIŪRA", "👤 VIEW PROFILE", "👤 ПРОСМОТР ПРОФИЛЯ"),
    "nav.groups": ("👥 MANO GRUPĖS", "👥 MY GROUPS", "👥 МОИ ГРУППЫ"),
    "nav.moderation_history": (
        "▤ MODERAVIMO ISTORIJA",
        "▤ MODERATION HISTORY",
        "▤ ИСТОРИЯ МОДЕРАЦИИ",
    ),
    "nav.commands": ("ⓘ PAGALBA IR KOMANDOS", "ⓘ HELP AND COMMANDS", "ⓘ ПОМОЩЬ И КОМАНДЫ"),
    "nav.moderation": ("⚖ MODERAVIMAS", "⚖ MODERATION", "⚖ МОДЕРАЦИЯ"),
    "nav.warnings": ("⚠ ĮSPĖJIMAI", "⚠ WARNINGS", "⚠ ПРЕДУПРЕЖДЕНИЯ"),
    "nav.protection": ("🛡 APSAUGA", "🛡 PROTECTION", "🛡 ЗАЩИТА"),
    "nav.settings": ("⚙ NUSTATYMAI", "⚙ SETTINGS", "⚙ НАСТРОЙКИ"),
    "nav.history": ("▤ VEIKSMŲ ISTORIJA", "▤ ACTION HISTORY", "▤ ИСТОРИЯ ДЕЙСТВИЙ"),
    "nav.to_groups": ("◀ MANO GRUPĖS", "◀ MY GROUPS", "◀ МОИ ГРУППЫ"),
    "nav.clients": ("👥 KLIENTAI", "👥 CLIENTS", "👥 КЛИЕНТЫ"),
    "nav.ads_list": ("▤ MANO REKLAMOS", "▤ MY ADS", "▤ МОЯ РЕКЛАМА"),
    "nav.create_ad": ("＋ SUKURTI REKLAMĄ", "＋ CREATE AD", "＋ СОЗДАТЬ РЕКЛАМУ"),
    "nav.choose": ("Pasirink veiksmą.", "Choose an action.", "Выберите действие."),
    "nav.empty": ("Įrašų nėra.", "No records.", "Записей нет."),
    "nav.ads_settings_note": (
        "Intervalas, grupės, klientas ir galiojimas valdomi kiekvienai reklamai atskirai. Pasirink reklamą.",
        "Intervals, groups, clients and validity are managed per ad. Select an ad.",
        "Интервал, группы, клиент и срок задаются для каждой рекламы отдельно. Выберите рекламу.",
    ),
    "nav.ads_stats": (
        "Siuntimo bandymai: {total}\nSėkmingi: {sent}\nNepavykę: {failed}\nNeaiški baigtis: {uncertain}\nAtšaukti: {cancelled}\n\nTai kaupiamų siuntimo bandymų duomenys. Atskirų reklamų statistika pateikiama jų valdymo ekranuose.",
        "Delivery attempts: {total}\nSuccessful: {sent}\nFailed: {failed}\nUncertain: {uncertain}\nCancelled: {cancelled}\n\nThese are recorded delivery attempts. Per-ad statistics are available in each ad panel.",
        "Попыток отправки: {total}\nУспешных: {sent}\nНеудачных: {failed}\nНеизвестный результат: {uncertain}\nОтменённых: {cancelled}\n\nДанные сохранённых попыток. Статистика каждой рекламы доступна в её панели.",
    ),
    "nav.group_help": (
        "Komandos: /ban, /unban, /mute, /unmute, /warn, /unwarn, /kick, /warns.\n\nKomandas rašyk valdomoje grupėje. Pasirinktos grupės nustatymai ir istorija atskiri. Reikia atitinkamų Telegram administratoriaus ir boto teisių.",
        "Commands: /ban, /unban, /mute, /unmute, /warn, /unwarn, /kick, /warns.\n\nUse commands in the managed group. Each group has its own settings and history. The actor and bot need the appropriate Telegram rights.",
        "Команды: /ban, /unban, /mute, /unmute, /warn, /unwarn, /kick, /warns.\n\nИспользуйте команды в нужной группе. Настройки и история отдельны для каждой группы. Нужны соответствующие права администратора и бота.",
    ),
    "nav.removed": (
        "Ši funkcija pašalinta. Atidaryk pagrindinį meniu.",
        "This feature has been removed. Open the main menu.",
        "Эта функция удалена. Откройте главное меню.",
    ),
}
# Stable action labels from the requested moderation layout.
for _command in ("BAN", "UNBAN", "MUTE", "UNMUTE", "WARN", "KICK"):
    LABELS["lm." + _command] = (_command,) * 3
for _prefix in ("ws.", "checks."):
    for _command in ("KICK", "MUTE", "BAN"):
        LABELS[_prefix + _command] = (_command,) * 3

LABELS["nav.sanction_row"] = (
    "<b>#{id} · {kind}</b> · {status}\n👤 <code>{user}</code> · ♛ <code>{admin}</code>\n{time} UTC\nPriežastis: {reason}",
    "<b>#{id} · {kind}</b> · {status}\n👤 <code>{user}</code> · ♛ <code>{admin}</code>\n{time} UTC\nReason: {reason}",
    "<b>#{id} · {kind}</b> · {status}\n👤 <code>{user}</code> · ♛ <code>{admin}</code>\n{time} UTC\nПричина: {reason}",
)
LABELS.update(
    {
        "button.positive": ("＋ REP",) * 3,
        "button.negative": ("− REP",) * 3,
        "button.preview": ("👁 Peržiūra", "👁 Preview", "👁 Просмотр"),
        "button.next": ("▶ Kitas", "▶ Next", "▶ Далее"),
        "button.reason": ("✎ Priežastis", "✎ Reason", "✎ Причина"),
        "button.evidence": ("▤ Įrodymai", "▤ Evidence", "▤ Доказательства"),
    }
)
CATALOGS = {
    lang: {key: text[i] for key, text in LABELS.items()}
    for i, lang in enumerate(("lt", "en", "ru"))
}


def standardize(catalogs):
    """Normalize existing short control labels, without touching notices or content."""
    replacements = {
        "◀️": "◀",
        "▶️": "▶",
        "‹": "◀",
        "›": "▶",
        "⬅️": "◀",
        "⬅": "◀",
        "✅": "✓",
        "✔️": "✓",
        "❌": "×",
        "➕": "＋",
        "➖": "−",
        "📋": "▤",
        "📜": "▤",
        "🔍": "🔎",
        "⌕": "🔎",
        "⚙️": "⚙",
    }
    controls = {
        "button.check_profile": "👤",
        "button.positive": "＋",
        "button.negative": "−",
        "admin_help.section.basics": "ⓘ",
        "admin_help.section.scam": "🛡",
        "admin_help.section.permissions": "♛",
        "admin_help.section.reputation": "⭐",
        "admin_help.section.top": "🏆",
        "admin_help.section.reports": "📩",
        "tm.clear": "×",
        "tm.search": "🔎",
        "sm.id": "＋",
        "sm.username": "＋",
        "ac.assign": "＋",
        "ac.change": "👤",
        "ac.unlink": "×",
        "ua.groups": "👥",
        "ua.select_groups": "👥",
        "ua.deliveries": "▤",
        "ws.warned": "▤",
        "ws.OFF": "×",
        "checks.OFF": "×",
        "checks.WARN": "⚠",
        "checks.title": "🛡",
        "checks.words": "▤",
        "bw.title": "▤",
        "checks.join": "✓",
        "checks.arabic": "🛡",
        "checks.han": "🛡",
        "checks.cyrillic": "🛡",
        "checks.rule.arabic": "🛡",
        "checks.rule.han": "🛡",
        "checks.rule.cyrillic": "🛡",
        "checks.photo": "👤",
        "checks.rule.photo": "👤",
    }
    for entries in catalogs.values():
        for key, header_symbol in {
            "p.admin": "♛",
            "p.audit_title": "▤",
            "group.TITLE": "👥",
            "gh.title": "📁",
        }.items():
            if key in entries:
                entries[key] = re.sub(
                    r"(\n\n)[^<\n]+(?=<b>)",
                    r"\g<1>" + header_symbol + " ",
                    entries[key],
                    count=1,
                )
        for key, text in list(entries.items()):
            if (
                "\n" in text
                or len(text) > 100
                or key.startswith(("p.", "group.", "audit.", "diagnostic.", "core.", "command."))
            ):
                continue
            for previous, replacement in replacements.items():
                if text.startswith(previous):
                    text = replacement + text[len(previous) :]
                    break
            # Known semantic control families also lose unrelated legacy icons.
            leaf = key.rsplit(".", 1)[-1]
            symbol = (
                "◀"
                if leaf in {"back", "back_admin", "to_list", "check_home", "rep_list"}
                else "▤"
                if leaf in {"history", "audit", "list", "log"}
                else "🔎"
                if leaf in {"search", "lookup", "lookup_other"}
                else "⚙"
                if leaf == "settings"
                else "×"
                if leaf in {"cancel", "reject", "close", "delete"}
                else "＋"
                if leaf in {"add", "new"}
                else "−"
                if leaf in {"remove", "revoke"}
                else "✓"
                if leaf in {"approve", "confirm", "save", "submit"}
                else None
            )
            symbol = controls.get(key, symbol)
            if symbol:
                # Keep translated words and counters; replace the leading icon only.
                first, sep, rest = text.partition(" ")
                if sep and not any(c.isalpha() for c in first):
                    text = symbol + " " + rest
                elif not text.startswith(symbol):
                    text = symbol + " " + text
            entries[key] = text
