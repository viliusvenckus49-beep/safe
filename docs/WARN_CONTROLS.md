# SAFECheck warning escalation and receipt controls

An enabled moderation module supports both permanent and timed BAN/MUTE. Omitting
a duration means permanent; existing explicit duration tokens remain supported.
Legacy `allow_permanent_*` columns are retained for compatibility but no longer
block enabled commands or appear as separate UI switches. Current Telegram rights,
protected administrators/owners, opt-in modules and time-range checks still apply.

UNMUTE lifts the owned personal restriction using all supported permission flags
set to true, as required by `restrictChatMember`; group defaults continue to apply.
Copying group defaults into a personal restriction can leave Telegram reporting
`restricted` after release and previously blocked subsequent MUTE/BAN/KICK. A
legacy, permanent member exception exactly matching every current group-default
flag is accepted as having no additional restriction. Unknown flags, departed
members, finite deadlines and stricter restrictions remain protected. This repair
does not clear historical sanctions or grant ownership of a foreign restriction.

## Warning lifecycle

Each distinct `/warn` records one `GroupSanction(kind=WARN)`. Telegram redelivery
of the same message/request reuses its existing draft and sanction. An intentional
new message is a new warning. Warnings have no expiry and are scoped to group/user.

| WARN status | Meaning |
| --- | --- |
| RECORDED | Active |
| CANCELLED | Explicitly cleared by an authorized administrator |
| COMPLETED | Consumed by a confirmed successful automatic MUTE |

On the third active warning, the existing group lock serializes creation of one
24-hour MUTE and its normal fenced APPLY/RELEASE jobs. A durable escalation links
the triggering warning, sanction and public receipt. The existing worker performs
fresh Telegram identity, role, bot permission, policy and ownership checks.

Only a confirmed successful APPLY owned by SAFECheck consumes the active warnings,
in the same fenced completion transaction. Failed, rejected, protected, ambiguous
or cancelled attempts retain warnings. FloodWait retries the same job under the
existing backoff/deadline rules; uncertain writes are never blindly repeated.
Existing foreign restrictions are not claimed as a successful automatic penalty.
Other live restrictions or a disabled MUTE policy can prevent escalation; the
warning receipt reports that warnings remain active. No group is affected by
another group's warnings. A manual clear can cancel an unsent escalation, but
cannot pretend to cancel an in-flight or uncertain API request.

The migration does not retroactively issue penalties. Existing RECORDED warnings
remain active; a new WARN evaluates the threshold under the current policy.

## Public buttons and private errors

WARN CONTROL navigation edits the same recipient-only message with native
`editEphemeralMessageText`, using its `ephemeral_message_id` and receiver, never
ordinary `message_id=0`. Back cancels its pending confirmation and returns to the
private menu. Close/confirm/cancel delete that private panel; if deletion fails,
cleanup attempts to disable its keyboard without reporting a committed WARN
reset as failed. Private UI delivery failures never trigger a public navigation
hint. DM fallback uses normal edits only in the administrator's own DM.

Receipt formatting reloads job results with `populate_existing=True`, so an ORM
object cached as PENDING cannot override a confirmed RELEASED UNMUTE. A private
queued receipt is removed when the confirmed public result replaces it. Existing
signed receipt IDs encode ephemeral IDs negatively under PRIVATE visibility;
normal/public message IDs remain positive. No schema migration is needed. Old
records storing only ordinary message_id=0 cannot identify an ephemeral message
for deletion, so those old IDs are never guessed.

After a confirmed manual release, the original BAN/MUTE receipt becomes
USER • UNBANNED / USER • UNMUTED, using the releasing administrator. The old
duration, reason and generic "sanction revoked" footer are omitted; the release
button is removed. A text /unban or /unmute also sends a new public confirmation
immediately, regardless of elapsed time. A release button updates its original
receipt. Rejected, changed, foreign or uncertain releases never claim success.

An explicitly cleared WARN receipt becomes USER • UNWARNED using the audited
clearing administrator; its history button remains. One durable public UNWARNED
summary is queued per confirmed reset of previously public warnings, including
an all-warning reset; existing warning messages are also refreshed. A repeated
confirmation cannot create another reset/summary. Consumed automatic warnings
retain their existing consumed state rather than pretending they were UNWARNed.

WARN receipts expose `× UNWARN` and target-specific `📋 HISTORY`; active owned
BAN/MUTE receipts expose UNBAN/UNMUTE. WARN CONTROL privately lists active warning
IDs, dates, reasons and issuers, and can confirm one warning or all warnings.
`/unwarn ID` opens this control; `/unwarn ID WARN_ID` selects the exact warning.
`/warns` aliases `/warnings`. Texts, buttons and history support LT/EN/RU. Unknown display names
fall back to one Telegram ID field instead of duplicating the ID in two fields.

Release callbacks resolve the exact sanction and stored public receipt. WARN
views derive group/user from a persisted warning and check current group-local
authority; they never trust caller-supplied group/user IDs or global SAFECheck
administrator status. Destructive warning confirmations bind the actual actor,
warning scope and active set. Cross-group, protected-target and stale mutations
fail closed; authorized read-only history remains available after cancellation
or consumption.

Clearing warnings uses a five-minute confirmation token bound to the actual actor,
origin warning and current active-warning set. Confirmation is sent using a native
recipient-only ephemeral group message with explicit-rejection DM fallback. An
individual selection is persisted; a changed warning set, expired token,
revoked rights or repeat confirmation cannot clear additional warnings. Clear and
consume audits retain original warning reasons, issuance dates and administrators;
history also shows the state-change administrator and date.

Release buttons enqueue the existing exact-sanction revoke operation. Worker
fencing, ownership/SCAM checks and a unique release job prevent duplicate effects.
The original receipt becomes the release result and loses its control. Manual
release and expiry also retire old public buttons. Errors and WARN management use native HTML ephemeral delivery, with safe DM
fallback after explicit rejection. Small successful confirmation acknowledgements
use real private Telegram alerts. Pending, failed and unknown native outcomes
remain private; only confirmed successful moderation results are public. The
private receipt visibility marker prevents publishing diagnostics during a later
worker update.

Automatic announcement sending claims its durable receipt before calling Telegram.
Ambiguous delivery is not blindly resent. Explicit FloodWait rejection allows a
retry after its prescribed delay. API success alone does not prove client display.

## Migration and staging

Revision **0017**, after 0016, adds only `group_warning_escalations` and
`group_warning_resets`. Existing records and constraints remain intact. Downgrade
refuses to discard warning-control history. No dependencies are added.

Before any production promotion, use an isolated staging database/session/bot:

1. Stop only that staging bot/worker; preserve its database and session.
2. Verify candidate source, backup metadata and existing revision; apply 0017 and
   run Alembic drift checks. Never use production migration permission implicitly.
3. Start the reviewed candidate with a single poller. Check bot, PostgreSQL, Redis
   and worker readiness. Limit native moderation tests to a consenting non-admin.
4. Test permanent and timed BAN/MUTE using username, ID and reply; test UNBAN and
   UNMUTE buttons, stale clicks, local rights revocation and a second group.
5. Issue WARN 1 and 2, inspect 1/3 and 2/3, then confirm WARN 3 causes one 24-hour
   MUTE, resets active count to 0 and retains all history. Test its UNMUTE button.
6. Test clear confirmation, cancellation, expiry, double clicks and another actor.
   Fault injection must prove failed/uncertain MUTE retains all warnings.
7. Confirm public buttons are visible to ordinary members but cannot be used by
   them, and ephemeral errors/confirmations are invisible to a second member.

Rollback keeps schema 0017 and its history. Stop the new staging runtime, inspect
all pending/processing/uncertain escalations and finish or reconcile them before
running an older worker: its code cannot perform the new consume hook. Never
replay an ambiguous native action or drop the new history tables. Production merge,
migration, deployment and restarts still require separate explicit approval.

Professional extension, source/license analysis and acceptance limits: see
[MODERATION_REFERENCE_REVIEW.md](MODERATION_REFERENCE_REVIEW.md).
