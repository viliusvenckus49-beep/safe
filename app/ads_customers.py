"""Content-only customer capabilities; tokens select a campaign, never grant authority."""

import hashlib
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select

from app.ads_services import AdsService
from app.errors import DomainError
from app.group_authorization import numeric_id
from app.models import (
    AdCampaign,
    AdContentDraft,
    AdCustomerDraft,
    AdCustomerGrant,
    AdCustomerLink,
    AuditEvent,
    now,
)
from app.moderation_services import utc


class CustomerAccess:
    def __init__(self, settings: Any, session: Any):
        self.ads = AdsService(settings, session)
        self.session = session

    async def latest(self, campaign_id: int) -> AdCustomerGrant | None:
        return await self.session.scalar(
            select(AdCustomerGrant)
            .where(AdCustomerGrant.campaign_id == campaign_id)
            .order_by(AdCustomerGrant.id.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )

    @staticmethod
    def stamp(grant: AdCustomerGrant | None) -> str:
        return f"{grant.id}.{grant.version}" if grant else "0.0"

    async def admin(self, actor: int, campaign_id: int) -> AdCustomerGrant | None:
        await self.ads.get(actor, campaign_id, archived=True)
        return await self.latest(campaign_id)

    async def check(self, actor: int, campaign_id: int, *, edit: bool = False) -> AdCustomerGrant:
        numeric_id(actor)
        self.ads.database_id(campaign_id)
        grant = await self.latest(campaign_id)
        campaign = await self.session.get(AdCampaign, campaign_id, populate_existing=True)
        if not grant or grant.status != "ACTIVE" or grant.customer_id != actor or not campaign:
            raise DomainError("ac_denied")
        if edit and (
            campaign.status != "DRAFT"
            or (grant.editing_until is not None and utc(grant.editing_until) <= now())
        ):
            raise DomainError("ac_readonly")
        return grant

    async def checked_admin(self, actor: int, cid: int, expected: str):
        grant = await self.admin(actor, cid)
        if self.stamp(grant) != expected:
            raise DomainError("ads_conflict")
        return grant

    async def revoke_links(self, actor: int, grant: AdCustomerGrant) -> None:
        rows = await self.session.scalars(
            select(AdCustomerLink).where(
                AdCustomerLink.grant_id == grant.id, AdCustomerLink.status == "ACTIVE"
            )
        )
        for row in rows:
            row.status, row.revoked_by, row.revoked_at = "REVOKED", actor, now()
        await self.session.flush()

    async def assign(self, actor: int, cid: int, customer: int, expected: str) -> AdCustomerGrant:
        numeric_id(customer)
        async with self.ads.write(actor):
            previous = await self.checked_admin(actor, cid, expected)
            await self.ads.get(actor, cid)  # New grants require an unarchived campaign.
            if previous and previous.status == "ACTIVE":
                previous.status, previous.revoked_by, previous.revoked_at = "REVOKED", actor, now()
                previous.version += 1
                await self.revoke_links(actor, previous)
                await self.session.flush()  # Release the partial unique key before INSERT.
            grant = AdCustomerGrant(campaign_id=cid, customer_id=customer, assigned_by=actor)
            self.session.add(grant)
            await self.session.flush()
            self.ads.audit(actor, "customer_assigned", cid, grant_id=grant.id, customer_id=customer)
        return grant

    async def revoke(self, actor: int, cid: int, expected: str) -> None:
        async with self.ads.write(actor):
            grant = await self.checked_admin(actor, cid, expected)
            if not grant or grant.status != "ACTIVE":
                raise DomainError("ac_denied")
            grant.status, grant.revoked_by, grant.revoked_at = "REVOKED", actor, now()
            grant.version += 1
            await self.revoke_links(actor, grant)
            self.ads.audit(actor, "customer_revoked", cid, grant_id=grant.id)

    async def deadline(self, actor: int, cid: int, expected: str, until: datetime | None) -> None:
        if until is not None and (not isinstance(until, datetime) or until.tzinfo is None):
            raise DomainError("ads_stale")
        async with self.ads.write(actor):
            grant = await self.checked_admin(actor, cid, expected)
            if not grant or grant.status != "ACTIVE":
                raise DomainError("ac_denied")
            grant.editing_until = utc(until) if until else None
            grant.version += 1
            self.ads.audit(
                actor,
                "customer_deadline",
                cid,
                grant_id=grant.id,
                editing_until=until.isoformat() if until else None,
            )

    async def link(self, actor: int, cid: int, expected: str, *, rotate: bool = False) -> str:
        # Only the hash persists. The raw token is returned once, never audited or stored in FSM.
        async with self.ads.write(actor):
            grant = await self.checked_admin(actor, cid, expected)
            await self.ads.get(actor, cid)
            if not grant or grant.status != "ACTIVE":
                raise DomainError("ac_denied")
            old = await self.session.scalar(
                select(AdCustomerLink).where(
                    AdCustomerLink.grant_id == grant.id, AdCustomerLink.status == "ACTIVE"
                )
            )
            if old and not rotate:
                raise DomainError("ac_once")
            await self.revoke_links(actor, grant)
            token = secrets.token_urlsafe(32)
            self.session.add(
                AdCustomerLink(
                    grant_id=grant.id,
                    token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
                    created_by=actor,
                )
            )
            grant.version += 1
            self.ads.audit(actor, "customer_link_created", cid, grant_id=grant.id, rotated=rotate)
        return token

    async def unlink(self, actor: int, cid: int, expected: str) -> None:
        async with self.ads.write(actor):
            grant = await self.checked_admin(actor, cid, expected)
            if not grant or grant.status != "ACTIVE":
                raise DomainError("ac_denied")
            await self.revoke_links(actor, grant)
            grant.version += 1
            self.ads.audit(actor, "customer_link_revoked", cid, grant_id=grant.id)

    async def resolve(self, actor: int, token: str) -> int:
        # Uniform error for malformed, unknown, revoked, foreign and archived links.
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise DomainError("ac_denied")
        row = await self.session.scalar(
            select(AdCustomerLink).where(
                AdCustomerLink.token_hash == hashlib.sha256(token.encode("ascii")).hexdigest(),
                AdCustomerLink.status == "ACTIVE",
            )
        )
        if row is None:
            raise DomainError("ac_denied")
        grant = await self.session.get(AdCustomerGrant, row.grant_id, populate_existing=True)
        if grant is None:
            raise DomainError("ac_denied")
        current = await self.check(actor, grant.campaign_id)
        campaign = await self.session.get(AdCampaign, grant.campaign_id)
        if current.id != grant.id or campaign.status != "DRAFT":
            raise DomainError("ac_denied")
        return grant.campaign_id

    async def history(self, actor: int, cid: int, page: int = 0) -> list[AuditEvent]:
        await self.admin(actor, cid)
        return list(
            await self.session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.action.in_(
                        (
                            "ads_customer_assigned",
                            "ads_customer_revoked",
                            "ads_customer_deadline",
                            "ads_customer_link_created",
                            "ads_customer_link_revoked",
                        )
                    ),
                    AuditEvent.details["campaign_id"].as_integer() == cid,
                )
                .order_by(AuditEvent.id.desc())
                .offset(max(0, min(page, 10000)) * 5)
                .limit(6)
            )
        )


class CustomerAdsService(AdsService):
    """Uses the same editor/transactions, with campaign-scoped content capabilities only."""

    customer = True

    def __init__(self, settings: Any, session: Any):
        super().__init__(settings, session)
        self.grants = CustomerAccess(settings, session)
        self.writing = False
        self.expected_grant: int | None = None

    async def authorize(self, actor: int) -> None:
        numeric_id(actor)  # Every exposed operation additionally checks its campaign grant.

    @asynccontextmanager
    async def write(self, actor: int):
        async with super().write(actor):
            self.writing = True
            try:
                yield
            finally:
                self.writing = False

    async def get(self, actor: int, campaign_id: int, *, archived: bool = False) -> AdCampaign:
        await self.grants.check(actor, campaign_id, edit=self.writing)
        campaign = await self.session.get(AdCampaign, campaign_id, populate_existing=True)
        assert campaign is not None
        return campaign

    async def draft_grant(self, actor: int, campaign_id: int) -> int:
        grant = await self.grants.check(actor, campaign_id, edit=True)
        if self.expected_grant is not None and self.expected_grant != grant.id:
            raise DomainError("ac_denied")
        return grant.id

    async def check_draft_scope(self, actor: int, draft: AdContentDraft) -> None:
        grant = await self.grants.check(actor, draft.campaign_id, edit=True)
        binding = await self.session.get(AdCustomerDraft, draft.id, populate_existing=True)
        if not binding or (binding.grant_id, binding.campaign_id, binding.customer_id) != (
            grant.id,
            draft.campaign_id,
            actor,
        ):
            raise DomainError("ac_denied")

    async def stage(self, actor, campaign_id, base, field, value, request):
        if field not in {"TEXT", "MEDIA", "BUTTONS"}:
            raise DomainError("ac_denied")
        return await super().stage(actor, campaign_id, base, field, value, request)

    async def campaigns(self, actor: int, page: int = 0) -> list[AdCampaign]:
        numeric_id(actor)
        return list(
            await self.session.scalars(
                select(AdCampaign)
                .join(AdCustomerGrant)
                .where(
                    AdCustomerGrant.customer_id == actor,
                    AdCustomerGrant.status == "ACTIVE",
                    AdCampaign.status != "ARCHIVED",
                )
                .order_by(AdCampaign.id.desc())
                .offset(max(0, min(page, 10000)) * 8)
                .limit(9)
            )
        )

    async def create(self, actor: int, name: str, request: str) -> AdCampaign:
        raise DomainError("ac_denied")

    async def archive(self, actor: int, campaign_id: int, version: int) -> None:
        raise DomainError("ac_denied")

    async def unfinished(self, actor: int) -> list[AdContentDraft]:
        # Never expose admin drafts or grants for unrelated campaigns.
        numeric_id(actor)
        return list(
            await self.session.scalars(
                select(AdContentDraft)
                .join(AdCustomerDraft, AdCustomerDraft.draft_id == AdContentDraft.id)
                .join(AdCustomerGrant, AdCustomerGrant.id == AdCustomerDraft.grant_id)
                .join(AdCampaign, AdCampaign.id == AdContentDraft.campaign_id)
                .where(
                    AdCustomerGrant.status == "ACTIVE",
                    or_(
                        AdCustomerGrant.editing_until.is_(None),
                        AdCustomerGrant.editing_until > now(),
                    ),
                    AdCustomerGrant.customer_id == actor,
                    AdContentDraft.actor_id == actor,
                    AdCampaign.status == "DRAFT",
                    AdCampaign.version == AdContentDraft.base_version,
                    AdContentDraft.status.in_(("READY", "COLLECTING")),
                    AdContentDraft.expires_at > now(),
                )
                .order_by(AdContentDraft.created_at.desc())
                .limit(8)
            )
        )
