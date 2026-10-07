# SafeCheck Bot V1
Telegram reputation + scam registry bot built with aiogram 3 and SQLAlchemy.

## Commands
- `+rep @username`, `-rep @username`
- `/rep @username`
- `/top`
- `/report @username reason`
- `/ask @username`
- `/add_sc @username reason` (admin)
- `/del_sc @username` (admin)
- `/scammers`

## Run
1. Python 3.11+
2. `python -m venv .venv`
3. Activate venv and `pip install -r requirements.txt`
4. Copy `.env.example` to `.env`; set `BOT_TOKEN` and numeric `ADMIN_IDS`.
5. `python main.py`

## Important V1 behavior
Unknown / non-listed users are never labeled "trusted". Telegram numeric IDs are supported by the data model; V2 should resolve/store them aggressively when users interact or are targeted via replies.
