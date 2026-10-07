# SAFECheck 2.5.5

Simplified member backup description LT/EN/RU. Export is now readable UTF-8 safecheck-members.txt rather than JSON: numbered display name, username if known, Telegram ID. Group title and empty-list text localized. Newlines in user names are collapsed so members cannot inject extra rows. Export authorization/audit and stored metadata/history are unchanged.

24 targeted text-export/group-service/group-approval/i18n-audit tests passed; Ruff check/format and mypy37 passed. Docker build and live startup verified. No migration.
