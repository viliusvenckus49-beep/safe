# SAFECheck 2.12.0 — administrator SCAM identity supplements

Private `/admin` → SCAM registry retains existing search and pagination callbacks, adds selectable records. Buttons use @username or numeric Telegram ID; cards display both known/unknown identity fields. Missing ID or username can be supplied manually with actor-scoped FSM preview and nonce confirmation. `/add_sc ID`, the existing numeric target wizard and group command remain supported. After private `/add_sc @username` with unknown ID, the following plain ID message opens confirmation rather than being ignored.

`ScamManagement` validates fields, requires fresh numeric administrator authorization, serializes with the shared identity lock and commits record changes, audit and approved-group ban outbox atomically. No overwritten known fields; mismatched known usernames and already-active scam targets reject. Binding to an existing numeric identity transfers only this confirmed record, leaving historical REP/report rows intact on their original targets. Audit captures old identity and the change; record creation/moderator/reason remain unchanged. Exact nonce replay is idempotent; cross-actor/payload replay rejects. Telegram cannot verify arbitrary ID/username ownership, so the administrator must independently check before confirming.

UI callbacks require private chat and fresh authorization; no group editing controls. All new text is LT/EN/RU and user values escaped. Invalid input restores registry navigation. Existing public registry behavior is retained. No schema migration: Alembic head0009.

Verification:
- Existing targeted Telegram/localization/SCAM tests:96 passed.
- Targeted navigation/new identity journeys:33 passed.
- Full Docker suite with isolated PostgreSQL schemas and Redis:444 passed93.63s, no skips.
- Added validation, conflict, numeric command/wizard, three-locale plain-ID confirmation, duplicate confirmation, authority/stale input, commit rollback, existing reputation preservation, PG competing targets and single ban-outbox tests.
- Initial full run exposed two navigation regressions; fixed by retaining existing callback/page/search builders, then reran full suite successfully.
- Ruff check/format135files, mypy47modules, pip check, git diff check, TODO/FIXME and credential-pattern source search passed.

Final installed Docker package navigation/identity checks:33 passed9.41s with explicit asyncio mode (initial tests-only mount lacked project asyncio configuration; rerun corrected). Docker build, live Alembic check and app.main --check passed. Live safecheck-live-test now2.12.0 with polling_started observed and unless-stopped restart policy. Private backup /tmp/safecheck-before-scam-identity-20261006.dump; stopped rollback safecheck-rollback-2-11-0 retained. No production records modified for testing.
