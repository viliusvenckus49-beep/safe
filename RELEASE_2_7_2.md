# SAFECheck 2.7.2 — SCAM presentation

Group /add_sc confirmations omit the unknown-ID/automatic-blocking warning; the private administrator command and wizard retain it. /scammers cards omit the identity caveat. /ask continues to distinguish unknown identity.

Manual confirmed SCAM records (no report relationship) display a localized administrator confirmation in /ask/profile and /scammers, replacing optional internal notes such as proofs. Original reasons are retained in the database; report-linked SCAM records continue to display their escaped original reasons. No data rewrite, migration, authorization or banning changes.

Checks: targeted presentation/Telegram/i18n/policy suite110passed10.65s; full Docker PostgreSQL/Redis suite343passed66.45s, no skips. Ruff check/format104files, mypy38modules, gitdiffcheck, source token/TODO/FIXME scan and Docker build passed. Reviewed display provenance, preserved notes/reasons, unchanged identity and auto-ban policies and LT/EN/RU placeholder parity. Alembic check and startup check passed before deployment; DBhead0008 unchanged. Runtime remains temporary; real-phone acceptance is a user check.

Startup remains python -m app.main; README contains environment and Docker commands. No additional credentials needed.

Deployed safecheck:2.7.2; polling_started confirms version2.7.2 and container running. Private backup /tmp/safecheck-before-scam-presentation-20261005.dump; stopped previous container safecheck-rollback-2-7-1 retained.
