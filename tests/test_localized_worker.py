"""Background notifications resolve each recipient's saved locale."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.group_presentation import recovery_notification
from app.bot.group_runtime import process_group_jobs
from app.group_services import GroupService
from app.i18n import language, use_language
from app.services import Service


@pytest.mark.asyncio
async def test_worker_uses_recipient_locale_not_admin_or_previous_recipient(database, settings):
    async with database() as session:
        groups = GroupService(settings, session)
        service = Service(settings, session)
        await groups.register_group(900, -100, "Original <group>", True)
        for actor, lang in [(12, "en"), (13, "ru")]:
            await groups.observe_member(-100, actor, None, "Original name")
            await service.set_language(actor, lang)
            await groups.mark_private_contact(actor)
            await groups.subscribe(actor, -100, True)
        await groups.queue_recovery(900, -100, "https://t.me/+Invite123", "locales")
    bot = SimpleNamespace(send_message=AsyncMock(return_value=True))
    with use_language("lt"):
        assert await process_group_jobs(bot, settings, database) == 2
        assert language.get() == "lt"
    messages = {call.args[0]: call.args[1] for call in bot.send_message.await_args_list}
    assert messages[12] == recovery_notification(
        "Original <group>", "https://t.me/+Invite123", lang="en"
    )
    assert messages[13] == recovery_notification(
        "Original <group>", "https://t.me/+Invite123", lang="ru"
    )
