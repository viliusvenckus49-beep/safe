# Independent operations review · 2.13.0

The independent `operations_review` agent inspected `deploy/ops.py`, `deploy/upload_backup.py`, `deploy/secret_entrypoint.py`, `docker-compose.production.yml`, `app/health.py`, the systemd context, operation tests and documentation. The review was read-only: no Docker/network commands, live database actions or secret-file reads were performed by that reviewer.

Two material findings were raised and fixed by the coordinating engineer:

1. Rollback selected the old image only for its current invocation. A subsequent `start` or host boot could use the newer selector still saved in Compose env. The final implementation checks schema compatibility, atomically persists the selected image in an owned regular nonsecret env file, and only then stops and starts the bot. Other configuration is preserved; symlinks/duplicate selectors fail before stop; dry-run writes nothing.
2. Restore first used Redis after replacing PostgreSQL, despite documenting healthy infrastructure as a precondition. The final implementation checks PostgreSQL/Redis health and the separate Redis maintenance user's PING before stopping the poller or replacing the database. Invalid credentials/unhealthy Redis therefore do not start the destructive sequence.

The reviewer independently inspected both final fixes and reported **no remaining material finding in this scope**. Backup verification restores in a disposable container with no network, ports or host volumes. Offsite command errors are captured and rendered generically; retention preserves the selected restore archive.

Execution evidence belongs to the coordinating engineer: **44 mocked operations tests passed**, included in the **569-test final integration suite**. A real unique disposable Compose project with PostgreSQL/Redis and fake Telegram passed initialization, mounted-secret loading, health, backup/checksum, separate verifier restore, rescue backup, database restore, stale-FSM clearing and monitoring after the final changes. A fresh private live backup was also independently restored before temporary bot replacement.

Real VPS reboot/systemd timers, configured external backup storage, independent remote download/restore and actual notification delivery remain deployment acceptance tasks. Mocked transport tests and read-only review do not establish those outcomes. The temporary testing runtime provides no permanent hosting guarantee.
