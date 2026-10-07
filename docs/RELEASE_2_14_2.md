# SAFECheck 2.14.2

The configured group owner can remove a staged or approved group through `/groups` → group → Remove group → Confirm removal. The bot withdraws protection, recovery consent and outstanding delivery jobs, hides the group from management and recovery menus, and attempts to leave Telegram. If Telegram rejects leaving, activity remains disabled and the owner receives a short warning. Removed groups ignore messages, old callbacks and membership events; later bot promotion cannot silently reactivate them.

Existing managed-group rows, observed members, completed bans, recovery history, user identities, SCAM, TRUSTED and reputation records remain stored. Removal uses an existing audit event with a BIGINT group ID; no migration or table deletion is required. Pending, failed and processing jobs become obsolete. A worker's stale completion cannot resurrect jobs withdrawn during its Telegram call.

SCAM receipts always retain the requested BLOCK STATUS layout, including UNKNOWN ID receipts. Banned / Already banned / Queued counts come from actual numeric-ID jobs. An unknown ID displays zero counts and cannot imply successful or queued Telegram bans. All three existing UI languages remain supported; existing callback payloads are unchanged, with new group-removal actions added.

Regression coverage includes owner-only access, pending-group removal, history preservation, cancellation of each unfinished job state, stale confirmations, worker revalidation, Telegram leave success/refusal, silent removed-group messages and joins, large PostgreSQL group IDs, and localized callback lengths. Existing reputation and ban retry tests remain part of the full suite.

Local validation: 46 presentation, keyboard and catalog tests passed; Ruff, formatting, mypy (51 application modules), syntax compilation and diff whitespace checks passed. Full CI and backup-verified deployment results will be recorded after completion.
