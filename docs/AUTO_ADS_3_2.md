# AUTO ADS 3.2 — one-group schedules and durable publication

Based on audited PR #35 (`af7e0b8`). #31 → #32 → #33 → #35 are still open,
so this PR is stacked on #35, not on an assumption that those changes are in main.
No production configuration, runtime DB, VPS or real group was changed or activated.

## Architecture and permissions

0014 adds `ad_schedules`, `ad_publication_attempts`, `ad_publication_parts` and
`ad_publication_gate` (a shared durable FloodWait deadline).
Existing migrations and content snapshots are unchanged. Schedule rows are keyed
by campaign/group, ready for independent per-group settings in 3.3. The 3.2
service/UI deliberately supports one selected group per campaign. Changing that
selection pauses the policy and retains attempts with their original chat IDs.

Content and delivery have independent lifecycles. The 3.1 campaign DRAFT state
continues to mean an editable content container; ACTIVE/PAUSED/STOPPED/EXPIRED
live on its schedule. Saving text/media/buttons neither changes policy version
nor next due time. Each attempt references one immutable content-version ID.
Already claimed attempts keep that version, including their companion buttons.

Management requires current global SAFECheck AdminService rights. Selecting,
configuring or activating a group additionally requires the actor to be its real
Telegram administrator/creator and the bot to be an administrator. Approved,
enabled group/supergroup IDs are required. Owner/global authority does not
bypass local Telegram rights. Pause/stop still requires the actor's local rights,
but can stop a policy even when the bot itself has lost group rights. Group-local
moderation permission alone does not grant access to shared global ad content.
No Group Help/MTProto is used.
The existing persistent `group_removed` marker is authoritative even if legacy
approved/enabled flags are stale; removed groups cannot be selected or published.

Each callback/input reauthorizes. Expected policy versions and confirmed content
revision reject stale confirmations. Duplicate activation is idempotent. Writes
and worker transitions share the existing identity SQL lock, rechecking global
authority against revocation. This is cross-process serialization, not an asyncio
mutex or Redis-only schedule. Locks cover short SQL transitions only, never API
I/O. Before large 3.3 deployments, move publication transitions to ordered
per-schedule row locks while preserving the ACL revocation boundary.

## Scheduling rules

Supported seconds: 1800, 3600, 7200, 10800, 14400, 21600, 28800, 43200, 86400.
All operational datetimes are UTC. The UI uses Europe/Vilnius with explicit
offsets. Civil start date/time are retained as configuration; changing either
recomputes its UTC instant without converting intervals into calendar days.

Spring-gap times are rejected. Ambiguous autumn times require a correct explicit
offset (`03:30 +03:00` or `03:30 +02:00`). Invalid offset/date combinations fail.
Expiry is exclusive, optional, later than start and still in the future at
activation. Every part checks expiry again before starting an API write.

For UTC start S and interval I, slots are S + nI. A 24-hour interval always lasts
86400 seconds across DST. Activation/resume selects the first slot at or after
the current instant; it does not replay earlier slots. The next poll may run a
due slot up to 60 seconds late. Older slots are skipped and the next UTC slot
strictly after now is persisted. Never backfill all missed slots or spin through
each missed interval. A busy previous attempt suppresses that slot. Expired
policies stop scheduling and retain history; sent Telegram messages are retained.

## Durable execution and transitions

SQL is the authority. The existing Redis FSM is used only for input navigation.
Unsaved input can expire; committed schedules/attempts survive process restarts.
The regular polling process starts a dedicated bounded ADS worker. Existing
schedules are not imported or auto-enabled: new policies default to DRAFT.

Allowed attempt transitions:

| From | To | Cause |
| --- | --- | --- |
| PENDING | CLAIMED, CANCELLED | Lease acquisition or policy invalidation |
| CLAIMED | SENDING, DEFERRED, FAILED, CANCELLED | Committed write intent, preflight deferral/failure, invalidated policy |
| CLAIMED | PENDING | Lease expired before a write started |
| SENDING | CLAIMED | Confirmed part succeeded; another part remains |
| SENDING | SENT | Every part returned valid actual message IDs |
| SENDING | DEFERRED | Definitive Telegram 429; only unsent part is deferred |
| SENDING | FAILED | Definitive rejection or exhausted safe retries |
| SENDING | UNCERTAIN | Lost/invalid response, timeout/5xx, or expired in-flight lease |
| DEFERRED | CLAIMED, CANCELLED | Retry due or policy invalidation |

SENT/FAILED/UNCERTAIN/CANCELLED are terminal. A stale worker cannot change them.
Each claim gets an unpredictable token and increasing fencing number. A unique
schedule/due-slot constraint prevents duplicate attempts. Every part atomically
consumes CLAIMED into SENDING and commits before calling Telegram. Two processes
cannot start the same part. A 90-second lease exceeds the 20-second bounded write
timeout and preflight reads. Expired CLAIMED leases may be recovered; expired
SENDING leases become UNCERTAIN and pause the policy, never automatic re-sends.

Telegram rights reads happen outside SQL and can be safely deferred. Before each
write the DB rechecks schedule generation, active/global authority, managed-group
flags and expiry. Real actor/bot group rights are checked before each part.
Telegram may revoke rights in the unavoidable gap after a read; its write result
remains authoritative. No cross-system transaction can eliminate that gap.

Each part has at most four safe attempts. 429 honors retry_after.
429 also establishes a SQL-backed global ADS cooldown, checked at claim and
immediately before each write. Cancelling one campaign cannot erase this gate or
allow another process/campaign to bypass the server's delay. Other definitive
write rejections fail and pause. Read timeouts/network/5xx use a bounded delay.
Write timeouts, connection loss, 5xx and malformed success replies are UNCERTAIN,
because the server may have accepted the request. There is no exactly-once claim.
If a response arrives after lease recovery, a stale fence cannot overwrite the
terminal record. Reconciliation requires inspecting Telegram; Bot API provides
no universal idempotency key or arbitrary message-history lookup.

## Composite media and public callbacks

TEXT/PHOTO/VIDEO use one part. ALBUM plus buttons uses two parts: native media
group, then a native keyboard message labelled `↳`. Only its unsent part can be
deferred/retried. Successful album message IDs remain recorded even if buttons
fail or become uncertain. Partial CANCELLED/FAILED/UNCERTAIN attempts remain
visible with each part's status/IDs; they are not counted as successful campaigns.

Text and captions preserve native entities and Custom Emoji metadata with
`parse_mode=None`. URL/share/copy buttons reuse the 3.1 native renderer. Public
popup/alert callbacks have a separate read-only prefix and require the exact
delivered chat/message ID of a successful keyboard-bearing part. They read only
the pinned published revision. They grant no campaign/draft/management access.
Private 3.1 preview callbacks retain their administrator checks.

## Pause, stop, notification and history

Pause/stop/versioned changes cancel PENDING/CLAIMED/DEFERRED work and forbid new
attempts. An already committed SENDING request cannot be recalled; its real result
is retained. If it succeeds, later unstarted parts are cancelled. In-flight writes
may complete after pause/expiry. The UI explicitly explains this limit and never
claims an existing Telegram message was removed. Start requires fresh confirmation
and schedules a future slot; it never retries a terminal uncertain attempt.

Audit events record policy changes, skipped slots, lease transitions, part outcomes
and confirmed IDs, without ad text. Statistics count SENT, FAILED and UNCERTAIN
separately, show next slot/last success and retain detailed errors/partial results.
Native history pagination exposes older retained attempts, five per page.
No view/click metrics are invented. Stored errors are bounded and credential/URL
redacted. Management names/errors are HTML-escaped; no raw traceback is shown.

Important attempt/policy failures claim one notification in SQL before trying a
private message. A restarted worker does not spam it. If the authorizer was
revoked, the global owner is used when configured. Delivery cannot be guaranteed
if that person never started the bot, blocked it, or the process stops between
claim and send. Notification failure never retries the advertisement.

## Migration, tests and readiness

0014 is additive with foreign keys, uniqueness, positive IDs, valid intervals,
complete ACTIVE settings, legal status/lease constraints. Downgrade refuses if
any publication data exists. Do not force-delete history to downgrade; retain the
schema and prepare a reviewed forward rollback. Old migration tests first remove
empty 0014 tables before testing old DDL; legacy snapshot exclusions include the
new tables without excluding any old user data.

Automated coverage includes all intervals, dates/DST, real SQLite/PostgreSQL
workers/transactions, fencing/restart checkpoints, duplicate confirmations,
pause/resume/stop, 429 budgets, actual timeout, partial albums, content pinning,
revocation, removed groups, expiry, private/public callback isolation and LT/EN/RU.
Exact test and quality results are recorded in the PR, not inferred from mocks.
CI also imports the packaged engine and resolves Vilnius time inside the built
read-only image with networking disabled and no runtime secrets.
Telegram API tests use doubles: actual entitlement/rendering, native share/copy,
album layout, permissions and physical VPS power-loss still need an isolated
staging smoke test before separately approved production deployment.

3.3 can add explicit per-campaign group selection and independent schedule rows,
bulk/future-group assignment policies and fairer per-group worker partitioning.
It must preserve per-attempt content pins and per-part outcomes. No GLOBAL ADS
bulk assignment, client ACL/self-service, deep links or payments are implemented.
