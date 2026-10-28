# CRIMSON SAFECHECK command-menu release — 2026-10-28

Deployment completed at 11:01 UTC; independent verification completed at 11:03 UTC.

- Bot: @RedSafeCheckBot.
- Source branch: fix/hide-command-suggestions-20261028.
- Exact source: 6d7ed89f17b966acac0ea409bf2a260e0df2bdae.
- Running image: safecheck:2.15.4-6d7ed89f17b9.
- Schema: 0023, unchanged.
- PR: https://github.com/viliusvenckus49-beep/safe/pull/57 — unmerged.

## Behavior

Slash suggestions expose start, ask, report; group and group-administrator scopes additionally expose help. The rep alias and all moderation commands remain executable through their existing authorized handlers but are hidden from suggestions. All neutral/LT/EN/RU global and group/admin-group menus were checked through Telegram getMyCommands. The configured private administrator scope was also verified. Existing private per-chat menus refresh on /start or language selection.

The installed command-menu module and handler SHA-256 hashes match the reviewed source. The optional sandbox operator's embedded copies and pinned hashes were updated to keep its source-integrity checks valid; the retired test bot was not recreated.

## Verification

- Full CI: 2680 passed; lint, format, mypy, Docker, packaged runtime and backup/restore rehearsal passed.
- Local full suite: 2523 passed, 157 integration checks skipped because local PostgreSQL/Redis were not configured.
- Backup-first production release succeeded. Data-preservation checks passed; no aggregate data counts decreased.
- Bot, PostgreSQL and Redis healthy; polling, worker and advertising heartbeats healthy.
- The other five running containers retained their IDs, images and start times, including the separate redsafe-gate bot. Only the main SAFECheck bot was replaced.
- The first independent check used relative source paths unsupported by the installed package layout. The read-only verifier was corrected to resolve installed module paths; final verification passed without additional application changes.

CI: https://github.com/viliusvenckus49-beep/safe/actions/runs/40674274997
Deployment: https://github.com/viliusvenckus49-beep/safe/actions/runs/40676265896
Final live verification: https://github.com/viliusvenckus49-beep/safe/actions/runs/40676909584

The release includes verified server-local backups. This report does not establish off-server database backup coverage. Runtime credentials and database exports are not included in GitHub.

For future work, fetch the source branch and use the immutable source above as the deployed baseline. The repository default branch can be older. Working copies under /tmp are ephemeral; push all completed and unfinished development work to GitHub branches. The ops/command-menu-verification-20261028 branch contains the read-only verification workflow and this release record; it is not the application deployment source.
