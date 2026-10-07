# SAFECheck 2.13.0 — final audit and operations

Released in the temporary testing environment on 2026-10-06. This increment preserves the existing bot and adds final security/data/UX fixes and reproducible VPS operations. It does not establish permanent hosting.

## Changes

- Seven privileged service mutations serialize with owner role changes and revalidate current numeric authorization inside the write transaction.
- SCAM identity supplementation rejects a different observed numeric owner of the username. A matching, explicitly confirmed ID transfers only the selected SCAM record; historical REP/reports remain on their original identity.
- Known IDs with an unresolved historical username SCAM match receive a separate LT/EN/RU warning, rather than a clear result or an automatic numeric accusation.
- Pagination accepts ASCII digits only; malformed callbacks preserve active report drafts. Administrator ID configuration rejects Unicode/oversized values.
- Runtime health requires recent successful Telegram polling and worker progress. Operational status exposes only aggregate pending/terminal job counts.
- Production Compose loads secrets from private mounted files, uses dedicated PostgreSQL/Redis volumes and authenticated Redis, and keeps the bot unprivileged with a read-only filesystem and bounded logs.
- Operations CLI provides backup/independent restore verification, update, controlled restore, persistent compatible image rollback, retention, optional rclone offsite upload and explicit opt-in operator alerts. Restore preflights Redis health and maintenance credentials before replacing data.
- systemd boot, daily backup and monitor templates plus setup/operation documentation are included.

No schema change: Alembic head remains **0009**. Tests use synthetic identities and isolated databases; no production REP, SCAM, report, role or group changes were made as QA fixtures.

## Executed verification

| Check | Result |
| --- | --- |
| Full Docker pytest suite, including isolated PostgreSQL/Redis | **569 passed in 111.52s**, no skips |
| Operations unit tests, mocked Docker/rclone/Telegram | **44 passed in 0.14s**, included in the full suite |
| Tests against the installed application in the final image; only tests mounted | **89 passed in 12.32s** |
| Ruff lint / format | Passed |
| mypy configured application scope | Passed; **49 source files** |
| `pip check`, local dev environment and final image | Passed |
| `git diff --check`; TODO/FIXME and token/private-key pattern scans | Passed; no matches in the source scan |
| Docker build `safecheck:2.13.0` | Passed |
| Production Compose acceptance with fake Telegram, unique disposable project | Passed after final fixes; real PostgreSQL/Redis, migrations, secret loading, health, backup/checksum, isolated restore verification, rescue backup, DB restore and stale FSM clearing |
| systemd unit syntax | Offline verification passed with a temporary `docker.service` dependency stub; units were not installed |
| Candidate live schema/startup checks | `alembic check` and `python -m app.main --check` passed |
| Live runtime | `safecheck:2.13.0`, running, Docker health **healthy**, restart **unless-stopped**; successful polling/worker heartbeat and dependency readiness confirmed |

Earlier baseline/adversarial runs exposed real regressions. They were fixed before the successful final run. Audit agents' default-sandbox pytest attempts stalled and were terminated; successful integration checks were run by the coordinating agent in Docker. Independent findings, direct review scope and limitations are documented separately rather than represented as independent test reruns.

Final Docker image ID:

```text
sha256:3bfe8c98a2e39c65dd7b77b243d93131817a0572a1cb474f62f96c056c85afe7
```

## Temporary runtime safety

Before replacement, the old poller was stopped and a transactionally consistent private custom PostgreSQL archive plus checksum were saved outside the repository. The archive was fully restored into a disposable PostgreSQL verifier with no network, ports or host volumes. Verification passed before the candidate started. The prior 2.12.0 container remains stopped as `safecheck-rollback-2-12-0`; both versions use head0009. No migration or data reset was run during deployment.

Private backup location is recorded in the internal continuation note, not distributed as part of the project. Neither secrets nor live database dumps are packaged.

## Reproducible commands

After installing development dependencies and providing isolated test URLs as described in README:

```sh
pytest -q
ruff check .
ruff format --check .
mypy app
python -m pip check
docker build -t safecheck:2.13.0 .
# Requires Docker, Compose and the three specified images; uses only fake data/Telegram.
python3 tests/run_operations_acceptance.py
```

On a configured VPS, exact setup and secret permissions are in [docs/OPERATIONS.md](docs/OPERATIONS.md). Existing installations use backup-first deploy; initialization refuses a nonempty database.

```sh
sudo python3 deploy/ops.py --dry-run deploy
sudo python3 deploy/ops.py deploy
sudo python3 deploy/ops.py status
```

## Remaining acceptance

- A real persistent VPS, reboot/timer acceptance and external uptime monitoring are still required. This workspace is temporary and provides no 24-hour uptime guarantee.
- Offsite destination/credentials, remote retention/encryption policy and independent download/restore rehearsal are not configured. The ready adapter is tested with mocks.
- Optional Telegram operations alerts remain disabled; real recipient authorization and transport delivery were not tested.
- Phone rendering, button wrapping and client-specific ForceReply behavior require user acceptance. Automated fake Telegram journeys verify logic, not visual appearance.
- Telegram Bot API limitations remain: only observed group members are backed up; arbitrary username/ID ownership cannot be verified; automatic bans need known IDs and sufficient group permissions.
- Rotate the previously shared bot token privately before permanent deployment. Credentials must stay outside Git/build contexts and reports.

Further details: [final engineering review](docs/FINAL_REVIEW.md), [security](docs/AUDIT_SECURITY.md), [data](docs/AUDIT_DATA.md), [UX](docs/AUDIT_UX.md), [operations](docs/AUDIT_OPERATIONS.md).
