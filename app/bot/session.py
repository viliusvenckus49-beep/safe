"""Telegram transport respects outbound proxies and system CA trust."""

import os
import ssl
from collections.abc import Callable
from typing import Any

import structlog
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from app.config import Settings


async def _request(make_request: Any, bot: Any, method: Any, *, icon_fallback: bool) -> Any:
    try:
        return await make_request(bot, method)
    except TelegramBadRequest:
        markup = getattr(method, "reply_markup", None)
        if not icon_fallback or not isinstance(markup, InlineKeyboardMarkup):
            raise
        if not any(b.icon_custom_emoji_id for row in markup.inline_keyboard for b in row):
            raise
        plain = markup.model_copy(
            update={
                "inline_keyboard": [
                    [b.model_copy(update={"icon_custom_emoji_id": None}) for b in row]
                    for row in markup.inline_keyboard
                ]
            }
        )
        structlog.get_logger().warning(
            "telegram_button_icon_retry",
            operation=type(method).__name__,
            exception_type="TelegramBadRequest",
        )
        return await make_request(bot, method.model_copy(update={"reply_markup": plain}))


def telegram_session(
    settings: Settings, *, on_poll: Callable[[], None] | None = None, icon_fallback: bool = False
) -> AiohttpSession:
    proxy = (
        settings.telegram_proxy_url.get_secret_value()
        if settings.telegram_proxy_url
        else os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    )
    session = AiohttpSession(proxy=proxy)
    # Aiogram initializes its connector with certifi only. Add system roots,
    # including an operator-provided SSL_CERT_FILE, without disabling verification.
    context = session._connector_init.get("ssl")
    if not isinstance(context, ssl.SSLContext):
        context = ssl.create_default_context()
        session._connector_init["ssl"] = context
    else:
        context.load_default_certs()

    async def telemetry(make_request: Any, bot: Any, method: Any) -> Any:
        operation = type(method).__name__
        try:
            result = await _request(make_request, bot, method, icon_fallback=icon_fallback)
        except Exception as error:
            structlog.get_logger().warning(
                "telegram_request_failed", operation=operation, exception_type=type(error).__name__
            )
            raise
        if operation == "GetUpdates":
            if on_poll is not None:
                on_poll()
            structlog.get_logger().info("poll_completed", operation="poll", updates=len(result))
        return result

    session.middleware(telemetry)
    return session
