import re
from html import unescape
from types import SimpleNamespace

import pytest
from sqlalchemy import insert

from app.bot.keyboards import admin_pages
from app.i18n import use_language
from app.models import User
from app.presentation import users
from app.services import DomainError, Service


@pytest.mark.asyncio
async def test_users_pages_have_100_rows_and_do_not_overlap(database, settings):
    async with database() as session:
        await session.execute(
            insert(User), [{"telegram_id": i, "username": f"user{i}"} for i in range(1, 206)]
        )
        await session.commit()
        service = Service(settings, session)
        first, total = await service.users(900, 0)
        second, _ = await service.users(900, 1)
        third, _ = await service.users(900, 2)
        assert total == 205
        assert [len(first), len(second), len(third)] == [100, 100, 5]
        assert len({u.id for u in first + second + third}) == total
        with pytest.raises(DomainError, match="forbidden"):
            await service.users(1, 0)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_100_long_names_fit_telegram_and_are_escaped(lang):
    rows = [
        SimpleNamespace(username=None, telegram_id=9223372036854775807, display_name="😀<&>\n" * 80)
        for _ in range(100)
    ]
    with use_language(lang):
        rendered = users(rows)
    assert rendered.count("👥") == 101
    assert "&lt;" in rendered and "&amp;" in rendered
    assert len(unescape(re.sub(r"<[^>]+>", "", rendered)).encode("utf-16-le")) // 2 < 4096


def test_user_pagination_uses_100_and_other_admin_lists_stay_at_5():
    markup = admin_pages("users", 1, 205, page_size=100)
    assert markup.inline_keyboard[0][1].text == "2 / 3"
    assert admin_pages("audit", 1, 205).inline_keyboard[0][1].text == "2 / 41"


def test_user_list_shows_numeric_id_or_no_id():
    rendered = users(
        [
            SimpleNamespace(username="example", telegram_id=42),
            SimpleNamespace(username="unknown", telegram_id=None),
        ]
    )
    assert "<code>👥 1. @example [42]</code>" in rendered
    assert "<code>👥 2. @unknown [ID nežinomas]</code>" in rendered


def test_user_list_numbers_continue_on_next_page():
    rendered = users([SimpleNamespace(username="example", telegram_id=42)], offset=100)
    assert "👥 101." in rendered
    assert "<code>" in rendered and "<a " not in rendered


@pytest.mark.asyncio
async def test_observed_id_automatically_supersedes_unknown_directory_row(database, settings):
    async with database() as session:
        service = Service(settings, session)
        placeholder = await service.resolve("@example")
        await service.set_trusted(900, "@example", True, "unknown-trusted")
        known = await service.observe(42, "EXAMPLE", "Observed name")
        await service.observe(42, "example", "Updated name")
        rows, total = await service.users(900, 0)
        assert total == 1
        assert [row.id for row in rows] == [known.id]
        assert rows[0].telegram_id == 42
        assert rows[0].display_name == "Updated name"
        assert await session.get(User, placeholder.id) is not None
        assert not (await service.profile("42"))["trusted"]
        assert (await service.profile(f"u:{placeholder.id}"))["trusted"]
    async with database() as session:
        rows, total = await Service(settings, session).users(900, 0)
        assert total == 1 and rows[0].telegram_id == 42


@pytest.mark.asyncio
async def test_directory_username_changes_and_reuse_do_not_merge_identity(database, settings):
    async with database() as session:
        service = Service(settings, session)
        placeholder = await service.resolve("@original")
        first = await service.observe(42, "original", "First")
        await service.observe(42, "changed", "First")
        rows, total = await service.users(900, 0)
        assert total == 2  # The old username is no longer linked to this numeric identity.
        assert {row.id for row in rows} == {placeholder.id, first.id}
        second = await service.observe(43, "original", "Second")
        rows, total = await service.users(900, 0)
        assert total == 2
        assert {row.id for row in rows} == {first.id, second.id}
        await service.observe(43, None, "No username")
        rows, total = await service.users(900, 0)
        assert total == 3
        assert {row.id for row in rows} == {placeholder.id, first.id, second.id}
