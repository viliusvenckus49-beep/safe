# SAFECheck 2.9.0 — founder and moderator roles

The existing numeric-ID owner is displayed as SAFECheck founder; other currently authorized administrators as moderators. Roles appear on shared /ask, /rep and profile cards in LT/EN/RU, independently from status. Active roles automatically supply TRUSTED. Revoking access removes the role and its automatic TRUSTED; existing manual/TOP designations remain independent. Active SCAM suppresses TRUSTED. Unknown usernames cannot acquire roles. No new permission level or schema; current owner protections and database revocation precedence remain.

Manual TRUSTED removal receipts distinguish role-based continuation from TOP-derived continuation. No fake role confirmation date. TOP display and existing commands/navigation unchanged.

Security review: role checked through existing fresh database authorization, numeric identity only, role metadata cannot grant authorization, username changes cannot transfer role, unknown placeholders not linked, SCAM precedence preserved.

Focused validation:73 existing TRUSTED/admin/result-card tests passed5.15s and6 new role regressions passed0.71s. Ruff check/format115 files, mypy38 modules, git diff check and Docker build passed. No migration required.

Full Docker PostgreSQL/Redis suite393 passed75.40s, no skips.

Live2.9.0 polling verified; owner founder role and TRUSTED verified against live service. Alembic schema/startup checks passed, head0008 unchanged. Private backup /tmp/safecheck-before-roles-20261005.dump; rollback safecheck-rollback-2-8-4 retained.
