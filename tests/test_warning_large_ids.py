"""Real SQL regression for cancelled WARN receipts and history with Telegram IDs."""

from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message
from test_group_moderation_foundation import CHAT, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import LocalTransport, enabled

from app.bot.local_admin import history_panel
from app.bot.moderation_messages import HEADINGS, success_message
from app.models import GroupModerationDraft, GroupSanction
from app.moderation_services import GroupModerationService
from app.services import Service
from app.warning_services import reset_prompt, reset_warnings


async def cancelled_warning_receipt_and_history(factory, settings, target):
    native = NativeBot()
    await enabled(factory, native)
    async with factory() as session:
        local = GroupModerationService(session, native)
        draft = await local.prepare(
            CHAT,
            42,
            target,
            "WARN",
            until_date=None,
            reason="Large ID regression",
            request="large-id-warning",
        )
        sanction = await local.confirm(draft.id, 42)
        prompt = await reset_prompt(local, sanction, 42)
        await reset_warnings(local, prompt.id, 42)
        did, sid = draft.id, sanction.id

    transport = LocalTransport(native)
    async with Bot(settings.bot_token.get_secret_value(), session=transport) as bot:
        async with factory() as session:
            draft = await session.get(GroupModerationDraft, did)
            sanction = await session.get(GroupSanction, sid)
            assert sanction.status == "CANCELLED"
            rendered = await success_message(session, draft, sanction)
            assert rendered.startswith(f"<b>{HEADINGS['WARN']}</b>")
            assert "1/3" in rendered
            assert str(target) in rendered
            message = Message(
                message_id=100,
                date=datetime.now(UTC),
                chat=Chat(id=42, type="private"),
            ).as_(bot)
            await history_panel(
                message, Service(settings, session), 42, CHAT, target, warnings=True
            )
        assert any("Large ID regression" in (getattr(c, "text", "") or "") for c in transport.calls)
    assert native.calls == []  # Reading/revoking a warning never imposes a native sanction.


@pytest.mark.parametrize("target", [51, 8896102663])
async def test_cancelled_warning_with_large_ids_sqlite(database, settings, target):
    await cancelled_warning_receipt_and_history(database, settings, target)


@pytest.mark.parametrize("target", [51, 8896102663])
async def test_cancelled_warning_with_large_ids_postgres(postgres_contract, target):
    factory, settings = postgres_contract
    await cancelled_warning_receipt_and_history(factory, settings, target)
