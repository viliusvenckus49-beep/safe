from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy import select, func
from db import Session, User, Reputation, ScamEntry, Report, get_or_create_user, rep_stats
from config import settings

router = Router()

def arg_username(message: Message) -> str | None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) >= 2 and parts[1].startswith("@"):
        return parts[1].lstrip("@").lower()
    if message.reply_to_message and message.reply_to_message.from_user:
        return (message.reply_to_message.from_user.username or "").lower() or None
    return None

def reason_arg(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=2)
    return parts[2].strip() if len(parts) >= 3 else ""

@router.message(CommandStart())
async def start(m: Message):
    await m.answer("🛡 SafeCheck\n\n/ask @username — patikrinti\n/rep @username — reputacija\n/top — TOP 10\n/report @username priežastis — pranešti")

@router.message(Command("rep"))
@router.message(Command("ask"))
async def check(m: Message):
    username = arg_username(m)
    if not username:
        return await m.answer("Naudojimas: /ask @username")
    async with Session() as s:
        user = (await s.execute(select(User).where(func.lower(User.username) == username))).scalar_one_or_none()
        if not user:
            return await m.answer(f"⚪ @{username} SafeCheck bazėje įrašų nėra. Tai nereiškia, kad asmuo yra patikimas.")
        score, plus, minus = await rep_stats(s, user.id)
        scam = (await s.execute(select(ScamEntry).where(ScamEntry.user_id == user.id, ScamEntry.active == True))).scalar_one_or_none()
        if scam:
            await m.answer(f"🔴 SCAM ALERT\n@{username} yra SafeCheck scam sąraše.\nPriežastis: {scam.reason}\nREP: {score:+d} (+{plus} / -{minus})")
        else:
            await m.answer(f"🟢 @{username} nėra SafeCheck scam sąraše.\nREP: {score:+d} (+{plus} / -{minus})\n\nTai nėra patikimumo garantija.")

@router.message(F.text.regexp(r"^[+-]rep(?:\s|$)"))
async def vote(m: Message):
    username = arg_username(m)
    if not username or not m.from_user:
        return await m.answer("Naudojimas: +rep @username arba atsakyk į žmogaus žinutę su +rep")
    value = 1 if (m.text or "").lower().startswith("+rep") else -1
    if m.from_user.username and m.from_user.username.lower() == username:
        return await m.answer("Sau reputacijos suteikti negalima.")
    async with Session() as s:
        target = await get_or_create_user(s, username)
        old = (await s.execute(select(Reputation).where(Reputation.giver_tg_id == m.from_user.id, Reputation.target_user_id == target.id))).scalar_one_or_none()
        if old:
            old.value = value
        else:
            s.add(Reputation(giver_tg_id=m.from_user.id, target_user_id=target.id, value=value))
        await s.commit()
        score, plus, minus = await rep_stats(s, target.id)
    await m.answer(f"{'➕' if value == 1 else '➖'} REP @{username}\nDabar: {score:+d} (+{plus} / -{minus})")

@router.message(Command("top"))
async def top(m: Message):
    async with Session() as s:
        q = select(User.username, func.coalesce(func.sum(Reputation.value), 0).label("score")).join(Reputation, Reputation.target_user_id == User.id).group_by(User.id).order_by(func.sum(Reputation.value).desc()).limit(10)
        rows = (await s.execute(q)).all()
    if not rows: return await m.answer("TOP dar tuščias.")
    await m.answer("🏆 SafeCheck TOP 10\n\n" + "\n".join(f"{i}. @{u} — {score:+d}" for i, (u, score) in enumerate(rows, 1)))

@router.message(Command("report"))
async def report(m: Message):
    username, reason = arg_username(m), reason_arg(m)
    if not username or not reason or not m.from_user:
        return await m.answer("Naudojimas: /report @username priežastis")
    async with Session() as s:
        target = await get_or_create_user(s, username)
        s.add(Report(reporter_tg_id=m.from_user.id, target_user_id=target.id, reason=reason))
        await s.commit()
    await m.answer("📨 Reportas pateiktas administracijai. Jis automatiškai nepaverčia žmogaus scammeriu.")

@router.message(Command("add_sc"))
async def add_sc(m: Message):
    if not m.from_user or m.from_user.id not in settings.admins: return
    username, reason = arg_username(m), reason_arg(m)
    if not username or not reason: return await m.answer("Naudojimas: /add_sc @username priežastis")
    async with Session() as s:
        target = await get_or_create_user(s, username)
        entry = (await s.execute(select(ScamEntry).where(ScamEntry.user_id == target.id))).scalar_one_or_none()
        if entry:
            entry.reason, entry.active, entry.added_by = reason, True, m.from_user.id
        else:
            s.add(ScamEntry(user_id=target.id, reason=reason, added_by=m.from_user.id))
        await s.commit()
    await m.answer(f"🔴 @{username} įtrauktas į SCAM sąrašą.")

@router.message(Command("del_sc"))
async def del_sc(m: Message):
    if not m.from_user or m.from_user.id not in settings.admins: return
    username = arg_username(m)
    if not username: return await m.answer("Naudojimas: /del_sc @username")
    async with Session() as s:
        user = (await s.execute(select(User).where(func.lower(User.username) == username))).scalar_one_or_none()
        if not user: return await m.answer("Įrašas nerastas.")
        entry = (await s.execute(select(ScamEntry).where(ScamEntry.user_id == user.id, ScamEntry.active == True))).scalar_one_or_none()
        if not entry: return await m.answer("Aktyvaus SCAM įrašo nėra.")
        entry.active = False
        await s.commit()
    await m.answer(f"✅ @{username} pašalintas iš aktyvaus SCAM sąrašo.")

@router.message(Command("scammers"))
async def scammers(m: Message):
    async with Session() as s:
        rows = (await s.execute(select(User.username, ScamEntry.reason).join(ScamEntry, ScamEntry.user_id == User.id).where(ScamEntry.active == True).order_by(ScamEntry.created_at.desc()).limit(25))).all()
    if not rows: return await m.answer("Aktyvus SCAM sąrašas tuščias.")
    await m.answer("🚨 SCAM SĄRAŠAS\n\n" + "\n".join(f"@{u} — {r}" for u, r in rows))
