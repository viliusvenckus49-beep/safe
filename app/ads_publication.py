"""SQL-owned publication policies and fenced state transitions; no network under locks."""

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select

from app.ads_schedule import INTERVALS, LIVE, local_instant, parts_for, publishable, slot
from app.ads_services import AdsService
from app.errors import DomainError
from app.group_authorization import numeric_id
from app.group_services import GroupService
from app.models import (
    AdCampaign,
    AdContentDraft,
    AdContentVersion,
    AdPublicationAttempt,
    AdPublicationGate,
    AdPublicationPart,
    AdSchedule,
    ManagedGroup,
    now,
)
from app.moderation_services import utc

LEASE_SECONDS = 90
MAX_TRIES = 4


async def publication_rights(
    bot: Any, chat_id: int, actor: int | None = None, *, require_bot: bool = True
) -> None:
    numeric_id(chat_id, chat=True)
    identities = ([actor] if actor is not None else []) + ([bot.id] if require_bot else [])
    for identity in identities:
        member = await asyncio.wait_for(bot.get_chat_member(chat_id, identity), 5)
        if getattr(getattr(member, "user", None), "id", None) != identity or member.status not in {
            "creator",
            "administrator",
        }:
            raise DomainError("ap_rights")


class PublicationService:
    def __init__(self, settings: Any, session: Any):
        self.ads = AdsService(settings, session)
        self.session = session

    async def defer_all(self, seconds: int) -> None:
        until = now() + timedelta(seconds=max(1, seconds))
        gate = await self.session.get(AdPublicationGate, 1, populate_existing=True)
        if gate is None:
            self.session.add(AdPublicationGate(id=1, until_at=until))
        elif utc(gate.until_at) < until:
            gate.until_at = until

    async def cooldown(self) -> datetime | None:
        gate = await self.session.get(AdPublicationGate, 1, populate_existing=True)
        return utc(gate.until_at) if gate and utc(gate.until_at) > now() else None

    async def one(self, actor: int, cid: int) -> AdSchedule | None:
        await self.ads.get(actor, cid, archived=True)
        rows = list(
            await self.session.scalars(
                select(AdSchedule)
                .where(AdSchedule.campaign_id == cid)
                .execution_options(populate_existing=True)
                .limit(2)
            )
        )
        if len(rows) > 1:
            raise DomainError("ap_multiple")  # 3.3 will add explicit group selection.
        return rows[0] if rows else None

    async def ensure(self, actor: int, cid: int) -> AdSchedule:
        async with self.ads.write(actor):
            await self.ads.get(actor, cid)
            row = await self.one(actor, cid)
            if row is None:
                row = AdSchedule(campaign_id=cid, authorized_by=actor)
                self.session.add(row)
                await self.session.flush()
                self.ads.audit(actor, "schedule_created", cid, schedule_id=row.id)
        return row

    async def group(self, chat: int) -> ManagedGroup:
        numeric_id(chat, chat=True)
        row = await self.session.get(ManagedGroup, chat, populate_existing=True)
        if (
            not row
            or not row.approved
            or not row.enabled
            or row.chat_type not in {"group", "supergroup"}
        ):
            raise DomainError("ap_group")
        if await GroupService(self.ads.access.settings, self.session).is_removed_group(chat):
            raise DomainError("ap_group")
        return row

    async def previewed(self, campaign: AdCampaign) -> AdContentVersion:
        version = await self.session.scalar(
            select(AdContentVersion).where(
                AdContentVersion.campaign_id == campaign.id,
                AdContentVersion.revision == campaign.version,
            )
        )
        checked = await self.session.scalar(
            select(AdContentDraft.id)
            .where(
                AdContentDraft.campaign_id == campaign.id,
                AdContentDraft.saved_revision == campaign.version,
                AdContentDraft.status == "SAVED",
                AdContentDraft.previewed.is_(True),
            )
            .limit(1)
        )
        if version is None or checked is None:
            raise DomainError("ap_preview")
        publishable(version.content)
        return version

    async def cancel_waiting(self, schedule: AdSchedule, reason: str) -> None:
        rows = list(
            await self.session.scalars(
                select(AdPublicationAttempt).where(
                    AdPublicationAttempt.schedule_id == schedule.id,
                    AdPublicationAttempt.status.in_(("PENDING", "CLAIMED", "DEFERRED")),
                )
            )
        )
        for row in rows:
            await self.terminal(row, "CANCELLED", reason)

    async def change(
        self, actor: int, cid: int, expected: int, field: str, value: Any, request: str, bot: Any
    ) -> AdSchedule:
        self.ads.request(request)
        row = await self.one(actor, cid)
        if row is None:
            raise DomainError("ap_stale")
        chat = value if field == "chat_id" else row.chat_id
        if chat is not None:
            await self.group(chat)
        await self.session.commit()
        if chat is not None:
            await publication_rights(bot, chat, actor)
        payload = hashlib.sha256(json.dumps([field, value], default=str).encode()).hexdigest()
        async with self.ads.write(actor):
            await self.ads.get(actor, cid)
            row = await self.one(actor, cid)
            assert row is not None
            if row.last_request == request:
                if row.last_payload != payload or row.authorized_by != actor:
                    raise DomainError("ap_stale")
                return row
            if row.version != expected:
                raise DomainError("ap_stale")
            if field == "interval_seconds":
                if type(value) is not int or value not in INTERVALS:
                    raise DomainError("ap_interval")
                row.interval_seconds = value
            elif field == "chat_id":
                await self.group(value)
                row.chat_id = value
            elif field == "start_date":
                if type(value) is not date:
                    raise DomainError("ap_time")
                row.start_date = value
            elif field == "start_time":
                # Validate even before a date is selected.
                local_instant(row.start_date or date(2026, 1, 15), value)
                row.start_time = value
            elif field == "end_at":
                if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                    raise DomainError("ap_time")
                row.end_at = value
            else:
                raise DomainError("ap_stale")
            row.start_at = (
                local_instant(row.start_date, row.start_time)
                if row.start_date and row.start_time
                else None
            )
            if row.end_at and row.start_at and utc(row.end_at) <= utc(row.start_at):
                raise DomainError("ap_dates")
            row.status = "PAUSED" if row.status == "ACTIVE" else "DRAFT"
            row.version += 1
            row.next_run_at = None
            row.authorized_by, row.updated_at = actor, now()
            row.last_request, row.last_payload = request, payload
            await self.cancel_waiting(row, "POLICY_CHANGED")
            self.ads.audit(
                actor, "schedule_changed", cid, schedule_id=row.id, field=field, version=row.version
            )
        return row

    async def validate(self, actor: int, cid: int) -> tuple[AdSchedule, AdContentVersion]:
        campaign = await self.ads.get(actor, cid)
        row = await self.one(actor, cid)
        if not row or not row.chat_id or row.interval_seconds not in INTERVALS or not row.start_at:
            raise DomainError("ap_incomplete")
        await self.group(row.chat_id)
        if row.end_at and (utc(row.end_at) <= utc(row.start_at) or utc(row.end_at) <= now()):
            raise DomainError("ap_dates")
        return row, await self.previewed(campaign)

    async def activate(
        self, actor: int, cid: int, expected: int, revision: int, bot: Any
    ) -> AdSchedule:
        row, content = await self.validate(actor, cid)
        chat = row.chat_id
        assert chat is not None
        await self.session.commit()
        await publication_rights(bot, chat, actor)
        async with self.ads.write(actor):
            row, content = await self.validate(actor, cid)
            if row.chat_id != chat or content.revision != revision:
                raise DomainError("ap_stale")
            if (
                row.status == "ACTIVE"
                and row.activation_base == expected
                and row.activated_revision == revision
            ):
                return row
            if row.version != expected:
                raise DomainError("ap_stale")
            # An old in-flight request cannot be made safe by a second activation.
            running = await self.session.scalar(
                select(AdPublicationAttempt.id).where(
                    AdPublicationAttempt.schedule_id == row.id,
                    AdPublicationAttempt.status == "SENDING",
                )
            )
            if running:
                raise DomainError("ap_running")
            row.status = "ACTIVE"
            row.last_error, row.alert_pending = None, False
            row.activation_base, row.activated_revision = expected, revision
            row.version += 1
            row.authorized_by, row.updated_at = actor, now()
            assert row.start_at is not None and row.interval_seconds is not None
            row.next_run_at = slot(row.start_at, row.interval_seconds, now(), inclusive=True)
            if row.end_at and row.next_run_at >= utc(row.end_at):
                raise DomainError("ap_dates")
            await self.cancel_waiting(row, "REACTIVATED")
            self.ads.audit(
                actor, "schedule_activated", cid, schedule_id=row.id, version=row.version
            )
        return row

    async def control(
        self, actor: int, cid: int, expected: int, status: str, bot: Any
    ) -> AdSchedule:
        if status not in {"PAUSED", "STOPPED"}:
            raise DomainError("ap_stale")
        previous = await self.one(actor, cid)
        chat = previous.chat_id if previous else None
        await self.session.commit()
        if chat is not None:
            await publication_rights(bot, chat, actor, require_bot=False)
        async with self.ads.write(actor):
            row = await self.one(actor, cid)
            if row is None:
                raise DomainError("ap_stale")
            if row.chat_id != chat:
                raise DomainError("ap_stale")
            if row.status == status and row.version == expected + 1:
                return row
            if row.version != expected:
                raise DomainError("ap_stale")
            row.status, row.next_run_at = status, None
            row.version += 1
            row.updated_at = now()
            await self.cancel_waiting(row, status)
            self.ads.audit(actor, "schedule_" + status.lower(), cid, schedule_id=row.id)
        return row

    def event(self, row: AdPublicationAttempt, status: str, **details: Any) -> None:
        self.ads.audit(
            0,
            "publication_" + status.lower(),
            0,
            attempt_id=row.id,
            schedule_id=row.schedule_id,
            chat_id=row.chat_id,
            content_version_id=row.content_version_id,
            fence=row.fence,
            **details,
        )

    async def parts(self, aid: int) -> list[AdPublicationPart]:
        return list(
            await self.session.scalars(
                select(AdPublicationPart)
                .where(AdPublicationPart.attempt_id == aid)
                .order_by(AdPublicationPart.position)
            )
        )

    async def terminal(
        self, row: AdPublicationAttempt, status: str, reason: str | None = None
    ) -> None:
        row.status, row.error, row.completed_at = status, reason, now()
        row.lease_until, row.retry_at = None, None
        for part in await self.parts(row.id):
            if part.status not in {"SENT", "UNCERTAIN", "FAILED"}:
                part.status = (
                    "UNCERTAIN"
                    if status == "UNCERTAIN" and part.status == "SENDING"
                    else "CANCELLED"
                )
                part.error = reason
        self.event(row, status, error=reason)

    async def eligible(self, row: AdSchedule) -> bool:
        campaign = await self.session.get(AdCampaign, row.campaign_id, populate_existing=True)
        try:
            await self.ads.authorize(row.authorized_by)
            if row.chat_id is None:
                return False
            await self.group(row.chat_id)
        except DomainError:
            return False
        return bool(campaign and campaign.status == "DRAFT" and row.status == "ACTIVE")

    async def enqueue(self, limit: int = 20) -> int:
        count = 0
        # Short SQL serialization also works across independent SQLite processes.
        async with self.ads.write_system():
            instant = now()
            schedules = list(
                await self.session.scalars(
                    select(AdSchedule)
                    .where(AdSchedule.status == "ACTIVE")
                    .order_by(AdSchedule.next_run_at)
                    .limit(limit)
                )
            )
            for schedule in schedules:
                if not await self.eligible(schedule):
                    schedule.status, schedule.next_run_at = "PAUSED", None
                    schedule.last_error, schedule.alert_pending = "AUTHORITY_LOST", True
                    await self.cancel_waiting(schedule, "AUTHORITY_LOST")
                    self.ads.audit(
                        0, "schedule_disabled", schedule.campaign_id, schedule_id=schedule.id
                    )
                    continue
                if schedule.end_at and utc(schedule.end_at) <= instant:
                    schedule.status, schedule.next_run_at = "EXPIRED", None
                    await self.cancel_waiting(schedule, "EXPIRED")
                    self.ads.audit(
                        0, "schedule_expired", schedule.campaign_id, schedule_id=schedule.id
                    )
                    continue
                if not schedule.next_run_at or utc(schedule.next_run_at) > instant:
                    continue
                due = utc(schedule.next_run_at)
                following = slot(schedule.start_at, schedule.interval_seconds, instant)
                schedule.next_run_at = following
                # Grace is capped at one minute: old missed slots are skipped, not backfilled.
                if (instant - due).total_seconds() > 60:
                    self.ads.audit(
                        0,
                        "publication_skipped",
                        schedule.campaign_id,
                        schedule_id=schedule.id,
                        due=due.isoformat(),
                    )
                    continue
                exists = await self.session.scalar(
                    select(AdPublicationAttempt.id).where(
                        AdPublicationAttempt.schedule_id == schedule.id,
                        AdPublicationAttempt.due_at == due,
                    )
                )
                busy = await self.session.scalar(
                    select(AdPublicationAttempt.id).where(
                        AdPublicationAttempt.schedule_id == schedule.id,
                        AdPublicationAttempt.status.in_(LIVE),
                    )
                )
                if exists or busy:
                    continue
                campaign = await self.session.get(AdCampaign, schedule.campaign_id)
                try:
                    content = await self.previewed(campaign)
                except DomainError as error:
                    schedule.status, schedule.next_run_at = "PAUSED", None
                    schedule.last_error, schedule.alert_pending = error.code, True
                    self.ads.audit(
                        0,
                        "schedule_invalid_content",
                        schedule.campaign_id,
                        schedule_id=schedule.id,
                        error=error.code,
                    )
                    continue
                attempt = AdPublicationAttempt(
                    schedule_id=schedule.id,
                    content_version_id=content.id,
                    chat_id=schedule.chat_id,
                    schedule_version=schedule.version,
                    due_at=due,
                    status="PENDING",
                )
                self.session.add(attempt)
                await self.session.flush()
                for position, kind in enumerate(parts_for(content.content)):
                    self.session.add(
                        AdPublicationPart(
                            attempt_id=attempt.id, position=position, kind=kind, message_ids=[]
                        )
                    )
                self.event(attempt, "PENDING")
                count += 1
        return count

    async def claim(self) -> tuple[int, str, int] | None:
        async with self.ads.write_system():
            instant = now()
            expired = list(
                await self.session.scalars(
                    select(AdPublicationAttempt).where(
                        AdPublicationAttempt.status.in_(("CLAIMED", "SENDING")),
                        AdPublicationAttempt.lease_until <= instant,
                    )
                )
            )
            for row in expired:
                if row.status == "SENDING":
                    await self.terminal(row, "UNCERTAIN", "LEASE_EXPIRED_DURING_SEND")
                    schedule = await self.session.get(AdSchedule, row.schedule_id)
                    schedule.status, schedule.next_run_at = "PAUSED", None
                else:
                    row.status, row.token, row.lease_until = "PENDING", None, None
                    self.event(row, "PENDING", recovery=True)
            if await self.cooldown():
                return None
            rows = list(
                await self.session.scalars(
                    select(AdPublicationAttempt)
                    .where(
                        AdPublicationAttempt.status.in_(("PENDING", "DEFERRED")),
                        or_(
                            AdPublicationAttempt.retry_at.is_(None),
                            AdPublicationAttempt.retry_at <= instant,
                        ),
                    )
                    .order_by(AdPublicationAttempt.id)
                    .limit(100)
                )
            )
            for row in rows:
                if row.retry_at and utc(row.retry_at) > instant:
                    continue
                schedule = await self.session.get(AdSchedule, row.schedule_id)
                if (
                    not await self.eligible(schedule)
                    or row.schedule_version != schedule.version
                    or (schedule.end_at and utc(schedule.end_at) <= instant)
                ):
                    await self.terminal(row, "CANCELLED", "INELIGIBLE")
                    continue
                row.status, row.token = "CLAIMED", uuid4().hex
                row.fence += 1
                row.lease_until = instant + timedelta(seconds=LEASE_SECONDS)
                self.event(row, "CLAIMED")
                return row.id, row.token, row.fence
        return None

    async def preflight_failure(
        self, aid: int, token: str, fence: int, error: str, *, retry: int | None = None
    ) -> None:
        async with self.ads.write_system():
            row = await self.session.get(AdPublicationAttempt, aid, populate_existing=True)
            if not row or (row.token, row.fence, row.status) != (token, fence, "CLAIMED"):
                return
            part = next((p for p in await self.parts(aid) if p.status != "SENT"), None)
            if part is None:
                return
            part.tries += 1
            part.error = error
            if retry is not None and error.startswith("TelegramRetryAfter"):
                await self.defer_all(retry)
            if retry is not None and part.tries < MAX_TRIES:
                row.status, part.status, row.error = "DEFERRED", "DEFERRED", error
                row.retry_at, row.lease_until = now() + timedelta(seconds=max(1, retry)), None
                self.event(row, "DEFERRED", preflight=True, error=error)
            else:
                part.status = "FAILED"
                await self.terminal(row, "FAILED", error)
                schedule = await self.session.get(AdSchedule, row.schedule_id)
                schedule.status, schedule.next_run_at = "PAUSED", None
                schedule.last_error = error

    async def begin(self, aid: int, token: str, fence: int) -> tuple[Any, Any, Any] | None:
        async with self.ads.write_system():
            row = await self.session.get(AdPublicationAttempt, aid, populate_existing=True)
            if (
                not row
                or row.token != token
                or row.fence != fence
                or row.status != "CLAIMED"
                or not row.lease_until
                or utc(row.lease_until) <= now()
            ):
                return None
            schedule = await self.session.get(AdSchedule, row.schedule_id, populate_existing=True)
            if (
                not await self.eligible(schedule)
                or row.schedule_version != schedule.version
                or (schedule.end_at and utc(schedule.end_at) <= now())
            ):
                await self.terminal(row, "CANCELLED", "INELIGIBLE_BEFORE_SEND")
                return None
            parts = await self.parts(aid)
            gate = await self.cooldown()
            if gate:
                row.status, row.retry_at, row.lease_until = "DEFERRED", gate, None
                self.event(row, "DEFERRED", global_cooldown=True)
                return None
            part = next((p for p in parts if p.status != "SENT"), None)
            if part is None:
                await self.terminal(row, "SENT")
                return None
            if part.status not in {"PENDING", "DEFERRED"} or part.tries >= MAX_TRIES:
                await self.terminal(row, "FAILED", "RETRY_LIMIT")
                schedule.status, schedule.next_run_at = "PAUSED", None
                return None
            row.status, part.status = "SENDING", "SENDING"
            part.tries += 1
            row.lease_until = now() + timedelta(seconds=LEASE_SECONDS)
            content = await self.session.get(AdContentVersion, row.content_version_id)
            self.event(row, "SENDING", part_id=part.id, kind=part.kind, tries=part.tries)
            return row, part, content

    async def result(
        self,
        aid: int,
        token: str,
        fence: int,
        part_id: int,
        status: str,
        ids: list[int] | None = None,
        error: str | None = None,
        retry: int = 0,
    ) -> bool:
        if status not in {"SENT", "FAILED", "DEFERRED", "UNCERTAIN"}:
            raise DomainError("ap_stale")
        async with self.ads.write_system():
            row = await self.session.get(AdPublicationAttempt, aid, populate_existing=True)
            part = await self.session.get(AdPublicationPart, part_id, populate_existing=True)
            if (
                not row
                or not part
                or part.attempt_id != aid
                or row.token != token
                or row.fence != fence
                or row.status != "SENDING"
                or part.status != "SENDING"
            ):
                return False
            schedule = await self.session.get(AdSchedule, row.schedule_id)
            if status == "SENT":
                if not ids or any(type(i) is not int or i <= 0 for i in ids):
                    raise DomainError("ap_result")
                part.status, part.message_ids, part.error = "SENT", ids, None
                self.event(row, "part_sent", part_id=part.id, message_ids=ids)
                if all(p.status == "SENT" for p in await self.parts(aid)):
                    row.status, row.completed_at, row.lease_until = "SENT", now(), None
                    self.event(row, "SENT")
                else:
                    row.status = "CLAIMED"
                return row.status == "CLAIMED"
            part.status, part.error = status, error
            if status == "DEFERRED":
                await self.defer_all(retry)
            if status == "DEFERRED" and part.tries < MAX_TRIES:
                row.status, row.error = "DEFERRED", error
                row.retry_at = now() + timedelta(seconds=max(1, retry))
                row.lease_until = None
                self.event(row, "DEFERRED", part_id=part.id, retry_after=max(1, retry), error=error)
            else:
                if status == "DEFERRED":
                    part.status = "FAILED"
                await self.terminal(row, "FAILED" if status == "DEFERRED" else status, error)
                schedule.status, schedule.next_run_at = "PAUSED", None
                schedule.last_error = error
            return False

    async def history(self, actor: int, cid: int, limit: int = 10) -> list[AdPublicationAttempt]:
        row = await self.one(actor, cid)
        if row is None:
            return []
        return list(
            await self.session.scalars(
                select(AdPublicationAttempt)
                .where(AdPublicationAttempt.schedule_id == row.id)
                .order_by(AdPublicationAttempt.id.desc())
                .limit(limit)
            )
        )

    async def statistics(self, actor: int, cid: int) -> dict:
        row = await self.one(actor, cid)
        if row is None:
            return {"sent": 0, "failed": 0, "last": None}
        sent = await self.session.scalar(
            select(func.count())
            .select_from(AdPublicationAttempt)
            .where(
                AdPublicationAttempt.schedule_id == row.id, AdPublicationAttempt.status == "SENT"
            )
        )
        failed = await self.session.scalar(
            select(func.count())
            .select_from(AdPublicationAttempt)
            .where(
                AdPublicationAttempt.schedule_id == row.id, AdPublicationAttempt.status == "FAILED"
            )
        )
        uncertain = await self.session.scalar(
            select(func.count())
            .select_from(AdPublicationAttempt)
            .where(
                AdPublicationAttempt.schedule_id == row.id,
                AdPublicationAttempt.status == "UNCERTAIN",
            )
        )
        last = await self.session.scalar(
            select(func.max(AdPublicationAttempt.completed_at)).where(
                AdPublicationAttempt.schedule_id == row.id, AdPublicationAttempt.status == "SENT"
            )
        )
        return {"sent": sent, "failed": failed, "uncertain": uncertain, "last": last}
