"""Administrative management of independent TRUSTED bases without changing access roles."""

from app.errors import DomainError
from app.models import TopVisibility, TopVisibilityAction, TrustedAction
from app.services import Service


class TrustedManagement:
    PAGE_SIZE = 8

    def __init__(self, core: Service):
        self.core = core

    async def detail(self, actor: int, target_id: int) -> dict:
        await self.core.require_admin(actor)
        user = await self.core.repo.user_by_id(target_id)
        if user is None:
            raise DomainError("not_found")
        data = await self.core._profile_user(user)
        manual = await self.core.repo.trusted_designation(user.id)
        visibility = await self.core.repo.top_visibility(user.id)
        data["manual_trusted"] = bool(manual and manual.active)
        data["top_included"] = bool(visibility and visibility.visible)
        data["removable"] = data["manual_trusted"] or data["top_included"]
        return data

    async def page(self, actor: int, page: int, query: str = "") -> tuple[list[dict], int, int]:
        await self.core.require_admin(actor)
        if not 0 <= page <= 999999 or len(query) > 64:
            raise DomainError("invalid_input")
        if query.isascii() and query.isdigit() and not 0 < int(query) <= 9223372036854775807:
            raise DomainError("invalid_target")
        args = (query.strip(), self.core.settings.group_owner, self.core.settings.admins)
        users, total = await self.core.repo.trusted_candidates(
            page * self.PAGE_SIZE, self.PAGE_SIZE, *args
        )
        page = min(page, max(0, (total - 1) // self.PAGE_SIZE))
        if not users and total:
            users, _ = await self.core.repo.trusted_candidates(
                page * self.PAGE_SIZE, self.PAGE_SIZE, *args
            )
        return [await self.detail(actor, user.id) for user in users], total, page

    async def revoke(self, actor: int, target_id: int, nonce: str) -> dict:
        import re

        if not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise DomainError("stale_callback")
        await self.core.require_admin(actor)
        key = "trusted-ui:" + nonce
        session, repo = self.core.session, self.core.repo
        try:
            await repo.lock_identity_metadata()
            await self.core.require_admin(actor)
            data = await self.detail(actor, target_id)
            await self.core._lock(data["user"])
            data = await self.detail(actor, target_id)
            previous = await repo.trusted_action(key)
            if previous:
                if (previous.actor_id, previous.target_id, previous.active) != (
                    actor,
                    target_id,
                    False,
                ):
                    raise DomainError("stale_callback")
                await session.commit()
                return data
            if not data["removable"]:
                raise DomainError("stale_callback")
            manual = await repo.trusted_designation(target_id)
            if manual and manual.active:
                manual.active, manual.actor_id = False, actor
                self.core._audit(actor, "trusted_removed", target_id)
            visibility = await repo.top_visibility(target_id)
            if visibility is None:
                session.add(TopVisibility(target_id=target_id, visible=False))
            else:
                visibility.visible = False
            session.add(
                TrustedAction(target_id=target_id, actor_id=actor, active=False, request_key=key)
            )
            session.add(
                TopVisibilityAction(
                    target_id=target_id,
                    actor_id=actor,
                    visible=False,
                    reason="Administrator confirmed removal from TRUSTED management",
                    request_key=key,
                )
            )
            self.core._audit(
                actor,
                "top_visibility",
                target_id,
                visible=False,
                reason="TRUSTED management",
                request_key=key,
            )
            await session.commit()
            return await self.detail(actor, target_id)
        except Exception:
            await session.rollback()
            raise
