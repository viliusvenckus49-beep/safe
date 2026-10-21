# CRIMSON SAFECHECK AUTO ADS: review and staging guide

## Result

One private Telegram hierarchy: **AUTO ADS → My ads → selected ad**. Each existing
`AdCampaign.id` is its `ad_id`; no advertising records were copied into a second
system. Root counts, four-ad pages, active/inactive filters and literal-name
search use real SQL records. Empty/new/expired schedules are included in the
inactive filter and labelled as paused/draft rather than presented as sending.

The individual editor provides content, photo/video/animation/document media,
URL button add/edit/remove/order/rows, nine intervals, explicit group selection,
client assignment/revocation/editing deadline, client links, delivery validity,
statistics, change history, actual private preview, activation/pause and confirmed
soft deletion. Pending content edits are recoverable per ad. Content fields use
the existing preview-before-save flow and immutable revision store. Saving a
draft does not activate publishing.

An explicitly assigned client sees the internal name of **their own** ad, as
requested, and content/media/URL editing plus preview/save. Administrative
settings are checked server-side and are unavailable to a client. Successful
content saves require no additional administrator approval. An already enqueued
attempt retains its pinned immutable revision; the next slot takes the latest
saved revision. Saving content never creates a slot or changes `next_run_at`.

Existing extended non-URL buttons and delivered-button callbacks are preserved.
URL buttons can be arranged in rows using the existing `label - URL && label -
URL` text grammar. Single-button edits preserve other button kinds/rows. A photo
or video album has separate companion buttons, reflecting the Bot API album
limitation. Animation and document are single-media ads; mixed document/GIF
albums are rejected before save. Native Telegram entities take precedence over
raw HTML; supported Telegram HTML input is converted to validated UTF-16
entities, not displayed as tag text.

## Data and worker

* Existing campaign IDs, content revisions/drafts, customer grants/links,
  assignments, publication attempts/parts/rate gates and audits remain in place.
* Existing per-group heterogeneous intervals/dates are displayed as differing
  settings. Navigation/migration does not overwrite them. An explicit individual
  ad interval/date edit applies that field across that ad's included schedules.
* `AdAssignmentPolicy` remains the per-ad defaults/version record. All included
  schedules are locked in ID order under the policy lock during ad-wide changes.
* Existing PostgreSQL worker, slot uniqueness, claims/fences, expiry, fair ordering,
  RetryAfter/rate gates and uncertain-write handling remain the only sender.
* New recipients stay drafts until explicit activation. Clearing a selected
  audience stops and excludes that ad's schedules without deleting their history.
  Activation/resumption chooses a future slot; it never backfills missed sends.
* Rights reads happen outside SQL locks; current local authority, approvals and
  optimistic versions are checked again under the mutation lock. Existing
  conservative actor/bot group-administrator requirements remain unchanged.
* Pause/delete cannot recall a native Telegram request already in flight. Unknown
  writes remain uncertain and are not blindly retried. The UI does not promise
  exactly-once network delivery after an ambiguous Telegram response.

## Audience snapshot

**All groups** means the currently approved, enabled, eligible groups whose
actor/bot rights were verified, shown for explicit confirmation. It does not mean
future automatic enrolment. Adding another managed group never changes an
existing ad's audience. `FUTURE` and `FUTURE_ON` can no longer be selected. The
legacy reconciler hook intentionally does not expand audiences. Cached GLOBAL
ADS/schedule/customer admin callbacks return to the unified private editor;
cached creation starts the same new-ad flow. Old selection records are retained,
not silently applied by navigation.

## Client link lifecycle

1. Generate an invitation for one ad. A cryptographically random 43-character
   token produces a 46-character `ai_…` start payload. Store only SHA-256, issuer,
   ad, expected grant identity, expiry, consumption and revocation metadata.
2. Default expiry is 24 hours. Rotation invalidates the previous unused
   invitation. If a client is already assigned, only that numeric Telegram ID can
   redeem it. Otherwise the **first redeemer** is assigned: share only with the
   intended client. Assigning the numeric ID first is the safer workflow.
3. Consumption serializes with ACL/client changes, verifies the issuer is still
   authorized, checks the ad and expected grant, and binds at most once.
4. The client immediately opens that ad's content editor and can copy a permanent
   editor link. Its `ae_<ad>_<grant>_<version>` payload is a **selector, not a
   password**: every open checks the current numeric-ID grant, version, editing
   deadline, unarchived ad and enabled link capability.
5. Revoking links disables both invitation and permanent selector. Replacing or
   revoking the client invalidates old grants/drafts/selectors immediately. New
   link generation is required to re-enable direct selectors after unlinking.
   Existing hashed `ad_…` links continue to check their original numeric-ID grant.

The actual running bot username comes from authenticated `getMe`, never a
hardcoded example or guessed username. Raw invitation tokens are shown only in
the generation screen; they are not stored in Redis FSM, SQL audit or logs.
They cannot be copied again after leaving that screen; rotate if lost. Permanent
authorized editor selectors can be copied again from Client link.

## Migration 0020

Follows 0019 without altering migrations 0013–0019:

* Add `ad_client_invitations`, constraints and one-active-invitation partial index.
* Widen `ad_publication_parts.kind` to nine characters and allow ANIMATION and
  DOCUMENT in its check constraint.
* Convert only legacy FUTURE policies to EXISTING snapshots, disable future
  activation/reconciliation and bump their policy version. Audit previous mode
  and version. Existing schedule recipients, content, customers and history remain.
* Refuse downgrade when invitation history or new-media delivery parts exist.
  Downgrade never silently restores automatic future-group enrolment.

No production migration or deployment was performed. Existing production
allowlists and the separate moderation test installer were not expanded to deploy
this branch. This PR is stacked on moderation PR #44 so 0017–0019 are retained;
#42/#43/#44 remain separate and unmerged.

## Payments / STATUSAS

No payment ledger or paid STATUSAS prerequisite validator exists in the reviewed
version. Existing publication end times and client editing deadlines are
preserved as distinct controls. This change does **not** charge €20/€30, infer a
payment from a date, or invent a STATUSAS gate. A manual validity edit explicitly
says it does not register payment. Wiring a separate payment source and an
automatic 30-day paid entitlement requires the real payment/status source to be
identified first. The advertised prices are not newly enforced by this PR.

## Safe real Telegram acceptance

These steps are for an explicitly approved isolated staging deployment. No bot
was restarted on VPS during development.

1. Review this stacked branch and 0020, make a database backup and rehearse on a
   separate staging copy. Use separate PostgreSQL/Redis namespaces and a test bot
   token; do not start a second poller with a token already in use. Do not pass
   production credentials to the test harness.
2. Upgrade **only that staging database** to 0020 and run Alembic drift checks.
   Verify old content/customer/schedule/history counts and FUTURE snapshot audit.
   Keep active or uncertain work for reconciliation, not deletion to bypass checks.
3. Register/approve two test groups and verify the test bot/admin permissions.
   Open `/ads` in the test bot's private chat. Create A and B; configure A only.
   Check all filters/search/pages and that B's content/settings remain identical.
4. Preview text, photo, video, animation, document and URL rows in private chat.
   Confirm that no preview appears in a group. Check iPhone menu edits/back and all
   three languages. Save and activate only the intended ad/groups.
5. Assign a consenting test account by numeric ID, generate a 24-hour invitation,
   redeem it and copy its permanent editor link. Try another account/another ad,
   replay/rotate/unlink/revoke/replace. Verify all unauthorized access is denied.
6. Save client content before a real slot. Verify there is no immediate extra
   message and the next slot uses the new version. Already pinned in-flight work
   may correctly finish with its previous consistent version.
7. Pause/resume, change interval/validity and soft-delete with confirmation. Restart
   staging and check persistence. Add a third group: neither A nor B may target it
   until the administrator explicitly confirms a new audience.
8. Check per-ad successful/failed/uncertain statistics and audits against actual
   Telegram messages. Real flood control, Bot API/client behavior and network
   loss still require this live acceptance; simulations do not prove them.

Stop at staging acceptance. Production rollout and merging require separate
explicit approval.
