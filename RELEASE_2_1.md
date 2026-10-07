# SAFECheck 2.1 verification — 2026-10-03

Implemented: all new user +REP/-REP require administrator approval; private paginated REP queue with human references; administrator positive/negative adjustments and current-counter reset with reason/preview; immutable legacy events plus adjustment ledger; administrator TOP inclusion/exclusion, negative-total exclusion and Telegram profile links; consistent Lithuanian HTML cards with restrained bold formatting.

Global reset requested by owner was executed through numeric-authorized service. Private backup created outside repository. Verified all 3 stored users have score/positive/negative counters zero, and zero pending requests. Original events remain; pending requests are rejected rather than later restoring old REP. Global reset and decisions are idempotent and audited.

Verification executed:
- Complete Docker suite using isolated PostgreSQL/Redis: **138 passed, no skips**, 22.45 seconds.
- Ruff lint and format checks passed; mypy passed for 19 application files; pip check passed.
- New migration chain upgrade/check/downgrade/re-upgrade tested on SQLite; historical reputation preservation upgrade regression passed.
- Actual live PostgreSQL upgraded to 0003; Alembic check reported no new operations.
- Docker safecheck:2.1 build passed; live readiness --check passed.
- Live bot logs show version2.1.0 polling_started and repeated successful Telegram polls.
- Independent reviewer examined numeric authorization, preview nonce expiry, immutable history, aggregation snapshots, locks and global reset idempotency. Original fixes retested in full suite.

Deployment: configure environment as README, then docker compose up -d --build. Existing deployments should stop polling before migration; use alembic upgrade head and restart one polling process. No real credentials or private database backups are shipped in source artifacts.

Limits: no permanent hosting/SLA established by this test environment. Full real Telegram photo/document/report/moderation acceptance remains a user journey to verify; automated dispatcher tests cover flows but mock Telegram delivery. Username-only identity cannot be resolved reliably by Telegram API. Numeric profile links are subject to Telegram access/privacy rules. Previously exposed BotFather credential replacement remains required for permanent deployment.
