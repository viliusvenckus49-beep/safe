"""Durable ADS worker. Never retry an ambiguous Telegram write."""

import asyncio
from html import escape
from typing import Any

import structlog
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InputMediaPhoto, InputMediaVideo, Message, MessageEntity
from sqlalchemy import select

from app.ads_publication import PublicationService, publication_rights
from app.ads_schedule import publishable
from app.bot.ads import preview_keyboard
from app.errors import DomainError, safe_telegram_reason
from app.i18n import t, use_language
from app.models import AdPublicationAttempt, AdSchedule
from app.repositories import Repository

API_TIMEOUT = 20
log = structlog.get_logger()


class UnconfirmedDelivery(RuntimeError):
    def __init__(self, message_ids: list[int]):
        super().__init__("Incomplete Telegram delivery response")
        self.message_ids = message_ids


def diagnostic(error: Exception) -> str:
    if isinstance(error, DomainError):
        return error.code
    if isinstance(error, TelegramAPIError):
        return type(error).__name__ + ": " + safe_telegram_reason(error.message)
    return type(error).__name__


async def send_part(bot: Any, attempt: Any, part: Any, version: Any) -> list[int]:
    content = publishable(version.content)
    markup = preview_keyboard(content, "", public_attempt=attempt.id)
    entities = [MessageEntity.model_validate(e) for e in content["entities"]]
    args = {"chat_id": attempt.chat_id, "parse_mode": None}
    if part.kind == "TEXT":
        result = await bot.send_message(
            **args, text=content["text"], entities=entities, reply_markup=markup
        )
    elif part.kind == "BUTTONS":
        result = await bot.send_message(**args, text="↳", reply_markup=markup)
    elif part.kind in {"PHOTO", "VIDEO"}:
        media = content["media"][0]
        args.update(caption=content["text"] or None, caption_entities=entities, reply_markup=markup)
        result = await (
            bot.send_photo(**args, photo=media["file_id"])
            if part.kind == "PHOTO"
            else bot.send_video(**args, video=media["file_id"])
        )
    else:
        album = []
        for index, media in enumerate(content["media"]):
            cls = InputMediaPhoto if media["kind"] == "photo" else InputMediaVideo
            album.append(
                cls(
                    media=media["file_id"],
                    caption=(content["text"] or None) if index == 0 else None,
                    caption_entities=entities if index == 0 else None,
                    parse_mode=None,
                )
            )
        result = await bot.send_media_group(chat_id=attempt.chat_id, media=album)
    messages = result if isinstance(result, list) else [result]
    confirmed = list(
        dict.fromkeys(
            m.message_id
            for m in messages
            if isinstance(m, Message) and m.chat.id == attempt.chat_id and m.message_id > 0
        )
    )
    expected = len(content["media"]) if part.kind == "ALBUM" else 1
    if len(messages) != expected or any(
        not isinstance(m, Message) or m.chat.id != attempt.chat_id or m.message_id <= 0
        for m in messages
    ):
        raise UnconfirmedDelivery(confirmed)
    if len({m.message_id for m in messages}) != expected:
        raise UnconfirmedDelivery(confirmed)
    return [m.message_id for m in messages]


class AdsPublisher:
    def __init__(self, bot: Any, settings: Any, sessions: Any):
        self.bot, self.settings, self.sessions = bot, settings, sessions
        self.background = False
        self.inflight: set[asyncio.Task] = set()

    async def execute(self, claim: tuple[int, str, int]) -> None:
        aid, token, fence = claim
        while True:
            # Rights reads are outside SQL locks and are safe to retry before a write.
            async with self.sessions() as session:
                row = await session.get(AdPublicationAttempt, aid)
                schedule = await session.get(AdSchedule, row.schedule_id)
                chat, actor = row.chat_id, schedule.authorized_by
            try:
                await publication_rights(self.bot, chat, actor)
            except Exception as error:
                delay = (
                    error.retry_after
                    if isinstance(error, TelegramRetryAfter)
                    else (
                        15
                        if isinstance(
                            error,
                            (TimeoutError, OSError, TelegramNetworkError, TelegramServerError),
                        )
                        else None
                    )
                )
                async with self.sessions() as session:
                    await PublicationService(self.settings, session).preflight_failure(
                        aid, token, fence, diagnostic(error), retry=delay
                    )
                await self.notify(aid)
                return
            async with self.sessions() as session:
                prepared = await PublicationService(self.settings, session).begin(aid, token, fence)
            if prepared is None:
                return
            row, part, version = prepared
            status, ids, reason, delay = "SENT", None, None, 0
            try:
                ids = await asyncio.wait_for(send_part(self.bot, row, part, version), API_TIMEOUT)
            except TelegramRetryAfter as error:
                status, delay = "DEFERRED", error.retry_after
                reason = "TelegramRetryAfter: " + safe_telegram_reason(error.message)
            except (TelegramBadRequest, TelegramForbiddenError, DomainError) as error:
                status, reason = "FAILED", type(error).__name__
                if hasattr(error, "message"):
                    reason += ": " + safe_telegram_reason(error.message)
            except UnconfirmedDelivery as error:
                status, reason, ids = "UNCERTAIN", type(error).__name__, error.message_ids
            except Exception as error:
                # Includes timeouts, connection loss, 5xx and malformed success replies.
                status, reason = "UNCERTAIN", type(error).__name__
            async with self.sessions() as session:
                more = await PublicationService(self.settings, session).result(
                    aid, token, fence, part.id, status, ids, reason, delay
                )
            log.info(
                "ads_publication_result",
                attempt_id=aid,
                chat_id=chat,
                part_id=part.id,
                status=t("ap." + status),
                error=reason,
            )
            if not more:
                await self.notify(aid)
                return

    async def notify(self, aid: int) -> None:
        async with self.sessions() as session:
            service = PublicationService(self.settings, session)
            async with service.ads.write_system():
                row = await session.get(AdPublicationAttempt, aid, populate_existing=True)
                if row is None or row.status not in {"FAILED", "UNCERTAIN"} or row.notified:
                    return
                row.notified = True  # At most one attempt; no notification spam on restart.
                schedule = await session.get(AdSchedule, row.schedule_id)
                actor, chat, status, error = (
                    schedule.authorized_by,
                    row.chat_id,
                    row.status,
                    row.error,
                )
                if not await service.ads.access.is_admin(actor):
                    actor = self.settings.group_owner
                if actor is None:
                    return
                lang = await Repository(session).language(actor)
        try:
            with use_language(lang):
                await asyncio.wait_for(
                    self.bot.send_message(
                        actor,
                        t(
                            "ap.notification",
                            id=aid,
                            chat=chat,
                            status=status,
                            error=escape(error or ""),
                        ),
                        parse_mode="HTML",
                    ),
                    10,
                )
        except Exception as error:
            log.warning(
                "ads_notification_failed", attempt_id=aid, exception_type=type(error).__name__
            )

    async def notify_policy(self, sid: int) -> None:
        async with self.sessions() as session:
            service = PublicationService(self.settings, session)
            async with service.ads.write_system():
                row = await session.get(AdSchedule, sid, populate_existing=True)
                if not row or not row.alert_pending:
                    return
                row.alert_pending = False
                actor = row.authorized_by
                if not await service.ads.access.is_admin(actor):
                    actor = self.settings.group_owner
                if actor is None:
                    return
                chat, error = row.chat_id, row.last_error
                lang = await Repository(session).language(actor)
        try:
            with use_language(lang):
                await asyncio.wait_for(
                    self.bot.send_message(
                        actor,
                        t("ap.policy_notification", id=sid, chat=chat, error=escape(error or "")),
                        parse_mode="HTML",
                    ),
                    10,
                )
        except Exception as error:
            log.warning(
                "ads_policy_notification_failed",
                schedule_id=sid,
                exception_type=type(error).__name__,
            )

    async def tick(self) -> None:
        if self.background:
            await self.pool_tick()
            return
        async with self.sessions() as session:
            await PublicationService(self.settings, session).enqueue()
        for _ in range(20):
            async with self.sessions() as session:
                claim = await PublicationService(self.settings, session).claim()
            if claim is None:
                break
            await self.execute(claim)
        async with self.sessions() as session:
            ids = list(
                await session.scalars(
                    select(AdPublicationAttempt.id)
                    .where(
                        AdPublicationAttempt.status.in_(("FAILED", "UNCERTAIN")),
                        AdPublicationAttempt.notified.is_(False),
                    )
                    .limit(10)
                )
            )
        for aid in ids:
            await self.notify(aid)
        async with self.sessions() as session:
            schedules = list(
                await session.scalars(
                    select(AdSchedule.id).where(AdSchedule.alert_pending.is_(True)).limit(10)
                )
            )
        for sid in schedules:
            await self.notify_policy(sid)

    async def pool_tick(self) -> None:
        finished = {task for task in self.inflight if task.done()}
        for task in finished:
            self.inflight.remove(task)
            try:
                task.result()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.error(
                    "ads_execution_failed",
                    operation=task.get_name(),
                    exception_type=type(error).__name__,
                )
        while len(self.inflight) < self.settings.ads_worker_concurrency:
            async with self.sessions() as session:
                claim = await PublicationService(self.settings, session).claim()
            if claim is None:
                break
            self.inflight.add(
                asyncio.create_task(self.execute(claim), name=f"ads-publication-{claim[0]}")
            )
        if self.inflight:
            await asyncio.wait(self.inflight, timeout=0.1, return_when=asyncio.FIRST_COMPLETED)

    async def planning(self) -> None:
        while True:
            try:
                async with self.sessions() as session:
                    await PublicationService(self.settings, session).enqueue(limit=128)
            except Exception as error:
                log.error("ads_planning_failed", exception_type=type(error).__name__)
            await asyncio.sleep(0.5)

    async def coordination(self) -> None:
        from app.ads_global import GlobalAdsService
        from app.models import AdGlobalOperation

        while True:
            try:
                async with self.sessions() as session:
                    operations = list(
                        await session.scalars(
                            select(AdGlobalOperation.id)
                            .where(AdGlobalOperation.status == "RUNNING")
                            .order_by(AdGlobalOperation.created_at)
                            .limit(4)
                        )
                    )
                for oid in operations:
                    async with self.sessions() as session:
                        await GlobalAdsService(self.settings, session).run(oid, self.bot)
                async with self.sessions() as session:
                    await GlobalAdsService(self.settings, session).reconcile(
                        self.bot, groups_per_policy=8
                    )
                async with self.sessions() as session:
                    attempts = list(
                        await session.scalars(
                            select(AdPublicationAttempt.id)
                            .where(
                                AdPublicationAttempt.status.in_(("FAILED", "UNCERTAIN")),
                                AdPublicationAttempt.notified.is_(False),
                            )
                            .limit(10)
                        )
                    )
                    policies = list(
                        await session.scalars(
                            select(AdSchedule.id)
                            .where(AdSchedule.alert_pending.is_(True))
                            .limit(10)
                        )
                    )
                for aid in attempts:
                    await self.notify(aid)
                for sid in policies:
                    await self.notify_policy(sid)
            except Exception as error:
                log.error("ads_coordination_failed", exception_type=type(error).__name__)
            await asyncio.sleep(1)


async def ads_worker(bot: Any, settings: Any, sessions: Any) -> None:
    publisher = AdsPublisher(bot, settings, sessions)
    # Complete the first planning cycle before dispatching; a separate loop keeps
    # scheduling responsive while a Telegram call in one pool slot is slow.
    async with sessions() as session:
        await PublicationService(settings, session).enqueue(limit=128)
    publisher.background = True
    planning = asyncio.create_task(publisher.planning())
    coordination = asyncio.create_task(publisher.coordination())
    try:
        while True:
            try:
                await publisher.tick()
            except Exception as error:
                log.error("ads_worker_failed", exception_type=type(error).__name__)
            await asyncio.sleep(0.1)
    finally:
        tasks = [planning, coordination, *publisher.inflight]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
