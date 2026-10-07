# SAFECheck 2.9.1 — result controls in groups

Public lookup/profile cards now share check-another, positive/negative REP and localized Cancel buttons in private chats and groups. Cancel closes the current panel through the existing screen lifecycle, clearing the acting user's draft. Completed notices remain without controls. Administrative registry searches retain their contextual Back control. REP still requires administrator approval; permissions and anti-abuse policy unchanged.

Regression tests cover LT/EN/RU, private/group /ask, addressed /ask, /rep and /profile, localized labels, actual Telegram deletion and state clearing. Existing registry pagination return regression retained.78 focused navigation/Telegram tests passed9.68s; Ruff check/format116 files and mypy38 modules passed. Docker image built; no schema changes.

Final full Docker PostgreSQL/Redis suite399 passed68.69s, no skips. Old no-group-controls assertions were updated to the requested controls contract; actual close deletion and state reset verified.

Docker build, schema and startup checks passed; live2.9.1 polling verified. Head0008 unchanged. Private backup /tmp/safecheck-before-result-controls-20261005.dump; stopped rollback safecheck-rollback-2-9-0 retained.
