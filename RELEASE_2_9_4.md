# SAFECheck 2.9.4 — TOP identity deduplication

A current observed numeric identity supersedes an old username-only record with the same username in TOP ranking. Filter before ordering/limit10 so slots are filled by eligible distinct entries. Preserve all historical records, scores and TRUSTED decisions; do not transfer them on username matching. Numeric exclusion cannot reveal the superseded placeholder as a bypass. Username change/removal can expose an unresolved historical entry again rather than falsely binding past judgments.

Confirmed live issue: cart3lis had a username-only entry and a current numeric moderator entry. Fix applies automatically to all matching duplicates, without manual data rewriting. Existing TRUSTED qualification, SCAM/negative exclusions and admin authorization unchanged.

Validation:31 focused TRUSTED/admin-REP tests passed5.72s. New regression covers duplicate placeholder/current moderator, historical manual designation retained, explicit exclusion cannot reveal old row and username change. Ruff check/format119 files, mypy38 modules, git diff check passed. No schema changes.

Full Docker PostgreSQL/Redis suite403 passed73.82s, no skips. Docker build/schema/startup checks passed. Live2.9.4 polling verified; cart3lis has exactly one actual TOP entry, with numeric identity. Head0008 unchanged; private backup /tmp/safecheck-before-top-dedup-20261005.dump and stopped rollback safecheck-rollback-2-9-3 retained.
