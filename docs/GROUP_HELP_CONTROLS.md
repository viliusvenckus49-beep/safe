# Group-first administration, word filters and KICK rejoin handling

Administrator → Group Help → group → Warnings / Checks / Banned Words / AUTO ADS.
Private callbacks edit one panel. Each route checks live Telegram group authority;
AUTO ADS additionally keeps its global SAFECheck administrator requirement.
The group ADS list contains assigned ads only, with an explicit warning that
editing a shared ad also affects its other recipient groups. Existing independent
per-ad controls, clients, payment rules and schedules are unchanged.

The public warning history command is `/warns`; `/warnings` is no longer registered
or advertised. Existing numeric-ID and verified-username rules still apply.
Commands initialize/enable their capability flags when a live authorized group
administrator invokes a command, or enters group settings. The bot must have
restriction rights, the managed group must remain approved/enabled, existing
maximum durations and explicit permanent-action restrictions remain intact.
No member action is taken by opening settings. Native sanctions still use the
existing durable executor, SCAM guards, identity checks and audit history.

## Checks KICK

A noncompliant group membership episode counts once per group/user, across all
KICK rules. A duplicate join update or another message is not another attempt.
First entry: notice then 15 seconds; second/third: notice then 5 seconds; fourth
and subsequent unresolved entries: notice then a 24-hour BAN. No countdown or
seconds are printed in member notices. Pre-action text describes a *planned*
action; confirmed results are applied to the same message. The fourth BAN notice
specifies 24 hours and does not invite immediate rejoining.

Counters survive restart and reset only after confirmed compliance with every
configured KICK rule. Rejoining closes unclaimed work from the previous membership
but never pretends an executing/uncertain Telegram action was cancelled. The
current profile, permissions and rule version are rechecked before execution.
Only confirmed sanctions are reported as completed; KICK includes the unban step.

## Banned Words

Each group has up to 100 literal words/phrases (1–100 characters), independent
from profile spam-name filters. Unicode NFKC/case folding and whole-word boundaries
are used; regular expressions are not accepted. Text, captions and edited messages
are checked. Admins/creators/bots/channel senders remain exempt.

Controls: Off/Warn/Kick/Mute/Ban; independent deletion switch; add/remove/list;
MUTE duration; Back. Off with deletion enabled is a deletion-only filter.
Initially the list is empty, so upgrading enables no new content filter.

An authenticated message creates one fenced event keyed by group/message ID.
The configured word is retained as evidence, not the entire message content.
If deletion is enabled, deletion must return success before the exact formatted
removal notice is sent. Unknown delivery outcomes are retained and never replayed.
RetryAfter is bounded; clean edits cancel only unclaimed punishment. Optional
punishment uses the existing executor after the delivered notice; words KICK does
not count as an obligation rejoin. Profile and word checks do not issue duplicate
deletions for the same message.

## Migration and validation

0023 adds columns to the existing Checks tables, retains every existing column
and row, and refuses destructive downgrade. Existing deadlines keep their original
15-second minimum; new episode records store the exact immutable 15/5/0 delay.
Production release tooling pins the new migration and verifies all original
columns using both an online rehearsal and a frozen backup before migration.

Tests use real SQLite and isolated PostgreSQL plus stateful Telegram doubles.
These are not live Telegram native-action confirmation. Live validation should use
a consenting non-admin test account in an isolated group, with a harmless test
keyword. Production members are not test targets.
