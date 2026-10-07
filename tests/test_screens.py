"""Panel navigation sends at the bottom and retires its predecessor safely."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage

from app import presentation as p
from app.bot.screens import close_panel, flow_screen, panel_state, render


def screen():
    return SimpleNamespace(
        message_id=10,
        chat=SimpleNamespace(id=1),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=11)),
        bot=SimpleNamespace(delete_message=AsyncMock(), edit_message_reply_markup=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_render_sends_new_before_deleting_old_and_tracks_anchor():
    message = screen()
    state = SimpleNamespace(update_data=AsyncMock())
    token = panel_state.set(state)
    try:
        result = await render(message, "new")
    finally:
        panel_state.reset(token)
    assert result.message_id == 11
    message.answer.assert_awaited_once_with("new", reply_markup=None)
    state.update_data.assert_awaited_once_with(screen_message_id=11)
    message.bot.delete_message.assert_awaited_once_with(1, 10)


@pytest.mark.asyncio
async def test_send_failure_keeps_old_panel():
    message = screen()
    message.answer.side_effect = RuntimeError("transport unavailable")
    with pytest.raises(RuntimeError):
        await render(message, "new")
    message.bot.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_flow_replaces_stored_anchor():
    message = screen()
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"screen_message_id": 9}), update_data=AsyncMock()
    )
    await flow_screen(message, state, "new")
    state.update_data.assert_awaited_once_with(screen_message_id=11)
    message.bot.delete_message.assert_awaited_once_with(1, 9)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["message to delete not found", "message can't be deleted"])
async def test_close_known_telegram_restrictions_are_quiet(reason):
    message = screen()
    message.bot.delete_message.side_effect = TelegramBadRequest(
        method=DeleteMessage(chat_id=1, message_id=10), message=reason
    )
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"screen_message_id": 10}), clear=AsyncMock()
    )
    await close_panel(message, state)
    state.clear.assert_awaited_once()
    message.answer.assert_not_awaited()
    if "can't" in reason:
        message.bot.edit_message_reply_markup.assert_awaited_once()


def test_leaderboard_prefers_display_name_and_escapes_html():
    user = SimpleNamespace(display_name="Dog <team>", username="example")
    output = p.leaderboard([{"user": user, "score": 12}])
    assert "Dog &lt;team&gt;" in output
    assert "@example" not in output
