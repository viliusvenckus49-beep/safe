"""Prevent direct user-visible strings from bypassing the locale boundary."""

import ast
from pathlib import Path

from app.i18n import CATALOGS


def test_active_telegram_calls_do_not_embed_literal_interface_text():
    paths = [*Path("app/bot").glob("*.py"), Path("app/main.py")]
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {
                "answer",
                "reply",
                "edit_text",
                "send_message",
                "answer_photo",
                "answer_document",
                "button",
            }:
                continue
            values = [kw.value for kw in node.keywords if kw.arg in {"text", "caption"}]
            if node.func.attr in {"answer", "reply", "edit_text"} and node.args:
                values.append(node.args[0])
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    assert not any(c.isalpha() for c in value.value), (str(path), node.lineno)


def test_literal_translation_keys_exist_in_every_locale():
    for path in Path("app").rglob("*.py"):
        if "locales" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "t"
                and node.args
            ):
                key = node.args[0]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    assert all(key.value in catalog for catalog in CATALOGS.values()), (
                        str(path),
                        node.lineno,
                        key.value,
                    )
