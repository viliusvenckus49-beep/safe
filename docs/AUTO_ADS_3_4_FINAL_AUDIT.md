# AUTO ADS 3.4 — final security and integration review

Reviewed foundation: PR #37 HEAD
`34f975a5922ee7dc69f381b4aa1983e71cda6974`. The 3.4 patch is developed on an
isolated branch whose base tree is identical to that commit. Final patch/CI
identities are recorded in the linked pull request. No deployment was performed.

The review is a separate source-reading and adversarial-test pass after initial
implementation, not a claim that CI or request doubles prove real Telegram delivery.

## Findings corrected during implementation/review

| ID | Severity | Cause | Correction and regression evidence |
| --- | --- | --- | --- |
| C1 | HIGH | An unbound FSM text/button edit could resume after revoke and reassignment of the same numeric customer and accidentally acquire the new grant. | Pin the original grant in the input context and check it inside the final staging transaction. Bind every SQL draft to its immutable grant ID. Real SQLite/PostgreSQL old-FSM/reassignment tests and native UI regression verify no draft/version is committed. |
| C2 | HIGH | Reusing an actor's draft through the other capability (when an administrator is also a customer) could confuse which authority had approved the edit. | Admin and customer services reject drafts from the opposite scope, and list only their own scope. Both SQL dialects test attempted cross-scope saves and unchanged content. |
| C3 | MEDIUM | Restricting all grant management to unarchived campaigns would prevent revoking retained read access after archival. | Allow administrators to inspect/revoke archived grants; new assignment/link creation still requires an unarchived campaign. Both SQL dialects verify archived reads stop after revocation. |
| C4 | MEDIUM | SQL CHECK expressions involving nullable revocation actors could evaluate to NULL and accept incomplete revocation metadata. | Explicit non-null/positive actor and non-null time requirements for REVOKED grants/links. The additive 0016 constraints and real PostgreSQL integrity contracts protect this boundary. |
| C6 | MEDIUM | Looking up the latest retained grant by campaign had no matching full-history index; the active-only unique index cannot satisfy this query. | Add `(campaign_id, id)` index in the new 0016 extension and metadata. A real PostgreSQL EXPLAIN regression with 1,000 retained grants verifies an indexed, bounded newest-grant lookup. |
| C5 | LOW | A dual-role administrator returning from customer editing into GLOBAL ADS creation could retain the previous FSM capability marker. | Explicitly reset scope for ordinary and global campaign creation; every callback derives scope from its registered callback type and every service still rechecks authority. |

Initial test failures also exposed an import error on the new start route and
incorrect test assumptions about callback alerts, cumulative message transcripts,
ORM objects after rollback, and the planner advancing its own next slot. These were
corrected; no successful action was fabricated to satisfy those tests.

## Security conclusions

* Numeric grant authority is independent of global/local administrator authority.
  Customers cannot grant themselves access or reach group/policy/schedule setters.
* A token is a high-entropy campaign selector, never a bearer authorization grant.
  Only hashes persist, no arbitrary expiry is invented, and rotation invalidates
  old selectors. Private one-time copy/display is intentionally not recoverable.
* Final saves, replacement and revocation serialize through the existing SQL ACL
  boundary. Parallel PostgreSQL saves produce one new revision and one conflict;
  a save waiting behind committed revocation fails closed. Atomic rollback does
  not leave a half-revoked assignment or a partially inserted client draft.
* SQL uniqueness/FKs supplement service checks. Neither this PR nor the existing
  application claims privileged direct SQL access is tenant-isolated by RLS.
* Content validation and native preview remain shared with 3.1. No second parser,
  unsafe HTML interpolation or custom emoji offset implementation was introduced.
* Customer saves change immutable content selection only; existing schedules and
  pinned attempts are unchanged. Existing 3.2/3.3 lease/fencing, UNCERTAIN, partial
  album, rate limiting and authorization code is not changed by this feature.
* Existing SCAM/REP/TRUSTED/profile/moderation handlers and data remain intact;
  the ordinary home route only conditionally adds an authorized customer's entry.

## Validation

All database services/data are disposable; no real Telegram messages or
advertisements are sent. Request doubles are never reported as live delivery.

| Completed check | Result |
| --- | --- |
| Initial complete suite, PostgreSQL 17 + Redis 7.4 | 1,779 passed, four existing Alembic configuration deprecation warnings, 305.17 s |
| Final complete suite including the last regressions | **1,791 passed before the final index correction**, no skips, four existing Alembic configuration deprecation warnings, **297.42 s** |
| Customer service/UI/migration tests, including real PostgreSQL and Redis | 94 passed, 29.91 s |
| Ruff check / format check | Passed, 285 files formatted |
| mypy | Passed, 93 source files |
| Python compilation | Passed for app, migrations and tests |
| Alembic upgrade through 0016 and schema drift | Passed; real PostgreSQL migration/retention tests also participate in the complete suite |
| Production Compose validation | Passed using dummy secret files only |
| Docker build | Passed, review image `d10c650d86eccff1ba3104931da0446d846c43b2e275f8b99013b4f788ccb99c` |
| Packaged worker startup/shutdown | Passed, read-only image, no network, disposable tmpfs SQLite |

Final GitHub CI is a separate remote gate. Its run IDs/conclusions are recorded
in the PR validation section after completion; use the branch-specific badge and
PR checks for the final remote head. Local request doubles are not substituted
for live Telegram verification. The customer cases extend the unchanged 1,697-test foundation. Final index
correction validation and remote run evidence are recorded in the PR. No unrelated user worktree edits were included.

[![3.4 quality checks](https://github.com/viliusvenckus49-beep/safe/actions/workflows/ci.yml/badge.svg?branch=auto-ads-3-4-20261019)](https://github.com/viliusvenckus49-beep/safe/actions?query=branch%3Aauto-ads-3-4-20261019)

## Residual risks / 3.5 readiness

* A Telegram API double proves request shape and application transitions, not live
  transport, client rendering, Custom Emoji entitlement or old-client copy support.
  Selectable URL text is provided as the manual copy alternative.
* Redis remains necessary for UI/FSM availability. SQL grants, token hashes,
  drafts, immutable versions and schedules persist independently. In-progress
  content input can be lost if FSM data is intentionally evicted; saved SQL drafts
  can be recovered. Existing production cold-start Redis dependency is unchanged.
* A response lost after token creation requires explicit regeneration. Plaintext
  cannot be recovered from SHA-256; this is an intentional security tradeoff.
* Revocation cannot retract preview/content already delivered while authority was
  valid, or undo Telegram requests already in flight. A final save rechecks access.
* Shared short ACL/content writes serialize. Production-sized content history and
  multi-customer workload profiling, backup/restore, staging and live compatibility
  rehearsals belong in 3.5. No exactly-once Telegram promise is made.
* Deployment still requires manual dependency merges/rebase and regression checks:
  #31 → #32 → #33 → #35 → #36 → #37 → this 3.4 PR. Do not auto-merge or deploy.

The architecture supports 3.5 integration/hardening without introducing customer
schedule authority. Readiness is conditional on the completed final checks and
manual review; this is not authorization to activate advertisements.
