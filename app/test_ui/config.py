from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class UISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TEST_UI_", env_file=None, extra="ignore")
    bot_token: SecretStr
    admin_ids: str
    state_file: Path = Path("/state/design.json")
    log_level: str = "INFO"
    telegram_proxy_url: SecretStr | None = None

    @field_validator("bot_token")
    @classmethod
    def valid_token(cls, value: SecretStr) -> SecretStr:
        head, separator, tail = value.get_secret_value().partition(":")
        if not separator or not head.isascii() or not head.isdigit() or not tail.strip():
            raise ValueError("Invalid test bot token")
        return value

    @field_validator("admin_ids")
    @classmethod
    def valid_admins(cls, value: str) -> str:
        entries = [part.strip() for part in value.split(",")]
        if not entries or any(
            not p.isascii() or not p.isdigit() or not 0 < int(p) <= 9223372036854775807
            for p in entries
        ):
            raise ValueError("Explicit numeric test administrator IDs required")
        return value

    @property
    def admins(self) -> frozenset[int]:
        return frozenset(int(part.strip()) for part in self.admin_ids.split(","))
