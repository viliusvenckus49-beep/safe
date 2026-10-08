# SAFECheck 2.14.4

SCAM cards expose an admin-only retry button. Registration receipts expose REFRESH in private chats and groups. Refresh edits the existing receipt, rechecks protected groups and retries through the existing durable ban executor. Actual Telegram responses determine Banned / Already banned / Queued. Unknown IDs show guidance to add an ID; usernames are never used as ban identifiers.

Manual retries are serialized using the existing metadata lock and audit table, limited to once per target per 60 seconds across administrators, and do not reset PROCESSING leases, disabled groups or unexpired TelegramRetryAfter waits. No new database schema or migrations.

/del_sc @username or numeric ID removes an active SCAM record immediately without an input wizard or required reason. Admin permissions, removal history and audit remain; this does not automatically unban anyone. The no-argument wizard is retained.

The four selected main-menu labels now use 🔍, 🚨, 🏆 and 🌐 in LT / EN / RU; callbacks and layout are unchanged.

Validation: tests cover immediate deletion by username and ID, private/group refresh, non-admin denial, repeated clicks, durable retry success, flood waits and callback compatibility. Full test and deployment evidence will be recorded after completion. Production deployment uses the existing verified backup and code-only deploy safeguards; no production reset/delete commands.
