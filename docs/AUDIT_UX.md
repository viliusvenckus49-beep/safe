# Final Telegram UX audit

Reviewed the private and group Telegram routes, shared screens and keyboard builders,
FSM transitions, and LT/EN/RU locale boundaries for the final release candidate.
All dispatcher journeys use isolated test databases and a fake Telegram transport;
this audit did not send live Telegram messages or modify production records.

## Fixed findings

- Pagination callbacks for the public SCAM registry, users, audit log, and REP queue
  accepted Unicode digits through `str.isdigit()`. The superscript digit `²` then
  raised `ValueError` when converted with `int()`. Arabic and fullwidth digits were
  also accepted despite never being emitted by these keyboards. These routes now
  require ASCII digits and return the localized stale response for malformed input.
- The audit and REP queue routes cleared an active draft before validating their
  callback page. Validation now happens first, preserving the draft, current FSM
  stage, and nonce when a malformed callback is rejected.

Root implemented the shared handler fixes. The owned regression file is
`tests/test_final_ux.py`, with 42 parameterized dispatcher cases covering:

- All four pagination routes with superscript, Arabic, and fullwidth digits,
  including active report preservation and no submitted reports.
- Report preview nonce rotation, rejection of previous controls, cancellation,
  and replay after cancellation in LT/EN/RU.
- Group REP comment retry: only the current prompt in the same group can accept a
  reply; previous prompts, another group, private chat, and cancelled drafts cannot
  create a reputation request.
- SCAM identity supplement retargeting and cancellation invalidate previous
  confirmations without changing the unresolved identity.
- Private administrator callback families (`sa`, `ta`, `ah`, `acl`, `rpa`, `rpm`,
  `mod`) remain isolated from groups in all three locales and create no SCAM,
  reputation request, or report records.

## Validation and limits

Root's final isolated full suite passed: **569 tests in 111.52 seconds**, including
the new UX journeys. Three additional locale regressions for the unresolved SCAM
warning also passed. Related navigation, REP comment, identity supplement,
callback, keyboard, screen, and localization tests were included in root's
verification. No further material navigation or localization defect was confirmed
in this review.

User preferences from `CONTINUATION.md` were retained: group result controls and
Cancel behavior, contextual administrator Back routes, mandatory REP comments,
private SCAM registry management, four public slash menu commands, and persistent
per-user LT/EN/RU language selection.

Actual Telegram phone rendering, image/caption appearance, button wrapping, and
client ForceReply behavior still require on-device visual acceptance. Fake
transport journeys verify routing, content, controls, and mutation boundaries;
they do not constitute live Telegram or visual client verification.
