# SAFECheck 2.0 baseline verification — 2026-10-03

Baseline features: numeric Telegram identity and username history; reputation events and anti-abuse; profiles, lookup and leaderboard; guided reports with evidence/preview/cancel; administrator moderation and audited active/removed scam registry; Lithuanian HTML presentation; PostgreSQL/Alembic, Redis FSM, typed configuration, structured sanitized logs, Docker and CI.

Executed checks:
- Complete suite in Docker against isolated PostgreSQL and Redis: 97 passed, no skips (14.28s).
- Ruff lint and formatting passed; mypy passed for 17 source files; pip check passed.
- Docker image build passed.
- Live PostgreSQL Alembic check: no new upgrade operations detected.
- Live application --check: startup_check_passed.
- Independent reviewer approved architecture/security and transport; final decision wording corrected to remain accurate when an old approval callback is repeated after scam removal. Presentation tests rerun after correction.
- Source scan found no TODO/FIXME or suspected real Telegram tokens in inspected source/tests/migrations/docs.

Live test bot @RedSafeCheckBot uses the rebuilt image. User confirmed Telegram buttons worked. Runtime proxy misconfiguration during recreation was corrected; polling and message processing observed. Credentials remain outside the repository. Test environment hosting does not guarantee permanent uptime. Final wording correction is pending the next image deployment.

Remaining external acceptance: manually complete actual Telegram report/evidence/moderation journey. Automated dispatcher journeys cover these paths; they do not establish end-to-end Telegram file delivery. Hosting, backups, monitoring and BotFather credential replacement remain deployment tasks. The exposed credential must be replaced for permanent deployment.

Next requirements are recorded separately in NEXT_STAGE.md. Current reputation behavior is the baseline, not the newly requested approval-controlled reputation design.
