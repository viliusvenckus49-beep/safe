# Command suggestion menus — 2026-10-28

Development branch: `fix/hide-command-suggestions-20261028`.
Base application: `088ff2c521665630e031d4c2786564eb9f7efb44` (`safecheck-ads-group-detach`).

Telegram slash suggestions contain `start`, `ask`, and `report`; groups additionally show `help`. The old administrator command menu is replaced for the language-neutral scope and LT/EN/RU. Moderation commands (`ban`, `unban`, `warn`, `unwarn`, `mute`, `unmute`, `warns`, `kick`, `moderation`) and the lookup alias `rep` retain their existing handlers and authorization but are not advertised.

Private `/start`, including client deep links, refreshes the user's chat-specific command menu using the current stored locale. Language selection continues to update that menu. This replaces old chat-specific lists that otherwise override global defaults. Existing private menus are refreshed when that user next sends `/start` or selects a language. Existing deployment startup refreshes global/group/admin-group menus and configured bot administrators' private menus.

No callback, business rule, schema, environment variable or runtime credential was changed. This branch is not deployed or merged by this task. After installing the updated application and restarting it normally, Telegram group/admin-group menus are overwritten automatically; an old private menu may require `/start`. Telegram clients may briefly cache command suggestions.

Validation: 129 command-menu, lookup-policy, navigation and Telegram-dispatch tests passed locally. Ruff lint/format checks and `git diff --check` passed. The optional test-sandbox operator also embeds the updated handler and command-menu module with byte-exact hashes; its archived source identity is preserved for compatibility. Further validation is recorded in the PR/CI.
