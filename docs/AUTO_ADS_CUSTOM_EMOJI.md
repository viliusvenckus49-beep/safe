# AUTO ADS: text length and Custom Emoji validation

The validator measures the parsed text in Unicode code points: 4096 for a text
message and 1024 for a media caption. UTF-16 units remain necessary for Telegram
entity offsets and lengths, but are not the text-length budget. Mathematical
Unicode letters and emoji outside the BMP previously consumed two units and
could incorrectly fail the local validator. HTML tags are parsed before length
validation; the visible text and native entity metadata are preserved.

Ordinary bot messages have no separate local 100-Custom-Emoji cap. The validator
retains the 100 explicit formatting-entity limit, excluding Custom Emoji and
automatic URLs/mentions. All incoming emoji IDs and entity order are preserved.
An 8192-entry work ceiling permits 4096 custom emoji plus formatting while
bounding untrusted input. UTF-16 boundaries are computed once, avoiding repeated
prefix decoding. Invalid offsets, surrogate slices, URLs, IDs and crossing spans
still fail validation. No emoji or content is silently removed.

Length failures have distinct LT/EN/RU messages with actual/allowed counts for
text, media captions and albums. A generic invalid-content error no longer
misdiagnoses every failure as a length violation. A media-caption failure states
that the saved ad has media; editing text never silently clears that media.

Native administrator/client input saves immediately as an immutable content
version for the next scheduled send. Rejection leaves the saved version intact.
Content edits do not activate, reschedule or immediately publish an ad. Existing
ACL/grants, other ads, buttons and media are preserved. No database migration is
required; schema remains 0025.

## Verification sources (reviewed 2026-11-12)

* [TDLib MessageContent.cpp](https://github.com/tdlib/td/blob/master/td/telegram/MessageContent.cpp):
  bot message text and captions use `utf8_length` with the respective maximum.
* [TDLib MessageEntity.cpp](https://github.com/tdlib/td/blob/master/td/telegram/MessageEntity.cpp):
  `get_input_message_entities` excludes `CustomEmoji` from its 100 explicit-entity
  counter. Its separate secret-chat emoji limit is not an ordinary bot-message
  limit. `message_animated_emoji_max` concerns animation and must not be used to
  impose an ordinary-message send cap.
* [Bot API formatting](https://core.telegram.org/bots/api#formatting-options):
  outgoing Custom Emoji still require Telegram permission for the bot. Correct
  metadata preservation does not itself establish permission or rendering.

Tests use simulated Telegram transport and synthetic IDs. They cover 4096
astral characters, 1024-character media captions, HTML parsing, more than 100
custom emoji, surrogate boundaries, localized precise errors, and administrator
and client immediate-save journeys without extra publication or schedule changes.
A real preview is still the way to confirm Telegram rendering for a particular
bot. No production advertisement text or token is included in this document.
