# Independent final security audit

Audit date: 2026-10-06. Initial source reviewed: SAFECheck 2.12.0. Final candidate validation was supplied by the coordinating agent after remediation. No live bot, production database, credential files, or real Telegram transport were used for this audit.

## Material finding: administrator revocation race — fixed

Severity: **high**. The affected methods in `app/services.py` were `add_scam`, `remove_scam`, `moderate`, `moderate_reputation`, `_adjust_rep`, `set_top_visibility`, and `admin_reset_all_reputation`.

These methods checked administrator authority before asynchronous preparation or database lock acquisition. An owner could commit revocation after that check while the update was waiting. The already running operation could subsequently create or remove a SCAM designation, approve a report or reputation request, change reputation or TOP visibility, or reset all reputation using the revoked grant. Fresh administrator queries alone did not close this interval.

The coordinating agent added `Service._admin_write_lock(actor)` and applied it to all seven mutation paths. Final source was independently reread: the helper is at `app/services.py:159`, and calls precede mutation locks at lines 369, 399, 426, 531, 574, 638, and 694. It acquires the shared identity metadata lock used by owner grants and revocations, then checks current authority before acquiring user locks and applying changes. The final `AdminService.change` was also independently reread: owner grants, revocations, and retry handling acquire this same metadata lock before changing the administrator row and committing. This provides a transaction ordering: a mutation that holds the shared lock completes before a waiting revocation; a mutation acquiring it after committed revocation is rejected. Target resolution commits its preparation separately, so authority must be checked again in the mutation transaction. TRUSTED changes and SCAM identity supplements already checked authority under that shared lock.

`tests/test_final_security.py` adds seven isolated regression cases that commit owner revocation at the relevant write-lock boundary and assert rejection and unchanged protected state. An eighth case verifies that a previously loaded administrator row is refreshed after revocation in another session. The coordinating agent reported all eight passing after remediation, with additional PostgreSQL revocation ordering contracts passing against an actual isolated PostgreSQL instance.

The original local pytest attempts stalled in the default execution sandbox and were stopped. They did not supply a completed baseline exploit run. The finding is grounded in the reviewed control flow; the passing remediation regressions and PostgreSQL ordering checks are validation results reported by the coordinating agent.

## Other reviewed boundaries

- Authority uses numeric Telegram IDs. Owner-only delegation checks, immutable owner handling, persistent configured-admin revocation, and fresh administrator row loading were reviewed. Username, display name, TRUSTED designation, and leaderboard inclusion do not grant administrator access.
- Administrator delegation, SCAM supplements, TRUSTED revocation, reputation adjustments, and report submission callbacks check server-side authority, state, and confirmation data. Confirmation state belongs to the actor's FSM context. Protected management and moderation callbacks enforce private-chat access. No additional material bypass was identified in the reviewed flows.
- Identity resolution prefers observed numeric identities without transferring historical reputation or reports from username placeholders. SCAM supplements transfer only the confirmed record and reject conflicting identities. Existing TRUSTED and reputation replay handling binds request keys to actor, target, and operation. Self-voting, reciprocal voting, duplicate votes, cooldowns, and moderation requirements are enforced in the service layer.
- Presentation functions reviewed escape user-controlled names, usernames, report reasons, reputation comments, and evidence links before HTML rendering. Application error and transport logs reviewed record exception types and operation metadata without exception messages, request bodies, tokens, database URLs, or private evidence. No additional material HTML injection or secret logging issue was identified in those reviewed boundaries.

## Validation and limits

Final validation reported by the coordinating agent: **569 tests passed in 111.52 seconds**, including **44 operations checks** that also passed in a focused run. These totals include work by other audit agents and are not represented as independently rerun by this reviewer.

This reviewer directly inspected the initial security-sensitive service, repository, callback/FSM, identity supplement, TRUSTED, presentation, and logging source, authored the eight focused regression cases, and independently reread the final shared-lock helper, all seven usages, and owner revocation implementation after stalled test processes were terminated. The newly introduced health integration, operations status module, and deployment secret entrypoint were not independently inspected by this reviewer. The coordinating agent reports an independent operations review with no remaining material finding in those components. Production deployment and runtime behavior were outside this audit's direct verification scope.
