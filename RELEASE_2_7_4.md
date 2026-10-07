# SAFECheck 2.7.4 — home photo

User-provided SAFECheck image bundled unchanged in app/assets/home.jpg and packaged through setuptools package data. /start, first language chooser, language confirmation and home callbacks send one photo with localized caption and the existing inline keyboard. Generic screen helpers now accept an explicit home_photo option; other screens remain text. Existing tracking, replacement at chat bottom and close/cancel behavior apply to the entire photo message. Failed photo sends preserve the previous panel.

Verification: full Docker PostgreSQL/Redis suite351passed74.87s, no skips;8new banner tests passed3.57s plus114 existing Telegram/localization/navigation/group tests passed12.85s. Covers LT/EN/RU in private/groups, caption length, actual SendPhoto/input file, matching asset, replacement/close and failure preservation. Test transports normalize captions only for text assertions; dedicated media tests assert SendPhoto. Ruff check/format107files, mypy38modules, gitdiffcheck, source token/TODO/FIXME scan and Docker build passed. Final image verified bundled JPEG readable by nonroot runtime user. Reviewed callback permissions/escaping unchanged, single-message navigation and package portability.

No migration or database/business logic changes. Startup remains python -m app.main; README documents packaging. No new credentials. Actual Telegram rendering remains a user-phone acceptance check; runtime is temporary hosting.

Deployed safecheck:2.7.4; Alembic check and startup readiness passed, head0008 unchanged. polling_started confirms version2.7.4. Private backup /tmp/safecheck-before-home-photo-20261005.dump; stopped rollback container safecheck-rollback-2-7-3 retained.
