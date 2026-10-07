# SAFECheck 2.4.1

Telegram slash menus show only `/start`, `/ask`, `/rep`, `/report` in LT, EN and RU, including administrator menus. Other commands remain usable manually. No database migration or business logic changes.

Verification: 29 focused command, catalog, keyboard and callback tests passed; Ruff lint/format and mypy passed; Docker image built and live startup check passed. Existing private command scopes synchronized via Telegram API.
