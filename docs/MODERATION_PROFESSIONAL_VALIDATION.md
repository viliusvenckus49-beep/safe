# Professional moderation validation

2026-10-20; branch `safecheck-moderation-professional`, based on PR #43 commit
`29a0e827243c883927bb8188191460173bbb6d0b`.

## Results

- **2,155 distinct collected tests covered successfully**, across partitions.
  The completed prefix covers 1,203 tests before `test_moderation_messages.py`;
  the remainder covers 952 tests from that file onward (952 passed).
  The initial complete-run attempt also passed 70 overlapping tests from the
  latter partition; outdated UI expectations were repaired and rerun there.
- **218 final targeted routing/SQL/privacy tests passed** after callback/menu
  refinements. This overlaps the collection above, not 218 additional tests.
- Real isolated PostgreSQL 17.6 and Redis 7.4.5, using Unix sockets and Docker
  `--network none`, no published ports. No real Telegram credential/session.
- Ruff lint and format: passed (315 Python files formatted).
- mypy: passed (99 application source files).
- `git diff --check`: passed.
- Remaining warnings were existing Alembic `path_separator` deprecation notices.

## Covered behavior

Real dispatcher + SQL paths test public WARN -> private management -> exact WARN
confirmation -> audited cancellation; all-warning reset and stale confirmations;
user/group isolation; actual current actor/bot rights; protected targets; 3 WARN
-> 24-hour owned MUTE and only-then consumption; individual/all reset races in
PostgreSQL; individual reset above/below the automatic threshold; preservation
of consumed-warning history and active automatic MUTE; MUTE/UNMUTE and BAN/UNBAN;
existing foreign/changed restrictions; repeated commands/buttons; lease fencing,
timeouts, RetryAfter and no unknown-write replay; LT/EN/RU HTML; exact command
names; explicit Ephemeral rejection -> functional DM confirmation; ambiguous
private delivery -> later public success; pending/private receipts versus public
success and original-message control retirement; rejected APPLY orphan RELEASE
cancellation with audit.

Migration checks execute actual Alembic SQLite upgrades/checks and PostgreSQL DDL:
old warning rows/confirmations survive 0018, selected warnings keep foreign-key
integrity, nullable fields retain legacy records, and populated new metadata
blocks destructive downgrade. Unused additions may be removed while older
history-preservation guards remain in effect. Production deployment whitelists
were deliberately not expanded.

## Reproduce

Use the repository's locked Python environment, an isolated TEST_POSTGRES_URL
and TEST_REDIS_URL, then `python -m pytest -q`. Run Ruff and mypy per the existing
CI workflow. Do not point integration variables at a production database: the
PostgreSQL fixture creates/drops unique test schemas.

## Not a live Telegram acceptance result

Telegram uses a stateful API double. Tests do not establish client-side
Ephemeral visibility, second-account invisibility, actual personal/group
permission semantics, live flood control, or delivery timing. These require the
approved isolated bot/group after reviewing and applying schema 0018. No VPS
installer was created or run for this branch. No production migration, restart,
deployment or PR merge was performed.
