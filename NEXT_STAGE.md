# Implemented stage: administrator-controlled reputation (2.1)

Requested 2026-10-03. Implemented after baseline checkpoint cb8e2e8; see RELEASE_2_1.md for verification and deployment.

1. Numeric-ID authorized administrators can add/remove reputation and clear a selected user's reputation. Preserve immutable event/audit history; resetting must not destroy original events.
2. Every user +REP/-REP requires an administrator decision. Pending requests do not affect reputation; approval/rejection must be transactional, audited and idempotent. Adapt anti-abuse checks, queues, UX, migrations and tests together.
3. Administrators can include/exclude specific people from TOP 10 without changing REP; preserve audit history. TOP 10 excludes negative reputation totals. Entries have contextual buttons linking to the user's Telegram profile when identity permits. Handle missing usernames and unavailable profiles gracefully.
4. Polish Lithuanian presentation with restrained HTML bold formatting and consistent status cards. Escape all external content.

Deliver in sequence: architecture/migration and admin REP controls; user REP moderation; leaderboard links/filter; presentation polish; independent review and full QA. Existing live reports and reputation must survive migrations.

Additional owner request: clear all current REP. Applied global audited reset, preserving history and rejecting pending requests.
