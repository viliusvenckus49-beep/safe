"""Durable, actor-bound GLOBAL ADS assignment and independent schedule operations."""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ads_locks import ads_transaction
from app.ads_publication import PublicationService, publication_rights
from app.ads_schedule import INTERVALS, VILNIUS, slot
from app.ads_services import AdsService
from app.errors import DomainError
from app.group_services import GroupService
from app.models import (
    AdAssignmentPolicy,
    AdCampaign,
    AdGlobalOperation,
    AdGlobalOperationItem,
    AdPublicationAttempt,
    AdSchedule,
    ManagedGroup,
    now,
)
from app.moderation_services import utc

MODES = {"SINGLE", "SELECTED", "EXISTING", "FUTURE"}
ACTIONS = {
    "START",
    "PAUSE",
    "STOP",
    "REMOVE",
    "DEFAULTS",
    "INHERIT",
    "CHANGE",
    "FUTURE_ON",
    "FUTURE_OFF",
}


def copy_defaults(row, policy, *, reset=False):
    overrides = set() if reset else set(row.override_fields or [])
    if "interval_seconds" not in overrides:
        row.interval_seconds = policy.interval_seconds
    if "start_at" not in overrides:
        row.start_at = policy.start_at
        if policy.start_at:
            local = utc(policy.start_at).astimezone(VILNIUS)
            clock = local.strftime("%H:%M %z")
            row.start_date, row.start_time = local.date(), clock[:-2] + ":" + clock[-2:]
        else:
            row.start_date, row.start_time = None, None
    if "end_at" not in overrides:
        row.end_at = policy.end_at
    row.override_fields = sorted(overrides)
    if row.end_at and row.start_at and utc(row.end_at) <= utc(row.start_at):
        raise DomainError("ap_dates")


class GlobalAdsService:
    def __init__(self, settings, session):
        self.settings, self.session = settings, session
        self.ads = AdsService(settings, session)

    async def policy(self, actor, cid):
        await self.ads.get(actor, cid)
        row = await self.session.get(AdAssignmentPolicy, cid, populate_existing=True)
        if row is None:
            async with self.ads.write(actor):
                row = await self.session.get(AdAssignmentPolicy, cid, populate_existing=True)
                if row is None:
                    row = AdAssignmentPolicy(campaign_id=cid, authorized_by=actor)
                    self.session.add(row)
                    await self.session.flush()
        return row

    @staticmethod
    def eligible_groups():
        return select(ManagedGroup).where(
            ManagedGroup.approved.is_(True),
            ManagedGroup.enabled.is_(True),
            ManagedGroup.chat_type.in_(("group", "supergroup")),
            ~GroupService.removed_group(ManagedGroup.chat_id),
        )

    async def groups(self, actor, *, page=0, limit=9):
        await self.ads.authorize(actor)
        return list(
            await self.session.scalars(
                self.eligible_groups()
                .order_by(ManagedGroup.chat_id)
                .offset(max(0, min(page, 10000)) * 8)
                .limit(limit)
            )
        )

    async def schedules(self, actor, cid, *, page=0, include_excluded=False):
        await self.ads.get(actor, cid, archived=True)
        return list(
            await self.session.scalars(
                select(AdSchedule)
                .where(
                    AdSchedule.campaign_id == cid,
                    AdSchedule.chat_id.is_not(None),
                    *([] if include_excluded else [AdSchedule.excluded.is_(False)]),
                )
                .order_by(AdSchedule.id)
                .offset(max(0, min(page, 10000)) * 8)
                .limit(9)
            )
        )

    async def defaults(self, actor, cid, expected, field, value):
        await self.policy(actor, cid)
        async with ads_transaction(self.session, cid, policy=True):
            await self.ads.get(actor, cid)
            row = await self.session.get(AdAssignmentPolicy, cid, populate_existing=True)
            if row.version != expected:
                raise DomainError("ap_stale")
            self.set_default(row, actor, field, value)
            self.ads.audit(actor, "global_defaults", cid, field=field, version=row.version)
        return row

    @staticmethod
    def validate_default(row, field, value):
        if field == "interval_seconds":
            if type(value) is not int or value not in INTERVALS:
                raise DomainError("ap_interval")
        elif field in {"start_at", "end_at"}:
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                raise DomainError("ap_time")
            if field == "start_at" and value is None:
                raise DomainError("ap_time")
        else:
            raise DomainError("ap_stale")
        start = value if field == "start_at" else row.start_at
        end = value if field == "end_at" else row.end_at
        if start and end and utc(end) <= utc(start):
            raise DomainError("ap_dates")

    def set_default(self, row, actor, field, value):
        self.validate_default(row, field, value)
        setattr(row, field, value)
        row.version += 1
        row.authorized_by, row.updated_at = actor, now()
        row.auto_activate = False

    async def prepare_defaults(self, actor, cid, expected, field, value, request):
        """Persist a confirmation without changing defaults or future activation consent."""
        self.ads.request(request)
        await self.policy(actor, cid)
        payload = {
            "defaults": True,
            "field": field,
            "value": value.isoformat() if isinstance(value, datetime) else value,
        }
        async with ads_transaction(self.session, cid, policy=True):
            policy = await self.policy(actor, cid)
            previous = await self.session.scalar(
                select(AdGlobalOperation).where(AdGlobalOperation.request_key == request)
            )
            if previous:
                if (previous.actor_id, previous.campaign_id, previous.action, previous.payload) != (
                    actor,
                    cid,
                    "CHANGE",
                    payload,
                ):
                    raise DomainError("ap_stale")
                return previous
            if policy.version != expected:
                raise DomainError("ap_stale")
            self.validate_default(policy, field, value)
            op = AdGlobalOperation(
                id=uuid4().hex[:16],
                campaign_id=cid,
                actor_id=actor,
                action="CHANGE",
                status="READY",
                policy_version=expected,
                payload=payload,
                request_key=request,
                expires_at=now() + timedelta(minutes=15),
            )
            self.session.add(op)
            self.ads.audit(
                actor,
                "global_prepared",
                cid,
                operation_id=op.id,
                operation_action="CHANGE",
                defaults=True,
                targets=0,
            )
        return op

    async def pending(self, actor, page=0):
        await self.ads.authorize(actor)
        return list(
            await self.session.scalars(
                select(AdGlobalOperation)
                .join(AdCampaign)
                .where(
                    AdGlobalOperation.actor_id == actor,
                    AdCampaign.status == "DRAFT",
                    AdGlobalOperation.status.in_(("SELECTING", "READY", "RUNNING")),
                    or_(
                        AdGlobalOperation.status == "RUNNING", AdGlobalOperation.expires_at > now()
                    ),
                )
                .order_by(AdGlobalOperation.created_at.desc())
                .offset(max(0, min(page, 10000)) * 8)
                .limit(9)
            )
        )

    async def operation(self, actor, oid):
        await self.ads.authorize(actor)
        if (
            not isinstance(oid, str)
            or len(oid) != 16
            or any(c not in "0123456789abcdef" for c in oid)
        ):
            raise DomainError("ap_stale")
        op = await self.session.get(AdGlobalOperation, oid, populate_existing=True)
        if op is None or op.actor_id != actor:
            raise DomainError("ap_stale")
        await self.ads.get(actor, op.campaign_id, archived=True)
        return op

    async def selection(self, actor, cid, mode, request):
        if mode not in MODES:
            raise DomainError("ap_stale")
        self.ads.request(request)
        await self.policy(actor, cid)
        async with ads_transaction(self.session, cid, policy=True):
            policy = await self.policy(actor, cid)
            previous = await self.session.scalar(
                select(AdGlobalOperation).where(AdGlobalOperation.request_key == request)
            )
            if previous:
                if (previous.actor_id, previous.campaign_id, previous.payload.get("mode")) != (
                    actor,
                    cid,
                    mode,
                ):
                    raise DomainError("ap_stale")
                return previous
            op = AdGlobalOperation(
                id=uuid4().hex[:16],
                campaign_id=cid,
                actor_id=actor,
                action="ASSIGN",
                status="SELECTING",
                policy_version=policy.version,
                payload={"mode": mode, "selected": []},
                request_key=request,
                expires_at=now() + timedelta(minutes=30),
            )
            self.session.add(op)
        return op

    async def toggle(self, actor, oid, version, chat, bot):
        op = await self.operation(actor, oid)
        await PublicationService(self.settings, self.session).group(chat)
        cid = op.campaign_id
        await self.session.commit()
        await publication_rights(bot, chat, actor)
        async with ads_transaction(self.session, cid, policy=True):
            op = await self.operation(actor, oid)
            policy = await self.policy(actor, cid)
            if (
                op.status != "SELECTING"
                or op.version != version
                or utc(op.expires_at) <= now()
                or op.policy_version != policy.version
            ):
                raise DomainError("ap_stale")
            selected = set(op.payload["selected"])
            if chat in selected:
                selected.remove(chat)
            elif op.payload["mode"] == "SINGLE":
                selected = {chat}
            else:
                selected.add(chat)
            op.payload = {**op.payload, "selected": sorted(selected)}
            op.version += 1
        return op

    async def ready_selection(self, actor, oid, version, bot):
        op = await self.operation(actor, oid)
        if op.status != "SELECTING" or op.version != version:
            raise DomainError("ap_stale")
        mode, cid = op.payload["mode"], op.campaign_id
        if mode in {"EXISTING", "FUTURE"}:
            chats = list(
                await self.session.scalars(
                    self.eligible_groups().with_only_columns(ManagedGroup.chat_id)
                )
            )
        else:
            chats = op.payload["selected"]
            if not chats or (mode == "SINGLE" and len(chats) != 1):
                raise DomainError("ap_group")
        rows = list(
            await self.session.scalars(select(AdSchedule).where(AdSchedule.campaign_id == cid))
        )
        bychat = {row.chat_id: row for row in rows if row.chat_id is not None}
        targets = []
        for chat in sorted(set(chats)):
            row = bychat.get(chat)
            if row and row.excluded and mode in {"EXISTING", "FUTURE"}:
                continue
            targets.append((chat, row.id if row else None, row.version if row else None, "ASSIGN"))
        if mode in {"SINGLE", "SELECTED"}:
            targets += [
                (r.chat_id, r.id, r.version, "REMOVE")
                for r in rows
                if r.chat_id is not None and not r.excluded and r.chat_id not in chats
            ]
        membership = [
            [r.id, r.version, r.chat_id, r.excluded] for r in sorted(rows, key=lambda r: r.id)
        ]
        return await self._ready(actor, op, targets, bot, membership=membership)

    async def prepare(
        self, actor, cid, action, request, bot, *, sid=None, payload=None, expected_version=None
    ):
        if action not in ACTIONS:
            raise DomainError("ap_stale")
        self.ads.request(request)
        policy = await self.policy(actor, cid)
        previous = await self.session.scalar(
            select(AdGlobalOperation).where(AdGlobalOperation.request_key == request)
        )
        if previous:
            previous_payload = {k: v for k, v in previous.payload.items() if k != "membership"}
            if (previous.actor_id, previous.campaign_id, previous.action, previous_payload) != (
                actor,
                cid,
                action,
                {**(payload or {}), **({"single": sid} if sid is not None else {})},
            ):
                raise DomainError("ap_stale")
            return previous
        rows = list(
            await self.session.scalars(
                select(AdSchedule).where(
                    AdSchedule.campaign_id == cid,
                    AdSchedule.excluded.is_(False),
                    AdSchedule.chat_id.is_not(None),
                    *([AdSchedule.id == sid] if sid is not None else []),
                )
            )
        )
        if sid is not None and (
            not rows or (expected_version is not None and rows[0].version != expected_version)
        ):
            raise DomainError("ap_stale")
        op = AdGlobalOperation(
            id=uuid4().hex[:16],
            campaign_id=cid,
            actor_id=actor,
            action=action,
            status="SELECTING",
            policy_version=policy.version,
            payload={**(payload or {}), **({"single": sid} if sid is not None else {})},
            request_key=request,
            expires_at=now() + timedelta(minutes=15),
        )
        if sid is None and not action.startswith("FUTURE_"):
            all_rows = list(
                await self.session.scalars(
                    select(AdSchedule).where(AdSchedule.campaign_id == cid).order_by(AdSchedule.id)
                )
            )
            op.payload = {
                **op.payload,
                "membership": [[r.id, r.version, r.chat_id, r.excluded] for r in all_rows],
            }
        if action in {"START", "FUTURE_ON"}:
            campaign = await self.ads.get(actor, cid)
            op.content_revision = (
                await PublicationService(self.settings, self.session).previewed(campaign)
            ).revision
        targets = (
            []
            if action.startswith("FUTURE_")
            else [(r.chat_id, r.id, r.version, action) for r in rows]
        )
        await self.session.commit()
        return await self._ready(actor, op, targets, bot, new=True)

    async def _ready(self, actor, op, targets, bot, *, new=False, membership=None):
        # Live preflight outside SQL; confirmation reports real runnable/denied counts.
        cid, expected_policy = op.campaign_id, op.policy_version
        selection_version = op.version
        await self.session.commit()
        semaphore = asyncio.Semaphore(8)

        async def check(target):
            chat, sid, version, action = target
            async with semaphore:
                try:
                    await publication_rights(
                        bot, chat, actor, require_bot=action not in {"PAUSE", "STOP", "REMOVE"}
                    )
                    return (*target, None)
                except Exception as error:
                    from app.bot.ads_publisher import diagnostic

                    return (*target, diagnostic(error))

        checked = await asyncio.gather(*(check(t) for t in targets))
        async with ads_transaction(self.session, cid, policy=True):
            policy = await self.policy(actor, cid)
            if expected_policy != policy.version:
                raise DomainError("ap_stale")
            if new:
                previous = await self.session.scalar(
                    select(AdGlobalOperation).where(AdGlobalOperation.request_key == op.request_key)
                )
                if previous:
                    if (
                        previous.actor_id,
                        previous.campaign_id,
                        previous.action,
                        {k: v for k, v in previous.payload.items() if k != "membership"},
                    ) != (
                        op.actor_id,
                        op.campaign_id,
                        op.action,
                        {k: v for k, v in op.payload.items() if k != "membership"},
                    ):
                        raise DomainError("ap_stale")
                    return previous
                if utc(op.expires_at) <= now():
                    raise DomainError("ap_stale")
                self.session.add(op)
            else:
                current = await self.operation(actor, op.id)
                if (
                    current.status != "SELECTING"
                    or current.version != selection_version
                    or utc(current.expires_at) <= now()
                ):
                    raise DomainError("ap_stale")
                op = current
            if membership is not None:
                op.payload = {**op.payload, "membership": membership}
            await self.session.flush()
            for chat, sid, version, action, error in checked:
                self.session.add(
                    AdGlobalOperationItem(
                        operation_id=op.id,
                        chat_id=chat,
                        schedule_id=sid,
                        expected_version=version,
                        action=action,
                        status="FAILED" if error else "PENDING",
                        error=error,
                    )
                )
            op.status = "READY"
            self.ads.audit(
                actor,
                "global_prepared",
                cid,
                operation_id=op.id,
                operation_action=op.action,
                targets=len(targets),
            )
        return op

    async def confirm(self, actor, oid, version):
        op = await self.operation(actor, oid)
        cid = op.campaign_id
        async with ads_transaction(self.session, cid, policy=True):
            op = await self.operation(actor, oid)
            policy = await self.policy(actor, cid)
            if op.status in {"RUNNING", "DONE"}:
                return op
            if (
                op.status != "READY"
                or op.version != version
                or utc(op.expires_at) <= now()
                or op.policy_version != policy.version
            ):
                raise DomainError("ap_stale")
            campaign = await self.ads.get(actor, cid)
            if "membership" in op.payload:
                current_rows = list(
                    await self.session.scalars(
                        select(AdSchedule)
                        .where(AdSchedule.campaign_id == cid)
                        .order_by(AdSchedule.id)
                    )
                )
                if op.payload["membership"] != [
                    [r.id, r.version, r.chat_id, r.excluded] for r in current_rows
                ]:
                    raise DomainError("ap_stale")
            if op.content_revision is not None and campaign.version != op.content_revision:
                raise DomainError("ap_stale")
            if op.action == "ASSIGN":
                policy.mode = op.payload["mode"]
                policy.auto_activate = False
            elif op.action == "CHANGE" and op.payload.get("defaults"):
                field, value = op.payload["field"], op.payload["value"]
                if field in {"start_at", "end_at"} and value is not None:
                    value = datetime.fromisoformat(value)
                self.set_default(policy, actor, field, value)
                op.policy_version = policy.version
                self.ads.audit(actor, "global_defaults", cid, field=field, version=policy.version)
            elif op.action == "FUTURE_OFF" or (
                op.action in {"PAUSE", "STOP"} and not op.payload.get("single")
            ):
                policy.auto_activate = False
            elif op.action == "FUTURE_ON":
                if (
                    policy.mode != "FUTURE"
                    or not policy.start_at
                    or (policy.end_at and utc(policy.end_at) <= now())
                ):
                    raise DomainError("ap_incomplete")
                await PublicationService(self.settings, self.session).previewed(campaign)
                policy.auto_activate = True
            if op.action in {"ASSIGN", "FUTURE_ON", "FUTURE_OFF"} or (
                op.action in {"PAUSE", "STOP"} and not op.payload.get("single")
            ):
                policy.version += 1
                policy.authorized_by, policy.updated_at = actor, now()
                op.policy_version = policy.version
            op.status = (
                "DONE"
                if (op.action.startswith("FUTURE_") or op.payload.get("defaults"))
                else "RUNNING"
            )
            self.ads.audit(
                actor, "global_confirmed", cid, operation_id=op.id, operation_action=op.action
            )
        return op

    async def cancel(self, actor, oid):
        op = await self.operation(actor, oid)
        async with ads_transaction(self.session, op.campaign_id, policy=True):
            op = await self.operation(actor, oid)
            if op.status not in {"SELECTING", "READY"}:
                raise DomainError("ap_stale")
            op.status = "CANCELLED"
            self.ads.audit(actor, "global_cancelled", op.campaign_id, operation_id=op.id)

    async def progress(self, actor, oid):
        op = await self.operation(actor, oid)
        counts = dict(
            (
                await self.session.execute(
                    select(AdGlobalOperationItem.status, func.count())
                    .where(AdGlobalOperationItem.operation_id == oid)
                    .group_by(AdGlobalOperationItem.status)
                )
            ).all()
        )
        return op, counts

    async def _claim_item(self, oid, chat):
        op = await self.session.get(AdGlobalOperation, oid)
        if not op:
            return None
        async with ads_transaction(self.session, op.campaign_id, policy=True):
            op = await self.session.get(AdGlobalOperation, oid, populate_existing=True)
            item = await self.session.get(
                AdGlobalOperationItem, (oid, chat), populate_existing=True
            )
            if not item or op.status != "RUNNING" or item.status not in {"PENDING", "CLAIMED"}:
                return None
            if item.status == "CLAIMED" and utc(item.lease_until) > now():
                return None
            policy = await self.session.get(AdAssignmentPolicy, op.campaign_id)
            try:
                await self.ads.get(op.actor_id, op.campaign_id)
                if policy.version != op.policy_version:
                    raise DomainError("ap_stale")
            except DomainError as error:
                item.status, item.error, item.lease_until = "FAILED", error.code, None
                self.ads.audit(
                    0,
                    "global_item_denied",
                    op.campaign_id,
                    operation_id=oid,
                    chat_id=chat,
                    error=error.code,
                )
                return None
            item.status, item.token, item.lease_until = (
                "CLAIMED",
                uuid4().hex,
                now() + timedelta(seconds=90),
            )
            return item.token

    async def _apply(self, oid, chat, token, bot):
        op = await self.session.get(AdGlobalOperation, oid)
        item = await self.session.get(AdGlobalOperationItem, (oid, chat))
        actor, cid = op.actor_id, op.campaign_id
        sid, expected, action = item.schedule_id, item.expected_version, item.action
        await self.session.commit()
        pub = PublicationService(self.settings, self.session, sid, operation=(oid, chat, token))
        if action == "START":
            await pub.activate(actor, cid, expected, op.content_revision, bot)
            return
        if action in {"PAUSE", "STOP"}:
            await pub.control(
                actor, cid, expected, "PAUSED" if action == "PAUSE" else "STOPPED", bot
            )
            return
        if action == "CHANGE":
            from datetime import date, datetime

            payload = op.payload
            value = payload["value"]
            if payload["field"] == "start_date":
                value = date.fromisoformat(value)
            elif payload["field"] == "end_at" and value is not None:
                value = datetime.fromisoformat(value)
            await pub.change(
                actor,
                cid,
                expected,
                payload["field"],
                value,
                "global:" + oid + ":" + str(chat),
                bot,
            )
            return
        await publication_rights(bot, chat, actor, require_bot=action != "REMOVE")
        if action == "ASSIGN":
            # Assignment creation is an atomic membership + item-result transaction.
            async with ads_transaction(self.session, cid, policy=True):
                policy = await self.policy(actor, cid)
                op = await self.operation(actor, oid)
                item = await self.session.get(
                    AdGlobalOperationItem, (oid, chat), populate_existing=True
                )
                if (
                    op.status != "RUNNING"
                    or op.policy_version != policy.version
                    or item.token != token
                    or item.status != "CLAIMED"
                    or utc(item.lease_until) <= now()
                ):
                    raise DomainError("ap_stale")
                await pub.group(chat)
                row = await self.session.scalar(
                    select(AdSchedule)
                    .where(AdSchedule.campaign_id == cid, AdSchedule.chat_id == chat)
                    .with_for_update()
                )
                if (row is None) != (sid is None) or (
                    row and (row.id != sid or row.version != expected)
                ):
                    raise DomainError("ap_stale")
                if row is None:
                    row = AdSchedule(
                        campaign_id=cid,
                        chat_id=chat,
                        authorized_by=actor,
                        override_fields=[],
                        excluded=False,
                    )
                    copy_defaults(row, policy)
                    self.session.add(row)
                    await self.session.flush()
                elif row.excluded:
                    row.excluded, row.status, row.next_run_at = False, "DRAFT", None
                    row.version += 1
                item.schedule_id, item.status, item.lease_until = row.id, "SUCCEEDED", None
                self.ads.audit(
                    actor,
                    "global_assigned",
                    cid,
                    operation_id=oid,
                    chat_id=chat,
                    schedule_id=row.id,
                )
            return
        async with pub.write(actor, cid):
            row = await pub.one(actor, cid)
            if row is None or row.chat_id != chat or row.version != expected:
                raise DomainError("ap_stale")
            policy = await self.policy(actor, cid)
            if action != "REMOVE":
                await pub.group(chat)
            if action == "REMOVE":
                row.excluded = True
                row.status = "STOPPED"
            elif action in {"DEFAULTS", "INHERIT"}:
                copy_defaults(row, policy, reset=action == "INHERIT")
                row.status = "PAUSED" if row.status == "ACTIVE" else "DRAFT"
            else:
                raise DomainError("ap_stale")
            row.version += 1
            row.next_run_at, row.authorized_by, row.updated_at = None, actor, now()
            await pub.cancel_waiting(row, action)
            self.ads.audit(
                actor,
                "global_" + action.lower(),
                cid,
                operation_id=oid,
                chat_id=chat,
                schedule_id=row.id,
            )

    async def run_item(self, oid, chat, bot):
        token = await self._claim_item(oid, chat)
        if token is None:
            return
        try:
            await self._apply(oid, chat, token, bot)
        except Exception as error:
            from app.bot.ads_publisher import diagnostic

            await self.session.rollback()
            op = await self.session.get(AdGlobalOperation, oid)
            async with ads_transaction(self.session, op.campaign_id, policy=True):
                item = await self.session.get(
                    AdGlobalOperationItem, (oid, chat), populate_existing=True
                )
                if item.status == "CLAIMED" and item.token == token:
                    item.status, item.error, item.lease_until = "FAILED", diagnostic(error), None
                    self.ads.audit(
                        op.actor_id,
                        "global_item_failed",
                        op.campaign_id,
                        operation_id=oid,
                        chat_id=chat,
                        error=item.error,
                    )

    async def run(self, oid, bot, *, limit=16):
        op = await self.session.get(AdGlobalOperation, oid)
        if not op or op.status != "RUNNING":
            return
        chats = list(
            await self.session.scalars(
                select(AdGlobalOperationItem.chat_id)
                .where(
                    AdGlobalOperationItem.operation_id == oid,
                    or_(
                        AdGlobalOperationItem.status == "PENDING",
                        (AdGlobalOperationItem.status == "CLAIMED")
                        & (AdGlobalOperationItem.lease_until <= now()),
                    ),
                )
                .order_by(AdGlobalOperationItem.chat_id)
                .limit(limit)
            )
        )
        await self.session.commit()
        factory = async_sessionmaker(self.session.bind, expire_on_commit=False)
        semaphore = asyncio.Semaphore(8)

        async def execute(chat):
            async with semaphore, factory() as session:
                await GlobalAdsService(self.settings, session).run_item(oid, chat, bot)

        await asyncio.gather(*(execute(chat) for chat in chats))
        async with ads_transaction(self.session, op.campaign_id, policy=True):
            remaining = await self.session.scalar(
                select(func.count())
                .select_from(AdGlobalOperationItem)
                .where(
                    AdGlobalOperationItem.operation_id == oid,
                    AdGlobalOperationItem.status.in_(("PENDING", "CLAIMED")),
                )
            )
            op = await self.session.get(AdGlobalOperation, oid, populate_existing=True)
            if not remaining and op.status == "RUNNING":
                op.status = "DONE"
                self.ads.audit(op.actor_id, "global_done", op.campaign_id, operation_id=oid)

    async def reconcile(self, bot, *, groups_per_policy=32):
        ids = list(
            await self.session.scalars(
                select(AdAssignmentPolicy.campaign_id)
                .where(
                    AdAssignmentPolicy.mode == "FUTURE",
                    or_(
                        AdAssignmentPolicy.reconcile_at.is_(None),
                        AdAssignmentPolicy.reconcile_at <= now(),
                    ),
                )
                .order_by(AdAssignmentPolicy.reconcile_at.asc().nulls_first())
                .limit(4)
            )
        )
        await self.session.commit()
        for cid in ids:
            async with ads_transaction(
                self.session, cid, policy=True, skip_locked=True
            ) as acquired:
                if not acquired:
                    continue
                policy = await self.session.get(AdAssignmentPolicy, cid, populate_existing=True)
                if policy.mode != "FUTURE" or (
                    policy.reconcile_at and utc(policy.reconcile_at) > now()
                ):
                    continue
                try:
                    await self.ads.get(policy.authorized_by, cid)
                except DomainError:
                    policy.auto_activate = False
                    policy.reconcile_at = now() + timedelta(minutes=5)
                    continue
                if policy.end_at and utc(policy.end_at) <= now():
                    policy.auto_activate = False
                    policy.reconcile_at = now() + timedelta(minutes=5)
                    continue
                stmt = self.eligible_groups()
                if policy.scan_after is not None:
                    stmt = stmt.where(ManagedGroup.chat_id > policy.scan_after)
                groups = list(
                    await self.session.scalars(
                        stmt.order_by(ManagedGroup.chat_id).limit(groups_per_policy)
                    )
                )
                chats = [g.chat_id for g in groups]
                actor, version = policy.authorized_by, policy.version
                policy.scan_after = chats[-1] if len(chats) == groups_per_policy else None
                policy.reconcile_at = now() + timedelta(
                    seconds=1 if policy.scan_after is not None else 30
                )
            for chat in chats:
                # Existing rows, including exclusions, are authoritative and never reset.
                exists = await self.session.scalar(
                    select(AdSchedule.id).where(
                        AdSchedule.campaign_id == cid, AdSchedule.chat_id == chat
                    )
                )
                await self.session.commit()
                if exists:
                    continue
                try:
                    await publication_rights(bot, chat, actor)
                    async with ads_transaction(self.session, cid, policy=True):
                        policy = await self.policy(actor, cid)
                        if (
                            policy.version != version
                            or policy.mode != "FUTURE"
                            or (policy.end_at and utc(policy.end_at) <= now())
                        ):
                            continue
                        pub = PublicationService(self.settings, self.session)
                        await pub.group(chat)
                        if await self.session.scalar(
                            select(AdSchedule.id).where(
                                AdSchedule.campaign_id == cid, AdSchedule.chat_id == chat
                            )
                        ):
                            continue
                        row = AdSchedule(
                            campaign_id=cid,
                            chat_id=chat,
                            authorized_by=actor,
                            override_fields=[],
                            excluded=False,
                        )
                        copy_defaults(row, policy)
                        if policy.auto_activate and row.start_at:
                            await pub.previewed(await self.ads.get(actor, cid))
                            following = slot(
                                row.start_at, policy.interval_seconds, now(), inclusive=True
                            )
                            if not row.end_at or following < utc(row.end_at):
                                row.status, row.next_run_at = "ACTIVE", following
                        self.session.add(row)
                        await self.session.flush()
                        self.ads.audit(
                            actor,
                            "future_assigned",
                            cid,
                            chat_id=chat,
                            schedule_id=row.id,
                            active=row.status == "ACTIVE",
                            policy_version=version,
                        )
                except Exception as error:
                    from app.bot.ads_publisher import diagnostic

                    await self.session.rollback()
                    async with ads_transaction(self.session, cid, policy=True):
                        policy = await self.session.get(AdAssignmentPolicy, cid)
                        policy.last_error = str(chat) + ": " + diagnostic(error)

    async def statistics(self, actor, cid=None):
        await self.ads.authorize(actor)
        if cid is not None:
            await self.ads.get(actor, cid, archived=True)
        schedule_filter = [AdSchedule.campaign_id == cid] if cid is not None else []
        live = (
            (AdCampaign.status == "DRAFT")
            & (AdSchedule.status == "ACTIVE")
            & or_(AdSchedule.end_at.is_(None), AdSchedule.end_at > now())
        )
        next_live = live & or_(
            AdSchedule.end_at.is_(None), AdSchedule.next_run_at < AdSchedule.end_at
        )
        rows = (
            await self.session.execute(
                select(
                    func.count(func.distinct(AdSchedule.chat_id)),
                    func.count(),
                    func.count(func.distinct(case((live, AdSchedule.chat_id)))),
                    func.count(
                        func.distinct(
                            case(
                                (
                                    (AdSchedule.status == "PAUSED")
                                    & (AdCampaign.status == "DRAFT"),
                                    AdSchedule.chat_id,
                                )
                            )
                        )
                    ),
                    func.min(case((next_live, AdSchedule.next_run_at))),
                )
                .select_from(AdSchedule)
                .join(AdCampaign, AdCampaign.id == AdSchedule.campaign_id)
                .where(
                    AdSchedule.excluded.is_(False),
                    AdSchedule.chat_id.is_not(None),
                    *schedule_filter,
                )
            )
        ).one()
        outcomes = (
            await self.session.execute(
                select(
                    AdPublicationAttempt.status,
                    func.count(),
                    func.max(AdPublicationAttempt.completed_at),
                )
                .join(AdSchedule, AdPublicationAttempt.schedule_id == AdSchedule.id)
                .where(*schedule_filter)
                .group_by(AdPublicationAttempt.status)
            )
        ).all()
        partial = await self.session.scalar(
            select(func.count())
            .select_from(AdPublicationAttempt)
            .join(AdSchedule, AdPublicationAttempt.schedule_id == AdSchedule.id)
            .where(*schedule_filter, PublicationService.partial_outcome())
        )
        campaign_count = await self.session.scalar(
            select(func.count())
            .select_from(AdCampaign)
            .where(
                AdCampaign.status == "DRAFT", *([AdCampaign.id == cid] if cid is not None else [])
            )
        )
        return {
            "campaigns": campaign_count,
            "groups": rows[0],
            "assignments": rows[1],
            "active": rows[2] or 0,
            "paused": rows[3] or 0,
            "next": rows[4],
            "sent": sum(n for s, n, _ in outcomes if s == "SENT"),
            "failed": sum(n for s, n, _ in outcomes if s == "FAILED"),
            "uncertain": sum(n for s, n, _ in outcomes if s == "UNCERTAIN"),
            "partial": partial,
            "last": max((utc(d) for s, _, d in outcomes if s == "SENT" and d), default=None),
        }
