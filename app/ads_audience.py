"""Withdraw unavailable audiences under the existing exclusive identity/ACL lock."""

from sqlalchemy import or_, select

from app.models import AdAssignmentPolicy, AdCampaign, AdSchedule, ManagedGroup, now


async def detach_group_ads(session, settings, chat, *, actor=0, reason="GROUP_UNAVAILABLE"):
    """Caller holds Repository.lock_identity_metadata; never commits or performs I/O."""
    from app.ads_publication import PublicationService

    service = PublicationService(settings, session)
    campaigns = list(
        await session.scalars(
            select(AdSchedule.campaign_id)
            .where(AdSchedule.chat_id == chat, AdSchedule.excluded.is_(False))
            .distinct()
            .order_by(AdSchedule.campaign_id)
        )
    )
    total = 0
    for cid in campaigns:
        policy = await session.scalar(
            select(AdAssignmentPolicy)
            .where(AdAssignmentPolicy.campaign_id == cid)
            .with_for_update()
        )
        await session.execute(
            select(AdCampaign.id).where(AdCampaign.id == cid).with_for_update(read=True)
        )
        rows = list(
            await session.scalars(
                select(AdSchedule)
                .where(
                    AdSchedule.campaign_id == cid,
                    AdSchedule.chat_id == chat,
                    AdSchedule.excluded.is_(False),
                )
                .order_by(AdSchedule.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        for row in rows:
            row.excluded, row.next_run_at = True, None
            if row.status not in {"STOPPED", "EXPIRED"}:
                row.status = "STOPPED"
            row.version += 1
            row.last_error, row.alert_pending, row.updated_at = reason, False, now()
            # SENDING/UNCERTAIN attempts are historical delivery evidence, never replayed.
            await service.cancel_waiting(row, reason)
        if rows:
            if policy is not None:
                policy.version += 1
                policy.updated_at = now()
            service.ads.audit(
                actor,
                "group_detached",
                cid,
                chat_id=chat,
                schedule_ids=[r.id for r in rows],
                reason=reason,
            )
            total += len(rows)
    return total


async def prune_unavailable_groups(session, settings):
    """Repair legacy/offline removals; recheck group eligibility under the ACL lock."""
    from app.group_services import GroupService
    from app.repositories import Repository

    invalid = or_(
        ManagedGroup.approved.is_(False),
        ManagedGroup.enabled.is_(False),
        ManagedGroup.chat_type.not_in(("group", "supergroup")),
        GroupService.removed_group(ManagedGroup.chat_id),
    )
    query = (
        select(ManagedGroup.chat_id)
        .join(AdSchedule, AdSchedule.chat_id == ManagedGroup.chat_id)
        .where(AdSchedule.excluded.is_(False), invalid)
        .distinct()
        .order_by(ManagedGroup.chat_id)
    )
    candidates = list(await session.scalars(query))
    await session.commit()
    if not candidates:
        return 0
    await Repository(session).lock_identity_metadata()
    candidates = list(await session.scalars(query.where(ManagedGroup.chat_id.in_(candidates))))
    total = 0
    for chat in candidates:
        total += await detach_group_ads(session, settings, chat)
    await session.commit()
    return total
