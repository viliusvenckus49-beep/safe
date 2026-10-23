"""Real routing/SQL for navigation; Telegram transport is a test double."""

from string import Formatter

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, SendPhoto
from sqlalchemy import func, select
from test_admin_home import last_screen, prepare, values
from test_group_removal import seed
from test_groups_telegram import WorkerBot
from test_local_moderation_stage2 import local_journey as local_journey
from test_telegram import journey as journey

from app import presentation as p
from app.bot import group_keyboards, group_presentation, states
from app.bot import keyboards as kb
from app.bot.admin_navigation import route, section_keyboard
from app.bot.callbacks import AutoAd
from app.bot.group_keyboards import GroupAction
from app.bot.group_runtime import process_group_jobs
from app.group_services import GroupService
from app.i18n import CATALOGS, t, use_language
from app.models import AuditEvent, RecoveryCampaign, RecoveryDelivery

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("actor", [1, 42, 900])
async def test_home_keeps_text_without_photo_and_role_visibility(
    journey, database, settings, lang, actor
):
    await prepare(database, settings, actor, lang)
    await journey.send("/start", actor=actor)
    screen = last_screen(journey)
    with use_language(lang):
        assert isinstance(screen, SendMessage) and screen.text == p.home()
        assert screen.reply_markup == kb.home(actor != 1, owner=actor == 900)
    assert not any(isinstance(c, SendPhoto) for c in journey.transport.calls)
    if actor != 1:
        await journey.click(kb.action("admin"), actor=actor)
        for section in ["scam", "users", "system"]:
            await journey.click(route(section), actor=actor)
            with use_language(lang):
                assert last_screen(journey).reply_markup == section_keyboard(section)
            assert isinstance(last_screen(journey), EditMessageText)
            await journey.click(kb.action("admin"), actor=actor)
        await journey.click(kb.action("rep_admin"), actor=actor)
        with use_language(lang):
            assert last_screen(journey).reply_markup == section_keyboard("reputation")
        await journey.click(route("owner"), actor=actor)
        if actor == 900:
            assert GroupAction(action="list").pack() in values(last_screen(journey).reply_markup)
        else:
            assert any(
                isinstance(c, AnswerCallbackQuery) and c.show_alert
                for c in journey.transport.calls[-2:]
            )


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_each_category_subscreen_returns_to_parent(journey, database, settings, lang):
    await prepare(database, settings, 900, lang)
    for parent, child in [
        ("scam", kb.action("category_audit", "scam:0")),
        ("reputation", kb.action("category_audit", "reputation:0")),
        ("users", kb.action("users", "0")),
        ("system", kb.action("audit")),
    ]:
        await journey.click(route(parent), actor=900)
        await journey.click(child, actor=900)
        expected = kb.action("rep_admin") if parent == "reputation" else route(parent)
        assert expected in values(last_screen(journey).reply_markup)
        await journey.click(expected, actor=900)
        with use_language(lang):
            assert last_screen(journey).reply_markup == section_keyboard(parent)
    for child in ["ads_clients", "ads_stats", "ads_settings"]:
        await journey.click(route(child), actor=900)
        assert AutoAd(action="root").pack() in values(last_screen(journey).reply_markup)
        await journey.click(AutoAd(action="root").pack(), actor=900)
        assert kb.action("admin") in values(last_screen(journey).reply_markup)


async def test_user_search_profile_and_navigation_clear_fsm(journey, database, settings):
    await prepare(database, settings, 900, "lt")
    for entry in ["user_search", "user_profile"]:
        await journey.click(kb.action(entry), actor=900)
        assert await journey.state(900) == states.InputFlow.lookup.state
        await journey.send("42", actor=900)
        assert route("users") in values(last_screen(journey).reply_markup)
        assert await journey.state(900) is None
    await journey.click(kb.action("user_search"), actor=900)
    await journey.click(kb.action("home"), actor=900)
    assert await journey.state(900) is None


async def test_removed_group_recovery_cannot_send_even_with_historical_pending_rows(
    journey, database, settings
):
    await seed(database, settings)
    async with database() as session:
        before = [
            (r.id, r.status, r.attempts) for r in await session.scalars(select(RecoveryDelivery))
        ]
        campaigns = await session.scalar(select(func.count()).select_from(RecoveryCampaign))
    bot = WorkerBot()
    await process_group_jobs(bot, settings, database)
    assert bot.messages == []
    for name in [
        "subscriptions",
        "consent",
        "subscribe",
        "unsubscribe",
        "recover",
        "send",
        "export",
    ]:
        await journey.click(GroupAction(action=name, chat_id=-1000000000001).pack(), actor=900)
        assert isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
        assert journey.transport.calls[-1].show_alert
    state = journey.dp.fsm.get_context(bot=journey.bot, chat_id=900, user_id=900)
    await state.set_state("RecoveryFlow:invite")
    await journey.send("https://t.me/+OldLink", actor=900)
    assert await journey.state(900) is None
    await journey.send("/recovery", actor=900)
    async with database() as session:
        assert [
            (r.id, r.status, r.attempts) for r in await session.scalars(select(RecoveryDelivery))
        ] == before
        assert await session.scalar(select(func.count()).select_from(RecoveryCampaign)) == campaigns
    for name in [
        "subscribe",
        "prepare_recovery",
        "queue_recovery",
        "export_members",
        "claim_deliveries",
        "finish_delivery",
    ]:
        assert not hasattr(GroupService, name)
    assert not hasattr(states, "RecoveryFlow")
    assert not hasattr(group_keyboards, "consent") and not hasattr(
        group_presentation, "members_backup"
    )


async def test_category_audit_is_filtered_and_paginated(journey, database, settings):
    await prepare(database, settings, 900, "lt")
    async with database() as session:
        session.add_all(
            [AuditEvent(actor_id=900, action="scam_added", details={}) for _ in range(7)]
        )
        session.add(AuditEvent(actor_id=12345, action="reputation_adjust", details={}))
        await session.commit()
    await journey.click(kb.action("category_audit", "scam:0"), actor=900)
    assert "12345" not in last_screen(journey).text
    assert kb.action("category_audit", "scam:1") in values(last_screen(journey).reply_markup)
    await journey.click(kb.action("category_audit", "scam:1"), actor=900)
    assert route("scam") in values(last_screen(journey).reply_markup)


async def test_all_locale_templates_and_buttons_use_consistent_symbols():
    for lang in ("lt", "en", "ru"):
        with use_language(lang):
            for markup in [
                kb.home(True, owner=True),
                kb.admin(True),
                *[section_keyboard(c) for c in ("scam", "reputation", "users", "system", "owner")],
            ]:
                for row in markup.inline_keyboard:
                    for b in row:
                        assert b.icon_custom_emoji_id is None
                        assert b.callback_data and len(b.callback_data.encode()) <= 64
                        assert not b.callback_data.startswith("sg|") or b.callback_data.startswith(
                            "sg|list|"
                        )
            for key in CATALOGS["lt"]:
                assert key in CATALOGS[lang]
                assert {
                    field for _, field, _, _ in Formatter().parse(CATALOGS["lt"][key]) if field
                } == {field for _, field, _, _ in Formatter().parse(CATALOGS[lang][key]) if field}
            assert t("button.back").startswith("◀ ")
            assert t("button.admin").startswith("♛ ")
            assert "command.recovery" not in CATALOGS[lang]


async def test_clients_are_real_paginated_latest_assignments(journey, database, settings):
    from app.ads_customers import CustomerAccess
    from app.ads_services import AdsService
    from app.models import AdCampaign

    await prepare(database, settings, 900, "lt")
    ids = []
    async with database() as session:
        for i in range(6):
            campaign = await AdsService(settings, session).create(
                900, f"CLIENT-AD-{i}", f"stage2:{i}"
            )
            await CustomerAccess(settings, session).assign(900, campaign.id, 100 + i, "0.0")
            ids.append(campaign.id)
    await journey.click(route("ads_clients"), actor=900)
    first = last_screen(journey)
    links = [v for v in values(first.reply_markup) if v.startswith("ua:client:")]
    assert len(links) == 4 and "CLIENT-AD-5" not in first.text
    await journey.click(kb.action("ads_clients_page", "1"), actor=900)
    assert (
        "CLIENT-AD-5" in last_screen(journey).text
        and "CLIENT-AD-0" not in last_screen(journey).text
    )
    async with database() as session:
        access = CustomerAccess(settings, session)
        grant = await access.latest(ids[5])
        await access.revoke(900, ids[5], access.stamp(grant))
        assert (await session.get(AdCampaign, ids[0])).internal_name == "CLIENT-AD-0"
    await journey.click(kb.action("ads_clients_page", "1"), actor=900)
    assert "CLIENT-AD-5" not in last_screen(journey).text
    await journey.click(route("ads_clients"), actor=1)
    assert journey.transport.calls[-1].show_alert


async def test_receipt_card_is_not_deleted_or_edited_by_navigation(journey):
    from datetime import UTC, datetime

    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    markup = kb.check_result(42, persistent=True)
    await journey.dp.feed_update(
        journey.bot,
        Update(
            update_id=900001,
            callback_query=CallbackQuery(
                id="receipt-navigation",
                from_user=User(id=1, is_bot=False, first_name="User"),
                chat_instance="test",
                data=kb.action("lookup"),
                message=Message(
                    message_id=445,
                    date=datetime.now(UTC),
                    chat=Chat(id=1, type="private"),
                    reply_markup=markup,
                ),
            ),
        ),
    )
    assert not any(c.message_id == 445 for c in journey.transport.deletions)
    assert not any(
        isinstance(c, EditMessageText) and c.message_id == 445 for c in journey.transport.calls
    )
    assert isinstance(last_screen(journey), SendMessage)
    assert await journey.state(1) == states.InputFlow.lookup.state


async def test_selected_group_moderation_and_history_are_scoped(local_journey, database, settings):
    from test_group_moderation_foundation import CHAT, OTHER

    from app.bot.callbacks import LocalAction
    from app.bot.group_help import cb
    from app.models import GroupSanction

    j, native = local_journey
    async with database() as session:
        from app.services import Service

        await Service(settings, session).set_language(42, "lt")
        for index, chat in enumerate((CHAT, OTHER)):
            session.add(
                GroupSanction(
                    chat_id=chat,
                    telegram_id=8800000000 + index,
                    kind="WARN",
                    actor_id=42,
                    reason="<unsafe>&reason",
                    status="RECORDED",
                    owned=False,
                    policy_version=1,
                    request_key=f"navigation-history:{index}",
                )
            )
        await session.commit()
    await j.click(cb("group", CHAT), actor=42)
    menu = last_screen(j).reply_markup
    assert LocalAction(action="group", chat=CHAT).pack() in values(menu)
    await j.click(LocalAction(action="group", chat=CHAT).pack(), actor=42)
    actions = values(last_screen(j).reply_markup)
    assert len(actions) == 7 and cb("group", CHAT) in actions
    for command in ("BAN", "UNBAN", "MUTE", "UNMUTE", "WARN", "KICK"):
        assert LocalAction(action="action", chat=CHAT, value=command).pack() in actions
    await j.click(cb("history", CHAT), actor=42)
    text = last_screen(j).text
    assert "8800000000" in text and "8800000001" not in text
    assert "&lt;unsafe&gt;&amp;reason" in text and "<unsafe>" not in text
    assert LocalAction(action="action", chat=CHAT, value="HISTORY").pack() in values(
        last_screen(j).reply_markup
    )
    await j.click(cb("history", CHAT), actor=51)
    assert j.transport.calls[-1].show_alert


async def test_home_html_is_identical_to_the_previous_release():
    import ast
    import subprocess

    raw = subprocess.check_output(
        ["git", "show", "35787658fd8389a43487f4aa25f7ea22f4f16b68:app/locales/presentation.py"],
        text=True,
    )
    tree = ast.parse(raw)
    assignment = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "CATALOGS" for t in n.targets)
    )
    original = ast.literal_eval(assignment.value)
    for lang in ("lt", "en", "ru"):
        assert CATALOGS[lang]["p.home"] == original[lang]["p.home"]
