# Replacing an owned MUTE with BAN or KICK

An administrator may now ban or kick a member who has an active, owned
SAFECheck MUTE, without an intermediate UNMUTE. Other cross-kind combinations
remain blocked. Existing actor/bot rights, protected targets, SCAM rules,
policy checks, native lease fencing and group isolation still apply.

Schema 0019 adds a nullable `group_sanctions.replaces_sanction_id` self-reference.
The live unique index includes `kind`, allowing the prior MUTE and a pending
linked BAN/KICK to coexist. The service holds the existing group SQL lock and
permits only one replacement, only for an ACTIVE owned MUTE, with no processing
or uncertain predecessor job. Direct SQL writes do not receive these service
guards; production must continue using the moderated service entry points.

The executor checks the actual member's full MUTE permission/deadline signature
against the predecessor before replacing a restricted member. A restriction
whose ownership or signature cannot be verified is preserved. A separate BAN
appearing during execution is not adopted as this request's successful write.

Only a confirmed `APPLIED` BAN/KICK marks the predecessor `COMPLETED`, records a
`group_moderation_sanction_replaced` audit event, cancels its unsent RELEASE as
`REPLACED`, and refreshes its original receipt without an UNMUTE button. The
prior MUTE does not resume after the replacement ends. Its original reason,
administrator, deadlines, APPLY record and warning history remain intact.

An explicitly rejected replacement leaves the old MUTE active. RetryAfter
remains queued using the existing bounded retry policy. A timeout after a native
write leaves the replacement UNCERTAIN, retains both records and blocks old
automatic/manual release; it is never blindly replayed. Telegram and PostgreSQL
do not offer a shared atomic transaction, so an uncertain write still requires
read-only reconciliation against Telegram and the audit trail.

Old release buttons are checked against the current sanction state and cannot
release a replacement. Terminal historical rows cannot be rewritten by a fresh
revoke request. Concurrent BAN/KICK requests serialize and only one is accepted.

The migration preserves existing data and refuses to discard populated
replacement history on downgrade. Deployment must use an isolated test DB
first; production migration/deployment remains a separate approval.

The operator still requires idle test work before replacement of its container.
Its errors now distinguish confirmed active sanctions, processing work, pending
work and uncertain outcomes. An ACTIVE/APPLIED MUTE is not described as an
unknown write. It never deletes an active sanction to pass an upgrade check.

Tests use real SQL and a stateful Telegram API double. They are not a live
Telegram acceptance result or proof of second-account Ephemeral invisibility.
