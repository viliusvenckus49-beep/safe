# Replace the deleted bot and owner; preserve ADMIN_IDS

The owner can be set independently using GROUP_OWNER_ID. ADMIN_IDS remains the
same string/list. Existing server-side owner checks grant the new numeric ID its
owner access; ordinary administrators cannot assume that role. The derived
bootstrap_staff set includes the owner for admin-list presentation and initial
command-menu setup, without changing administrator configuration or DB grants.

Changing the owner does not rename/reassign historical Telegram user IDs,
reputation, SCAM records, ads or merchant ownership. The former owner no longer
has owner privileges. If that ID remains in ADMIN_IDS or the administrator table,
its ordinary administrator access remains intentionally unchanged as requested.
There is no schema migration in this change.

## Server-only credential preparation

Run `deploy/prepare_identity_change.py` on the existing VPS with sudo in an
interactive SSH terminal. No dependency installation or token argument is needed.
The program asks for the expected new @bot username, new numeric owner ID and
new token. Token input is hidden; a terminal that cannot hide it is refused.
Do not put token values in chat, GitHub, shell command arguments or screenshots.

The program detects the current safecheck Compose project/source and reads the
existing selectors from /etc/safecheck/compose.env. Override only the selector
path with --compose-env if necessary. It uses the actual current image to make
read-only getMe/getWebhookInfo calls, honoring the current Telegram proxy, and
requires the returned bot ID and username to match the entered identity. It
never starts polling or a worker, sends a message, changes a webhook or alters
command menus. Credentials travel into that probe through private stdin.

It reads aggregate counts from PostgreSQL inside BEGIN READ ONLY and creates a
private staging directory under /var/lib/safecheck/identity-change/<random-id>:

- settings.before / credentials.before: untouched existing file contents.
- settings.next: only GROUP_OWNER_ID changed; ADMIN_IDS is preserved.
- credentials.next: only BOT_TOKEN changed; DB, Redis and proxy credentials stay.
- manifest.json: public identity, original paths/image, content hashes and counts;
  no raw tokens or DSNs.

Directory permissions are 0700 and files 0600. These are staging copies owned by
root, not replacements for the mounted production secret (whose existing owner
and mode must be preserved during the eventual activation). Active files, the
production DB, Redis and running services remain unchanged. Do not print or send
staged file contents. Share only the safe IDENTITY_PREPARED path and status/count
lines if the next deployment step needs them.

## Required cutover review

Preparation does not deploy the Python change or activate the token/owner. Before
activation, use the tested exact-source image, take and verify the production DB
backup, preserve original secret-file ownership/mode, and review the staged
manifest against still-current originals. Do not use an old image whose Settings
requires the new owner to be in ADMIN_IDS.

A new Telegram bot is a new identity:

- Add it to the intended groups and grant its required administrator rights.
  Existing cached permissions for the deleted bot do not establish these rights.
- Review pending moderation, checks, outbox and ads work before restarting workers;
  preserve/reconcile it rather than deleting it to bypass deployment guards.
- Telegram media file_id values belong to their issuing bot. Saved ad/report media
  metadata remains in the database but cannot simply be reused by the new bot.
  Re-upload available originals or forward messages to the new bot and update
  the relevant ad through the existing editor. Do not reactivate unverified media.
- Existing deep links still address the old username. Regenerate/share links to
  the new bot using the existing client-access UI; do not reset clients or tokens
  indiscriminately.
- If the deleted user supplied the MTProto session, that session needs separate
  reauthentication. Do not enable a stale session or delete the session backups.
  GROUP_HELP_BOT_ID pins the external ghStaffBot, not our own new bot; do not replace
  it with the new CRIMSON bot ID.
- Have the new owner open the new bot with /start, then test owner controls and
  ordinary-admin boundaries. Existing administrators also need to start the new
  bot before it can contact them privately.

Do not run ordinary deployment blindly against deleted credentials: its identity
probe may fail before the cutover. Stage credentials first, then perform the
reviewed cutover with the exact tested image and unchanged data services. Never
restore an old database, merge identity records, clear Redis globally, drop
historic data, merge a PR or restart another bot as a shortcut.

## Existing read-only Docker secret files

The preparer accepts the installed root-owned 0444 runtime secret only when its
host path contains a private owner-controlled directory and its ancestors cannot
be replaced by another user (sticky system temporary directories are supported).
This matches the existing /etc/safecheck (0700) and secrets (0700) deployment.
It does not chmod/chown existing files. Ordinary shared/writable modes and symlink
files remain rejected. Error text identifies the path and permissions without
showing file contents. File checks now happen before username/owner/token prompts.

`sudo python3 deploy/prepare_identity_change.py --check-files-only` validates only
file metadata and the nonsecret selector file. It does not read credential-file
contents, ask for a token, query Telegram, touch PostgreSQL or create staging files.
