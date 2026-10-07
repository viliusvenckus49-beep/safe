# SAFECheck 2.8.2 — automatic identity directory refresh

The administrator user directory automatically prefers the currently observed Telegram numeric identity over an older username-only entry with the same normalized username. Existing database rows are covered immediately; rows and pagination totals share the same eligibility predicate. No schema migration or destructive merge is performed.

Historical username-only records remain available internally with their original REP, reports, SCAM and TRUSTED associations. Matching a current username cannot prove historical account ownership, so those judgments are not transferred to the observed numeric identity. Changing/removing an observed username can make the unresolved historical entry visible again. Active SCAM exclusions remain in place. TOP10 presentation remains without IDs. Previously sent Telegram panels refresh when reopened.

Review: checked correlated SQL aliases, NULL usernames, normalization, repeated observations, cross-session persistence, username reassignment, historical TRUSTED isolation and shared pagination filtering. General user directory only; existing observation, moderation, group backup and authorization policies preserved.

Validation: focused SQLite suite31 passed; baseline full Docker PostgreSQL/Redis suite386 passed72.51s; additional PostgreSQL directory regression passed3.68s. Final full Docker PostgreSQL/Redis suite387 passed74.24s, no skips. Ruff check/format111 files, mypy38 source modules, pip check, git diff check and source TODO/FIXME/token-pattern scan passed. Docker safecheck:2.8.2 built; Alembic schema check and application startup check passed; migration head0008 unchanged.

Deployment: live container update uses a private PostgreSQL backup and retains the stopped2.8.1 rollback container. Startup command remains python -m app.main; see README for configuration and Docker commands. Temporary hosting and actual Telegram phone rendering remain operational limits.

Live polling_started confirmed version2.8.2. Private backup /tmp/safecheck-before-directory-20261005.dump; stopped rollback safecheck-rollback-2-8-1 retained.
