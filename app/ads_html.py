"""Strict Telegram HTML input to native UTF-16 entities; no HTML sent as visible tags."""

import re
from html.parser import HTMLParser

from app.errors import DomainError

TAGS = {
    "b": "bold",
    "strong": "bold",
    "i": "italic",
    "em": "italic",
    "u": "underline",
    "ins": "underline",
    "s": "strikethrough",
    "strike": "strikethrough",
    "del": "strikethrough",
    "code": "code",
    "pre": "pre",
    "blockquote": "blockquote",
    "tg-spoiler": "spoiler",
    "span": "spoiler",
    "a": "text_link",
    "tg-emoji": "custom_emoji",
}


class TelegramHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = ""
        self.entities: list[dict] = []
        self.stack: list[tuple[str, int, dict]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag not in TAGS or len(attributes) != len(attrs):
            raise DomainError("ads_entity")
        item = {"type": TAGS[tag]}
        allowed = {
            "a": {"href"},
            "tg-emoji": {"emoji-id"},
            "blockquote": {"expandable"},
            "span": {"class"},
            "code": {"class"},
        }
        if set(attributes) - allowed.get(tag, set()):
            raise DomainError("ads_entity")
        if tag == "span" and attributes.get("class") != "tg-spoiler":
            raise DomainError("ads_entity")
        if tag == "code" and "class" in attributes:
            if (
                not self.stack
                or self.stack[-1][0] != "pre"
                or not re.fullmatch(r"language-[A-Za-z0-9_+-]{1,64}", attributes["class"] or "")
            ):
                raise DomainError("ads_entity")
            self.stack[-1][2]["language"] = attributes["class"][9:]
        if tag == "a":
            item["url"] = attributes.get("href", "")
        elif tag == "tg-emoji":
            item["custom_emoji_id"] = attributes.get("emoji-id", "")
        elif tag == "blockquote" and "expandable" in attributes:
            item["type"] = "expandable_blockquote"
        self.stack.append((tag, len(self.text.encode("utf-16-le")) // 2, item))

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            raise DomainError("ads_entity")
        _, offset, item = self.stack.pop()
        length = len(self.text.encode("utf-16-le")) // 2 - offset
        if length and not (tag == "code" and self.stack and self.stack[-1][0] == "pre"):
            self.entities.append({**item, "offset": offset, "length": length})

    def handle_data(self, data):
        self.text += data

    def handle_comment(self, data):
        raise DomainError("ads_entity")

    def handle_decl(self, decl):
        raise DomainError("ads_entity")


def has_html(text):
    return bool(re.search(r"</?(?:" + "|".join(TAGS) + r")(?:\s|>|/)", text))


def parse_html(text):
    if not has_html(text):
        return {"text": text, "entities": []}
    parser = TelegramHTML()
    try:
        parser.feed(text)
        parser.close()
    except (ValueError, UnicodeError) as error:
        raise DomainError("ads_entity") from error
    if parser.stack:
        raise DomainError("ads_entity")
    return {"text": parser.text, "entities": parser.entities}
