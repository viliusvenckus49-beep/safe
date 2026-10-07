import pytest
from aiogram.methods import SendDocument

from app.bot.group_keyboards import GroupAction
from app.bot.group_presentation import members_backup
from app.group_services import GroupService
from app.i18n import use_language

pytest_plugins = ["test_telegram"]


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_readable_backup_preserves_id_and_unicode_without_json(lang):
    with use_language(lang):
        content = members_backup(
            "Group\nname",
            [{"display_name": "Jonas\nŽąsis", "username": "example", "telegram_id": 42}],
        ).decode("utf-8")
    assert "1. Jonas Žąsis — @example [42]" in content
    assert "telegram_id" not in content and "{" not in content
    assert "Group name" in content


def test_backup_empty_and_member_without_username():
    assert (
        "no ID"
        not in members_backup(
            "Group", [{"display_name": "Jonas", "username": None, "telegram_id": 42}]
        ).decode()
    )
    assert "Išsaugotų narių nėra." in members_backup("Group", []).decode()


@pytest.mark.asyncio
async def test_export_button_sends_txt_document(journey, database, settings):
    async with database() as session:
        groups = GroupService(settings, session)
        await groups.register_group(900, -100, "Group", True)
        await groups.observe_member(-100, 42, "example", "Jonas")
    await journey.click(GroupAction(action="export", chat_id=-100).pack(), actor=900)
    document = next(
        call.document for call in journey.transport.calls if isinstance(call, SendDocument)
    )
    assert document.filename == "safecheck-members.txt"
    assert "1. Jonas — @example [42]" in document.data.decode("utf-8")
