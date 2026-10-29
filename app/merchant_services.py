"""Catalog transactions reuse existing identity/admin locks, REP and audit services."""

import re
from urllib.parse import urlsplit

from sqlalchemy import delete

from app.errors import DomainError
from app.merchant_catalog import bounded, city, grams, normalized, search
from app.merchant_repository import MerchantRepository
from app.models import (
    AuditEvent,
    MerchantEntry,
    MerchantSearchGram,
    MerchantSettings,
    now,
)


class MerchantService:
    def __init__(self, core):
        self.core, self.session = core, core.session
        self.repo = MerchantRepository(self.session)

    async def settings(self):
        return await self.session.get(
            MerchantSettings, 1, populate_existing=True
        ) or MerchantSettings(
            id=1, enabled=True, registrations=True, publish_authors=False, copy={}, version=1
        )

    async def available(self):
        if not (await self.settings()).enabled:
            raise DomainError("merchant_disabled")

    async def listing(self, *, city_code="", query="", page=0, admin=False, actor=0):
        if admin:
            await self.core.require_admin(actor)
        else:
            await self.available()
        if city_code:
            city(city_code)
        if query:
            query = search(query)
        return await self.repo.listing(city=city_code, query=query, page=page, admin=admin)

    async def public(self, entry_id):
        await self.available()
        entry = await self.repo.public(entry_id)
        if entry is None:
            raise DomainError("merchant_missing")
        return entry

    async def profile(self, entry_id):
        entry = await self.public(entry_id)
        data = await self.core.profile(str(entry.owner.telegram_id))
        await self.session.commit()
        return entry, data

    async def reviews(self, entry_id, sign="all", page=0):
        if sign not in ("all", "pos", "neg"):
            raise DomainError("merchant_invalid")
        entry = await self.public(entry_id)
        rows, total, page = await self.repo.reviews(entry.owner_user_id, sign, page)
        return entry, rows, total, page

    async def _write(self, actor, admin=False):
        if admin:
            await self.core.require_admin(actor)
        # Existing lock coordinates grants/revocations, SCAM and username ownership.
        await self.core.repo.lock_identity_metadata()
        if admin:
            await self.core.require_admin(actor)

    async def _locked_entry(self, actor, entry_id):
        await self._write(actor, True)
        return await self.editable(actor, entry_id)

    def _audit(self, actor, action, entry=None, **details):
        self.session.add(
            AuditEvent(
                actor_id=actor,
                action="merchant_" + action,
                target_id=entry.owner_user_id if entry else None,
                details={"merchant_id": entry.id if entry else None, **details},
            )
        )

    @staticmethod
    def validate_field(field, value):
        if field == "city_code":
            return city(value)
        if field == "name":
            value = bounded(value or "", 2, 64)
            if "\n" in value or len(normalized(value)) > 128:
                raise DomainError("merchant_invalid")
            return value
        if field == "description":
            return bounded(value or "", 10, 600)
        if field == "contact":
            if value in (None, "", "-"):
                return None
            value = bounded(value, 5, 200)
            if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", value):
                return value
            try:
                url = urlsplit(value)
            except ValueError:
                raise DomainError("merchant_invalid") from None
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or any(c.isspace() for c in value)
                or "\\" in value
            ):
                raise DomainError("merchant_invalid")
            try:
                if url.port not in (None, 443):
                    raise ValueError
                url.hostname.encode("idna")
            except (ValueError, UnicodeError):
                raise DomainError("merchant_invalid") from None
            return value
        raise DomainError("merchant_invalid")

    async def editable(self, actor, entry_id):
        await self.core.require_admin(actor)
        entry = await self.repo.entry(entry_id)
        if entry is None:
            raise DomainError("merchant_missing")
        return entry

    async def creation_target(self, actor, target_id):
        await self.core.require_admin(actor)
        if type(target_id) is not int or not 0 < target_id <= 9223372036854775807:
            raise DomainError("merchant_identity")
        user = await self.core.repo.user_by_telegram(target_id)
        if user is None:
            raise DomainError("merchant_identity")
        if await self.repo.mine(target_id) is not None:
            raise DomainError("merchant_duplicate")
        if await self.core.repo.has_scam_identity(user):
            raise DomainError("merchant_blocked")
        return user

    async def save(self, actor, entry_id, version, changes, *, target_id=0):
        """One serialized, version-checked write; never creates an application."""
        await self._write(actor, True)
        if not isinstance(changes, dict) or not set(changes) <= {
            "name",
            "description",
            "city_code",
            "contact",
        }:
            raise DomainError("merchant_invalid")
        if entry_id:
            entry = await self.editable(actor, entry_id)
            if entry.version != version or entry.status == "REMOVED":
                raise DomainError("merchant_stale")
        else:
            user = await self.creation_target(actor, target_id)
            entry = MerchantEntry(owner_user_id=user.id, status="DRAFT", version=1)
            self.session.add(entry)
        values = {
            field: self.validate_field(field, changes.get(field, getattr(entry, field, None)))
            for field in ("name", "city_code", "description", "contact")
        }
        if entry.owner_user_id is None:
            raise DomainError("merchant_identity")
        # Identity ownership and reputation never change when editing public contacts.
        owner = await self.core.repo.user_by_id(entry.owner_user_id)
        if owner is None or owner.telegram_id is None:
            raise DomainError("merchant_identity")
        if await self.core.repo.has_scam_identity(owner):
            raise DomainError("merchant_blocked")
        changed = [k for k, v in values.items() if getattr(entry, k, None) != v]
        for field, value in values.items():
            setattr(entry, field, value)
        entry.name_key = normalized(entry.name)
        if entry.status in ("DRAFT", "PENDING", "REJECTED"):
            entry.status = "APPROVED"
        entry.approved_at = now()
        entry.approved_by = actor
        entry.version += 1
        await self.session.flush()
        await self.session.execute(
            delete(MerchantSearchGram).where(MerchantSearchGram.entry_id == entry.id)
        )
        self.session.add_all(
            [MerchantSearchGram(entry_id=entry.id, token=g) for g in grams(entry.name_key)]
        )
        self._audit(actor, "updated" if entry_id else "created", entry, fields=changed)
        saved_id = entry.id
        await self.session.commit()
        return await self.repo.entry(saved_id)

    async def change_status(self, actor, entry_id, version, status, reason=""):
        entry = await self._locked_entry(actor, entry_id)
        if entry.version != version or status not in ("SUSPENDED", "APPROVED", "REMOVED"):
            raise DomainError("merchant_stale")
        if status == "APPROVED":
            if entry.status != "SUSPENDED" or not entry.approved_at:
                raise DomainError("merchant_stale")
            if await self.core.repo.has_scam_identity(entry.owner):
                raise DomainError("merchant_blocked")
        elif status == "SUSPENDED" and entry.status != "APPROVED":
            raise DomainError("merchant_stale")
        elif status == "REMOVED" and entry.status not in (
            "APPROVED",
            "SUSPENDED",
            "DRAFT",
            "PENDING",
            "REJECTED",
        ):
            raise DomainError("merchant_stale")
        previous = entry.status
        entry.status = status
        entry.reason = bounded(reason, 3, 600) if reason and status != "APPROVED" else None
        entry.version += 1
        self._audit(actor, "status", entry, previous=previous, status=status)
        await self.session.commit()
        return entry

    async def configure(self, actor, version, field, value, *, lang="lt"):
        await self._write(actor, True)
        row = await self.settings()
        if row.version != version:
            raise DomainError("merchant_stale")
        if row not in self.session:
            self.session.add(row)
        if field in ("enabled", "publish_authors"):
            if type(value) is not bool:
                raise DomainError("merchant_invalid")
            setattr(row, field, value)
        elif field in ("title", "intro") and lang in ("lt", "en", "ru"):
            value = bounded(value, 2, 64 if field == "title" else 300)
            copy = {k: dict(v) for k, v in row.copy.items()}
            copy.setdefault(lang, {})[field] = value
            row.copy = copy
        else:
            raise DomainError("merchant_invalid")
        row.version += 1
        self._audit(actor, "settings", field=field, language=lang)
        await self.session.commit()
        return row
