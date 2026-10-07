# SAFECheck 2.7.0

Recovery entry is shown only in the private home menu. Admin commands /add_trusted and /del_trusted accept numeric IDs, usernames or replied-to users. Manual TRUSTED designations persist independently of dynamic TOP10 membership. All current TOP10 entries are automatically TRUSTED; leaving TOP removes only automatic status. Active SCAM takes precedence. Existing absence-of-SCAM warnings remain: TRUSTED is a designation, not a guarantee. Slash command discovery remains start/ask/rep/report.

Architecture: thin command handlers, centralized LT/EN/RU presentation and administrator handbook, shared service authorization, repository queries, immutable request/action history plus mutable current designation. Identity metadata is refreshed under the shared identity lock before authorization and SCAM checks. Replayed requests cannot restore revoked grants and return an accurate replay response. No reputation or report policy changes.

Database: additive Alembic0008 creates trusted_designations and trusted_actions with foreign keys, target index and unique request key. Private backup taken before deployment; fingerprints of all23 existing tables unchanged after upgrade. Alembic check and startup readiness passed.

Verification: full Docker PostgreSQL/Redis pytest:322 passed in64.85s, no skips. Includes grant/revoke/replay, concurrent requests, permission checks, refreshed identity races, username changes/reuse, SCAM priority, TOP transitions, three-language command/lookup output, recovery menu visibility and migration preservation. Ruff check/format99files, mypy37modules, pipcheck, gitdiffcheck, source TODO/FIXME/token scan and Docker build passed. Independent security review approved the final fixes.

Deployment: safecheck:2.7.0 started; structured polling_started confirms version2.7.0, container running, DBhead0008. Start with python -m app.main after alembic upgrade head; see README for Docker and environment setup. No new credentials required. Existing hosting is a temporary development runtime; real-phone UX acceptance remains with the user. No real users were granted TRUSTED for tests.
