# Moderation reliability review — 2026-10-22

The reported `/unban @redsafeads` reached SAFECheck and identified user 8896102663,
but found no local sanction to release. The main bot's `group_sanctions` and
`group_sanction_jobs` had no rows for that user. Older global SCAM-ban jobs exist,
including native BANNED and ALREADY_BANNED results; those use another registry.
All seven SCAM records for this identity were REMOVED. Therefore the absence of a
local row does not establish that a different bot originally imposed the ban.
Bot API membership reads cannot establish who imposed a current restriction.

Read-only production evidence:

- https://github.com/viliusvenckus49-beep/safe/actions/runs/39725429859
- https://github.com/viliusvenckus49-beep/safe/actions/runs/39725569174

At 05:00 UTC the bot and all workers were healthy. In the reported Šiauliai group
the user was already an administrator; several other groups still reported
permanent bans. The bot had restriction rights in the active groups. Diagnosis
sent only read requests; it did not remove any sanction or change production DB.

The user explicitly changed the policy: authorized administrators must be able
to release restrictions applied manually, through another bot or through an
older SAFECheck path. Active SCAM and administrator/owner protections remain.

## Implemented behavior

| Path | Result |
| --- | --- |
| Owned BAN/MUTE release | Existing direct command and original receipt controls remain. |
| UNBAN/UNMUTE without current local ownership | Private actor-bound confirmation of the actual Telegram snapshot. |
| Confirmation | Fresh actor/bot/target/SCAM checks, group serialization, snapshot comparison and one durable RELEASE job. |
| Ownership/history | Observed restrictions remain `owned=False`; approval and completion have separate audit records. |
| Manual removal or replacement of a prior ACTIVE restriction | Proven stale bookkeeping is retired, pending old releases cancelled and history retained. No Telegram write during reconciliation. |
| BAN/KICK over a manual or another bot's MUTE | Explicit authorized action is allowed; prior MUTE ownership is not borrowed. |
| MUTE over a partial restriction | Full MUTE is allowed when the user can still send messages; repeating a confirmed full MUTE remains a private no-op. |
| MUTE/KICK for a departed user | Private membership diagnostic; no native write. Preemptive BAN remains supported. |
| Back/cancel/stale controls | Leaving a private preview cancels its pending draft; private confirmation is bound to its source message and actor. |
| Wrong reversal kind | UNMUTE on a banned user reports the ban; UNBAN on a restricted user reports absence of a ban, without lifting the MUTE. |
| WARN/UNWARN | Existing group-isolated history, individual/all reset, three-WARN / 24h MUTE policy and protection of already consumed warnings remain. |
| API timeout/RetryAfter | Ambiguous writes stay UNCERTAIN without replay; flood waits keep a deferred fenced job. An approval cannot authorize a changed or expired snapshot. |

Approval uses existing durable moderation drafts and scoped audit records, not
FSM or a fabricated claim of sanction ownership. It expires with the draft,
survives a process restart, and is bound to the exact group, target, reversal and
approving actor. Existing schema 0020 is sufficient; no migration or new runtime
dependency is added. LT/EN/RU, private ephemeral delivery/DM fallback and public
success receipts retain existing infrastructure and CRIMSON presentation.

## Verification

- Broad moderation regression run against isolated real PostgreSQL: 542 passed.
- Final expanded targeted run against isolated real PostgreSQL: 253 passed.
- 36 new tests cover external/legacy releases, actor isolation, target/SCAM and
  rights changes, expiry, timeout/RetryAfter, concurrency, manual clear/reapply,
  stronger sanctions, partial restrictions, departed users and stale navigation.
- Ruff, formatting and mypy (108 application files) pass.
- Telegram transports in automated tests are doubles. These results do not
  establish live end-user button delivery or successful real sanction changes.

Full repository CI and deployment evidence are recorded in the PR/release report
after execution. No manual production ban/mute/unban is sent solely as a test.
