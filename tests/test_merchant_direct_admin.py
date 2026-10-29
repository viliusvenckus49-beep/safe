"""Full aiogram direct CRUD journeys with server-side permissions and one panel."""

import time

import pytest
from aiogram.methods import AnswerCallbackQuery, SendMessage
from sqlalchemy import func, select
from test_merchants_data import merchant
from test_merchants_telegram import initial_menu, open_admin_catalog, open_catalog, screen, tap
from test_telegram import journey as journey

from app.bot.callbacks import Merchant
from app.i18n import CATALOGS
from app.models import MerchantEntry, MerchantProposal
from app.services import Service


def buttons(j):
    return [b.text for row in screen(j)[1].reply_markup.inline_keyboard for b in row]


def context(j):
    return j.dp.fsm.get_context(bot=j.bot, chat_id=900, user_id=900)


async def select_entry(j, database, settings):
    async with database() as s:
        e, _ = await merchant(s, settings, 42, "North Studio")
        eid = e.id
    await open_admin_catalog(j)
    await tap(j, "a_list", actor=900)
    await tap(j, "a_entry", actor=900)
    return eid


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_direct_create_and_exact_admin_menus(journey, database, settings, lang):
    await initial_menu(journey, 42)
    async with database() as s:
        await Service(settings, s).set_language(900, lang)
    await open_admin_catalog(journey)
    assert buttons(journey) == [
        CATALOGS[lang]["mc." + k] for k in ("admin_list", "add", "search", "settings", "admin_back")
    ]
    anchor = screen(journey)[0]
    await tap(journey, "a_create", actor=900)
    await journey.send("42", actor=900)
    assert CATALOGS[lang]["mc.editor_title"] in screen(journey)[1].text
    async with database() as s:
        assert await s.scalar(select(func.count()).select_from(MerchantEntry)) == 0
    for field, value in (
        ("name", "North Studio"),
        ("description", "Teisėtos paslaugos <&>\n\nKita pastraipa."),
        ("contact", "https://t.me/contact_name"),
    ):
        await tap(journey, "a_field", actor=900, value=field)
        assert buttons(journey) == [CATALOGS[lang]["mc.step_back"]]
        await journey.send(value, actor=900)
    await tap(journey, "a_field", actor=900, value="city_code")
    await tap(journey, "a_cityvalue", actor=900, value="vilnius")
    assert CATALOGS[lang]["mc.confirm"] in buttons(journey)
    async with database() as s:
        assert await s.scalar(select(func.count()).select_from(MerchantEntry)) == 0
    await tap(journey, "a_commit", actor=900)
    assert buttons(journey) == [
        CATALOGS[lang]["mc." + k] for k in ("change", "suspend", "remove", "manage_back")
    ]
    assert CATALOGS[lang]["mc.manage_title"] in screen(journey)[1].text
    assert (
        "<blockquote><i>Teisėtos paslaugos &lt;&amp;&gt;\n\nKita pastraipa.</i></blockquote>"
        in screen(journey)[1].text
    )
    assert screen(journey)[0] == anchor
    # Commands may open their own panel, inline browsing does not send another.
    async with database() as s:
        entry = await s.scalar(select(MerchantEntry))
        assert entry.status == "APPROVED" and entry.contact == "https://t.me/contact_name"
        assert await s.scalar(select(func.count()).select_from(MerchantProposal)) == 0
    await tap(journey, "a_root", actor=900)
    assert CATALOGS[lang]["mc.admin"] in screen(journey)[1].text


@pytest.mark.parametrize("field", ["name", "description", "city_code", "contact"])
async def test_field_back_preserves_other_edits_disables_input_and_returns_one_step(
    journey, database, settings, field
):
    eid = await select_entry(journey, database, settings)
    anchor = screen(journey)[0]
    await tap(journey, "a_editor", actor=900)
    await tap(journey, "a_field", actor=900, value="name")
    assert "North Studio" in screen(journey)[1].text
    await journey.send("Changed Studio", actor=900)
    await tap(journey, "a_field", actor=900, value=field)
    assert "◀ ATGAL" in buttons(journey)
    assert "◀ PREKEIVIŲ VALDYMAS" not in buttons(journey)
    await tap(journey, "a_editback", actor=900)
    assert "PREKEIVIO REDAGAVIMAS" in screen(journey)[1].text
    assert "Changed Studio" in screen(journey)[1].text
    assert await journey.state(900) is None
    assert (await context(journey).get_data())["mc_input"] is None
    async with database() as s:
        assert (await s.get(MerchantEntry, eid)).name == "North Studio"
    await tap(journey, "a_commit", actor=900)
    async with database() as s:
        entry = await s.get(MerchantEntry, eid)
        assert entry.name == "Changed Studio" and entry.city_code == "vilnius"
    assert screen(journey)[0] == anchor


async def test_cancel_discards_every_field_and_returns_same_merchant(journey, database, settings):
    eid = await select_entry(journey, database, settings)
    before = screen(journey)[1].text
    await tap(journey, "a_editor", actor=900)
    await tap(journey, "a_field", actor=900, value="description")
    await journey.send("Unpublished new description", actor=900)
    await tap(journey, "a_field", actor=900, value="city_code")
    await tap(journey, "a_cityvalue", actor=900, value="kaunas")
    await tap(journey, "a_discard", actor=900)
    assert screen(journey)[1].text == before
    assert await journey.state(900) is None
    assert (await context(journey).get_data())["mc_editor"] is None
    async with database() as s:
        assert (await s.get(MerchantEntry, eid)).city_code == "vilnius"
        assert await s.scalar(select(func.count()).select_from(MerchantEntry)) == 1


async def test_pause_resume_and_named_delete_confirmation(journey, database, settings):
    eid = await select_entry(journey, database, settings)
    await tap(journey, "a_suspend", actor=900)
    assert "▶ ATNAUJINTI" in buttons(journey)
    async with database() as s:
        assert (await s.get(MerchantEntry, eid)).status == "SUSPENDED"
    await tap(journey, "a_restore", actor=900)
    assert "⏸ SUSTABDYTI" in buttons(journey)
    await tap(journey, "a_remove", actor=900)
    assert "<b>North Studio</b>" in screen(journey)[1].text
    async with database() as s:
        assert (await s.get(MerchantEntry, eid)).status == "APPROVED"
    await tap(journey, "a_entry", actor=900)
    assert "PREKEIVIO VALDYMAS" in screen(journey)[1].text
    await tap(journey, "a_remove", actor=900)
    await tap(journey, "a_delete", actor=900)
    async with database() as s:
        entry = await s.get(MerchantEntry, eid)
        assert entry.status == "REMOVED" and entry.description
    assert buttons(journey) == ["◀ PREKEIVIŲ VALDYMAS"]


@pytest.mark.parametrize(
    "action",
    [
        "terms",
        "begin",
        "edit",
        "draft",
        "submit",
        "removal",
        "requestremove",
        "cancel",
        "a_terms",
        "a_target",
        "a_queue",
        "a_edit",
        "a_draft",
        "a_submit",
        "a_approve",
        "a_reject",
        "a_audit",
        "a_name",
        "a_setcity",
    ],
)
async def test_retired_callbacks_never_run_proposal_logic(journey, database, settings, action):
    await select_entry(journey, database, settings)
    before = screen(journey)[1].text
    await tap(journey, actor=900, forged=Merchant(action=action, entry=1, version=1).pack())
    assert screen(journey)[1].text == before
    assert isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
    assert journey.transport.calls[-1].show_alert
    async with database() as s:
        assert await s.scalar(select(func.count()).select_from(MerchantProposal)) == 0
        assert await s.scalar(select(func.count()).select_from(MerchantEntry)) == 1


async def test_owner_has_no_requests_and_cannot_forge_admin_callbacks(journey, database, settings):
    eid = await select_entry(journey, database, settings)
    await open_catalog(journey, 42)
    await tap(journey, "mine", actor=42)
    assert buttons(journey) == ["👤 PREKEIVIO PROFILIS", "◀ PREKEIVIŲ MENIU"]
    before = screen(journey)[1].text
    for action in (
        "a_editor",
        "a_commit",
        "a_suspend",
        "a_restore",
        "a_delete",
        "a_create",
        "a_settings",
        "edit",
        "removal",
    ):
        await tap(journey, actor=42, forged=Merchant(action=action, entry=eid, version=1).pack())
        assert journey.transport.calls[-1].show_alert
        assert screen(journey)[1].text == before


async def test_expired_editor_or_replayed_commit_cannot_write(journey, database, settings):
    eid = await select_entry(journey, database, settings)
    await tap(journey, "a_editor", actor=900)
    ctx = context(journey)
    editor = (await ctx.get_data())["mc_editor"]
    old = Merchant(action="a_commit", entry=eid, version=editor["revision"]).pack()
    editor["until"] = time.time() - 1
    await ctx.update_data(mc_editor=editor)
    await tap(journey, actor=900, forged=old)
    assert journey.transport.calls[-1].show_alert
    async with database() as s:
        assert (await s.get(MerchantEntry, eid)).name == "North Studio"


async def test_invalid_input_can_be_corrected_on_same_panel(journey, database, settings):
    await select_entry(journey, database, settings)
    anchor = screen(journey)[0]
    await tap(journey, "a_editor", actor=900)
    await tap(journey, "a_field", actor=900, value="name")
    await journey.send("x", actor=900)
    assert "Netinkama įvestis" in screen(journey)[1].text
    assert buttons(journey) == ["◀ ATGAL"]
    await journey.send("Correct Studio", actor=900)
    await tap(journey, "a_commit", actor=900)
    assert "Correct Studio" in screen(journey)[1].text
    assert screen(journey)[0] == anchor


async def test_create_cancel_leaves_no_empty_merchant_and_settings_no_intake(
    journey, database, settings
):
    await initial_menu(journey, 42)
    await open_admin_catalog(journey)
    initial_sends = sum(isinstance(c, SendMessage) for c in journey.transport.calls)
    await tap(journey, "a_create", actor=900)
    await journey.send("42", actor=900)
    await tap(journey, "a_discard", actor=900)
    await tap(journey, "a_settings", actor=900)
    assert "prašymai" not in screen(journey)[1].text.lower() + str(buttons(journey)).lower()
    assert sum(isinstance(c, SendMessage) for c in journey.transport.calls) == initial_sends
    async with database() as s:
        assert await s.scalar(select(func.count()).select_from(MerchantEntry)) == 0
        assert await s.scalar(select(func.count()).select_from(MerchantProposal)) == 0


async def test_revoked_admin_cannot_submit_or_continue_field_input(journey, database, settings):
    from app.models import Administrator

    eid = await select_entry(journey, database, settings)
    await tap(journey, "a_editor", actor=900)
    await tap(journey, "a_field", actor=900, value="name")
    async with database() as s:
        s.add(Administrator(telegram_id=900, active=False, assigned_by=901))
        await s.commit()
    await journey.send("Forbidden new name", actor=900)
    assert await journey.state(900) is None
    async with database() as s:
        assert (await s.get(MerchantEntry, eid)).name == "North Studio"
        assert await s.scalar(select(func.count()).select_from(MerchantProposal)) == 0


async def test_commit_replay_and_wrong_merchant_id_do_not_write(journey, database, settings):
    eid = await select_entry(journey, database, settings)
    await tap(journey, "a_editor", actor=900)
    await tap(journey, "a_field", actor=900, value="name")
    await journey.send("Confirmed Studio", actor=900)
    editor = (await context(journey).get_data())["mc_editor"]
    await tap(
        journey,
        actor=900,
        forged=Merchant(action="a_commit", entry=eid + 1, version=editor["revision"]).pack(),
    )
    assert journey.transport.calls[-1].show_alert
    old = Merchant(action="a_commit", entry=eid, version=editor["revision"]).pack()
    await tap(journey, actor=900, forged=old)
    await tap(journey, actor=900, forged=old)
    assert journey.transport.calls[-1].show_alert
    async with database() as s:
        entry = await s.get(MerchantEntry, eid)
        assert entry.name == "Confirmed Studio"
        assert entry.version == 2
        assert await s.scalar(select(func.count()).select_from(MerchantEntry)) == 1
    await open_catalog(journey)
    await tap(journey, "cities")
    await tap(journey, "list", value="vilnius")
    assert "Confirmed Studio" in screen(journey)[1].text
    await tap(journey, "profile")
    assert "Confirmed Studio" in screen(journey)[1].text


async def test_delete_needs_matching_confirmation_and_cancel_invalidates_it(
    journey, database, settings
):
    eid = await select_entry(journey, database, settings)
    await tap(journey, actor=900, forged=Merchant(action="a_delete", entry=eid, version=1).pack())
    assert journey.transport.calls[-1].show_alert
    await tap(journey, "a_remove", actor=900)
    token = (await context(journey).get_data())["mc_delete"]["token"]
    old = Merchant(action="a_delete", entry=eid, version=1, value=token).pack()
    await tap(journey, "a_entry", actor=900)
    await tap(journey, actor=900, forged=old)
    assert journey.transport.calls[-1].show_alert
    async with database() as s:
        assert (await s.get(MerchantEntry, eid)).status == "APPROVED"
