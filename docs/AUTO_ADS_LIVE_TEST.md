# Existing RedTrustBot sandbox: live AUTO ADS acceptance

This is an opt-in **test deployment**, authorized separately from production.
PR #45 remains unmerged. `tools/auto_ads_live_test.py` targets application revision
`a9018d95a4374da08faeb31a81b6361aef83465f`, including schema 0020. It is a
standalone Python standard-library operator bundle so an iPhone operator does not
need to paste source files, clone repositories or install development dependencies.
The previous c83b096 AUTO ADS sandbox is accepted by its exact original hashes;
its existing private runtime/client boundary is verified before replacement.
This candidate includes the rich Custom Emoji entity-budget correction.
Its embedded, hashed predecessor is the reviewed WARN CONTROLS staging operator;
only its verification/bootstrap helpers are used, never its old `main()`.

## Boundary and prerequisites

* Existing private, root-owned sandbox directory:
  `/home/ubuntu/safecheck-redtrust-mtproto-test-20261020`.
* Existing pinned `SAFECHECK_MTPROTO_TEST_SETUP.py` and
  `SAFECHECK_MTPROTO_LOOKUP_ONLY.py` helpers; SHA-256 pins are embedded. Unknown
  source revisions, source modifications and runtime guards are refused.
* Existing PostgreSQL/Redis, images, dependencies and authenticated MTProto session
  are reused. No login, account enrollment, package installation or image pull.
* Only test bot ID **8986848376**, username **RedTrustBot**, is accepted. Enter its
  current token at the hidden terminal prompt. Never paste tokens into chat,
  command arguments or an issue. Active webhooks are refused, not deleted.
* Group **-1004454398792** and administrator **28563234** are the existing approved
  scope. The update does **not** inspect or require an unrestricted target member.
  Confirmed active moderation sanctions are retained and do not block deployment.
* The Telegram transport guard allows writes only to that group, the administrator
  DM and the optional explicitly supplied test-client DM. Existing native
  moderation guard remains restricted to target **8896102663** in that test group.
  A second/third group acceptance remains untested with this one-group sandbox.
* Add a consenting client's numeric ID with `--client-id 123456789` when applying.
  This merely permits its DM at the sandbox transport boundary; application-level
  invitation/grant checks are still required. Without the option, client DM
  acceptance is deliberately unavailable; administrator previews still work.
* Known active ads must first be paused in the existing test bot. Archived
  campaigns can retain historical `ACTIVE` schedule rows: these do not block
  deployment because the publication worker rejects archived campaigns. The
  read-only check reports their count as `ArchivedActiveSchedulesIgnored`; it
  does not alter or remove those rows. Excluded schedules also cannot publish.
  Pending,
  processing or uncertain publication/moderation work blocks deployment and is
  reported read-only. It is never deleted/replayed to bypass a check.

## Applying a reviewed artifact

Download the script from an immutable, reviewed GitHub commit; check its supplied
SHA-256 **before** executing it. The operator command is:

```sh
sudo -n python3 /home/ubuntu/SAFECHECK_AUTO_ADS_LIVE_TEST.py apply
```

For the separate consenting test client's editor/deep-link acceptance:

```sh
sudo -n python3 /home/ubuntu/SAFECHECK_AUTO_ADS_LIVE_TEST.py apply --client-id 123456789
```

The script takes the same nonblocking lock as the older moderation updater,
verifies the production container fingerprint and validates the test token with
read-only Telegram calls. It stops only the owned test bot, rechecks work, and
writes a private SQL dump/context backup. It restores that dump into a new random
**test PostgreSQL database**, runs the actual migrations and Alembic drift check,
and verifies every old table's row hashes over its original columns. Existing
audit rows remain identical; the intentional FUTURE → EXISTING audience-policy
conversion is accounted for. On successful rehearsal it drops only that random
copy. A failed rehearsal is retained for inspection.

Only after successful rehearsal does it update the reviewed application source,
migrate the sandbox database, verify retained rows again and replace/start the
test bot. PostgreSQL, Redis and the session are neither replaced nor cleared.
Successful output ends with:

```text
AUTO_ADS_STAGING=HEALTHY; Schema=0020; HEAD=a9018d95a4374da08faeb31a81b6361aef83465f
DBPreserved=True; RedisPreserved=True; SessionPreserved=True; ProductionBaselineUnchanged=True
```

This confirms sandbox process health, **not live feature acceptance**. The
updater does not activate ads or send test messages/sanctions itself. Subsequent
normal scheduler/moderation workers still execute explicitly configured work.

For read-only inspection, even while an ad is active:

```sh
sudo -n python3 /home/ubuntu/SAFECHECK_AUTO_ADS_LIVE_TEST.py status
```

On `AUTO_ADS_TEST_STOP`, preserve the output and backups for review. Do not repeat
prepare/login or edit database rows. There is no automatic destructive downgrade
or rollback. Failures before source replacement preserve the old code/schema. Failures after
source replacement can leave the new source with the old schema until recovery;
failures after stopping can leave the test bot stopped. The reported private backup and code
backup allow a reviewed recovery. The old WARN CONTROLS installer does not know
the AUTO ADS revision and must not be used to overwrite it.

## Telegram acceptance, in order

1. Open **@RedTrustBot privately**, send `/ads`. Confirm AUTO ADS navigation and
   a single edited menu when moving Back. Do not use the production bot.
2. Create **LIVE A** and **LIVE B**. Set A's text, media, URL rows, interval,
   validity and group. Inspect B: its values/content must remain unchanged.
   Create more drafts to exercise four-item pagination, filters and search.
3. Preview text/photo/video/animation/document and URL buttons. These previews
   must stay private. Check LT/EN/RU. No group delivery occurs from Save/Preview.
4. Activate A explicitly for **Crypto (-1004454398792)** only. Use a real allowed
   interval (minimum 30 minutes) and an explicit appropriate start time. Observe
   the actual scheduled delivery, then compare A's statistics/history with it.
   Do not lower interval limits or manually edit `next_run_at` to fake acceptance.
5. With the separately allowlisted test account: generate an invitation, redeem
   it once, edit A, save, reopen via the authorized permanent link. Check another
   account/ad, replayed token, changed client and revoked access are rejected.
6. Save new content between scheduled slots: no immediate extra delivery; the
   next slot uses that version. Pause A: no further slots. Resume, expiry and
   confirmed deletion affect only A. B remains unchanged throughout.
7. A later controlled sandbox restart should preserve content/ACL/history/FSM.
   Pause schedules before using the updater again; do not clear active work.

These steps need real Telegram observation. Automated Bot API doubles do not
prove Telegram ephemeral delivery, real flood control, token claims, client
rendering or network ambiguity. Multi-group acceptance requires an explicitly
extended sandbox allowlist; do not repurpose a production group.

Production changes, production database operations, VPS production deployment
and PR merge remain outside this procedure.
