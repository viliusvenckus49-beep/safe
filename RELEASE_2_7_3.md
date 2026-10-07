# SAFECheck 2.7.3 — instruction wording

Removed the optional-reason sentence from invalid /add_sc group usage instructions in LT/EN/RU. Information card now explains +rep @username, -rep @username, administrator approval and /rep @username alongside existing /ask and private /report instructions. No handler/service/authorization/database changes. /add_sc remains admin-only with unchanged optional argument handling.

Verification:104 focused Telegram, localization, catalog and SCAM-policy tests passed in9.50s; explicitly includes admin and non-admin SCAM operations. Manual catalog assertions verified all3language cards and removed usage wording. Ruff check/format104files, mypy38modules, gitdiffcheck and Docker build passed. Full suite not repeated for this text-only release; prior2.7.2 fullsuite343passed. No migration, DBhead0008 unchanged. Standard startup python -m app.main; README contains configuration/Docker commands. Temporary hosting and real-phone acceptance limits remain.

Deployed safecheck:2.7.3; Alembic check and startup readiness passed; polling_started confirms version2.7.3. Private backup /tmp/safecheck-before-info-20261005.dump and stopped rollback container safecheck-rollback-2-7-2 retained.
