# SAFECheck 2.9.5 — TOP display-name fallback

If a symbol-only display name is filtered out for the aligned text table, fall back to the available username before the localized generic user label. The numeric cart3lis account has a symbol-only name; table now shows @cart3lis rather than Vartotojas. Normal names, button labels, column alignment/bounds, escaping, ranking and identity dedup remain unchanged. No data modifications or migrations.

Validation:96 focused presentation/result-card/keyboard/Telegram tests passed7.43s including new LT/EN/RU symbol/zero-width fallback regression. Ruff and mypy38 modules passed; full403 suite previously passed for2.9.4 and not rerun for this presentation-only fallback.

Ruff format120 files, git diff check, Docker build/schema/startup passed. Live2.9.5 polling and actual cart3lis table username verified (single entry). Head0008 unchanged; backup /tmp/safecheck-before-top-name-20261005.dump; rollback safecheck-rollback-2-9-4 retained.
