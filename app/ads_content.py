"""Validated Telegram content snapshots; no scheduler, client grants or network requests."""

import re
from copy import deepcopy
from ipaddress import IPv6Address
from urllib.parse import urlsplit

from aiogram.types import Message, MessageEntity

from app.ads_html import has_html, parse_html
from app.errors import DomainError

EMPTY = {"text": "", "entities": [], "media": [], "buttons": []}
ENTITY_TYPES = {
    "bold",
    "italic",
    "underline",
    "strikethrough",
    "spoiler",
    "code",
    "pre",
    "blockquote",
    "expandable_blockquote",
    "text_link",
    "text_mention",
    "custom_emoji",
    "mention",
    "hashtag",
    "cashtag",
    "bot_command",
    "url",
    "email",
    "phone_number",
}


def units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise DomainError("ads_url")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
        host = parsed.hostname.encode("idna").decode()
        if ":" in host:
            IPv6Address(host)
        elif not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in host.rstrip(".").split(".")
        ):
            raise ValueError()
    except (ValueError, UnicodeError) as error:
        raise DomainError("ads_url") from error
    return value


def emoji_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise DomainError("ads_entity")
    return value


def entities_for(text: str, values: list[dict]) -> list[dict]:
    raw = text.encode("utf-16-le")
    result = []
    if len(values) > 100:
        raise DomainError("ads_entity")
    for value in values:
        try:
            item = MessageEntity.model_validate(value)
            if (
                item.type not in ENTITY_TYPES
                or item.offset < 0
                or item.length < 1
                or item.offset + item.length > len(raw) // 2
            ):
                raise ValueError()
            raw[: item.offset * 2].decode("utf-16-le")
            raw[item.offset * 2 : (item.offset + item.length) * 2].decode("utf-16-le")
            if item.type == "text_link":
                url(item.url or "")
            if item.type == "custom_emoji":
                emoji_id(item.custom_emoji_id or "")
            if item.type == "text_mention" and (not item.user or item.user.id <= 0):
                raise ValueError()
            if item.language and (
                len(item.language) > 64 or re.search(r"[\x00-\x1f]", item.language)
            ):
                raise ValueError()
        except (ValueError, UnicodeError, TypeError) as error:
            raise DomainError("ads_entity") from error
        result.append(item.model_dump(mode="json", exclude_none=True))
    for a in result:
        for b in result:
            if a["offset"] < b["offset"] < a["offset"] + a["length"] < b["offset"] + b["length"]:
                raise DomainError("ads_entity")
    return result


def text_from_message(message: Message) -> dict:
    text = message.text if message.text is not None else message.caption
    if text is None or units(text) > 4096:
        raise DomainError("ads_text")
    values = message.entities if message.text is not None else message.caption_entities
    automatic = {"url", "mention", "hashtag", "cashtag", "email", "phone_number", "bot_command"}
    if not values or (has_html(text) and all(v.type in automatic for v in values)):
        parsed = parse_html(text)
        return {
            "text": parsed["text"],
            "entities": entities_for(parsed["text"], parsed["entities"]),
        }
    return {
        "text": text,
        "entities": entities_for(
            text, [e.model_dump(mode="json", exclude_none=True) for e in values or []]
        ),
    }


def media_from_message(message: Message) -> dict:
    for kind in ("photo", "video", "animation", "document"):
        obj = getattr(message, kind, None)
        if obj:
            return {"kind": kind, "file_id": (obj[-1] if kind == "photo" else obj).file_id}
    raise DomainError("ads_media")


def parse_buttons(text: str, entities: list[dict] | None = None) -> list[list[dict]]:
    if units(text) > 8192:
        raise DomainError("ads_buttons")
    custom = [e for e in entities_for(text, entities or []) if e["type"] == "custom_emoji"]
    used: set[int] = set()
    rows = []
    position = 0
    for line in text.splitlines(keepends=True):
        row = []
        segment_start = 0
        for segment in line.rstrip("\r\n").split("&&"):
            label, delimiter, action = segment.partition(" - ")
            start = position + segment_start
            segment_start += units(segment) + 2
            if not segment.strip():
                if line.strip():
                    raise DomainError("ads_buttons")
                continue
            if not delimiter or not label.strip():
                raise DomainError("ads_buttons")
            label_start = start + units(label) - units(label.lstrip())
            label_end = start + units(label)
            icon = None
            for i, entity in enumerate(custom):
                if label_start <= entity["offset"] < label_end:
                    if (
                        icon
                        or entity["offset"] != label_start
                        or entity["offset"] + entity["length"] > label_end
                    ):
                        raise DomainError("ads_entity")
                    icon = emoji_id(entity["custom_emoji_id"])
                    label = label.encode("utf-16-le")[
                        ((entity["offset"] - start) + entity["length"]) * 2 :
                    ].decode("utf-16-le")
                    used.add(i)
            action, label = action.strip(), label.strip()
            if not label or units(label) > 64:
                raise DomainError("ads_buttons")
            if action == "rules":
                # Bot API has no group-rules endpoint; never create a dead/pretend rules button.
                raise DomainError("ads_rules")
            prefix, sep, value = action.partition(":")
            if prefix in {"popup", "alert", "share", "copy"} and sep:
                kind, value = prefix, value.strip()
                limit = 200 if kind in {"popup", "alert"} else 256 if kind == "copy" else 1024
                if not value or units(value) > limit:
                    raise DomainError("ads_buttons")
            else:
                kind, value = "url", url(action)
            row.append({"label": label, "kind": kind, "value": value, "icon_custom_emoji_id": icon})
        if row:
            if len(row) > 8:
                raise DomainError("ads_buttons")
            rows.append(row)
        position += units(line)
    if len(used) != len(custom) or len(rows) > 10 or sum(map(len, rows)) > 50:
        raise DomainError("ads_buttons")
    return rows


def validate_content(content: dict) -> dict:
    try:
        if not isinstance(content, dict) or set(content) != set(EMPTY):
            raise ValueError()
        text, media, buttons = content["text"], content["media"], content["buttons"]
        if (
            not isinstance(text, str)
            or not isinstance(media, list)
            or not isinstance(buttons, list)
        ):
            raise ValueError()
        if units(text) > (1024 if media else 4096) or len(media) > 10 or len(buttons) > 10:
            raise ValueError()
        for item in media:
            if (
                set(item) != {"kind", "file_id"}
                or item["kind"] not in {"photo", "video", "animation", "document"}
                or not isinstance(item["file_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", item["file_id"])
            ):
                raise ValueError()
        if len(media) > 1 and any(item["kind"] not in {"photo", "video"} for item in media):
            raise ValueError()
        if sum(map(len, buttons)) > 50:
            raise ValueError()
        for row in buttons:
            if not isinstance(row, list) or not 1 <= len(row) <= 8:
                raise ValueError()
            for button in row:
                if set(button) != {"label", "kind", "value", "icon_custom_emoji_id"}:
                    raise ValueError()
                label, kind, value = button["label"], button["kind"], button["value"]
                if (
                    not isinstance(label, str)
                    or not label.strip()
                    or units(label) > 64
                    or re.search(r"[\r\n\x00]", label)
                ):
                    raise ValueError()
                if not isinstance(value, str) or kind not in {
                    "url",
                    "popup",
                    "alert",
                    "copy",
                    "share",
                }:
                    raise ValueError()
                if kind == "url":
                    url(value)
                elif not value or units(value) > (
                    200 if kind in {"popup", "alert"} else 256 if kind == "copy" else 1024
                ):
                    raise ValueError()
                if button["icon_custom_emoji_id"] is not None:
                    emoji_id(button["icon_custom_emoji_id"])
        return {**deepcopy(content), "entities": entities_for(text, content["entities"])}
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as error:
        raise DomainError("ads_content") from error
