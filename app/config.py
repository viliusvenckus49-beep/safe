from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    bot_token: SecretStr
    database_url: str = Field(default="sqlite+aiosqlite:///./safecheck.db", repr=False)
    admin_ids: str = ""
    group_owner_id: int | None = Field(default=None, gt=0, le=9223372036854775807)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    environment: Literal["development", "production"] = "development"
    redis_url: SecretStr | None = None
    telegram_proxy_url: SecretStr | None = None
    group_help_enabled: bool = False
    group_help_state_dir: str = "/run/mtproto"
    group_help_staff_id: int = Field(default=-1004300060813, lt=0, ge=-9223372036854775808)
    group_help_bot_id: int = Field(default=0, ge=0, le=9223372036854775807)
    group_help_scope_ids: str = ""
    fsm_ttl_seconds: int = 1800
    rep_cooldown_seconds: int = 60
    report_cooldown_seconds: int = 300

    @field_validator("bot_token")
    @classmethod
    def token_valid(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if ":" not in token or not token.split(":", 1)[0].isdigit() or not token.split(":", 1)[1]:
            raise ValueError("BOT_TOKEN must be a valid BotFather token")
        return value

    @field_validator("admin_ids")
    @classmethod
    def admins_valid(cls, value: str) -> str:
        if any(
            not part.strip().isascii()
            or not part.strip().isdigit()
            or not 0 < int(part.strip()) <= 9223372036854775807
            for part in value.split(",")
            if part.strip()
        ):
            raise ValueError("ADMIN_IDS must contain positive numeric Telegram IDs")
        return value

    @field_validator("database_url")
    @classmethod
    def database_valid(cls, value: str) -> str:
        if not value.startswith(("sqlite+aiosqlite:///", "postgresql+asyncpg://")):
            raise ValueError("DATABASE_URL must use sqlite+aiosqlite or postgresql+asyncpg")
        return value

    @field_validator("redis_url")
    @classmethod
    def redis_valid(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL must use redis:// or rediss://")
        return value

    @model_validator(mode="after")
    def production_valid(self):
        if (
            self.rep_cooldown_seconds < 0
            or self.report_cooldown_seconds < 0
            or self.fsm_ttl_seconds <= 0
        ):
            raise ValueError("Cooldowns must be nonnegative and FSM TTL positive")
        if self.environment == "production" and (
            not self.database_url.startswith("postgresql+asyncpg://")
            or not self.redis_url
            or not self.admins
        ):
            raise ValueError("Production requires PostgreSQL, REDIS_URL and ADMIN_IDS")
        if self.group_owner_id is not None and self.group_owner_id not in self.admins:
            raise ValueError("GROUP_OWNER_ID must be one of ADMIN_IDS")
        if self.group_help_enabled and (
            self.group_help_staff_id >= 0
            or self.group_help_bot_id <= 0
            or not self.group_help_scope
            or not self.group_help_state_dir.startswith("/")
        ):
            raise ValueError("Group Help requires verified staff, bot, scope and private session")
        return self

    @field_validator("group_help_scope_ids")
    @classmethod
    def scope_valid(cls, value: str) -> str:
        if any(
            not part.strip().startswith("-")
            or not part.strip()[1:].isascii()
            or not part.strip()[1:].isdigit()
            or not -9223372036854775808 <= int(part.strip()) < 0
            for part in value.split(",")
            if part.strip()
        ):
            raise ValueError("GROUP_HELP_SCOPE_IDS must contain negative chat IDs")
        return value

    @property
    def group_help_scope(self) -> frozenset[int]:
        return frozenset(int(x.strip()) for x in self.group_help_scope_ids.split(",") if x.strip())

    @property
    def group_owner(self) -> int | None:
        if self.group_owner_id is not None:
            return self.group_owner_id
        return next(iter(self.admins)) if len(self.admins) == 1 else None

    @property
    def admins(self) -> frozenset[int]:
        return frozenset(int(x.strip()) for x in self.admin_ids.split(",") if x.strip())
