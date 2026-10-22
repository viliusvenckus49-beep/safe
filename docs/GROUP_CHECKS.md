# Group Help → Checks (Security)

Private navigation: Administrator panel → Group Help → Checks → authorized group.
LT/EN/RU. The supplied screenshot's two tabs expand each rule's six actions directly
below that rule: Off / Advise / Warn, Kick / Mute / Ban. Selected tabs/rules use
native primary styling; the chosen action uses success styling. Back and settings
edit one existing private menu message. All writes recheck live group rights and
use an independent policy version, with stale callbacks answered privately.

Implemented obligations: surname, username, and profile photo visible to the bot.
Implemented name filters: Arabic letters, Han ideographs, Cyrillic letters, and
administrator-configured case-insensitive NFKC-normalized phrases. Scripts do not
infer nationality. Empty phrase lists cannot enable a spam-name action. Photo
privacy can hide an existing picture; the rule and reason explicitly require a
photo visible to the bot. Unavailable API evidence never counts as a violation.

**No member-addition quota and no channel-membership obligation are included.**
Existing groups acquire no active rules during migration. All rule actions default
to Off; join checks default on when an administrator configures this feature.
Message checks use the same enabled rules; deletion is a separate explicit switch.
Administrators, creators, bots, anonymous/channel senders, removed groups and
nonmembers are exempt. Existing SCAM, moderation opt-in/module gates, rights and
foreign-restriction guards remain in force. Native actions require the existing
corresponding moderation module; configuration does not silently enable it.

Each confirmed violation episode creates a durable event. The bot first sends a
REDSAFE SECURITY notice, using the actual display name and numeric ID. Native
WARN/KICK/MUTE/BAN can only be queued **15 seconds after confirmed notice delivery**.
The confirmed notice timestamp, immutable execution deadline and notice ID persist
across worker and bot restarts. Queue polling time cannot shorten that deadline. No coroutine
sleeps holding SQL locks or waits out the grace period. An unconfirmed notice
cannot authorize a native sanction. RetryAfter is retried only after Telegram's
explicit rejection; ambiguous delivery is retained as uncertain and not replayed.
Advise sends the notice without imposing a sanction.

At the deadline, live identity, compliance and rights are checked again. The
existing sanction executor also rechecks the rule and policy version before its
native APPLY. A corrected username/photo/name cancels the pending action. Settings
changes cancel unclaimed Checks actions, leave manual sanctions alone, and refuse
to fabricate cancellation of executing/uncertain native operations. Multiple
violations select the strongest configured action (Ban, Kick, Mute, Warn, Advise)
instead of competing sanctions. Persistent violations do not repeatedly advise or
WARN; a compliant period or genuine later rejoin starts a new episode.

KICK uses the existing ban/unban executor and is confirmed only after both stages.
MUTE uses the configured Checks duration (default 24h, adjustable 5m–30d, subject
to the group's existing maximum). BAN is permanent. WARN uses existing history,
thresholds and automatic-punishment rules. Removing one WARN does not undo a
separately applied native sanction.

The final LT KICK card is exactly:

```html
🛡 <b>REDSAFE SECURITY</b>

👤 <b>{escaped_display_name}</b> [<code>{user_id}</code>]

⚠️ <b>Priežastis:</b> Nenustatytas @username.
🚫 <b>Veiksmas:</b> <b>KICKED</b>

<i>Norėdami prisijungti, pirmiausia nustatykite Telegram naudotojo vardą.</i>
```

The pre-action version labels the action as planned and includes the 15-second
notice; it never claims unconfirmed success. After confirmed execution, the same
notice can be updated to the final card. The per-group result-update switch can
keep completion quiet. No new success-message pile is created. Event history
retains numeric identity, rule, action, deadline, sanction link, actual result and
state, even when updating the Telegram notice fails. Native failure/uncertainty
is never represented as successful KICKED/BANNED/MUTED.

Migration **0022** only adds group_check_settings, group_check_members and
group_check_events. It changes no existing rows/columns and activates no rules.
A downgrade refuses to discard any Checks configuration, observations or history.
The production rollout pins the migration and verifies legacy rows on restored
private backups before and after migration. Its idle guard includes Checks work.

Automated tests use real SQLite/PostgreSQL, real routing and stateful Telegram
API doubles. They cover exact HTML/escaping, all actions, grace-period fencing,
restart persistence, corrected identity, duplicate updates, concurrent workers,
policy races, per-group isolation, permissions, unknown photos, RetryAfter,
interrupted delivery, localized inline navigation, migration/drift/retention and
quiet completion. These are not live Telegram native-action or iPhone UI proof.
