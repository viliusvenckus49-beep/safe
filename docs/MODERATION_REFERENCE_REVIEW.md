# Moderation reference review and implementation

Reviewed 2026-10-20. SAFECheck base: `29a0e827243c883927bb8188191460173bbb6d0b`
(`safecheck-warn-controls`, PR #43). PR #42 remains open against main; #43 remains
open against its username branch. This change is stacked on #43, with no merges
or production deployment. Findings describe checked source snapshots, not a live
verification of third-party Telegram bots.

## References and licenses

- LibreGroupHelp: [Sp3rick/GroupHelp](https://github.com/Sp3rick/GroupHelp),
  snapshot `bf22efb13468fa27ce7cace445f448277bf6162b`. `LICENSE` is GPL-3.0;
  `README.md` explicitly says "Project discontinued". Node.js, a Git-based
  `node-telegram-bot-api` dependency, JSON storage. No upstream code was copied,
  run or installed. Compatibility with this repository's licensing was not assumed.
- [vitabled/wladmin_bot](https://github.com/vitabled/wladmin_bot), snapshot
  `50b4ba422e37a45f98a62d81af99c5d756ed5eaa`. README's License section states MIT,
  but the inspected tree has no standalone license text. No upstream code was
  copied, installed or executed. Python/aiogram 3, SQLAlchemy/PostgreSQL and Redis
  are conceptually compatible; dependency versions differ, so none were imported.
- [Group Help command list](https://telegra.ph/Group-Help-command-list-08-27),
  dated 2020-08-27: documentation only. Confirms `/unwarn`, `/mute`, `/unmute`,
  `/ban`, `/unban`, `/kick` and private settings commands. It does not establish
  implementation, transaction safety, API retry behavior or Ephemeral support.

## Concrete comparison

| Area | Evidence in references | SAFECheck decision |
| --- | --- | --- |
| WARN controls | Libre `api/utils/punishment.js`: `genUnpunishButtons`, decrement/reset; `plugins/punish.js`: callback handlers | Keep public receipts; add recipient-only WARN CONTROL with individual selection and existing all-reset confirmation. Do not adopt unrestricted counter mutation. |
| WARN settings/menu | Libre `plugins/warns.js`: warned-user list, limit and punishment menu | Reuse navigation ideas only. Do not add group-wide reset or alter the fixed 3 WARN / 24-hour policy. |
| Individual history | wladmin `bot/db/models.py::Warn`, `bot/db/crud.py::list_active_warns`, `deactivate_last_warn`; `bot/handlers/moderation.py::cmd_warns`, `cmd_unwarn` | Select the exact existing SAFECheck sanction ID, retain reason/date/actor/history. No new warnings store and no deletion. `/unwarn` opens management, `/unwarn ID WARN_ID` opens exact confirmation; `/warns` aliases `/warnings`. |
| Concurrency | wladmin `bot/db/crud.py::_advisory_lock_warn` serializes chat/user writes | Preserve SAFECheck's existing group-lock order, persisted tokens, worker lease fencing, unique live-sanction constraint and request keys. Do not add another conflicting lock model. |
| Auto sanctions | Libre `silentPunish`; wladmin `bot/handlers/actions.py::do_warn` | Keep SAFECheck's stronger durable escalation. Consume warnings only after owned MUTE success; retain on failure/unknown. Single reset may cancel only a still-unsent escalation when count drops below 3, never an already applied MUTE. |
| Permissions | wladmin `bot/handlers/moderation.py::prepare_action`: fresh actor/bot/target guards | Preserve live, chat-scoped authorizer and protected owner/admin/bot targets. No global-owner bypass. History is read-only but still checks actual actor rights. |
| UNMUTE/UNBAN | Libre `silentUnpunish` uses all-true permissions and `only_if_banned` | These behaviors already exist in SAFECheck's base. Retain exact ownership/deadline checks and independent permissions. |
| KICK | Libre uses unban for kick; wladmin `do_kick` | Do not copy Libre behavior. Preserve SAFECheck ban-then-release jobs and explicit incomplete/unknown outcomes. |
| Durations | wladmin `bot/services/moderation.py::parse_duration` returns None for absent **and invalid** strings | Keep SAFECheck's stricter parser: malformed duration is an error, not an accidental permanent punishment. No duration still means permanent BAN/MUTE. |
| API errors/retries | Libre `api/tg/SafeTelegram.js::forceHandleRequest` loops; retry counter is initialized inside loop | Do not import this logic. Preserve bounded RetryAfter scheduling and no replay after an ambiguous sanction write. |
| UI/error safety | Both source trees use HTML helpers; their normal command replies do not establish native recipient-only behavior | Use SAFECheck's native Ephemeral transport with explicit-rejection DM fallback. Public success receipts; private pending/failure/unknown results. Never print a fake visibility label. |
| Tests | wladmin `tests/test_warns.py`, `test_moderation.py`, `test_handlers_moderation.py`, `test_targets.py` | Use scenario-based routing/SQL/native-API-double tests and real isolated PostgreSQL/Redis contracts. No claim that mocks prove live Telegram behavior. Libre has no declared package test script in inspected `package.json`. |

## Implementation and data preservation

- `app/bot/warning_panel.py`: localized private control, selection by warning ID,
  target-specific paginated history and per-warning reason/date/administrator.
- `app/warning_services.py`: reuse actor-bound 5-minute reset tokens, snapshot
  count/max-ID checks and group locks; optionally clear one selected active WARN.
- `app/bot/moderation_errors.py`, `moderation_outcomes.py`: specific explanations
  from verified Telegram reads and stored results. No attribution to another
  human moderator based merely on a missing database row.
- `app/bot/moderation_controls.py`, `local_admin.py`: public success vs private
  diagnostic transport; persisted visibility prevents turning a private pending
  message into a purported public success. Existing buttons are retired by state.
- `app/bot/local_moderation.py`: a definitively failed/cancelled unowned APPLY
  cancels its unused RELEASE with audit, avoiding the orphan task seen on staging.
- `app/locales/moderation_control.py`, `app/i18n.py`: complete LT/EN/RU additions;
  retain USER • WARNED / MUTED / BANNED headings and the CRIMSON signature.
- `models.py`, migration `0018`: nullable exact-warning selection, receipt
  visibility and original command. Existing rows/history are retained. No new
  dependency, production deployment whitelist change or unrelated module rewrite.

## Limits and operator acceptance

No Telegram token or real user session is used by these tests. Native API tests
use a stateful test double. PostgreSQL/Redis tests run in owned local containers
with no network and Unix sockets; no production/VPS database is accessed.

Migration 0018 must be reviewed and applied only to an approved test environment
before trying this version. Production deploy allowlists deliberately remain
unchanged. The existing VPS updater is **not** an installer for this branch.
Do not blindly downgrade: retain the additive schema/history and use a reviewed
forward rollback. An older application does not understand individual selections
or private receipt visibility. Migration 0018 permits removing its nullable additions only when they contain no data; populated selections or delivery metadata block downgrade.

Live acceptance still needs: actual Ephemeral support and a second account's
visibility check; DM fallback after an explicit rejection; personal/group
permission semantics; target-only WARN/UNWARN/auto-MUTE and release controls in
the isolated consenting test group. Do not have two pollers for the same token.
Telegram does not provide a general transactional link to PostgreSQL: external
changes, lost permissions, deleted messages and ambiguous network delivery remain
possible. Unknown sanction writes are preserved, never automatically replayed.
