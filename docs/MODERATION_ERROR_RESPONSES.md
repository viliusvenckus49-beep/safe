# Moderation lookup diagnostics

The seven existing commands `/warn`, `/ban`, `/mute`, `/kick`, `/unban`,
`/unmute`, and `/warnings` share command-bound HTML diagnostics in LT/EN/RU.
Headings use `<b><u>…</u></b>`; command names are validated and escaped.

Three separate outcomes are represented:

- Missing/invalid user: `Naudotojas nerastas` with the actual command and
  instructions to supply a username, numeric ID, mention, or message reply.
- Verified absent member: `Naudotojas nėra grupės narys`. Only a fresh,
  matching numeric ID and explicit nonmember status establish absence. For a
  username request, its current username must also match. Unknown statuses,
  API failures, wrong IDs, or changed usernames never establish absence.
- Unverified username: `Nepavyko patvirtinti naudotojo`, with numeric-ID or
  message-reply alternatives.

The exact translated HTML is in `app/locales/local_moderation.py`.
Permission failures have separate administrator/bot headings. Diagnostics after
possibly completed actions never assert that an action was not performed.

## Native ephemeral delivery

Pinned aiogram **3.31.0** supports `EphemeralMessageParameters` natively.
Group errors use `sendMessage` with the original chat/topic, `parse_mode="HTML"`,
and `ephemeral_message_parameters.receiver_user_id` bound to the actual command
sender. No direct HTTP adapter, dependency upgrade, command registration change,
or fake visibility label is needed. Diagnostic messages are not stored as
editable public receipt panels.

[Official Bot API conditions](https://core.telegram.org/bots/api#ephemeral-messages-and-commands):
a chat administrator bot may send ephemeral messages to non-bot members without
an action reference. Other bots need an eligible incoming action reference within
15 seconds. This implementation uses the administrator route appropriate to the
existing moderation bot. Telegram explicitly does **not** guarantee delivery,
including to offline clients. API success is not proof that a client displayed it.

Only explicit BadRequest/Forbidden rejection permits a DM fallback. A rejected
DM permits only a generic public navigation hint, containing neither the target
nor the diagnostic details. FloodWait, timeout, network, and server failures stop
without retry or channel switching. Private-menu commands remain private HTML
replies. Existing callback alerts and successful moderation receipts are unchanged.

## Validation and staging acceptance

Automated tests exercise the real command router and isolated SQL fixtures with a
Bot API transport double. They verify all seven commands, the three outcomes,
LT/EN/RU, wire-model parameters, HTML escaping, permission separation, and safe
fallbacks. They also preserve preemptive bans and history for departed users.
These tests do not prove Telegram client visibility.

Before production approval, apply this revision only to the already isolated
staging bot, preserving its database/session and keeping any other poller for the
same token stopped. Do not recreate its database or repeat login/migrations.

1. Confirm the staging bot is administrator in the approved test group.
2. From the administrator account run each command without a target, then with a
   syntactically valid unresolved username. Verify the correct heading, actual
   command, bold/underlined rendering, and absence of literal HTML tags.
3. Confirm that a second ordinary group member cannot see those diagnostic
   messages. Check the administrator's Telegram client visibility indicator.
4. Exercise verified-absence diagnosis through a controlled lookup failure;
   never assume that a valid preemptive ban or `/unban` must fail for a departed
   member. Transport-level fault cases remain covered by automated tests.
5. Verify valid numeric ID, reply, and verified username targeting retains the
   existing behavior. Use `/warnings` first; sanctions require a consenting test
   target and the existing staging guard's explicit allowlist.
6. Record the tested Git commit and client observations without tokens, phone
   numbers, login codes, or account session files.

No production deployment, migration, restart, or database change is part of this
patch. Production promotion remains subject to separate approval.
