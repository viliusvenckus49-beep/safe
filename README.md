# 🛡 SAFECheck 2.13.2

SAFECheck is a Telegram reputation service with Lithuanian, English and Russian interfaces and moderated scam registry. User reports are private moderation requests; they do **not** label a person a confirmed scammer. An absent scam record is never a guarantee of reliability.

## Features

- Inline home menu, database lookup, profiles, TOP 10 and paginated confirmed registry.
- Reply-based or targeted `+rep` / `-rep` requests, all requiring numeric-ID administrator approval. Pending/rejected requests never affect reputation.
- Administrator REP additions, deductions and reset with reason, confirmation and an immutable adjustment/audit history.
- TOP 10 excludes negative totals, links to Telegram profiles, and supports audited administrator inclusion/exclusion independently of reputation.
- Guided private report wizard: target, reason, optional photo/document evidence, preview, edit, back, cancel and submit.
- Numeric-ID admin authorization, pending reports, evidence review, approve/reject, registry changes, statistics and audit history.
- PostgreSQL transactions, constraints, Alembic migrations, structured logs and Redis FSM storage in production.

Commands: `/start`, `/help`, `/language`, `/cancel`, `/ask @username`, `/rep @username`, `/profile`, `/top`, `/report`, `/scammers`, `/admin`, `/add_sc @username`, `/del_sc @username reason`. Numeric Telegram IDs work as targets. Authorized administrators can use `/add_sc @username` or `/add_sc 123456789` without a reason in private chat or groups. Supplied reasons and `/del_sc` removal reasons must contain 10–1500 characters. Unknown usernames are recorded with an explicit warning: automatic bans require a known numeric ID. Cancel/Close deletes the current panel and clears the wizard; plain messages do not reopen it. Navigation and explicit commands send a new tracked panel at the chat bottom and delete the preceding panel when Telegram permits. Replies supply numeric identity directly. Most commands are also reachable through buttons.

## Architecture

`app/config.py` validates environment settings; `app/db.py` configures async engines; `app/models.py` defines relational storage; `app/repositories.py` provides reusable queries; `app/services.py` applies transactional rules. `app/bot/` contains Telegram routing, FSM, middleware, callback schemas and keyboard builders. `app/presentation.py` owns localized rendering and HTML escaping. `app/i18n.py` provides per-update locale context, one translation function and fallback; `app/locales/` contains matching LT/EN/RU template catalogs. `app/main.py` checks readiness and starts polling. Original V1 source is retained under `legacy/` for reference and is not executed or included in the image.

Core tables: users, username_history, operation_locks, identity_lock, reputation_events, reputation_requests, reputation_adjustments, top_visibility, top_visibility_actions, reports, report_evidence, scam_records, moderation_actions and audit_events. Legacy reputation events remain unchanged; new votes are moderated requests, one per giver/receiver pair. Rejected requests also reserve the pair to prevent repeated pressure on moderators. Request keys prevent duplicate delivery; active scam status has a partial unique index. Removing status preserves the record and audit trail. Moderation, adjustments and registry changes use transactions and row locks.

Short metadata transactions serialize username observations through a database coordination row, preventing concurrent updates from clearing both owners. Telegram numeric ID is the durable identity. Usernames are observed metadata, can be reassigned and are retained in history. An unverified username-only record is never automatically merged into a subsequently observed numeric identity: Telegram provides no reliable API for resolving arbitrary usernames into user IDs. Prefer replies or numeric IDs for consequential moderation. Such username-only records require human identity review; bot absence remains inconclusive.

## Languages

First `/start` asks users, including existing users without a preference, to choose Lithuanian (`lt`), English (`en`) or Russian (`ru`). Selection is stored in `users.language`, keyed by numeric Telegram identity. Repeated `/start` opens the selected-language home screen. Use the main menu language button or `/language` to change it; a change closes unfinished drafts and confirms in the newly selected language.

Presentation, buttons, errors, callbacks, admin panels, command descriptions and recovery notifications share the same catalogs. Group responses use the acting user's stored preference; no separate group language setting is created. Background recovery notifications resolve each recipient's preference. Command identifiers and callback payloads stay unchanged, except for the new validated `language:lt`, `language:en`, `language:ru` callbacks.

Reputation, SCAM records, reports and history remain shared. Names, reasons, evidence and historical moderator notes are retained in their original form; interface language does not translate or duplicate business data. Before language selection, Lithuanian is the fallback. Missing translations fall back to Lithuanian; unknown keys or malformed templates produce a safe generic response and a structured log entry, without logging user content.

Alembic migration `0005` adds only a nullable checked language field. Existing rows remain unchanged and initially have no preference. Back up the database before upgrading; do not recreate it:

```sh
alembic upgrade head
python -m app.main --check
```

For a running Compose deployment, take the documented PostgreSQL backup, then:

```sh
docker compose stop bot
docker compose build bot migrate
docker compose run --rm migrate
docker compose up -d bot
```

Add future translations to the centralized catalogs using identical keys and named placeholders. Tests verify key/placeholder parity, escaping, persistent preferences, language switching, locale isolation, workflows and migration preservation. `legacy/` remains an inactive V1 source archive and has no part in the multilingual runtime.

## Reputation administration

Use `/admin` in a private chat. **Laukiantys REP** opens the pending queue; each request has a human-readable `RP-YYYY-NNNNNN` reference and approve/reject buttons. Repeated identical decisions do not apply reputation twice; opposite decisions on a reviewed request are rejected.

**REP ir TOP valdymas** provides additions/deductions (1–10,000), full current reputation reset, and TOP inclusion/exclusion. Every change asks for a target, reason and explicit confirmation. Target identity is pinned across the wizard and previous preview buttons expire when the preview changes. All operations require server-side numeric admin authorization and preserve audit history. Administrative adjustments are already approved actions; they are not user vote requests.

A reset appends compensating ledger entries so current positive/negative counters become zero. It never deletes votes or their history. Pending requests remain for review and future approvals can change the new score. Historical V1/2.0 votes are retained as pre-existing reputation; only new user votes require approval. An operator-only `admin_reset_all_reputation` service supports an authorized all-user reset: it locks the user set, resets both counters, rejects pending requests, audits the action and never repeats the reset on an idempotent retry.

TOP sorts eligible users by total reputation. Negative totals are always excluded, even for manually included users. Manual exclusion persists until an administrator includes the user again. Inclusion permits zero-score users but cannot force a position above higher scores or bypass the ten-person limit. Telegram profile links without usernames depend on Telegram privacy/access rules; lookup/profile cards remain available through the main menu.

## Group moderation and recovery

Ordinary group messages do not trigger menus. Callback navigation edits the existing bot panel; wizard text updates edit the stored panel when possible. Uneditable/deleted panels get a replacement. Old messages from previous versions are not automatically purged. TOP 10 uses the observed Telegram display name first, falling back to username where the name is unavailable.

Give the bot Telegram administrator permission to restrict users. Only the numeric SAFECheck owner can stage a group by promoting the bot or sending `/start`; explicit approval in private `/groups` verifies current Telegram rights before activation. Group `/start` opens the ordinary home menu. Slash command menus contain only `/start`, `/ask`, `/rep`, `/report`; other commands remain usable manually.

Confirmed ACTIVE scam records with known Telegram IDs schedule bans in registered enabled groups, including preemptive bans where Telegram allows them. New member events and messages recheck known IDs. Reports alone never trigger bans. Durable ban jobs retry transient failures with backoff, respect Telegram RetryAfter, revalidate current SCAM/group status before requests, and record outcomes. Username-only records cannot safely cause numeric-ID bans. Removing SCAM status does not automatically unban someone. Bots cannot ban group owners or bypass Telegram permission rules. Permanent/preemptive protection requires a supergroup; basic-group behavior is more limited and the panel warns about this.

The group administration panel exports a readable UTF-8 TXT member list containing names, usernames and Telegram IDs the bot has observed. **It is not an enumeration of every group member.** Telegram privacy mode/update delivery can limit visibility. No Telethon user-account scraper or mass-inviting service is configured.

Users can open `/recovery` privately (or the main-menu recovery button), select a group they have been observed in and explicitly subscribe. They can revoke consent at any time. Selecting a group alone is not consent. The administrator enters a validated `https://t.me/...` invitation URL and confirms a recipient-count preview. Only consenting subscribers are queued; revoked consent is checked again before sending. Group disappearance does not automatically initiate a broadcast. At-least-once delivery may duplicate a notification if a process stops after Telegram accepts it but before the success is recorded; a message already in flight cannot be recalled by later unsubscribe. These limitations do not affect REP/report idempotency.

New tables: managed_groups, observed_members, private_contacts, recovery_subscriptions, recovery_campaigns, recovery_deliveries and ban_actions, introduced by migration 0004.

## Requirements and BotFather

Python 3.12+, PostgreSQL for production, Redis for production, or Docker with Compose. SQLite is for development/tests only.

1. In Telegram, open the official `@BotFather`, run `/newbot` and retain its token privately.
2. Use a dedicated bot for each environment. Set the bot description and language to Lithuanian as desired.
3. For group reply-based REP, add the bot to the group and configure BotFather privacy mode appropriately. With privacy enabled Telegram may not deliver plain `+rep` messages. Group administrator rights are not required for reputation features.
4. Obtain your administrators' numeric Telegram IDs from messages/updates delivered to your bot. A username is never an administrator credential.

## Local installation

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
cp .env.example .env
```

Supply your BotFather token and numeric administrator IDs through `.env` or your secret manager. Do not commit `.env`. Defaults use local SQLite and in-memory FSM storage; unfinished wizards are lost on restart in development.

```sh
alembic upgrade head
python -m app.main --check
python -m app.main
```

`--check` validates configuration, migration version, database connectivity and Redis when configured; it does not contact Telegram. Normal startup registers public bot commands and verifies that no webhook is active before polling. Configuration failures show field names without secret values.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BOT_TOKEN` | Required BotFather token; secret |
| `DATABASE_URL` | `sqlite+aiosqlite:///./safecheck.db` in development; `postgresql+asyncpg://…` in production |
| `ADMIN_IDS` | Comma-separated positive numeric Telegram IDs |
| `ENVIRONMENT` | `development` or `production`; production requires PostgreSQL, Redis and administrators |
| `TELEGRAM_PROXY_URL` | Optional explicit outbound proxy; otherwise inherited `HTTPS_PROXY` / `HTTP_PROXY` is used with system CA trust |
| `REDIS_URL` | Redis FSM storage and isolation connection; treat as secret if password included |
| `LOG_LEVEL` | Valid logging level; default `INFO` |
| `REP_COOLDOWN_SECONDS` | Global interval between votes, in addition to per-pair/reciprocal rules |
| `REPORT_COOLDOWN_SECONDS` | Interval between reports; default 300 seconds |
| `FSM_TTL_SECONDS` | Production draft expiry; default 1800 seconds |
| `POSTGRES_PASSWORD` | Compose PostgreSQL password only; use a generated password |

URL-encode passwords when embedding them in database URLs. Use your production secret manager rather than baking values into images. Settings are validated at startup; migrations load `DATABASE_URL` independently.

## PostgreSQL and Docker

Copy `.env.example` and set:

```dotenv
ENVIRONMENT=production
DATABASE_URL=postgresql+asyncpg://safecheck:YOUR_URL_ENCODED_PASSWORD@postgres:5432/safecheck
REDIS_URL=redis://redis:6379/0
POSTGRES_PASSWORD=YOUR_GENERATED_PASSWORD
```

Also supply `BOT_TOKEN` and `ADMIN_IDS`. Generate the password locally, for example with `openssl rand -hex 32`; replace both password placeholders with that generated value. The placeholders above are documentation, not usable credentials.

```sh
docker compose build
docker compose up -d
# Inspect service state and structured bot logs:
docker compose ps
docker compose logs --tail=100 bot
# Run a readiness check without contacting Telegram:
docker compose run --rm bot python -m app.main --check
```

Compose starts PostgreSQL/Redis, runs migrations as a separate one-shot service and starts the bot after migration success. No database/Redis ports are published. The bot runs as an unprivileged user with a read-only root filesystem. Production Docker builds use `requirements.lock`; development tools in the lockfile add image size but make the verification toolchain reproducible.

Behind a TLS-intercepting build proxy, supply the CA as a BuildKit secret without copying it into the image:

```sh
docker build --secret id=proxy_ca,src=/path/to/combined-ca-bundle.pem -t safecheck:2.13.2 .
```

## Migrations

```sh
alembic current
alembic upgrade head
# After intentionally changing models, review the generated SQL before deployment:
alembic revision --autogenerate -m "describe_schema_change"
alembic upgrade head
```

Back up before applying a migration. Migration CLI diagnostics are operator-only; never send their output to Telegram. Do not run `create_all` against production or point the new initial migration at an existing V1 schema.

## Upgrading V1 data

The uploaded archive contains source only. If you have an existing V1 SQLite database, stop V1, preserve a read-only backup and create/migrate a separate empty destination database. Use the import command below after verifying the backup path:

```sh
alembic upgrade head
python -m app.import_v1 --source /path/to/v1-backup.db
```

The importer refuses a non-empty destination and imports transactionally. Historical V1 reputation values can be preserved as initial events; past overwritten votes cannot be recovered. Username-only identities remain unverified. Keep the original database backup until you have reviewed the imported registry and counts.

## Tests and quality checks

```sh
pip install -e '.[dev]'
pytest -q
ruff check .
ruff format --check .
mypy
```

Tests use isolated databases and mocked Telegram transport; no real token is required. PostgreSQL/Redis integration tests use isolated schemas/key prefixes and run when `TEST_POSTGRES_URL` and `TEST_REDIS_URL` are supplied. CI configures disposable services automatically. For your dedicated local test services:

```sh
export TEST_POSTGRES_URL='postgresql+asyncpg://USER:PASSWORD@localhost:5432/safecheck_test'
export TEST_REDIS_URL='redis://localhost:6379/1'
pytest -q
```

Replace the placeholders with test credentials. Never point test tooling at production.

## Production operations

Run one polling bot process per token. This version implements long polling, not webhook hosting. Redis retains report drafts across restarts and isolates same-user FSM operations; drafts expire. Protect Redis/PostgreSQL on a private network and use TLS/authentication for remote connections. Do not publish either service to the public Internet. Redis persistence contains private report draft text/evidence IDs and needs the same access policy as PostgreSQL.

The service protects against self-votes, repeated giver/receiver votes, rapid votes, recent reciprocal votes, repeated pending reports, malformed inputs and duplicate submissions. These controls reduce common abuse; they do not establish a person's real-world identity or prevent coordinated accounts from colluding. Only administrators see pending reports and evidence. Public registry entries contain confirmed reasons, not reporter identities or evidence file IDs.

Monitor structured `startup_failed` and `update_failed` events, database capacity, backups and polling availability. Logs include exception type rather than exception text/tracebacks to avoid token, database URL and report-text leakage. Operator logs are still restricted data. Keep all Telegram evidence files private; opening a document should follow your moderation team's normal untrusted-file policy.

Back up PostgreSQL regularly with `pg_dump` to encrypted storage; test restores into a separate database. Back up Redis only if restoring unsubmitted drafts is required. Define retention and access policies for reports, evidence and audit events before launch. Audit/registry history is preserved by application operations; database owners can still modify storage directly, so restrict privileged access.

`ADMIN_IDS` provides bootstrap access; runtime grants and revocations are stored in the database and take effect without restarting. An explicit database revocation overrides configured access. The owner remains immutable. Do not grant roles via usernames. Before deployment, run the checks above, review the registry, verify admin IDs, test one real private report/moderation journey with the dedicated test bot and confirm backups. A live Telegram acceptance test requires your credentials and is separate from mocked automated tests.

TOP 10 uses an escaped HTML `<pre>` table with ten numbered rows, bounded display names and right-aligned scores. Profile buttons retain full names. Report submission confirmation is concise; references remain available internally and to moderators.

## Closed group ownership (2.4)

Only `GROUP_OWNER_ID` can stage, approve, list/manage groups, export members or approve recovery delivery. This numeric ID must be included in `ADMIN_IDS`. With exactly one administrator it is the default owner; with several administrators and no explicit owner, group management fails closed. Other administrators retain their existing reputation/report permissions.

Adding/promoting the bot or owner `/start` stages a new group without activating it. Open `/groups` privately as the owner, select the group and press **Approve group**. Approval freshly checks that the owner is a Telegram owner/administrator and that the bot can restrict members. Pending groups do not collect members, ban users, accept subscriptions or send recovery notifications. Existing registered groups are preserved as approved by migration `0006`.

Recovery still requires recipient opt-in and owner preview/confirmation; disabled approved groups remain recoverable after the original group disappears. There are no public applications and no automatic invitations. Membership backups contain only observed users. Before deployment back up the database, run `alembic upgrade head`, then `python -m app.main --check`.


## Administrator management and handbook (2.5)

The owner opens `/admins` privately or uses **Administrators** in `/admin`. Select **Add administrator**, enter a numeric Telegram ID and confirm the preview. Alternatively use `/admins 123456789` or reply to a person's message with `/admins` in private chat. Explicit command arguments take precedence over reply targets. Usernames cannot grant permissions; unknown numeric IDs are allowed after owner confirmation. Remove an administrator using the list button and confirmation. Changes are immediate, persisted, idempotent and audited; history is retained. No bot restart is needed.

Set `GROUP_OWNER_ID` to the immutable owner's positive numeric Telegram ID, included in `ADMIN_IDS`. A single configured administrator is the default owner; ambiguous owner configuration fails closed. Database administrators can moderate REP, reports and SCAM records, manage TOP, view statistics and audit history. They cannot grant/revoke administrators or manage groups/recovery. Explicit DB revocations override bootstrap `ADMIN_IDS`; the owner cannot be revoked through the bot.

`/admin_help` and **Administrator guide** show six short sections in the administrator's selected LT/EN/RU language: getting started, ratings, reports/evidence, registry, TOP and permissions. They require active administrator access and private chat. Granting rights does not send an unsolicited Telegram message; direct the new administrator to `/admin` and `/admin_help`.

Migration `0007` adds `administrators` and `administrator_changes` only. Back up, run `alembic upgrade head`, then `python -m app.main --check` and start the bot. Existing users, reports, ratings, SCAM records, groups and history are preserved.


Administrator user lists show 100 users per page, as `👥 1. @username [Telegram ID]` or `[no ID]` for username-only identities. Rows use monospace formatting with non-clickable usernames; numbering continues across pages. Names are shortened only when needed to keep all 100 entries within Telegram’s message limit; stored names remain intact. Other moderation/audit lists retain their existing page sizes.


## Interface and SCAM filtering (2.6)

Active SCAM users are excluded from the administrator user list and member TXT exports, with pagination totals computed from the same filter. Existing identities, membership history, reputation and audit records remain stored. Newly observed active SCAM members do not enter the backup; known numeric-ID bans remain enforced. Username-only active records are matched against normalized current usernames when filtering, without claiming a verified numeric identity. Removing active SCAM status makes retained historical entries eligible again.

Administrators can use `/add_sc ID` or `/add_sc @username` without a reason, privately or in groups; the private target wizard also skips the reason step. A supplied reason is still validated. User reports require their reason as before. Removal and reputation-adjustment reasons retain existing rules. SCAM cards without a reason show status/date without an empty reason heading.

SCAM registry menu, command and callbacks are private-only. Home no longer has an administration button; group-management entry buttons are removed. Authorized operators use `/admin` and `/groups` manually; slash discovery still shows only start/ask/rep/report. Information explains `/ask` and private `/report` in LT/EN/RU.


## TRUSTED designations (2.7)

Administrators use `/add_trusted ID` or `/add_trusted @username` (also replying to a user) to grant a manual designation, and `/del_trusted ID` to remove the manual designation. Both commands revalidate numeric-ID administrator access, retain audit/history and are idempotent for Telegram update retries. Unknown username identities remain explicitly unverified; they are not transferred automatically to later observed numeric users.

Current TOP 10 members automatically show TRUSTED in `/ask`, `/rep` and profiles. This is calculated from the existing leaderboard, not permanently written for every historical TOP member. Leaving TOP removes automatic status; an active manual designation can remain. Removing a manual designation does not suppress automatic TOP status, and the response explains this. Active SCAM overrides both sources and blocks a manual grant. TRUSTED is a SAFECheck designation, not an independent identity verification or guarantee.

Group recovery entry is visible only in the private bot. Migration0008 adds trusted_designations and trusted_actions without changing existing tables. Back up, run `alembic upgrade head`, then startup check and normal polling. Slash menu stays four commands.

Navigation in2.8.2: standalone notices have no inline controls. Lookup/profile cards now offer check-another, REP and cancel controls in groups and private chats. Cancel is reserved for unfinished input/confirmation flows. Administrator sections return to the admin panel; REP decisions return to the originating queue page, report decisions to report moderation, and registry searches to their originating page. Existing bottom-of-chat panel replacement and four-command discovery remain unchanged.

SCAM presentation in2.8.2: manual SCAM records display an administrator confirmation instead of internal notes; notes remain stored. Report-linked reasons remain visible. Group addition receipts and registry cards omit unknown-ID warnings; private administrator receipts and /ask identity caveats remain.

Home banner in2.8.2: app/assets/home.jpg is bundled as Python package data and sent as a single photo with localized caption and existing inline menu. /start, language selection confirmation and home navigation reuse the same panel lifecycle. Previous panels are retired only after a successful send; closing removes the photo menu.

Result cards in2.8.2 use a shared brand heading, REP totals, explicit SCAM/TRUSTED status and identity context. Public SCAM references are formatted SC-00001 from the actual stored record ID. Manual TRUSTED shows the stored designation update date; TOP-derived TRUSTED identifies its automatic source and does not invent a confirmation date. A known numeric ID means an identity link, not a document verification. No-SCAM results retain the trustworthiness disclaimer; TRUSTED results omit it. Public long names/reasons are bounded for Telegram while original records remain unchanged. Administrator, moderation and list screens share restrained headings/dividers; input prompts, command errors, numeric TOP rows and plain-text member exports retain their existing format.

Identity labels in2.8.2: user-check/profile cards, SCAM/user lists and moderation identity fields show the numeric Telegram ID or a localized unknown-ID label. Internal database primary keys are never substituted for Telegram IDs. TOP10 text and buttons intentionally retain their existing format without IDs.

User directory in2.8.2 automatically hides a username-only row when a currently observed numeric identity owns the same username. Pagination counts use the same filter. Original records and historical judgments remain stored and are never transferred solely on username matching. If the observed account changes or removes its username, the unresolved historical row may appear again. Existing panels refresh when reopened.

Profile roles in2.9.0: the configured SAFECheck owner is the founder; other currently authorized administrators are moderators. Numeric identity and fresh database-backed access determine the role. Roles grant automatic TRUSTED while active; manual and TOP designations remain independent and active SCAM overrides TRUSTED. Username-only records never receive a role.

TOP policy in2.9.3: manual TRUSTED, active founder/moderator roles or explicit administrator inclusion qualify for ranking. Explicit administrator exclusion overrides automatic eligibility. REP approval alone never qualifies a user. Nonnegative reputation and no active SCAM remain required; rank up to10. Historical records are preserved.

TOP identity deduplication in2.9.4: a currently observed numeric identity supersedes an old username-only row sharing its current normalized username. Filter before ranking/limit, preserve unresolved history and never transfer scores or judgments solely by username. Hiding the numeric identity cannot expose its old placeholder as a bypass.

TOP name fallback in2.9.5: if a symbol-only display name cannot be represented in the aligned text table, use its available username before the generic localized user label. Existing button display names and ranking remain unchanged.

TRUSTED management in2.10.0: private /admin → Trusted users provides8-row pages, username/ID search, source/status cards and nonce-bound removal confirmation. Removal atomically clears manual TRUSTED and excludes TOP while preserving REP, history and role permissions. Role-only cards are read-only; role-based TRUSTED remains until owner revokes access through /admins. Administrative history includes superseded or currently inactive designations to allow cleanup without falsely merging identities.

REP comments in2.12.0: every new +REP/-REP request requires5 non-whitespace visible characters, up to1500. Commands accept `+rep @username comment` / `-rep ID comment`; reply-based commands accept a comment after the command. Omitting it or using profile/result buttons starts a comment flow. In groups, reply to the bot prompt; unrelated messages and other users are ignored. Cancel with /cancel. Moderators see escaped comments. Migration0009 preserves legacy requests with NULL comments and original constraints; new submissions enforce comments in the service. Back up before upgrading; downgrading0009 removes the comment field, so avoid downgrade after new comments are stored.

### SCAM registro tapatybės papildymas (2.12)

Privačiai: `/admin` → SCAM registras → pasirink įrašą → „Pridėti ID“ arba „Pridėti username“. Mygtukai rodomi tik trūkstamiems duomenims. Įvesk reikšmę ir patvirtink, kad tai tas pats žmogus. Esami užpildyti duomenys ir konfliktuojantys įrašai neperrašomi.

Po `/add_sc @username`, jei ID nežinomas, galima iš karto siųsti skaitinį ID tame pačiame privačiame pokalbyje; jis nebus įrašytas be patvirtinimo. `/add_sc 8727262126` ir `/add_sc` → ID vedlys taip pat palaikomi.

Papildymas yra audituojamas ir atliekamas transakcijoje. REP, pranešimai ir jų istorija tarp paskyrų nesujungiami. Pridėjus ID, aktyvus įrašas suplanuoja blokavimą tik patvirtintose prižiūrimose grupėse, kai botas turi teises. Telegram negali patvirtinti bet kurio ID ir username ryšio: administratorius turi patikrinti tapatybę prieš patvirtindamas. Schemos migracijos šiam leidimui nereikia.


### 2.13 · Galutinis auditas ir VPS priežiūra

Baigiamoji ataskaita ir tikslūs patikrų rezultatai: [RELEASE_2_13.md](RELEASE_2_13.md) ir [docs/FINAL_REVIEW.md](docs/FINAL_REVIEW.md).

Saugumo auditas sustiprino jautrių administratoriaus veiksmų teisių tikrinimą transakcijoje; atšaukta rolė negali užbaigti laukiančio veiksmo. Callback puslapiavimas priima tik ASCII skaičius ir netaisyklinga įvestis nebeištrina pradėto pranešimo. Rankinis SCAM ID papildymas atmeta username konfliktą su kita jau žinoma paskyra. Jei žinomas ID sutampa tik su neišspręstu istoriniu username įrašu, patikra parodo atskirą perspėjimą ir automatiškai nesuteikia tam ID SCAM statuso.

Nuolatinio serverio paruošimas, slaptų reikšmių failai, sistemos paleidimas, kasdienės kopijos su atkūrimo patikra, monitorius, offsite rclone adapteris, atnaujinimas ir atkūrimas aprašyti [docs/OPERATIONS.md](docs/OPERATIONS.md). Naudok `docker-compose.production.yml` nuolatiniam VPS; ankstesnis `docker-compose.yml` skirtas paprastesniam vietiniam paleidimui.

```sh
python3 deploy/ops.py --dry-run initialize
python3 deploy/ops.py --dry-run deploy
python3 deploy/ops.py --dry-run backup
```

Sausas paleidimas nekeičia failų, neprisijungia prie Docker ir nesiunčia Telegram pranešimų. Tikram VPS reikalinga saugi išorinė konfigūracija. Offsite saugykla ir perspėjimų gavėjas neparenkami automatiškai.

```sh
# Reikalauja vietinio Docker, Compose ir šių trijų paruoštų image:
# safecheck:2.13.2, postgres:17.6-alpine, redis:7.4.5-alpine
python3 tests/run_operations_acceptance.py
```

Šis priėmimo testas sukuria unikalų Docker projektą, naudoja tik testinius duomenis ir netikrą Telegram transportą, patikrina tikrą kopijos atkūrimą ir pašalina tik savo projekto konteinerius bei tomus. Netikros testinės paslaptys turi lengvesnes failų teises, kad testą būtų galima paleisti be root; gamybinės teisės nurodytos operacijų vadove. Jis neliečia veikiančio boto ar jo DB.

`python -m app.health --check` tikrina paskutinius sėkmingus Telegram polling ir grupių worker ciklus; `python -m app.operations_status` pateikia tik agreguotus užduočių skaičius. Sveikatos patikra nereikalauja tokeno. Docker secrets paleidime statuso komandą vykdyk per `deploy/secret_entrypoint.py`, kaip nurodyta operacijų vadove.

SCAM / TRUSTED veiksmų patvirtinimai ir administratoriaus teisių klaidos išlieka pokalbyje. Meniu ir vedlių langai keičiami įprastai; atšaukimas uždaro tik aktyvų langą.
