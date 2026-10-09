from datetime import UTC, datetime

import pytest
from aiogram.types import Chat, Message, MessageEntity, Sticker

from app.test_ui.emojis import edited_text, icon_from_message

ICON = "5368324170671202286"


def message(**values):
    return Message(
        message_id=1, date=datetime.now(UTC), chat=Chat(id=900, type="private"), **values
    )


def test_caption_and_custom_sticker_can_supply_genuine_emoji_id():
    assert (
        icon_from_message(
            message(
                caption="📑",
                caption_entities=[
                    MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id=ICON)
                ],
            )
        )
        == ICON
    )
    assert (
        icon_from_message(
            message(
                sticker=Sticker(
                    file_id="test-file",
                    file_unique_id="test-unique",
                    type="custom_emoji",
                    width=100,
                    height=100,
                    is_animated=True,
                    is_video=False,
                    custom_emoji_id=ICON,
                )
            )
        )
        == ICON
    )


def test_multiple_text_entities_keep_prefix_offsets_and_typed_html():
    result = edited_text(
        message(
            text="📑 <b>safe</b> 📑",
            entities=[
                MessageEntity(type="custom_emoji", offset=offset, length=2, custom_emoji_id=ICON)
                for offset in (0, 15)
            ],
        )
    )
    tag = f'<tg-emoji emoji-id="{ICON}">📑</tg-emoji>'
    assert result == f"{tag} <b>safe</b> {tag}"


@pytest.mark.parametrize("offset,length", [(1, 2), (0, 3), (0, 0)])
def test_invalid_utf16_entity_boundaries_are_rejected(offset, length):
    with pytest.raises(ValueError):
        edited_text(
            message(
                text="📑",
                entities=[
                    MessageEntity(
                        type="custom_emoji", offset=offset, length=length, custom_emoji_id=ICON
                    )
                ],
            )
        )
