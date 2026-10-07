from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select
from test_telegram import journey as telegram_journey

from app import presentation as p
from app.bot import keyboards as kb
from app.bot.states import RepVoteFlow
from app.errors import DomainError
from app.i18n import t, use_language
from app.models import ReputationRequest
from app.services import Service

journey = telegram_journey


@pytest.mark.asyncio
@pytest.mark.parametrize("comment", [None, "", "abcd", "     ", "a   b", "\u200b" * 5, "x" * 1501])
async def test_service_requires_five_visible_characters(database, settings, comment):
    async with database() as session:
        with pytest.raises(DomainError, match="rep_comment"):
            await Service(settings, session).vote(1, "42", 1, None, "request", comment=comment)
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1, 1])
async def test_comment_is_stored_pending_and_replay_cannot_change_it(database, settings, value):
    async with database() as session:
        service = Service(settings, session)
        result = await service.vote(1, "42", value, None, "request", comment="  abcde  ")
        assert result["reputation_request"].comment == "abcde"
        assert result["reputation_request"].status == "PENDING" and result["score"] == 0
        await service.vote(1, "42", value, None, "request", comment="abcde")
        with pytest.raises(DomainError):
            await service.vote(1, "42", value, None, "request", comment="different")
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("value", [-1, 1])
async def test_profile_button_requests_comment_rejects_short_then_submits(
    journey, database, settings, lang, value
):
    async with database() as session:
        await Service(settings, session).set_language(1, lang)
    await journey.click(kb.action("vote+" if value == 1 else "vote-", "42"))
    assert await journey.state() == RepVoteFlow.comment.state
    await journey.send("abcd")
    assert await journey.state() == RepVoteFlow.comment.state
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 0
    await journey.send("abcde")
    assert await journey.state() is None
    async with database() as session:
        request = (await session.scalars(select(ReputationRequest))).one()
        assert request.value == value and request.comment == "abcde" and request.status == "PENDING"
    with use_language(lang):
        assert t("error.rep_comment") in journey.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["+rep", "-rep"])
async def test_command_comment_wizard_and_cancel(journey, database, command):
    await journey.send(command + " 42")
    assert await journey.state() == RepVoteFlow.comment.state
    await journey.click(kb.action("close"))
    assert await journey.state() is None
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 0
    await journey.send(command + " 42 Great service")
    async with database() as session:
        request = (await session.scalars(select(ReputationRequest))).one()
        assert request.comment == "Great service"


@pytest.mark.asyncio
async def test_group_wizard_only_accepts_author_reply_to_prompt(journey, database):
    await journey.click(kb.action("vote+", "42"), chat=-100)
    state = journey.dp.fsm.get_context(bot=journey.bot, chat_id=-100, user_id=1)
    data = await state.get_data()
    prompt = Message(
        message_id=data["rep_prompt_id"],
        date=datetime.now(UTC),
        chat=Chat(id=-100, type="supergroup"),
        from_user=User(id=123456, is_bot=True, first_name="Bot"),
    )
    await journey.send("Great service", chat=-100)
    await journey.send("Great service", actor=2, chat=-100, reply_to_message=prompt)
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 0
    await journey.send("abcd", chat=-100, reply_to_message=prompt)
    data = await state.get_data()
    assert data["rep_prompt_id"] != prompt.message_id
    prompt = prompt.model_copy(update={"message_id": data["rep_prompt_id"]})
    await journey.send("Great service", chat=-100, reply_to_message=prompt)
    assert await state.get_state() is None
    async with database() as session:
        request = (await session.scalars(select(ReputationRequest))).one()
        assert request.comment == "Great service" and request.chat_id == -100


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_moderation_comment_escaped_and_legacy_missing_comment_supported(lang):
    request = SimpleNamespace(
        reference="RP-2026-000001",
        receiver=SimpleNamespace(username="target", telegram_id=42),
        giver=SimpleNamespace(username="giver", telegram_id=1),
        value=1,
        status="PENDING",
        created_at=datetime.now(UTC),
        comment="<b>Fake</b> & evidence",
    )
    with use_language(lang):
        card = p.reputation_review(request)
        assert "&lt;b&gt;Fake&lt;/b&gt; &amp; evidence" in card
        request.comment = None
        assert "Fake" not in p.reputation_review(request)
