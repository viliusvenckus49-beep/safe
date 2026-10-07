# SAFECheck 2.6.0

Implemented: active SCAM entries excluded from100-user administrator pages (rows and counts) and readable member exports. Existing user/member/REP/audit history remains stored. New active SCAM members do not enter backups; known numeric bans remain enforced. Unverified username-only entries use normalized current username matching solely for filtering. Removed status makes retained history eligible again.

Administrators can add SCAM by numeric ID/username without a reason via command or private target wizard. Optional supplied reasons retain validation; user reports, removal reasons and REP adjustments retain their rules. Empty SCAM reasons are stored as empty strings and displayed without an empty heading. No schema change.

SCAM registry button/command/callbacks private-only, including old group callbacks. Home administration button and group-management entry buttons removed; /admin and /groups keep existing authorization. Other navigation returns home. Information now explains /ask and private /report in all3languages. Slash menus remain start/ask/rep/report.

Architecture: eligibility SQL EXISTS filters in Repository and group services; shared identity lock serializes new member observation against scam addition. Existing service, callbacks, presentation and centralized LT/EN/RU catalogs reused.

Verification: full Docker pytest with isolated PostgreSQL and Redis — 309 passed in54.12s, no skips. Includes concurrent scam-add/member-observation, active/removed filtering, username case/history, reason-free admin commands/FSM, non-admin bypass, report reason preservation and menu/callback localization. Ruff check/format96files, mypy37modules, pipcheck, gitdiffcheck, hardcoded-token/TODO/FIXME scan and Docker build passed. Independent reviewer found no material security/SQL/concurrency issue; dedicated private-registry wording fixed the reported UX issue.

Deployment: safecheck:2.6.0, DB head0007 unchanged. Alembic check and startup readiness passed. Standard startup: python -m app.main; see README for Docker and credentials. No additional user credentials needed. Real-phone acceptance remains the user's final interface check; no real target was added/banned for tests.
