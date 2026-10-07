# SAFECheck 2.13.2 database recovery

The original database remained in the managed workspace while the VPS started a fresh PostgreSQL database. This recovery merges the legacy records into the existing VPS database so that today's activity remains available. It preserves the current Compose project and persistent database volumes.

Isolated staging passed against a fresh VPS backup on 2026-10-07 (GitHub run `37664027339`, commit `b3836f6543c149c11d68dae76869b4c4d1afe603`). The plan retained current rows, matched 18 identities, and added 128 users, 31 SCAM records, 9 TRUSTED designations, 3 administrator records, 8 groups, 164 observed members, and 11 REP requests. The initial VPS audit showed 25 users and 2 REP requests; another user was recorded before staging. The live target contained 26 users before import. All 623 tests passed, along with Ruff, formatting, mypy, and dependency checks.

The VPS code update passed on run `37664027179`. Image `safecheck:2.13.2-b3836f6543c1` and package version `2.13.2` were verified healthy after the update. Pre-update backup: `/var/backups/safecheck/safecheck-20261007T181126Z-695274de.dump`.

Live merge succeeded on run `37665105963` at 18:16 UTC. Independent audit run `37665288503` confirmed the following counts and restored a new full backup into a disposable verifier:

| Records | Count |
| --- | ---: |
| Users | 154 |
| REP requests | 13 (7 approved; 2 requests created today) |
| REP events / adjustments | 1 / 1 |
| SCAM history | 31 (19 active, 12 removed) |
| TRUSTED designations | 9 active |
| Administrator records | 3 (2 active, 1 explicit revocation) |
| Groups / observed members | 8 / 164 |
| Reports | 3 |
| Legacy import markers | 1 |

The two requests created on the VPS today remain in the database. The 11 imported legacy requests retain their status and relationships. A repeat import was applied to an isolated clone and returned `already_imported: true` with no new inserts. Bot, PostgreSQL, and Redis were independently verified healthy on version 2.13.2.

The verified pre-merge archive is `/var/backups/safecheck/safecheck-20261007T181538Z-f1909028.dump`. The post-merge archive `/var/backups/safecheck/safecheck-20261007T181630Z-4a6359aa.dump` was restored successfully during the independent audit. Private source snapshot and backups stay outside GitHub; the one-time receiving private key was retired. Deployment and recovery workflows are manual-only after this controlled activation, and recovery defaults to `plan`.

## Recovery procedure

1. Export a consistent legacy snapshot and retain a private copy of the source database.
2. Transfer an authenticated, encrypted snapshot. The receiving private key stays on the VPS; plaintext snapshots, database archives, and credentials stay outside the repository.
3. Deploy version 2.13.2, including the stale callback guard, before applying the live merge.
4. Run `restore_legacy.py plan`. It creates a fresh, verified VPS backup and restores it into an isolated, disposable PostgreSQL container. It applies the proposed merge to that copy, validating real database constraints without changing live records.
5. Run `restore_legacy.py apply`. It acquires the maintenance lock, stops the bot gracefully, takes another fresh verified backup, and repeats the isolated validation. It then checks that the live target snapshot still matches the validated snapshot and commits the merge in one transaction.
6. Restart the bot and require a healthy service. A failed post-merge health check restores the fresh pre-merge backup. The transfer receiver key is retired after success.

The disposable verifier uses a private network namespace and temporary database storage. Its application process runs the maintenance command without polling Telegram. Logs show aggregate counts and conflict codes rather than database records or credentials.

## Merge rules

Known users match by numeric Telegram ID. Username-only records match other username-only records; a username alone does not attach one person's identity or TRUSTED status to a different known ID. New surrogate IDs and foreign-key mappings preserve links between imported records.

Current records remain in the target database. Timestamp comparisons preserve newer profile and moderation changes; imported history, reputation, reports, evidence, SCAM records, groups, and administrative records retain their relationships. Colliding human-readable report references receive an available reference. Unsafe identity, reputation, request-key, or active SCAM conflicts stop the merge for review.

Old queued ban or recovery deliveries are marked obsolete rather than replayed. PostgreSQL uses a serializable transaction and an advisory transaction lock. The target digest check rejects changes made after validation. A `legacy_database_import` audit marker records the source digest; repeating the same snapshot is idempotent.

## Maintenance commands

Use a trusted application environment that loads its database credentials privately. The low-level utility supports:

```sh
python deploy/merge_database.py export /private/source.json
python deploy/merge_database.py plan /private/source.json
python deploy/merge_database.py apply /private/source.json --expected-target-digest TARGET_SHA256
```

`export` writes a private snapshot of the configured database. `plan` calculates a merge without writing records; its aggregate JSON output includes the target digest. `apply` performs the transactional merge. These low-level commands do not provide the wrapper's service stop, verified backup, isolated restore, or restart checks, so use the wrapper for live VPS recovery.

From a reviewed release checkout on the VPS:

```sh
sudo python3 deploy/restore_legacy.py plan /private/snapshot.sealed SOURCE_SHA256 </dev/null
sudo python3 deploy/restore_legacy.py apply /private/snapshot.sealed SOURCE_SHA256 </dev/null
```

Replace the payload path and digest with the reviewed encrypted snapshot and its expected canonical source digest. The wrapper discovers the active deployment from the bot container's Compose labels and uses the existing private deployment configuration. It retains the validated plaintext snapshot and aggregate plan under the root-only data-transfer directory for controlled retries.

After recovery, verify aggregate table counts, approved and pending reputation activity, active and removed SCAM history, TRUSTED designations, and administrator/group records. Confirm that the bot, PostgreSQL, and Redis are healthy. Keep both source and pre-merge backups until the result is verified.

## Existing Telegram messages

Internal user and record numbers can change during import. The stale callback guard rejects buttons on messages created before the recovery marker and clears their old conversation state. Users reopen the menu with `/start`; new menus and typed commands work normally. Existing chat messages remain visible.

## Operational scope

This procedure does not establish an off-server backup destination, systemd backup/monitor timers, or an independent uptime monitor. Their configuration and verification remain separate operational tasks described in [OPERATIONS.md](OPERATIONS.md).
