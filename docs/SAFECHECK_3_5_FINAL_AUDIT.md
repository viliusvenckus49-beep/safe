# SAFECheck 3.5 — final integration and security review

## Source, scope and independence

Fresh GitHub verification on 2026-10-19 found PRs #31, #32, #33, #35, #36, #37
and #38 open, unmerged and individually mergeable. Every incremental comparison
was ahead-only, with its merge base equal to the preceding PR's current HEAD.
Their final push and PR quality runs had succeeded. These were baseline evidence,
not proof of correctness. Actual services, routes, transactions, migrations,
worker lifecycle and transport code were read again, including the earlier audits.

Stage 3.5 base: PR #38, branch `auto-ads-3-4-20261019`, HEAD
`179d5af5fba51fde63c81b40079b4f6112749cde`, tree
`f83d69281e5c9c89c4f92891ec15c798b3b5407f`. The isolated local base has the
same tree. Audited application subtree: `626113ff0483a73e14bb1700f8628a7637b05d1a`.
Final audited source/remote HEAD and completed CI run URLs are recorded
in this stage's PR validation section; compare trees, not local/API commit IDs.

The second review pass examines the final patch separately from implementation;
this is not external certification. No agents, live Telegram transport, production
credentials, VPS changes, production SQL changes, PR merges or real ad activation
were used. Unrelated user worktree modifications were preserved.

## Confirmed findings and corrections

| ID | Severity | Cause and effect | Correction and regression |
| --- | --- | --- | --- |
| S35-1 | HIGH | `serve` awaited only polling. A required group/ADS task could fail while the bot remained running; an unexpected planner/coordinator exit could also disappear silently. | Supervise all required tasks, propagate failures/unexpected completion, join peers, and exit nonzero for the existing Docker restart policy. Real-SQL child-loop regressions cover return, error and cancellation; main lifecycle tests cover both required workers. |
| S35-2 | MEDIUM | Poll/group heartbeats could report healthy despite a dead or stuck ADS planner, dispatcher or coordinator. | Require three additional ADS progress timestamps in new runtime health files. Emit only after actual successful work/read boundaries; throttle writes. Legacy health-file readers remain compatible. Tests expire each signal independently and verify failed planning cannot refresh it. Reconciliation reports progress between bounded per-group checks, preventing false stale signals during long scans. |
| S35-3 | MEDIUM | Awaiting an already-failed worker in `finally`, or one failed resource closer, skipped later cleanup and could mask the original failure. | Cancel/join tasks with exception collection, attempt all resource closes with bounds and redacted diagnostics, preserve the primary error. Tests inject closer failure and verify storage, isolation and bot cleanup still happen. |
| S35-4 | MEDIUM | Directly cancelling aiogram's `start_polling` parent left internal polling/stop-signal children unjoined; update handlers could outlive DB shutdown. | Use public `stop_polling` first, then bounded update draining/cancellation before resource closure. Native aiogram smoke reproduced the orphaned children before correction. Tests assert no pending tasks and that updates finish before storage/DB shutdown. |
| S35-5 | MEDIUM | Profile membership checks used unbounded `gather`; one 100-group request started 100 concurrent Telegram calls. | Share an eight-slot semaphore per bot across profile requests, retaining real membership queries, counts and released SQL transactions. PostgreSQL regressions cover 100 groups and three concurrent profiles; the original implementation failed the concurrency assertion. |

No confirmed CRITICAL finding remains. All five confirmed runtime findings above
are fixed. No uncorrected HIGH finding was identified in the reviewed paths.
This is not a claim of exhaustive absence of defects.

The operations acceptance harness also needed current-image selection, the current
transport injection signature, and a readable copy of public entrypoint code:
this workspace materializes source files with mode 0600, unlike a normal Git
checkout. Only disposable harness configuration was changed; production Compose,
Dockerfile, deployment CLI and secret files were not changed.

## Cross-module review and preserved boundaries

| Component | Reviewed boundary and automated evidence |
| --- | --- |
| Identity / profiles | Numeric IDs remain authority; username changes retire previous ownership. Activity uniqueness uses `(chat_id,message_id)` and excludes commands/bots. History is observed, not invented. Profile HTML and LT/EN/RU formatting retained; only membership concurrency changed. |
| REP / SCAM / TRUSTED | Existing identity/admin write boundaries, approval/replay rules, SCAM conflict checks, manual versus role-derived TRUSTED and owner-only grants remain intact. Existing native routing, reciprocal-vote, approval, admin and trusted regressions run unchanged. |
| Local moderation | Global admins do not acquire local Telegram permissions. Numeric targets, current actor/bot rights, protected admins, durable actor-bound confirmation, SCAM-protected release and native result validation remain unchanged. BAN/MUTE/expiry/KICK recovery regressions retain actual SQL state assertions. |
| ADS content | Shared text/entities/media/button validation and preview/save, immutable version author, optimistic conflicts and history/restore retained. Native request shapes preserve UTF-16 and custom emoji metadata. Internal names never become published content. |
| Publication | SQL intent precedes API writes; ordered ACL/policy/campaign/schedule/rate locks are short and do not span Telegram calls. Leases/fences, unique slots, immutable pinning, bounded pool and retry rules unchanged. |
| GLOBAL ADS | Snapshot versus FUTURE consent, stable managed chat IDs, fresh local/global/bot authorization, tombstone exclusions and durable bulk items remain separate from shared content. Per-group failures do not roll back other items. |
| Customer self-service | Active numeric grant, not token possession, authorizes every operation. SQL-bound draft scope, one active client, hashed rotating/revocable links and deadline checks remain in the final write boundary. Customers cannot call schedule/policy setters. |
| Redis / runtime | SQL remains authoritative for grants, drafts, schedules and attempts. Redis supports FSM/isolation; cold startup still fails closed if configured Redis is unavailable. Required worker failure now terminates polling instead of staying superficially healthy. |

`test_stage35_integration.py` adds a real PostgreSQL path combining approved REP,
SCAM, TRUSTED, activity, a confirmed local sanction, customer deep link/edit/preview/
save or revocation, two publication slots and persisted statistics. It verifies
complete protected-table snapshots, unchanged schedule settings, pinned old content
for the already-created attempt and new content for the next one. Telegram is a
request double. Existing native UI tests exercise every major user flow in all
three locales; they are not replaced by test-count-only assertions.

## Recovery, delivery and statistics conclusions

- All nine intervals are fixed UTC durations. Europe/Vilnius input rejects
  nonexistent/ambiguous local times; end is exclusive. Content changes do not reset
  dates or intervals. Old missed slots are skipped, not replayed as a burst.
- CLAIMED recovery gets a new token/fence. Ambiguous SENDING recovery becomes
  UNCERTAIN and cannot be turned into SENT by a stale worker. A lost API response
  is not safe to replay blindly. Pause/Stop cannot retract an in-flight request.
- Confirmed message IDs survive partial album/companion outcomes. SENT requires
  native Telegram success data for every required part. PARTIAL is a subset of
  non-SENT outcomes with confirmed IDs, not an extra disjoint total.
- Campaign and per-group statistics derive from stored attempts, schedules and
  parts; historical assigned groups differ from effective active groups and from
  schedule rows. A group active in one campaign and paused in another can appear
  in both state counts. No view, click or audience estimates are fabricated.
- Existing concurrent worker, SQL rollback, Redis outage, permission revocation,
  bulk replay, FUTURE-policy race, shared FloodWait, slow-group fairness and
  restart tests remain part of the complete suite. Production rate budgets stay
  positive; only explicitly isolated development benchmarks set them to zero.

## Validation ledger

Final local/CI evidence is filled after executing the final source, not inferred
from earlier stage results. The initial integrated suite passed **1,818 tests**
before the final two profile-concurrency regressions were added.

| Final local check | Result |
| --- | --- |
| Complete final pytest with isolated PostgreSQL and Redis | **1,820 passed**, no skips, 17 Alembic configuration deprecation warnings, **350.21 s** |
| New Stage 3.5 automated regressions | **28 passed**: 24 runtime, two cross-module PostgreSQL, two profile-concurrency cases |
| Ruff / format / mypy / Python compilation | Passed; 293 formatted files, 93 typed source files |
| Alembic upgrade and schema drift | Passed on fresh PostgreSQL and SQLite through 0016; migration retention/constraints included in full suite |
| Production Compose validation | Passed with dummy secret files and no runtime secret resolution |
| Final Docker build | Passed; review image `de915e332c2b4df38b8ac744e9893b1d8b96d3d54c311a6e102c4ddff715fa87` |
| Final packaged worker and whole-bot smoke | Both passed, read-only/no network/tmpfs; native aiogram polling and required loops stopped without pending tasks |
| Actual isolated backup/restore | Passed: 45 table fingerprints, 24 nonempty tables / 26 synthetic rows, sequences equal, schema 0016; checksum/private files, rescue backup and stale FSM clearing checked |
| Remote final-head CI | Exact run URLs, HEAD and conclusions are recorded in the PR validation section after completion; local results are not substituted for remote results |

CI now includes the packaged whole-bot smoke and the disposable Compose
backup/restore rehearsal in addition to the existing full quality/worker gates.
No real Telegram client acceptance or production/offsite restore is claimed.

Synthetic PostgreSQL/request-double benchmark, pool 8, three reported logical CPUs:

| Groups | Enqueue s | Dispatch s | Total s | Dispatch publications/s | SQL statements | Peak SQL connections | Process peak RSS KiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 0.146 | 0.512 | 0.658 | 19.55 | 754 | 8 | 210816 |
| 100 | 1.323 | 3.344 | 4.667 | 29.90 | 7414 | 8 | 212352 |
| 1000 | 12.429 | 37.382 | 49.811 | 26.75 | 74028 | 8 | 215680 |

Aggregate SQL elapsed time: 1.914 / 9.112 / 88.341 s, overlapping across workers;
it is not wall time. RSS is cumulative process high-water, not per-case allocation.
All cases asserted one SENT attempt and one delivery per synthetic destination.
These figures include SQL/network-loop overhead but exclude real Telegram latency
and rate waits. They show roughly linear query cost (~74 statements/publication),
not production capacity. At the default 20 messages/s ADS budget, 1,000 single-part
sends alone need at least 50 seconds; media/companion and other traffic add cost.
No production-sized PostgreSQL history, VPS resource sizing, Redis QPS/memory
profiling or real Telegram throughput was established by these tests.

## Residual risks and disposition

| Severity | Remaining condition | Disposition |
| --- | --- | --- |
| MEDIUM | Real Telegram rendering, emoji entitlement, old-client copy/share, permissions and transport compatibility are untested live. | Blocks PRODUCTION READY; perform the private staging matrix in the readiness document. |
| MEDIUM | Target VPS capacity, actual schema/data volume, offsite retention/recovery ownership and deployment secrets have not been inspected in this task. | Blocks LIVE authorization. Disposable backup/restore proves tooling, not a backup of production. |
| LOW | aiogram's separately tracked update-task set is an internal API. | Bounded shutdown uses it defensively; native packaged smoke is now a CI gate. Retest on dependency updates. |
| LOW | Redis is required at cold start; UI/FSM may be unavailable during an outage. | Intentional fail-closed behavior; SQL jobs/history survive. Recovery tests and runbook document it. |
| LOW | Health becoming unhealthy does not itself make Docker restart a live process. | Required task exits now fail the process; stuck/database-outage signals need existing external monitoring/operator intervention. Test monitoring before LIVE. |
| INFORMATIONAL | Telegram and local SQL cannot share an atomic exactly-once transaction; authority can change after the final remote read. | Keep UNCERTAIN, fencing, audit and manual reconciliation. Do not promise impossible guarantees or recall of in-flight requests. |

No new schema change was needed: HEAD remains **0016**, with 0011→0016 additive
stage migrations unchanged. Do not introduce a cosmetic 0017. Merge in dependency
order only after review and rerun final integration against the updated base:
**#31 → #32 → #33 → #35 → #36 → #37 → #38 → Stage 3.5**.
Do not retarget later PRs blindly after squash/rebase; compare cumulative trees and
migration heads. No merge or deployment was executed.

Readiness, staging cases and operational gates are specified in
[production readiness](SAFECHECK_3_5_PRODUCTION_READINESS.md),
[deployment runbook](SAFECHECK_3_5_DEPLOYMENT_RUNBOOK.md) and
[backup/restore](SAFECHECK_3_5_BACKUP_RESTORE.md).
