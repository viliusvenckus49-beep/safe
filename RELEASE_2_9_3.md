# SAFECheck 2.9.3 — TRUSTED qualifies for TOP

Corrects overly narrow2.9.2 eligibility. Active manual TRUSTED, a current founder/moderator role or explicit administrator inclusion qualify for TOP. Explicit exclusion overrides automatic eligibility. REP approval alone never qualifies. Nonnegative scores, SCAM exclusions and limit10/ranking by score remain unchanged. Role eligibility uses numeric IDs, current Administrator grants and database revocations overriding bootstrap IDs; owner remains immutable. Manual/TRUSTED/role source does not depend on TOP, preventing circular qualification.

User-reported rokfeleriss28 has active manual TRUSTED, no explicit exclusion and nonnegative score/no SCAM; eligible without creating a synthetic inclusion/audit grant. Existing records are unchanged. Updated LT/EN/RU handbook and README. No schema changes.

Added tests for manual TRUSTED qualification, explicit exclusion priority, reinstatement, role revocation and SCAM exclusion. Initial63 focused existing tests passed7.50s; Ruff/mypy passed.

Final full Docker PostgreSQL/Redis suite402 passed73.95s, no skips. Updated old empty-leaderboard assertion to target exclusion because the independently TRUSTED founder remains eligible. Ruff check/format118 files, mypy38 modules, git diff check and Docker build passed. New image read-only check against live data confirms rokfeleriss28 appears.

Live2.9.3 polling and rokfeleriss28 in actual live TOP verified. Schema/startup checks passed; head0008 unchanged. Private backup /tmp/safecheck-before-trusted-top-20261005.dump; rollback safecheck-rollback-2-9-2 retained. Retired obsolete stopped2.8.0-.2 containers/images and unused build cache for space, current/recent rollbacks and all volumes preserved.
