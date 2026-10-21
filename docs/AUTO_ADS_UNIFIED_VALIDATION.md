# AUTO ADS validation — 2026-10-21

## Evidence and limits

Application rebuild collection before the live-operator addition: **2,259 test
cases**. The operator adds **39** cases, making the current collection **2,298**.
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
