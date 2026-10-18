# AUTO ADS 3.1: campaign content foundation

This PR builds on unmerged #33 (which depends on #32 and #31). None is assumed to
be deployed. Migration 0013 follows 0012; old migrations remain unchanged.

## Available now

Authorized global SAFECheck administrators and the owner can open `/ads` in a
private chat or AUTO ADS in the existing Administration menu. Group-local
moderation rights grant no advertising access. Ordinary users have no ADS menu.

Create a campaign with a private internal name, then edit text, media and buttons
independently. The internal name never enters preview content. All campaigns are
**DRAFT**; there is no scheduler, activation, group publication or fake delivery
statistics in this stage. The nullable client ID is unassigned and is never used
to grant access. Client assignment and self-service belong to 3.4.

Text is stored literally with validated Telegram entities (including genuine
Custom Emoji IDs). The bot does not interpret typed HTML. Each native send uses
explicit entities and `parse_mode=None` so the global HTML default cannot turn
user text into markup. Management panels escape dynamic names with HTML escape.

Media collection accepts photos/videos, one file or up to ten in an album.
Captions on media input do not replace the separately edited ad text. Send all
parts, then Finish collection. Message IDs deduplicate inputs and order them even
if they arrive out of order. The collection is stored in SQL; Unsaved changes
can reopen it after losing FSM state/restarting. Overlong captions are rejected,
never silently truncated. A clear-field action removes media explicitly.

Each edit creates a private, actor-bound, one-hour working draft. Successfully
preview the complete draft before saving. Content saves create immutable
numbered revisions; they do not mutate the earlier revision. History offers
restore as another previewable draft, creating a new revision on save. A stale
base revision produces a conflict, never a silent overwrite. Repeated Save uses
the existing result. A matching request cannot be reused with changed input.

Delete archives a draft campaign and retains its revisions, working drafts and
audit trail. Archived campaigns cannot be edited or selected for preview.

## Native buttons and API limits

Grammar: `Label - https://t.me/Example`. `&&` places buttons in the same row;
newlines create new rows. Supported actions:

- HTTP/HTTPS URL: validated scheme, host, port, no credentials or whitespace.
- `popup: Text` / `alert: Text`: real `answerCallbackQuery` responses, at most
  200 UTF-16 units. Preview callbacks require current administrator rights and
  access to the original content/draft; they reveal no private draft to others.
- `share: Text`: native Telegram sharing URL, not an inline bot/Mini App.
- `copy: Text`: native `CopyTextButton`, at most 256 UTF-16 units.

A maximum of eight buttons per row, ten rows and fifty buttons total is enforced.
Arbitrary `callback_data` cannot be supplied. `rules` is rejected with a clear
message: Bot API has no native group-rules lookup, and this project has no rules
source yet. No dead or pretend rules button is saved.

A genuine Custom Emoji entity at the leading label supplies
`icon_custom_emoji_id`; ordinary emoji stay in label text. Exactly one custom
icon per button is supported by the native API. UTF-16 spans are validated,
including surrogate boundaries. IDs are stored even if Telegram refuses to
render them. Existing transport icon fallback remains in effect; real premium
availability depends on Telegram/bot entitlement, not a simulated HTML icon.
Text Custom Emoji remain real `custom_emoji` entities.

Albums cannot carry `reply_markup` in `sendMediaGroup`. Full album preview uses
an actual photo/video album followed by a clearly labelled buttons message.
Single photo/video previews carry a native caption and keyboard. A text-only ad
uses a native message. Incomplete empty drafts are explicitly labelled as such,
not presented as publishable content. Already posted ads are never edited.

## Authorization and storage

New tables: `ad_campaigns`, `ad_content_versions`, `ad_content_drafts`. Advertising
content has no groups, intervals, start/end dates or publishing settings inside
its schema. Server-side field whitelists reject unknown/mass-assigned settings.

Every read/callback/input/save rechecks the numeric actor through AdminService.
Writes share the existing identity-metadata SQL lock with owner ACL revocations;
this serializes grants/revocations and final writes on PostgreSQL and SQLite.
No permission grant is cached in Redis/FSM. SQL locks are released before each
Telegram preview request. Preview success is recorded only after all native
preview requests succeed; API rejection leaves the content unsaved.

Audit records contain actor, campaign, field, draft and revision references,
without copying advertising text or private button values into log metadata.
A downgrade refuses before DDL if any campaign exists, including archived ones.
Do not delete history to force rollback: retain schema and use a reviewed forward
rollback. No production config, DB, deployment allowlist or VPS changes are made.

## Next stages (not implemented)

3.2 will add publication settings separately from content: per-group schedules,
UTC due instants with Europe/Vilnius civil-time/DST validation, durable attempts,
leases, FloodWait deferral and explicit uncertain outcomes. A send should capture
one immutable content revision when it starts. Content edits must not reset due
times or change a send already in progress. Validate publishability at activation.
Do not claim Telegram exactly-once delivery after a write timeout.

3.3 adds real GLOBAL ADS assignments with independent per-group settings,
including existing/future groups, using global authorization.

3.4 adds client ACLs, a content-only field whitelist, expiry checks and the
revocable non-capability deep links specified in issue #34. A link must never
create access; stale callback/FSM and final-save checks must enforce revocation.

3.5 adds actual send statistics, important-error notifications, advanced hardening,
integration/staging smoke tests and a separately approved deployment plan.
