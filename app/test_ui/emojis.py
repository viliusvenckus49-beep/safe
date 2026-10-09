"""Read genuine Telegram custom-emoji entities; never infer them from Unicode."""

from html import escape

from aiogram.types import Message

from app.test_ui.profile import validate_icon


def icon_from_message(message: Message) -> str:
    entities = message.entities if message.text is not None else message.caption_entities
    custom = [e.custom_emoji_id for e in entities or [] if e.type == "custom_emoji"]
    if custom:
        if len(custom) != 1:
            raise ValueError("Send one custom emoji")
        return validate_icon(custom[0] or "")
    if message.sticker and message.sticker.type == "custom_emoji":
        return validate_icon(message.sticker.custom_emoji_id or "")
    return validate_icon((message.text or "").strip())


def edited_text(message: Message) -> str:
    """Keep typed HTML/placeholders while preserving custom emoji sent from the picker."""
    content = message.text or ""
    data = content.encode("utf-16-le")
    entries = [e for e in message.entities or [] if e.type == "custom_emoji"]
    end = len(data) // 2
    for entity in sorted(entries, key=lambda e: e.offset, reverse=True):
        if entity.offset < 0 or entity.length <= 0 or entity.offset + entity.length > end:
            raise ValueError("Invalid emoji entity")
        emoji_id = validate_icon(entity.custom_emoji_id or "")
        start, stop = entity.offset * 2, (entity.offset + entity.length) * 2
        fallback = data[start:stop].decode("utf-16-le")
        tag = f'<tg-emoji emoji-id="{emoji_id}">{escape(fallback)}</tg-emoji>'
        data = data[:start] + tag.encode("utf-16-le") + data[stop:]
        end = entity.offset
    return data.decode("utf-16-le")
