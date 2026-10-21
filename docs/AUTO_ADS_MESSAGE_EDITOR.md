# AUTO ADS: immediate message editor and bottom-of-chat controls

The user explicitly selected immediate save for text, media and buttons, for
administrators and assigned clients. New content controls now use the existing
AdsService/CustomerAdsService transactions, grants, version checks and audit
history through `save_received()`. This validates and saves READY native input
without claiming a Telegram preview occurred: `previewed` remains false and the
content_saved audit records `validation=native_input`. The legacy draft save and
restore paths retain their preview requirement. No schema migration is added.

### Activation and scheduled publication

The publication gate now accepts either a saved legacy preview or the exact
`ads_content_saved` transaction marked `validation=native_input`. The latter must
match the selected campaign, current revision, saved draft ID, content field,
actor and immutable version request key. It accepts TEXT/MEDIA/BUTTONS only;
RESTORE still requires its original preview flow. A preview flag is never invented.
Previously this gate only accepted `previewed=true`, so immediate saves could
neither activate nor be selected at an existing schedule's next slot.

Unified activation, retained internal global operations, individual schedules
and the enqueue worker all use this same check. Existing committed native saves
are recognized without editing them again or migrating data. Expired dates,
missing audience, non-publishable content and insufficient rights remain errors.
Activation and edits do not add an immediate send; an already queued publication
keeps its original pinned version and the next slot uses the newly saved version.

If a date such as `2004-10-22` is stored as the end of validity, activation must
still fail as expired. Correct the ad's validity using the actual intended dates;
the update does not silently rewrite them.

## Native dialog

* Text / Media / URL button controls open a fresh prompt at the bottom of the DM.
* Valid text, a single supported media file or a complete button set immediately
  creates an immutable content version and a “Message set” receipt, replying to
  the received input. There is no extra Preview/Save requirement in this flow.
* The existing individual URL add/edit/remove/order menu remains available, now
  backed by immediate content saves. Other button kinds and layout are retained.
* A native photo/video album remains a single collecting draft until “Finish
  collection” is pressed: Bot API updates do not mark the last album part.
  Back cancels collection rather than saving a partial album. This is an album
  boundary, not an extra preview/save approval.
* Back replaces the receipt with the selected ad's main menu. It does not emit a
  public group message. Main panels show Text/Media/Buttons presence indicators.
* Per-field See and full preview use the actual existing native sender, then put
  one small Back controller **after** the preview. Back removes the preview and
  shows the ad's main menu. Previous recorded previews/controls are retired;
  incoming user messages and published group ads are never deleted.

A Telegram text edit cannot move an old message to the bottom. The controller
therefore sends a replacement and deletes its recorded predecessor. If Telegram
refuses deletion, it attempts to remove the old keyboard; stale actions are also
rejected by the current frame/token, version and fresh ACL checks. This ensures
one executable controller; Telegram may retain an old non-executable bubble if
both cleanup methods fail. The client controls its own scroll position; the bot
cannot force-scroll an iPhone. No HTML forms, Mini App or simulated visibility
labels are used.

## Isolation and failure behavior

FSM metadata is scoped by the existing bot/chat/user storage key. Input binds the
ad ID, content version, role, client grant, current controller and opaque frame
token. Production uses RedisEventIsolation. Restarting a dispatcher with the same
Redis preserves the input/controller binding. Grant revocation, expiry, admin
revocation and another administrator's successful edit are rechecked at write.
Old Back/Clear/Finish actions cannot change a later frame or saved revision.

Only the selected content version changes. Schedule interval, audience, validity,
next run, paused state and other ads are untouched. Saving does not send a group
message. The existing scheduler pins a consistent version at its scheduled slot.
Native validation errors leave the previous saved content intact. Delivery of a
receipt can fail after a successful database commit: this is logged as an
unconfirmed receipt rather than claiming that the content save failed or applying
it again. Preview failure explicitly says saved content is unchanged. Real
Telegram rendering, cleanup/flood behavior and phone scrolling still need live
acceptance; request doubles do not establish those properties.

## Test acceptance

`tests/test_ads_message_editor.py` covers LT/EN/RU text/media/buttons for both
roles, back/cleanup, optional preview ordering and failure, partial albums,
individual URL operations, stale frames, grant revocation, forbidden actors,
legacy preview protection, idempotent received saves, native Custom Emoji/HTML,
separate-ad/schedule retention, actual Redis restart and PostgreSQL competing
administrators. Existing content/customer/publication tests remain in place.

Live staging: open /ads, select an ad, Text → send → Message set → Back. Repeat
for Media and URL buttons and through a consenting client's existing deep link.
Use See repeatedly: the Back controller must be below the preview, and Back must
return to one ad menu. Never activate a production audience for this acceptance.
