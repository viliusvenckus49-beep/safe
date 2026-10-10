from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_mtproto_relay import Client, relay_settings
from test_telegram import journey as telegram_journey

from app.bot.callbacks import Action
from app.errors import DomainError
from app.group_services import GroupService
from app.models import BanAction, User
from app.mtproto_relay import GroupHelpRelay, set_relay
from app.services import Service

journey = telegram_journey


@pytest.fixture(autouse=True)
def clear_lookup_relay():
    set_relay(None)
    yield
    set_relay(None)


def connect(database, client=None):
    settings = relay_settings()
    client = client or Client()
    relay = GroupHelpRelay(settings, database, client, SimpleNamespace(id=555))
    set_relay(relay)
    return settings, client, relay


@pytest.mark.asyncio
async def test_public_lookup_discovers_unseen_user_without_registering_scam(database):
    settings, client, _ = connect(database)
    async with database() as session:
        profile = await Service(settings, session).profile("@ScAmMeR")
        assert profile["user"].telegram_id == 22
        assert profile["user"].username == "scammer"
        assert profile["user"].display_name == "Original name Text"
        assert profile["scam"] is None
        assert not (await session.scalars(select(BanAction))).all()
    assert len(client.calls) == 1 and client.calls[0].username == "scammer"


@pytest.mark.asyncio
async def test_first_id_resolution_preserves_unknown_row_reputation_and_trusted(database):
    settings, _, _ = connect(database)
    legacy = settings.model_copy(update={"group_help_enabled": False})
    async with database() as session:
        core = Service(legacy, session)
        unknown = await core.resolve("@scammer")
        old_id = unknown.id
        await core.admin_adjust_rep(900, "@scammer", 3, "Verified earlier reputation", "old-rep")
        await core.set_trusted(900, "@scammer", True, "old-trusted")
    async with database() as session:
        profile = await Service(settings, session).profile("@scammer")
        assert profile["user"].id == old_id and profile["user"].telegram_id == 22
        assert profile["score"] == 3 and profile["trusted"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["rep", "report", "trusted", "adjust", "top", "scam"])
async def test_every_public_target_flow_uses_verified_automatic_identity(database, operation):
    settings, client, _ = connect(database)
    async with database() as session:
        core = Service(settings, session)
        if operation == "rep":
            await core.vote(1, "@scammer", 1, None, "lookup-rep", comment="Verified feedback")
        elif operation == "report":
            await core.submit_report(1, "@scammer", "Verified report details", [], "lookup-report")
        elif operation == "trusted":
            await core.set_trusted(900, "@scammer", True, "lookup-trusted")
        elif operation == "adjust":
            await core.admin_adjust_rep(900, "@scammer", 1, "Verified adjustment", "lookup-adjust")
        elif operation == "top":
            await core.set_top_visibility(
                900, "@scammer", True, "Verified TOP change", "lookup-top"
            )
        else:
            await GroupService(settings, session).register_group(900, -1000, "Protected", True)
            record = await core.add_scam(900, "@scammer")
            assert record.target.telegram_id == 22
            jobs = list((await session.scalars(select(BanAction))).all())
            assert len(jobs) == 1 and jobs[0].telegram_id == 22
        assert (await core.repo.user_by_username("scammer")).telegram_id == 22
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_unknown_pinned_callback_completes_same_identity(database):
    settings, _, _ = connect(database)
    async with database() as session:
        old = await Service(
            settings.model_copy(update={"group_help_enabled": False}), session
        ).resolve("@scammer")
        result = await Service(settings, session).resolve(f"u:{old.id}")
        assert result.id == old.id and result.telegram_id == 22


@pytest.mark.asyncio
async def test_current_username_lookup_does_not_move_existing_numeric_scam(database):
    settings, _, _ = connect(database)
    async with database() as session:
        core = Service(settings, session)
        old = await core.observe(23, "scammer", "Previous owner")
        record = await core.add_scam(900, "23")
        current = await core.resolve("@scammer")
        assert current.telegram_id == 22 and current.id != old.id
        await session.refresh(old)
        assert old.telegram_id == 23 and old.username is None
        assert (await core.repo.scam_by_id(record.id)).target.telegram_id == 23
        assert (await core.resolve(f"u:{old.id}")).telegram_id == 23


@pytest.mark.asyncio
async def test_stale_unknown_callback_never_redirects_to_another_row(database):
    settings, _, _ = connect(database)
    async with database() as session:
        old = await Service(
            settings.model_copy(update={"group_help_enabled": False}), session
        ).resolve("@scammer")
        core = Service(settings, session)
        await core.observe(22, None, "Known person")
        result = await core.resolve(f"u:{old.id}")
        assert result.id == old.id and result.telegram_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unavailable", "wrong_username", "flood"])
async def test_unavailable_lookup_falls_back_without_inventing_id(database, failure):
    settings, client, relay = connect(database)
    if failure == "wrong_username":
        client.username = "other_person"
    elif failure == "flood":
        from telethon.errors import FloodWaitError

        client.error = FloodWaitError(request=None, capture=120)
    else:
        client.error = OSError("Unavailable")
    async with database() as session:
        core = Service(settings, session)
        user = await core.resolve("@scammer")
        assert user.telegram_id is None
        assert (await core.resolve("@scammer")).id == user.id
    assert len(client.calls) == 1
    if failure == "flood":
        from app.models import now

        assert relay.flood_until > now()


@pytest.mark.asyncio
async def test_cached_success_and_disabled_lookup_preserve_existing_behavior(database):
    settings, client, _ = connect(database)
    async with database() as session:
        core = Service(settings, session)
        first = await core.resolve("@scammer")
        second = await core.resolve("@scammer")
        assert first.id == second.id
        offline = Service(settings.model_copy(update={"group_help_enabled": False}), session)
        assert (await offline.resolve("@unseen_person")).telegram_id is None
        assert (await core.resolve("8805206375")).telegram_id == 8805206375
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_invalid_targets_never_call_telegram(database):
    settings, client, _ = connect(database)
    async with database() as session:
        for value in ("@bad!", "@x", "u:bad", "https://t.me/scammer"):
            with pytest.raises(DomainError):
                await Service(settings, session).resolve(value)
    assert not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["positive", "negative", "report"])
async def test_verified_username_resolution_keeps_self_abuse_guards(database, operation):
    settings, _, _ = connect(database)
    async with database() as session:
        core = Service(settings, session)
        expected = "self_report" if operation == "report" else "self_rep"
        with pytest.raises(DomainError, match=expected):
            if operation == "report":
                await core.submit_report(
                    22, "@scammer", "Verified report details", [], "self-report"
                )
            else:
                value = 1 if operation == "positive" else -1
                await core.vote(
                    22, "@scammer", value, None, "self-rep", comment="Verified feedback"
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["ask", "rep", "profile", "report"])
async def test_commands_discover_new_public_target_id(journey, database, settings, command):
    enabled = relay_settings()
    settings.group_help_enabled = True
    settings.group_help_bot_id = enabled.group_help_bot_id
    settings.group_help_scope_ids = enabled.group_help_scope_ids
    _, client, _ = connect(database)
    await journey.send(f"/{command} @scammer")
    if command != "report":
        assert "<code>22</code>" in journey.text()
        markup = journey.transport.calls[-1].reply_markup
        profile_callback = next(
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.startswith("sc|profile|")
        )
        assert Action.unpack(profile_callback).value == "22"
    async with database() as session:
        assert (
            await session.scalar(select(User).where(User.telegram_id == 22))
        ).username == "scammer"
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_lookup_cache_expires_and_simultaneous_requests_share_one_query(
    database, monkeypatch
):
    import asyncio

    settings, client, relay = connect(database)
    results = await asyncio.gather(*(relay.lookup_username("scammer") for _ in range(5)))
    assert all(user.id == 22 for user in results) and len(client.calls) == 1
    cached_until = relay.lookups["scammer"][0]
    monkeypatch.setattr("app.mtproto_relay.monotonic", lambda: cached_until + 1)
    client.user_id = 33
    assert (await relay.lookup_username("scammer")).id == 33
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_ask_rechecks_cached_failure_links_existing_scam_and_enqueues(
    journey, database, settings, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr("app.bot.middleware.monotonic", lambda: clock[0])
    enabled, client, _ = connect(database)
    settings.group_help_enabled = True
    settings.group_help_bot_id = enabled.group_help_bot_id
    settings.group_help_scope_ids = enabled.group_help_scope_ids
    legacy = settings.model_copy(update={"group_help_enabled": False})
    async with database() as session:
        core = Service(legacy, session)
        await GroupService(settings, session).register_group(900, -1000, "Protected", True)
        record = await core.add_scam(900, "@scammer")
        record_id, target_id = record.id, record.target_id
    client.error = OSError("Unavailable")
    await journey.send("/ask @scammer")
    client.error = None
    clock[0] += 10
    await journey.send("/ask @scammer")
    async with database() as session:
        core = Service(settings, session)
        record = await core.repo.scam_by_id(record_id)
        assert record.target_id == target_id and record.target.telegram_id == 22
        jobs = list((await session.scalars(select(BanAction))).all())
        assert len(jobs) == 1 and jobs[0].telegram_id == 22
    assert len(client.calls) == 2
    client.user_id = 33
    clock[0] += 10
    await journey.send("/ask @scammer")
    async with database() as session:
        core = Service(settings, session)
        assert (await core.repo.user_by_username("scammer")).telegram_id == 33
        assert (await core.repo.scam_by_id(record_id)).target.telegram_id == 22
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_explicit_refresh_still_respects_telegram_flood_wait(database):
    from telethon.errors import FloodWaitError

    settings, client, _ = connect(database)
    client.error = FloodWaitError(request=None, capture=120)
    async with database() as session:
        core = Service(settings, session)
        assert (await core.profile("@scammer", refresh_identity=True))["user"].telegram_id is None
        assert (await core.profile("@scammer", refresh_identity=True))["user"].telegram_id is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_failed_live_check_reports_cause_and_preserves_known_numeric_identity(database):
    from telethon.errors import UsernameInvalidError

    from app import presentation as p
    from app.i18n import t

    settings, client, _ = connect(database)
    client.error = UsernameInvalidError(request=None)
    async with database() as session:
        core = Service(settings, session)
        await core.observe(22, "scammer", "Original person")
        record = await core.add_scam(900, "22")
        profile = await core.profile("@scammer", refresh_identity=True)
        assert profile["user"].telegram_id == 22 and profile["scam"].id == record.id
        assert profile["identity_lookup"].code == "invalid_username"
        text = p.profile(profile)
        assert t("diagnostic.identity_invalid_username") in text
        assert t("diagnostic.identity_saved") in text
