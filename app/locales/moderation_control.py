"""WARN management and evidence-based, recipient-only moderation outcomes."""

CATALOGS: dict[str, dict[str, str | tuple[str, str]]] = {
    "lt": {
        "external_title": "<b><u>Patvirtinti apribojimo panaikinimą</u></b>",
        "external_confirm": "Bus atliktas <b>{action}</b> ir panaikintas esamas šio naudotojo Telegram apribojimas <b>tik šioje grupėje</b>. Prieš vykdymą teisės ir apribojimo būsena bus patikrintos dar kartą.",
        "replaced": "<b>MUTE užbaigtas: pritaikytas {kind}.</b> Ankstesnis UNMUTE nebegalioja.",
        "warn_title": "⚠️ 𝗨𝗦𝗘𝗥 • 𝗪𝗔𝗥𝗡𝗘𝗗",
        "unwarn": "× UNWARN",
        "history": "📋 ISTORIJA",
        "remove_one": "− Anuliuoti 1 WARN",
        "remove_all": "× Anuliuoti visus WARN",
        "active_count": "Aktyvūs įspėjimai: <b>{count}/{limit}</b>",
        "select": "WARN #{id}",
        "no_reason": "Priežastis nenurodyta.",
        "single_confirm": "<b><u>Anuliuoti pasirinktą WARN?</u></b>\n\nBus anuliuotas tik aukščiau nurodytas įspėjimas. <b>Istorija išliks.</b>",
        "already_muted": (
            "Naudotojas jau nutildytas",
            "Telegram patvirtino, kad naudotojo siuntimo teisės jau apribotos. <b>Papildomas veiksmas neatliktas.</b>",
        ),
        "already_banned": (
            "Naudotojas jau užblokuotas",
            "Telegram patvirtino esamą blokavimą. <b>Papildomas veiksmas neatliktas.</b>",
        ),
        "already_unmuted": (
            "Naudotojas jau atnutildytas",
            "Telegram neberodo asmeninio nutildymo. <b>Grupės bendrosios taisyklės išlieka.</b>",
        ),
        "already_unbanned": (
            "Naudotojas jau atblokuotas",
            "Telegram neberodo šio naudotojo blokavimo. <b>Prisijungimas prie grupės automatiškai neatliekamas.</b>",
        ),
        "expired": (
            "Sankcija pasibaigusi",
            "Sankcijos laikas baigėsi, o Telegram neberodo atitinkamo apribojimo. <b>Papildomas veiksmas neatliktas.</b>",
        ),
        "foreign": (
            "Reikia patvirtinti apribojimo panaikinimą",
            "Šiam Telegram apribojimui nėra galiojančio panaikinimo patvirtinimo. <b>Paleisk UNBAN arba UNMUTE komandą iš naujo.</b> Apribojimas dar nepanaikintas.",
        ),
        "changed": (
            "Apribojimo būsena pasikeitė",
            "Ankstesnis patvirtinimas nebeatitinka dabartinės Telegram būsenos. <b>Paleisk komandą iš naujo.</b> Apribojimas nepanaikintas.",
        ),
        "busy": (
            "Veiksmas jau vykdomas",
            "Šiam naudotojui grupėje jau vykdomas moderavimo veiksmas. <b>Naujas veiksmas nepradėtas.</b>",
        ),
        "uncertain": (
            "Veiksmo rezultatas neaiškus",
            "Veiksmas galėjo būti atliktas, bet jo baigtis nepatvirtinta. <b>Automatiškai nekartojama.</b> Patikrinkite istoriją ir Telegram būseną.",
        ),
        "api_rejected": (
            "Telegram veiksmo nepatvirtino",
            "Telegram API atmetė užklausą arba nepatvirtino jos sėkmės. <b>Tiksli priežastis nepatvirtinta.</b> Patikrinkite istoriją.",
        ),
        "protected_target": (
            "Naudotojas apsaugotas",
            "Šis taikinys yra <b>grupės savininkas, administratorius arba pats botas</b>. Veiksmas neatliktas.",
        ),
        "scam": (
            "SCAM apsauga",
            "Veiksmą sustabdė <b>SAFECheck SCAM apsaugos taisyklė</b>. Esami apribojimai nepanaikinti.",
        ),
        "stale": (
            "Patvirtinimas nebegalioja",
            "Veiksmas jau atliktas, būsena pasikeitė arba patvirtinimas nebegalioja. <b>Atidarykite naują valdymą.</b>",
        ),
        "no_warnings": (
            "Aktyvių įspėjimų nėra",
            "Šis naudotojas šioje grupėje neturi <b>aktyvių WARN</b>. Istorija išsaugota.",
        ),
        "restriction": (
            "Esamas apribojimas",
            "Telegram rodo esamą apribojimą. <b>Naujas veiksmas neatliktas.</b> Jo kilmė patikimai nenustatyta.",
        ),
        "queued": ("Veiksmas laukia vykdymo", "Užduotis įrašyta. <b>Sėkmė dar nepatvirtinta.</b>"),
        "cancelled": (
            "Veiksmas neatliktas",
            "Šis veiksmas atšauktas arba nebuvo pritaikytas. <b>Patikrinkite istoriją.</b>",
        ),
        "duration_invalid": (
            "Netinkama trukmė",
            "Trukmė neatitinka leidžiamo intervalo arba veiksmo tipo. <b>Patikrinkite komandos argumentus.</b>",
        ),
        "supergroup": (
            "Reikalinga supergrupė",
            "Šiam veiksmui reikalinga <b>Telegram supergrupė</b>. Veiksmas neatliktas.",
        ),
    },
    "en": {
        "external_title": "<b><u>Confirm restriction removal</u></b>",
        "external_confirm": "<b>{action}</b> will remove this user's current Telegram restriction <b>in this group only</b>. Permissions and the restriction state will be checked again before execution.",
        "replaced": "<b>MUTE ended: {kind} applied.</b> The previous UNMUTE control is no longer valid.",
        "warn_title": "⚠️ 𝗨𝗦𝗘𝗥 • 𝗪𝗔𝗥𝗡𝗘𝗗",
        "unwarn": "× UNWARN",
        "history": "📋 HISTORY",
        "remove_one": "− Clear 1 WARN",
        "remove_all": "× Clear all WARNs",
        "active_count": "Active warnings: <b>{count}/{limit}</b>",
        "select": "WARN #{id}",
        "no_reason": "No reason provided.",
        "single_confirm": "<b><u>Clear this WARN?</u></b>\n\nOnly the warning shown above will be cleared. <b>History is preserved.</b>",
        "already_muted": (
            "User already muted",
            "Telegram confirms existing sending restrictions. <b>No additional action was taken.</b>",
        ),
        "already_banned": (
            "User already banned",
            "Telegram confirms an existing ban. <b>No additional action was taken.</b>",
        ),
        "already_unmuted": (
            "User already unmuted",
            "Telegram no longer reports a personal mute. <b>Group default rules still apply.</b>",
        ),
        "already_unbanned": (
            "User already unbanned",
            "Telegram no longer reports a ban. <b>The user is not automatically rejoined.</b>",
        ),
        "expired": (
            "Sanction expired",
            "The deadline passed and Telegram no longer reports the restriction. <b>No further action was taken.</b>",
        ),
        "foreign": (
            "Restriction removal needs confirmation",
            "There is no valid removal approval for this Telegram restriction. <b>Run UNBAN or UNMUTE again.</b> The restriction has not been lifted.",
        ),
        "changed": (
            "Restriction changed",
            "The previous approval no longer matches the current Telegram state. <b>Run the command again.</b> The restriction has not been lifted.",
        ),
        "busy": (
            "Action in progress",
            "A moderation action for this user in this group is in progress. <b>No new action was started.</b>",
        ),
        "uncertain": (
            "Action outcome unknown",
            "The action may have occurred but its outcome is unconfirmed. <b>No automatic replay.</b> Review history and Telegram state.",
        ),
        "api_rejected": (
            "Telegram did not confirm the action",
            "The API rejected the request or did not confirm success. <b>The precise cause is unconfirmed.</b> Review history.",
        ),
        "protected_target": (
            "Protected user",
            "The target is the <b>group owner, an administrator or this bot</b>. No action was taken.",
        ),
        "scam": (
            "SCAM protection",
            "A <b>SAFECheck SCAM protection rule</b> blocked the action. Existing restrictions were not lifted.",
        ),
        "stale": (
            "Confirmation expired",
            "The action completed, state changed or confirmation expired. <b>Open a new control panel.</b>",
        ),
        "no_warnings": (
            "No active warnings",
            "This user has no <b>active WARNs</b> in this group. History is preserved.",
        ),
        "restriction": (
            "Existing restriction",
            "Telegram reports an existing restriction. <b>No new action was taken.</b> Its origin is unconfirmed.",
        ),
        "queued": ("Action queued", "The task was recorded. <b>Success is not confirmed yet.</b>"),
        "cancelled": (
            "Action not applied",
            "The action was cancelled or never applied. <b>Review history.</b>",
        ),
        "duration_invalid": (
            "Invalid duration",
            "The duration is outside the allowed range or incompatible with the action. <b>Check the arguments.</b>",
        ),
        "supergroup": (
            "Supergroup required",
            "This action requires a <b>Telegram supergroup</b>. No action was taken.",
        ),
    },
    "ru": {
        "external_title": "<b><u>Подтвердить снятие ограничения</u></b>",
        "external_confirm": "<b>{action}</b> снимет текущее ограничение Telegram у этого пользователя <b>только в этой группе</b>. Перед выполнением права и состояние ограничения будут проверены повторно.",
        "replaced": "<b>MUTE завершён: применён {kind}.</b> Прежняя кнопка UNMUTE больше не действует.",
        "warn_title": "⚠️ 𝗨𝗦𝗘𝗥 • 𝗪𝗔𝗥𝗡𝗘𝗗",
        "unwarn": "× UNWARN",
        "history": "📋 ИСТОРИЯ",
        "remove_one": "− Снять 1 WARN",
        "remove_all": "× Снять все WARN",
        "active_count": "Активные предупреждения: <b>{count}/{limit}</b>",
        "select": "WARN #{id}",
        "no_reason": "Причина не указана.",
        "single_confirm": "<b><u>Снять выбранный WARN?</u></b>\n\nБудет снято только указанное выше предупреждение. <b>История сохранится.</b>",
        "already_muted": (
            "Пользователь уже заглушён",
            "Telegram подтвердил ограничения отправки. <b>Дополнительное действие не выполнено.</b>",
        ),
        "already_banned": (
            "Пользователь уже заблокирован",
            "Telegram подтвердил блокировку. <b>Дополнительное действие не выполнено.</b>",
        ),
        "already_unmuted": (
            "Пользователь уже может писать",
            "Telegram больше не показывает личный мут. <b>Общие правила группы сохраняются.</b>",
        ),
        "already_unbanned": (
            "Пользователь уже разблокирован",
            "Telegram больше не показывает блокировку. <b>Автоматического вступления в группу нет.</b>",
        ),
        "expired": (
            "Срок санкции истёк",
            "Срок истёк, и Telegram больше не показывает ограничение. <b>Дополнительных действий нет.</b>",
        ),
        "foreign": (
            "Нужно подтвердить снятие ограничения",
            "Для этого ограничения Telegram нет действующего подтверждения снятия. <b>Повторите UNBAN или UNMUTE.</b> Ограничение ещё не снято.",
        ),
        "changed": (
            "Ограничение изменилось",
            "Предыдущее подтверждение больше не соответствует текущему состоянию Telegram. <b>Повторите команду.</b> Ограничение не снято.",
        ),
        "busy": (
            "Действие выполняется",
            "Для пользователя в этой группе уже выполняется действие. <b>Новое действие не начато.</b>",
        ),
        "uncertain": (
            "Результат действия неизвестен",
            "Действие могло произойти, но результат не подтверждён. <b>Автоматического повтора нет.</b> Проверьте историю и Telegram.",
        ),
        "api_rejected": (
            "Telegram не подтвердил действие",
            "API отклонил запрос или не подтвердил успех. <b>Точная причина не установлена.</b> Проверьте историю.",
        ),
        "protected_target": (
            "Защищённый пользователь",
            "Цель — <b>владелец, администратор группы или этот бот</b>. Действие не выполнено.",
        ),
        "scam": (
            "Защита SCAM",
            "Действие остановлено <b>правилом защиты SAFECheck SCAM</b>. Существующие ограничения не сняты.",
        ),
        "stale": (
            "Подтверждение недействительно",
            "Действие завершено, состояние изменилось или подтверждение истекло. <b>Откройте новое управление.</b>",
        ),
        "no_warnings": (
            "Нет активных предупреждений",
            "У пользователя нет <b>активных WARN</b> в этой группе. История сохранена.",
        ),
        "restriction": (
            "Существующее ограничение",
            "Telegram показывает ограничение. <b>Новое действие не выполнено.</b> Его источник не подтверждён.",
        ),
        "queued": ("Действие в очереди", "Задача записана. <b>Успех ещё не подтверждён.</b>"),
        "cancelled": (
            "Действие не применено",
            "Действие отменено или не было применено. <b>Проверьте историю.</b>",
        ),
        "duration_invalid": (
            "Неверный срок",
            "Срок вне допустимого диапазона или не подходит действию. <b>Проверьте аргументы.</b>",
        ),
        "supergroup": (
            "Нужна супергруппа",
            "Действию нужна <b>супергруппа Telegram</b>. Действие не выполнено.",
        ),
    },
}
