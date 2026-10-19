"""ADS lock order: shared ACL -> optional policy -> campaign -> schedules -> rate gates.

PostgreSQL shared ACL reads coexist; existing ACL revocation's UPDATE waits for
them. SQLite retains the existing single-writer lock. Never use around network IO.
"""

from contextlib import asynccontextmanager

from sqlalchemy import select

from app.models import AdAssignmentPolicy, AdCampaign, AdSchedule, IdentityLock
from app.repositories import Repository


async def acl_lock(session):
    if session.get_bind().dialect.name != "postgresql":
        await Repository(session).lock_identity_metadata()
        return
    from sqlalchemy.dialects.postgresql import insert

    await session.execute(insert(IdentityLock).values(id=1).on_conflict_do_nothing())
    await session.execute(
        select(IdentityLock.id).where(IdentityLock.id == 1).with_for_update(read=True)
    )


@asynccontextmanager
async def ads_transaction(session, cid=None, sid=None, *, policy=False, skip_locked=False):
    try:
        # Discard earlier snapshots before acquiring authority/row locks in order.
        await session.commit()
        await acl_lock(session)
        if policy and cid is not None:
            row = await session.scalar(
                select(AdAssignmentPolicy)
                .where(AdAssignmentPolicy.campaign_id == cid)
                .with_for_update(skip_locked=skip_locked)
                .execution_options(populate_existing=True)
            )
            if row is None:
                yield False
                await session.commit()
                return
        if cid is not None:
            await session.execute(
                select(AdCampaign.id).where(AdCampaign.id == cid).with_for_update(read=True)
            )
        if sid is not None:
            row = await session.scalar(
                select(AdSchedule)
                .where(AdSchedule.id == sid)
                .with_for_update(skip_locked=skip_locked)
                .execution_options(populate_existing=True)
            )
            if row is None:
                yield False
                await session.commit()
                return
        yield True
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
