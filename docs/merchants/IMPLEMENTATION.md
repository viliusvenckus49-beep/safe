# REDSAFE merchants integration plan

Baseline: deployed source 6d7ed89f17b966acac0ea409bf2a260e0df2bdae, schema 0023. GitHub branch and working tree verified on 2026-10-28. Development only; VPS deployment requires separate explicit authorization.

## Existing integration points

- `app/bot/keyboards.py`: preserve home text and existing callbacks; add private merchant entry and administrator category.
- `app/bot/handlers.py`: register one separate merchant callback prefix before generic routes; preserve /ask, /rep and profile behavior.
- `app/bot/screens.py`: edit-in-place with existing Telegram fallback. Search/registration input uses bounded FSM sessions and the same panel anchor.
- `app/services.py`, `app/repositories.py`: existing numeric identity, admin authorization and REP ledger. No merchant REP balance or TRUSTED grant is added.
- Existing reputation ledger: legacy events + APPROVED requests + administrative adjustments. Public review history is restricted to approved request comments and historical accepted events; administrative corrections can make totals differ from the number of reviews.
- `app/models.py` + Alembic: additive merchant entries, proposals, searchable name bigrams and singleton catalog settings; stable existing User foreign keys. Proposal content is separate from published fields.
- Existing AuditEvent: catalog-scoped metadata and actor IDs, no secrets, no private pending proposal text in public screens.
- LT/EN/RU: a new locale catalog merged into the existing catalogs.

## Phases and checks

A. Route and layout foundation, city catalog and coherent navigation tests. Empty screens mean actual no-data states, never production sample merchants. Incomplete registration/admin actions are not exposed during this phase.
B. Additive schema, SQL pagination, normalized name search with indexed bigram postings, exact current username lookup and SCAM exclusion. Test migration preservation and ORM agreement.
C. Durable registration/change/removal proposals, per-owner uniqueness, expiry, rate limits, version checks, atomic moderation, audit and administrative settings. Test owner/admin isolation and PostgreSQL concurrency.
D. Shared REP aggregate query, real profile/check links and approved review history. No numerical username reassignment or merchant-to-TRUSTED shortcut.
E. Finish private input flows, search/review filters and pagination, LT/EN/RU and complete Telegram journeys; run regression and CI. Refresh the existing pinned optional test bundle as required by its source-integrity contract, without executing it or recreating any test bot.

## Policy

One merchant entry per stable existing user; historical removed entries can be re-submitted for review. Public edits remain unchanged until approved. Catalog approval is not TRUSTED and never overrides SCAM. Active SCAM identities are excluded at query time, including unresolved username-linked SCAM according to the existing identity policy. Ordinary registrations require a known numeric ID. Review authors are anonymous by default until an administrator enables publication under their privacy policy. Individual proposal cancellation and owner removal requests preserve audit/history.

Registration drafts are durable proposals with a bounded lifetime; submitted proposals persist across bot restarts. No production seeding or destructive migration. Source/schema compatibility in `production_release.py` must be reviewed separately for 0024 before a production deployment; this task does not silently extend the existing 0023 production guard.

## Recorded phase validation

- A: 3 foundation tests passed (city catalog, input bounds, language keys and callback sizes). Navigation entry points remain unmounted until the transactional module is ready, so no incomplete UI is exposed.
- B: 31 data, reputation-regression and migration tests passed. SQLite migration reaches 0024 and matches model metadata; downgrade guards retain any catalog data.
- C: 8 transactional service tests passed; one PostgreSQL-only simultaneous approval test awaits CI. Draft resumption, replay, authorization, edits, soft removal, SCAM exclusion, policy controls and daily limits covered.
