# Latest: SAFECheck 2.13.1 persistent notices (activation blocked)

Permission denials and SCAM/TRUSTED receipts persist in chat. Receipt callbacks revalidate numeric authorization, preserve source notice and retire only active menu. ContextVar resets per update; menus/wizards otherwise unchanged. 29 regression cases added; full isolated PostgreSQL/Redis suite 598 passed111.10s, installed-image76 passed17.78s, operations44 passed0.31s, Ruff159/mypy49/pipcheck passed. No migration, head0009. Image safecheck:2.13.1 built. Backup verifier readiness corrected to TCP to avoid temporary startup server.

Live deploy candidate schema/startup checks and isolated backup restore passed, but Telegram rejected configured token with TelegramUnauthorizedError. Do not claim polling active. Helper /tmp/safecheck-persistent-notices-deploy.py rolls back on failed health; rollback name safecheck-rollback-2-13-0. Need valid token privately configured before activation; never print secrets/log payloads. OVH VPS access still not supplied. Read RELEASE_2_13_1.md.

# Latest: SAFECheck 2.13.0 final audit and operations

Read RELEASE_2_13.md and docs/FINAL_REVIEW.md. Independent security/data/UX findings and operations review completed; material fixes tested. Shared identity lock + fresh numeric authorization in seven admin writes serializes with owner revocation. SCAM ID supplement rejects conflicting observed username owner. Unresolved username SCAM match on known ID renders separate LT/EN/RU warning without auto-binding/ban. ASCII pagination validation precedes draft clearing. Typed ID limits, successful poll/worker heartbeat, aggregate status added.

New deploy/ CLI, production Compose, systemd templates, private secret entrypoint, verified backups, controlled restore/rollback, rclone adapter and explicit opt-in alerts. Rollback persists image selector; restore preflights Redis maintenance PING before stopping/dropping. Permanent VPS/timers/offsite/alerts NOT configured. Do not silently send operator alerts or reuse bot token/admin recipient. Read docs/OPERATIONS.md before future hosting.

Final full Docker suite with isolated PostgreSQL/Redis:569 passed111.52s no skips. Ops44 included; installed-package89 passed12.32s. Ruff lint/format, mypy49, pipcheck dev+image, diff/scans/build passed. Actual disposable production Compose + fake Telegram + backup/checksum + full restore/rescue + FSM reset passed after final fixes. systemd offline syntax passed using temporary Docker dependency stub, no units installed. No new migration, head0009.

Live safecheck-live-test image safecheck:2.13.0, running, healthy Docker heartbeat, restartunless-stopped. Candidate schema/startup plus successful poll/worker/dependency readiness passed. Private live backup /tmp/safecheck-before-final-release-20261006T154332Z.dump and checksum0600, fully restored in no-network/tmpfs verifier before release. Stopped rollback safecheck-rollback-2-12-0 retained. No production QA records inserted. Never print env/token/container Env/backup rows. Old stalled host audit pytest processes terminated; use network-enabled Docker checks for future QA. Rotate previously shared token privately before permanent hosting.

# Latest: SAFECheck2.12.0 SCAM administrator supplements

Read RELEASE_2_12.md. New app/scam_management.py, app/bot/scam_admin.py, localized catalog. Privateadminregistry retains original pagination/search callbacks, new sa record/detail/field/confirm callbacks. Missingfields only, explicitpreviewFSMnonce, sharedidentitylock/freshauth/audit+banoutboxatomic. Privateunknown /add_sc enables nextplainIDconfirmation. Known /add_sc ID and wizard preserved. No migration(head0009), historicalREP/reportsnotmerged. Full444passed93.63s inclPGconcurrency/Redis; targeted33passed; Ruff135/mypy47. Live2.12.0 polling_started verified2026-10-06T14:22:53Z, schema/startupchecks passed, restartunless-stopped. Privatebackup /tmp/safecheck-before-scam-identity-20261006.dump; rollback safecheck-rollback-2-11-0. Installedpackage33passed9.41s.

# Latest: SAFECheck 2.11.0 mandatory REP comments

Userrequires>=5chars every+/-REPbutton/profile/command/reply. New RepVoteFlow.comment and app/bot/rep_vote.py; commandsacceptinlinecommentorwizard, buttonsalwayswizard. Service.vote keywordcomment mandatoryvalidation(defaultNone rejected),5visible/nonspace min1500max, normalization/Cfstripping, replaycomparescomment. New ReputationRequest.comment nullablelegacy, migration0009 DBcheckNULLortrimlen5..1500; SQLitebatch explicitlyreadds originalunnamed value/status/selfchecks. Moderatorcardescapes/boundscomment; notesunchanged. Grouponlyactinguserreplycurrentprompt accepted (no unrelatedchatter/others); selectiveForceReplygivermention, targetcode; invalidpromptrefresh/deleteold; cancellation/self/duplicate/cooldownpreserved. Updatedoldservice/UItestsubmissions supplycomment, callbacktestnowfillswizard.21newcommentcases+legacyconstraintmigrationregression. FullDockerPGRedis435passed88.76s, SQLitemigration+PGisolatedpreflight upgrade/check/downgrade/reupgrade passed. Ruff131/mypy44/build/schema/startup passed. Live2.11.0polling/Nonecommentrejectverified, head0009. All oldtable data fingerprints preservedwhilebotstopped (excludeonlynewcommentfield+alembicversion). Privatebackup /tmp/safecheck-before-rep-comments-20261005.dump rollback safecheck-rollback-2-10-0; doNOTdowngradeafternewcomments (dropsfield). No realuserREP insertedverification. Old2.8.3/2.8.4/2.9.0 retiredfordisk, recent/volumespreserved. Neverprintenv. Read RELEASE_2_11.md.

# Latest: SAFECheck 2.10.0 TRUSTED admin management

Userapprovedprivate/adminTrustedusers list/search/confirmationremove. NewTrustedManagement service+repo trusted_candidates; listingmanualactive/TOPincluded/currentnumericroles (retainedinactive/superseded managementrecordsintentional).8rows pages/query64 literalLIKEescape. Sourcescards/callbacks/FSM3locale catalogcentral. Roleonlyreadonly; manual+role warnsroleTRUSTEDremains. RemoveatomicallymanualFalse+TOPFalse preservesREP/access/history, freshadminauthorization identity+userlocks; immutable TrustedAction/TopVisibilityAction noncepair, audit trusted_removed/top_visibility, replaycannotreclearnewgrant. Structuredta callbacksprivateonly/staleFSMguard. Back/cancelcontextualreturnslist/page/query; errornavrestorestrusted context. FullDockerPGRedis413passed79.95s no skips includingPG6concurrentremovals, rollbackpermissions/replay/UI3langtests. Ruff127/mypy43/build/schema/startup passed. Live2.10.0polling/menu/listreadonlyverified; no realuserremovaltests. Head0008 unchanged backup /tmp/safecheck-before-trusted-management-20261005.dump rollback safecheck-rollback-2-9-5. Neverprintenv. Read RELEASE_2_10.md.

# Latest: SAFECheck 2.9.5 TOP symbol-name fallback

UserTOPsaysVartotojas becausecart3lisdisplayname™ filteredbytable. _table_name optionalfallbackusedby leaderboard available@username before genericlabel. Buttonname™ unchanged userpreferrednames. Escapingwidthalignmentnormalnames unchanged.96focusedtests7.43s/Ruff120/mypy38/diff/build/schema/startup passed, previousfull403notrerun presentationonly. Live2.9.5polling/cart3lisusernameintableandsingleentryverified. Backup /tmp/safecheck-before-top-name-20261005.dump rollback safecheck-rollback-2-9-4, head0008 unchanged. Neverprintenv. Read RELEASE_2_9_5.md.

# Latest: SAFECheck 2.9.4 TOP identity deduplication

Usercart3lisduplicatedTOP: oldusername-onlymanualtrusted/explicitTOP andknownnumericmoderator. Addedleaderssupersededunknownfilter sameconceptasdirectory beforelimit/ranking. Keepknowncurrentidentity, preserveoldhistory/status/REP nofalsemerge. Explicitexclusioncannotrevealunknownplaceholder, usernamechangescanexposeunresolvedagain. Newregressioncoversall. FullDockerPGRedis403passed73.82s/Ruff119/mypy38/build/schema/startup passed. Live2.9.4polling/livecart3lisexactlyoneknownIDTOPverified. Backup /tmp/safecheck-before-top-dedup-20261005.dump rollback safecheck-rollback-2-9-3. Head0008unchanged. Neverprintenv. Read RELEASE_2_9_4.md.

# Latest: SAFECheck 2.9.3 manual/role TRUSTED qualifies TOP

UserclarifiedtrustedpeoplecanTOP; rokfeleriss28 manualtrusted hadnoexplicitTOP andfellout2.9.2. CorrectedSQLmanualTrustedDesignationactive OR numericcurrentrole OR explicitTopVisible; explicitfalseveto. REPonlyneverqualifies; >=0/noSCAM/limit10 preserved. RoleSQLAdministratoractive + bootstrapifnoDBrow, immutableowner; freshsameSQLsnapshot, no circularTOPselfqualification. Servicepassesowner/adminsettings bothprofileandTOP.3localehandbook READMEupdated. FullDockerPGRedis402passed73.95s/Ruff118/mypy38/build/schema/startup passed; fixedoldemptyTOPtest to asserttargetexclusionbecausefoundereligible. Live2.9.3polling/live rokfeleriss28TOPverified. Backup /tmp/safecheck-before-trusted-top-20261005.dump rollback safecheck-rollback-2-9-2. Older2.8.0-.2 rollbackcontainers/images andunusedcache retiredfordisk; allvolumes/currentrecent retained. Neverprintenv. Read RELEASE_2_9_3.md.

# Latest: SAFECheck 2.9.2 explicitly approved TOP only

UserrequiresTOPonlyadminconfirmedtrustedpeople. leaderboardrequiresTopVisibility.visibleTrue (no implicitREP enroll), nonnegative andnotactiveSCAM exactnumeric/currentunknownusername. Existingexplicitdecisionspreserved/noimplicitbackfill; REP/history untouched. TOP-derivedTRUSTEDonlyactualeligiblerank, manual/role independent. 3localeadminhandbookupdated. FullDockerPGRedis400passed73.13s/Ruff117/mypy38/build/schema/startup passed. Live2.9.2polling/liveTOPinvariantsverified. Head0008unchanged backup /tmp/safecheck-before-top-approval-20261005.dump rollback safecheck-rollback-2-9-1. Older stopped2.7.1-.3 rollbacks/images retired fordiskspace; allvolumes/currentrecentcontainerspreserved. Neverprintenv. Read RELEASE_2_9_2.md.

# Latest: SAFECheck 2.9.1 result controls private/groups

UserrequestedreplaceBackwithCancel on screenshotprofile andsamecontrols groups. Publicask/rep/profile buttonslookup/vote+/vote-/close3locales; closeactualdeletion/stateclear. ContextualadminregistrysearchBackretained. Notificationsno newcontrols. New6journeycasesprivate/groups3locales4commands. FullDockerPGRedis399passed68.69s afterobsoletegroupnobuttonassertionsupdated; Ruff116/mypy38/build/schema/startup passed. Live2.9.1pollingconfirmed head0008 unchanged. Backup /tmp/safecheck-before-result-controls-20261005.dump rollback safecheck-rollback-2-9-0. Neverprintenv. Read RELEASE_2_9_1.md.

# Latest: SAFECheck 2.9.0 founder/moderator roles

Userapproved founderrole forowner andmoderator forotheractiveDB/configadmins, automaticTRUSTED. _profile_user numericroleviafreshAdminService, trustedsource manual>role>top, activeSCAMoverrides. Publiccards /ask/rep/profile roleblock3locales. Revocationremovesrole/TRUSTEDunlessindependentmanual/TOP. ManualTRUSTEDremovalreceiptrole-specific. No schema/authpermissionchanges. FullDockerPGRedis393passed75.40s/Ruff115/mypy38/build/schema/startup passed. Live2.9.0polling +ownerroleTRUSTEDverified. Backup /tmp/safecheck-before-roles-20261005.dump rollback safecheck-rollback-2-8-4. Buildcacheprunedonlyunused recoveredspace; neverprintenv. Read RELEASE_2_9.md.

# Latest: SAFECheck 2.8.4 clean home copy

Userapproved new home caption with boldSAFECHECK divider/checkratings/reportfraud/actionprompt.3locales, photo/buttons unchanged.95focusedtests13.57s/Ruff112/mypy38/diff/build/schema/startup passed. Live2.8.4pollingverified; head0008 unchanged. Backup /tmp/safecheck-before-home-copy-20261005.dump rollback safecheck-rollback-2-8-3. Prioruserrequested removal owned111111 (6ones) emptyunknownplaceholder: referencecheckonlyOperationLock, privatebackup /tmp/safecheck-before-remove-owned-typo-20261005.dump, deletedtransactionally withaudit user_typo_removed actor28563234; realowned11111 knownID preserved. Neverprintenv.

# Latest: SAFECheck 2.8.3 TRUSTED disclaimer removed

Userexplicitrequest removegenerictrustworthinesswarning fromTRUSTED results. profile() warningonlyif notscam and nottrusted; bothmanual/TOP,3locales. Unknownidentitywarning andclear-resultdisclaimerretained.96focusedtests9.31s/Ruff111/mypy38/diff/build/alembiccheck/startup passed; previousfull387notrerun presentationonly. Live2.8.3, head0008 unchanged; privatebackup /tmp/safecheck-before-trusted-card-20261005.dump rollback safecheck-rollback-2-8-2. Neverprintenv. Also investigated owned11111: knownID vs owned111111 unknownID; different usernames, no bug.

# Latest: SAFECheck 2.8.2 automatic directory identity refresh

Live safecheck:2.8.2 polling_started confirmed; head0008 unchanged. Repository.users rows+count exclude username-only entry when current numeric observed User has same normalized username. No merge/delete/status transfer: historical judgments retained, old row can reappear if current username changes/removes. Same SCAM exclusions and TOP noIDs retained. Existing panels need reopening. Regression testsSQLite andPostgreSQL confirm repeatedobserve/currentID/crosssession/count/renames/reuse/TRUSTEDnoninheritance. FinalDockerPGRedis387passed74.24s no skips; Ruff111/mypy38/pipcheck/sourcepatternscan/diff/build/alembiccheck/startup passed. Privatebackup /tmp/safecheck-before-directory-20261005.dump; stoppedrollback safecheck-rollback-2-8-1. Credentials /tmp/safecheck-live.env neverprint. Read RELEASE_2_8_2.md.

# Latest: SAFECheck 2.8.1 ID labels, TOP excluded

Live safecheck:2.8.1, head0008 unchanged. Usercorrectedscope: TOP10 noIDs unchanged. Sharedidentity p.telegram_id_label/p.identity shows numericTelegramID orlocalizedunknown (LT ID nežinomas/EN ID unknown/RU ID неизвестен). Appliedask/rep/profilecards/SCAMuserslists/moderationidentities/adminreceipts/REPpreview. NeverDBprimaryIDfallback. Lists100UTF16bounds unchanged. FullDockerPGRedis384passed74.97s; Ruff110/mypy38/build/alembiccheck/startup passed. Privatebackup /tmp/safecheck-before-identity-labels-20261005.dump; rollback safecheck-rollback-2-8-0 stopped. Disk recovered11G by unusedoldimages+buildercache cleanup, no runningcontainer/volumes removed. Neverprintenv. Read RELEASE_2_8_1.md.

# Latest: SAFECheck 2.8.0 result cards

Live safecheck:2.8.0, head0008. LT/EN/RU fancylookupcard: sharedmathbrandheading, signedREPtotals, distinctclearSCAMmanualTRUSTED/TOPTRUSTED, IDlinked wording notdocumentverification, unknownusernamecaveat, guaranteesdisclaimed. SCAMsourcepriority; reportreasonshownescaped/directadminconfirmation; actualSCAM.id publicSC-00001 reference, storedcreateddate, manualdesignationupdatedate, nofakeTOPdate. Serviceonlynewreadmetadata trusted_updated_at. Restrainedtitle/divider updatesadmin/report/REP/stats/audit/users/SCAM/info/group; simpleprompts/errors/TOPtable/memberTXT/keyboardnavigationunchanged. PublicUnicodefields boundedUTF16 beforeHTMLescaping. FullDockerPGRedis381passed74.31s,30newtests; Ruff109/mypy38/build/alembiccheck/startup passed. Privatebackup /tmp/safecheck-before-result-cards-20261005.dump; rollback safecheck-rollback-2-7-4 stopped. Neverprintenv. Read RELEASE_2_8.md.

# Latest: SAFECheck 2.7.4 home banner

Live safecheck:2.7.4, head0008 unchanged. Useruploaded image bundled app/assets/home.jpg via setuptools package-data. Home/start/firstlanguagepicker/languageconfirm one SendPhoto with localizedcaption+buttons. Explicit home_photo screenparameter preservesoldpaneluntilsendsuccess; lifecycleclose/replacement unchanged. FullDockerPGRedis351passed74.87s,8newmedia tests3.57s and114existingtests12.85s; Ruff107/mypy38/build/nonrootassetread/alembiccheck/startup passed. Privatebackup /tmp/safecheck-before-home-photo-20261005.dump; rollback safecheck-rollback-2-7-3 stopped. No realmanualsend; user/start checksphone. Neverprintenv. Read RELEASE_2_7_4.md.

# Latest: SAFECheck 2.7.3 info and usage wording

Live safecheck:2.7.3, head0008 unchanged. Invalidadd_scusage no optionalreason sentence LT/EN/RU; Infocard includes +/-rep @username, adminapproval and /rep checking. Textonly: handlers/services/authunchanged, adminonlyadd_sc preserved.104focusedtests9.50s, Ruff104/mypy38/build/catalogassertions/alembiccheck/startup passed; previousfull343 notrerun textonly. Privatebackup /tmp/safecheck-before-info-20261005.dump; rollback safecheck-rollback-2-7-2 stopped. Neverprintenv. Read RELEASE_2_7_3.md.

# Latest: SAFECheck 2.7.2 SCAM presentation

Live safecheck:2.7.2, head0008 unchanged. Unknown-IDwarning omitted in groupadd_sc andscammers; privateaddreceipt andaskcaveatretained. ManualSCAM(no report_id) publicreason replaced by localized administratorconfirmation; originalDBnotes unchanged; reportlinkedreasons stilldisplayescaped. FullDockerPGRedis343passed66.45s; focused110passed10.65s; Ruff104/mypy38/build/alembiccheck/startup passed. Privatebackup /tmp/safecheck-before-scam-presentation-20261005.dump; rollback safecheck-rollback-2-7-1 stopped. Neverprintcredentials. Read RELEASE_2_7_2.md.

# Latest: SAFECheck 2.7.1 navigation

Live safecheck:2.7.1, head0008 unchanged. Group lookup/REP/notice buttons removed; cancel only drafts/confirmations, home explicitclose retained. Contextual admin back routes, REPqueue page retained, reportdecisionbackpending, registryorigin+searchpage preserved, adminremove targetback, adminhandbook/access return hierarchy, recovery back/cancel. New app/bot/navigation.py flow-aware domain-error controls; RecoveryFlow moved states with identical persisted names. FullDockerPGRedis342passed72.04s; Ruff102/mypy38/pipcheck/build/alembiccheck/startup passed. Private backup /tmp/safecheck-before-navigation-20261005.dump; stoppedrollback safecheck-rollback-2-7 can run without downgrading (samehead0008). Credentials /tmp/safecheck-live.env neverprint. Read RELEASE_2_7_1.md.

# Latest: SAFECheck 2.7.0 TRUSTED

Live safecheck:2.7.0, DBhead0008. Recovery menu private-only. /add_trusted and /del_trusted admin-only ID/username/reply; manual persistent, automatic actualTOP10 dynamic, activeSCAM precedence. Replays consumed without regrant and truthful response. Identity metadata refreshed under sharedlock before freshauth/SCAM. LT/EN/RU UI and adminhelp. FullDockerPGRedis322passed64.85s; Ruff99/mypy37/pipcheck/build/migration/startup passed; reviewapproved. All23oldtable fingerprints preserved; private backup /tmp/safecheck-before-trusted-20261005.dump. Stopped previous2.6container retained as safecheck-rollback-2-6; downgrade0007 required before restarting it. Credentials remain private in /tmp/safecheck-live.env; NEVER print file or container envmetadata. No real target grant for tests.

# Latest: SAFECheck 2.6.0 scam filtering and menus

Live safecheck:2.6.0; head0007 unchanged. ActiveSCAM excluded from general users100 and TXTmemberbackup, oldhistory retained, newactiveSCAMobservations skipbackup, bans preserved. Add_sc admin reasonoptional, targetwizard immediate; reports/del_sc/REP reasonrules retained. SCAMlist privateonlycmd+callbacks; homeadmin/groupentrybuttonsremoved, commands intact; info ask/report3locales. FullDockerPGRedis309passed54.12s; Ruff96/mypy37/pipcheck/build/alembiccheck/startup passed; reviewapproved. Runtimeenv reconstructed securely after /tmp reset from existingcontainer settings; PGRedis restarted. Testenv similarly restored privately from oldQAcontainers. Never print envvalues/metadata.

# Latest: SAFECheck 2.5.5 readable member export

Live safecheck:2.5.5. Owner group member export is readable UTF8 TXT, not JSON; names/usernames/IDs retained, display newline sanitation, short localized caption.24focusedtests/ruff/mypy/build/startup passed. No migration, DBhead0007.

# Latest: SAFECheck 2.5.4 monospace users

Live safecheck:2.5.4. User requested non-clickable usernames in code font, 👥 plus numbering,100rows/page. No migration.

# Latest: SAFECheck 2.5.3 user-row spacing

Live safecheck:2.5.3. Format 👥 1. username [ID/no ID], numbering and links retained, no middle dot.100perpage. No migration.

# Latest: SAFECheck 2.5.2 clickable numbered users

Live safecheck:2.5.2. Plain rows 👥 number. username · [ID/no ID], clickable Telegram links. Numbering global across100rows/pages. Adaptive label bounds.64focusedtests passed,ruff/mypy/build/startup. DBhead0007 unchanged.

# Latest: SAFECheck 2.5.1 users100

Live safecheck:2.5.1, head0007. User admin list100perpage compact username[ID]/no ID; display capped16UTF16 units, underlying data unchanged. Other pages5.67focusedtests/ruff/mypy/build/startup passed.

# Latest: SAFECheck 2.5.0 administrators and handbook

Read RELEASE_2_5.md. Live image safecheck:2.5.0; PostgreSQL head0007. Owner-only /admins numeric ID/reply, preview confirm grant/revoke, immutable owner, dynamic database permissions checked in all admin handlers/services; bootstrap ADMIN_IDS overridden by explicit DB revocation. /admin_help six sections in LT/EN/RU, admin-only private. Slash menus still four commands. Full Docker PG/Redis suite285 passed51.30s; Ruff87files, mypy37, pipcheck/build/migration/startup passed; independent review approved. All21oldtable fingerprints preserved across0007. Private backup /tmp/safecheck-before-administrators-20261004.dump. Tests inside Docker use /tmp/safecheck-resume-client.env on safecheck-qa network; host does not resolve Docker service names. Docker build cache pruned afterdiskfull; old safecheck2.4.2 rollback retained. Never print runtimeenv values.

# Latest: SAFECheck 2.4.2 group start home

Live image safecheck:2.4.2. Group /start now opens standard home menu; private /groups owner approval preserved. 69 targeted Telegram/group tests passed plus Ruff/format/mypy/build/startup. No migration. See RELEASE_2_4_2.md.

# Latest: SAFECheck 2.4.1 command menu

Live image safecheck:2.4.1. Slash menus contain only start, ask, rep, report in every locale and private admin scope. Other handlers remain available manually. Focused command/catalog/keyboard/callback suite: 29 passed; Ruff, format, mypy, Docker build and startup check passed. Database unchanged at head0006.

# Latest: SAFECheck 2.4.0 closedgroup ownership

Read RELEASE_2_4.md first. Live image safecheck:2.4.0, PostgreSQL head0006, owner configured via GROUP_OWNER_ID in private runtimeenv. Onlyowner stages/approves/manages/exports/queuesrecovery. Newgroup pending until freshTelegramrights checked approval; existinggroup approvals preserved. 252 realPGRedis tests passed, qualitychecks32mypyfiles/build/migration/readiness passed. All21oldtable row fingerprints preserved acrossmigration. Private backup /tmp/safecheck-before-closed-groups-20261004.dump.

# Continuation — SAFECheck 2.3.0, 2026-10-04

Read RELEASE_2_3.md and README.md first. Complete per-user LT/EN/RU localization deployed in safecheck-live-test image safecheck:2.3.0, PostgreSQL head0005. User.language nullable (old/new unset users see chooser on /start), language:lt/en/ru callbackvalidated, mainmenu language and /language, /help. App/i18n context and catalogs193keysperlanguage. Groupresponsesactorlocale, recoveryworkerrecipientlocale, globalerrorlocaleindependent. Business records remain shared and untranslated. FullPGRedis235pass; Ruff/format/mypy31/pipcheck/build passed. Backup beforei18n privately /tmp; all21tablesoldrowfingerprintsunchanged acrossmigration. Live startup+pollverified. Phone acceptance pending.

Prior2.2.3 panels appear at bottom by sending/trackingnew anddeletingold, cancel closes only. 2.2.2 /del_sc allowedgroupswithnumericadmin, TOP10alignedtable. 2.2.1 /add_sc groups username/numeric supported; unknownusernameID cannotban. Group testbasic group require supergroup for permanent/preemptive guarantees. GlobalREPreset2.1 historypreserved.

Never print private runtime env or Docker environment metadata/backups. Runtime env path /tmp/safecheck-live.env; tests use /tmp/safecheck-resume-client.env with TEST_POSTGRES_URL isolatedschemas safecheck_test, not live safecheck_resume. Docker clientdefaultreachableproxy and CAsecret/mountrequired. No bulk scraping/inviting. OriginalV1legacy inactivearchive neverexecuted.

# Continuation — SAFECheck 2.2, 2026-10-04

Read RELEASE_2_2.md and README.md. Group stage completed and deployed: safecheck-live-test image safecheck:2.2 on safecheck-qa; PostgreSQL head0004, Redis persistent FSM. Existing test group verified via live Telegram and registered; it is a BASIC group (permanent/preemptive protection needs supergroup). Admin getMyCommands confirmed add_sc present. Full isolated PostgreSQL/Redis suite166pass, quality checks25mypyfiles, build/migration/readiness/Telegrampoll passed. See report for limitations and remaining real-user acceptance.

Do not print private runtime env files, Docker environment metadata or database backups. Private backup before2.2 in /tmp outside repo. Docker client defaults supply reachable proxy; do not replace with outer-only proxy hostname. Public CA mount/SSL_CERT_FILE preserve certificate verification.

Prior owner-requested global REP reset completed in2.1 and history preserved. Future tests must use safecheck_test isolated schemas, never live safecheck_resume. Group jobs can actually ban confirmed known numeric SCAM users in registered groups; recovery messages require explicit consent+adminconfirmation. No bulk scraping/inviting integration.
