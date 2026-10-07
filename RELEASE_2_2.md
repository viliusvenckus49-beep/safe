# SAFECheck 2.2 — 2026-10-04

Implemented: ordinary group message silence; existing-message callback navigation and FSM prompt panels; observed Telegram display names in TOP; administrators' command scope including add_sc/del_sc and public commands; verified managed-group registration; known numeric ACTIVE SCAM target ban outbox on confirmation/registration/join/message; bot-observed member JSON export; private explicit and revocable recovery consent; administrator invitation preview/confirmation; durable recovery delivery.

Architecture: app/bot/screens.py centralizes editable panels; group_service models/services + seven tables in migration0004 handle group state, member observations, consent, campaigns and leased outboxes; app/bot/groups.py provides Telegram journeys; app/bot/group_runtime.py executes bounded background jobs; typed authorization/HTML escaping/secret handling remain centralized.

Independent reviewer approved source/security after fixes: coordinated registration/scam-create transactions, fresh pre-send eligibility, retry delay preservation, successful-ban rearming on joins, stale consent cache invalidation. Final runtime QA exposed a lazy-load MissingGreenlet after eligibility refresh; fixed by explicit async campaign lookup and reran complete suite.

Executed verification:
- Full isolated PostgreSQL/Redis Docker suite: 166 passed, no skips, 26.59 seconds.
- Ruff lint and formatting passed; mypy passed for25 application files; pip check passed.
- Migration history and SQLite upgrade/check/downgrade/re-upgrade tested in suite.
- Source scans: no TODO/FIXME or suspected real Telegram tokens in current code.
- Docker safecheck:2.2 build succeeded.
- Private database backup outside repository created before deployment.
- Actual PostgreSQL upgradedhead0004; Alembic check no new operations; startup --check passed.
- Live bot logs version2.2.0 polling_started and successful Telegram polls.
- Existing test group registered only after live Telegram verified bot administrator/restriction rights; actual group type is basic group.
- Live getMyCommands verified numeric administrator's add_sc command present.

Operational limits: permanent/preemptive blocking requires a supergroup. Basic group outcomes depend on Telegram; permission/API failures are retained for retry or inspection and cannot be represented as success. Username-only scam records cannot safely trigger numeric bans; group owners/admins may be unbannable. Removing SCAM status does not automatically unban. No real-person ban was manufactured for testing; real moderation outcome remains to validate in a suitable test supergroup.

Member backup includes only observed people, not guaranteed full membership. No Telethon scraper/user-account session or mass-inviter is integrated. Recovery messages require private contact, observed membership and explicit consent; admin confirms every campaign. Delivery is at-least-once: crash-after-send-before-record may duplicate, and later unsubscribe cannot recall an in-flight message. Old historical bot panels are not automatically deleted; future navigation edits panels when possible.

Hosting remains a test environment, not guaranteed permanent uptime. Previously exposed BotFather token replacement remains a production deployment task. No credentials/private database dumps are shipped in source archive.

Startup: configure .env as README then docker compose up -d --build. Existing deployments: stop polling before migration, alembic upgrade head, start one polling process. Group setup: grant bot administrator restriction permission and configured numeric admin sends /start in group; private /groups manages observed backups and recovery. Users opt in through /recovery or main menu.
