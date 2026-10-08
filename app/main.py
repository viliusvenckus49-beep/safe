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
from app.bot.commands import commands, user_commands
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
        health = RuntimeHealth()
        bot = Bot(
            settings.bot_token.get_secret_value(),
            session=telegram_session(settings, on_poll=health.polling),
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
        await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        if health:
            health.close()
        if worker:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        set_relay(None)
        if relay:
            await relay.close()
        if isolation:
            await isolation.close()
        if storage:
            await storage.close()
        if bot:
            await bot.session.close()
        await engine.dispose()


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
