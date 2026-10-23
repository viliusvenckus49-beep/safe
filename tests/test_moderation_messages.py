"""Exact receipt layout and real dispatcher/SQL integration; no production API calls."""

from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Chat, Message
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select
from test_group_moderation_foundation import CHAT, OTHER, NativeBot
from test_local_moderation_stage2 import (
    confirmed,
    enabled,
    prepared,
)
from test_local_moderation_stage2 import (
    local_journey as local_journey,
)

from app.bot.callbacks import LocalAction
from app.bot.local_admin import receipt_text
from app.bot.local_moderation import LocalModerationExecutor
from app.bot.moderation_messages import (
    BRAND,
    HEADINGS,
    duration_text,
    format_success,
    identity_label,
)
from app.i18n import t, use_language
from app.models import AuditEvent, GroupModerationDraft, GroupSanction, ScamRecord, User, now
from app.services import Service


class SafeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.tags = []
        self.values = []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "code"} and attrs == []
        self.stack.append(tag)
        self.tags.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, data):
        self.values.append(data)


def render(kind, **values):
    return format_success(kind, 123456789, user="@example", admin="@admin", **values)


@pytest.mark.parametrize("kind", HEADINGS)
def test_exact_english_templates(kind):
    with use_language("en"):
        text = render(kind, reason="Spam", duration_seconds=604800, warnings=2)
    fields = [
        "<b>⚠️ 𝗨𝗦𝗘𝗥 • 𝗪𝗔𝗥𝗡𝗘𝗗</b>" if kind == "WARN" else HEADINGS[kind],
        "👤 <b>User:</b> @example",
        "🆔 <b>Telegram ID:</b> <code>123456789</code>",
    ]
    if kind in {"BAN", "MUTE"}:
        fields.append("⏳ <b>Duration:</b> 7 days")
    if kind == "WARN":
        fields.append("⚠️ <b>Warnings:</b> 2/3")
    fields += ["📋 <b>Reason:</b> Spam", "👑 <b>Admin:</b> @admin", BRAND]
    assert text == "\n\n".join(fields)
    assert "STATUS" not in text and "━━" not in text
    parser = SafeHTML()
    parser.feed(text)
    assert parser.stack == []


@pytest.mark.parametrize("kind", HEADINGS)
@pytest.mark.parametrize("reason", ["", "   \n\t", "Rule violation"])
def test_optional_reason_does_not_leave_empty_fields(kind, reason):
    with use_language("en"):
        text = render(kind, reason=reason)
    assert ("📋 <b>Reason:</b>" in text) == bool(reason.strip())
    assert "\n\n\n" not in text
    assert "⚠️ <b>Warnings:</b>" not in text
    assert ("⏳ <b>Duration:</b> Permanent" in text) == (kind in {"BAN", "MUTE"})


@pytest.mark.parametrize(
    "lang,labels",
    [
        (
            "lt",
            (
                "Naudotojas",
                "Trukmė",
                "Priežastis",
                "Administratorius",
                "Įspėjimai",
                "Neterminuotai",
            ),
        ),
        ("en", ("User", "Duration", "Reason", "Admin", "Warnings", "Permanent")),
        ("ru", ("Пользователь", "Срок", "Причина", "Администратор", "Предупреждения", "Бессрочно")),
    ],
)
@pytest.mark.parametrize("kind", HEADINGS)
def test_localized_receipts(lang, labels, kind):
    user, duration, reason, admin, warnings, permanent = labels
    with use_language(lang):
        text = render(kind, reason="test", warnings=2)
    for label in (user, reason, admin):
        assert f"<b>{label}:</b>" in text
    if kind in {"BAN", "MUTE"}:
        assert f"<b>{duration}:</b> {permanent}" in text
    if kind == "WARN":
        assert f"<b>{warnings}:</b> 2/3" in text


@pytest.mark.parametrize(
    "lang,seconds,kind,expected",
    [
        ("en", 1800, "BAN", "30 minutes"),
        ("en", 3600, "BAN", "1 hour"),
        ("en", 86400, "BAN", "1 day"),
        ("en", 86400, "MUTE", "24 hours"),
        ("en", 604800, "BAN", "7 days"),
        ("en", 2592000, "BAN", "30 days"),
        ("en", 604799, "BAN", "7 days"),
        ("lt", 1800, "MUTE", "30 minučių"),
        ("lt", 3600, "BAN", "1 valanda"),
        ("lt", 86400, "MUTE", "24 valandos"),
        ("lt", 604800, "BAN", "7 dienos"),
        ("lt", 2592000, "BAN", "30 dienų"),
        ("ru", 1800, "MUTE", "30 минут"),
        ("ru", 3600, "BAN", "1 час"),
        ("ru", 86400, "MUTE", "24 часа"),
        ("ru", 604800, "BAN", "7 дней"),
        ("ru", 2592000, "BAN", "30 дней"),
    ],
)
def test_duration_localization(lang, seconds, kind, expected):
    with use_language(lang):
        assert duration_text(seconds, kind) == expected


def test_escaped_unicode_values_cannot_inject_html_or_layout():
    text = format_success(
        "WARN",
        123,
        user='@<evil>&"',
        admin="Žąsis 🛡 <b>x</b>",
        reason="\n <script>bad</script>\n\n A & B <b>fake</b>\t",
        warnings=1,
    )
    parser = SafeHTML()
    parser.feed(text)
    assert parser.stack == []
    visible = "".join(parser.values)
    assert '@<evil>&"' in visible and "Žąsis 🛡 <b>x</b>" in visible
    assert "<script>bad</script> A & B <b>fake</b>" in visible
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "\n\n\n" not in text


@pytest.mark.parametrize(
    "username,name,expected",
    [
        ("example", "Name", "@example"),
        (None, "Žąsis & Co 🛡", "Žąsis & Co 🛡"),
        (None, "Vartotojas", "123"),
        (None, "", "123"),
    ],
)
def test_identity_fallbacks(username, name, expected):
    user = User(telegram_id=123, username=username, display_name=name)
    assert identity_label(user, 123) == expected
    assert identity_label(user, 456) == "456"
    assert identity_label(None, 123) == "123"


@pytest.mark.asyncio
async def test_warning_counter_is_scoped_stable_and_read_only(database):
    native = NativeBot()
    await enabled(database, native)
    rows = []
    drafts = []
    for index in range(3):
        draft = await prepared(database, native, "WARN", key=f"warning-{index}")
        rows.append(await confirmed(database, native, draft))
        drafts.append(draft)
    async with database() as session:
        session.add_all(
            [
                GroupSanction(
                    chat_id=OTHER,
                    telegram_id=51,
                    kind="WARN",
                    actor_id=42,
                    reason="",
                    status="RECORDED",
                    policy_version=1,
                    request_key="other-group",
                ),
                GroupSanction(
                    chat_id=CHAT,
                    telegram_id=52,
                    kind="WARN",
                    actor_id=42,
                    reason="",
                    status="RECORDED",
                    policy_version=1,
                    request_key="other-user",
                ),
            ]
        )
        await session.commit()
        before = list((await session.execute(select(GroupSanction.id, GroupSanction.status))).all())
        audits = await session.scalar(select(func.count()).select_from(AuditEvent))
        with use_language("en"):
            text, done = await receipt_text(
                session, await session.get(GroupModerationDraft, drafts[1].id)
            )
        assert done and "⚠️ <b>Warnings:</b> 2" in text
        assert (
            list((await session.execute(select(GroupSanction.id, GroupSanction.status))).all())
            == before
        )
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == audits
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 0
    assert native.calls == []


@pytest.mark.asyncio
async def test_release_uses_releasing_admin_and_stable_duration(database, settings):
    native = NativeBot()
    native.admin(CHAT, 43)
    await enabled(database, native)
    draft = await prepared(database, native, until=now() + timedelta(days=7))
    row = await confirmed(database, native, draft)
    worker = LocalModerationExecutor(database, native)
    await worker.process(sanction_id=row.id)
    async with database() as session:
        service = Service(settings, session)
        await service.observe(42, "initial_admin", "Initial")
        await service.observe(43, None, "Releasing <admin>")
        await service.observe(51, "example", "Example")
        with use_language("en"):
            text, _ = await receipt_text(session, await session.get(GroupModerationDraft, draft.id))
        assert "Duration:</b> 7 days" in text
    release = await prepared(database, native, "UNBAN", key="release-by-other-admin", actor=43)
    await confirmed(database, native, release, actor=43)
    await worker.process(sanction_id=row.id)
    async with database() as session:
        with use_language("en"):
            text, _ = await receipt_text(
                session, await session.get(GroupModerationDraft, release.id)
            )
        assert "𝗨𝗡𝗕𝗔𝗡𝗡𝗘𝗗" in text and "Admin:</b> Releasing &lt;admin&gt;" in text
        assert "initial_admin" not in text and "Duration:" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [kind for kind in HEADINGS if kind != "UNWARN"])
async def test_command_receipts_have_no_keyboard_and_keep_profile_separate(
    local_journey, database, settings, kind
):
    journey, native = local_journey
    if kind in {"UNBAN", "UNMUTE"}:
        await journey.send("/ban 51" if kind == "UNBAN" else "/mute 51", actor=42, chat=CHAT)
    await journey.send(
        f"/{kind.lower()} 51" + (" 7d" if kind in {"BAN", "MUTE"} else ""), actor=42, chat=CHAT
    )
    notices = [
        c
        for c in journey.transport.calls
        if isinstance(c, (SendMessage, EditMessageText)) and HEADINGS[kind] in (c.text or "")
    ]
    if kind in {"UNBAN", "UNMUTE"}:
        assert len([c for c in notices if isinstance(c, SendMessage)]) == 1
        assert not any(isinstance(c, EditMessageText) for c in notices)
    else:
        assert len(notices) == 1
    notice = notices[-1]
    assert notice.parse_mode == "HTML"
    assert (notice.reply_markup is not None) == (kind in {"BAN", "MUTE", "WARN"})
    if notice.reply_markup:
        from app.bot.callbacks import ModerationControl

        data = ModerationControl.unpack(notice.reply_markup.inline_keyboard[0][0].callback_data)
        assert data.action == ("manage" if kind == "WARN" else "release")
    async with database() as session:
        notice_id = (
            await session.scalar(
                select(GroupModerationDraft).where(GroupModerationDraft.kind == kind)
            )
        ).panel_message_id
        await Service(settings, session).observe(51, "example", "Example")
    before = len(journey.transport.calls)
    await journey.send("/info 51", actor=42)
    assert t("redsafe.title") in journey.text()
    assert not any(
        isinstance(c, EditMessageText) and c.chat_id == CHAT and c.message_id == notice_id
        for c in journey.transport.calls[before:]
    )
    assert not any(
        c.chat_id == CHAT and c.message_id == notice_id for c in journey.transport.deletions
    )


@pytest.mark.asyncio
async def test_reply_identity_and_private_confirmation_have_no_result_buttons(
    local_journey, database
):
    journey, _ = local_journey
    reply = Message(
        message_id=88,
        date=datetime.now(UTC),
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=TelegramUser(
            id=51, is_bot=False, first_name="Reply target", username="reply_target"
        ),
        text="Hi",
    )
    await journey.send("/warn", actor=42, chat=CHAT, reply_to_message=reply)
    assert "@reply_target" in journey.text()
    await journey.send("/moderation", actor=42)
    await journey.click(LocalAction(action="action", chat=CHAT, value="WARN").pack(), actor=42)
    await journey.send("51", actor=42)
    async with database() as session:
        draft = await session.scalar(
            select(GroupModerationDraft)
            .where(GroupModerationDraft.kind == "WARN")
            .order_by(GroupModerationDraft.created_at.desc())
        )
        draft_id = draft.id
    await journey.click(LocalAction(action="confirm", value=draft_id).pack(), actor=42)
    receipt = [
        c
        for c in journey.transport.calls
        if isinstance(c, EditMessageText) and HEADINGS["WARN"] in (c.text or "")
    ][-1]
    assert receipt.reply_markup is None and receipt.parse_mode == "HTML"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["pending", "failed", "uncertain", "already"])
async def test_unconfirmed_or_preexisting_results_never_use_success_heading(database, outcome):
    native = NativeBot()
    await enabled(database, native)
    draft = await prepared(database, native)
    row = await confirmed(database, native, draft)
    if outcome == "failed":
        native.api_false = True
    if outcome == "uncertain":
        native.error = TimeoutError()
    if outcome == "already":
        native.members[CHAT, 51] = native.peer(51, "kicked", until_date=None)
    if outcome != "pending":
        await LocalModerationExecutor(database, native).process(sanction_id=row.id)
    async with database() as session:
        text, done = await receipt_text(session, await session.get(GroupModerationDraft, draft.id))
    assert not any(header in text for header in HEADINGS.values())
    assert done == (outcome != "pending")


@pytest.mark.asyncio
async def test_private_profile_does_not_edit_or_delete_confirmed_receipt(
    local_journey, database, settings
):
    journey, _ = local_journey
    await journey.send("/moderation", actor=42)
    await journey.click(LocalAction(action="group", chat=CHAT).pack(), actor=42)
    await journey.send("/warn 51", actor=42)
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        notice_id = draft.panel_message_id
        await Service(settings, session).observe(51, "example", "Example")
    before = len(journey.transport.calls)
    await journey.send("/info 51", actor=42)
    assert t("redsafe.title") in journey.text()
    assert not any(
        isinstance(c, EditMessageText) and c.chat_id == 42 and c.message_id == notice_id
        for c in journey.transport.calls[before:]
    )
    assert not any(
        c.chat_id == 42 and c.message_id == notice_id for c in journey.transport.deletions
    )


@pytest.mark.asyncio
async def test_cancel_and_rejected_confirmations_do_not_add_receipt_buttons(
    local_journey, database
):
    journey, _ = local_journey
    await journey.send("/moderation", actor=42)
    await journey.click(LocalAction(action="action", chat=CHAT, value="WARN").pack(), actor=42)
    await journey.send("51", actor=42)
    async with database() as session:
        draft_id = (await session.scalar(select(GroupModerationDraft))).id
    await journey.click(LocalAction(action="cancel", value=draft_id).pack(), actor=42)
    edit = [c for c in journey.transport.calls if isinstance(c, EditMessageText)][-1]
    assert edit.reply_markup is None and t("lm.cancelled") in edit.text
    await journey.click(LocalAction(action="confirm", value=draft_id).pack(), actor=42)
    edit = [c for c in journey.transport.calls if isinstance(c, EditMessageText)][-1]
    assert edit.reply_markup is None and not any(h in edit.text for h in HEADINGS.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [kind for kind in HEADINGS if kind != "UNWARN"])
async def test_numeric_receipts_use_live_username_without_global_user_write(
    local_journey, database, kind
):
    journey, native = local_journey
    if kind in {"UNBAN", "UNMUTE"}:
        await journey.send(
            "/ban 51 30m" if kind == "UNBAN" else "/mute 51 30m", actor=42, chat=CHAT
        )
    if kind not in {"UNBAN", "UNMUTE"}:
        native.members[CHAT, 51] = native.peer(51)
    native.members[CHAT, 51].user.username = "redsafeads"
    await journey.send(
        f"/{kind.lower()} 51" + (" 30m" if kind in {"BAN", "MUTE"} else ""), actor=42, chat=CHAT
    )
    notice = [
        call
        for call in journey.transport.calls
        if isinstance(call, (SendMessage, EditMessageText)) and HEADINGS[kind] in call.text
    ][-1]
    assert "@redsafeads" in notice.text and "<code>51</code>" in notice.text
    async with database() as session:
        assert await session.scalar(select(User).where(User.telegram_id == 51)) is None


@pytest.mark.asyncio
async def test_username_command_receipt_uses_verified_name_not_raw_input(
    local_journey, database, settings
):
    journey, native = local_journey
    async with database() as session:
        await Service(settings, session).observe(51, "redsafeads", "Old label")
    native.members[CHAT, 51] = native.peer(51)
    native.members[CHAT, 51].user.username = "redsafeads"
    await journey.send("/warn @RedSafeAds", actor=42, chat=CHAT)
    notice = [
        call
        for call in journey.transport.calls
        if isinstance(call, SendMessage) and HEADINGS["WARN"] in call.text
    ][-1]
    assert "@redsafeads" in notice.text and "<code>51</code>" in notice.text
    assert "Old label" not in notice.text and "@RedSafeAds" not in notice.text


@pytest.mark.asyncio
async def test_private_preview_confirmation_and_history_show_username(local_journey, database):
    journey, native = local_journey
    native.members[CHAT, 51] = native.peer(51)
    native.members[CHAT, 51].user.username = "redsafeads"
    await journey.send("/moderation", actor=42)
    await journey.click(LocalAction(action="action", chat=CHAT, value="WARN").pack(), actor=42)
    await journey.send("51", actor=42)
    preview = [call for call in journey.transport.calls if isinstance(call, EditMessageText)][-1]
    assert "@redsafeads" in preview.text and "<code>51</code>" in preview.text
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        draft_id = draft.id
    await journey.click(LocalAction(action="confirm", value=draft_id).pack(), actor=42)
    receipt = [
        call
        for call in journey.transport.calls
        if isinstance(call, EditMessageText) and HEADINGS["WARN"] in call.text
    ][-1]
    assert "@redsafeads" in receipt.text
    await journey.click(LocalAction(action="whist", chat=CHAT, value="51:0").pack(), actor=42)
    history = [call for call in journey.transport.calls if isinstance(call, EditMessageText)][-1]
    assert "@redsafeads" in history.text and "<code>51</code>" in history.text


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["wrong_id", "unavailable", "renamed", "removed"])
async def test_receipt_lookup_is_display_only_and_fails_safely(database, settings, failure):
    from types import SimpleNamespace

    from app.bot.moderation_messages import identity_labels

    async with database() as session:
        await Service(settings, session).observe(51, "old_username", "Known name")

        async def member(chat, user):
            if failure == "unavailable":
                raise OSError("temporary read failure")
            return SimpleNamespace(
                user=TelegramUser(
                    id=99 if failure == "wrong_id" else 51,
                    is_bot=False,
                    first_name="Current name",
                    username=None if failure == "removed" else "current_username",
                )
            )

        labels = await identity_labels(
            session, {51}, bot=SimpleNamespace(get_chat_member=member), chat=CHAT
        )
        expected = (
            "@old_username"
            if failure in {"wrong_id", "unavailable"}
            else "Current name"
            if failure == "removed"
            else "@current_username"
        )
        assert labels[51] == expected
        assert (
            await session.scalar(select(User).where(User.telegram_id == 51))
        ).username == "old_username"
