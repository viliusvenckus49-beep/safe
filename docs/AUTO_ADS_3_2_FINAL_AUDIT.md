# AUTO ADS 3.2 / PR #36 final code audit

Reviewed remote head: `63690866cf2c8b8efd31cf8901c8ddbf979ba31e`, followed by
the fixes described below. Source was inspected afresh, including the worker,
SQL transitions, native API payloads, authorization, 0014 and tests; conclusions
are not inherited from the previous implementation report. This is a fresh
code review, not independent third-party certification.

At review time #31, #32, #33, #35 and #36 remain open and unmerged. Their heads
and stacked bases match the dependency chain #31 -> #32 -> #33 -> #35 -> #36.
No VPS, production DB or real Telegram group was used. The unrelated dirty
`app/repositories.py` in the original worktree was retained byte-for-byte.

## Findings and fixes

| Severity | Root cause / impact | Fix and regression |
| --- | --- | --- |
| Medium | Missed-run handling applied to schedule enqueue only. Persisted PENDING/CLAIMED/DEFERRED work could still send hours later, including an old album's companion keyboard. | Cancel waiting attempts at the end of their original interval, under the SQL lock at enqueue/claim/begin. Preserve sent parts and skip missed slots. Four restart regressions failed against the reviewed head. |
| Medium | Definitive/uncertain failure and expired SENDING recovery unconditionally set the schedule to PAUSED. A late result overwrote STOPPED or EXPIRED, or a subsequent edit's state. | Pause only an ACTIVE matching generation. Six stop/expiry/failure/recovery regressions failed before the fix. Attempt outcomes and notifications are retained independently. |
| Medium | Incomplete or duplicate album replies raised before retaining any returned IDs, losing reconciliation evidence for partially delivered media. | Retain valid distinct IDs for the requested chat while keeping the part/attempt UNCERTAIN and cancelling unsent companions. The partial-album regression failed before the fix; no blind album replay. |
| Low | The first schedule callback acknowledgement ran with an unnecessary open SQL read transaction. | Commit authorization reads before Telegram acknowledgement. Dispatcher navigation checks acknowledgements, edits and rights reads for an open transaction. No lock/transaction spans those requests. |

The packaged smoke check previously imported the worker only. It now starts
the actual loop, completes a real SQL polling cycle, cancels it and checks
shutdown in the built read-only container with networking disabled. A separate
worker-loop regression restarts the loop against persisted state without a
duplicate publication.

## Engine, API, permissions and schema conclusions

- All nine intervals use UTC fixed seconds. Vilnius local dates/times reject
  spring gaps and require an explicit correct offset for autumn ambiguity.
  Expiry is exclusive; each part rechecks it before committing SENDING.
- Content saves retain policy version/interval/next time. Each attempt pins an
  immutable revision. A new test saves content during the actual write await
  and verifies the current payload remains old and the next slot uses the new one.
- SQL serializes claims/transitions across independent connections. Tokens,
  fences, the unique schedule/slot key and committed SENDING intent prevent
  two workers from starting the same part. No Telegram call holds the SQL lock.
- CLAIMED crashes recover safely; SENDING crashes recover as UNCERTAIN. Lost
  replies/timeouts/5xx never imply SENT or an automatic retry. A late fenced
  response cannot overwrite recovery. Exactly-once delivery is not guaranteed.
- Telegram 429 establishes a durable shared ADS deadline; concurrent 30/60-second
  responses keep the longer deadline. Four safe attempts per part bound retries.
  Already in-flight calls cannot be recalled by a later 429, stop or expiry.
- Text/photo/video/album payloads use real aiogram methods, native entities and
  explicit `parse_mode=None`. Album keyboards use a separate part. URL, popup,
  alert, share and copy map to native API fields. Popup/alert callbacks require
  the exact delivered keyboard chat/message and pinned revision.
- UTF-16 validation and Custom Emoji metadata come from the shared 3.1 content
  model. The actual runtime transport can retry a definitive icon rejection once
  without icons. IDs remain in content; fallback does not establish icon rendering.
- Global owner/admin authority and real local Telegram administrator status are
  both required for schedule mutations/activation. The owner cannot bypass local
  checks. Runtime rechecks both actors and the bot, group approval/enabled/removal
  state, generation and expiry before each part. Revocation before confirmation
  is covered for local and global authority. Public ad popups grant no management.
- 0014 remains unchanged, additive and based on 0013; previous migration files
  are untouched. FKs, unique keys, status/lease/date/interval checks and retained
  old-row snapshots are tested on SQLite and PostgreSQL. Downgrade refuses to
  discard publication data. Application transitions provide legal state edges;
  DB enum checks alone do not restrict every possible privileged direct SQL update.
- No SCAM/REP/TRUSTED/profile/moderation schema or execution logic was changed.
  Deadlocks were not observed in the concurrency contracts. The shared identity
  SQL lock avoids unordered publication row-lock acquisition, at a throughput cost.

## Critical-scenario evidence

The `tests/test_ads_publication*.py` suite covers:

| Scenario | Evidence |
| --- | --- |
| Two workers claim/write one task | SQLite and PostgreSQL simultaneous enqueue/claim/execute contracts |
| Crash before API | CLAIMED expiry, token/fence replacement, stale begin rejected |
| Crash during API / accepted but lost reply | SENDING expiry, real async timeout and transport-loss contracts; UNCERTAIN, no replay |
| FloodWait / several concurrent FloodWaits | Bounded per-part retries, shared SQL gate, longest-deadline regression |
| Bot removed / loses administrator role | Forbidden and live-role loss contracts; no false success |
| Stop during send | SENDING outcome retained; late failure/recovery cannot overwrite STOPPED |
| Expiry during album | First native album retained; companion cancelled, policy EXPIRED |
| Edit during send | Independent-session save during the API await; pinned payload and unchanged schedule |
| Several hours of downtime | No catch-up burst; four persisted waiting/partial states cancelled; future slot succeeds |
| Duplicate callback | Native confirmation journey and service idempotency; one schedule/attempt |
| Admin revoked before confirmation | Local and global denial; policy remains paused |
| Partial album | Confirmed media IDs retained; companion failure or incomplete native reply remains non-successful |

These tests use Telegram API doubles. They verify state and request contracts,
not real Telegram delivery or a physical VPS power-loss event.

## Verification

Local disposable PostgreSQL 17 and Redis 7.4 were started specifically for this
audit, without production volumes/configuration. The full suite at the time of
collection passed **1606 tests**, with **4 existing Alembic deprecation warnings**
and no skips. The subsequently added SQL/Telegram navigation regression passed
separately; the final commit's CI reruns the combined suite.

Ruff check and format check (263 files), mypy (86 source files), Alembic upgrade
through 0014 and autogenerate check, production Compose validation using dummy
secret files, Docker build, and packaged worker startup/shutdown all passed.
Exact final commit CI results are attached to PR #36 rather than inferred from
an earlier green build.

## Remaining risks and 3.3 recommendations

The corrected 3.2 scope is suitable for merging after its dependencies and for
starting 3.3 development. It is not authorization to deploy or enable real ads.
A private staging-bot check remains necessary for Custom Emoji entitlement,
copy/share UI, album layout, actual permissions and Telegram error behavior.
Notification delivery is at-most-one attempt, not guaranteed. Recovery from an
unknown write needs human reconciliation; Bot API has no arbitrary history read.

The database already separates one campaign from independent campaign/chat
schedule rows, pinned attempts and per-part outcomes. Current 3.2 management
deliberately rejects multiple schedules for a campaign. Selected/all/future-group
assignment and central multi-group administration are **not implemented**.

Before high-volume GLOBAL ADS:

1. Replace campaign-only selection with explicit schedule/group identities in
   every UI/service action. Preserve per-group generations and authorization.
2. Introduce consistent ordered per-schedule locks/`SKIP LOCKED` or equivalent
   partitioning; preserve the ACL revocation boundary and immutable content pins.
   Current shared identity locking serializes unrelated SAFECheck identity work.
3. Separate enqueue cadence from slow Telegram execution; add a bounded worker
   pool with fair per-chat scheduling and explicit global/per-chat rate budgets.
   One process currently executes up to 20 claims sequentially. Under sustained
   latency it may intentionally skip slots beyond the documented grace window.
4. Add suitable schedule/status partial indexes and benchmark large histories;
   aggregate statistics without an N+1 query per campaign/group.
5. Represent selected/all-existing/future-group assignment policies explicitly;
   authorize every newly included group and keep individual pause independent.

No 3.3 functions, client access/deep links or production deployment were added.
