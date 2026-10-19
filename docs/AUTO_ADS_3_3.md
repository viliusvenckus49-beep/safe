# AUTO ADS 3.3 — GLOBAL ADS

Review branch: `auto-ads-3-3-20261019`. Dependency: PR #36 at
`891f4a52d0bcae3314dd5b5d4a08c53ecd2ffe4c` (audited 3.2 tree).
This is review-only work: no production deployment, database access, live group
activation, or Telegram publication was performed.

## Campaigns and group schedules

The existing 3.1 content/version model and 3.2 publication state machine remain
the source of truth. Each `(campaign_id, chat_id)` has its own `ad_schedules`
row, interval, UTC dates, status, next slot, and retained attempt history.
Editing campaign content does not mutate any of these schedule fields. Every
attempt pins an immutable content revision before dispatch.

The native private Telegram UI supports LT/EN/RU, paginated campaigns, managed
groups, assigned/removed schedules, operation results, and publication history.
Existing content callbacks and the unambiguous 3.2 single-group callbacks are
retained. Older campaign-only schedule buttons redirect to group selection when
the campaign now has several schedules.

Assignment modes:

* `SINGLE`: select exactly one group; other current assignments are removed.
* `SELECTED`: select several groups; other current assignments are removed.
* `EXISTING`: snapshot eligible existing groups, add authorized assignments once.
* `FUTURE`: the same snapshot plus durable periodic reconciliation of new groups.

An eligible group is explicitly approved, enabled, not removed from SAFECheck,
and a Telegram group/supergroup. Seeing a chat once does not make it eligible.
Global AUTO ADS authority never bypasses current local Telegram administrator
rights or bot publication rights. Rights are checked outside SQL transactions
at preflight and again when applying individual items. Publication rechecks
authority and bot rights through the existing 3.2 safeguards.

All explicit assignments initially create DRAFT schedules. The FUTURE mode
alone does **not** activate them. Future automatic activation needs a separate,
versioned confirmation with valid defaults and a successfully previewed current
content version. Reconciliation checks current group, actor, bot, campaign, and
policy eligibility again. Unauthorized groups remain unassigned. A campaign-wide
Pause/Stop disables future automatic activation; a per-group action does not.

Removed assignments retain their schedule and all publication history with
`excluded=True`. Reconciliation and EXISTING/FUTURE never resurrect exclusions.
An explicit SINGLE/SELECTED assignment may restore one as DRAFT.

## Defaults and confirmations

Defaults are snapshots for newly assigned groups, not live inherited settings.
Changing defaults leaves existing schedules untouched and disables future
automatic activation until it is reconfirmed. Individual changes mark explicit
override fields. Applying defaults to existing schedules requires confirmation,
preserves overrides, and pauses active schedules. A separate reset-to-defaults
action explicitly clears overrides. Both require a subsequent Start to publish.
Legacy 3.2 schedules migrate with all three fields marked as explicit overrides.

Selection wizards, target snapshots, confirmations, and per-group results are
stored in PostgreSQL. Operation IDs are identifiers, not bearer authorization.
Every operation is bound to its numeric actor ID, campaign, policy version,
expected schedule versions, and request key. Start also pins the previewed
content revision. Campaign-wide confirmations reject a changed membership
snapshot, rather than silently affecting a different group set.

Selections expire after 30 minutes; prepared action confirmations after 15
minutes. Confirmed RUNNING work survives expiry/restart and continues checking
current authorization. Duplicate confirmations return the same operation.
Per-item changes and successful item completion commit atomically. Expired
90-second item leases can be reclaimed without repeating committed mutations.
Partial denial/failure remains visible per group and does not roll back others.
The pending-operations UI reopens saved selections without relying on FSM state.

## Transactions, fairness, and delivery

Lock order is shared ACL boundary, optional campaign assignment policy,
campaign, selected schedule, and finally global/chat rate gates. Multiple
workers can hold shared ACL/campaign locks together. Existing ACL/content
writers still take the exclusive identity boundary, so revocation and content
changes cannot bypass authorization/pinning. No SQL transaction or row lock is
held across Telegram network calls.

Planning uses campaign-interleaved due candidates and per-schedule transactions
with PostgreSQL `SKIP LOCKED`. Dispatch records policy/schedule fairness markers
in short transactions. Claiming serializes only the brief policy dispatch
decision, not network execution. A bounded pool (default 8, configurable 1–32)
keeps a slow group from occupying every worker. Planning and GLOBAL ADS
coordination run independently of publication network requests.

Persistent SQL budgets default to 20 messages/second globally for ADS and 20
messages/minute per group across campaigns. Albums charge their message count;
their separate button message consumes another reservation. FloodWait advances
the shared durable deadline monotonically. Budgets are coordination limits,
not a claim that Telegram will permit that rate; other bot traffic and Telegram
limits still matter. Zero budgets are permitted for isolated tests/development
only and rejected in production settings.

The 3.2 UTC fixed-duration intervals, Vilnius DST validation, missed-slot policy,
expiry checks, lease/fencing, SENDING-before-API persistence, strict message ID
validation, and partial-album state remain in use. A network-ambiguous send is
UNCERTAIN, never inferred to be SENT and never blindly repeated. A Pause/Stop
can cancel waiting work but cannot undo a Telegram request already in progress.
No universal exactly-once guarantee is made.

Future reconciliation uses a persisted chat-ID cursor, bounded pages, and
periodic rescans; it does not depend on a single Telegram update. The worker
resumes persisted RUNNING operations after a process restart. Redis remains
part of the existing application architecture; it is not the only schedule,
policy, lease, or rate-budget store.

## Migration 0015

Parent revision: `0014`. Adds:

* `ad_assignment_policies`: versioned mode, defaults, explicit future activation
  consent, reconciliation checkpoint, dispatch fairness marker.
* `ad_global_operations` and `ad_global_operation_items`: actor-bound durable
  confirmations, target versions, per-group outcomes, tokens, leases, audit.
* `ad_chat_publication_gates`: shared per-chat publication budget.
* Schedule exclusion, override fields, dispatch marker; global rate marker;
  indexes for due schedules, attempts, reconciliation, and operation recovery.

Existing campaigns get a derived SINGLE policy with automatic activation OFF.
No existing schedule, content/version, publication history, REP, SCAM, TRUSTED,
profile, or moderation row is rewritten. Old migrations are unchanged. Downgrade
refuses to discard any new-table data, including backfilled policies; use a
reviewed forward rollback after adoption. Rehearse backup/restore and upgrade on
a production-sized copy before separately approving deployment. This document
does not authorize or perform that deployment.

## Fresh review findings and fixes

* **High — stale mass-action target snapshot:** a newly added/changed schedule
  between preparation and confirmation could make the displayed group set
  inaccurate. Campaign-wide confirmations now verify the complete ordered
  membership/version snapshot; individual items retain version/fencing checks.
* **Medium — duplicate preparation race after Telegram preflight:** concurrent
  requests could reach the unique request key at once. Preparation now rechecks
  the key under the policy lock and returns the existing actor-bound operation.
* **Medium — malformed authorized callback values:** truncated values could raise
  IndexError/TypeError. Both new input/callback routes reject these safely, with
  owner as well as unauthorized callback regression tests.
* **Medium — restart usability:** a persisted selection could outlive its FSM
  context without an entry point. Pending operations now resume the SQL wizard.
* **Medium — global statistics:** schedule totals could count the same group
  twice across campaigns. Distinct group counts and assignment counts are now
  separate, with a regression test for overlapping campaigns.

Review and regression tests were performed in this working session. This is
not an external security certification or evidence of live Telegram delivery.

## Validation and synthetic capacity

Automated coverage includes four assignment modes, overrides, individual/bulk
controls, exclusions, consent and expiry, actor/bot/global revocation, stale and
foreign callbacks, SQL operation replay/restart, multiple workers, fair claims,
rate limits, shared content, migration retention, and existing SAFECheck tests.
PostgreSQL tests demonstrate that independent schedules can acquire shared ACL
locks concurrently. Real PostgreSQL/Redis services are disposable test services.
Telegram API calls in these tests use doubles.

Isolated load run: Python 3.12, PostgreSQL 17-alpine, 8 worker slots, 2-core
cgroup CPU quota, 8 GiB memory limit (3 visible logical CPUs). ADS rate budgets
were disabled for this synthetic capacity measurement, with no real API calls.

| Groups | Enqueue | Worker delivery | Total | Worker publications/s |
| --- | ---: | ---: | ---: | ---: |
| 100 | 1.033 s | 2.522 s | 3.555 s | 39.65 |
| 1,000 | 10.874 s | 26.677 s | 37.551 s | 37.49 |

Both cases verified the exact number of SENT database outcomes and distinct
destination IDs without duplicates. These are local SQL/request-double capacity
results, **not live Telegram throughput**. Shared CI runners may be slower.
Actual command results and final full-suite counts are recorded in the PR.

## Remaining limits and 3.4 readiness

* Live Telegram rendering, permissions, Custom Emoji availability, and delivery
  still need a separately authorized staging trial. Mock success does not prove
  Telegram acceptance. Existing Custom Emoji fallback behavior remains.
* Telegram authority can change immediately after a successful rights check;
  final API denial and UNCERTAIN handling remain essential.
* Group selection preflight is concurrency-bounded but inspects a full selected
  snapshot; very large assignments can take time and trigger Telegram limits.
  Future reconciliation is paged. The synthetic load run measures publication,
  not thousands of real administrator-rights API calls.
* Brief global/chat rate locks and per-campaign dispatch policy locks remain
  intentional. Production DB latency/traffic and long-running operation fairness
  should be measured before increasing pool size or campaign scale.
* Rollback is deliberately conservative once GLOBAL ADS data exists. A backup,
  forward rollback plan, and dependency-order deployment are required.

The content/schedule separation, immutable versions, tombstones, durable audit,
and versioned operations support 3.4. That next stage must add separate numeric
client-ID authorization on every editing path, scoped content-only permissions,
secure hashed/revocable deep-link tokens, expiry read-only behavior, and tests
for stale buttons and revoked clients. No client self-service, deep links,
payments, or purchase flows are implemented in 3.3.
