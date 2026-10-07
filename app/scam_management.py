"""Explicit, audited administrator corrections; never merge reputation or reports."""

import re

from app.errors import DomainError
from app.models import ScamRecord, UsernameHistory
from app.services import Service


class ScamManagement:
    def __init__(self, core: Service):
        self.core = core

    async def detail(self, actor: int, record_id: int) -> ScamRecord:
        await self.core.require_admin(actor)
        record = await self.core.repo.scam_by_id(record_id)
        if record is None or record.status != "ACTIVE":
            raise DomainError("stale_callback")
        return record

    @staticmethod
    def validate(field: str, value: str) -> str:
        value = value.strip()
        if field == "id" and re.fullmatch(r"[1-9][0-9]{0,18}", value):
            if int(value) <= 9223372036854775807:
                return value
        if field == "username" and re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{4,31}", value):
            return value.removeprefix("@").lower()
        raise DomainError("invalid_target")

    async def supplement(self, actor: int, record_id: int, field: str, value: str, nonce: str):
        value = self.validate(field, value)
        if not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise DomainError("stale_callback")
        core = self.core
        await core.require_admin(actor)
        try:
            await core.repo.lock_identity_metadata()
            await core.require_admin(actor)
            previous = await core.repo.scam_identity_action(nonce)
            if previous:
                if previous.actor_id != actor or any(
                    previous.details[k] != v
                    for k, v in {"record_id": record_id, "field": field, "value": value}.items()
                ):
                    raise DomainError("stale_callback")
                await core.session.commit()
                return await self.detail(actor, record_id)
            record = await self.detail(actor, record_id)
            user = record.target
            before = {
                "target_id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "snapshot": record.username_snapshot,
            }
            if field == "id":
                if user.telegram_id is not None:
                    raise DomainError("sm_conflict")
                if user.username:
                    current_owner = await core.repo.user_by_username(user.username)
                    if (
                        current_owner is not None
                        and current_owner.telegram_id is not None
                        and current_owner.telegram_id != int(value)
                    ):
                        raise DomainError("sm_conflict")
                existing = await core.repo.user_by_telegram(int(value))
                if existing:
                    if await core.repo.active_scam(existing.id):
                        raise DomainError("duplicate_scam")
                    # Transfer only this confirmed record, not historical votes/reports.
                    if existing.username and user.username and existing.username != user.username:
                        raise DomainError("sm_conflict")
                    if not existing.username:
                        existing.username = user.username
                    record.target_id = existing.id
                    record.target = existing
                else:
                    user.telegram_id = int(value)
            else:
                if user.username:
                    raise DomainError("sm_conflict")
                existing = await core.repo.user_by_username(value)
                if existing and existing.id != user.id:
                    raise DomainError("sm_conflict")
                user.username = value
                core.session.add(UsernameHistory(user_id=user.id, username=value))
                record.username_snapshot = value
            core._audit(
                actor,
                "scam_identity_supplemented",
                record.target_id,
                record_id=record.id,
                field=field,
                value=value,
                nonce=nonce,
                before=before,
            )
            await core.session.flush()
            from app.group_services import enqueue_scam_bans

            await enqueue_scam_bans(core.session, record)
            await core.session.commit()
            return await self.detail(actor, record_id)
        except Exception:
            await core.session.rollback()
            raise
