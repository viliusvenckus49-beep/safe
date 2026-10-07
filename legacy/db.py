from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from config import settings

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Reputation(Base):
    __tablename__ = "reputation"
    __table_args__ = (UniqueConstraint("giver_tg_id", "target_user_id", name="uq_rep_vote"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    giver_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    value: Mapped[int] = mapped_column(Integer)  # +1 / -1
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class ScamEntry(Base):
    __tablename__ = "scam_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    reason: Mapped[str] = mapped_column(Text)
    added_by: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

engine = create_async_engine(settings.database_url)
Session = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_or_create_user(session: AsyncSession, username: str, tg_id: int | None = None) -> User:
    username = username.lstrip("@").lower()
    q = select(User).where(User.tg_id == tg_id) if tg_id else select(User).where(func.lower(User.username) == username)
    user = (await session.execute(q)).scalar_one_or_none()
    if user:
        if username and user.username != username:
            user.username = username
        return user
    user = User(tg_id=tg_id, username=username)
    session.add(user)
    await session.flush()
    return user

async def rep_stats(session: AsyncSession, user_id: int) -> tuple[int, int, int]:
    rows = (await session.execute(select(Reputation.value, func.count()).where(Reputation.target_user_id == user_id).group_by(Reputation.value))).all()
    counts = {v: c for v, c in rows}
    plus, minus = counts.get(1, 0), counts.get(-1, 0)
    return plus - minus, plus, minus
