# SAFECheck release: database protection gate (0010 -> 0016)

## Scope and verified base

Prepared against main `0605ccc70e02f85b7c2a8f17697a8d517c998714` after PRs
31, 40, 32, 33, 35, 36, 37, 38, 39. This document is an approval proposal,
not authority to execute production commands. No production backup, restore,
migration, restart, configuration change or deployment was performed.

VPS evidence: Ubuntu 24.04.4 x86_64, Docker 29.1.3 / Compose 2.40.3,
2 vCPU, 3.7 GiB RAM / 2.4 GiB available, 32G free disk, PostgreSQL 17.6,
11 MB database, Alembic 0010. Redis PONG, AOF enabled, persistence status ok.
Bot image ID `sha256:32f4c0741d8d8d05f1864ef77199bf487d0a876af8c338de26cd29f3196063b7`.
These are samples, not future capacity guarantees. The bot has no explicit
CPU/memory limit; changing limits is outside this preparation.

Existing `/var/backups/safecheck` contains dumps and digest-only `.sha256`
sidecars. Latest visible file: `safecheck-20261011T005651Z-696fb2d3.dump`,
155179 bytes. Presence is not validation. Backup systemd units are not installed;
periodic backup and offsite coverage remain unconfirmed. `/etc/safecheck/offsite.json`
was absent; that does not exclude provider snapshots or another configuration.

## Code review and migration compatibility

- `ops.backup()` uses pg_dump custom format, temporary/exclusive private files,
  SHA-256, independent restore verification, then retention pruning. Its offsite
  hook is optional. A successful local copy does not establish offsite coverage.
- `ops.verify()` rejects checksum mismatch before Docker. It restores to a unique
  container, network none, tmpfs data, with no production volume. Its built-in
  smoke checks only users/schema metadata; do not call it a complete semantic
  data-retention comparison. The rehearsal below performs full legacy comparisons.
- `ops.restore()` is DESTRUCTIVE: stops bot, takes a rescue backup, drops/recreates
  the selected DB, restores it and flushes the selected Redis FSM database. It is
  explicitly excluded from the initial approval below. Shared Redis consumers
  must be inventoried before any flush. Do not run `restore` to test a backup.
- `ops.deploy()` stops the bot, verifies a backup, migrates and checks readiness
  before start. It does not stop unrelated external writers. Its retention may
  prune old matching files, so use the no-pruning recipe below for the first copy.
- The GitHub deploy wrapper checks data-service configuration equality and old
  migration bytes, but its old allowlist accepted only 0010. This PR adds exact
  SHA-256 pins for 0011–0016, requires the complete reviewed chain and continues
  rejecting removed/changed/unknown migrations. No historical migration changes.
  Approval of these bytes is not permission to deploy them.
- Rollback calls old-image readiness first. After schema 0016, the old application
  may refuse to start; automatic image rollback is not a universal DB rollback.
  If that check fails, leave writers stopped and use a reviewed forward fix or
  separately approved rescue/restore procedure. Never force an Alembic stamp.

| Revision | Upgrade effect |
| --- | --- |
| 0011 | New local moderation tables; approved groups receive disabled settings. |
| 0012 | New command drafts/settings; widens sanction check constraints. |
| 0013 | New campaign, immutable content version and content draft tables. |
| 0014 | New schedules, attempts, parts and publication coordination tables. |
| 0015 | New GLOBAL ADS policies/operations, schedule fields and indexes; derived policies for existing campaigns. |
| 0016 | New customer grants/drafts and hashed-link records. |

Chain: 0010 -> 0011 -> 0012 -> 0013 -> 0014 -> 0015 -> 0016, one head.
No migration 0017 needed. 0012 does alter existing moderation constraints and 0015
adds columns/indexes, so allow a maintenance window; do not equate additive data
preservation with lock-free execution. PostgreSQL transactional DDL is used, but
inspect the actual resulting revision/schema after any interruption. Do not
assume either that all DDL committed or that none did. Set an approved lock /
statement deadline in the migration connection during the later maintenance gate.

## MTProto dependencies

VPS container `inspiring_germain`: image `safecheck-mtproto-tools:1.42.0`, restart
policy no, one python process. Reviewed tools Dockerfile includes Telethon 1.42.0
and `/app/mtproto_login.py`, not the SAFECheck SQL application. Reviewed login helper
writes private `api.json` / `account.session`, authenticates a Telegram user account
and verifies staff access; it does not use the PostgreSQL schema. Image name alone
does NOT prove the running command: Docker can override entrypoint/arguments.

The main bot's `app/mtproto_relay.py` DOES use SQLAlchemy sessions/audit/SCAM records,
and can send staff commands when configured. It shares the application's lifetime;
stop the whole bot, not merely polling, for a final write freeze. Preserve MTProto
session/credential state separately from the PostgreSQL dump, through approved
secret backup procedures. Do not copy a live SQLite session to claim a consistent
session backup. Do not run simultaneous login helpers/relays against one session.

Pending read-only evidence: classify PID1 as the known login helper, compare helper
code hash and inspect mount destinations/permissions without printing cmdline,
secret values, session content or full inspect JSON. If it is another process,
identify its DB/file writers before a maintenance approval. Do not stop it now.

## Approval split

### Gate 2A — online PostgreSQL backup and isolated restore ONLY

Authorize this separately. It permits a new private local archive/checksum and
one disposable test container on the VPS. Bot/PostgreSQL/Redis are not stopped or
reconfigured. pg_dump reads a transactionally consistent snapshot while writes
continue. The copy is useful for recovery rehearsal, but is NOT the final
pre-migration rollback boundary and does not include later live writes.

Risk: bounded temporary CPU/RAM/disk use, short DB read locks; archive contains
private data. Existing copies are not deleted. Test PostgreSQL has no network,
no host port, no production mount, max 512 MiB RAM, one CPU and 256 MiB tmpfs.
Use only the locally existing production PostgreSQL image ID; do not pull/install.
If those limits are insufficient, fail and review; do not enlarge silently.

Run the following reviewed Bash block ONLY after Gate 2A approval. On mobile,
transfer it as an operator file and check `bash -n`; do not flatten the heredocs.
If the established directory ownership/permissions differ, STOP rather than
chmod/chown existing production paths. Store output privately; share only status
and aggregate counts. This recipe never runs Alembic or app/Telegram code.

```bash
set -euo pipefail
umask 077
test "$(id -u)" -eq 0

docker_local() {
  env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock "$@"
}
backup_parent=/var/backups/safecheck
test -d "$backup_parent"
test ! -L "$backup_parent"
test "$(stat -c %u "$backup_parent")" -eq 0
test "$(stat -c %a "$backup_parent")" = 700
backup_dir=$(mktemp -d "$backup_parent/gate2-readiness.XXXXXXXX")
archive="$backup_dir/safecheck-gate2-$(date -u +%Y%m%dT%H%M%SZ).dump"
verify_container="safecheck-gate2-verify-$(cat /proc/sys/kernel/random/uuid)"
verify_image=$(docker_local inspect safecheck-postgres-1 --format '{{.Image}}')
created=0
cleanup() {
  if [ "$created" -eq 1 ]; then docker_local rm -f "$verify_container" >/dev/null; fi
}
trap cleanup EXIT

docker_local exec safecheck-postgres-1 sh -ec 'exec pg_dump -U "${POSTGRES_USER:?}" -d "${POSTGRES_DB:?}" -Fc --no-owner --no-acl --lock-wait-timeout=5s' > "$archive.part"
test -s "$archive.part"
python3 -c 'import os,sys; f=open(sys.argv[1],"rb"); os.fsync(f.fileno()); f.close()' "$archive.part"
mv "$archive.part" "$archive"
sha256sum "$archive" | cut -d ' ' -f 1 > "$archive.sha256"
test "$(sha256sum "$archive" | cut -d ' ' -f 1)" = "$(cat "$archive.sha256")"

docker_local run -d --pull=never --name "$verify_container" --network none --memory 512m --cpus 1 --tmpfs /var/lib/postgresql/data:rw,size=256m -e POSTGRES_HOST_AUTH_METHOD=trust "$verify_image" >/dev/null
created=1
docker_local exec "$verify_container" sh -ec 'for i in $(seq 1 60); do pg_isready -h 127.0.0.1 -U postgres >/dev/null && exit 0; sleep 1; done; exit 1'
docker_local exec "$verify_container" createdb -U postgres restore_check
docker_local exec -i "$verify_container" pg_restore --exit-on-error --single-transaction --no-owner --no-acl -U postgres -d restore_check < "$archive"
test "$(docker_local exec "$verify_container" psql -X -U postgres -d restore_check -Atc 'SELECT version_num FROM public.alembic_version')" = 0010
docker_local exec -i "$verify_container" psql -X -U postgres -d restore_check -v ON_ERROR_STOP=1 -P pager=off <<'SQL'
BEGIN READ ONLY;
SET LOCAL statement_timeout=10000;
SET LOCAL lock_timeout=2000;
SELECT version_num FROM public.alembic_version;
SELECT format('SELECT %L AS table_name,count(*) AS records FROM %I.%I;',table_name,table_schema,table_name)
FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name
\gexec
SELECT count(*) AS unvalidated_constraints FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated;
COMMIT;
SQL
printf 'Archive retained privately: %s\n' "$archive"
printf 'Checksum and isolated native restore completed; inspect counts/constraints before acceptance.\n'
```

Accept only if every command succeeds, restored revision is 0010, required legacy
tables exist, counts are plausible and no unexpected unvalidated constraints exist.
For stronger verification in the later frozen-writer gate, compare per-table
canonical row fingerprints and sequences from the same frozen live snapshot and
the clone. Online live counts can change; never assert they must match later.
The temporary clone is removed; archive/checksum remain private. On error, preserve
the archive and failure metadata, stop the procedure, and do NOT restore into live.
Interrupted client connections can leave a test container; identify its exact
unique name and ownership before separately approved cleanup. No broad Docker prune.

### Gate 2B — final write freeze / migration, separate approval

Do not request or execute until MTProto/external writer inventory, Gate 2A restore,
reviewed deployment fix, exact candidate image, staging requirements and offsite
plan are satisfied. Specify exact processes and commands, permitted downtime,
RPO/RTO, maintenance lock path, safe resume-on-pre-DDL-failure and who operates it.

Sequence: stop approved writers gracefully -> confirm no writer -> fresh backup
and fingerprint/sequence manifest -> isolated restore / compare -> approved offsite
verification -> approved 0010-to-0016 migration -> head/drift/retention checks ->
STOP before application start unless separate Gate 3 LIVE permission covers it.
Existing `github_deploy.py` bundles migrate+start, so it is NOT suitable for a
migration-only permission. Do not run it under Gate 2A or a narrower Gate 2B.
New moderation flags must remain disabled; do not insert/activate ADS schedules.

## Offsite and rollback plan

- Select an independent approved destination, encrypted in transit and at rest;
  set credentials through existing private secret management, never chat.
- Existing `deploy/upload_backup.py` supports a privately configured rclone remote,
  upload of the exact archive/digest pair and independent read-back verification.
  rclone/remote/encryption availability is unconfirmed; do not install/configure
  it or contact a destination under the current permission. A remote name alone
  does not prove at-rest encryption. Provider snapshots also need evidence.
- Propose daily backup, 14 daily copies plus the protected pre-release copy,
  independent offsite copy, monthly isolated restore, RPO <=24h and target RTO <=1h.
  These are unapproved targets, not existing guarantees. Measure restore duration.
- Do not enable existing timer merely because its unit file is in Git: confirm
  operations root/image/schema, writer policy, resource limits, offsite hook and
  retention first; enabling a timer is a production change requiring approval.
- Before schema changes, the old pinned image can continue. Application rollback
  is sufficient only if old readiness accepts the actual schema.
- After migration, prefer a reviewed forward fix. Never force schema downgrade,
  readiness bypass or stamp. Restore to live requires a NEW explicit permission,
  current-state rescue copy, agreed loss of intervening writes, all writers stopped,
  restored schema/image compatibility and approved FSM reconciliation.
- No automatic production dropdb / FLUSHDB commands are provided here. Restoring
  SQL does not undo Telegram messages; retain UNCERTAIN outcomes for reconciliation.

## This preparation's validation

- 77 targeted tests passed: `tests/test_github_deploy.py tests/test_operations.py`.
  Includes every supported source revision, missing/changed/renamed migration
  rejection, unknown 0017, backup privacy/checksum, verification isolation,
  failed migrations, rollback refusal and offsite adapter failure cases.
- Real local PostgreSQL 17.6 rehearsal: synthetic 0010 seed, native custom dump,
  native isolated restore, all 26 legacy table fingerprints equal; deliberately
  corrupt checksum rejected before Docker; upgrade to 0016 and Alembic check
  passed; all 26 legacy fingerprints unchanged; zero ADS schedules and no enabled
  moderation settings. Both disposable DB containers and temporary archive removed.
- This rehearsal used existing review-image dependencies with current source
  mounted; it is not a newly built final image or a production-data restore.
- Earlier full main CI: 1852 passed, including broader PG/Redis and backup/restore
  integration. Not rerun solely to repeat completed work. New PR CI is separate.
- Full production archive restore, offsite, MTProto runtime classification and
  actual Telegram staging remain pending. No new Alembic revision or app change.

Status: SAFE PREPARATION COMPLETE; production database protection NOT YET VERIFIED.
Next action: obtain Gate 2A permission for the exact online-copy/isolated-restore
scope above. No migration, bot stop/restart, deployment or ADS permission implied.
