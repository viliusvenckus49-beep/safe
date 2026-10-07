"""Adversarial final UX journeys against isolated SQLite and fake Telegram transport."""

from datetime import UTC, datetime

import pytest
from aiogram.methods import AnswerCallbackQuery, SendMessage, SendPhoto
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select
from test_telegram import journey as telegram_journey

from app.bot import keyboards as kb
from app.bot.callbacks import Action, ReportStep, ScamAdmin
from app.bot.states import ReportFlow, RepVoteFlow, ScamAdminFlow
from app.i18n import t
from app.models import Report, ReputationRequest, ScamRecord
from app.services import Service

journey = telegram_journey


def last_screen(j):
    return next(
        call for call in reversed(j.transport.calls) if isinstance(call, (SendMessage, SendPhoto))
    )


def callback_values(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def rows(database, model):
    async with database() as session:
        return await session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("route", ["scams", "users", "audit", "rep_pending"])
@pytest.mark.parametrize("value", ["²", "١", "１２"])
async def test_non_ascii_pagination_is_stale_without_crashing_or_discarding_draft(
    journey, database, settings, route, value
):
    actor = 1 if route == "scams" else 900
    async with database() as session:
        await Service(settings, session).set_language(actor, "en")
    await journey.send("/report 42 Detailed incident description", actor=actor)
    before = await journey.data(actor)
    await journey.click(Action(name=route, value=value).pack(), actor=actor)
    assert await journey.state(actor) == ReportFlow.evidence.state
    assert (await journey.data(actor))["nonce"] == before["nonce"]
    assert last_screen(journey).text == t("p.stale", "en")
    assert await rows(database, Report) == 0


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
async def test_report_preview_nonce_invalidates_previous_controls_and_cancel_discards(
    journey, database, settings, locale
):
    async with database() as session:
        await Service(settings, session).set_language(1, locale)
    await journey.send("/report 42 Detailed incident description")
    old_nonce = (await journey.data())["nonce"]
    await journey.step("preview")
    current = await journey.data()
    assert current["nonce"] != old_nonce
    await journey.click(ReportStep(action="submit", nonce=old_nonce).pack())
    assert await journey.state() == ReportFlow.preview.state
    assert isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
    assert journey.transport.calls[-1].text == t("p.stale", locale)
    assert await rows(database, Report) == 0
    await journey.send("/cancel")
    assert await journey.state() is None
    assert await journey.data() == {}
    await journey.click(ReportStep(action="submit", nonce=current["nonce"]).pack())
    assert await rows(database, Report) == 0


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
async def test_group_rep_retry_requires_latest_prompt_and_same_group(
    journey, database, settings, locale
):
    async with database() as session:
        await Service(settings, session).set_language(1, locale)
    await journey.click(kb.action("vote-", "42"), chat=-100)
    state = journey.dp.fsm.get_context(bot=journey.bot, chat_id=-100, user_id=1)
    assert await state.get_state() == RepVoteFlow.comment.state
    old_id = (await state.get_data())["rep_prompt_id"]
    prompt = Message(
        message_id=old_id,
        date=datetime.now(UTC),
        chat=Chat(id=-100, type="supergroup"),
        from_user=User(id=123456, is_bot=True, first_name="Bot"),
    )
    await journey.send("abcd", chat=-100, reply_to_message=prompt)
    new_id = (await state.get_data())["rep_prompt_id"]
    assert new_id != old_id
    assert last_screen(journey).text == t("error.rep_comment", locale)
    await journey.send("Detailed negative experience", chat=-100, reply_to_message=prompt)
    await journey.send("Detailed negative experience", chat=-101, reply_to_message=prompt)
    await journey.send("Detailed negative experience", reply_to_message=prompt)
    assert await rows(database, ReputationRequest) == 0
    assert await state.get_state() == RepVoteFlow.comment.state
    await journey.send("/cancel", chat=-100)
    assert await state.get_data() == {}
    await journey.send(
        "Detailed negative experience",
        chat=-100,
        reply_to_message=prompt.model_copy(update={"message_id": new_id}),
    )
    assert await rows(database, ReputationRequest) == 0


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
async def test_scam_supplement_cancel_and_retarget_invalidate_previous_confirmation(
    journey, database, settings, locale
):
    async with database() as session:
        await Service(settings, session).set_language(900, locale)
    await journey.send("/add_sc @unknownone", actor=900)
    record_id = (await journey.data(900))["scam_record"]
    await journey.send("42", actor=900)
    first_nonce = (await journey.data(900))["scam_nonce"]
    await journey.click(ScamAdmin(action="id", value=str(record_id)).pack(), actor=900)
    await journey.send("43", actor=900)
    second_nonce = (await journey.data(900))["scam_nonce"]
    assert first_nonce != second_nonce
    await journey.click(ScamAdmin(action="confirm", value=first_nonce).pack(), actor=900)
    assert await journey.state(900) == ScamAdminFlow.preview.state
    async with database() as session:
        assert (await Service(settings, session).profile("@unknownone"))["user"].telegram_id is None
    await journey.click(ScamAdmin(action="page", value="0").pack(), actor=900)
    assert await journey.state(900) is None
    await journey.click(ScamAdmin(action="confirm", value=second_nonce).pack(), actor=900)
    async with database() as session:
        assert (await Service(settings, session).profile("@unknownone"))["user"].telegram_id is None
    assert await rows(database, ScamRecord) == 1


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
@pytest.mark.parametrize("family", ["sa", "ta", "ah", "acl", "rpa", "rpm", "mod"])
async def test_private_admin_callback_families_cannot_mutate_from_groups(
    journey, database, settings, locale, family
):
    async with database() as session:
        await Service(settings, session).set_language(900, locale)
    samples = {
        "sa": "sa:confirm:" + "a" * 32,
        "ta": "ta:confirm:" + "a" * 32,
        "ah": "ah:menu",
        "acl": "acl:submit:" + "a" * 32,
        "rpa": "rpa:submit:" + "a" * 16,
        "rpm": "rpm:approve:RP-2026-000001",
        "mod": "mod:approve:SC-2026-000001",
    }
    await journey.click(samples[family], actor=900, chat=-100)
    assert await rows(database, ScamRecord) == 0
    assert await rows(database, ReputationRequest) == 0
    assert await rows(database, Report) == 0
    assert not any(getattr(call, "reply_markup", None) for call in journey.transport.calls)
    assert any(getattr(call, "text", None) for call in journey.transport.calls)
