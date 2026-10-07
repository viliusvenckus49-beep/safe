# SAFECheck 2.14.1

SCAM presence retries now compare Telegram group IDs as PostgreSQL BIGINT. The previous JSON integer comparison raised an int32 overflow for real supergroup IDs before a retry could reach Telegram.

Generic unhandled errors no longer post replies in groups. A SCAM user observed in an approved, enabled group triggers one private failure alert to the configured group owner. Existing audit records persist the claim per SCAM activation and group, including across process restarts. Telegram delivery failures are logged and do not produce repeated public or private messages. Alert transactions are separate from the ban worker's claimed jobs.

SCAM receipts retain their existing command routes and use the requested BLOCK STATUS format: Banned / Already banned / Queued. Already banned is counted only after Telegram returns a kicked membership status. A failed membership lookup still allows a direct ban attempt; Telegram rate limits remain respected. Unknown IDs and groups without protection retain their truthful localized states.

No database migration or data-service change is required. Existing SCAM, TRUSTED, reputation, group records and production volumes are retained. Deployment uses the existing tested, backup-verified code-only workflow.

Regression coverage includes large PostgreSQL chat IDs, presence retries, concurrent private-alert claims, restart deduplication, localized escaped notifications, silent group errors, exact receipt formatting, existing bans and failed membership lookups.
