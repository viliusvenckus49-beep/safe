# SAFECheck 2.5.1

Administrator user list increased to 100 per page. Compact escaped HTML rows show @username [Telegram ID] or @username [no ID]. Names bounded to 16 UTF-16 units to fit even 100 worst-case Unicode/19-digit IDs; stored identity metadata unchanged. User pagination uses100 independently of existing5-item moderation/audit lists.

Verification:67 focused user-pagination/presentation/keyboard/Telegram-journey tests passed; Ruff check/format, mypy37, gitdiffcheck, Docker build and live startup check passed. No migration.
