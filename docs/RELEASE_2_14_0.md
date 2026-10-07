# CRIMSON SAFECHECK™ 2.14.0

The existing Telegram application now has the requested seven-row private menu, consistent CRIMSON branding, localized LT/EN/RU interface and `＋ REP` / `− REP` labels. Dynamic usernames, display names and IDs stay ordinary escaped text. TOP entries no longer use space-padded alignment. Commands, callback schemas, reputation calculations, moderation, cooldown and self-vote protections remain compatible.

TRUSTED grants with known IDs use the VERIFIED receipt. SCAM receipts distinguish unknown identity, complete protection, partial protection and pending failures. BLOCKED requires actual successful Telegram responses in every active protected group. Receipts remain in chat after navigation, including approved reports and administrator identity supplements.

## Automatic protection

`app/group_services.py` retains the existing `ban_actions` outbox. SCAM registration, approved reports, identity supplements and trusted numeric observations enqueue every owner-approved, enabled group. `app/bot/group_runtime.py::process_scam_bans` initiates these committed jobs immediately and shares the same executor with the existing worker. No separate ban approval is needed.

Each attempt rechecks the current SCAM record, current numeric target ID and group protection. Only literal Telegram `True` counts as success. Structured logs include operation, user ID, chat ID, record, outcome and sanitized API error type/reason. The interactive executor claims one job at a time, has a 20-second processing budget and a 10-second API timeout; overflow stays pending for the worker. Obsolete identity jobs are skipped without stopping subsequent ready jobs.

BadRequest and Forbidden failures remain durable pending work. Losing ban rights does not silently remove an approved group from protection. The first subsequent group message retries a rejected preemptive ban immediately; repeat messages are paced through the existing audit table. Fresh joins retry immediately. Permission restoration, joins and messages preserve a future Telegram RetryAfter deadline. A successful attempt marks its job SUCCEEDED.

Numeric Telegram ID remains the durable identity. Username changes preserve SCAM, TRUSTED and reputation history. A username match alone cannot safely identify the owner of an older username-only SCAM record; the existing administrator Add ID function remains available. Telegram supplies no arbitrary private-user username resolver.

## Files and data compatibility

- Interface: `app/presentation.py`, `app/locales/`, `app/bot/keyboards.py`.
- Protection: `app/group_services.py`, `app/services.py`, `app/bot/group_runtime.py`, `app/bot/groups.py`, `app/bot/middleware.py`.
- Receipts and identity paths: `app/bot/scam_notices.py`, `app/bot/scam_admin.py`, `app/bot/handlers.py`.
- Regression tests: `tests/test_crimson_telegram.py`, `tests/test_scam_ban_flow.py`, existing presentation, navigation, localization and persistent-notice suites.
- Version and operation examples: project metadata, production image defaults, README and operations documentation.

No model or migration file changed. The existing migration head is `0009`; no database reset, legacy re-import or data replacement is part of this release. PostgreSQL and Redis definitions and existing production volumes are preserved. The existing deployment utility refuses differing schemas or data-service configuration and verifies a fresh database backup before replacing the bot image.

## Verification

The complete test suite contains 695 cases, including isolated PostgreSQL and Redis integration tests. Regression coverage includes known/unknown SCAM IDs, administrator ID linking, strict numeric observations, all/partial/rejected bans, missing permissions, first-message and join retries, RetryAfter, concurrent claims, timeouts, stale identities, username changes, preserved REP/TRUSTED history, both REP buttons, comment requirements, menu callbacks and LT/EN/RU receipts.

Additional checks cover Ruff lint/format, mypy, imports, SQLite migration readiness and Docker image build. A separate baseline comparison verified 57 callback payload combinations in all three languages; existing payloads remain unchanged. New report-decision receipt Back buttons carry the existing Action schema's `receipt` marker to preserve chat history.

## Telegram limits

Telegram can reject bans because the bot lacks rights, the target is a group owner/administrator or the group does not support that operation. Such results remain pending and are never displayed as successful bans. Telegram privacy/update delivery can limit observed member data. Numeric identity updates require trusted Telegram objects or administrator input; changing a username cannot erase numeric-ID protection.
