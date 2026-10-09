"""Run a private UI studio without a database, MTProto, or moderation workers."""

import asyncio
import sys
from time import time
from typing import Any

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand, ErrorEvent

from app.bot.session import telegram_session
from app.config import Settings
from app.logging import configure_logging
from app.test_ui.access import Access
from app.test_ui.config import UISettings
from app.test_ui.profile import Design
from app.test_ui.router import create_router
from app.test_ui.texts import text

ALLOWED_METHODS = frozenset(
    {
        "GetMe",
        "GetWebhookInfo",
        "GetUpdates",
        "SetMyCommands",
        "SendMessage",
        "SendPhoto",
        "SendDocument",
        "AnswerCallbackQuery",
    }
)


async def only_ui_requests(make_request: Any, bot: Any, method: Any) -> Any:
    if type(method).__name__ not in ALLOWED_METHODS:
        raise RuntimeError("UI studio does not permit moderation requests")
    return await make_request(bot, method)


async def serve(settings: UISettings):
    configure_logging(settings.log_level)
    design = Design(settings.state_file)
    design.save()
    access = Access(settings.state_file.with_name("access.json"), settings.admins)
    access.save()
    heartbeat = settings.state_file.parent / "heartbeat"
    heartbeat.unlink(missing_ok=True)

    def polling():
        heartbeat.write_text(str(time()))

    # Reuse the project's proxy/CA handling, without loading its environment or database.
    transport = telegram_session(
        Settings(
            bot_token=settings.bot_token,
            telegram_proxy_url=settings.telegram_proxy_url,
            _env_file=None,
        ),
        on_poll=polling,
    )
    transport.middleware(only_ui_requests)
    bot = Bot(
        settings.bot_token.get_secret_value(),
        session=transport,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage, isolation = MemoryStorage(), SimpleEventIsolation()
    dispatcher = Dispatcher(storage=storage, events_isolation=isolation)
    dispatcher.include_router(create_router(settings, design, access))

    @dispatcher.errors()
    async def errors(event: ErrorEvent):
        structlog.get_logger().error(
            "test_ui_update_failed", exception_type=type(event.exception).__name__
        )
        source = event.update.message or getattr(event.update.callback_query, "message", None)
        if source is not None and source.chat.type == "private" and access.can_edit(source.chat.id):
            try:
                await source.answer(text("error"))
            except Exception:
                pass
        return True

    try:
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise RuntimeError("Test bot has an existing webhook")
        me = await bot.get_me()
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="UI Studio"),
                BotCommand(command="ui", description="UI editor"),
                BotCommand(command="preview", description="Design preview"),
            ]
        )
        structlog.get_logger().info(
            "test_ui_started",
            username=me.username,
            moderation_enabled=False,
            database_enabled=False,
        )
        await dispatcher.start_polling(
            bot, allowed_updates=["message", "callback_query"], close_bot_session=False
        )
    finally:
        await isolation.close()
        await storage.close()
        await bot.session.close()


def run():
    try:
        if "--health" in sys.argv:
            settings = UISettings()
            heartbeat = settings.state_file.parent / "heartbeat"
            if not heartbeat.exists() or time() - heartbeat.stat().st_mtime > 180:
                raise RuntimeError("Polling heartbeat is stale")
            Design(settings.state_file)
            return
        asyncio.run(serve(UISettings()))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        # Do not display token-bearing validation errors, Telegram URLs, or edited texts.
        print("SAFECheck UI Studio startup failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()
