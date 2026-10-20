# Professional moderation validation

2026-10-20; branch `safecheck-moderation-professional`, based on PR #43 commit
`29a0e827243c883927bb8188191460173bbb6d0b`.

## Results

- **2,173 tests passed in one complete run** (`347.95s`), with real isolated
  PostgreSQL 17.6 and Redis 7.4.5. The later KICK absence refinement and its two
  additional regressions passed in a **437-test final moderation run** (`64.86s`),
  overlapping the complete suite. Telegram is a stateful API double.
- **45 isolated operator tests passed**, including pinned source/session retention,
  exact native-target allowlisting, schema 0019 and distinct active/pending/
  processing/uncertain upgrade-stop explanations.
- Docker services used Unix sockets, `--network none`, and no published ports.
  No real Telegram credential/session was used in these tests.
- Ruff lint and format passed (319 Python files); mypy passed (99 application
  source files); compileall and `git diff --check` passed.
- 17 warnings in the complete run are the existing Alembic `path_separator`
  deprecation notices, not test failures.
- The first broad run exposed two old migration snapshot assertions comparing
  entire row tuples after an additive column. They now compare every original
  column and value. Both were rerun successfully and the complete run above is
  green. No retained-data assertion was removed.

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

Owned MUTE → BAN/KICK, replacement rejection/timeout/RetryAfter, exact MUTE
signatures, release fencing, old controls, external-ban races, duplicate KICK
without another native write, and concurrent PostgreSQL replacement requests
are covered. Schema 0019 retains rows, matches model metadata and refuses to
discard used replacement links. See [replacement policy](SANCTION_REPLACEMENTS.md).

## Reproduce

Use the repository's locked Python environment, an isolated TEST_POSTGRES_URL
and TEST_REDIS_URL, then `python -m pytest -q`. Run Ruff and mypy per the existing
CI workflow. Do not point integration variables at a production database: the
PostgreSQL fixture creates/drops unique test schemas.

## Not a live Telegram acceptance result

Telegram uses a stateful API double. Tests do not establish client-side
Ephemeral visibility, second-account invisibility, actual personal/group
permission semantics, live flood control, or delivery timing. These require the
approved isolated bot/group after reviewing and applying schema 0019. A pinned
self-contained test updater is prepared separately as
`SAFECHECK_WARN_CONTROLS_TEST.py`; it has not been run on the VPS by this agent.
It preserves the test DB/cache/session and requires idle test work. No production
migration, restart, deployment or PR merge was performed.
