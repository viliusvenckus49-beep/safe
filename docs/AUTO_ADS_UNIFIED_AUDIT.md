# AUTO ADS unified audit and implementation plan

## Baseline and scope

Reviewed 2026-10-21, base `0206827ac06cdf926010df4893ff0eaa12c82e31`, branch
`safecheck-auto-ads-unified`. Main is `2f6b34b`; moderation PRs #42–#44 are
unmerged and contain migrations 0017–0019. Stack this change on #44; do not
rewrite those migrations, merge PRs, or deploy to VPS.

## Actual implementation

* `app/models.py`: AdCampaign.id already identifies each ad. Immutable
  AdContentVersion, actor-bound AdContentDraft and per-group AdSchedule retain
  content, history, intervals, validity and next run. Campaign DRAFT means a
  non-archived object, not necessarily an unscheduled advertisement.
* `app/ads_services.py`: numeric administrator ACL, serialized mutations,
  optimistic content revision, isolated field drafts, preview before save.
* `app/ads_publication.py`, `app/bot/ads_publisher.py`: durable unique slots,
  pinned content version, worker leases/fences, expiry, rate gates, RetryAfter,
  failed/ambiguous delivery distinction. Reuse this worker, not a second sender.
* `app/ads_global.py`: per-ad policies, approved managed groups, resumable bulk
  operations. FUTURE expands recipients automatically; this conflicts with the
  requested explicitly confirmed audience snapshot. Preserve existing recipients
  and archive the previous policy choice in audit when disabling future expansion.
* `app/ads_customers.py`: one current numeric-ID client per ad, grant-bound
  drafts and revocation. Existing hashed links only select a pre-assigned ad;
  they do not bind a new client and have no expiry. Preserve those links.
* `app/ads_content.py`, `app/bot/ads.py`: native Telegram entities, URL validation,
  photo/video/albums and extended legacy buttons. Missing animation/document
  delivery. Preserve legacy button kinds while adding individual URL controls.
* `app/bot/ads.py`, `global_ads.py`, `ads_schedule.py`, `ads_customers.py`:
  fragmented private menus, eight-row legacy lists, content preview/recovery,
  customer assignment and schedule management. Replace visible entry/navigation
  with one AUTO ADS hierarchy, retain compatibility for old safe callbacks.
* Redis holds Telegram FSM; actual content, grants and publication state are
  PostgreSQL durable. It is not a second authoritative advertisement store.
* `app/locales/` and `app/i18n.py`: LT/EN/RU context-selected translations.
* No payment ledger, STATUSAS paid-until field or €30/€20 purchase validator was
  found. Existing publication validity and client editing deadlines are distinct.
  Do not infer payment from a date or implement a new billing policy silently.

## Safe implementation plan

1. Reuse campaign IDs and assignment policies; no copying/rekeying legacy ads.
   Preserve legacy heterogeneous per-group settings until an explicit per-ad edit.
2. Add expiring hashed, single-use invitation records; serialize consumption with
   current ACL/grant changes. Durable editor selectors confer no authority and
   check the current numeric-ID grant each time.
3. Add per-ad management service and native Telegram UI: four-row real lists,
   filters/search, groups, interval/validity, lifecycle, statistics/audit, links.
   Use existing content staging and immutable revision save.
4. Keep audience snapshots. Validate Telegram rights outside SQL locks, then
   revalidate current local authority/version inside the transaction. Cancel only
   unstarted attempts when settings change; never replay ambiguous deliveries.
5. Extend media validation, preview and the same durable sender together. Migrate
   the publication-part kind constraint without removing content/history.
6. Tests: isolated SQLite contracts, real isolated PostgreSQL migrations and
   concurrency where available, existing ADS regressions, locale parity and native
   Bot API request contracts. Mocked API tests are not live Telegram validation.
7. Produce a reviewable PR and staging-only instructions; no production actions.

## Data protection

Additive migration 0020 follows 0019. No campaign, grant, content, history or
publication rows are deleted. Reject downgrade if invitation history exists.
No raw token in database, audit, FSM or logs. Existing in-flight sends cannot be
unsent; pause/delete affects future/unstarted work and must report that limit.
