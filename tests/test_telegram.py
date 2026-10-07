"""Dispatcher journeys use real aiogram routing and FSM without Telegram network calls."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    GetMe,
    SendMessage,
    SendPhoto,
    SetMyCommands,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select

from app import presentation as p
from app.bot.callbacks import Action, AdminRepStep, Moderation, ReportStep, ReputationModeration
from app.bot.handlers import create_router
from app.bot.states import AdminRepFlow, ReportFlow
from app.models import (
    ModerationAction,
    Report,
    ReportEvidence,
    ReputationAdjustment,
    ReputationEvent,
    ReputationRequest,
    ScamRecord,
)
from app.services import Service


class Transport(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.deletions = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, DeleteMessage):
            self.deletions.append(method)
            return True
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=123456, is_bot=True, first_name="SAFECheck", username="redsafecheckbot")
        if isinstance(method, (AnswerCallbackQuery, DeleteMessage, SetMyCommands)):
            return True
        return Message(
            message_id=(
                method.message_id if isinstance(method, EditMessageText) else len(self.calls)
            ),
            date=datetime.now(UTC),
            chat=Chat(id=int(method.chat_id), type="private"),
            text=getattr(method, "text", None),
        )


class Journey:
    def __init__(self, bot, dispatcher, transport):
        self.bot, self.dp, self.transport = bot, dispatcher, transport
        self.sequence = 0

    async def send(self, text=None, actor=1, chat=None, **extra):
        self.sequence += 1
        message = Message(
            message_id=self.sequence,
            date=datetime.now(UTC),
            chat=Chat(id=chat or actor, type="private" if chat is None else "supergroup"),
            from_user=User(id=actor, is_bot=False, first_name="Tester"),
            text=text,
            **extra,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.sequence, message=message))

    async def click(self, data, actor=1, chat=None):
        self.sequence += 1
        callback = CallbackQuery(
            id=str(self.sequence),
            from_user=User(id=actor, is_bot=False, first_name="Tester"),
            chat_instance="test",
            data=data,
            message=Message(
                message_id=100,
                date=datetime.now(UTC),
                chat=Chat(id=chat or actor, type="private" if chat is None else "supergroup"),
            ),
        )
        await self.dp.feed_update(
            self.bot, Update(update_id=self.sequence, callback_query=callback)
        )

    async def state(self, actor=1):
        return await self.dp.fsm.get_context(bot=self.bot, chat_id=actor, user_id=actor).get_state()

    async def data(self, actor=1):
        return await self.dp.fsm.get_context(bot=self.bot, chat_id=actor, user_id=actor).get_data()

    def text(self):
        return "\n".join(
            getattr(call, "text", None) or getattr(call, "caption", None) or ""
            for call in self.transport.calls
            if isinstance(call, (SendMessage, EditMessageText, SendPhoto))
        )

    async def step(self, action, actor=1):
        data = await self.data(actor)
        await self.click(ReportStep(action=action, nonce=data["nonce"]).pack(), actor)


@pytest_asyncio.fixture
async def journey(database, settings):
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_router(settings, database))
    yield Journey(bot, dispatcher, transport)
    await dispatcher.storage.close()
    await bot.session.close()


async def count(database, model):
    async with database() as session:
        return await session.scalar(select(func.count()).select_from(model))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command", ["/start", "/profile", "/ask 42", "/rep 42", "/top", "/scammers", "/admin"]
)
async def test_commands_dispatch(command, journey):
    await journey.send(command)
    assert journey.text()
    if command == "/admin":
        assert p.text("denied") in journey.text()
    else:
        assert any(
            isinstance(call, (SendMessage, SendPhoto)) and call.reply_markup
            for call in journey.transport.calls
        )


@pytest.mark.asyncio
async def test_report_full_wizard_evidence_edit_submit_retry(journey, database):
    await journey.send("/report")
    assert await journey.state() == ReportFlow.target.state
    await journey.send("invalid")
    assert await journey.state() == ReportFlow.target.state
    await journey.send("42")
    await journey.send("tiny")
    assert await journey.state() == ReportFlow.reason.state
    await journey.send("Lost <payment> and evidence")
    await journey.send(
        photo=[{"file_id": "photo", "file_unique_id": "unique", "width": 100, "height": 100}]
    )
    await journey.send(document={"file_id": "document", "file_unique_id": "unique2"})
    await journey.send("https://t.me/example/123")
    await journey.step("preview")
    assert await journey.state() == ReportFlow.preview.state
    assert "&lt;payment&gt;" in journey.text()
    assert "Įrodymai: 3" in journey.text()
    await journey.step("edit")
    await journey.send("Updated payment incident")
    await journey.step("preview")
    nonce = (await journey.data())["nonce"]
    await journey.step("submit")
    assert await journey.state() is None
    await journey.click(ReportStep(action="submit", nonce=nonce).pack())
    assert await count(database, Report) == 1
    assert await count(database, ReportEvidence) == 3
    assert await count(database, ScamRecord) == 0
    assert p.text("stale") == journey.transport.calls[-1].text


@pytest.mark.asyncio
async def test_report_back_target_change_clears_evidence_and_cancel(journey, database):
    await journey.send("/report 42 Incident with payment")
    await journey.send("https://t.me/example/123")
    await journey.step("back")
    await journey.step("back")
    await journey.send("43")
    assert (await journey.data())["evidence"] == []
    assert (await journey.data())["reason"] == ""
    await journey.click(Action(name="close").pack())
    assert await journey.state() is None
    assert await journey.data() == {}
    assert await count(database, Report) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
async def test_admin_moderation_double_click_private_evidence(journey, database, settings, approve):
    async with database() as session:
        report = await Service(settings, session).submit_report(
            1, "42", "Payment incident", [{"kind": "photo", "file_id": "sensitive"}], "test"
        )
        reference = report.reference
    evidence = Moderation(action="evidence", reference=reference).pack()
    await journey.click(evidence)
    await journey.click(evidence, actor=900, chat=-100)
    assert not any(isinstance(call, SendPhoto) for call in journey.transport.calls)
    await journey.click(evidence, actor=900)
    assert any(isinstance(call, SendPhoto) for call in journey.transport.calls)
    decision = Moderation(action="approve" if approve else "reject", reference=reference).pack()
    await journey.click(decision, actor=900)
    await journey.click(decision, actor=900)
    assert await count(database, ModerationAction) == 1
    assert await count(database, ScamRecord) == int(approve)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,actor,expected",
    [
        ("garbage", 1, "stale"),
        ("draft:submit:stale", 1, "stale"),
        ("mod:approve:invalid", 1, "denied"),
        ("mod:approve:invalid", 900, "stale"),
    ],
)
async def test_untrusted_callback_no_mutation(journey, database, data, actor, expected):
    await journey.click(data, actor=actor)
    assert await count(database, ScamRecord) == 0
    assert await count(database, ReputationEvent) == 0
    assert await count(database, ModerationAction) == 0
    assert journey.transport.calls[-1].text == p.text(expected)


@pytest.mark.asyncio
async def test_rep_unknown_username_keyboard_self_and_cooldown(journey, database):
    await journey.send("/ask @unknown_name")
    markup = journey.transport.calls[-1].reply_markup
    callback = next(
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if "+REP" in button.text
    )
    assert Action.unpack(callback).value.startswith("u:")
    await journey.click(callback)
    await journey.send("Test comment")
    await journey.send("+rep 43 Test comment")
    await journey.send("-rep @unknown_name Test comment", actor=2)
    await journey.send("+rep 3 Test comment", actor=3)
    assert await count(database, ReputationRequest) == 2
    assert await count(database, ReputationEvent) == 0
    assert p.error("rep_cooldown") in journey.text()
    assert p.error("self_rep") in journey.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", ["bot", "channel", "missing"])
async def test_anonymous_or_bot_actor_cannot_mutate(journey, database, sender):
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-100, type="supergroup"),
        from_user=None
        if sender == "missing"
        else User(id=100, is_bot=sender == "bot", first_name="Actor"),
        sender_chat=Chat(id=-200, type="channel") if sender == "channel" else None,
        text="+rep 42 Test comment",
    )
    await journey.dp.feed_update(journey.bot, Update(update_id=100, message=message))
    assert await count(database, ReputationEvent) == 0
    assert p.error("anonymous_identity") in journey.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["bot", "channel", "missing"])
async def test_reply_identity_cannot_be_shared_bot_or_channel(journey, database, target):
    reply = Message(
        message_id=100,
        date=datetime.now(UTC),
        chat=Chat(id=-100, type="supergroup"),
        from_user=None
        if target == "missing"
        else User(id=1087968824, is_bot=target == "bot", first_name="Reply"),
        sender_chat=Chat(id=-200, type="channel") if target == "channel" else None,
        text="source",
    )
    await journey.send("+rep Test comment", chat=-100, reply_to_message=reply)
    assert await count(database, ReputationEvent) == 0
    assert p.error("anonymous_identity") in journey.text()
    await journey.send("+rep 42 Test comment", chat=-100, reply_to_message=reply)
    assert await count(database, ReputationRequest) == 1
    assert await count(database, ReputationEvent) == 0


@pytest.mark.asyncio
async def test_real_reply_user_without_username_gets_rep(journey, database):
    reply = Message(
        message_id=100,
        date=datetime.now(UTC),
        chat=Chat(id=-100, type="supergroup"),
        from_user=User(id=42, is_bot=False, first_name="No username"),
        text="hello",
    )
    await journey.send("+rep Test comment", chat=-100, reply_to_message=reply)
    assert await count(database, ReputationRequest) == 1
    assert await count(database, ReputationEvent) == 0
    assert "No username" in journey.text()


@pytest.mark.asyncio
async def test_forged_admin_navigation_and_malformed_target_callbacks(journey, database):
    for name, value in [
        ("admin", ""),
        ("add_sc", ""),
        ("del_sc", ""),
        ("pending", ""),
        ("users", "0"),
        ("stats", ""),
        ("audit", "0"),
        ("scams", "-1"),
        ("vote+", "bad"),
    ]:
        await journey.click(Action(name=name, value=value).pack())
    assert await count(database, ScamRecord) == 0
    assert await count(database, ModerationAction) == 0
    assert await count(database, ReputationEvent) == 0
    assert p.text("denied") in "\n".join(
        getattr(call, "text", "") or "" for call in journey.transport.calls
    )


@pytest.mark.asyncio
async def test_navigation_clears_draft_and_scammers_pagination(journey, database, settings):
    async with database() as session:
        service = Service(settings, session)
        for target in range(10, 17):
            await service.add_scam(900, str(target), "Confirmed payment incident")
    await journey.send("/report")
    await journey.click(Action(name="top").pack())
    assert await journey.state() is None
    await journey.click(Action(name="scams", value="1").pack())
    markup = journey.transport.calls[-1].reply_markup
    assert any(button.text == "2 / 2" for row in markup.inline_keyboard for button in row)
    await journey.click(Action(name="scams", value="2").pack())
    markup = journey.transport.calls[-1].reply_markup
    assert any(button.text == "2 / 2" for row in markup.inline_keyboard for button in row)

    await journey.click(Action(name="scams", value="999999").pack())
    assert p.error("invalid_input") in journey.text()


async def admin_rep_draft(journey, operation, amount=None, target="42"):
    await journey.click(Action(name=operation).pack(), actor=900)
    await journey.send(target, actor=900)
    if amount is not None:
        await journey.send(str(amount), actor=900)
    await journey.send("Reviewed <payment> evidence", actor=900)
    assert await journey.state(900) == AdminRepFlow.preview.state
    return (await journey.data(900))["nonce"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,amount,score", [("rep_add", 12, 12), ("rep_sub", 7, -7), ("rep_reset", None, 0)]
)
async def test_admin_reputation_preview_submit_duplicate_preserves_history(
    journey, database, settings, operation, amount, score
):
    nonce = await admin_rep_draft(journey, operation, amount)
    assert "&lt;payment&gt;" in journey.transport.calls[-1].text
    callback = AdminRepStep(action="submit", nonce=nonce).pack()
    await journey.click(callback, actor=900)
    await journey.click(callback, actor=900)
    assert await journey.state(900) is None
    assert await count(database, ReputationAdjustment) == 1
    async with database() as session:
        assert (await Service(settings, session).profile("42"))["score"] == score


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
async def test_user_rep_pending_admin_queue_and_double_decision(
    journey, database, settings, approve
):
    await journey.send("+rep 42 Test comment")
    async with database() as session:
        svc = Service(settings, session)
        assert (await svc.profile("42"))["score"] == 0
        request = (await session.scalars(select(ReputationRequest))).one()
        reference = request.reference
    await journey.click(Action(name="rep_pending", value="0").pack(), actor=900)
    assert any(
        button.callback_data == Action(name="rep_review", value=reference).pack()
        for row in journey.transport.calls[-1].reply_markup.inline_keyboard
        for button in row
    )
    await journey.click(Action(name="rep_review", value=reference).pack(), actor=900)
    callback = ReputationModeration(
        action="approve" if approve else "reject", reference=reference
    ).pack()
    await journey.click(callback)
    await journey.click(callback, actor=900, chat=-100)
    assert await count(database, ReputationEvent) == 0
    await journey.click(callback, actor=900)
    await journey.click(callback, actor=900)
    assert await count(database, ReputationEvent) == 0
    async with database() as session:
        assert (await Service(settings, session).profile("42"))["score"] == int(approve)
        request = (await session.scalars(select(ReputationRequest))).one()
        assert request.status == ("APPROVED" if approve else "REJECTED")
    reverse = ReputationModeration(
        action="reject" if approve else "approve", reference=reference
    ).pack()
    await journey.click(reverse, actor=900)
    assert await count(database, ReputationEvent) == 0
    async with database() as session:
        request = (await session.scalars(select(ReputationRequest))).one()
        assert request.status == ("APPROVED" if approve else "REJECTED")
        assert (await Service(settings, session).profile("42"))["score"] == int(approve)


@pytest.mark.asyncio
async def test_admin_rep_forged_nonce_wrong_stage_back_and_cancel(journey, database):
    await journey.click(Action(name="rep_add").pack(), actor=900)
    nonce = (await journey.data(900))["nonce"]
    await journey.click(AdminRepStep(action="submit", nonce=nonce).pack(), actor=900)
    assert await journey.state(900) == AdminRepFlow.target.state
    await journey.send("42", actor=900)
    await journey.send("10001", actor=900)
    assert await journey.state(900) == AdminRepFlow.amount.state
    await journey.send("12", actor=900)
    await journey.send("Reviewed payment evidence", actor=900)
    nonce = (await journey.data(900))["nonce"]
    await journey.click(AdminRepStep(action="submit", nonce="forged").pack(), actor=900)
    assert await count(database, ReputationAdjustment) == 0
    await journey.click(AdminRepStep(action="back", nonce=nonce).pack(), actor=900)
    assert await journey.state(900) == AdminRepFlow.reason.state
    await journey.click(AdminRepStep(action="back", nonce=nonce).pack(), actor=900)
    assert await journey.state(900) == AdminRepFlow.amount.state
    await journey.click(Action(name="rep_admin").pack(), actor=900)
    assert await journey.state(900) is None
    await journey.click(AdminRepStep(action="submit", nonce=nonce).pack(), actor=900)
    assert await count(database, ReputationAdjustment) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "rep_admin",
        "rep_add",
        "rep_sub",
        "rep_reset",
        "top_include",
        "top_exclude",
        "rep_pending",
        "rep_review",
    ],
)
async def test_admin_rep_navigation_permissions_and_group_privacy(journey, database, operation):
    await journey.click(Action(name=operation, value="0").pack())
    await journey.click(Action(name=operation, value="0").pack(), actor=900, chat=-100)
    assert await journey.state() is None
    assert await count(database, ReputationAdjustment) == 0
    assert journey.transport.calls[-1].text == p.text("admin_private")


@pytest.mark.asyncio
async def test_top_buttons_exclude_and_include_positive_user(journey, database, settings):
    async with database() as session:
        await Service(settings, session).admin_adjust_rep(
            900, "42", 5, "Reviewed payment evidence", "seed"
        )
    nonce = await admin_rep_draft(journey, "top_exclude")
    await journey.click(AdminRepStep(action="submit", nonce=nonce).pack(), actor=900)
    async with database() as session:
        assert 42 not in [
            row["user"].telegram_id for row in await Service(settings, session).leaderboard()
        ]
    nonce = await admin_rep_draft(journey, "top_include")
    await journey.click(AdminRepStep(action="submit", nonce=nonce).pack(), actor=900)
    async with database() as session:
        assert (await Service(settings, session).leaderboard())[0]["score"] == 5


@pytest.mark.asyncio
async def test_admin_old_preview_cannot_submit_edited_amount(journey, database, settings):
    old_nonce = await admin_rep_draft(journey, "rep_add", 5)
    await journey.click(AdminRepStep(action="back", nonce=old_nonce).pack(), actor=900)
    await journey.click(AdminRepStep(action="back", nonce=old_nonce).pack(), actor=900)
    await journey.send("17", actor=900)
    await journey.send("New reviewed payment reason", actor=900)
    new_nonce = (await journey.data(900))["nonce"]
    assert new_nonce != old_nonce
    await journey.click(AdminRepStep(action="submit", nonce=old_nonce).pack(), actor=900)
    assert await count(database, ReputationAdjustment) == 0
    assert await journey.state(900) == AdminRepFlow.preview.state
    await journey.click(AdminRepStep(action="submit", nonce=new_nonce).pack(), actor=900)
    async with database() as session:
        assert (await Service(settings, session).profile("42"))["score"] == 17
    assert await count(database, ReputationAdjustment) == 1


@pytest.mark.asyncio
async def test_report_old_preview_cannot_submit_edited_reason(journey, database):
    await journey.send("/report 42 Original payment reason")
    await journey.step("preview")
    old_nonce = (await journey.data())["nonce"]
    await journey.step("edit")
    await journey.send("Changed payment evidence reason")
    await journey.step("preview")
    new_nonce = (await journey.data())["nonce"]
    assert new_nonce != old_nonce
    await journey.click(ReportStep(action="submit", nonce=old_nonce).pack())
    assert await count(database, Report) == 0
    assert await journey.state() == ReportFlow.preview.state
    await journey.step("submit")
    async with database() as session:
        report = (await session.scalars(select(Report))).one()
        assert report.reason == "Changed payment evidence reason"


@pytest.mark.asyncio
async def test_ordinary_group_messages_are_silent_but_rep_command_works(journey, database):
    await journey.send("Hello everyone", chat=-100)
    await journey.send(
        photo=[{"file_id": "photo", "file_unique_id": "unique", "width": 100, "height": 100}],
        chat=-100,
    )
    assert journey.transport.calls == []
    await journey.send("+rep 42 Test comment", chat=-100)
    assert await count(database, ReputationRequest) == 1
    assert any(isinstance(call, SendMessage) for call in journey.transport.calls)


@pytest.mark.asyncio
async def test_callback_navigation_sends_at_bottom_and_deletes_predecessor(journey):
    for action in ["home", "info", "lookup", "top", "profile", "home"]:
        await journey.click(Action(name=action).pack())
    panels = [
        call
        for call in journey.transport.calls
        if isinstance(call, (SendMessage, EditMessageText, SendPhoto))
    ]
    assert len(panels) == 6
    assert all(isinstance(call, (SendMessage, SendPhoto)) for call in panels)
    assert len(journey.transport.deletions) == 6
    assert all(call.message_id == 100 for call in journey.transport.deletions)


@pytest.mark.asyncio
async def test_report_typed_steps_replace_stored_panel(journey):
    await journey.send("/report")
    first_panel = next(call for call in journey.transport.calls if isinstance(call, SendMessage))
    assert first_panel.text == p.text("report_target")
    panel_id = (await journey.data())["screen_message_id"]
    await journey.send("42")
    await journey.send("Payment incident evidence")
    await journey.send("https://t.me/example/123")
    assert len(journey.transport.deletions) == 3
    assert journey.transport.deletions[0].message_id == panel_id
    assert len([c for c in journey.transport.calls if isinstance(c, SendMessage)]) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["8803241151", "@Example"])
async def test_group_admin_add_scam_numeric_and_username(journey, database, target):
    await journey.send(f"/add_sc {target} Payment fraud confirmed", actor=900, chat=-100)
    assert await count(database, ScamRecord) == 1
    if target.startswith("@"):
        assert p.text("scam_id_unknown") not in journey.text()
    await journey.send(f"/add_sc {target} Payment fraud confirmed", actor=1, chat=-100)
    assert await count(database, ScamRecord) == 1
    assert p.text("denied") in journey.text()


@pytest.mark.asyncio
async def test_close_panel_silent_until_explicit_command(journey):
    await journey.send("/start")
    await journey.send("/ask 42")
    assert sum(isinstance(c, (SendMessage, SendPhoto)) for c in journey.transport.calls) == 2
    assert journey.transport.deletions
    await journey.click(Action(name="close").pack())
    assert journey.transport.deletions[-1].message_id == 100
    assert await journey.data() == {}
    before = len(journey.transport.calls)
    await journey.send("ordinary message")
    assert len(journey.transport.calls) == before
    await journey.send("/start")
    assert any(isinstance(c, (SendMessage, SendPhoto)) for c in journey.transport.calls[before:])


@pytest.mark.asyncio
async def test_group_bad_add_scam_does_not_start_private_wizard(journey, database):
    await journey.send('/add_sc "42"', actor=900, chat=-100)
    assert p.text("scam_group_usage") in journey.text()
    assert await count(database, ScamRecord) == 0
    await journey.send("/del_sc 42 Payment fraud confirmed", actor=900, chat=-100)
    assert p.text("not_active") in journey.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["42", "@Example"])
async def test_group_scam_removal_authorized_and_audited(journey, database, target):
    await journey.send(f"/add_sc {target} Payment fraud confirmed", actor=900, chat=-100)
    await journey.send(f"/del_sc {target} Reviewed removal reason", actor=1, chat=-100)
    async with database() as session:
        record = await session.scalar(select(ScamRecord))
        assert record.status == "ACTIVE"
    await journey.send(f"/del_sc {target} Reviewed removal reason", actor=900, chat=-100)
    async with database() as session:
        record = await session.scalar(select(ScamRecord))
        assert record.status == "REMOVED"
    assert await count(database, ScamRecord) == 1


@pytest.mark.asyncio
async def test_cancel_command_closes_latest_callback_panel(journey):
    await journey.send("/start")
    await journey.click(Action(name="info").pack())
    latest = (await journey.data())["screen_message_id"]
    assert latest != 100
    panel_count = len(journey.transport.calls)
    await journey.send("/cancel")
    assert journey.transport.deletions[-1].message_id == latest
    assert await journey.data() == {}
    assert await journey.state() is None
    assert len(journey.transport.calls) == panel_count
