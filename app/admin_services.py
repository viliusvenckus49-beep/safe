"""Owner-controlled administrator access; no cached grants or username authorization."""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import DomainError
from app.models import Administrator, AdministratorChange, AuditEvent, User
from app.repositories import Repository


class AdminService:
    PAGE_SIZE = 8

    def __init__(self, settings: Settings, session: AsyncSession):
        self.settings = settings
        self.session = session
        self.repo = Repository(session)

    def is_owner(self, actor: int) -> bool:
        return self.settings.group_owner is not None and actor == self.settings.group_owner

    def require_owner(self, actor: int) -> None:
        if not self.is_owner(actor):
            raise DomainError("forbidden")

    async def is_admin(self, actor: int) -> bool:
        if self.is_owner(actor):
            return True
        row = await self.repo.administrator(actor)
        return row.active if row else actor in self.settings.admins

    async def require_admin(self, actor: int) -> None:
        if not await self.is_admin(actor):
            raise DomainError("forbidden")

    @staticmethod
    def target_id(value: str) -> int:
        if not re.fullmatch(r"[0-9]{1,19}", value) or not 0 < int(value) <= 9223372036854775807:
            raise DomainError("admin_id_invalid")
        return int(value)

    async def identity(self, actor: int, target: str) -> tuple[int, User | None]:
        self.require_owner(actor)
        tg_id = self.target_id(target.strip())
        if self.is_owner(tg_id):
            raise DomainError("admin_owner_immutable")
        return tg_id, await self.repo.user_by_telegram(tg_id)

    async def admins(self, actor: int, page: int = 0) -> tuple[list[tuple[int, User | None]], int]:
        self.require_owner(actor)
        rows = {row.telegram_id: row.active for row in await self.repo.administrator_rows()}
        candidates = self.settings.admins | frozenset(rows)
        ids = sorted(
            i for i in candidates if self.is_owner(i) or rows.get(i, i in self.settings.admins)
        )
        page = min(max(page, 0), max(0, (len(ids) - 1) // self.PAGE_SIZE))
        users = [
            (i, await self.repo.user_by_telegram(i))
            for i in ids[page * self.PAGE_SIZE : (page + 1) * self.PAGE_SIZE]
        ]
        return users, len(ids)

    async def change(self, actor: int, target: str, active: bool, request_key: str) -> bool:
        self.require_owner(actor)
        tg_id, _ = await self.identity(actor, target)
        if not re.fullmatch(r"[a-f0-9]{32}", request_key):
            raise DomainError("stale_callback")
        try:
            # Serializes first grants, revocations and retries on both databases.
            await self.repo.lock_identity_metadata()
            previous = await self.repo.administrator_change(request_key)
            if previous:
                if (previous.actor_id, previous.telegram_id, previous.active) != (
                    actor,
                    tg_id,
                    active,
                ):
                    raise DomainError("stale_callback")
                await self.session.commit()
                return False
            row = await self.repo.administrator(tg_id)
            current = row.active if row else tg_id in self.settings.admins
            changed = current != active
            if row is None:
                row = Administrator(telegram_id=tg_id, active=active, assigned_by=actor)
                self.session.add(row)
                await self.session.flush()
            else:
                row.active = active
                row.assigned_by = actor
            self.session.add(
                AdministratorChange(
                    telegram_id=tg_id, actor_id=actor, active=active, request_key=request_key
                )
            )
            if changed:
                self.session.add(
                    AuditEvent(
                        actor_id=actor,
                        action="administrator_added" if active else "administrator_removed",
                        details={"telegram_id": tg_id, "request_key": request_key},
                    )
                )
            await self.session.commit()
            return changed
        except Exception:
            await self.session.rollback()
            raise
