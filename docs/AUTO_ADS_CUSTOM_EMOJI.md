# AUTO ADS: Custom Emoji text validation

The old validator rejected any message with more than 100 total entities. This
incorrectly combined native custom emoji, user formatting and automatic links or
mentions. For example, 100 valid custom emoji plus one bold entity failed before
preview with the misleading generic “Invalid formatting or Custom Emoji metadata”.
A single emoji could still be imported successfully.

The validator now tracks separate budgets: 100 custom emoji and 100 explicit
formatting entities. Automatic URL/mention/hashtag and similar native entities do
not consume the formatting budget. All native UTF-16 offsets, IDs, text and entity
order are preserved, including directly sent and forwarded text/captions. No emoji
is silently removed or converted into an invented ID or plain-text replacement.
The existing 4096-unit text and 1024-unit media-caption bounds remain unchanged.
An explicit 4096-entry validation-work ceiling and a non-quadratic nesting check
bound processing of untrusted entity arrays. Invalid offsets, surrogate slices,
URLs, IDs and crossing spans still fail validation.

Known count errors have distinct LT/EN/RU messages (`ads_emoji_count`,
`ads_format_count`, `ads_entity_count`), separate from malformed metadata. Exceeding
a count leaves saved content untouched. Existing ACL/grants, immutable content
versions and publication timing are unchanged. Legacy drafts retain their preview
requirement; the subsequent native immediate editor follows the user's explicit
request to save received input without an extra Preview/Save step. No migration
is needed beyond the already reviewed AUTO ADS schema 0020.

## Verification sources and delivery limits

Reviewed official references on 2026-10-21:

* [TDLib MessageEntity.cpp](https://github.com/tdlib/td/blob/master/td/telegram/MessageEntity.cpp):
  `get_input_message_entities` excludes CustomEmoji from its 100 user-entity
  counter. This is a verification source; its implementation was not copied.
* [Telegram client configuration](https://core.telegram.org/api/config#message-animated-emoji-max):
  `message_animated_emoji_max` is the custom-emoji count limit; the published
  configuration currently shows 100. The conservative application cap follows it.
* [Bot API formatting](https://core.telegram.org/bots/api#formatting-options) and
  [Bot API 9.4 changelog](https://core.telegram.org/bots/api-changelog): Telegram
  separately controls whether a bot may send custom emoji. For direct private,
  group and supergroup messages an owner Premium subscription can permit this;
  the Fragment additional-username route is also documented. The editor does not
  infer BotFather ownership or subscription state from the current administrator.

Preserving incoming metadata is not proof of Telegram accepting an outgoing
message. The existing real preview remains the delivery check. Synthetic emoji
IDs and Bot API doubles in tests establish validation, routing and request
preservation only. They do not establish actual emoji rendering or permission.

## Regression evidence

New tests cover 100 emoji plus nested formatting, automatic mentions, forwarded
text and captions, separate count errors in LT/EN/RU, invalid entity boundaries,
randomized crossing/nesting equivalence, administrator and authorized-client
preview/save, and one scheduled publication with unchanged next-run time and no
duplicate slot. No user advertisement content or real credentials are needed.
