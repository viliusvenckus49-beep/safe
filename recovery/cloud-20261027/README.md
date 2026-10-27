# Cloud workspace preservation — 2026-10-27

This branch preserves files missing from the GitHub history reachable through the 76 branches inspected on 2026-10-27. It starts at 088ff2c521665630e031d4c2786564eb9f7efb44, the last production source recorded as verified on 2026-10-23. The actual running VPS version has not been checked again for this preservation task.

## Recovery files

- `files/safecheck-redsafe-check/app/repositories.py.txt`: exact bytes of a local older working version, originally `/workspace/safecheck-redsafe-check/app/repositories.py`. Its local base HEAD was f1e3e32c9a8dcfdbdd1554df0e27f49f4bb14fdb. This is archival material, not a replacement to copy over the current application blindly.
- `files/safecheck_bot/CONTINUATION.md`: exact historical working notes from the old local checkout. Its information is dated and does not describe the current deployment.
- `manifest.json`: original paths, lengths, and SHA-256 hashes. Verify hashes after download.
- `git-audit.json`: comparison of 21 local checkouts against 76 GitHub branches, including exact matching file versions and matching root trees.

Of 26 modified/untracked working files inspected, 25 already matched files on GitHub branches. The remaining repository module above and the historical continuation notes are now archived here. Earlier local commit IDs can differ while complete source trees match GitHub; these tree matches are recorded in the audit. This audit does not claim to back up ignored local archives, database contents, Telegram sessions, runtime secrets, or all workstation files.

## Resume in a fresh environment

1. Clone `https://github.com/viliusvenckus49-beep/safe.git`.
2. Fetch all branches and inspect `safecheck-ads-group-detach` for the last recorded application source; consult a fresh VPS check before considering production changes. The repository default branch may be older than deployed feature branches.
3. Check out `backup/cloud-preservation-20261027` to retrieve this recovery directory. Verify the files against `manifest.json`.
4. Use a new development branch for future changes; preserve existing user/admin permissions and business logic. Do not deploy or merge automatically.
5. Install dependencies using the existing repository instructions and lock/configuration files. Never start a polling bot with production credentials in a development environment.
6. Record the current branch, committed SHA, validation results, and next steps in a committed state file and push unfinished work to a development branch regularly.

A GitHub code snapshot does not replace PostgreSQL backups, media/file backups, or encrypted configuration backups. The existence and freshness of off-server backups must be verified separately. Do not place `.env`, credentials, Telegram session files, or database exports in this repository.

The previous `/workspace` filesystem reported zero available bytes even after regenerable caches were removed. A working copy can temporarily be placed under `/tmp`, where approximately 4.9 GiB was available; `/tmp` is ephemeral and must never be the sole copy. Creating or resizing the managed environment requires the hosting application's environment controls; this repository does not change that configuration.

No production code, workflow, configuration, database, or running bot was changed by this archival branch.
