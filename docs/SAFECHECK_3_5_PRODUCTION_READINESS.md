# SAFECheck 3.5 — readiness and private Telegram staging

CODE READY means final source checks pass and confirmed code findings are fixed.
STAGING READY means this candidate and an isolated, controlled test plan can be
handed to an operator for separately authorized Telegram tests; it does not mean
those tests passed. PRODUCTION READY requires live compatibility evidence, tested
production backup/restore and target-host operational approval. Green CI alone
cannot establish production readiness.

| Component | Status | Evidence | Remaining risk |
| --- | --- | --- | --- |
| SAFECheck core | Code checked | Native command/identity/profile regressions and cross-module PostgreSQL snapshots | Target-network profile latency; staging rendering |
| SCAM / REP / TRUSTED | Code checked | Approval, replay, global-admin, manual/role TRUSTED and preservation tests | Production data reconciliation before migration |
| Moderation | Code checked | Local/global separation, real permission request contracts, SCAM-protected unban and durable jobs | Live group rights and API behavior |
| AUTO ADS 3.1 | Code checked | Text/entities/photo/video/album/buttons/preview/version/restore tests | Emoji entitlement and Telegram clients |
| AUTO ADS 3.2 | Code checked | Nine intervals, DST, leases/fences, retry/UNCERTAIN and restart tests | No universal exactly-once delivery |
| GLOBAL ADS 3.3 | Code checked | Four modes, consent, exclusions, bulk replay/fairness and 100/1000-group SQL load | Real target-host scale and global API traffic |
| Customer self-service 3.4 | Code checked | Numeric grants, IDOR/replay/revoke/deadline and next-slot integration | Live deep link and old-client copy support |
| PostgreSQL migrations | HEAD 0016 | Additive retention tests, upgrade/drift; old migration files unchanged | Actual production baseline/size still unknown |
| Redis | Integration checked | Real isolated FSM/isolation/restart and outage tests | Required during cold startup; evicted unsaved input is not durable |
| Docker | Candidate checks | Build, Compose validation, read-only/no-network bot/worker smoke | Actual host limits, storage, reboot and monitoring rehearsal |
| Security | Confirmed code findings fixed | Five source findings with regressions; ACL/FSM/token/source re-review | Not external certification; live permission TOCTOU cannot be eliminated |
| Backups | Disposable rehearsal | Verified dump/checksum/independent restore/full-table comparison | Production/offsite backup and RPO/RTO approval pending |
| Telegram staging | **PENDING** | No test bot/group credentials used; plan below | Blocks production readiness |
| Production deployment | **NOT PERFORMED / NOT READY** | No VPS, production DB or real schedules changed | Separate explicit authorization required |

The exact final counts/checks and CI links live in the final audit and PR
validation section. CODE READY is conditional on that final-head ledger passing.
No remaining confirmed HIGH/CRITICAL defect may be waived as a staging task.

## Controlled Telegram staging — pending, not simulated as live

1. Obtain separate authorization and a newly provisioned test bot token, a private
   test supergroup, two consenting numeric customer IDs, an unrelated test account
   and a local group admin. Never reuse production tokens from chat history.
2. Run the exact reviewed image in a separate Compose project, fresh PostgreSQL/
   Redis volumes and separate secret files. Use only synthetic profiles/campaigns.
   Restrict managed-group enrollment to the test chat ID. Disable legacy MTProto
   relay for this test. Confirm no production DB URL, token, volume or chat is used.
3. Check `/start`, language selection, `/ask` by known ID/username, unknown username,
   SCAM-found/not-found, REP approval/history, TRUSTED and profile navigation in
   LT/EN/RU. Record actual client screenshots and Bot API method/result IDs without
   raw tokens or private content in diagnostic logs.
4. Test local BAN/MUTE/WARN/KICK/UNBAN/UNMUTE, temporary expiry, cancellation and
   missing actor/bot rights. Confirm local sanctions never create SCAM records and
   active SCAM is not weakened by local release. Restore test accounts afterward.
5. Create a draft ad with an internal name deliberately different from its text.
   Preview text formatting/UTF-16 astral characters, photo, video and mixed album.
   Test URL, popup, alert, share, copy and custom-emoji icons with the bot's actual
   entitlement. Verify ordinary emoji fallback and selectable-URL copy alternative.
   Check album companion buttons and retained native message IDs.
6. Assign customer A by numeric ID; create a one-time private deep link. Test cold
   `/start ad_…`, forwarding to stranger B, rotation, revocation, client replacement,
   deadline expiry, old buttons and pending editor saves. Verify B cannot read/edit
   A's campaign or change schedules, groups, deadlines or administrators.
7. Preview/save customer text/media/buttons; verify next scheduled test publication
   uses the new version while the existing schedule/next slot remains unchanged.
   Verify a started attempt retains its old version. Do not edit old Telegram ads.
8. Use the supported 30-minute interval (do not add production shortcuts). Exercise
   Pause/Resume/Stop, expiry and restart. Verify SQL status and actual test-chat
   messages agree. A send already in flight cannot be recalled by Stop.
9. Test multi-group isolation with a second private consenting test group, explicit
   FUTURE consent, removed-group exclusions and local/global/bot revocation.
   Confirm a group the bot merely saw is never enrolled automatically.
10. Do not spam Telegram to manufacture FloodWait. A real 429, if observed naturally,
    must retain the longest shared delay; validate deterministic failure/timeout
    paths using the isolated transport fault harness. Label those as injected, not
    live Telegram acceptance or real API 429 evidence.
11. Restart the candidate and underlying isolated services, check all five health
    signals, no catch-up burst, persisted grants/history, message IDs and UNCERTAIN
    manual reconciliation. Stop candidate first if observed results diverge.
12. Revoke staging links/grants, stop schedules and remove only the named staging
    project/volumes after preserving the evidence approved for retention. Never use
    broad Docker prune or production data to clean up staging.

## Go / No-Go

Go to authorized staging: reviewed final HEAD and CI pass, isolated environment
and consenting test identities verified, no open CRITICAL/HIGH finding.

Go to a separately authorized LIVE change: staging matrix accepted, verified
production/offsite backup and isolated restore accepted, schema baseline and
migration duration rehearsed, pinned rollback image available, target VPS disk/RAM
and monitors checked, schedule activation policy explicitly approved. Without all
of these the answer remains **PRODUCTION NOT READY**.

No-Go: missing backup, inconsistent schema, failed final CI, unresolved HIGH/
CRITICAL finding, leaked credential, unexpected target chat, misleading delivery
status, unknown production topology, or unapproved real schedule changes.
