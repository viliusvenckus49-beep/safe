# SAFECheck 2.14.3

Telegram identity-resolution refusals `PARTICIPANT_ID_INVALID` and `USER_NOT_PARTICIPANT` now keep ban jobs eligible for the existing bounded background retries. Previously every TelegramBadRequest set attempts to the terminal limit after the first refusal. Resolving a user through a later group could therefore leave earlier groups queued indefinitely until a fresh presence event.

Preemptive bans still use the confirmed numeric Telegram ID even when membership is `left`. Existing backoff, attempt limits, Telegram rate limits, owner-alert deduplication, permissions failures and retry-on-presence behavior remain intact. Rejected bans never count as successful. No migration, table deletion or reputation change is required.

Regression coverage simulates the first protected group refusing an unknown peer, a later group resolving the same user, and the background worker completing the earlier ban without a join or message. Existing rights and rate-limit refusal tests remain unchanged.

Validation: source commit `860d0f1d72ac3226b887b15a31390ca8accc2c66` passed all 733 tests with disposable PostgreSQL/Redis, Ruff, formatting, mypy, SQLite migration, Docker build and production Compose validation in [GitHub quality](https://github.com/viliusvenckus49-beep/safe/actions/runs/37731811471). Compose validation uses isolated placeholder files, never server credentials.

[Deployment](https://github.com/viliusvenckus49-beep/safe/actions/runs/37731811529) installed `safecheck:2.14.3-860d0f1d72ac` after verifying the restorable backup `/var/backups/safecheck/safecheck-20261008T052314Z-1902384d.dump`. Bot, PostgreSQL and Redis remained healthy. Before/after counts matched: 166 users, 1 reputation event, 13 reputation requests, 1 adjustment, 44 SCAM records, 18 TRUSTED designations, 4 administrators, 10 retained group records, 178 observed members and 3 reports.

The explicitly identified active SCAM target had four successful bans and five identity-resolution refusals. The five failed jobs were requeued under the existing metadata lock and executed through the centralized ban service. All nine approved, enabled groups then reported successful bans, zero pending jobs, and an independently verified Telegram `kicked` membership status, including the five groups where the target was previously `left`. The one-time remediation and push deployment trigger were removed from the final workflow.

Groups outside the approved protection list still require normal owner enrollment and bot restriction rights. Other Telegram refusals remain truthful pending failures; the patch does not bypass Telegram permissions or API restrictions.
