# AUTO ADS 3.4 — customer content capabilities

Base: audited PR #37, remote commit
`34f975a5922ee7dc69f381b4aa1983e71cda6974`. This is a stacked change,
not a production rollout. No advertisements are activated by this migration.

## Authority and data

`ad_customer_grants` retains numeric customer assignments. Its partial unique
index permits only one ACTIVE assignment per campaign. Replacement revokes the
previous grant and its links before inserting a new grant in the same transaction.
Reassigning the same numeric person creates a new grant identity; old drafts do
not regain authority. The legacy nullable campaign client metadata is deliberately
not imported as an authorization grant.

`ad_customer_drafts` binds the existing durable content draft to its grant,
campaign and numeric customer. Foreign keys and a composite grant identity key
reject invalid bindings. `ad_customer_links` contains only SHA-256 token hashes,
current/revoked state and creation/revocation actors and instants. There is no
cascade deletion of content, assignments, schedules or publication history.

Global administrators retain the existing shared campaign authority. Customer
capabilities are a separate service: TEXT/MEDIA/BUTTONS only. Creating, archiving,
restoring, granting access, managing groups, assignment policies and publication
schedules remain administrative operations. Existing `AdsService.authorize` and
publication authorization are not relaxed. Customers have no database credentials;
SQL constraints supplement application authorization, not PostgreSQL row-level
security against a privileged direct SQL operator.

Every content mutation rechecks the numeric actor and active grant under the
existing exclusive identity/ACL boundary shared by administrative revocation.
The shared 3.1 editor checks optimistic content revision, validates native
entities/UTF-16, media and buttons, requires successful full native preview, then
commits a new immutable revision and audit event. The numeric version author and
creation time remain in the existing version history. Grant binding identifies
the customer capability used by saved drafts. Administrative restore still creates
a new revision and requires preview; customers can read history but cannot restore.

Only campaign content/version/update metadata changes. No content or grant method
writes `ad_schedules`, publication attempts, rate gates or assignment policy.
Already pinned attempts retain their original version. Publication authorization
continues to use the administrator recorded on the schedule, not the customer
who authored content. No automatic editing of sent Telegram messages occurs.

## Links and time boundaries

Tokens use `secrets.token_urlsafe(32)` (256 random bits, 43 URL-safe characters).
The complete `ad_` start parameter is 46 characters. A deep link only selects a
campaign; a current numeric-ID grant is always required. Unknown, malformed,
foreign, revoked and archived links return the same access-denied response.
There is no automatic token lifetime. Rotation/revocation is committed to SQL;
restart cannot restore an old token. Grant replacement invalidates its links.
Archival invalidates link resolution even though historical link rows are retained.

Only a hash is stored. A newly generated URL is displayed ONCE in an authorized
administrator's private panel, with native `CopyTextButton` and selectable URL
text for manual copying on older clients. It is not put into FSM, audit events,
callback data, logs or persistent plaintext storage. Later management shows the
one-time rule and explicit regeneration/revocation controls; it cannot reconstruct
or offer to copy a hash as if it were a working URL. A lost response after creation
requires reopening the management panel and explicitly regenerating. A repeated
stale create/rotate callback cannot silently rotate another link.

Four independent concepts are preserved:

1. Individual group publication start/end dates continue to control sending.
2. `editing_until` is an optional, explicit customer editing deadline stored in UTC.
   Admin input requires ISO 8601 with an offset, or `-` for no deadline.
3. Token validity persists until explicit revocation/rotation, replacement or archive.
4. Revoked grants deny all customer reads and writes, including `/myads` and old buttons.

Editing deadline equality is expired. Expired editing rights or an archived
campaign allow an active customer to view permitted campaign information/history/
statistics but not save, stage or append edits. An expired editing deadline does
not itself revoke the selector link. Individual publication deadlines do not infer
an ambiguous campaign-wide editing date: campaigns can have different group end
instants. Content edits cannot extend, restart or reactivate any publication,
including already expired schedules. Administrators may explicitly set the separate
editing deadline. Archived assignments can still be revoked administratively.

## Native UI

All new text uses the existing LT/EN/RU catalogs. Existing callback IDs remain;
`ac4`, `aca4` add customer content and administrator grant/link routes. Existing
preview callbacks retain their prefix, with a separate customer source marker.
Private chat/actor binding is mandatory. `/start ad_TOKEN` is consumed before the
ordinary language/home screen without retaining its payload in FSM. A new user
can open the campaign immediately in the current/default language.

Assigned users get a private **My ads** entry and `/myads`, an eight-row paginated
list, their own content panel, preview, actual aggregate publication statistics,
version history and recoverable SQL drafts. Internal administrative names are not
exposed to customers. Existing panels are edited; preview advertisements are
separate messages as required for genuine Telegram rendering. Every callback
and FSM input revalidates scope; FSM is never an authorization source. The input
context pins the grant identity before staging text/buttons, so replacement during
input cannot migrate an old edit into a new assignment.

Both standard and GLOBAL ADS campaign panels expose customer and link management.
Admin changes use versioned grant stamps. Revoke/rotate actions require native
confirmation. Access history is paginated and localized; audit records retain the
real actor. No URL/token is audited. No new payment, Mini App or AI feature exists.

## Migration and review

0016 follows 0015 and only creates three tables/indexes; no old migration/table
is rewritten. Downgrade refuses nonempty customer tables rather than dropping
access/history data. Review a forward rollback or backup recovery after adoption.
Existing SQLite/PostgreSQL migration tests compare legacy rows. Direct older DDL
migration tests first remove the empty 0016 extension so retained FKs do not mask
the historical migration being tested.

See `AUTO_ADS_3_4_FINAL_AUDIT.md` for validation evidence and residual risks.
Deployment, real Telegram entitlement/Custom Emoji compatibility and staging/live
trials require separate approval. This PR neither merges dependencies nor touches
VPS configuration or production data.
