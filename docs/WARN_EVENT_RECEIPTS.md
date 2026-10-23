# WARN event receipts and REDSAFE navigation

## Causes and changes

Previously WARN receipt rendering counted sanctions still marked RECORDED. The existing automatic-punishment completion path changes them to COMPLETED, so a delayed receipt could show 0/3; refreshing older receipts also replaced their issued counts. Issuance now records warning_count, warning_limit and warning_action in the existing group_moderation_requested AuditEvent transaction, under the existing group lock and request idempotency boundary. No new warning table or migration is needed.

New WARN receipts read this immutable snapshot. Already published group moderation receipts retain their text; reconciliation updates only inline controls. Revocation gets its own receipt. Legacy warnings without snapshots use available cancellation/consumption audit history to reconstruct their issuance count; the historical limit cannot always be recovered, so this fallback uses current settings. Existing published historical texts are not rewritten.

A threshold WARN waits for its escalation result. Confirmed MUTE/BAN/KICK publishes only the final action with the actual configured duration and actual initiating administrator, explicitly labeled as automatic. OFF preserves normal warning receipts. Failed or uncertain actions preserve warnings and never announce successful punishment. Existing policy consumes active warnings only after confirmed native success (KICK requires successful release too). Pending RetryAfter work remains pending, without an extra trigger-WARN success card. Publication retains the existing claim-before-send fence: an ambiguous Telegram send is not blindly repeated.

REDSAFE SECURITY uses escaped display names linked by numeric Telegram ID, with visible code-formatted IDs. Banned-word notices snapshot the real group/user warning counter; DELETE-only rules still do not grant WARN. When a configured WARN is actually recorded, the same security notice updates to that warning's immutable count. Counter-edit retries are bounded and never repeat enforcement. Telegram-deleted/uneditable notices are not recreated. The existing Checks grace-period and filtering policies are unchanged.

PATIKRA and PROFILIS explicitly edit the same text message or supported media caption in private chats and groups. Caption limits use visible UTF-16 length. Repeated edits are harmless; unavailable edits use one replacement panel and retire the prior navigation panel. Confirmed moderation receipts are protected from these interactive edits. Existing target identifiers, access checks and LT/EN/RU routing remain intact.

## Changed components

- `app/warning_snapshots.py`: immutable audit snapshots and legacy fallback.
- `app/moderation_services.py`, `app/warning_services.py`: atomic issuance facts and automatic announcement eligibility.
- `app/bot/moderation_messages.py`, `local_admin.py`, `moderation_outcomes.py`, `moderation_controls.py`: snapshot rendering, threshold outcome selection, immutable public receipts and separate reversal receipts.
- `app/bot/checks_runtime.py`: linked display names, real warning counts and bounded counter-refresh retries.
- `app/bot/handlers.py`, `screens.py`: same-message profile/check navigation and historical-receipt protection.
- `app/locales/local_moderation.py`: automatic actor attribution in LT/EN/RU.
- `tools/auto_ads_live_test.py`: refreshed embedded reviewed runtime, including the new snapshot helper; test boundaries and enforcement allowlists unchanged.
- Tests: `test_warning_event_receipts.py` plus existing checks, Group Help, moderation, warning-controls, large-ID and isolated-operator contracts.

## Validation and safe review

The new tests exercise first/second/threshold warnings, MUTE/BAN/KICK and OFF behavior, nondefault limits, failure/timeout/RetryAfter, immutable history, real PostgreSQL concurrent grants and workers, scoped banned-word counts without duplicate WARNs, escaped linked display names, LT/EN/RU check/profile roundtrips in private/group messages, media captions, repeated edits, fallback and forged callbacks on moderation history.

Use isolated PostgreSQL and Redis with TEST_POSTGRES_URL and TEST_REDIS_URL for the full pytest suite; run ruff check/format, mypy and compileall. Production schema remains 0023. No production native actions are part of automated validation. Review the exact-source quality workflow before deployment; use the existing guarded deployment workflow, preserving scheduled sanctions and all historical data.

Known operational limits: Telegram can reject profile mentions according to client/privacy behavior. An ambiguous send or permanently uneditable/deleted notice is recorded rather than retried without bounds. Historical records predating snapshots lack guaranteed historical policy limits. A live visual check with a consenting test account remains distinct from mocked Bot API transport tests.
