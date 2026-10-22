# Per-group warning settings

Private navigation: Administrator panel → Group Help → ⚠️ User warnings → group.
The LT/EN/RU screen uses the supplied Group Help example's native bold/underline,
emoji and button rows. Only the selected warning limit gets the screenshot's ✅;
the chosen punishment is displayed in the bold-labelled summary. Callbacks edit
the same message. Member lists/details and Back also remain in that panel.

Each group persists a 2–6 warning limit, OFF/KICK/MUTE/BAN punishment and MUTE
duration in `group_moderation_settings`. Separate warning-version CAS and the
existing group lock prevent competing administrators from overwriting settings.
Changing warning settings does not invalidate unrelated manual moderation jobs.

Defaults remain **3 WARN → MUTE for 24 hours**. OFF disables automatic punishment,
not WARN recording. Warnings do not expire. Settings affect the next warning;
lowering a limit never retroactively punishes the entire existing warned list.
The existing group opt-in, per-action module gates, Telegram rights, SCAM and
administrator protection still apply. The UI alerts when required modules are
disabled instead of silently enabling unrelated moderation tools.

The duration menu offers 5m, 10m, 30m, 1h, 2h, 3h, 6h, 12h, 24h, 2d, 7d and 30d,
subject to the group's existing maximum duration. BAN is permanent; KICK follows
the existing ban-then-unban execution path.

Existing WARN sanctions retain numeric user ID, reason, date and actor. The warned
list groups active WARN records by user and reuses the existing warning detail
formatter. Existing individual/all UNWARN mechanisms retain their confirmations,
audits and concurrency checks. Escalations store their triggering limit snapshot.

Native punishments reuse the fenced sanction/job executor and public receipts.
Warnings are consumed only after confirmed owned MUTE/BAN success. KICK requires
successful completion of both stages; timeout, flood wait, rejected API actions
and incomplete KICK retain warnings. Concurrent requests cannot create a second
live automatic punishment. A configured BAN/KICK can replace an existing MUTE
through the existing linked-sanction mechanism without first unmuting the user.

Policy changes cancel only unclaimed automatic warning jobs. They refuse to
pretend an executing or ambiguous action was cancelled, retain all WARN rows and
do not revoke an already applied punishment. Warning receipts refresh their
group-specific denominator after a policy change.

Migration **0021** adds four policy columns and one escalation threshold column,
with defaults and constraints. Existing rows/columns are retained. A downgrade
is allowed only when new policy values/version and threshold snapshots still
represent the untouched legacy behavior; otherwise a forward rollback is needed.
The production release pins this migration and rehearses exact legacy-column
retention on restored backups before changing production. The isolated sandbox
operator uses the same reviewed runtime and 0021 target with its safety boundary.

Tests cover all 20 limit/punishment combinations, failed/ambiguous APIs, incomplete
KICK, MUTE replacements, settings races, group separation, stale callbacks,
editing one native message in three languages, persisted data, actual SQLite and
PostgreSQL migration/backfill, drift, and guarded rollback. Automated Telegram
transports are doubles; actual end-user Telegram UI delivery is separate.
