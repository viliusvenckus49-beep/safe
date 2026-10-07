# SAFECheck 2.13.1 — persistent action notices

Permission denials and SCAM/TRUSTED action confirmations remain in chat when another bot screen appears. Ordinary menus and wizard prompts retain their existing replacement and cancellation behavior. Receipt buttons preserve their source notice and retire only the active menu; existing callbacks remain supported. Context is isolated per update and authorization is revalidated before receipt callbacks.

No business rules, shared records or schema changed. Alembic head remains 0009. New regression coverage includes LT/EN/RU, private/group commands, permission failures, identity supplements, navigation and TRUSTED removal.

Verification: full PostgreSQL/Redis suite: 598 passed in 111.10s. Tests against the installed Docker package: 76 passed in 17.78s. Docker image safecheck:2.13.1 built successfully.

Permanent OVH deployment remains pending server access. This release updates the temporary testing runtime; it does not establish permanent hosting or guaranteed uptime.

Ruff lint/format (159 files), mypy (49 modules), pip check and operations regression tests (44 passed) also passed. A transient backup-verifier startup race was found and corrected: readiness now uses TCP, avoiding PostgreSQL entrypoint's temporary socket-only server. A fresh live backup was fully restored in an isolated verifier.

Live activation could not be verified: Telegram rejects the configured credential with TelegramUnauthorizedError. The deployment helper rolls back on failed health checks. A valid privately configured bot token is required before activation.
