"""REP media captions use the real dispatcher and the existing moderation safeguards."""

from datetime import UTC, datetime
from time import monotonic
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select
from test_telegram import journey as telegram_journey

from app import presentation as p
from app.bot.middleware import ServiceMiddleware
from app.bot.states import RepVoteFlow
from app.group_services import GroupService
from app.i18n import t
from app.models import ReputationRequest
from app.services import Service

journey = telegram_journey
CHAT = -1003976343267
MEDIA = {
    "photo": [{"file_id": "photo", "file_unique_id": "photo-unique", "width": 100, "height": 100}],
    "document": {"file_id": "document", "file_unique_id": "document-unique"},
}


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("command,value", [("+rep", 1), ("-REP", -1)])
@pytest.mark.parametrize("media", ["photo", "document"])
async def test_caption_submits_username_comment_without_applying_rep(
    journey, database, settings, lang, command, value, media
):
    async with database() as session:
        service = Service(settings, session)
        target = await service.observe(42, "sample_user", "Sample User")
        await service.set_language(1, lang)
        target_id = target.id
    await journey.send(
        caption=f"{command} @sample_user\nPayment incident with evidence",
        chat=CHAT,
        **{media: MEDIA[media]},
    )
    async with database() as session:
        request = (await session.scalars(select(ReputationRequest))).one()
        assert request.receiver_user_id == target_id
        assert request.chat_id == CHAT
        assert request.value == value
        assert request.comment == "Payment incident with evidence"
        assert request.status == "PENDING"
        assert (await Service(settings, session).profile("42"))["score"] == 0
    assert journey.text()


async def test_caption_reply_uses_replied_user_when_no_explicit_target(journey, database, settings):
    replied = Message(
        message_id=42,
        date=datetime.now(UTC),
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=User(id=42, is_bot=False, first_name="Target", username="sample_user"),
        text="Original message",
    )
    await journey.send(
        caption="-rep Payment incident",
        chat=CHAT,
        reply_to_message=replied,
        photo=MEDIA["photo"],
    )
    async with database() as session:
        request = (await session.scalars(select(ReputationRequest))).one()
        target = await Service(settings, session).repo.user_by_telegram(42)
        assert request.receiver_user_id == target.id and request.comment == "Payment incident"


async def test_caption_wizard_accepts_photo_comment_only_in_reply_to_prompt(journey, database):
    await journey.send(caption="+rep 42", chat=CHAT, photo=MEDIA["photo"])
    state = journey.dp.fsm.get_context(bot=journey.bot, chat_id=CHAT, user_id=1)
    assert await state.get_state() == RepVoteFlow.comment.state
    draft = await state.get_data()
    prompt = Message(
        message_id=draft["rep_prompt_id"],
        date=datetime.now(UTC),
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=User(id=123456, is_bot=True, first_name="Bot"),
    )
    await journey.send(caption="Great service", chat=CHAT, photo=MEDIA["photo"])
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 0
    await journey.send(
        caption="Great service", chat=CHAT, photo=MEDIA["photo"], reply_to_message=prompt
    )
    assert await state.get_state() is None
    async with database() as session:
        request = (await session.scalars(select(ReputationRequest))).one()
        assert request.value == 1 and request.comment == "Great service"


@pytest.mark.parametrize(
    "caption,error", [("-rep 1 Great service", "self_rep"), ("-rep 42 tiny", "rep_comment")]
)
async def test_caption_preserves_self_vote_and_comment_validation(
    journey, database, caption, error
):
    await journey.send(caption=caption, chat=CHAT, photo=MEDIA["photo"])
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 0
    assert p.error(error) in journey.text()


async def test_caption_cannot_bypass_rep_cooldown(journey, database):
    await journey.send(caption="-rep 42 Payment incident", chat=CHAT, photo=MEDIA["photo"])
    await journey.send(caption="+rep 43 Great service", chat=CHAT, photo=MEDIA["photo"])
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 1
    assert p.error("rep_cooldown") in journey.text()


@pytest.mark.parametrize("anonymous", [False, True])
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_caption_uses_same_early_rate_limit_and_anonymous_checks(
    database, settings, monkeypatch, anonymous, lang
):
    async with database() as session:
        await Service(settings, session).set_language(1, lang)
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=User(id=1, is_bot=False, first_name="Tester"),
        sender_chat=Chat(id=CHAT, type="supergroup") if anonymous else None,
        caption="-rep 42 Payment incident",
        photo=MEDIA["photo"],
    )
    middleware = ServiceMiddleware(settings, database)
    middleware.recent[1] = [monotonic()] * 20
    handler = AsyncMock()
    await middleware(handler, message, {})
    handler.assert_not_awaited()
    assert answer.await_args.args[0] == t(
        "error.anonymous_identity" if anonymous else "p.cooldown", lang
    )


async def test_removed_group_still_ignores_caption_rep(journey, database, settings):
    async with database() as session:
        groups = GroupService(settings, session)
        await groups.register_group(900, CHAT, "Group", True)
        await groups.remove_group(900, CHAT)
    await journey.send(caption="-rep 42 Payment incident", chat=CHAT, photo=MEDIA["photo"])
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 0
    assert not journey.transport.calls


async def test_unrelated_photo_caption_does_not_create_vote(journey, database):
    await journey.send(caption="Example: -rep 42 Payment incident", chat=CHAT, photo=MEDIA["photo"])
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 0
    assert not journey.transport.calls


async def test_text_rep_followed_by_separate_evidence_photo_creates_one_pending_vote(
    journey, database, settings
):
    async with database() as session:
        await Service(settings, session).observe(42, "sample_user", "Sample User")
    await journey.send("-rep @sample_user Payment incident", chat=CHAT)
    await journey.send(photo=MEDIA["photo"], chat=CHAT)
    await journey.send(caption="Evidence of payment", photo=MEDIA["photo"], chat=CHAT)
    async with database() as session:
        request = (await session.scalars(select(ReputationRequest))).one()
        assert request.value == -1 and request.status == "PENDING"
        assert request.comment == "Payment incident"
        assert (await Service(settings, session).profile("42"))["score"] == 0


async def test_caption_lookup_uses_existing_target_and_duplicate_limiter(journey):
    await journey.send("/ask 42", chat=CHAT)
    sent = len(journey.transport.calls)
    await journey.send(caption="/ask 42", chat=CHAT, photo=MEDIA["photo"])
    assert len(journey.transport.calls) == sent
    await journey.send(caption="/ask 43", chat=CHAT, photo=MEDIA["photo"])
    assert "43" in journey.transport.calls[-1].text
