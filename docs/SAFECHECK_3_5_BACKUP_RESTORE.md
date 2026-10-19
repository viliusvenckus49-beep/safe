# SAFECheck 3.5 — PostgreSQL backup and restore runbook

These are operator instructions for a separately authorized maintenance window.
No command here was run against production. The disposable rehearsal uses the
same `deploy/ops.py` CLI and fresh Compose project, volumes and synthetic data.

## Preconditions and identities

Inventory the actual Compose project, DB/schema revision, PostgreSQL major,
application image digest, nonsecret environment selector, secret paths, MTProto
state (if enabled) and every external writer/worker. The bot currently contains
both group and ADS workers; stopping only polling is insufficient. Do not assume
there is a separate worker service. Record UTC times and approved RPO/RTO.

Ensure the backup directory is owned by the operations account, 0700, with enough
space for the selected snapshot plus a rescue copy. Dumps/checksums are 0600.
Use version-compatible PostgreSQL tools (current Compose uses 17.6) and an approved
encrypted offsite destination. CLI output/alerts intentionally omit stderr that
could contain DSNs/tokens. Do not paste runtime secret files or dumps into tickets.

Use an explicit reviewed root/env/project for every command. Example variable
names below are placeholders to be filled from inventory, not defaults to copy
blindly. Do not source a secret dotenv file into an interactive shell.

```bash
ops_root=/path/to/reviewed/safecheck
ops_env=/etc/safecheck/compose.env
ops_project=confirmed_project_name
ops_backups=/var/backups/safecheck
ops_state=/var/lib/safecheck/operations
python "$ops_root/deploy/ops.py" --help
```

## Consistent pre-migration backup

1. Announce the approved window, enable operations maintenance lock, stop bot and
   **all** known writers. Allow graceful shutdown; if a send was already SENDING,
   retain its persisted intent and reconcile after startup. Never assume a killed
   HTTP call means Telegram did not receive it. Check no unexpected writer remains.
2. Existing `deploy` stops the bot and takes a verified backup before migrations.
   A stand-alone backup uses `pg_dump -Fc --no-owner --no-acl`, a consistent database
   snapshot even while live, but writers must be quiesced for a rollback boundary.
   Back up configuration and separate session/secret material using approved secure
   procedures; the PostgreSQL dump is not a backup of those files.
3. Invoke backup with explicit paths, optionally an approved offsite hook:

```bash
python "$ops_root/deploy/ops.py" --root "$ops_root" --env-file "$ops_env" \
  --project "$ops_project" --backup-dir "$ops_backups" --state-dir "$ops_state" backup
```

4. Verify the reported archive is a regular nonempty file, no symlink, owned and
   private, and its SHA-256 matches the sidecar checksum. The CLI writes/fsyncs an
   exclusive temporary file, atomically renames it, then verifies before pruning.
   Inspect `pg_restore --list` with the compatible tool as additional evidence;
   a table-of-contents listing alone does not prove successful restoration.
5. The CLI fully restores into a unique, network-disabled PostgreSQL container on
   tmpfs, with no production volume or live-cluster scratch DB. It checks schema
   metadata and drops only that verification container afterward. A checksum by
   itself is not a restore test.
6. Perform a separate isolated restore rehearsal with row counts/fingerprints and
   critical invariants: user IDs/username ownership, SCAM/REP/TRUSTED, activity,
   sanctions/jobs, campaigns/versions/drafts, grants/token hashes, policies,
   schedules, attempts/parts/message IDs, audit rows, sequences and Alembic head.
   Check constraints/FKs and apply `alembic check` with the intended image.
7. Copy the archive and checksum to approved encrypted offsite storage, verify the
   remote copy/checksum, record retention and recovery ownership. An untested local
   dump on the same VPS is not sufficient disaster recovery.

## Restore and migration failure

- On migration failure leave writers stopped, preserve logs/failed schema state,
  inspect transactional versus partial DDL and take a rescue snapshot before any
  destructive recovery. Do not force a downgrade or stamp an unverified schema.
- If the schema is unchanged and compatible with the pinned old image, application
  rollback is sufficient; verify `app.main --check` before starting it. The CLI
  persists the checked image selector for subsequent restarts.
- After additive migration the old image may intentionally reject a newer head.
  Prefer a reviewed forward fix. If old code cannot safely read the new schema,
  rollback means restoring the approved pre-change backup, with explicit acceptance
  of losing intervening writes. Never silently discard new data.
- Existing `restore --confirm-restore safecheck` is deliberately destructive to the
  selected DB. Before approving, confirm project/host/backup identity, stop writers
  and preserve current data. The CLI validates the selected archive independently,
  verifies infrastructure and Redis maintenance access, stops bot, creates a
  protected rescue backup, then recreates/restores the selected database.
- Redis UI/FSM state is explicitly cleared after restoration so callbacks cannot
  operate on DB history from another point in time. Persisted SQL grants/drafts/
  histories remain the restored truth. Other Redis consumers require separate
  inventory; do not clear a shared Redis database without approval.
- Readiness rejects a restored schema incompatible with the image. Choose the
  matching pinned image or reviewed forward migrations while writers stay stopped.
  Do not repeatedly restart incompatible code.

The CLI's exact option placement is shown by `--help`; choose the reviewed archive
explicitly. No destructive restore command is automatically triggered by this PR.

## Disposable acceptance evidence

Run only on an isolated review machine with Docker and a built candidate:

```bash
SAFECHECK_ACCEPTANCE_IMAGE=safecheck:3.5.0-review \
  PYTHONPATH=. python tests/run_operations_acceptance.py
```

The harness creates a unique `safecheck-ops-qa-*` project, generated fake secrets,
PostgreSQL/Redis volumes, current migrations and an explicitly fake Telegram
transport. It seeds legacy identity/REP/SCAM/TRUSTED/profile plus local sanctions,
ADS policies/history/parts and customer grants/drafts/hash rows. It compares
canonical ordered JSON fingerprints for **every** public table before backup and
after restore, checks restored schema, validates stale FSM removal and exercises
monitoring. It cleans up only its own project and volumes. Fake-only secret file
permissions in this harness are not a production secret-permissions template.

Completed on the final review image
`de915e332c2b4df38b8ac744e9893b1d8b96d3d54c311a6e102c4ddff715fa87`:
**45 table fingerprints matched**, **24 nonempty tables / 26 synthetic rows**,
all PostgreSQL sequences matched, schema **0016**. Native `pg_dump` and
`pg_restore` ran successfully, including independent verification and a rescue
backup. Archive/checksum permissions were 0600; stale Redis FSM was cleared;
post-restore bot/PostgreSQL/Redis/readiness monitoring was healthy. Only the
unique disposable Compose project and volumes were removed. The final CI also
runs this rehearsal; its exact-head result is recorded in the PR.

This test does not establish target-VPS throughput, offsite availability, recovery
of an actual production snapshot or an approved production RPO/RTO. Those remain
mandatory operational gates.
