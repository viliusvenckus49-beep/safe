# SAFECheck 2.14.4

SCAM cards expose an admin-only retry button. Registration receipts expose REFRESH in private chats and groups. Refresh edits the existing receipt, rechecks protected groups and retries through the existing durable ban executor. Actual Telegram responses determine Banned / Already banned / Queued. Unknown IDs show guidance to add an ID; usernames are never used as ban identifiers.

Manual retries are serialized using the existing metadata lock and audit table, limited to once per target per 60 seconds across administrators, and do not reset PROCESSING leases, disabled groups or unexpired TelegramRetryAfter waits. No new database schema or migrations.

/del_sc @username or numeric ID removes an active SCAM record immediately without an input wizard or required reason. Admin permissions, removal history and audit remain; this does not automatically unban anyone. The no-argument wizard is retained.

The four selected main-menu labels now use 🔍, 🚨, 🏆 and 🌐 in LT / EN / RU; callbacks and layout are unchanged.

Validation: tests cover immediate deletion by username and ID, private/group refresh, non-admin denial, repeated clicks, durable retry success, flood waits and callback compatibility. Local validation: 719 tests passed, 23 PostgreSQL/Redis tests skipped without those local services. GitHub production-quality gate: all 742 tests passed with disposable PostgreSQL/Redis, Ruff passed, 180 files formatted, mypy passed for 51 modules, pip check passed and production Compose validated. Production deployment uses the existing verified backup and code-only deploy safeguards; no production reset/delete commands.

Tested source: `9dfc62aff106592f0b9d796b67b323c0e3d7b381`. Deployment run: https://github.com/viliusvenckus49-beep/safe/actions/runs/37756808220.

Production verification: image `safecheck:2.14.4-9dfc62aff106` and all bot/PostgreSQL/Redis containers healthy. Verified backup `/var/backups/safecheck/safecheck-20261008T093159Z-359544c2.dump`. Counts before and after identical: users 173, rep events 1, rep requests 13, adjustments 1, SCAM records 46, TRUSTED 18, admins 4, groups 10, observed members 190, reports 3. The temporary branch push deployment trigger was removed after rollout; the final deploy workflow remains manual-only.
