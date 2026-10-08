# SAFECheck 2.14.3

Telegram identity-resolution refusals `PARTICIPANT_ID_INVALID` and `USER_NOT_PARTICIPANT` now keep ban jobs eligible for the existing bounded background retries. Previously every TelegramBadRequest set attempts to the terminal limit after the first refusal. Resolving a user through a later group could therefore leave earlier groups queued indefinitely until a fresh presence event.

Preemptive bans still use the confirmed numeric Telegram ID even when membership is `left`. Existing backoff, attempt limits, Telegram rate limits, owner-alert deduplication, permissions failures and retry-on-presence behavior remain intact. Rejected bans never count as successful. No migration, table deletion or reputation change is required.

Regression coverage simulates the first protected group refusing an unknown peer, a later group resolving the same user, and the background worker completing the earlier ban without a join or message. Existing rights and rate-limit refusal tests remain unchanged.

Full CI, deployment and targeted remediation results will be recorded after verification.
