"""SQL-only pagination/search, using the existing reputation and SCAM predicates."""

from sqlalchemy import String, cast, func, literal, or_, select
from sqlalchemy.orm import selectinload

from app.merchant_catalog import PAGE_SIZE, REVIEW_PAGE_SIZE, grams
from app.models import (
    MerchantEntry,
    MerchantProposal,
    MerchantSearchGram,
    ReputationEvent,
    ReputationRequest,
    User,
)
from app.repositories import reputation_ledger, scam_identity_exists


def public_entry():
    return (
        (MerchantEntry.status == "APPROVED")
        & User.telegram_id.is_not(None)
        & ~scam_identity_exists(User.id, User.username)
    )


class MerchantRepository:
    def __init__(self, session):
        self.session = session

    async def entry(self, entry_id: int):
        return await self.session.scalar(
            select(MerchantEntry)
            .where(MerchantEntry.id == entry_id)
            .options(selectinload(MerchantEntry.owner))
            .execution_options(populate_existing=True)
        )

    async def public(self, entry_id: int):
        return await self.session.scalar(
            select(MerchantEntry)
            .join(User, User.id == MerchantEntry.owner_user_id)
            .where(MerchantEntry.id == entry_id, public_entry())
            .options(selectinload(MerchantEntry.owner))
            .execution_options(populate_existing=True)
        )

    async def mine(self, actor: int):
        return await self.session.scalar(
            select(MerchantEntry)
            .join(User, User.id == MerchantEntry.owner_user_id)
            .where(User.telegram_id == actor)
            .options(selectinload(MerchantEntry.owner))
            .execution_options(populate_existing=True)
        )

    async def open_proposal(self, entry_id: int):
        return await self.session.scalar(
            select(MerchantProposal)
            .where(
                MerchantProposal.entry_id == entry_id,
                MerchantProposal.status.in_(("DRAFT", "PENDING")),
            )
            .execution_options(populate_existing=True)
        )

    async def latest_proposal(self, entry_id: int):
        return await self.session.scalar(
            select(MerchantProposal)
            .where(MerchantProposal.entry_id == entry_id)
            .order_by(MerchantProposal.id.desc())
            .limit(1)
        )

    async def listing(self, *, city="", query="", page=0, admin=False):
        conditions = [] if admin else [public_entry()]
        if city:
            conditions.append(MerchantEntry.city_code == city)
        if query:
            tokens = grams(query)
            matching = (
                select(MerchantSearchGram.entry_id)
                .where(MerchantSearchGram.token.in_(tokens))
                .group_by(MerchantSearchGram.entry_id)
                .having(func.count() == len(tokens))
            )
            conditions.append(
                or_(
                    User.username == query,
                    MerchantEntry.id.in_(matching)
                    & MerchantEntry.name_key.contains(query, autoescape=True),
                )
            )
        total = int(
            await self.session.scalar(
                select(func.count())
                .select_from(MerchantEntry)
                .join(User, User.id == MerchantEntry.owner_user_id)
                .where(*conditions)
            )
            or 0
        )
        page = min(max(0, page), max(0, (total - 1) // PAGE_SIZE))
        ledger = reputation_ledger()
        scores = (
            select(ledger.c.user_id, func.sum(ledger.c.positive - ledger.c.negative).label("score"))
            .group_by(ledger.c.user_id)
            .subquery()
        )
        score = func.coalesce(scores.c.score, 0)
        rows = await self.session.execute(
            select(MerchantEntry, User, score)
            .join(User, User.id == MerchantEntry.owner_user_id)
            .outerjoin(scores, scores.c.user_id == User.id)
            .where(*conditions)
            .order_by(score.desc(), MerchantEntry.name_key, MerchantEntry.id)
            .limit(PAGE_SIZE)
            .offset(page * PAGE_SIZE)
        )
        return [(entry, user, int(value)) for entry, user, value in rows], total, page

    async def reviews(self, user_id: int, sign: str = "all", page: int = 0):
        pending = select(
            literal("request").label("source"),
            ReputationRequest.id.label("id"),
            ReputationRequest.value.label("value"),
            ReputationRequest.created_at.label("date"),
            ReputationRequest.comment.label("comment"),
            ReputationRequest.giver_user_id.label("giver"),
        ).where(
            ReputationRequest.receiver_user_id == user_id, ReputationRequest.status == "APPROVED"
        )
        legacy = select(
            literal("legacy").label("source"),
            ReputationEvent.id.label("id"),
            ReputationEvent.value.label("value"),
            ReputationEvent.created_at.label("date"),
            cast(literal(None), String).label("comment"),
            ReputationEvent.giver_user_id.label("giver"),
        ).where(ReputationEvent.receiver_user_id == user_id)
        approved = pending.union_all(legacy).subquery()
        conditions = [] if sign == "all" else [approved.c.value == (1 if sign == "pos" else -1)]
        total = int(
            await self.session.scalar(select(func.count()).select_from(approved).where(*conditions))
            or 0
        )
        page = min(max(0, page), max(0, (total - 1) // REVIEW_PAGE_SIZE))
        rows = await self.session.execute(
            select(approved, User)
            .join(User, User.id == approved.c.giver)
            .where(*conditions)
            .order_by(approved.c.date.desc(), approved.c.source, approved.c.id.desc())
            .limit(REVIEW_PAGE_SIZE)
            .offset(page * REVIEW_PAGE_SIZE)
        )
        return list(rows.mappings()), total, page
