"""Validated polling startup with explicit migrations and graceful cleanup."""

import asyncio
import sys
from datetime import timedelta

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseEventIsolation, BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from aiogram.types import ErrorEvent
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import text

from app import __version__
from app.bot import create_router
from app.bot.ads_publisher import ads_worker
from app.bot.commands import commands, group_commands, user_commands
from app.bot.errors import error_response
from app.bot.group_runtime import group_worker
from app.bot.session import telegram_session
from app.config import Settings
from app.db import check_database, create_database
from app.health import RuntimeHealth
from app.i18n import LANGUAGES
from app.logging import configure_logging
from app.mtproto_relay import GroupHelpRelay, set_relay
from app.repositories import Repository


async def serve(settings: Settings, *, check_only: bool = False) -> None:
    configure_logging(settings.log_level)
    log = structlog.get_logger()
    engine, sessions = create_database(settings.database_url)
    bot: Bot | None = None
    storage: BaseStorage | None = None
    isolation: BaseEventIsolation | None = None
    worker: asyncio.Task[None] | None = None
    advertising: asyncio.Task[None] | None = None
    polling: asyncio.Task[None] | None = None
    dispatcher: Dispatcher | None = None
    health: RuntimeHealth | None = None
    relay: GroupHelpRelay | None = None
    try:
        await check_database(engine)
        expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        async with engine.connect() as connection:
            actual = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar()
        if actual != expected:
            raise RuntimeError("Database migration required")
        if settings.redis_url:
            redis_storage = RedisStorage.from_url(
                settings.redis_url.get_secret_value(),
                state_ttl=timedelta(seconds=settings.fsm_ttl_seconds),
                data_ttl=timedelta(seconds=settings.fsm_ttl_seconds),
            )
            storage = redis_storage
            await redis_storage.redis.ping()
            isolation = RedisEventIsolation(redis=redis_storage.redis)
        else:
            storage = MemoryStorage()
            isolation = SimpleEventIsolation()
        if check_only:
            log.info("startup_check_passed", operation="startup")
            return
        if settings.group_help_enabled:
            relay = await GroupHelpRelay.connect(settings, sessions)
            set_relay(relay)
        health = RuntimeHealth(advertising=True)
        bot = Bot(
            settings.bot_token.get_secret_value(),
            session=telegram_session(settings, on_poll=health.polling, icon_fallback=True),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dispatcher = Dispatcher(storage=storage, events_isolation=isolation)
        dispatcher.include_router(create_router(settings, sessions))

        @dispatcher.errors()
        async def handle_error(event: ErrorEvent) -> bool:
            # Exception strings/tracebacks may contain DSNs, tokens or private report text.
            source = event.update.callback_query or event.update.message
            actor = getattr(source, "from_user", None)
            message_context = getattr(source, "message", None) or event.update.message
            chat = getattr(message_context, "chat", None)
            log.error(
                "update_failed",
                operation="telegram_update",
                exception_type=type(event.exception).__name__,
                update_id=event.update.update_id,
                user_id=getattr(actor, "id", None),
                chat_id=getattr(chat, "id", None),
            )
            try:
                await error_response(event.update, sessions, settings)
            except Exception as error:
                log.warning("error_response_failed", exception_type=type(error).__name__)
            return True

        await bot.set_my_commands(commands("lt"))
        for lang in sorted(LANGUAGES):
            await bot.set_my_commands(commands(lang), language_code=lang)
        await group_commands(bot)
        for admin_id in settings.admins:
            async with sessions() as locale_session:
                selected_language = await Repository(locale_session).language(admin_id)
            await user_commands(bot, admin_id, selected_language or "lt", admin=True)
        # Avoid accidentally running polling while a previously installed webhook is active.
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise RuntimeError("Existing webhook must be removed before polling")
        log.info("polling_started", operation="startup", version=__version__)
        worker = asyncio.create_task(
            group_worker(bot, settings, sessions, on_progress=health.worker), name="group-worker"
        )
        advertising = asyncio.create_task(
            ads_worker(bot, settings, sessions, on_progress=health.advertising), name="ads-worker"
        )
        polling = asyncio.create_task(
            dispatcher.start_polling(bot, close_bot_session=False), name="telegram-polling"
        )
        done, _ = await asyncio.wait(
            (polling, worker, advertising), return_when=asyncio.FIRST_COMPLETED
        )
        # A background worker must never die silently while polling looks healthy.
        for task in (worker, advertising):
            if task in done:
                if not task.cancelled() and task.exception() is not None:
                    task.result()
                raise RuntimeError(f"Required task stopped: {task.get_name()}")
        polling.result()
    finally:
        primary_error = sys.exception()
        # Let aiogram join its own polling/stop-signal children before cancelling
        # its parent. Cancelling start_polling directly can orphan those children.
        for background_task in (worker, advertising):
            if background_task is not None:
                background_task.cancel()
        if polling and not polling.done() and dispatcher:
            try:
                await asyncio.wait_for(dispatcher.stop_polling(), 5)
            except (RuntimeError, TimeoutError):
                pass  # Startup may have failed before polling acquired its lock.
        tasks = [task for task in (polling, worker, advertising) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # aiogram keeps concurrently handled updates separately from polling.
        # Stop accepting updates first, then drain/cancel them before closing DB.
        updates = set(getattr(dispatcher, "_handle_update_tasks", ()))
        if updates:
            _, pending = await asyncio.wait(updates, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*updates, return_exceptions=True)
        set_relay(None)
        health_error = False
        if health:
            try:
                health.close()
            except OSError as error:
                health_error = True
                log.error("shutdown_failed", resource="health", exception_type=type(error).__name__)

        async def close_resource(name, close):
            try:
                await asyncio.wait_for(close(), 10)
            except Exception as error:
                log.error("shutdown_failed", resource=name, exception_type=type(error).__name__)
                return False
            return True

        # One failing closer must not leak every remaining resource or hide the
        # failure that triggered shutdown. Redis storage/isolation share a client.
        clean = not health_error
        for name, resource in (("relay", relay), ("isolation", isolation), ("storage", storage)):
            if resource is not None:
                clean = await close_resource(name, resource.close) and clean
        if bot:
            clean = await close_resource("bot", bot.session.close) and clean
        clean = await close_resource("database", engine.dispose) and clean
        if not clean and primary_error is None:
            raise RuntimeError("Runtime cleanup failed")


def run() -> None:
    try:
        settings = Settings()
    except ValidationError as error:
        # Never render ValidationError: its input_value can contain credentials.
        fields = ", ".join(
            sorted(
                {str(item["loc"][0]) if item["loc"] else "environment" for item in error.errors()}
            )
        )
        print(f"SAFECheck konfigūracija netinkama. Patikrinkite: {fields}.", file=sys.stderr)
        raise SystemExit(2) from None
    try:
        asyncio.run(serve(settings, check_only="--check" in sys.argv))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        structlog.get_logger().error(
            "startup_failed", operation="startup", exception_type=type(error).__name__
        )
        print(
            "SAFECheck nepaleistas. Patikrinkite konfigūraciją, migracijas ir paslaugų ryšį.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
