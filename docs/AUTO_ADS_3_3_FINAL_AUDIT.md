# AUTO ADS 3.3 — final security, reliability and performance audit

## Scope and source

Freshly reviewed PR #37 HEAD:
`d5deed14377644d9c95a37534e1f4d6a7b3746cf`.
GitHub HEAD was checked before review; it matched the supplied commit. The local
implementation commit has the identical Git tree
`77622b6f19944fbcf3954e56d52b02b1a05fbd28`.

Read the actual GLOBAL ADS services, UI routes, publication transitions and
worker, content validation/transport, models, 0013–0015 migrations, authorization,
and the 3.1, 3.2 and 3.3 audit documents. Earlier CI was treated as a baseline,
not evidence that the reviewed code had no defects. Findings below came from a
new code review and reproductions. This is not a third-party certification.

No production credentials, VPS, production database, real group activation, or
Telegram network publication was used. The unrelated uncommitted user file in
the original worktree was preserved. No 3.4 features were implemented.

## Findings, root causes, corrections

No CRITICAL or HIGH defect was confirmed in the reviewed paths. This does not
establish absence of all defects. Eight justified findings were corrected:

| ID | Severity | Cause and impact | Correction and evidence |
| --- | --- | --- | --- |
| F1 | MEDIUM | After a lease was recovered as UNCERTAIN, a late worker's response was correctly rejected by SQL fencing, but its structured log still reported the local API result as successful. Localized log status also impeded stable monitoring. | Log the persisted canonical attempt status separately from `telegram_status`. A late-success regression verifies SQL/part remain UNCERTAIN and the log does not claim SENT. No delivery state-machine semantics changed. |
| F2 | MEDIUM | Statistics counted terminal outcomes but omitted confirmed partial delivery, hiding an album whose companion failed, was cancelled, or had an unknown reply. | Count distinct non-SENT attempts with at least one retained Telegram message ID, at group and campaign/global scope. The UI explicitly labels this as a subset of existing outcomes; it must not be added to those totals. Includes incomplete album replies and never counts a fully SENT attempt as partial. LT/EN/RU updated. |
| F3 | MEDIUM | Default interval/date inputs immediately changed future assignment policy and disabled future activation consent, with no separate confirmation. This did not rewrite existing schedules, but the policy change could be unintended. | Stage an actor-bound, versioned SQL READY operation using the existing CHANGE action. Only confirmation changes defaults and invalidates future activation consent. Duplicate confirms are idempotent; competing administrator confirms cannot overwrite newer policy. No schema/action enum migration is needed. |
| F4 | MEDIUM | GLOBAL ADS schedule change confirmations printed raw UTC ISO date values, although administration uses Europe/Vilnius. An administrator could approve a time after misreading the zone. | Render start/expiry instants through the existing Vilnius formatter with an explicit offset. Storage remains UTC, and DST resolution remains unchanged. Both date regressions failed before this rendering correction. |
| F5 | LOW | Publication audit events used `campaign_id=0` rather than the actual campaign, weakening traceability across campaigns. | Resolve the schedule's real campaign under the existing transaction and write the correct ID. A regression verifies every newly created publication event. Historical audit rows are preserved. |
| F6 | LOW | The pending-operation menu included expired unconfirmed selections and archived campaigns, offering actions that could no longer succeed. | Filter only the menu, keeping all SQL history. Still show RUNNING operations past their original confirmation deadline because they require recovery. A regression verifies expired selection data remains stored but its button is absent. |
| F7 | MEDIUM | Statistics/panels trusted stored ACTIVE/next-run fields before the planner's next cleanup cycle. An archived/expired campaign, or a next slot beyond expiry, could still be advertised as an upcoming publication, although send eligibility already prevented it. | Filter effective active counts and upcoming instants against campaign status and expiry, including the exclusive end boundary. Apply the same next-due rule to individual schedule panels/statistics. Three regressions failed before the correction. No schedule/history row or execution policy is rewritten. |
| F8 | LOW | Final-head branch CI exposed a timing assumption in the pre-existing slow-group test: other groups could finish while the designated slow task was still legitimately CLAIMED, before its API call. A fixed ten-tick loop incorrectly required SENDING at that moment. | Synchronize on an explicit signal inside the slow API await and await the persisted completion of the other groups with a bounded deadline. This still fails if a slow request blocks all progress. No publication-engine transition was changed. |

Ten new regressions failed against the original reviewed source. After the main
corrections they passed. Two additional date-confirmation regressions failed
against the old rendering during the correction work and passed after F4.
Three further statistics regressions failed before F7. Further fault/concurrency
tests bring the new review file to 29 test cases.

## Architecture and authorization conclusions

* A campaign's immutable content versions remain separate from independent
  `(campaign_id, chat_id)` schedules. Content edits do not reset individual UTC
  dates, versions, intervals, or next slots. In-flight attempts retain their
  pinned version; later attempts use the current saved/previewed version.
* SINGLE/SELECTED confirmations snapshot selected targets and removals;
  EXISTING is a one-time eligible snapshot; FUTURE adds a durable cursor-based
  periodic rescan. Merely seeing a group never makes it eligible.
* Approval/enabled/type/removal checks are repeated under SQL authority locks
  after Telegram preflight. Future assignment requires current global actor,
  local Telegram actor and bot authority, an eligible campaign, current policy,
  and no existing schedule/tombstone. Concurrent reconciliation cannot insert
  duplicate assignments. Removed schedules remain excluded after restart.
* Future assignment alone creates DRAFT schedules. Automatic activation requires
  separate explicit consent tied to current defaults and previewed content.
  Policy changes, archive, expiry, and revocation during preflight are rechecked
  before insertion. Explicit re-addition restores the existing schedule ID as
  DRAFT; historical attempts remain attached.
* Current global administrators intentionally share campaign management. This is
  not a tenant/client ACL. Nonadmins and forged cross-campaign/schedule identities
  cannot obtain management access. Operations are additionally bound to the
  confirming numeric actor ID and current policy/schedule/content snapshots.
* Every dangerous per-group mutation rechecks real local administrator authority;
  global ownership alone does not bypass it. Publication checks actor and bot
  authority outside SQL, then checks local SQL eligibility/fence/expiry again.
  Telegram can change authority after a successful RPC; final API errors remain
  authoritative. Stop/remove can fail closed if local authority cannot be verified.
* Bulk items commit their mutation and completion atomically. Partial results are
  durable; one group's failure does not roll back another. Versioned confirmations
  reject newer policy/membership; expired item leases use fresh tokens. SQL rollback
  before SENDING commit leaves a known-unsent claim that can safely continue.

## Worker, locks, API and recovery

Lock order remains shared ACL boundary → optional policy → campaign → schedule
→ global/chat rate gates. Existing ACL/content writers use the exclusive
identity boundary. Independent schedules can hold shared locks together. No
Telegram request holds a database transaction or row lock. Brief policy and
global-rate serialization remain intentional; production DB latency matters.

The bounded pool, independent planner and coordinator keep slow network calls
from blocking all work. Campaign/schedule dispatch markers and interleaved due
selection provide fairness. Recovery uses PostgreSQL, not Redis as the only
state store. SQL operations and delivery continue when an isolated Redis
connection fails; a real Redis integration service also participates in the
full suite. A Redis outage can still disrupt bot UI/FSM availability.
The production entry point also requires a successful configured Redis ping
before starting polling/background tasks. A cold start during a Redis outage
therefore fails closed until Redis recovers; this audit does not claim an
independently deployed SQL worker starts without Redis. Persistent schedules
remain in PostgreSQL for subsequent recovery. No unsafe in-memory FSM fallback
was added.

CLAIMED recovery replaces tokens/fences. SENDING recovery is UNCERTAIN and pauses
the matching active generation; a late worker cannot override it. State intent
commits before Telegram API writes. Confirmed IDs are retained, including partial
album responses. Terminal API failure/unknown results do not trigger blind replay.
Definitively rejected retries are bounded. Global FloodWait retains the longest
deadline across workers/campaigns. Persistent positive rate-budget contracts are
tested separately from rate-disabled synthetic capacity tests.

Missed slots beyond the existing grace window are skipped. Persisted waiting
attempts become obsolete at the end of their original interval. Restart does
not backfill every missed advertisement. Pause/Stop/expiry can prevent unsent
parts, but cannot recall a request already in progress. A known album with an
unsent companion is partial, not an entirely SENT publication.

Native text, photo, video, album and companion-keyboard APIs retain entities,
UTF-16 metadata and Custom Emoji IDs. Button parsing, URL restrictions,
popup/alert delivery binding, share/copy, version history, restore and preview
continue through the existing 3.1 implementation/tests. Internal campaign names
remain management metadata, never publication content. No live Telegram client
rendering, emoji entitlement, transport compatibility or real delivery was
established by API doubles.

## Failure-scenario regression evidence

All tests assert persisted state, retained IDs, versions/counts, or actual request
contracts, rather than merely absence of an exception.

| Scenario | Evidence |
| --- | --- |
| Two workers / overlapping group schedules | Existing SQLite/PostgreSQL multiworker and shared chat-rate contracts; synthetic 10/100/1,000 groups |
| Two administrators | New PostgreSQL competing default confirmations: one commit, one stale rejection, schedules unchanged |
| Group removed while publishing | New mid-album managed-group revocation: known media IDs retained, companion cancelled, no resend |
| Bulk pause during send | New overlapping-campaign test: one accepted in-flight send retained; unrelated campaign continues |
| Content edited during send | Existing pinned-version/send-await regression and multi-group content independence test |
| Future group discovered twice | New concurrent PostgreSQL reconciliations: one row; explicit re-add retains ID/history |
| Duplicate bulk confirmation | Existing durable operation replay and new default-confirmation replay tests |
| Lost response / partial album | Existing timeout/network/malformed/incomplete response tests; new partial statistics tests |
| Several campaign FloodWaits | Existing shared longest-deadline and stop-cannot-bypass-gate tests |
| Worker crash / stale completion | Existing CLAIMED/SENDING recovery, fencing and loop restart tests; new late-success log test |
| Redis unavailable | New isolated TCP Redis connection-failure test followed by SQL operation recovery/publication; real Redis persistence/isolation suite |
| SQL rollback | New PostgreSQL abort before SENDING commit: no API call, no consumed try, safe continuation exactly once in the test |
| Administrator revoked before send | Existing global/local preflight revocation; new mid-album local-admin revocation |
| Campaign expiry / archive | Existing expiry/missed-slot tests; new expiry/archive changes during future-group preflight |
| Exclusion after restart | Existing SQL tombstone/restart tests and new concurrent reconciliation + explicit re-add test |
| Policy changes during reconciliation | New preflight interleaving: no stale assignment or automatic activation |
| Stale callback changes newer settings | Existing native UI version tests; new actor-bound confirmations and PostgreSQL competing-policy test |

## Database and migration review

0015 still follows 0014. **No migration file was changed by this audit.** No new
migration is required: the existing CHANGE operation stores policy confirmations.
FKs, `(campaign,chat)`, slot, revision/request and per-operation/chat unique keys
protect the corresponding identities. Status/date/interval/lease constraints
supplement application transitions. JSON partial-ID queries work on the tested
SQLite and PostgreSQL dialects; they count attempts, not individual messages.

Real migration tests compare every old table/column row before and after 0015,
including legacy schedules and content/history. Backfilled policies default to
SINGLE with autoactivation disabled; existing schedules retain explicit overrides.
Alembic version tracking makes a repeated `upgrade head` a no-op; executing raw
DDL twice outside Alembic is not supported. Downgrade refuses nonempty GLOBAL ADS
tables rather than deleting history. A reviewed forward rollback and tested
backup/restore remain required after adoption. Direct privileged SQL can bypass
application invariants; no immutable-history trigger or universal deadlock-free
guarantee is claimed.

Assigned-group and assignment counts describe retained non-excluded SQL
assignments, including historical campaign records; they are not live Telegram
membership counts. Active/next figures exclude archived campaigns and expired
or out-of-window slots. Each state counts distinct groups, but a group can be
active in one campaign and paused in another, so those state counts are not
mutually exclusive categories. Partial counts are a subset of attempt outcomes.

## Validation results

Final GitHub checks for the audit patch are linked in PR #37's audit section and at
[PR checks](https://github.com/viliusvenckus49-beep/safe/pull/37/checks). The older
1,667-test CI result is not substituted for the audit patch's validation.
The PR audit section records the exact patch commit and final branch/PR run IDs
and conclusions; use those linked runs for final CI evidence.

An intermediate final-head PR check passed all 1,697 tests and Docker smoke,
while its parallel branch check failed on F8 (1,696 passed). The failure was
investigated and corrected, not hidden with an unchanged rerun. The replacement
head's checks are the required final gate linked from the PR audit section.

| Final local check | Result |
| --- | --- |
| Full pytest, disposable PostgreSQL 17 / Redis 7.4, no skips | **1,697 passed**, 4 existing Alembic configuration deprecation warnings, 280.60 s |
| New final-audit regression cases | **29 passed**, 14.63 s |
| Targeted GLOBAL ADS / publication / UI suite | **164 passed**, 54.07 s |
| Ruff check / format check | Passed |
| mypy | Passed, 90 source files |
| Python compilation of app, migrations and tests | Passed |
| Alembic upgrade through 0015, repeated upgrade and schema drift check | Passed |
| SQLite and PostgreSQL old-row migration retention / downgrade guards | Passed in full suite |
| Production Compose using dummy secret paths only | Passed |
| Docker build | Passed |
| Packaged worker startup/shutdown, no network, read-only image, disposable tmpfs SQLite | Passed |
| Synthetic 10 / 100 / 1,000-group capacity run | **3 passed**, 46.98 s |

The local full-suite run precedes the synchronization-only F8 test correction;
production source is unchanged by that correction. The corrected slow-group
test additionally passed five consecutive isolated runs. Final-head CI reruns
the entire combined suite, including that correction.

The final packaged smoke initially could not create its container because the
managed review environment ran out of disk space. Specific review images and
identified review build-cache records were removed; the same image's smoke was
rerun successfully. No production Docker host, volumes or data were cleaned.
CI now also explicitly runs Python compilation and Alembic schema-drift checks.

Fresh isolated capacity run: Python 3.12, PostgreSQL 17-alpine, 8 worker slots,
2-core cgroup CPU quota, 8 GiB memory limit, 3 visible logical CPUs. Real SQL,
request doubles, rate budgets disabled only for this synthetic benchmark.

| Groups | Enqueue | Worker delivery | Total | Worker publications/s |
| --- | ---: | ---: | ---: | ---: |
| 10 | 0.123 s | 0.472 s | 0.594 s | 21.20 |
| 100 | 1.048 s | 3.133 s | 4.181 s | 31.91 |
| 1,000 | 11.608 s | 26.375 s | 37.983 s | 37.91 |

All three cases passed, checking exact SENT database totals, distinct destination
IDs and no duplicate delivery calls. Timing components are independently rounded.
These numbers are not live Telegram throughput. Production ADS budgets remain
20 messages/second globally and 20/minute/chat by default; zero is rejected in
production settings. Actual Telegram limits and other bot traffic still apply.

## Residual risks and 3.4 recommendations

The corrected 3.3 foundation is suitable for 3.4 development once final checks
pass, and for manual merge after its dependencies. It is not deployment approval.

* No physical VPS power-loss or live Telegram compatibility trial was performed.
  Bot API has no general exactly-once guarantee or arbitrary message-history
  reconciliation. Unknown results need human review; never replay them blindly.
* UI rights snapshots for very large assignments can be slow or rate limited.
  Reconciliation is bounded/paged; benchmark results do not measure thousands
  of real Telegram administrator RPCs. History retention and partial statistics
  should also be benchmarked against production-sized histories in 3.5.
* Shared rate gates and short per-campaign policy locks intentionally serialize
  coordination. Synthetic success does not guarantee no starvation/deadlock
  under every production workload. Monitor DB latency, pending age and errors.
* Notifications are at-most-one attempt; delivery is not guaranteed. Production
  backup/restore, rollback and staging rehearsals still require separate approval.

Concrete 3.4 changes, intentionally **not implemented** here:

1. Introduce explicit numeric client grants, separate from global/local admin
   rights. Existing nullable client metadata alone grants nothing. Check actor,
   campaign, grant version/revocation and expiry on every callback, FSM input,
   preview, history, restore and final content commit.
2. Provide a content-only capability/service for clients. Do not expose generic
   admin campaign/schedule setters or weaken `AdsService.authorize`. Clients
   cannot change names, groups, defaults, intervals, dates or grants. Concurrent
   content saves must retain optimistic revision checks and immutable history.
3. Define campaign-level client entitlement expiry explicitly. Group-specific
   end times and assignment-policy defaults can differ; neither is automatically
   the campaign's client-editing expiry. Add a separate reviewed field/policy
   and migration rather than guessing min/max group expiry.
4. Use cryptographically random deep-link tokens (e.g. 32 random bytes → 43
   URL-safe characters; `ad_` keeps the start parameter within 64 characters).
   Store a hash for lookup and a revocation/generation marker. A forwarded token
   never replaces numeric-ID authorization. If later copying requires recovering
   the token, design encrypted storage with a separate managed key; do not log
   plaintext or pretend a one-way hash can be copied back.
5. Revoke grants under the same SQL authority boundary as final saves (or a
   carefully ordered campaign ACL lock). Old buttons, saved FSM state, tokens
   and pending drafts must fail closed after revocation. Expired clients may
   read their allowed status but cannot commit content. Preserve unrelated
   campaign publication schedules when access changes.
6. Scope client history/statistics to their campaigns, sanitize audit details,
   and test cross-client IDs, stale/rotated/revoked tokens, grant revocation
   during commit, expiry, preview and restore. Restoring creates a new version;
   already pinned sends and published Telegram messages remain unchanged.

## Recommended merge order

At audit time all dependent PRs remained open and unmerged:
**#31 → #32 → #33 → #35 → #36 → #37**. Review and merge dependencies manually in
that order, retarget stacked PRs as needed, then rerun validation against the
resulting main branch. Review this audit patch in the existing #37; do not create
an unrelated PR. No automatic merge or production rollout is authorized here.
