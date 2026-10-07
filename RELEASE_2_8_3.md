# SAFECheck 2.8.3 — TRUSTED card wording

TRUSTED results omit the generic trustworthiness disclaimer in LT/EN/RU. Applies to both manual and actual TOP-derived TRUSTED. Clear/no-SCAM results retain the disclaimer; SCAM precedence and unknown-identity warnings remain unchanged. No changes to status policy, authorization, records, database schema or navigation.

Validation:96 focused result-card/TRUSTED/Telegram tests passed9.31s, including all three languages and known/unknown identities. Ruff check/format111 files, mypy38 modules and git diff check passed. Full387-test suite previously passed for2.8.2; not rerun for this presentation-only change.

Docker build, Alembic schema check and startup readiness passed. Live2.8.3 polling verified. Private backup /tmp/safecheck-before-trusted-card-20261005.dump; stoppedrollback safecheck-rollback-2-8-2 retained.
