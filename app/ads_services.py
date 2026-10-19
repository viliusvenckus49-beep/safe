"""Admin-only draft campaigns and immutable content revisions, independent of publication."""

import hashlib
import json
import re
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_services import AdminService
from app.ads_content import EMPTY, validate_content
from app.errors import DomainError
from app.group_authorization import numeric_id
from app.models import AdCampaign, AdContentDraft, AdContentVersion, AuditEvent, now
from app.moderation_services import utc
from app.repositories import Repository


class AdsService:
    def __init__(self, settings: Any, session: AsyncSession):
        self.session = session
        self.access = AdminService(settings, session)
        self.repo = Repository(session)

    async def authorize(self, actor: int) -> None:
        numeric_id(actor)
        await self.access.require_admin(actor)

    @asynccontextmanager
    async def write(self, actor: int):
        try:
            # Same lock as owner ACL revocation: no stale cached authorization at commit.
            await self.repo.lock_identity_metadata()
            await self.authorize(actor)
            yield
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    @asynccontextmanager
    async def write_system(self):
        """Serialize short ADS worker transitions with ACL changes; never hold for API I/O."""
        try:
            await self.repo.lock_identity_metadata()
            yield
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise

    def audit(self, actor: int, action: str, campaign: int, **details: Any) -> None:
        self.session.add(
            AuditEvent(
                actor_id=actor, action="ads_" + action, details={"campaign_id": campaign, **details}
            )
        )

    @staticmethod
    def request(value: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9:._-]{1,128}", value):
            raise DomainError("ads_stale")

    @staticmethod
    def database_id(value: int) -> None:
        # Campaign/revision PKs are PostgreSQL INTEGER, not Telegram BIGINT IDs.
        numeric_id(value)
        if value > 2**31 - 1:
            raise DomainError("ads_stale")

    async def get(self, actor: int, campaign_id: int, *, archived: bool = False) -> AdCampaign:
        await self.authorize(actor)
        self.database_id(campaign_id)
        row = await self.session.get(AdCampaign, campaign_id, populate_existing=True)
        if row is None or (not archived and row.status != "DRAFT"):
            raise DomainError("ads_missing")
        return row

    async def current(self, actor: int, campaign_id: int) -> tuple[AdCampaign, AdContentVersion]:
        campaign = await self.get(actor, campaign_id)
        content = await self.session.scalar(
            select(AdContentVersion).where(
                AdContentVersion.campaign_id == campaign.id,
                AdContentVersion.revision == campaign.version,
            )
        )
        if content is None:
            raise DomainError("ads_missing")
        return campaign, content

    async def campaigns(self, actor: int, page: int = 0) -> list[AdCampaign]:
        await self.authorize(actor)
        return list(
            await self.session.scalars(
                select(AdCampaign)
                .where(AdCampaign.status == "DRAFT")
                .order_by(AdCampaign.id.desc())
                .offset(max(0, min(page, 10000)) * 8)
                .limit(9)
            )
        )

    async def create(self, actor: int, name: str, request: str) -> AdCampaign:
        self.request(request)
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 128
            or re.search(r"[\x00-\x1f\x7f]", name)
        ):
            raise DomainError("ads_name")
        name = name.strip()
        async with self.write(actor):
            previous = await self.session.scalar(
                select(AdCampaign).where(AdCampaign.request_key == request)
            )
            if previous:
                if (previous.created_by, previous.internal_name) != (actor, name):
                    raise DomainError("ads_stale")
                return previous
            campaign = AdCampaign(
                internal_name=name, created_by=actor, updated_by=actor, request_key=request
            )
            self.session.add(campaign)
            await self.session.flush()
            self.session.add(
                AdContentVersion(
                    campaign_id=campaign.id,
                    revision=1,
                    content=deepcopy(EMPTY),
                    actor_id=actor,
                    request_key="create:" + str(campaign.id),
                )
            )
            self.audit(actor, "created", campaign.id, revision=1)
        return campaign

    async def stage(
        self, actor: int, campaign_id: int, base: int, field: str, value: Any, request: str
    ) -> AdContentDraft:
        self.request(request)
        if (
            type(base) is not int
            or base < 1
            or field not in {"TEXT", "MEDIA", "BUTTONS", "RESTORE"}
        ):
            raise DomainError("ads_stale")
        try:
            fingerprint = hashlib.sha256(
                json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
        except (ValueError, TypeError, UnicodeError) as error:
            raise DomainError("ads_content") from error
        async with self.write(actor):
            campaign, version = await self.current(actor, campaign_id)
            previous = await self.session.scalar(
                select(AdContentDraft).where(AdContentDraft.request_key == request)
            )
            if previous:
                if (
                    previous.actor_id,
                    previous.campaign_id,
                    previous.base_version,
                    previous.field,
                    previous.request_fingerprint,
                ) != (actor, campaign_id, base, field, fingerprint):
                    raise DomainError("ads_stale")
                return previous
            if campaign.version != base:
                raise DomainError("ads_conflict")
            content = deepcopy(version.content)
            if field == "TEXT":
                if not isinstance(value, dict) or set(value) != {"text", "entities"}:
                    raise DomainError("ads_content")
                content.update(value)
            elif field == "MEDIA":
                content["media"] = [] if value is None else value
            elif field == "BUTTONS":
                content["buttons"] = value
            else:
                self.database_id(value)
                original = await self.session.get(AdContentVersion, value)
                if original is None or original.campaign_id != campaign_id:
                    raise DomainError("ads_missing")
                content = deepcopy(original.content)
            content = validate_content(content)
            draft = AdContentDraft(
                id=uuid4().hex,
                campaign_id=campaign_id,
                actor_id=actor,
                base_version=base,
                field=field,
                content=content,
                inputs=[],
                status="COLLECTING" if field == "MEDIA" and value is None else "READY",
                request_key=request,
                request_fingerprint=fingerprint,
                expires_at=now() + timedelta(hours=1),
            )
            self.session.add(draft)
            self.audit(
                actor,
                "draft_created",
                campaign.id,
                draft_id=draft.id,
                field=field,
                base_version=base,
            )
        return draft

    async def draft(self, actor: int, draft_id: str, *, editable: bool = True) -> AdContentDraft:
        await self.authorize(actor)
        row = await self.session.get(AdContentDraft, draft_id, populate_existing=True)
        if row is None or row.actor_id != actor:
            raise DomainError("ads_stale")
        campaign = await self.get(actor, row.campaign_id)
        if editable and (row.status not in {"READY", "COLLECTING"} or utc(row.expires_at) <= now()):
            raise DomainError("ads_stale")
        if editable and campaign.version != row.base_version:
            raise DomainError("ads_conflict")
        return row

    async def unfinished(self, actor: int) -> list[AdContentDraft]:
        await self.authorize(actor)
        return list(
            await self.session.scalars(
                select(AdContentDraft)
                .join(AdCampaign)
                .where(
                    AdContentDraft.actor_id == actor,
                    AdContentDraft.status.in_(("READY", "COLLECTING")),
                    AdContentDraft.expires_at > now(),
                    AdCampaign.status == "DRAFT",
                    AdCampaign.version == AdContentDraft.base_version,
                )
                .order_by(AdContentDraft.created_at.desc())
                .limit(8)
            )
        )

    async def append_media(
        self, actor: int, draft_id: str, media: dict, message_id: int
    ) -> AdContentDraft:
        numeric_id(message_id)
        async with self.write(actor):
            draft = await self.draft(actor, draft_id)
            if draft.status != "COLLECTING":
                raise DomainError("ads_stale")
            inputs = deepcopy(draft.inputs)
            existing = next((entry for entry in inputs if entry["message_id"] == message_id), None)
            if existing:
                if existing["media"] != media:
                    raise DomainError("ads_stale")
                return draft
            inputs.append({"message_id": message_id, "media": media})
            inputs.sort(key=lambda entry: entry["message_id"])
            candidate = {**draft.content, "media": [entry["media"] for entry in inputs]}
            draft.content, draft.inputs = validate_content(candidate), inputs
            draft.previewed = False
        return draft

    async def finish_media(self, actor: int, draft_id: str) -> AdContentDraft:
        async with self.write(actor):
            draft = await self.draft(actor, draft_id)
            if draft.status != "COLLECTING" or not draft.content["media"]:
                raise DomainError("ads_media")
            draft.content = validate_content(draft.content)
            draft.status = "READY"
        return draft

    async def mark_previewed(self, actor: int, draft_id: str) -> None:
        async with self.write(actor):
            draft = await self.draft(actor, draft_id)
            if draft.status != "READY":
                raise DomainError("ads_stale")
            draft.previewed = True

    async def save(self, actor: int, draft_id: str) -> AdContentVersion:
        async with self.write(actor):
            draft = await self.draft(actor, draft_id, editable=False)
            if draft.status == "SAVED":
                saved = await self.session.scalar(
                    select(AdContentVersion).where(
                        AdContentVersion.campaign_id == draft.campaign_id,
                        AdContentVersion.revision == draft.saved_revision,
                    )
                )
                assert saved is not None
                return saved
            draft = await self.draft(actor, draft_id)
            if draft.status != "READY" or not draft.previewed:
                raise DomainError("ads_preview_required")
            campaign = await self.get(actor, draft.campaign_id)
            content = validate_content(draft.content)
            version = AdContentVersion(
                campaign_id=campaign.id,
                revision=campaign.version + 1,
                content=content,
                actor_id=actor,
                request_key="draft:" + draft.id,
            )
            self.session.add(version)
            campaign.version += 1
            campaign.updated_by, campaign.updated_at = actor, now()
            draft.status, draft.saved_revision = "SAVED", campaign.version
            self.audit(
                actor,
                "content_saved",
                campaign.id,
                revision=campaign.version,
                draft_id=draft.id,
                field=draft.field,
            )
        return version

    async def cancel(self, actor: int, draft_id: str) -> None:
        async with self.write(actor):
            draft = await self.draft(actor, draft_id, editable=False)
            if draft.status == "SAVED":
                raise DomainError("ads_stale")
            if draft.status != "CANCELLED":
                draft.status = "CANCELLED"
                self.audit(actor, "draft_cancelled", draft.campaign_id, draft_id=draft.id)

    async def history(self, actor: int, campaign_id: int, page: int = 0) -> list[AdContentVersion]:
        await self.get(actor, campaign_id, archived=True)
        return list(
            await self.session.scalars(
                select(AdContentVersion)
                .where(AdContentVersion.campaign_id == campaign_id)
                .order_by(AdContentVersion.revision.desc())
                .offset(max(0, min(page, 10000)) * 5)
                .limit(6)
            )
        )

    async def archive(self, actor: int, campaign_id: int, version: int) -> None:
        async with self.write(actor):
            campaign = await self.get(actor, campaign_id, archived=True)
            if campaign.version != version:
                raise DomainError("ads_conflict")
            if campaign.status != "ARCHIVED":
                campaign.status, campaign.updated_by, campaign.updated_at = "ARCHIVED", actor, now()
                self.audit(actor, "archived", campaign.id, revision=campaign.version)
