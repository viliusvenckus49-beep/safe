# SAFECheck 3.5 — gated deployment and rollback

**No LIVE deployment, PR merge or production DB operation is authorized by this
runbook.** Use it only after a separate approval naming the target, image and
maintenance window. Staging remains pending as documented in production readiness.

## Review and merge order

Verified current open stack on 2026-10-19:

| PR | Current HEAD | Required base |
| --- | --- | --- |
| #31 | d0da67acc339d9698d44e7cd1fb61e086efc169a | main |
| #32 | 52e48ad0dfa50d8f6d2f48404480987ea22a9f65 | #31 |
| #33 | e883c35938558353d5c26fa7d32d0a19c1b033c7 | #32 |
| #35 | af7e0b839d369eb04c1fbea954871834faae80d8 | #33 |
| #36 | 891f4a52d0bcae3314dd5b5d4a08c53ecd2ffe4c | #35 |
| #37 | 34f975a5922ee7dc69f381b4aa1983e71cda6974 | #36 |
| #38 | 179d5af5fba51fde63c81b40079b4f6112749cde | #37 |
| Stage 3.5 | Final SHA in PR validation section | #38 |

1. Refresh all HEADs/checks immediately before any approved merge. Review each
   incremental diff and verify the cumulative tree/migration chain is preserved.
2. Merge only in table order with explicit human approval. Prefer a strategy that
   preserves stacked ancestry. If squash/rebase changes ancestry, retarget/rebase
   the next PR carefully, compare trees and rerun CI; never assume commit IDs alone
   prove there are no duplicate/lost patches. Do not merge from this task.
3. Run the full final integration suite on the resulting candidate, build a pinned
   image/digest, archive CI/check evidence and record the previous deployed image.
   No `latest` tag and no unreviewed `main` checkout as a deployment selector.

## Preflight on the authorized target

- Inventory VPS OS, Docker/Compose versions, actual services/volumes/writers,
  database head/data size, secrets, configured relay and approved managed chats.
  Inspect disk/inodes/RAM/load and reserve room for image, migration, backup and
  rescue restore. No arbitrary memory limits were introduced by this stage;
  choose limits from target measurements and load rehearsal.
- Verify production configuration uses PostgreSQL, Redis, positive ADS rate limits,
  numeric admin/owner IDs and explicit image selection. Secret entrypoint loads an
  allowlist from mounted files; runtime UID 10001 must be able to read designated
  secrets and public entrypoint code without making secrets generally readable.
  Never print credentials to validate this. Confirm no test settings/rate bypass.
- Current Compose provides persistent PG/Redis volumes, AOF, startup dependencies,
  migration one-shot, read-only nonroot bot, dropped capabilities, log rotation,
  restart policies and a 45-second grace period. Both background workers run inside
  the bot process; do not start a second polling copy with the same token.
- Validate Compose without resolving secret values to console; check effective
  service paths and health commands. Preserve existing MTProto state/configuration
  if used; do not connect replacement accounts as part of this task.
- Confirm external health monitoring/alerts and on-call owner. Poll, group worker,
  ADS plan/dispatch/coord heartbeats are required; unhealthy alone does not make
  Docker restart. Required task exit fails the process and restart policy applies.
- Finish the private live Telegram staging matrix. Obtain a verified offsite
  pre-change backup and isolated restore rehearsal as described in backup/restore.

## Approved maintenance sequence

1. Record exact candidate/base/previous image and database revision. Approve the
   expected data migration path. Through current stages the linear head is 0016:
   0010 → 0011 → 0012 → 0013 → 0014 → 0015 → 0016. Stage 3.5 adds no migration.
2. Quiesce every writer, let required tasks and native polling shut down, record
   in-flight attempts. Take/verify the pre-migration backup plus offsite copy.
3. Use existing `deploy/ops.py deploy` with explicit root/env/project/state/backup
   paths. It verifies infrastructure, stops bot, takes a verified backup, runs the
   one-shot migration and `app.main --check`, then starts the candidate. Do not
   substitute `Base.metadata.create_all`, `alembic stamp` or destructive downgrade.
4. Verify Alembic head 0016 and `alembic check`, constraints/row preservation and
   successful runtime health for polling/group/three ADS loops. Check logs for
   redacted exception types, DB connectivity, bounded retries and stale leases.
5. Smoke ordinary SAFECheck and local moderation using approved test identities.
   Compare retained SCAM/REP/TRUSTED/history counts with the backup manifest.
   Inspect campaign/grant counts and schedule settings without activating ads.
6. **Separate activation approval is required.** Deployment can resume existing
   ACTIVE schedules automatically; inventory and obtain an explicit policy for
   those before starting the candidate. This task does not pause, edit or activate
   production schedules to manufacture a safe-looking deployment.
7. Observe health/rate/FloodWait/UNCERTAIN/partial outcomes and SQL queue age for
   the agreed window. Do not clear unknown outcomes or replay them blindly.
8. Record the result, image, schema, health, evidence and any manual reconciliation.
   Reboot/restart rehearsal on staging precedes any approved target reboot.

## Rollback / No-Go

Stop new writers immediately if data invariants, authorization, schema or delivery
status disagree. Preserve the database and diagnostic metadata. Distinguish:

- Application-only issue, schema compatible: select the explicit previous pinned
  image, verify readiness, then use reviewed `ops.py rollback --image …`. It checks
  migration compatibility and persists the selector; do not force an incompatible
  image to start.
- Schema incompatibility/failed migration: leave writers stopped and choose a
  reviewed forward fix, or explicitly approved restore with rescue backup and
  accepted RPO. New customer/campaign/publication data must not be silently lost.
- Unknown Telegram outcome: retain UNCERTAIN and known message IDs. Image rollback
  and DB restore cannot retract messages Telegram already accepted.

Do not use schema downgrade as a shortcut. Do not delete Docker volumes, prune
unknown images or remove retained user/history rows during rollback. Actual
production restore, offsite recovery and LIVE restart require separate approval.
