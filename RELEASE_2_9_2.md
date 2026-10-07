# SAFECheck 2.9.2 — administrator-approved TOP membership

TOP eligibility now requires an existing explicit administrator inclusion decision (TopVisibility.visible=true). Approved REP and legacy ledger entries alone never enroll a person or grant TOP-derived TRUSTED. Existing explicit decisions remain; implicit entries disappear without deleting scores/history. Manual TRUSTED and role-based TRUSTED remain independent and do not enroll people.

Rank only eligible included identities with nonnegative score and no active SCAM matching numeric identity or unresolved current username. Apply all filters before ordering/limit10. Actual eligible TOP still supplies automatic TRUSTED. Administrator inclusion/exclusion UI, permissions, audits, request idempotency and ranking by score remain unchanged. Updated LT/EN/RU admin handbook.

Review: verified shared ranking/profile source, no implicit backfill, SQL correlated aliases and NULL usernames, SCAM precedence, no-score explicitly included members, exclusion/reinclusion history, authorization and immutable reputation preservation.

Validation:57 focused services/admin-REP/TRUSTED tests passed6.77s. Ruff check/format117 files, mypy38 modules, git diff check and source TODO/FIXME/token-pattern scan passed. No schema changes.

Full Docker PostgreSQL/Redis suite400 passed73.13s, no skips. Docker build, schema/startup checks passed; live2.9.2 polling and actual TOP explicit-approval/nonnegative invariants verified. Head0008 unchanged. Private backup /tmp/safecheck-before-top-approval-20261005.dump; rollback safecheck-rollback-2-9-1 retained. Obsolete stopped2.7.1/2.7.2/2.7.3 rollback containers/images and dangling failed-build image retired to reclaim space; current/recent rollbacks and all volumes preserved.
