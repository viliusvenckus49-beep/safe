"""Single-use client binding and non-secret editor selectors with fresh grant checks."""

import hashlib
import re
import secrets
from datetime import timedelta

from sqlalchemy import select

from app.ads_customers import CustomerAccess
from app.ads_services import AdsService
from app.errors import DomainError
from app.group_authorization import numeric_id
from app.models import AdClientInvitation, AdCustomerGrant, AdCustomerLink, now
from app.moderation_services import utc


class ClientInvitations:
    def __init__(self, settings, session):
        self.ads = AdsService(settings, session)
        self.grants = CustomerAccess(settings, session)
        self.session = session

    async def editor_enabled(self, grant):
        return bool(
            await self.session.scalar(
                select(AdCustomerLink.id)
                .where(AdCustomerLink.grant_id == grant.id, AdCustomerLink.status == "ACTIVE")
                .limit(1)
            )
        )

    async def enable_editor(self, actor, grant):
        if not await self.editor_enabled(grant):
            # Existing hashed-link records also serve as revocable selector capability.
            # This random marker is not exposed as another bearer credential.
            digest = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
            self.session.add(
                AdCustomerLink(
                    grant_id=grant.id, token_hash=digest, status="ACTIVE", created_by=actor
                )
            )
            await self.session.flush()

    async def revoke_locked(self, actor, cid):
        rows = await self.session.scalars(
            select(AdClientInvitation).where(
                AdClientInvitation.campaign_id == cid, AdClientInvitation.status == "ACTIVE"
            )
        )
        for row in rows:
            row.status = "REVOKED"
        await self.session.flush()

    async def revoke(self, actor, cid):
        async with self.ads.write(actor):
            await self.ads.get(actor, cid)
            await self.revoke_locked(actor, cid)
            self.ads.audit(actor, "invitation_revoked", cid)

    async def issue(self, actor, cid, expected, *, lifetime=timedelta(hours=24)):
        if not timedelta(minutes=1) <= lifetime <= timedelta(days=7):
            raise DomainError("ads_stale")
        token = secrets.token_urlsafe(32)
        async with self.ads.write(actor):
            grant = await self.grants.checked_admin(actor, cid, expected)
            await self.ads.get(actor, cid)
            if grant and grant.status == "ACTIVE":
                await self.enable_editor(actor, grant)
            await self.revoke_locked(actor, cid)
            instant = now()
            self.session.add(
                AdClientInvitation(
                    campaign_id=cid,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    expected_grant=expected,
                    expected_customer_id=grant.customer_id
                    if grant and grant.status == "ACTIVE"
                    else None,
                    created_by=actor,
                    created_at=instant,
                    expires_at=instant + lifetime,
                )
            )
            self.ads.audit(
                actor, "invitation_issued", cid, expires_at=(instant + lifetime).isoformat()
            )
        return "ai_" + token

    async def consume(self, actor, payload):
        numeric_id(actor)
        if not re.fullmatch(r"ai_[A-Za-z0-9_-]{43}", payload):
            raise DomainError("ac_link")
        digest = hashlib.sha256(payload[3:].encode()).hexdigest()
        async with self.ads.write_system():
            row = await self.session.scalar(
                select(AdClientInvitation)
                .where(AdClientInvitation.token_hash == digest)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if not row or row.status != "ACTIVE" or utc(row.expires_at) <= now():
                raise DomainError("ac_link")
            # Invitation creator must still be an authorized administrator.
            await self.ads.get(row.created_by, row.campaign_id)
            grant = await self.grants.latest(row.campaign_id)
            if self.grants.stamp(grant) != row.expected_grant:
                raise DomainError("ac_link")
            if row.expected_customer_id is not None and row.expected_customer_id != actor:
                raise DomainError("ac_denied")
            if not grant or grant.status != "ACTIVE":
                grant = AdCustomerGrant(
                    campaign_id=row.campaign_id, customer_id=actor, assigned_by=row.created_by
                )
                self.session.add(grant)
                await self.session.flush()
                self.ads.audit(
                    actor,
                    "customer_bound",
                    row.campaign_id,
                    customer_id=actor,
                    assigned_by=row.created_by,
                    grant_id=grant.id,
                )
            await self.grants.check(actor, row.campaign_id, edit=True)
            await self.enable_editor(row.created_by, grant)
            row.status, row.consumed_by, row.consumed_at, row.grant_id = (
                "USED",
                actor,
                now(),
                grant.id,
            )
            self.ads.audit(actor, "invitation_used", row.campaign_id, grant_id=grant.id)
            cid = row.campaign_id
        return cid

    async def editor(self, actor, payload):
        if not re.fullmatch(r"ae_[1-9][0-9]{0,9}_[1-9][0-9]{0,9}_[1-9][0-9]{0,9}", payload):
            raise DomainError("ac_link")
        cid, gid, version = map(int, payload[3:].split("_"))
        # This is a selector, never a credential. Revoked/replaced users are denied.
        grant = await self.grants.check(actor, cid, edit=True)
        if (grant.id, grant.version) != (gid, version) or not await self.editor_enabled(grant):
            raise DomainError("ac_link")
        return cid
