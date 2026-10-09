# Crimson Staff MTProto bridge

SAFECheck's optional bridge uses a private Telegram user session to resolve a current public username and submit **plain `/ban ID`** to Group Help in Crimson Staff. The previously supplied staff ID `-5572682269` was invalid. The authenticated account verified the actual group as `-1004300060813` and verified `@ghStaffBot` membership. Plain `/info 5108847812` returned a response from that bot; `/info@ghStaffBot ...` did not.

The feature defaults to **disabled**. Existing installations continue using the original Bot API worker. There is no separate reputation system, SCAM register, database or ban queue.

## Private account login

Create your application at https://my.telegram.org/apps. On the VPS:

```bash
install -d -m 700 "$HOME/.config/safecheck-mtproto-account2" && sudo docker run --rm -it --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user "$(id -u):$(id -g)" --mount "type=bind,src=$HOME/.config/safecheck-mtproto-account2,dst=/state" safecheck-mtproto-tools:1.42.0 --state-dir /state
```

Enter API hash, phone, login code and any two-step password only in the hidden terminal prompts. Never put them or the session in chat, GitHub, screenshots or runtime logs. The helper stores `api.json` and `account.session` with mode 0600 in a 0700 directory and reuses a saved login. A failed staff check retains the authenticated session.

The private runtime copy is `/etc/safecheck/mtproto`, owned by container UID/GID 10001. A saved session
can become unauthorized and require another login. Provision with a SQLite backup of the session,
not a copy of an open SQLite database. Keep the original login directory private for interactive
maintenance; do not run two workers against the same session file.

To switch accounts, provision a separate private directory (`/etc/safecheck/mtproto-next`), with mode
0700 and UID/GID 10001. The prepared helper accepts API credentials, the new account's phone number,
login code and two-step password interactively:

```bash
sudo docker run --rm -it --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 --mount type=bind,src=/etc/safecheck/mtproto-next,dst=/state safecheck-mtproto-tools:1.42.0-account-switch --state-dir /state
```

When switching to a different staff group, use the prepared
`safecheck-mtproto-tools:1.42.0-staff-switch` helper image and append
`--staff-id <negative Telegram group ID>`. The optional `--staff-bot <username>` selects the bot
to check during login; its default remains `ghStaffBot`. The selected group must appear in the
new account's Telegram dialogs. Membership in the old group cannot satisfy the new group check.
These options only verify the saved login; they do not change production configuration or send
`/ban`. Before enabling a different staff group, set its runtime staff ID, verify the pinned bot
identity and reconcile the exact staff-linked protected group scope.

Join the configured Crimson Staff group with the new account and grant its required Group Help
staff permissions. A fresh session obtains the private group's real entity from Telegram dialogs
when the numeric ID is not cached; it matches the exact configured ID, never the group title. The
helper verifies membership and the staff bot without sending moderation commands. Provisioning
does not enable the relay or change the running bot. After login, verify the pinned staff bot ID and
approved scope before selecting the new private directory and re-enabling the existing relay.

## Configuration and scope

Nonsecret runtime settings:

```
GROUP_HELP_ENABLED=true
GROUP_HELP_STAFF_ID=-1004300060813
GROUP_HELP_BOT_ID=<verified numeric ID of ghStaffBot>
GROUP_HELP_SCOPE_IDS=<comma-separated negative IDs of the linked protected groups>
GROUP_HELP_STATE_DIR=/run/mtproto
```

Compose selector: `SAFECHECK_MTPROTO_DIR=/etc/safecheck/mtproto`. API credentials remain in the private bind mount; they are not environment variables or GitHub secrets. Disabled installations mount an empty state directory and never open a Telegram user session.

`GROUP_HELP_SCOPE_IDS` must match the authorized Group Help staff-linked group list. Startup checks the pinned bot ID, authenticated user membership, staff bot membership, and active SAFECheck approval of all declared groups. These groups use Group Help; protected groups outside that staff scope retain the numeric Bot API path. Group Help owns its global staff command scope, so its staff-linked list must be kept in sync when changing scope. Removing/disabling a declared SAFECheck group stops further global staff submissions; it does not silently re-enroll the group. Unlink it from Group Help before changing the declared scope.

## Execution and real results

- Every public target input now uses the shared identity resolver: `/ask`, `/rep`, profile lookup, reports, SCAM, TRUSTED, reputation adjustments and TOP management. A current public username is resolved through MTProto even when SAFECheck has never observed that account. Verified observations complete the existing unknown-ID user row when possible, preserving its reputation and TRUSTED history. Numeric targets and existing numeric callback identities remain pinned.
- Public lookups share a bounded short cache (30 seconds for successful results, 15 seconds for failures), a six-second foreground budget and the existing persisted Telegram flood-wait protection. Failed/private/unavailable lookups retain the existing unknown-ID or known database identity without inventing an ID. Looking up a regular user does not register SCAM or send a staff ban.
- Unknown-ID SCAM records use fresh Telegram `ResolveUsernameRequest`, not a cached or invented ID. The returned current username must match exactly. Existing numeric identities are never overwritten. The existing audited identity-supplement service checks admin authority and rechecks the username under its metadata lock, then enqueues the existing `BanAction` jobs.
- A trusted Telegram user object observed later by the existing identity service also links pending username-only SCAM records immediately, including retired unknown metadata retained in the SCAM snapshot. The same audited supplement service performs the link and queues bans; numeric judgments remain stable when an alias changes.
- Both interactive registration/refresh and the background worker use this same bridge and durable ban outbox. No separate ban worker competes for claims.
- When an actual user entity is available to the account, the numeric command argument carries a Telegram user mention so Group Help can receive the verified user object. No zero-access-hash peer or fabricated entity is used. A username that now resolves to a different ID cannot replace an existing numeric judgment.
- One plain staff command is durably limited to at most once per record per minute, with eight attempts. Pending work survives restart. Manual refresh retains admin checks and cooldowns and can retry unknown usernames without a confirmation screen.
- Telegram flood waits are persisted and respected after restart. New transport activation requeues prior failed active staff-scope jobs once, preserving live claims and unexpired flood waits.
- Submitting `/ban` is **not success**. Each protected group is checked through Telegram `getChatMember`; only `kicked` is reported as a verified ban. The existing direct numeric Bot API fallback remains. Unverified/rejected operations stay pending, with existing bounded retry and presence-triggered retry.
- Removing SCAM or group protection prevents subsequent dispatch. Reputation, TRUSTED, votes, identity history, translations and callback values remain unchanged.

No DB migration is required: this uses the existing SCAM, identity, `BanAction` and audit tables. Deployment must pass the existing test suite, new bridge tests, syntax/import, Ruff, mypy and Compose validation, then take a verified production backup before changing the bot image. Do not reset production data.
