"""Provision a private MTProto session interactively; never send moderation commands."""

import argparse
import asyncio
import getpass
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

STAFF_CHAT_ID = -1004300060813
STAFF_BOT = "ghStaffBot"


def staff_chat_id(value: str) -> int:
    if not re.fullmatch(r"-[0-9]+", value) or not -(2**63) <= int(value) < 0:
        raise argparse.ArgumentTypeError("Staff group ID must be a negative Telegram chat ID")
    return int(value)


def staff_bot_username(value: str) -> str:
    username = value.removeprefix("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        raise argparse.ArgumentTypeError("Staff bot must be a Telegram username")
    return username


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("State directory must not be a symlink")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ValueError("State directory is unavailable")
    path.chmod(0o700)


def private_file(path: Path, *, create: bool = False) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("Expected a regular private file")
    os.fchmod(fd, 0o600)
    return fd


def validate_credentials(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"api_id", "api_hash"}:
        raise ValueError("Invalid API configuration")
    api_id, api_hash = value["api_id"], value["api_hash"]
    if type(api_id) is not int or not 0 < api_id <= 2147483647:
        raise ValueError("Invalid api_id")
    if not isinstance(api_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        raise ValueError("Invalid api_hash")
    return {"api_id": api_id, "api_hash": api_hash}


def credentials(path: Path) -> dict[str, Any]:
    if path.exists() or path.is_symlink():
        with os.fdopen(private_file(path), "r") as handle:
            return validate_credentials(json.load(handle))
    api_id = input("api_id iš my.telegram.org: ").strip()
    if not re.fullmatch(r"[0-9]+", api_id):
        raise ValueError("Invalid api_id")
    values = validate_credentials(
        {"api_id": int(api_id), "api_hash": getpass.getpass("api_hash (paslėptas): ").strip()}
    )
    with os.fdopen(private_file(path, create=True), "w") as handle:
        json.dump(values, handle)
    return values


async def verify_staff(
    client: Any, *, staff_id: int = STAFF_CHAT_ID, staff_bot: str = STAFF_BOT
) -> bool:
    me = await client.get_me()
    if me is None or getattr(me, "bot", False):
        raise ValueError("A user account is required")
    try:
        staff = await client.get_entity(staff_id)
    except ValueError:
        # A fresh session has not cached the access hash of a private supergroup yet.
        # Dialogs provide a real Telegram entity; never identify staff by its title.
        async for dialog in client.iter_dialogs():
            if dialog.id == staff_id:
                staff = dialog.entity
                break
        else:
            raise ValueError("Configured staff group is not in the account's dialogs") from None
    if getattr(staff, "left", False) or getattr(staff, "deactivated", False):
        raise ValueError("Staff group membership is required")
    bot = await client.get_entity(staff_bot)
    if not getattr(bot, "bot", False) or (
        getattr(bot, "username", "").casefold() != staff_bot.casefold()
    ):
        raise ValueError("Unexpected staff bot identity")
    members = await client.get_participants(staff)
    if not any(member.id == bot.id for member in members):
        raise ValueError("Staff bot is not in the configured group")
    print(f"MTProto paskyra prijungta. ID: {me.id}")
    print(f"Staff grupė pasiekiama: {staff_id}; botas: @{staff_bot}")
    print("Blokavimo komandų nesiųsta. SAFECheck automatinis ryšys dar neįjungtas.")
    return True


async def login(
    state_dir: Path, *, staff_id: int = STAFF_CHAT_ID, staff_bot: str = STAFF_BOT
) -> None:
    from telethon import TelegramClient

    os.umask(0o077)
    private_directory(state_dir)
    config = credentials(state_dir / "api.json")
    session = state_dir / "account.session"
    fd = private_file(session, create=not (session.exists() or session.is_symlink()))
    os.close(fd)
    client = TelegramClient(
        str(session), config["api_id"], config["api_hash"], flood_sleep_threshold=0
    )
    try:
        await client.start(
            phone=lambda: getpass.getpass("Paskyros numeris su šalies kodu (paslėptas): "),
            code_callback=lambda: getpass.getpass("Telegram prisijungimo kodas (paslėptas): "),
            password=lambda: getpass.getpass("Dviejų žingsnių slaptažodis (paslėptas): "),
        )
        try:
            await verify_staff(client, staff_id=staff_id, staff_bot=staff_bot)
        except Exception as error:
            print(f"Sesija išsaugota; staff patikra nepraėjo: {type(error).__name__}.")
            print(f"Pridėk šią paskyrą ir @{staff_bot} į {staff_id}, tada paleisk dar kartą.")
            raise SystemExit(3) from None
    finally:
        await client.disconnect()
        session.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".config/safecheck-mtproto")
    parser.add_argument("--staff-id", type=staff_chat_id, default=STAFF_CHAT_ID)
    parser.add_argument("--staff-bot", type=staff_bot_username, default=STAFF_BOT)
    options = parser.parse_args()
    try:
        if not sys.stdin.isatty():
            raise ValueError("Interactive terminal required")
        asyncio.run(
            login(options.state_dir, staff_id=options.staff_id, staff_bot=options.staff_bot)
        )
    except (KeyboardInterrupt, EOFError):
        print("Prisijungimas nutrauktas.")
        raise SystemExit(1) from None
    except Exception as error:
        # Never echo exception messages, credentials, login codes or the session.
        print(f"Prisijungimas nepavyko: {type(error).__name__}.")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
