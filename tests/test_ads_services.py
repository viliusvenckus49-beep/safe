"""Durable edits, ownership, ACL revocation and optimistic concurrent saves."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from test_integration_contracts import postgres_contract as postgres_contract

from app.admin_services import AdminService
from app.ads_services import AdsService
from app.errors import DomainError
from app.models import AdCampaign, AdContentDraft, AdContentVersion, AuditEvent, now

pytestmark = pytest.mark.asyncio


async def create(sessions, settings, request="campaign"):
    async with sessions() as session:
        return await AdsService(settings, session).create(900, "Private internal title", request)


async def draft(
    sessions, settings, cid, request="draft", *, base=1, actor=900, field="TEXT", value=None
):
    async with sessions() as session:
        return await AdsService(settings, session).stage(
            actor,
            cid,
            base,
            field,
            value if value is not None else {"text": "Advertising text", "entities": []},
            request,
        )


async def save(sessions, settings, did, actor=900):
    async with sessions() as session:
        service = AdsService(settings, session)
        await service.mark_previewed(actor, did)
        return await service.save(actor, did)


async def test_creation_and_save_are_durable_idempotent_and_versioned(database, settings):
    campaign = await create(database, settings)
    assert (await create(database, settings)).id == campaign.id
    async with database() as session:
        row, current = await AdsService(settings, session).current(900, campaign.id)
        assert row.status == "DRAFT" and row.client_telegram_id is None
        assert current.content == {"text": "", "entities": [], "media": [], "buttons": []}
    pending = await draft(database, settings, campaign.id)
    async with database() as session:
        with pytest.raises(DomainError, match="preview_required"):
            await AdsService(settings, session).save(900, pending.id)
    result = await save(database, settings, pending.id)
    async with database() as session:
        duplicate = await AdsService(settings, session).save(900, pending.id)
        assert duplicate.id == result.id and result.revision == 2
        assert await session.scalar(select(func.count()).select_from(AdContentVersion)) == 2
        assert await session.scalar(select(func.count()).select_from(AdCampaign)) == 1
        assert {r.action for r in await session.scalars(select(AuditEvent))} >= {
            "ads_created",
            "ads_content_saved",
        }


async def test_request_payload_cannot_change_on_replay(database, settings):
    campaign = await create(database, settings)
    original = await draft(database, settings, campaign.id)
    assert (await draft(database, settings, campaign.id)).id == original.id
    with pytest.raises(DomainError, match="stale"):
        await draft(database, settings, campaign.id, value={"text": "Changed", "entities": []})


async def test_private_draft_cannot_be_claimed_by_another_global_admin(database, settings):
    settings.admin_ids = "900,901"
    campaign = await create(database, settings)
    pending = await draft(database, settings, campaign.id)
    async with database() as session:
        for method in ("draft", "mark_previewed", "save", "cancel"):
            with pytest.raises(DomainError, match="stale"):
                await getattr(AdsService(settings, session), method)(901, pending.id)
            await session.rollback()


async def test_live_revocation_blocks_save_and_even_read_routes(database, settings):
    settings.group_owner_id = 999
    settings.admin_ids = "900"
    campaign = await create(database, settings)
    pending = await draft(database, settings, campaign.id)
    async with database() as session:
        await AdsService(settings, session).mark_previewed(900, pending.id)
        await AdminService(settings, session).change(999, "900", False, "a" * 32)
    async with database() as session:
        service = AdsService(settings, session)
        for operation in (
            service.save(900, pending.id),
            service.get(900, campaign.id),
            service.history(900, campaign.id),
            service.campaigns(900),
        ):
            with pytest.raises(DomainError, match="forbidden"):
                await operation
            await session.rollback()
        assert (await session.get(AdCampaign, campaign.id)).version == 1


async def race(sessions, settings):
    campaign = await create(sessions, settings)
    first = await draft(sessions, settings, campaign.id, "edit-one")
    second = await draft(
        sessions, settings, campaign.id, "edit-two", value={"text": "Other text", "entities": []}
    )
    for pending in (first, second):
        async with sessions() as session:
            await AdsService(settings, session).mark_previewed(900, pending.id)

    async def perform(did):
        async with sessions() as session:
            try:
                await AdsService(settings, session).save(900, did)
                return "ok"
            except DomainError as error:
                return error.code

    results = await asyncio.gather(perform(first.id), perform(second.id))
    assert sorted(results) == ["ads_conflict", "ok"]
    async with sessions() as session:
        assert (await session.get(AdCampaign, campaign.id)).version == 2
        assert await session.scalar(select(func.count()).select_from(AdContentVersion)) == 2


async def test_sqlite_concurrent_edits(database, settings):
    await race(database, settings)


async def test_postgres_concurrent_edits(postgres_contract):
    sessions, settings = postgres_contract
    await race(sessions, settings)


async def test_media_restart_order_and_duplicates_do_not_touch_text(database, settings):
    campaign = await create(database, settings)
    await save(database, settings, (await draft(database, settings, campaign.id)).id)
    async with database() as session:
        pending = await AdsService(settings, session).stage(
            900, campaign.id, 2, "MEDIA", None, "media-start"
        )
    for mid, kind in [(12, "video"), (10, "photo"), (12, "video")]:
        async with database() as session:
            await AdsService(settings, session).append_media(
                900, pending.id, {"kind": kind, "file_id": f"file_{mid}"}, mid
            )
    async with database() as session:
        service = AdsService(settings, session)
        restored = await service.draft(900, pending.id)
        assert restored.content["text"] == "Advertising text"
        assert [i["file_id"] for i in restored.content["media"]] == ["file_10", "file_12"]
        assert (await service.unfinished(900))[0].id == pending.id
        await service.finish_media(900, pending.id)
    await save(database, settings, pending.id)
    async with database() as session:
        assert (await AdsService(settings, session).current(900, campaign.id))[1].content[
            "text"
        ] == "Advertising text"


async def test_restore_creates_new_history_and_archive_never_deletes_it(database, settings):
    campaign = await create(database, settings)
    async with database() as session:
        initial = (await AdsService(settings, session).current(900, campaign.id))[1]
    await save(database, settings, (await draft(database, settings, campaign.id)).id)
    restored = await draft(
        database, settings, campaign.id, "restore", base=2, field="RESTORE", value=initial.id
    )
    await save(database, settings, restored.id)
    async with database() as session:
        service = AdsService(settings, session)
        history = await service.history(900, campaign.id)
        assert [v.revision for v in history] == [3, 2, 1]
        assert (
            history[0].content == history[-1].content
            and history[1].content["text"] == "Advertising text"
        )
        await service.archive(900, campaign.id, 3)
        assert await service.campaigns(900) == []
        assert len(await service.history(900, campaign.id)) == 3
        with pytest.raises(DomainError):
            await service.stage(
                900, campaign.id, 3, "TEXT", {"text": "x", "entities": []}, "archived-edit"
            )


async def test_expired_cancelled_and_unpreviewed_drafts_never_save(database, settings):
    campaign = await create(database, settings)
    pending = await draft(database, settings, campaign.id)
    async with database() as session:
        await session.execute(
            update(AdContentDraft)
            .where(AdContentDraft.id == pending.id)
            .values(expires_at=now() - timedelta(seconds=1), previewed=True)
        )
        await session.commit()
        with pytest.raises(DomainError, match="stale"):
            await AdsService(settings, session).save(900, pending.id)
