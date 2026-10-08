# SAFECheck VPS operations

These files prepare a single Ubuntu VPS running Docker Engine, Compose v2 (`--wait` support), systemd and Python 3.11+. They have not installed services on this workspace or configured a real VPS, Telegram notification recipient, or off-server account. Run one polling process per bot token. A host failure cannot be reported by a monitor on that same host; configure an independent uptime check and off-server backup checks for that requirement.

`docker-compose.production.yml` uses separate PostgreSQL/Redis volumes, no published ports, authenticated Redis, AOF persistence (`appendfsync everysec`), bounded container logs, an unprivileged bot with a read-only root filesystem, and graceful 45-second shutdown. PostgreSQL/Redis patch versions and the application version are explicit. Pin release images by digest where available; version tags can be reassigned. Keep application and database images available before an outage. Do not change PostgreSQL major versions through an application update.

## Initial setup on the future VPS

Install Docker Engine and its Compose plugin from the official Ubuntu installation instructions. Enable `docker.service`. Put the reviewed checkout/release at `/opt/safecheck`, readable by the operations user (the supplied units run as root because Docker access grants host-level control). Keep credentials outside the checkout, build context, shell history and Git.

```sh
sudo install -d -m 0700 /etc/safecheck /etc/safecheck/secrets /var/lib/safecheck /var/backups/safecheck
sudo install -m 0600 deploy/compose.env.example /etc/safecheck/compose.env
sudo install -m 0600 deploy/runtime.env.example /etc/safecheck/runtime.env
sudo install -m 0400 deploy/runtime.secrets.example /etc/safecheck/secrets/runtime.env
sudo install -m 0400 deploy/redis.acl.example /etc/safecheck/secrets/redis_acl
```

Edit these files privately, replacing every placeholder. `compose.env` supplies image/path selectors; `runtime.env` contains only nonsecret settings (`ADMIN_IDS`, explicit `GROUP_OWNER_ID`, log level and health thresholds). `/etc/safecheck/secrets/runtime.env` contains only `BOT_TOKEN`, `DATABASE_URL`, `REDIS_URL`, and optional `TELEGRAM_PROXY_URL`. The entrypoint loads these into the application process without putting values in Compose's container environment configuration. The migration container uses the same entrypoint. Credentials still exist in application memory and are visible to host root/Docker administrators; local Compose secrets are bind mounts, not an encrypted secret store.

Generate three independent random hex passwords with your password manager or `openssl rand -hex 32`, saving them directly into private files rather than command arguments. Do not use the examples as credentials:

| File | Contents | Owner | Mode |
| --- | --- | --- | --- |
| `secrets/runtime.env` | Runtime secret env described above | `10001:10001` | `0400` |
| `secrets/postgres_password` | PostgreSQL password, matching `DATABASE_URL` | `70:70` | `0400` |
| `secrets/redis_password` | FSM password, matching `REDIS_URL` and ACL `default` user | `999:999` | `0400` |
| `secrets/redis_maintenance_password` | Separate restore-only password, matching ACL `maintenance` user | `999:999` | `0400` |
| `secrets/redis_acl` | Completed ACL template, with both Redis passwords | `999:999` | `0400` |

The parent `secrets` directory stays root-owned `0700`. UID 70 and UID 999 correspond to the selected PostgreSQL/Redis Alpine images; verify them when changing images (`docker run --rm --network none --entrypoint id IMAGE postgres` or `... redis`). Compose file-backed secret ownership/mode overrides are not reliable, so set the **source** file ownership. `deploy/secret_entrypoint.py` is nonsecret and must be world-readable (`0644`) for the application's UID. Do not put secret values in either nonsecret env file. Hex passwords avoid URL-encoding mistakes; other passwords must be URL-encoded in connection URLs.

Build the reviewed application and obtain the selected infrastructure images before initializing:

```sh
docker build -t safecheck:2.14.0 .
docker pull postgres:17.6-alpine
docker pull redis:7.4.5-alpine
sudo python3 deploy/ops.py --dry-run initialize
sudo python3 deploy/ops.py initialize
sudo python3 deploy/ops.py status
```

`initialize` refuses a database with any existing public tables. Existing installations must preserve their project/volume identity and use the deployment procedure, or import a reviewed backup; changing a Compose project name creates different volumes. Initialization runs migrations, checks PostgreSQL/Redis/schema without contacting Telegram, then starts polling. The final polling start uses the configured real token; test with a dedicated test bot first. `start` is for later boots and runs no migrations.

## Boot, daily backups and monitoring

On the actual VPS, install the supplied units after initialization succeeds:

```sh
sudo install -m 0644 deploy/systemd/safecheck* /etc/systemd/system/
sudo systemctl daemon-reload
sudo python3 deploy/install_monitor.py
sudo systemctl enable --now safecheck.service safecheck-backup.timer safecheck-monitor.timer
sudo systemctl list-timers 'safecheck-*'
```

These commands are installation instructions; they have not been executed here. `safecheck.service` resumes services after reboot without migrations. Docker `unless-stopped` restarts crashed processes. The bot's Docker health check requires recent **successful Telegram polls and worker cycles**, rather than merely a live process. PostgreSQL/Redis health checks and `app.main --check` validate dependencies and migration readiness. The monitor runs every two minutes, checks aggregate terminal ban/recovery failures and overdue outbox work, and reports locally through journal/stdout. Pending REP/report counts are displayed but do not cause notifications.

`install_monitor.py` installs the monitor wrapper and enables its timer. Each invocation follows the currently running bot's Compose release directory, so later releases are monitored automatically. It preserves `/etc/safecheck/operations.env` and any explicit alert options. Aggregate samples are retained privately in `/var/lib/safecheck/monitor-history.jsonl`, rotated at 2 MiB with seven retained files. They contain service status and queue counts, without usernames, Telegram IDs or credentials. No additional server is needed; samples accrue over time and do not establish long-term stability immediately. `/status` exposes the current polling/worker ages and queue health only to administrators in private chat. The timer reports health and does not restart services.

A Docker health failure does not itself restart a running container. The monitor deliberately reports a stalled polling process for operator action; use `status`, inspect private logs, and restart the bot after diagnosing the cause. Deployment/restore holds a lock and maintenance marker; monitoring is quiet during maintenance and reports markers older than one hour. If an operator kills the CLI, check that no operation remains running before removing a stale marker or abandoned `safecheck-verify-*` container.

The daily timer runs around 03:00 UTC with up to 15 minutes of jitter and catches one missed run after boot. Backups use a transactionally consistent PostgreSQL custom archive, atomic publication, SHA-256 checksum, directory mode `0700`, file/checksum mode `0600`, and 14-day retention. **Every backup is fully restored into a separate disposable PostgreSQL container** with no network, ports or host volumes; data stays on tmpfs and the verifier container is removed afterwards. Verification also requires SAFECheck tables and Alembic metadata. Allow RAM and disk capacity for a second database; large databases may require adapting this tmpfs verification strategy. A checksum detects corruption, not malicious modification by an administrator.

```sh
sudo python3 deploy/ops.py backup
sudo python3 deploy/ops.py verify /var/backups/safecheck/SAFE_ARCHIVE.dump
sudo journalctl -u safecheck-backup.service -u safecheck-monitor.service --since today
```

Retention only removes matching regular archive filenames after a successful verified backup and configured upload. Failed dumps never publish an archive; failed verification/upload keeps local evidence and stops the operation. Journals receive generic command failures, never raw Docker/app stderr or backup contents. Manual private Docker logs may contain user IDs and should remain access controlled.

### Off-server storage

Local backups do not survive VPS loss. Off-server storage is **not configured by default**; no account, credentials, encryption key or remote destination is invented. The ready adapter `deploy/upload_backup.py` uses the standard rclone CLI with your configured remote; source changes are unnecessary. Install rclone on the future VPS, configure an encrypted remote/private destination with independent retention and least-privilege credentials, and install the adapter/configuration:

```sh
sudo apt-get install rclone
sudo rclone config --config /var/lib/safecheck/rclone.conf
sudo chmod 0600 /var/lib/safecheck/rclone.conf
sudo install -m 0700 deploy/upload_backup.py /etc/safecheck/upload-backup
sudo install -m 0600 deploy/offsite.json.example /etc/safecheck/offsite.json
```

Replace only the remote/prefix placeholder in `/etc/safecheck/offsite.json` with the name configured by rclone and your private backup directory. It stores absolute binary/configuration paths and the destination selector; authentication stays in root-owned private `rclone.conf`. Its location under `/var/lib/safecheck` permits OAuth credential refresh under the supplied systemd filesystem restrictions. If needed, select a different private JSON file with `SAFECHECK_OFFSITE_CONFIG=/absolute/path` in `operations.env`.

The adapter accepts the archive and checksum paths, validates their ownership/permissions and local SHA-256, uploads both with literal argument lists, then runs `rclone check --download` restricted to that pair. It separately downloads the remote checksum and compares it with the local archive's SHA-256. Any failed transfer, missing/changed remote file or checksum mismatch returns nonzero; raw rclone stdout/stderr and credential values never reach the journal. Read-back checks download the entire archive, so account for bandwidth and run time. Configure remote retention and test downloading/restoring from a different machine. The application can report whether the adapter is configured and whether its verified call succeeded; it cannot establish independent provider health or remote retention.

Optional nonsecret `/etc/safecheck/operations.env` applies flags to systemd invocations:

```ini
SAFECHECK_OPS_OPTIONS=--retention-days 14 --offsite-command /etc/safecheck/upload-backup
```

Protect this file and the executable as root-owned `0600` and `0700` respectively. Run `sudo python3 deploy/ops.py --offsite-command /etc/safecheck/upload-backup backup` once before enabling the daily timer. Tests mock rclone and do not use external storage. An independent service must alert on a missing daily backup or unreachable VPS: the local health monitor cannot observe its own machine's disappearance.

### Optional administrator notifications

Notifications remain disabled unless **both** an explicit `--alert-token-file` and `--alert-chat-id` are provided. Use a dedicated monitoring bot when practical, a positive numeric administrator ID, and a root-owned token file with mode `0600`. The recipient must have started that bot. The application bot token and `ADMIN_IDS` are never silently reused. Add flags to `SAFECHECK_OPS_OPTIONS` (paths/ID only; never the token):

```ini
SAFECHECK_OPS_OPTIONS=--alert-token-file /etc/safecheck/alert-token --alert-chat-id REPLACE_WITH_POSITIVE_ADMIN_ID --outbox-max-age 3600
```

Combine these with offsite flags when needed. Alerts cover service/dependency failure, stale outbox work, newly increased terminal job counts, and recovery. Existing terminal counts are reported once on first monitoring. Unresolved health/stale-work reminders are limited to hourly; unchanged historical terminal counts do not repeat. Delivery errors remain generic. Tests mock Telegram transport and never send real messages. A manual monitor invocation with real configured credentials can send an alert; `--dry-run` cannot.

## Application update and rollback

Build/load the reviewed version under a new explicit version or immutable digest; never overwrite a running release tag. Update only `SAFECHECK_IMAGE` in the nonsecret Compose env file. Keep the previous image. Use:

```sh
sudo python3 deploy/ops.py --dry-run deploy
sudo python3 deploy/ops.py deploy
sudo python3 deploy/ops.py status
```

Deployment validates image/configuration, requires healthy existing infrastructure, stops the old poller gracefully, creates and independently restores a fresh backup, records the old image ID and backup in private `/var/lib/safecheck/last-deployment.json`, runs migrations, checks schema/dependencies without Telegram, then starts and waits for a healthy poller. It does not replace PostgreSQL/Redis containers. If backup/verification/upload fails before migrations, it resumes the exact previous bot container. Once migrations start, failure leaves the bot stopped for review; there is no automatic migration downgrade or unsafe old-image restart.

`rollback --image safecheck:PREVIOUS_NUMERIC_VERSION` (or the saved full `sha256:…` image ID) checks the previous image against the current schema **before** stopping the running bot. It refuses incompatible migration heads. After that check, it atomically saves the selected image in the owned regular Compose env file, preserving other settings. Later `start` calls and host boots therefore retain the rollback. A symlink or duplicate image selectors are rejected before stopping the bot. Application rollback is suitable only while that image accepts the current schema. Rolling back schema/data requires restoring a predeployment backup and selecting the matching application image; writes since the snapshot will be lost. Rehearse that path on a separate test VPS. Review subsequent data changes before choosing it.

## Controlled database restore

First download the archive and its checksum from off-server storage and run `verify`. Restore requires explicit `--confirm-restore safecheck`, verifies the selected archive in isolation, checks PostgreSQL/Redis health and maintenance Redis credentials before stopping polling, creates/verifies/uploads a rescue backup of the current database, then **replaces** the dedicated `safecheck` database. It preserves the selected archive from retention, clears the dedicated Redis DB 0 to discard drafts referring to replaced rows, checks the restored migration head, and starts polling only on successful checks. This procedure requires healthy current PostgreSQL/Redis and enough capacity for the rescue backup; a completely lost database requires provisioning a new isolated installation and importing the verified archive manually under an operator's recovery plan.

```sh
sudo python3 deploy/ops.py --dry-run restore /var/backups/safecheck/SAFE_ARCHIVE.dump --confirm-restore safecheck
sudo python3 deploy/ops.py restore /var/backups/safecheck/SAFE_ARCHIVE.dump --confirm-restore safecheck
sudo python3 deploy/ops.py status
```

This Redis instance/database must be dedicated to SAFECheck. Restore never clears a shared/remote Redis service. The maintenance ACL permits only `PING`/`FLUSHDB`; its password is mounted only into Redis, not the application. A restored old schema and newer image fail closed; select the matching image before retrying checks/start. Restore failure after database replacement leaves polling stopped, and the rescue archive remains available.

## Verification without production access

`python3 deploy/ops.py --dry-run …` prints commands without Docker calls, file writes or Telegram requests. `pytest -q tests/test_operations.py` uses mocked Docker/Telegram transport and temporary private archives. The tests cover backup ordering, permissions, retention, corruption, isolated verifier cleanup, failed-update behavior, compatible rollback checks and alert opt-in. Real host reboot/timer behavior, off-server credentials, alert delivery and a full disaster-recovery rehearsal remain VPS acceptance tasks.
