BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';
DO $review$
DECLARE
  attempt ad_publication_attempts%ROWTYPE;
  sched ad_schedules%ROWTYPE;
  part ad_publication_parts%ROWTYPE;
  evidence json;
BEGIN
  PERFORM id FROM identity_lock WHERE id=1 FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Missing ADS ACL lock'; END IF;
  PERFORM id FROM ad_campaigns WHERE id=2 AND internal_name='pranashas' FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'Campaign identity changed'; END IF;
  SELECT * INTO STRICT sched FROM ad_schedules WHERE id=5 FOR UPDATE;
  SELECT * INTO STRICT attempt FROM ad_publication_attempts WHERE id=79 FOR UPDATE;
  IF sched.campaign_id IS DISTINCT FROM 2 OR sched.chat_id IS DISTINCT FROM -1004395482317 OR sched.version IS DISTINCT FROM 6 OR sched.status IS DISTINCT FROM 'PAUSED' OR sched.next_run_at IS NOT NULL THEN
    RAISE EXCEPTION 'Schedule changed; manual review required';
  END IF;
  IF attempt.status='CANCELLED' AND attempt.error='HUMAN_REVIEW_NOT_OBSERVED' AND EXISTS(
    SELECT 1 FROM audit_events WHERE action='ads_publication_manual_closed' AND details->>'attempt_id'='79' AND details->>'review_key'='owner-20261022-ads79-not-observed'
  ) THEN
    RAISE NOTICE 'ADS79_MANUAL_CLOSE_ALREADY_RECORDED';
    RETURN;
  END IF;
  IF attempt.status IS DISTINCT FROM 'UNCERTAIN' OR attempt.error IS DISTINCT FROM 'TelegramNetworkError' OR attempt.schedule_id IS DISTINCT FROM 5 OR attempt.content_version_id IS DISTINCT FROM 4 OR attempt.chat_id IS DISTINCT FROM -1004395482317 OR attempt.schedule_version IS DISTINCT FROM 6 OR attempt.fence IS DISTINCT FROM 1 OR attempt.due_at IS DISTINCT FROM '2026-10-22 16:19:00+00'::timestamptz OR attempt.lease_until IS NOT NULL OR attempt.retry_at IS NOT NULL OR attempt.completed_at IS NULL THEN
    RAISE EXCEPTION 'Attempt changed; manual review required';
  END IF;
  IF (SELECT count(*) FROM ad_publication_parts WHERE attempt_id=79) IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'Part set changed; manual review required';
  END IF;
  SELECT * INTO STRICT part FROM ad_publication_parts WHERE attempt_id=79 FOR UPDATE;
  IF part.id IS DISTINCT FROM 79 OR part.position IS DISTINCT FROM 0 OR part.kind IS DISTINCT FROM 'PHOTO' OR part.status IS DISTINCT FROM 'UNCERTAIN' OR part.tries IS DISTINCT FROM 1 OR part.error IS DISTINCT FROM 'TelegramNetworkError' OR part.message_ids::jsonb IS DISTINCT FROM '[]'::jsonb THEN
    RAISE EXCEPTION 'Part evidence changed; manual review required';
  END IF;
  evidence := json_build_object(
    'review_key','owner-20261022-ads79-not-observed',
    'attempt_id',79,'campaign_id',2,'schedule_id',5,'chat_id',attempt.chat_id,
    'content_version_id',attempt.content_version_id,'fence',attempt.fence,
    'previous_status',attempt.status,'previous_error',attempt.error,
    'previous_completed_at',attempt.completed_at,'part_id',part.id,
    'part_status',part.status,'message_ids',part.message_ids,
    'resolution','CANCELLED','delivery_outcome','UNKNOWN',
    'evidence_source','project owner checked Telegram group',
    'owner_report','nepasirode','automatic_retry',false,'schedule_remains','PAUSED'
  );
  INSERT INTO audit_events(actor_id,action,target_id,details,created_at)
    VALUES(0,'ads_publication_manual_closed',NULL,evidence,CURRENT_TIMESTAMP);
  UPDATE ad_publication_attempts SET status='CANCELLED',error='HUMAN_REVIEW_NOT_OBSERVED'
    WHERE id=79 AND status='UNCERTAIN' AND fence=1;
  IF NOT FOUND THEN RAISE EXCEPTION 'Attempt fence changed'; END IF;
  -- Keep the PHOTO part UNCERTAIN and its original error/receipts untouched.
  -- This is a human closure, never a claim of a Telegram-confirmed failure.
  RAISE NOTICE 'ADS79_MANUAL_CLOSED_NO_RETRY';
END
$review$;
COMMIT;
