# AUTO ADS 3.1 / PR #35 audit

Reviewed source: PR #35 head `55e40288e7ed12fa196e698edaa6f2b8f02e76ef`,
then the fixes in this document. This is a fresh code-level review and additional
regression testing, not a claim of independent third-party certification.
PRs #31, #32 and #33 remain open; #35 is stacked on #33. Review dependency order
and retarget after merging the bases. No VPS, production DB or deployment changes.

## Findings fixed

| Severity | Cause and effect | Fix and regression evidence |
| --- | --- | --- |
| Medium | History, restore, unsaved-draft and delete navigation could leave an earlier text editor active in FSM. A subsequent unrelated message could become an unintended draft for the previously selected campaign. | Authorized ADS management navigation closes the old input state; explicit edit or recovered media collection opens it again. Dispatcher tests cover five destinations and verify unrelated text is not stored. |
| Medium | Campaign/revision primary keys use PostgreSQL INTEGER, but callback checks accepted BIGINT-sized values. Revision preview lookup did not check the bound at all. Forged large IDs could escape domain error handling as driver overflow. | Validate positive INTEGER keys before campaign, restore and preview lookups. Tests exercise PostgreSQL and a forged preview callback beyond BIGINT. Telegram user IDs remain BIGINT and are unchanged. |
| Low | URL user-info detection tested truthiness. Empty user-info such as `https://@example.com` bypassed the stated no-credentials policy. | Reject any non-None username/password. Both empty-user-info regressions failed against the reviewed source and pass after the fix. No URLs are fetched by the parser. |
| Low | Button label length included the genuine custom-icon placeholder before extracting its separate API field. A valid 64-unit label plus icon was rejected. | Apply the length limit after icon extraction. Ordinary emoji remain part of the label limit. The regression failed against the reviewed source. |

No critical/high authorization bypass or existing SCAM/REP data mutation was
identified in the reviewed paths. This does not establish absence of all bugs.

## Functional and security review

- Creation validates private internal names. Names appear in escaped management
  panels only; native previews read content snapshots, not campaign metadata.
- Text stays literal with native entities and explicit `parse_mode=None`.
  UTF-16 spans, surrogate boundaries, crossing entities, links and genuine
  Custom Emoji IDs are validated. Telegram remains the final authority on
  additional entity nesting and bot-specific emoji/file availability; rejected
  full previews do not unlock an unpreviewed draft for saving.
- Photo/video IDs are validated and passed as native file IDs. Albums contain at
  most ten items, use the first caption and preserve the separately edited text.
  Album inputs are SQL-backed, ordered and deduplicated by message ID. An album
  keyboard is necessarily sent in a separate companion message.
- URL, popup, alert, share and copy definitions map to actual native API fields.
  `&&`/newlines determine rows; custom icons come only from Telegram entities.
  `rules` is explicitly rejected because no rules source exists. Arbitrary
  callback code cannot be injected through the parser.
- Every management callback, input and service read/write checks current global
  AdminService authority. Private chat/actor binding is checked before native
  previews. Group-local rights, TRUSTED and a nullable client ID grant no ADS
  rights. Drafts belong to one administrator; committed campaigns are shared
  among authorized global administrators by design.
- All writes use the existing identity-metadata SQL lock and recheck rights after
  obtaining it. The same lock serializes administrator revocation. No lock spans
  a Telegram preview. New PostgreSQL tests exercise six simultaneous identical
  saves and revocation committed while a previously authorized save waits.
- Request keys bind actor/campaign/base/field/input fingerprint. Final save checks
  the current revision and adds a new immutable snapshot in the same transaction.
  Duplicate saves return the saved revision. Restore creates another draft and
  revision; archive retains content, drafts and audit references.
- LT/EN/RU have matching keys and dispatcher preview/save journeys. Existing
  callback IDs and business logic outside ADS are unchanged.

## Migration and retention

0013 follows 0012 and only adds campaigns, content versions and editor drafts.
Existing migrations are unchanged. Foreign keys and unique revision/request
constraints supplement service checks. Upgrade tests compare legacy table rows
before/after on SQLite and PostgreSQL. SQLite Alembic autogenerate check verifies
model/schema agreement. Downgrade refuses if campaign data exists; do not delete
data to force a rollback. Application code provides revision immutability;
privileged direct SQL access is not prevented by an immutable-history trigger.

## Verification and limitations

Local pure regressions, Ruff check/format and mypy are executable here. Local
aiosqlite execution stalled until the bounded test timeout, and access to the
local Docker socket is denied by the workspace sandbox. Therefore the full
pytest suite, PostgreSQL/Redis contracts, Alembic, Compose and image build must
be assessed from the GitHub Actions run for the **fix commit**, not an older
green run. Exact results and that run are recorded in PR #35's audit update.

All Telegram send tests use API doubles. No live bot credentials or real client
were used. Custom Emoji rendering/entitlement and transport fallback, native
copy/share behavior (including the text-only share URL), album layout and
callback popups still require a private staging-bot smoke test. Existing
transport fallback retries a rejected icon keyboard once without icons while
retaining IDs in stored content; a successful fallback is not proof that the
premium icon rendered. Media restart tests recover SQL state in new sessions;
they are not a physical VPS power-loss test.

## Readiness for 3.2

The separated, versioned content foundation is suitable for the next stage.
There is **no working scheduler or publisher** in this PR. In particular:

- Add separate per-campaign/per-group publication settings and durable attempt
  records. Content saves must not rewrite intervals or next-due instants.
- Add supported intervals (30 minutes; 1/2/3/4/6/8/12/24 hours), group overrides,
  start/end, pause/resume and quiet hours. Store UTC instants; explicitly resolve
  ambiguous/nonexistent Europe/Vilnius civil times and test both DST boundaries.
- Extend the DRAFT/ARCHIVED status constraint with a new additive migration;
  validate publishability before activation. Empty drafts are currently legal.
- Pin one immutable content-version ID per attempt. Add leases, recovery after
  crashes, FloodWait deferral and uncertain-send outcomes; do not promise
  exactly-once delivery. Album-plus-keyboard attempts can partially succeed.
- Current preview popup callbacks deliberately require a private administrator.
  Group publication needs a separate read-only public callback path bound to a
  published revision, without exposing private working drafts or management.
- GLOBAL ADS assignments and future-group policies belong in separate tables.
  Client ACLs, expiry, token hashes/rotation/revocation and content-only editing
  remain 3.4 work. The current nullable client ID creates no capability.
- Keep the global identity lock short. Reassess contention before high-volume
  publishing; publication claims must not hold it across API requests.

Merge recommendation applies to the **3.1 draft-content scope**, after exact-head
CI succeeds and the stacked bases are handled. It is not approval to activate
production advertising or a claim that live Premium Emoji behavior is verified.
