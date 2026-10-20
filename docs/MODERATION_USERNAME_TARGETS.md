# Moderation targets: numeric ID, reply and @username

Moderation commands and private moderation panels accept a numeric Telegram ID,
a reply to a user message, or `@username`. Examples:

- `/warn @example Rule violation`
- `/ban @example 1h Spam`
- `/mute 123456789 30m Flood`
- Reply to a consenting non-admin test account: `/warn Test reason`

`/ban@BotUsername` addresses the command to that bot; it does not supply a target.

## Identity and authorization

The Bot API cannot resolve arbitrary private-user usernames. When the existing
MTProto integration is explicitly enabled and its relay is active, moderation
reuses `lookup_identity(refresh=True)` to resolve a public username to a numeric
ID. No new account connection or session copy is created. The lookup runs without
a SQL transaction and uses the relay's shared timeout/FloodWait safeguards.
Without a usable relay result, exactly one previously observed numeric identity
is required. Both paths require current local administrator rights and an
approved/enabled group, then fresh `getChatMember` confirmation of the same numeric
ID and username. Unknown, ambiguous, renamed or unverified identities fail closed.

This path deliberately does not call the global identity-observation service:
resolving a moderation target must not create identity records or trigger SCAM
linking/global sanctions. A VPS login helper alone does not prove that the bot's
relay is enabled or authenticated. Isolated staging has MTProto disabled and does
not import the production account session. Use numeric IDs/replies until the
actual account integration is verified.

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

The optional MTProto extension adds regressions for unseen/recycled usernames,
disabled/disconnected relays, timeout and transport failure, invalid identities,
Bot API mismatch, authorization before lookup, absence of SQL transactions during
lookup, and absence of global identity/SCAM mutations. Request doubles do not
prove live MTProto account connectivity. The verification figures above describe
the earlier cache-only revision; extension results are reported separately.
