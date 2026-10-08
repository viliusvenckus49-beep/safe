# Crimson Staff MTProto setup

Configured staff destination: `-1004300060813`; moderation bot: `@ghStaffBot`.

The login tool is an isolated first step. It does not change the SAFECheck application, write to production DB, send `/ban`, or enable automatic staff commands. Connecting the session to existing SCAM jobs remains pending authentication and verification of Group Help staff command scope.

1. Add your controlled Telegram account to Crimson Staff. Ensure the configured staff bot is present and authorized to moderate the intended groups.
2. Create application credentials at https://my.telegram.org/apps (API development tools).
3. In Termius on the VPS run:

```bash
sudo docker run --rm -it --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user "$(id -u):$(id -g)" --mount "type=bind,src=$HOME/.config/safecheck-mtproto-account2,dst=/state" safecheck-mtproto-tools:1.42.0 --state-dir /state
```

4. Enter api_id, api_hash, phone number, Telegram login code and any two-step password only at terminal prompts. Sensitive input is hidden. Do not paste it into chat, GitHub or screenshots.
5. The tool stores `api.json` and `account.session` in `~/.config/safecheck-mtproto-account2` with owner-only permissions. Existing sessions are reused. No credentials are embedded in the image or source archive.
6. Successful setup confirms account ID, staff access and staff bot membership. If staff verification fails after login, the session stays saved; correct membership and rerun.

Telegram requires user authentication for the first account login. Session files grant account access and must be protected like passwords.

Next: connect a disabled-by-default relay to durable SAFECheck SCAM jobs, resolve current public usernames through MTProto, save the actual ID, send the configured staff command, and verify bans in each protected group. Sending a command never proves ban success. Group Help's global staff scope must match the authorized group scope.

Provisioned independently on the VPS: `safecheck-mtproto-tools:1.42.0`; Telethon version import and CLI help verified. SAFECheck 2.14.4 and PostgreSQL/Redis stayed healthy. Provisioning run: https://github.com/viliusvenckus49-beep/safe/actions/runs/37779790269. No account authenticated or moderation commands sent by provisioning.

Authenticated account: `8876719157`. Actual staff group ID `-1004300060813` was obtained from the authenticated account's Telegram dialogs, with @ghStaffBot membership verified. The previously supplied basic-group ID `-5572682269` is invalid for this account. Active session directory is `~/.config/safecheck-mtproto-account2`; no login secrets were copied to GitHub or logs.
