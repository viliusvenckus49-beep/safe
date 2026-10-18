# SAFECheck 3.0 stage 2: local moderation

This change builds on PR #31 and migration 0011. It does not deploy, alter
production data, enable modules, or replace the existing SCAM engine.

## Entry points and permissions

Use `/moderation` in a private chat to select an approved, enabled group where
you currently have Telegram moderation rights. `/moderation -100...` selects a
specific group. In a group, `/moderation` sends the controls privately. If the
bot cannot initiate the private chat, it provides an Open bot button instead.

Global SAFECheck administrator/moderator status does not grant local rights.
The human actor must be the group's owner or an administrator with
`can_restrict_members`. Settings also require `can_change_info`. The bot must
have `can_restrict_members`. Administrators and the bot itself are protected
targets. Rights are checked again before native API execution.

Telegram command menus expose the new commands only in the group-administrator
scope. Server-side checks still reject administrators without restriction
rights. Ordinary users retain their existing menus and features. No existing
callback IDs are renamed; local panels use the `lm2` namespace.

## Commands

Commands work in the selected private group context or directly in that group:

| Command | Example | Effect |
| --- | --- | --- |
| `/ban` | `/ban 123456789 7d optional reason` | Local timed ban |
| `/ban` | `/ban 123456789` | Local permanent ban, if explicitly enabled |
| `/unban` | `/unban 123456789 optional reason` | Release an owned local BAN/KICK restriction |
| `/mute` | `/mute 123456789 30m optional reason` | Local timed mute |
| `/mute` | `/mute 123456789` | Permanent mute, if explicitly enabled |
| `/unmute` | `/unmute 123456789` | Release an owned local mute |
| `/warn` | `/warn 123456789 optional reason` | Persist a local warning |
| `/kick` | `/kick 123456789 optional reason` | Ban, then unban to permit rejoining |
| `/warnings` | `/warnings 123456789` | Private warning history for this group |

Replying to a genuine user's message replaces the numeric argument, for example
`/mute 1h reason`. Explicit numeric IDs take precedence over a reply. Anonymous
sender-chat messages cannot supply a target. Usernames, ambiguous IDs and
unsupported duration tokens are rejected; the MTProto/Group Help resolver is
not used by these actions.

Supported durations: `30m`, `1h`, `1d`, `7d`, `30d`; `forever`, `neribotai`,
`навсегда`, or omitted duration mean permanent. Configured group duration limits
still apply. MUTE, KICK and timed BAN require a supergroup. A missing reason
produces no reason line. All dynamic text is HTML-escaped. LT/EN/RU are supplied.

Raw commands are explicit execution instructions. Menu-selected actions first
show the immutable target, action, expiry and optional reason, with Confirm and
Cancel buttons. These drafts expire in ten minutes and belong only to their
creator. Confirm/cancel races are serialized in SQL. Existing confirmations
cannot be reused to submit changed arguments or trigger a second action.

## Settings and histories

All stage-2 flags default to false: warnings, kicks, permanent mutes. Existing
master/BAN/MUTE/permanent-BAN flags remain unchanged. A group owner or authorized
administrator must explicitly opt in using Group settings. Optimistic policy
versions reject stale toggle clicks, and switches preserve custom duration
limits. Policy changes cancel queued applies that have not started; they do not
falsely cancel in-flight API writes or remove active restrictions.

Private history is scoped to an authorized group's selected numeric user ID.
It includes original action, actor, date, status, optional reason and the latest
release request/actor/reason. Older release drafts and full attempt audit events
remain in SQL. WARN has no Telegram-native enforcement API: success means its
warning record was committed, not that a native restriction was applied. This
stage adds no automatic warning escalation or warning reset.

## Durable execution and truthful results

BAN/MUTE retain the stage-1 fenced job executor. KICK adds a persisted second
release step. API rejection/false is failure; read failures and explicit flood
limits can retry with a delay. A write timeout or expired execution lease has
an uncertain outcome and is not blindly replayed. Each job is consumed once
across concurrent local workers; unknown outcomes require human review.

A queued receipt never claims success. It is updated in place after a confirmed
outcome, including after restarting the worker. Missing receipts are not
recreated. Identical edits are deduplicated, transient delivery failures are
deferred, and navigating away retires edits to a reused private panel.

Local BAN never creates a SCAM record and does not alter REP, TRUSTED, identity
or REDSAFE profile data. `/unban` and `/unmute` preserve active SCAM restrictions,
foreign restrictions and changed restrictions. `/unban` is deliberately not a
universal way to remove another bot's ban. A new explicit release request can
retry a definitively failed release; replaying an old confirmation cannot.

KICK can fail between its ban and unban steps, leaving a ban in place. Its
receipt says so. A new `/unban ID` can retry a definitive failure, subject to
fresh rights, ownership and SCAM checks. Telegram may delete a banned user's
messages; a KICK preview explicitly warns about that native behavior.

## Schema, validation and deployment boundary

Migration `0012` follows `0011`, adds settings flags and actor-bound confirmation/
receipt storage, and expands only the local sanction kind/status constraints.
It preserves existing sanction/job rows and all legacy tables. Old migrations
are untouched. Downgrade refuses before DDL if any stage-2 drafts, warning/kick
history, permanent mutes or enabled new flags exist. Do not discard history to
force a downgrade: retain schema and prepare a reviewed forward rollback.

Tests cover SQLite and disposable PostgreSQL migrations/data snapshots,
permissions/forged callbacks, durations, native API outcomes, concurrent
confirm/cancel, replay/release retry, SCAM priority, KICK restarts and deferred
receipt delivery. Existing full-suite tests cover the retained SCAM/REP/profile
and navigation paths. API tests use doubles, not real group members.

The existing production deployment schema allowlist is intentionally unchanged;
production deployment must be reviewed separately. Back up and restore-test the
production database, merge/review the foundation first, then review migration
0012 and staging smoke tests before any production rollout. No deployment was
performed for this PR.

Telegram supplies no atomic permission-check-and-moderate or restriction-owner
API: a small external race with other administrators/bots remains. Retain the
supported single polling-process topology for the existing SCAM engine; local
SQL job fencing does not make multiple legacy SCAM pollers safe. Deleted users,
unknown peers, revoked rights, flood limits and inaccessible groups may prevent
native actions. These are recorded as failure/queued/uncertain, never success.

## Stage 2.1 receipt presentation

Confirmed BAN, WARN, MUTE, KICK, UNBAN and UNMUTE receipts use one shared HTML
formatter in `app/bot/moderation_messages.py`. English brand/status headings and
the CRIMSON signature retain the supplied Unicode design; field labels and
human-readable durations follow the selected LT/EN/RU language. Dynamic names and
reasons are escaped, and whitespace is compacted to keep user text inside its
field. IDs retain safe `<code>` formatting.

Receipts carry no inline keyboard and are not tracked as profile/navigation
screens. Private action previews keep their existing Confirm/Cancel controls;
settings and histories retain their controls. Use `/moderation` to return to
administration after a receipt. Existing callback routes are unchanged.

The formatter only reads existing numeric identities and recorded warnings;
it never resolves a username or changes an action. Immediate reply-command
results can also use the Telegram objects already attached to that update.
Unknown names fall back to the actual numeric ID. WARN shows the committed count
for this group/user up to that warning, without an invented limit. Temporary
durations are derived from the stored expiry and draft creation, not the time
of a later refresh. There is no schema change in stage 2.1.

Only the existing confirmed-success branches use the new success headings.
Queued, failed, uncertain, protected, cancelled and already-banned results keep
their previous meanings. Deferred results update the existing receipt in place;
older receipts are not deleted or converted in bulk. Existing messages retain
their old appearance unless their own deferred result or explicit refresh needs
an update. Profile views do not edit or delete moderation receipts.
