import ssl

import pytest

from app.bot.session import telegram_session
from app.config import Settings


def test_transport_uses_inherited_proxy_and_keeps_tls_verification(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    settings = Settings(bot_token="123456:TEST_ONLY", _env_file=None)
    session = telegram_session(settings)
    context = session._connector_init["ssl"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert session._proxy == "http://127.0.0.1:8080"


def test_transport_explicit_proxy_is_secret_and_overrides_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    settings = Settings(
        bot_token="123456:TEST_ONLY",
        telegram_proxy_url="http://user:SECRET_PROXY_PASSWORD@127.0.0.1:8081",
        _env_file=None,
    )
    assert "SECRET_PROXY_PASSWORD" not in repr(settings)
    assert telegram_session(settings)._proxy == settings.telegram_proxy_url.get_secret_value()


@pytest.mark.asyncio
async def test_transport_can_close_without_connection(settings):
    await telegram_session(settings).close()
