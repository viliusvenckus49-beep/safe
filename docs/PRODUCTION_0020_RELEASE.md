# Production release to schema 0020

Authorization: the user's 2026-10-21 instruction, “reikia idiegti viska i pagrindini
bota”, explicitly authorizes deployment to the main bot. It supersedes earlier
instructions to keep these changes staging-only. It does not merge any PR.

Candidate: `safecheck-production-3-0`, based on the reviewed AUTO ADS application
and moderation stack at `ebbde1a`. The staging operator and injected staging
runtime are never used in production. The application runs its normal transport;
client access remains governed by per-ad grants, not the sandbox client allowlist.

The manual `vps-deploy.yml` workflow runs the full service-enabled tests and code
checks before SSH. It uploads the exact Git archive and invokes the dedicated
`deploy/production_release.py`, leaving the older 0016-only deploy route unchanged.
No token or database archive leaves the VPS.

The release verifies the current production project, unchanged data services,
credential paths and volumes, pinned migration files 0010–0020, available disk and
memory, known writers and unfinished/uncertain native work. A read-only GetMe
confirms the production identity; RedTrustBot is explicitly refused. Existing
environment settings and credentials are retained rather than copying test files.

An online private pg_dump is restored in a network-isolated, resource-limited
temporary PostgreSQL container. A dummy-token candidate runs the real Alembic
upgrade and drift check there. Legacy rows are compared by original column values
and cryptographic fingerprints; only migration 0020's documented FUTURE policy
conversion and appended audit rows are permitted.

After this passes, the existing operations lock is acquired and only the main bot
is stopped. Another fresh, frozen dump is created and independently rehearsed.
Only then is the production schema upgraded, retained rows checked again, and the
candidate's actual database/Redis readiness checked. The new bot starts with a
health wait. Database and Redis containers are fingerprinted again, and the image
selector is persisted only after success. Backups and private release state are
retained; existing backups are never pruned, and Redis is never reset.

Failures before migration restart the original bot. Once migration begins, the
release deliberately does not drop schemas, restore an older database or restart
an incompatible old image. A failed candidate is stopped, and the database,
Redis and frozen backup remain available for a forward recovery. This can require
operator intervention and extend downtime; it prevents silently discarding
updates processed by a candidate. Prior images and source directories are kept.

The release preserves existing group module switches and optional MTProto/relay
configuration. Installing the code does not grant new group administrators,
activate ads, enable an unconfigured user session, or extend paid entitlements.
Group settings can be managed through the existing owner controls. Public
moderation/advertising actions are not issued by the release itself; normal
background processing resumes only when the production bot starts.

Fresh read-only preflight before this implementation confirmed main bot image
`safecheck:2.15.4-4836d1975afc`, healthy bot/PostgreSQL/Redis, and 32 GiB free disk:
https://github.com/viliusvenckus49-beep/safe/actions/runs/37657992529/attempts/3.
Final deployment evidence must be recorded separately after execution.
