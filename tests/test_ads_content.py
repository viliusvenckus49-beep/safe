"""Pure Telegram payload validation: UTF-16, native buttons and actual emoji metadata."""

from copy import deepcopy

import pytest
from aiogram.types import MessageEntity

from app.ads_content import EMPTY, entities_for, parse_buttons, units, url, validate_content
from app.bot.ads import preview_keyboard
from app.errors import DomainError
from app.i18n import CATALOGS


@pytest.mark.parametrize(
    "value",
    [
        "https://t.me/Example",
        "http://example.com/a?x=1&b=two",
        "https://example.com/path-a",
        "https://münchen.example/path",
    ],
)
def test_valid_urls(value):
    assert url(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "data:text/html,bad",
        "file:///etc/passwd",
        "https://user:pass@example.com",
        "https://example.com\n",
        "https:///path",
        "https://example.com:99999",
        "https://bad host",
        "https://example.com@evil.com",
        "tg://unsafe",
    ],
)
def test_unsafe_urls_are_rejected(value):
    with pytest.raises(DomainError):
        url(value)


def test_rows_kinds_and_real_native_buttons():
    text = "One - https://t.me/Example && Two - popup: Hello\nAlert - alert: Warning\nShare - share: Share this\nCopy - copy: Copy me"
    rows = parse_buttons(text)
    assert [len(row) for row in rows] == [2, 1, 1, 1]
    keyboard = preview_keyboard({**EMPTY, "buttons": rows}, "d" + "a" * 32)
    one, two = keyboard.inline_keyboard[0]
    assert one.url == "https://t.me/Example" and one.callback_data is None
    assert two.callback_data.startswith("ab3:d") and len(two.callback_data.encode()) <= 64
    assert keyboard.inline_keyboard[2][0].url.startswith("https://t.me/share/url?")
    assert keyboard.inline_keyboard[3][0].copy_text.text == "Copy me"


@pytest.mark.parametrize(
    "value",
    [
        "No delimiter",
        " - https://example.com",
        "X - ",
        "X - popup: ",
        "X - alert: " + "A" * 201,
        "X - copy: " + "A" * 257,
        "X - callback: grant_admin",
        "X - https://example.com &&",
        "X - rules",
    ],
)
def test_malformed_or_unavailable_buttons_are_not_faked(value):
    with pytest.raises(DomainError):
        parse_buttons(value)


def test_utf16_custom_emoji_at_each_button_lead_is_preserved():
    first = "😀 Name - https://t.me/Example"
    text = first + " && 🍒 Second - popup: Hello"
    start = units(first + " && ")
    entities = [
        {"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": "531234567890"},
        {"type": "custom_emoji", "offset": start, "length": 2, "custom_emoji_id": "531234567891"},
    ]
    rows = parse_buttons(text, entities)
    assert rows[0][0]["label"] == "Name" and rows[0][1]["label"] == "Second"
    markup = preview_keyboard({**EMPTY, "buttons": rows}, "v1")
    assert markup.inline_keyboard[0][0].icon_custom_emoji_id == "531234567890"
    assert markup.inline_keyboard[0][1].icon_custom_emoji_id == "531234567891"
    assert parse_buttons(first)[0][0]["label"] == "😀 Name"
    assert parse_buttons(first)[0][0]["icon_custom_emoji_id"] is None


@pytest.mark.parametrize("offset,length", [(1, 1), (0, 1), (-1, 2), (100, 2)])
def test_surrogate_boundaries_and_entity_offsets(offset, length):
    with pytest.raises(DomainError):
        entities_for(
            "😀 Text",
            [
                {
                    "type": "custom_emoji",
                    "offset": offset,
                    "length": length,
                    "custom_emoji_id": "123",
                }
            ],
        )


def test_unicode_formatting_is_not_converted_to_html_or_fancy_text():
    text = "<b>Literal</b> 😀"
    content = {
        **deepcopy(EMPTY),
        "text": text,
        "entities": [
            {"type": "bold", "offset": 0, "length": 3},
            {
                "type": "custom_emoji",
                "offset": units(text) - 2,
                "length": 2,
                "custom_emoji_id": "12345",
            },
        ],
    }
    checked = validate_content(content)
    assert checked["text"] == text
    assert MessageEntity.model_validate(checked["entities"][1]).custom_emoji_id == "12345"


@pytest.mark.parametrize(
    "change",
    [
        {"groups": [-100]},
        {"text": "x" * 4097},
        {"media": [{"kind": "document", "file_id": "abc"}]},
        {"media": [{"kind": "photo", "file_id": "https://example.com"}]},
        {"media": [{"kind": "video", "file_id": "file_id"}] * 11},
        {"text": "x" * 1025, "media": [{"kind": "photo", "file_id": "abc"}]},
        {
            "buttons": [
                [{"label": "x", "kind": "callback", "value": "admin", "icon_custom_emoji_id": None}]
            ]
        },
        {
            "entities": [{"type": "text_link", "offset": 0, "length": 1, "url": "javascript:bad"}],
            "text": "x",
        },
    ],
)
def test_content_never_accepts_publishing_settings_or_invalid_media(change):
    with pytest.raises(DomainError):
        validate_content({**deepcopy(EMPTY), **change})


def test_caption_and_album_boundaries():
    assert validate_content(
        {**deepcopy(EMPTY), "text": "x" * 1024, "media": [{"kind": "photo", "file_id": "abc"}] * 10}
    )["media"]
    assert validate_content({**deepcopy(EMPTY), "text": "x" * 4096})["text"]
    assert parse_buttons("") == []


def test_locale_keys_are_complete_without_future_feature_promises():
    keys = {key for key in CATALOGS["lt"] if key.startswith("ads.")}
    assert keys and all(
        {k for k in CATALOGS[lang] if k.startswith("ads.")} == keys for lang in ("en", "ru")
    )
