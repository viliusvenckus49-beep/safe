# SAFECheck 3.0 — stage 1 review

This branch adds the local moderation foundation only. It is not deployed. Public
commands, callback handlers, menus and LT/EN/RU translations are unchanged.
No AUTO ADS, CAPTCHA, anti-spam or Group Help integration is added.

## Authority and settings

`GroupModerationService` accepts numeric Telegram chat, actor and target IDs. A
caller must supply the authenticated sender ID, never a user-selected actor ID.
There are no public handlers for this API in this stage.

The group must already be approved, enabled and not removed in `GroupService`.
Global SAFECheck owners/moderators have no implicit local authority. Every request
and manual execution verifies real Telegram membership with a bounded timeout:

* A human group creator, or administrator with `can_restrict_members`, can request
  a local restriction/revocation.
* Changing local settings additionally requires `can_change_info` for administrators.
* The bot must currently be an administrator with `can_restrict_members`.
* Creators, administrators and the bot itself are protected targets. Anonymous
  sender-chat administrators cannot be impersonated with a numeric user ID.
* Authorization failures and unavailable Telegram checks fail closed.

Each group has `enabled`, `bans_enabled` and `mutes_enabled`, all defaulting to
false. Enabling the master flag alone enables no actions. Configuration changes
increment a policy version and invalidate queued applications. The default
maximum duration is 30 days; permanent bans require explicit separate permission.
Finite restrictions must have at least 60 seconds remaining before application,
avoiding Telegram's interpretation of very short durations as permanent.

## Durable local sanctions

`group_sanctions` is separate from SCAM records and report moderation actions.
The initial restriction types are BAN and MUTE. Commands such as WARN and KICK
are for a subsequent stage, once their domain semantics/UI have been reviewed.

Request keys bind chat, actor, target, kind, reason and expiration. Repeating the
same request returns the same sanction; changing its payload conflicts. Per-group
SQL serialization and a partial unique index allow one live restriction for a
chat/user pair. Different groups remain independent.

`group_sanction_jobs` contains APPLY and optional RELEASE work, committed in the
same transaction as the sanction. The existing background cycle processes a
bounded number of native Bot API operations. A conditional claim, lease token,
attempt cap and persisted retry deadline prevent competing claims/replay after
restart. `RetryAfter` is deferred; it does not count as a successful action.

Confirmed API results set ACTIVE/REVOKED/EXPIRED. False returns, lost rights and
API rejections never become successful bans. A failed release preserves the
active restriction and its ownership. Audit events retain chat/user/job IDs,
operation, attempt, result and outcome. No local operation writes reputation,
SCAM judgments, TRUSTED designations or profile activity.

A timed-out write or expired processing lease becomes UNCERTAIN and is not
automatically replayed. Telegram and SQL cannot provide a shared atomic commit;
the next stage needs a reviewed reconciliation UI/API for these cases and for
explicit retry of terminal release failures. Repeating the original revoke key
remains idempotent; it does not silently restart a failed release. Do not clear
UNCERTAIN rows or delete jobs to force a retry.

Temporary restrictions also use Telegram's native `until_date`. Scheduled expiry
cleanup may proceed when new moderation is switched off or the original actor
loses their rights. Removed/disabled managed groups receive no new API writes.
The executor only releases restrictions it confirmed creating; existing bans,
changed restrictions, protected targets and active SCAM identities are preserved.
Unmute restores the group's current default permissions, not unrestricted access.
The existing user moderation lock coordinates SCAM/local writes in the current
single polling process. Multi-process SCAM/local coordination needs further work
before deploying multiple independent moderation executors.

## Migration and release boundary

New Alembic revision `0011`, parent `0010`, creates only:

* `group_moderation_settings`
* `group_sanctions`
* `group_sanction_jobs`

Approved groups receive disabled settings in the new table. Existing tables,
migrations and data are not rewritten. Later-approved groups receive settings on
their first authorized configuration. No production database was accessed.

Before a future production release: review this PR, pass the complete CI suite
(including disposable PostgreSQL contracts), take and verify a database backup,
and explicitly review deployment compatibility for `0011`. The existing
`deploy/github_deploy.py` migration allowlist is intentionally unchanged, so this
candidate cannot pass the current production migration guard automatically.
Do not bypass that guard. Migrate the approved schema before starting the new
image. For rollback, run the previous image and retain the additive tables;
the explicit downgrade is exercised only on disposable test databases.

## Validation

`tests/test_group_moderation_foundation.py` covers live rights, global ACL bypasses,
group isolation/module toggles, duration bounds, protected targets, payload-bound
idempotency, concurrent submissions/claims, lease recovery, API failure handling,
safe release and worker restart. PostgreSQL contracts use only
`TEST_POSTGRES_URL` and isolated random schemas.

`tests/test_group_moderation_migration.py` runs the actual 0010 → 0011 migration,
checks all legacy table snapshots (including REP, SCAM, TRUSTED, username history,
language and profile activity), default-off settings, constraints, schema/model
equivalence and downgrade isolation. An additional contract runs real Alembic
upgrades/downgrades in a disposable PostgreSQL schema in existing CI.
