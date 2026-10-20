# Moderation targets: numeric ID, reply and @username

Moderation commands and private moderation panels accept a numeric Telegram ID,
a reply to a user message, or `@username`. Examples:

- `/warn @example Rule violation`
- `/ban @example 1h Spam`
- `/mute 123456789 30m Flood`
- Reply to a consenting non-admin test account: `/warn Test reason`

`/ban@BotUsername` addresses the command to that bot; it does not supply a target.

## Identity and authorization

The Bot API cannot resolve arbitrary private-user usernames. A username must
already map to exactly one numeric user ID observed by SAFECheck. The selected
group must be approved/enabled and the actor must currently have local Telegram
moderation rights. `getChatMember(chat_id, numeric_id)` must then confirm both the
same numeric ID and the same current username. Matching is ASCII/case-insensitive;
unknown, ambiguous, renamed, malformed or unverified identities fail closed.

Ask an unknown user to write in the group, or use a numeric ID / reply. No public
username-to-ID guess, placeholder identity, historical-name match or MTProto
connection is introduced. The resolver does not rewrite identity or SCAM records.

All downstream authorization, protected bot/admin checks, module opt-in, sanction
limits, audit and confirmation checks remain unchanged. Drafts, execution and
replayed callbacks keep the resolved numeric ID; a later username reassignment
cannot redirect an existing action to its new owner. An explicit unknown username
never silently falls back to another person in a replied-to message. Telegram
cannot atomically lock a username across a lookup and sanction; the verified
numeric identity is pinned rather than re-resolved at execution time.

LT/EN/RU input/error messages explain these limits. Existing numeric and reply
syntax, safe HTML receipts and SCAM/REP/TRUSTED isolation remain unchanged. No new
schema, migration or dependency is required.

## Verification

Regression coverage uses real isolated SQLite/PostgreSQL sessions and stateful
Telegram request doubles. It covers commands/private panels, unchanged numeric
and reply paths, stale/ambiguous usernames, missing/wrong live identities,
API failures/timeouts, revocation, protected targets, confirmation pinning and
localization. These automated checks are not native Telegram client proof.
Actual test results and the PR CI are reported separately after execution.

Local isolated verification: **153 passed** in 23.94s (new username regressions
and existing stage-1/stage-2 moderation tests), including real PostgreSQL
transactions. Ruff check/format passed; mypy passed for93 application files;
Python compilation passed. Native Telegram verification of this patch and final
GitHub CI are separate acceptance steps, not implied by request-double tests.
