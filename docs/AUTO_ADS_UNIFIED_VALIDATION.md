# AUTO ADS validation — 2026-10-21

## Evidence and limits

Application rebuild collection before the live-operator addition: **2,259 test
cases**. The operator adds **55** cases and the Custom Emoji correction adds **36**,
and the native message editor adds **38**, making the current collection **2,388**.
Test counts below overlap; do not add them.

* Complete SQLite/default run: **2,132 passed, 124 skipped** (before the final
  three additional tests). Skips explicitly require integration services.
* Complete run with real isolated PostgreSQL 17.6 and Redis 7.4.5: **2,258 passed,
  one test-harness failure**. The failing legacy migration snapshot did not yet
  classify the new empty `ad_client_invitations` table as a new table, so it
  compared a different table set to its 0010 baseline. No legacy row changed.
* After correcting only that migration-test table catalog, **all 13 related
  legacy/new migration tests passed**, including the formerly failing real
  PostgreSQL 0010→head→0010 retention/drift test. No application change was needed
  for that failure. Every case in the current collection has a passing result;
  the full run and focused migration rerun are reported separately for accuracy.
* Final selected-ad UI, publication history and new AUTO ADS release checks: **74 passed** with both services after the final list-query/locale refinement.
* New AUTO ADS contracts, native UI/FSM and migrations: **55 passed** with
  both real services, including first-redeemer races and link disable/re-enable.
* ADS PostgreSQL service/worker/customer/global/migration run: **244 passed**.
* Native UI/runtime/Redis regression run: **69 passed, 6 skipped** (PostgreSQL-only
  cases were covered in the service-enabled runs).
* Ruff lint: pass. Ruff format: **335 files** checked. Mypy: **105 app sources**.
  Compileall and `git diff --check`: pass. Production Compose configuration
  parses with example inputs and empty temporary secret files; nothing deployed.

PostgreSQL/Redis containers were task-owned, memory-limited, network-isolated and
used Unix sockets; production/test VPS services and credentials were untouched.
No Telegram token/session or production database was used. All Telegram requests
use doubles; native request envelopes, routing, SQL state transitions and Redis
persistence are tested, not real client delivery or live flood-control behavior.

The ordinary release image was not rebuilt locally because available disk space
was limited. Runtime container testing uses the existing dependency image with
this branch's source/migrations mounted read-only and a non-root task UID;
startup, worker progress and shutdown passed with networking disabled. This
is narrower than validating a newly built release artifact. The existing CI
workflow still builds the ordinary Dockerfile and runs packaged smoke/operations
checks; its remote results must be reviewed before release.

## Required scenario coverage

`tests/test_auto_ads_unified.py`, `test_auto_ads_ui.py` and
`test_auto_ads_migration.py` supply new contracts. Existing `test_ads_*` suites
continue checking worker fencing, native API failures and the preserved editor.

| # | Requested case | Passing coverage |
|---|---|---|
| 1 | Create ad | named creation; native name→editor→draft journey |
| 2 | Internal name | persisted SQL name; escaped private panel; never prepended to publication |
| 3 | Filters | real ACTIVE/inactive SQL predicates and native filter buttons |
| 4 | Search | literal wildcard-safe name search; Redis dialog input |
| 5 | Pagination | four visible records plus next-page sentinel; real second page |
| 6 | Individual menu | LT/EN/RU control inventory and per-ad callback IDs |
| 7 | Text | existing versioned stage→preview→save; UTF-16 entities and HTML conversion |
| 8 | Media | photo/video/albums preserved; animation/document validation and native sends |
| 9 | URL controls | add/edit/remove draft journey, actual URL preview, preserved rows/other kinds |
| 10 | Intervals | all nine intervals; no immediate slot; other ad unchanged |
| 11 | Selected groups | per-ad snapshot; explicit selection/confirmation; stale-state rechecks |
| 12 | All groups | explicit current snapshot; later groups never auto-enrolled |
| 13 | Client | numeric-ID assignment; one current grant; own editor only |
| 14 | Generate link | real bot username; URL-safe bounded random payload; SHA-256 only persisted |
| 15 | One-time token | replay denial and real PostgreSQL/SQLite parallel first-redeemer tests |
| 16 | Invalid token | malformed, expired, rotated, revoked, wrong-client, replaced-grant cases |
| 17 | Revoke client | old selectors/drafts denied; grant-bound mutation authorization |
| 18 | Direct editor | authorized permanent selector; wrong account/version/link capability denied |
| 19 | Client save | automatically creates immutable active content revision, no approval layer |
| 20 | No extra send | `next_run_at` unchanged; pinned attempt sends old snapshot, next slot sends new |
| 21 | Foreign ad | current numeric-ID grant checks on reads/writes and callback tampering |
| 22 | Expiry | worker marks expired; content/history retained; editing does not reactivate |
| 23 | Statistics | ad-scoped SQL attempts/counts and paginated retained delivery history |
| 24 | Audit | exact campaign scope, actor/time/field; raw tokens absent |
| 25 | Pause/resume | persisted state and future slots; expired/paused client edits stay inactive |
| 26 | Delete | actual confirmation, stale confirmation rejection, archive/revoke/stop atomically |
| 27 | Restart | new database engine preserves content/grant/audience/audit; real Redis client restart |
| 28 | Duplicate sends | existing slot uniqueness, claim/fence, parallel worker and uncertain-write tests |
| 29 | Migration | actual SQLite and PostgreSQL 0020 DDL; old rows/schedules/grants retained; drift/rollback guard |
| 30 | LT/EN/RU | native root/list/panel journeys and catalog/placeholder parity |

Extra tests retain old admin/client editor separation, public delivered-button
binding, actor/bot permissions, group removal during Telegram reads, concurrent
administrator settings, RetryAfter/network timeout/partial outcomes, audit
identity, required-worker supervision and clean shutdown.

## Remaining acceptance

Use [the staging guide](AUTO_ADS_UNIFIED.md#safe-real-telegram-acceptance) for a
separate approved test bot and consenting client account. Check actual iPhone
menu transitions, media/file IDs, public slot delivery, edited content at the
next slot and real rights/rate limits. No real Telegram acceptance or VPS
rollout is claimed. Payment/STATUSAS integration remains dependent on identifying
the actual entitlement source; manual validity is not a payment record.

## Opt-in existing-sandbox live operator (2026-10-21)

`tools/auto_ads_live_test.py` packages the pinned candidate for the existing
RedTrustBot sandbox, with read-only identity/work checks, private pg_dump backup,
restore-and-migrate rehearsal, original-row hash verification, narrow transport
scope and no automatic activation. See `AUTO_ADS_LIVE_TEST.md` for operator and
Telegram acceptance instructions. Preparing this artifact did not access the
user's VPS or a real Telegram token.

* Live-operator + selected-ad UI/service/migration suite with real task-owned
  PostgreSQL/Redis: **94 passed** (includes 39 operator checks; counts overlap).
* Separate full legacy sandbox source extraction/upgrade: exact candidate hashes,
  preserved private session/context, repeat no-op and tampered-source refusal pass.
* Separate real Docker PostgreSQL pg_dump → private restore → migration rehearsal
  → 0019-to-0020 staging upgrade passes with original-row verification. The
  non-root migration process can read candidate code even if the operator's
  original checkout used private directory/file modes; the SQL backup stays 0600
  under a 0700 backup directory.
* Ruff lint/format, mypy and whitespace checks pass after adding the operator.
  No VPS deployment or live Telegram observation occurred in these checks.

## Archived-ad preflight correction

The legacy content archive function changes `ad_campaigns.status` to ARCHIVED
without rewriting schedule history. The initial sandbox operator counted all
ACTIVE schedule rows, although `PublicationService.eligible()` explicitly rejects
archived campaigns and excluded schedules. This could block deployment after a
confirmed archive. The corrected read-only query counts ACTIVE schedules only
for non-archived campaigns that are not excluded; archived ACTIVE rows are counted
separately for diagnostics. Pending/claimed/sending/deferred/uncertain publications
and pending/processing/uncertain moderation jobs continue blocking the update,
including work that references an archived campaign. No database cleanup, state
rewrite, application change or payload revision is involved.

Fourteen real-SQL regression cases exercise actual legacy archive behavior, live
ads, paused/excluded schedules and pending/uncertain/deferred archived-ad work,
on both SQLite and isolated PostgreSQL. The original schedule/history is retained.

After the correction, all **113** selected operator/publication checks passed with
real task-owned PostgreSQL (Telegram calls still simulated), including all 53
operator checks. Ruff lint/format and whitespace checks passed. This is not a VPS
or real Telegram deployment result.

## Rich Custom Emoji correction

`docs/AUTO_ADS_CUSTOM_EMOJI.md` records the exact validation issue and official
Telegram/TDLib references. The application no longer counts custom emoji, explicit
formatting and automatic entities toward one combined 100-element limit. All
native data is preserved; independent count errors are localized. The bot's
Telegram permission to render custom emoji is still verified by a real preview,
not assumed from validation. The interval-nesting check now uses a bounded sorted
pass, with 500 deterministic differential cases against the previous crossing rule.

Initial content/admin/client/publication run: **179 passed, 2 skipped** (real
PostgreSQL-only cases). Broader selected SQLite AUTO ADS/operator regression:
**273 passed, 15 skipped** (integration services absent in that run). Test counts
overlap. Full legacy-source reconstruction through the previous c83b096 AUTO ADS
sandbox and the a9018d9 emoji candidate confirms exact hashes, preserved context
and private session, and repeat no-op behavior. The operator accepts the previous
AUTO ADS revision by its exact original manifest and does not widen its private
client allowlist. No additional migration is required. All Telegram delivery
requests in these checks use doubles.

Final full `tests/test_ads*.py tests/test_auto_ads*.py` run with real, task-owned
PostgreSQL 17.6 and Redis 7.4.5: **514 passed** in 184.77 seconds, no skips. This
includes all 55 operator cases and the new rich-emoji validation, admin/customer
FSM, actual migrations, publication, invitation/ACL and Redis regression cases.
Ruff lint/format, mypy (105 app sources) and whitespace checks pass. No user's VPS,
production database or real Telegram token was used; outgoing Telegram requests
remain mocked, so actual native emoji rendering awaits live acceptance.

## Immediate message editor

The user explicitly selected immediate save for administrator and client text,
media and buttons. `docs/AUTO_ADS_MESSAGE_EDITOR.md` describes the updated native
flow, controller cleanup, optional previews, album completion and failure bounds.
No additional migration or dependency is added. Legacy drafts keep their preview
requirement; received content is audited as native input, never marked previewed.

All **38** dedicated controller checks passed with actual task-owned PostgreSQL
and Redis, including parallel saves and a Redis dispatcher restart. Selected
legacy/new UI/service regressions initially passed **112** cases with **8**
integration skips; counts overlap. Shared Telegram doubles now return the correct
list of Messages for sendMediaGroup, making private preview cleanup testable.
Ruff lint/format, mypy (106 app sources) and whitespace checks pass.

Final full ADS/AUTO ADS + shared Telegram router + stage-3.5 runtime regression
with real, task-owned PostgreSQL 17.6 and Redis 7.4.5: **634 passed** in 205.39s,
no skips. Thirteen warnings concern the pre-existing Alembic path_separator
configuration deprecation; no migration/test failure occurred. All 38 native
controller checks are included. Bot API calls are mocked; phone rendering,
Telegram cleanup permissions and real native delivery still need live acceptance.
No production or user's VPS operations were performed by these checks.
