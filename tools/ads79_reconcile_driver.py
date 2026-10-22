"""One-shot human closure; never send/retry, keep unknown delivery evidence."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

root = Path(sys.argv[1])
sql = Path(sys.argv[3]).read_bytes()
expected_sql_sha = sys.argv[2]
if hashlib.sha256(sql).hexdigest() != expected_sql_sha:
    raise SystemExit('Reconciliation SQL checksum mismatch')
spec = importlib.util.spec_from_file_location('release_probe', root / 'deploy/production_release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
release = module.Release(root, root.name)
with release.original.lock():
    # Private full backup precedes the only database mutation.
    backup_dir = Path('/var/backups/safecheck') / ('manual-ads79-' + uuid4().hex)
    backup_dir.mkdir(mode=0o700)
    archive = backup_dir / 'before.dump'
    with os.fdopen(os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
        subprocess.run(['docker','exec','safecheck-postgres-1','pg_dump','-U','safecheck','-d','safecheck','-Fc'], stdout=handle, stderr=subprocess.PIPE, check=True, timeout=120)
    if archive.stat().st_size < 1024:
        raise RuntimeError('Backup is unexpectedly small')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = backup_dir / 'before.dump.sha256'
    checksum.write_text(digest + '  before.dump\n')
    checksum.chmod(0o600)
    with archive.open('rb') as handle:
        subprocess.run(['docker','exec','-i','safecheck-postgres-1','pg_restore','--list'], stdin=handle, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=120)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Backup changed')
    print('ManualReviewBackup=PASS', flush=True)
    subprocess.run(['docker','exec','-i','safecheck-postgres-1','psql','-X','-q','-U','safecheck','-d','safecheck','-v','ON_ERROR_STOP=1','-f','-'], input=sql, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30)
    record = {'attempt_id':79,'resolution':'CANCELLED','delivery_outcome':'UNKNOWN','owner_report':'nepasirode','retry':False,'source_sha':root.name,'sql_sha256':expected_sql_sha,'backup_sha256':digest,'recorded_at':datetime.now(UTC).isoformat()}
    receipt = backup_dir / 'review.json'
    receipt.write_text(json.dumps(record, sort_keys=True) + '\n')
    receipt.chmod(0o600)
    print('ADS79ManualReview=' + json.dumps(record, sort_keys=True), flush=True)
    release.preserve_services()
    release.idle()
    print('ProductionReleaseIdle=PASS', flush=True)
