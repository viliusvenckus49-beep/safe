# Direct merchant administration

The catalog now uses direct, authorized administrator writes. It does not create
or process applications, change requests, approvals or rejections. Existing
owner-controlled administrator access remains the authority; no new role is
introduced. Every admin callback/input checks current access, and writes recheck
access after acquiring the existing shared identity metadata lock.

## Navigation

Administration → Merchant management:

- Merchant list
- Add merchant
- Find merchant
- Directory settings
- Back to Administration

Selected merchant: Edit merchant, Suspend/Resume, Remove listing, Back to Merchant
management. There is no preview, action history or list shortcut on this card.
Public My Listing is read-only and links to the existing public profile when
available. Public registration, search and information remain absent as before.

Edit merchant → Name / Description / City / Contact → enter a value → editor
preview → **✓ PATVIRTINTI** (Confirm) or Cancel. Confirm updates the same entry
and returns to its management card. Cancel returns there without a database
write. A field's **◀ ATGAL** returns to the same editor and cancels text input;
previously entered session values remain. City selection uses the same behavior.
The editing session uses the existing FSM storage and expires after one hour.
Field input expires after ten minutes. Explicit commands discard the form using
the existing flow cleanup. No partial merchant is created when Add is cancelled.

Create requires an existing, known numeric Telegram identity. Confirm publishes
the new listing immediately. The unique owner constraint and shared lock prevent
duplicate entries. Historical incomplete entries can be completed through their
existing management card rather than creating another listing.

An entry version prevents lost updates and repeated writes. FSM revisions bind
field and confirm controls to their own editor/merchant. Removed application
callbacks return a stale-control error and never run historical workflows.
Removal uses a merchant-specific, expiring confirmation token and soft-deletes
the entry; it does not remove the identity or historical data. Cancel invalidates
the confirmation. Suspend hides only that listing; editing a suspended listing
keeps it suspended. Resume still checks the existing SCAM policy.

## Data and formatting

Migration **0025_merchant_public_contact.py**, after 0024, adds a nullable
`merchant_entries.contact` (200 characters). No existing columns, rows, proposals,
settings, reputations or identities are deleted, rewritten or copied. Old contact
information continues to come from the linked account until explicitly edited.
A public contact may be an @username or HTTPS link; `-` restores the linked-account
fallback. Editing it never changes the owner_user_id, account username, REP,
TRUSTED or SCAM records. The existing model supports one city/region per merchant.

Description text is stored as plain text and rendered in full, safely escaped,
inside `<blockquote><i>…</i></blockquote>` in public/admin cards and the editor.
Paragraphs are preserved. Existing length limits remain 2–64 characters for names
and 10–600 for descriptions; descriptions are not shortened during display.
The public card retains shared REP statistics and the existing TRUSTED/SCAM
warning rules. Native Telegram quote colors depend on the Telegram client/theme.

Historical proposal tables and the old `registrations` column remain mapped for
schema/data compatibility only. Runtime submission/moderation services, owner
change/removal requests, queue/history screens, keyboards and obsolete locale
texts are removed. The legacy setting cannot be toggled or used to enable a
workflow. Existing internal audit continues with merchant_created,
merchant_updated, merchant_status and merchant_settings events.

## Changed files

- `app/models.py`: additive public contact field; historical-only schema notes.
- `app/merchant_services.py`: direct create/update, contact validation, existing
  permission/identity locks, version checks, status changes and audit.
- `app/merchant_repository.py`: remove unused proposal lookups; existing SQL
  filtering/search, reputation and review queries preserved.
- `app/bot/merchants.py`: direct editor/FSM, one-panel navigation, field back,
  confirmation/cancel, deletion confirmation and obsolete callback rejection.
- `app/bot/merchant_keyboards.py`: simplified admin/card/editor keyboards.
- `app/bot/merchant_views.py`: management/editor headings, current field values,
  escaped contact presentation and full quoted italic descriptions.
- `app/locales/merchants.py`, `app/locales/merchant_workflows.py`: LT/EN/RU text.
- `migrations/versions/0025_merchant_public_contact.py`: additive schema upgrade;
  downgrade refuses to discard non-null contact information.
- `deploy/production_release.py`, `deploy/production_probe.py`: reviewed migration
  pin and schema target 0025, preserving all release/backup/retention guards.
- `tools/auto_ads_live_test.py`: refresh reviewed embedded source integrity only;
  this retired optional sandbox tool is not used for deployment or live tests.
- `tests/test_merchant_direct_admin.py`: new aiogram CRUD/navigation/security tests.
- `tests/test_merchants_service.py`: replace retired proposal tests with direct
  writes, version conflict, identity/data preservation and concurrency coverage.
- `tests/test_merchants_telegram.py`: preserve catalog/review/search tests; retire
  obsolete application journeys and update expected navigation.
- `tests/test_merchant_profile_design.py`: editor replaces proposal preview.
- `tests/test_merchants_migration.py`: schema head and full 0024→0025 retention.
- `tests/test_auto_ads_migration.py`, `tests/test_production_release.py`: new head.
- `tests/test_auto_ads_live_operator.py`: new reviewed package/schema contract.
- `docs/merchants/DIRECT_ADMIN.md`: this operational/review guide.

## Safe release and acceptance

Use this development branch based on deployed 8aef5d5, not the older main branch.
Review the diff and take a verified database backup. Run the existing exact-SHA
CI and backup/restore/retention rehearsal before production migration. Apply
`alembic upgrade head` through the existing guarded release; the target is 0025.
Do not use an old image expecting schema 0024 after new contacts are saved, run
a destructive rollback, recreate the retired test bot or clear Redis/database
state. Other bot modules, secrets and VPS configuration are unchanged.

Before publishing, test with an isolated DB and mocked Telegram transport:
create/cancel, city/list/search, edit each field and Back, inspect unchanged public
data before Confirm, Confirm and inspect updated public data, Cancel and compare
old values, pause/resume, remove/cancel/confirm, stale/replayed controls, forged
admin calls and access revocation. Apply migrations to a populated 0024 copy and
compare all original table columns/rows; test populated-contact downgrade refusal.
Use an isolated PostgreSQL database for concurrent writes. Real Telegram visual
checks are separate from mocked aiogram transport tests; automated tests do not
send ads, sanctions or messages to actual users.
