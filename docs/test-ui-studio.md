# SAFECheck UI Studio

UI Studio is a separate Telegram bot for changing and previewing SAFECheck text and buttons.
It uses the same catalogs and presentation/keyboard builders with synthetic user data.
It never registers production handlers, opens a reputation database, starts moderation workers,
or connects to MTProto / Group Help. The Telegram transport rejects moderation API methods.

## In Telegram

1. Open the test bot and send `/start` or `/ui`. Access is limited to administrators and explicitly added editors in private chat.
2. Select **Buttons** or **Texts**. Browse, or search for part of the text.
3. Select an item, choose **Edit**, then send the replacement. Emoji are supported. Required placeholders must stay present. Buttons stay on one line, up to 64 UTF-16 units; templates up to 3500 units with supported Telegram HTML.
4. Choose **Preview** to see a single item, or preview whole main/admin menus, profile, information, SCAM and TRUSTED messages.
5. **Button layout** lets you reorder the main and administration menus. Send numbered rows; every existing button must appear exactly once. Its callback identity never changes. Normal-user main-menu previews exclude administration controls.
6. **Language** selects LT / EN / RU. Edits are independent per language. Layout is shared across languages.
7. **Restore default** removes an individual override; **Restore layout** restores menu ordering.
8. **Download design** exports `safecheck-design.json`, containing only version, text overrides and layouts. Exporting does not apply anything to production.
9. Administrators have **Editors → Add by ID**. Enter a positive numeric Telegram user ID to grant immediate editing access. Editors can edit and preview designs, but cannot view or change the access list, including through forged callbacks. New members open the bot themselves and press **Start**; the bot sends no unsolicited invitation.

Design changes persist across restarts in `/var/lib/safecheck-ui-studio/design.json`.
Test-only administrators and editors persist separately in `access.json` in the same private state directory. They never become production SAFECheck administrators. Permissions are not included in the exported design.
Editing the same field concurrently detects a stale draft rather than overwriting another administrator's edit.
All user identities and status counters in previews are examples. Clicking production-style action
buttons shows a demo notice or navigates preview screens; it never bans/unbans or changes reputation.

## Deployment

Create a separate bot through BotFather `/newbot`. Store its token as the GitHub Actions secret
`SAFECHECK_TEST_BOT_TOKEN`; never paste tokens into chats, source files or issue descriptions.
Run **SAFECheck UI Studio deploy** against the tested code.

The workflow uses the existing pinned VPS SSH connection. It builds an independent image and runs
only Compose project `safecheck-ui-studio`, service `studio`. It refuses a token for the production
bot, copies only its configured administrator IDs, saves a private design backup on updates, and
verifies production container identities/images/start times are unchanged. It never runs production
Compose or database migrations. Initial failure stops only the new preview service; update failure
restores its previous private configuration and image. The runtime file is
`/etc/safecheck-ui-studio/runtime.env`, mode 0600. The editor state is a separate private mount.
The administrator allowlist is read through the production container's existing secret loader;
only its numeric bot identity and administrator IDs leave that process. Preview validation runs
offline before startup. A small standard-library health probe checks polling and persisted state
without importing a second bot process; the editor container has a 256 MiB memory limit.
For subsequent updates the root deployment script supports `--reuse-config`, which uses the test
bot credential already installed on the VPS without transmitting it back to GitHub. The optional
`--grant-admin ID` grants a test-only administrator, preserving existing editors and design. The
access file is backed up on deployment and restored if deployment fails.

Production SAFECheck does not load exported design files. Moving a chosen design into production is
a separate code update using its unchanged action callbacks and localization keys.
