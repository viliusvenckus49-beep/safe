# AUTO ADS changed files

Changes relative to `0206827ac06cdf926010df4893ff0eaa12c82e31` (PR #44).

- `app/ads_content.py`
- `app/ads_global.py`
- `app/ads_html.py`
- `app/ads_invitations.py`
- `app/ads_unified.py`
- `app/bot/ads.py`
- `app/bot/ads_customers.py`
- `app/bot/ads_publisher.py`
- `app/bot/auto_ads.py`
- `app/bot/callbacks.py`
- `app/bot/handlers.py`
- `app/bot/states.py`
- `app/i18n.py`
- `app/locales/ads.py`
- `app/locales/auto_ads.py`
- `app/models.py`
- `docs/AUTO_ADS_CUSTOM_EMOJI.md`
- `docs/AUTO_ADS_LIVE_TEST.md`
- `docs/AUTO_ADS_UNIFIED.md`
- `docs/AUTO_ADS_UNIFIED_AUDIT.md`
- `docs/AUTO_ADS_UNIFIED_FILES.md`
- `docs/AUTO_ADS_UNIFIED_VALIDATION.md`
- `migrations/versions/0020_unified_auto_ads.py`
- `tests/run_runtime_smoke.py`
- `tests/test_ads_content.py`
- `tests/test_ads_customers.py`
- `tests/test_ads_customers_migration.py`
- `tests/test_ads_customers_ui.py`
- `tests/test_ads_global.py`
- `tests/test_ads_global_final_audit.py`
- `tests/test_ads_global_ui.py`
- `tests/test_ads_publication.py`
- `tests/test_ads_publication_ui.py`
- `tests/test_ads_telegram.py`
- `tests/test_auto_ads_live_operator.py`
- `tests/test_auto_ads_migration.py`
- `tests/test_auto_ads_ui.py`
- `tests/test_auto_ads_unified.py`
- `tests/test_group_moderation_migration.py`
- `tests/test_stage35_runtime.py`
- `tools/auto_ads_live_test.py`

No new runtime dependency; no change to payment, SCAM, TRUSTED, reputation or
moderation implementation. The legacy moderation migration test only adds the new
invitation table to its new-table inventory. Runtime smoke fixtures use schema 0020.
