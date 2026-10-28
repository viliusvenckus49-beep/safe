"""Catalog transactions reuse existing identity/admin locks, REP and audit services."""

import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select

from app.errors import DomainError
from app.merchant_catalog import DRAFT_TTL, bounded, city, grams, normalized, search, utc
from app.merchant_repository import MerchantRepository
from app.models import (
    AuditEvent,
    MerchantEntry,
    MerchantProposal,
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

    async def _owned(self, actor, entry_id, admin=False):
        await self._write(actor, admin)
        entry = await self.repo.entry(entry_id)
        if entry is None:
            raise DomainError("merchant_missing")
        if not admin and entry.owner.telegram_id != actor:
            raise DomainError("forbidden")
        return entry

    def _audit(self, actor, action, entry=None, **details):
        self.session.add(
            AuditEvent(
                actor_id=actor,
                action="merchant_" + action,
                target_id=entry.owner_user_id if entry else None,
                details={"merchant_id": entry.id if entry else None, **details},
            )
        )

    async def begin(self, actor, kind="CREATE", *, entry_id=0, target_id=None, consent=False):
        admin = target_id is not None
        if kind not in ("CREATE", "EDIT", "REMOVE") or (kind == "CREATE" and not consent):
            raise DomainError("merchant_invalid")
        await self._write(actor, admin)
        settings = await self.settings()
        if not admin and (not settings.enabled or not settings.registrations) and kind != "REMOVE":
            raise DomainError("merchant_disabled")
        if entry_id:
            entry = await self.repo.entry(entry_id)
            if entry is None:
                raise DomainError("merchant_missing")
            if not admin and entry.owner.telegram_id != actor:
                raise DomainError("forbidden")
        else:
            user = await self.core.repo.user_by_telegram(target_id or actor)
            if user is None or user.telegram_id is None:
                raise DomainError("merchant_identity")
            entry = await self.repo.mine(user.telegram_id)
            if entry is None:
                entry = MerchantEntry(owner_user_id=user.id, status="DRAFT", version=1)
                self.session.add(entry)
                await self.session.flush()
                entry = await self.repo.entry(entry.id)
        if kind != "REMOVE" and await self.core.repo.has_scam_identity(entry.owner):
            raise DomainError("merchant_blocked")
        if kind == "CREATE" and entry.status not in ("DRAFT", "REJECTED", "REMOVED"):
            raise DomainError("merchant_duplicate")
        if kind in ("EDIT", "REMOVE") and entry.status not in ("APPROVED", "SUSPENDED"):
            raise DomainError("merchant_stale")
        old = await self.repo.open_proposal(entry.id)
        if old:
            if old.status == "DRAFT" and utc(old.expires_at) < now():
                old.status = "CANCELLED"
                old.version += 1
                self._audit(actor, "draft_expired", entry, proposal_id=old.id)
                await self.session.flush()
            elif old.status == "DRAFT" and old.kind == kind:
                await self.session.commit()
                return old
            else:
                raise DomainError("merchant_duplicate")
        if not admin:
            count = await self.session.scalar(
                select(func.count())
                .select_from(MerchantProposal)
                .where(
                    MerchantProposal.actor_id == actor,
                    MerchantProposal.created_at >= now() - timedelta(days=1),
                )
            )
            if count >= 3:
                raise DomainError("merchant_rate")
        proposal = MerchantProposal(
            entry_id=entry.id,
            actor_id=actor,
            kind=kind,
            status="DRAFT",
            name=entry.name if kind == "EDIT" else None,
            city_code=entry.city_code if kind == "EDIT" else None,
            description=entry.description if kind == "EDIT" else None,
            base_version=entry.version,
            version=1,
            request_key=secrets.token_hex(16),
            expires_at=now() + timedelta(seconds=DRAFT_TTL),
        )
        self.session.add(proposal)
        await self.session.flush()
        self._audit(actor, "draft_created", entry, proposal_id=proposal.id, kind=kind)
        await self.session.commit()
        return proposal

    async def proposal(self, actor, proposal_id, *, admin=False, lock=False):
        if admin:
            await self.core.require_admin(actor)
        proposal = await self.session.get(MerchantProposal, proposal_id, populate_existing=True)
        if proposal is None:
            raise DomainError("merchant_missing")
        entry = await (
            self._owned(actor, proposal.entry_id, admin)
            if lock
            else self.repo.entry(proposal.entry_id)
        )
        if not admin and entry.owner.telegram_id != actor:
            raise DomainError("forbidden")
        if lock:
            await self.session.refresh(proposal)
        return proposal, entry

    @staticmethod
    def validate_fields(name, city_code, description):
        name = bounded(name or "", 2, 64)
        if "\n" in name or len(normalized(name)) > 128:
            raise DomainError("merchant_invalid")
        return name, city(city_code or ""), bounded(description or "", 10, 600)

    async def update_draft(self, actor, proposal_id, field, value, version, *, admin=False):
        proposal, entry = await self.proposal(actor, proposal_id, admin=admin, lock=True)
        if proposal.status != "DRAFT" or proposal.version != version:
            raise DomainError("merchant_stale")
        if utc(proposal.expires_at) < now():
            raise DomainError("merchant_expired")
        if field == "city_code":
            value = city(value)
        elif field == "name":
            value = bounded(value, 2, 64)
            if "\n" in value or len(normalized(value)) > 128:
                raise DomainError("merchant_invalid")
        elif field == "description":
            value = bounded(value, 10, 600)
        else:
            raise DomainError("merchant_invalid")
        setattr(proposal, field, value)
        proposal.version += 1
        self._audit(actor, "draft_updated", entry, proposal_id=proposal.id, field=field)
        await self.session.commit()
        return proposal

    async def submit(self, actor, proposal_id, version, *, admin=False):
        proposal, entry = await self.proposal(actor, proposal_id, admin=admin, lock=True)
        if (
            proposal.status != "DRAFT"
            or proposal.version != version
            or entry.version != proposal.base_version
        ):
            raise DomainError("merchant_stale")
        if utc(proposal.expires_at) < now():
            raise DomainError("merchant_expired")
        settings = await self.settings()
        if proposal.kind != "REMOVE":
            if not admin and (not settings.enabled or not settings.registrations):
                raise DomainError("merchant_disabled")
            if await self.core.repo.has_scam_identity(entry.owner):
                raise DomainError("merchant_blocked")
            self.validate_fields(proposal.name, proposal.city_code, proposal.description)
        proposal.status = "PENDING"
        proposal.version += 1
        if proposal.kind == "CREATE":
            entry.status = "PENDING"
        self._audit(actor, "submitted", entry, proposal_id=proposal.id, kind=proposal.kind)
        await self.session.commit()
        return proposal

    async def cancel(self, actor, proposal_id, version):
        proposal, entry = await self.proposal(actor, proposal_id, lock=True)
        if proposal.status not in ("DRAFT", "PENDING") or proposal.version != version:
            raise DomainError("merchant_stale")
        proposal.status = "CANCELLED"
        proposal.version += 1
        if proposal.kind == "CREATE":
            entry.status = "REMOVED"
            entry.version += 1
        self._audit(actor, "cancelled", entry, proposal_id=proposal.id)
        await self.session.commit()

    async def moderate(self, actor, proposal_id, version, approve, reason=""):
        proposal, entry = await self.proposal(actor, proposal_id, admin=True, lock=True)
        final = "APPROVED" if approve else "REJECTED"
        if proposal.status == final:
            await self.session.commit()
            return proposal
        if proposal.status != "PENDING" or proposal.version != version:
            raise DomainError("merchant_stale")
        if not approve:
            reason = bounded(reason, 3, 600)
        if approve:
            if entry.version != proposal.base_version:
                raise DomainError("merchant_stale")
            if proposal.kind == "REMOVE":
                entry.status = "REMOVED"
            else:
                if await self.core.repo.has_scam_identity(entry.owner):
                    raise DomainError("merchant_blocked")
                name, code, description = self.validate_fields(
                    proposal.name, proposal.city_code, proposal.description
                )
                entry.name = name
                entry.name_key = normalized(name)
                entry.city_code = code
                entry.description = description
                if proposal.kind == "CREATE":
                    entry.status = "APPROVED"
                entry.approved_at = now()
                entry.approved_by = actor
                entry.reason = None
                await self.session.execute(
                    delete(MerchantSearchGram).where(MerchantSearchGram.entry_id == entry.id)
                )
                self.session.add_all(
                    [MerchantSearchGram(entry_id=entry.id, token=g) for g in grams(entry.name_key)]
                )
            entry.version += 1
        elif proposal.kind == "CREATE":
            entry.status = "REJECTED"
            entry.version += 1
        proposal.status = final
        proposal.reason = reason or None
        proposal.moderated_by = actor
        proposal.moderated_at = now()
        proposal.version += 1
        self._audit(actor, final.lower(), entry, proposal_id=proposal.id, kind=proposal.kind)
        await self.session.commit()
        return proposal

    async def change_status(self, actor, entry_id, version, status, reason=""):
        entry = await self._owned(actor, entry_id, admin=True)
        if entry.version != version or status not in ("SUSPENDED", "APPROVED", "REMOVED"):
            raise DomainError("merchant_stale")
        if status == "APPROVED":
            if entry.status != "SUSPENDED" or not entry.approved_at:
                raise DomainError("merchant_stale")
            if await self.core.repo.has_scam_identity(entry.owner):
                raise DomainError("merchant_blocked")
        elif status == "SUSPENDED" and entry.status != "APPROVED":
            raise DomainError("merchant_stale")
        elif status == "REMOVED" and entry.status not in ("APPROVED", "SUSPENDED"):
            raise DomainError("merchant_stale")
        previous = entry.status
        entry.status = status
        entry.reason = bounded(reason, 3, 600) if status != "APPROVED" else None
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
        if field in ("enabled", "registrations", "publish_authors"):
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

    async def pending(self, actor, page=0):
        await self.core.require_admin(actor)
        total = int(
            await self.session.scalar(
                select(func.count())
                .select_from(MerchantProposal)
                .where(MerchantProposal.status == "PENDING")
            )
            or 0
        )
        page = min(max(0, page), max(0, (total - 1) // 6))
        rows = list(
            await self.session.scalars(
                select(MerchantProposal)
                .where(MerchantProposal.status == "PENDING")
                .order_by(MerchantProposal.created_at, MerchantProposal.id)
                .limit(6)
                .offset(page * 6)
            )
        )
        return rows, total, page

    async def history(self, actor, entry_id=0, page=0):
        await self.core.require_admin(actor)
        conditions = [AuditEvent.action.startswith("merchant_")]
        if entry_id:
            conditions.append(AuditEvent.details["merchant_id"].as_integer() == entry_id)
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(AuditEvent).where(*conditions)
            )
            or 0
        )
        page = min(max(0, page), max(0, (total - 1) // 5))
        rows = list(
            await self.session.scalars(
                select(AuditEvent)
                .where(*conditions)
                .order_by(AuditEvent.id.desc())
                .limit(5)
                .offset(page * 5)
            )
        )
        return rows, total, page
