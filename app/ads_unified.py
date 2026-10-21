"""Per-ad AUTO ADS management over the existing durable publication infrastructure."""

from datetime import datetime

from sqlalchemy import exists, func, select

from app.ads_customers import CustomerAccess
from app.ads_global import GlobalAdsService
from app.ads_invitations import ClientInvitations
from app.ads_locks import ads_transaction
from app.ads_publication import PublicationService, publication_rights
from app.ads_schedule import INTERVALS, VILNIUS, slot
from app.ads_services import AdsService
from app.errors import DomainError
from app.models import (
    AdAssignmentPolicy,
    AdCampaign,
    AdPublicationAttempt,
    AdSchedule,
    AuditEvent,
    ManagedGroup,
    now,
)
from app.moderation_services import utc


class UnifiedAds:
    def __init__(self, settings, session):
        self.settings, self.session = settings, session
        self.ads = AdsService(settings, session)
        self.global_ads = GlobalAdsService(settings, session)
        self.pub = PublicationService(settings, session)
        self.grants = CustomerAccess(settings, session)

    @staticmethod
    def active():
        return exists(
            select(AdSchedule.id).where(
                AdSchedule.campaign_id == AdCampaign.id,
                AdSchedule.status == "ACTIVE",
                AdSchedule.excluded.is_(False),
                (AdSchedule.end_at.is_(None) | (AdSchedule.end_at > now())),
            )
        )

    async def counts(self, actor):
        await self.ads.authorize(actor)
        total = await self.session.scalar(
            select(func.count()).select_from(AdCampaign).where(AdCampaign.status == "DRAFT")
        )
        active = await self.session.scalar(
            select(func.count())
            .select_from(AdCampaign)
            .where(AdCampaign.status == "DRAFT", self.active())
        )
        return total or 0, active or 0, (total or 0) - (active or 0)

    async def listing(self, actor, *, status="all", query="", page=0):
        await self.ads.authorize(actor)
        if status not in {"all", "active", "paused"} or len(query) > 128 or not 0 <= page <= 10000:
            raise DomainError("ads_stale")
        statement = select(AdCampaign).where(AdCampaign.status == "DRAFT")
        if status != "all":
            statement = statement.where(self.active() if status == "active" else ~self.active())
        if query:
            # Treat wildcard characters literally, never as a query language.
            statement = statement.where(AdCampaign.internal_name.icontains(query, autoescape=True))
        return list(
            await self.session.scalars(
                statement.order_by(AdCampaign.id.desc()).offset(page * 4).limit(5)
            )
        )

    async def policy(self, actor, cid):
        return await self.global_ads.policy(actor, cid)

    async def schedules(self, cid, *, locked=False):
        query = select(AdSchedule).where(AdSchedule.campaign_id == cid).order_by(AdSchedule.id)
        if locked:
            query = query.with_for_update().execution_options(populate_existing=True)
        return list(await self.session.scalars(query))

    async def details(self, actor, cid):
        campaign, content = await self.ads.current(actor, cid)
        policy = await self.policy(actor, cid)
        schedules = [
            s for s in await self.schedules(cid) if not s.excluded and s.chat_id is not None
        ]
        grant = await self.grants.latest(cid)
        stats = await self.statistics(actor, cid)
        return campaign, content, policy, schedules, grant, stats

    async def statistics(self, actor, cid):
        await self.ads.get(actor, cid, archived=True)
        attempts = AdPublicationAttempt
        base = select(attempts).join(AdSchedule).where(AdSchedule.campaign_id == cid)
        counts = dict(
            (
                await self.session.execute(
                    select(attempts.status, func.count())
                    .join(AdSchedule)
                    .where(AdSchedule.campaign_id == cid)
                    .group_by(attempts.status)
                )
            ).all()
        )
        last = await self.session.scalar(
            select(func.max(attempts.completed_at))
            .join(AdSchedule)
            .where(AdSchedule.campaign_id == cid, attempts.status == "SENT")
        )
        first = await self.session.scalar(
            select(func.min(attempts.created_at))
            .join(AdSchedule)
            .where(AdSchedule.campaign_id == cid)
        )
        rows = await self.schedules(cid)
        due = [
            utc(s.next_run_at)
            for s in rows
            if s.status == "ACTIVE"
            and not s.excluded
            and s.next_run_at
            and (not s.end_at or utc(s.next_run_at) < utc(s.end_at))
        ]
        errors = list(
            await self.session.scalars(
                base.where(attempts.error.is_not(None)).order_by(attempts.id.desc()).limit(5)
            )
        )
        groups = list(
            await self.session.execute(
                select(ManagedGroup.chat_id, ManagedGroup.title)
                .join(AdSchedule, AdSchedule.chat_id == ManagedGroup.chat_id)
                .where(AdSchedule.campaign_id == cid, AdSchedule.excluded.is_(False))
                .order_by(ManagedGroup.chat_id)
            )
        )
        return {
            "counts": counts,
            "total": sum(counts.values()),
            "last": last,
            "first": first,
            "next": min(due) if due else None,
            "errors": errors,
            "groups": groups,
        }

    async def deliveries(self, actor, cid, page=0):
        await self.ads.get(actor, cid, archived=True)
        if not 0 <= page <= 10000:
            raise DomainError("ads_stale")
        return list(
            await self.session.scalars(
                select(AdPublicationAttempt)
                .join(AdSchedule)
                .where(AdSchedule.campaign_id == cid)
                .order_by(AdPublicationAttempt.id.desc())
                .offset(page * 5)
                .limit(6)
            )
        )

    async def history(self, actor, cid, page=0):
        await self.ads.get(actor, cid, archived=True)
        if not 0 <= page <= 10000:
            raise DomainError("ads_stale")
        return list(
            await self.session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.action.startswith("ads_"),
                    AuditEvent.details["campaign_id"].as_integer() == cid,
                )
                .order_by(AuditEvent.id.desc())
                .offset(page * 5)
                .limit(6)
            )
        )

    async def eligible(self, actor):
        await self.ads.authorize(actor)
        return list(
            await self.session.scalars(
                self.global_ads.eligible_groups().order_by(ManagedGroup.chat_id)
            )
        )

    async def checked(self, actor, cid, expected):
        await self.ads.get(actor, cid)
        policy = await self.session.get(AdAssignmentPolicy, cid, populate_existing=True)
        if not policy or policy.version != expected:
            raise DomainError("ads_conflict")
        return policy

    async def preflight(self, actor, cid, bot, chats=None):
        await self.ads.get(actor, cid)
        targets = (
            chats
            if chats is not None
            else [
                s.chat_id
                for s in await self.schedules(cid)
                if not s.excluded and s.chat_id is not None
            ]
        )
        await self.session.commit()
        for chat in targets:
            await self.pub.group(chat)
            await self.session.commit()
            await publication_rights(bot, chat, actor)
        return targets

    async def groups(self, actor, cid, expected, chats, bot, *, all_groups=False):
        await self.policy(actor, cid)
        if (
            not isinstance(chats, list)
            or (not chats and all_groups)
            or len(chats) != len(set(chats))
        ):
            raise DomainError("ap_group")
        await self.preflight(actor, cid, bot, chats)
        async with ads_transaction(self.session, cid, policy=True):
            policy = await self.checked(actor, cid, expected)
            rows = await self.schedules(cid, locked=True)
            for chat in chats:
                await self.pub.group(chat)  # Fresh approval under the current ACL lock.
            bychat = {s.chat_id: s for s in rows}
            for row in rows:
                if row.chat_id not in chats:
                    row.excluded, row.status, row.next_run_at = True, "STOPPED", None
                    row.version += 1
                    await self.pub.cancel_waiting(row, "AUDIENCE_CHANGED")
            for chat in chats:
                row = bychat.get(chat)
                if row is None:
                    row = AdSchedule(
                        campaign_id=cid,
                        chat_id=chat,
                        authorized_by=actor,
                        interval_seconds=policy.interval_seconds,
                        start_at=policy.start_at,
                        end_at=policy.end_at,
                    )
                    self.session.add(row)
                elif row.excluded:
                    row.excluded, row.status, row.next_run_at = False, "DRAFT", None
                    row.version += 1
                # Newly selected recipients remain drafts until explicit activation.
            policy.mode = "EXISTING" if all_groups else "SELECTED"
            policy.auto_activate, policy.reconcile_at = False, None
            policy.version += 1
            self.ads.audit(
                actor,
                "audience_changed",
                cid,
                chat_ids=sorted(chats),
                mode=policy.mode,
                version=policy.version,
            )
        return policy

    async def change(self, actor, cid, expected, field, value, bot):
        await self.policy(actor, cid)
        if field == "interval_seconds":
            if type(value) is not int or value not in INTERVALS:
                raise DomainError("ap_interval")
        elif field in {"start_at", "end_at"}:
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                raise DomainError("ap_time")
            if field == "start_at" and value is None:
                raise DomainError("ap_time")
        else:
            raise DomainError("ads_stale")
        await self.preflight(actor, cid, bot)
        async with ads_transaction(self.session, cid, policy=True):
            policy = await self.checked(actor, cid, expected)
            rows = await self.schedules(cid, locked=True)
            start = value if field == "start_at" else policy.start_at
            end = value if field == "end_at" else policy.end_at
            if start and end and utc(end) <= utc(start):
                raise DomainError("ap_dates")
            setattr(policy, field, value)
            policy.version += 1
            policy.authorized_by = actor
            for row in rows:
                if row.excluded:
                    continue
                if row.chat_id is not None:
                    await self.pub.group(row.chat_id)
                setattr(row, field, value)
                if row.start_at and row.end_at and utc(row.end_at) <= utc(row.start_at):
                    raise DomainError("ap_dates")
                row.version += 1
                row.authorized_by = actor
                if field == "start_at":
                    self.civil_time(row)
                if row.status == "ACTIVE":
                    anchor = utc(row.start_at)
                    row.next_run_at = slot(anchor, row.interval_seconds, now())
                    if row.end_at and utc(row.next_run_at) >= utc(row.end_at):
                        row.status, row.next_run_at = "EXPIRED", None
                await self.pub.cancel_waiting(row, "SETTINGS_CHANGED")
            self.ads.audit(
                actor,
                "settings_changed",
                cid,
                field=field,
                value=value.isoformat() if isinstance(value, datetime) else value,
                version=policy.version,
            )
        return policy

    @staticmethod
    def civil_time(row):
        if row.start_at:
            local = utc(row.start_at).astimezone(VILNIUS)
            offset = local.strftime("%z")
            row.start_date = local.date()
            row.start_time = local.strftime("%H:%M ") + offset[:-2] + ":" + offset[-2:]

    async def lifecycle(self, actor, cid, expected, action, bot):
        if action not in {"START", "PAUSE", "DELETE"}:
            raise DomainError("ads_stale")
        await self.policy(actor, cid)
        if action == "START":
            await self.preflight(actor, cid, bot)
        async with ads_transaction(self.session, cid, policy=True):
            policy = await self.checked(actor, cid, expected)
            campaign = await self.ads.get(actor, cid)
            rows = await self.schedules(cid, locked=True)
            included = [r for r in rows if not r.excluded and r.chat_id is not None]
            if action == "START":
                await self.pub.previewed(campaign)
                if not included:
                    raise DomainError("ap_group")
                sending = await self.session.scalar(
                    select(AdPublicationAttempt.id)
                    .join(AdSchedule)
                    .where(AdSchedule.campaign_id == cid, AdPublicationAttempt.status == "SENDING")
                    .limit(1)
                )
                if sending:
                    raise DomainError("ap_running")
            instant = now()
            for row in included:
                if action == "START":
                    await self.pub.group(row.chat_id)
                    row.interval_seconds = row.interval_seconds or policy.interval_seconds
                    row.start_at = row.start_at or policy.start_at or instant
                    row.end_at = row.end_at or policy.end_at
                    if row.end_at and utc(row.end_at) <= max(instant, utc(row.start_at)):
                        raise DomainError("ap_dates")
                    self.civil_time(row)
                    # Activation/resumption never sends immediately or backfills missed slots.
                    row.next_run_at = slot(row.start_at, row.interval_seconds, instant)
                    row.status, row.authorized_by = "ACTIVE", actor
                    row.activated_revision, row.activation_base = campaign.version, campaign.version
                else:
                    row.status, row.next_run_at = "PAUSED" if action == "PAUSE" else "STOPPED", None
                row.version += 1
                await self.pub.cancel_waiting(row, action)
            policy.version += 1
            policy.auto_activate, policy.reconcile_at = False, None
            if action == "DELETE":
                campaign.status, campaign.updated_by, campaign.updated_at = (
                    "ARCHIVED",
                    actor,
                    instant,
                )
                grant = await self.grants.latest(cid)
                if grant and grant.status == "ACTIVE":
                    grant.status, grant.revoked_by, grant.revoked_at = "REVOKED", actor, instant
                    grant.version += 1
                    await self.grants.revoke_links(actor, grant)
                await ClientInvitations(self.settings, self.session).revoke_locked(actor, cid)
            self.ads.audit(actor, "lifecycle", cid, operation=action, version=policy.version)
        return policy
