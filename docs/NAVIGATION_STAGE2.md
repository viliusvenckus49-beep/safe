# Navigation stage 2

This change reorganizes the existing bot; it does not replace its business services.
The home HTML (`p.home`, all three languages) is retained and `/start` uses text,
not a photo. Existing home callback identifiers and customer ADS authorization remain.
The approved Unicode symbols replace shipped custom-emoji navigation icons. Explicit
UI Studio previews and their Telegram icon fallback remain isolated from production.

## Navigation

- Home: Check; Profile / Report; TOP 10 / SCAM registry; Rate / Information;
  Language; My ads; Administration (administrators only); Close.
- Administration: SCAM and trust; Reputation; Group Help; AUTO ADS; Users;
  System; Owner controls (owner only); Guide; Main menu.
- SCAM and trust: pending reports, registry, add/remove SCAM, trusted users,
  filtered action history. Existing report evidence and decision flows are unchanged.
- Reputation: pending REP, add/subtract/reset, TOP 10 inclusion/exclusion,
  filtered REP history. Existing confirmation and calculation services are unchanged.
- Group Help: My groups; moderation history (select an authorized group); help.
  Selected group: moderation; warnings; protection; settings; action history.
  Moderation exposes BAN/UNBAN, MUTE/UNMUTE, WARN/KICK; target history remains in
  the history screen. Protection opens existing Checks or Banned Words. Settings
  retains the existing native settings and the selected group's ADS assignments.
- AUTO ADS: My ads, create ad, clients, recorded delivery statistics, settings,
  Administration. Client lists use current SQL grants, pagination and existing
  per-ad controls. Settings directs to individual ad settings; no global interval,
  audience, client, expiry or content control has been introduced. Aggregate counts
  are recorded attempt statuses, not guessed deliveries; per-ad statistics remain.
- Users: list, existing-identity search, profile lookup.
- System: actual database statistics, runtime health, general audit history.
- Owner controls: existing administrator ACL and group approval/removal screens.

All related labels are translated in LT/EN/RU. The same callback identifiers work
across languages. New categories reuse the single existing `Action` handler.
Every restricted route rechecks server authorization. Owner identity is still the
configured numeric owner; an administrator grant cannot change it. Group controls
still require live Telegram rights, independently of global bot administrator status.

Callback navigation edits its private panel in place. Incoming text/media retains
bottom-of-chat prompts. Legacy uneditable/photo panels fall back to send/retire.
Permanent notices, check receipts and moderation outcomes are preserved; a check
receipt is distinguished from the navigable username-history screen by its controls.
Unknown legacy callbacks return a private stale/removed alert. Obsolete private FSM
drafts are cleared without executing their former operations.

## Removed group recovery runtime

Removed `/recovery`, subscription/consent screens and controls, recovery link
input/confirmation, `RecoveryFlow`, member-export actions and rendering, subscription
and recovery GroupService APIs, private-contact enrollment, and the delivery branch
of `process_group_jobs`. Removed their unused locale entries and menu links. Old
`sg` callbacks cannot recreate subscriptions, export members, queue campaigns or send.
The worker never claims historical recovery deliveries, including PENDING/PROCESSING.

Legacy `PrivateContact`, `RecoverySubscription`, `RecoveryCampaign` and
`RecoveryDelivery` models, tables, original migrations and historical audit labels
remain for data compatibility. No destructive migration or row deletion is required.
`ObservedMember` remains: SCAM protection and identity resolution share it.
Group registration/approval/removal, native sanctions, checks and SCAM workers remain.
Operational queue age no longer counts the retired delivery queue.

No token, environment variable, VPS configuration, scheduler, payment rule or
reputation algorithm is changed. No dependency is added. The migration head remains
0023. A pre-stage-2 binary contains the former recovery worker; do not roll back to
such a binary without reviewing any historical pending recovery jobs. This change
preserves those rows rather than silently relabeling their outcomes.

## Verification

Router/SQL tests cover the three user roles and three languages, category/back
navigation, forged owner callbacks, current client-grant pagination and revocation,
user search/profile FSM, filtered audit pagination, group-scoped moderation/history,
escaped identities/reasons, callback length, Unicode labels, permanent receipt
preservation, and inert legacy callbacks/FSM/queued recovery records. Existing report,
SCAM/TRUSTED, REP/TOP, Checks/WARN, sanction and ADS suites remain regression checks.

Tests use Telegram transport doubles: passing tests and healthy deployment do not
constitute a real Telegram confirmation of each inline interaction or punishment.
Review the final CI/deployment report for exact runs and remaining live checks.
