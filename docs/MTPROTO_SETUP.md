# Crimson Staff MTProto bridge

SAFECheck's optional bridge uses a private Telegram user session to resolve a current public username and submit **plain `/ban ID`** to Group Help in Crimson Staff. The previously supplied staff ID `-5572682269` was invalid. The authenticated account verified the actual group as `-1004300060813` and verified `@ghStaffBot` membership. Plain `/info 5108847812` returned a response from that bot; `/info@ghStaffBot ...` did not.

The feature defaults to **disabled**. Existing installations continue using the original Bot API worker. There is no separate reputation system, SCAM register, database or ban queue.

## Private account login

Create your application at https://my.telegram.org/apps. On the VPS:

```bash
install -d -m 700 "$HOME/.config/safecheck-mtproto-account2" && sudo docker run --rm -it --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user "$(id -u):$(id -g)" --mount "type=bind,src=$HOME/.config/safecheck-mtproto-account2,dst=/state" safecheck-mtproto-tools:1.42.0 --state-dir /state
```

Enter API hash, phone, login code and any two-step password only in the hidden terminal prompts. Never put them or the session in chat, GitHub, screenshots or runtime logs. The helper stores `api.json` and `account.session` with mode 0600 in a 0700 directory and reuses a saved login. A failed staff check retains the authenticated session.

Authenticated account `8876719157` is already logged in. Repeating login is unnecessary. The private runtime copy is `/etc/safecheck/mtproto`, owned by container UID/GID 10001. Provision with a SQLite backup of the session, not a copy of an open SQLite database. Keep the original login directory private for interactive maintenance; do not run two workers against the same session file.

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

- Unknown-ID SCAM records use fresh Telegram `ResolveUsernameRequest`, not a cached or invented ID. The returned current username must match exactly. Existing numeric identities are never overwritten. The existing audited identity-supplement service checks admin authority and rechecks the username under its metadata lock, then enqueues the existing `BanAction` jobs.
- Both interactive registration/refresh and the background worker use this same bridge and durable ban outbox. No separate ban worker competes for claims.
- One plain staff command is durably limited to at most once per record per minute, with eight attempts. Pending work survives restart. Manual refresh retains admin checks and cooldowns and can retry unknown usernames without a confirmation screen.
- Telegram flood waits are persisted and respected after restart. New transport activation requeues prior failed active staff-scope jobs once, preserving live claims and unexpired flood waits.
- Submitting `/ban` is **not success**. Each protected group is checked through Telegram `getChatMember`; only `kicked` is reported as a verified ban. The existing direct numeric Bot API fallback remains. Unverified/rejected operations stay pending, with existing bounded retry and presence-triggered retry.
- Removing SCAM or group protection prevents subsequent dispatch. Reputation, TRUSTED, votes, identity history, translations and callback values remain unchanged.

No DB migration is required: this uses the existing SCAM, identity, `BanAction` and audit tables. Deployment must pass the existing test suite, new bridge tests, syntax/import, Ruff, mypy and Compose validation, then take a verified production backup before changing the bot image. Do not reset production data.
