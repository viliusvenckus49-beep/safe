# SAFECheck 2.8.1 — Telegram ID labels

Shared identity formatter displays username/name with numeric Telegram ID or localized unknown-ID text. Applied to /ask,/rep,/profile and other profile cards, SCAM/user lists, REP/report moderation identities, administrative receipts and REP preview. Administrator list/member export already retain their numeric IDs. TOP10 text and link buttons deliberately unchanged, without IDs.

Only Telegram user identity metadata supplies the ID; database primary keys never substitute for missing Telegram IDs. LT/EN/RU unknown labels, escaped formatting and UTF16 display bounds preserved. No schema, authorization, reputation or status-policy changes.

Checks: full Docker PostgreSQL/Redis suite384passed74.97s, no skips. Existing129 targeted tests10.12s plus new three-language list/ID/TOP exclusion assertions and24card identity assertions. Userpagination100 rows and message limits passed. Ruff check/format110files, mypy38modules, gitdiffcheck, source token/TODO/FIXME scan and Docker build passed. Reviewed ID provenance, missing metadata, HTML entity nesting, TOP preservation and list bounds. No migration; head0008. Standard startup python -m app.main; see README for Docker/configuration. Temporary hosting and phone-rendering acceptance limits remain.

Deployed safecheck:2.8.1; Alembic check and startup readiness passed. polling_started confirms version2.8.1. Private backup /tmp/safecheck-before-identity-labels-20261005.dump; stopped rollback container safecheck-rollback-2-8-0 retained. Unused historical images/build cache removed to recover space; running containers and volumes preserved.
