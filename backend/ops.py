"""Operator-only encrypted backups and quarantined restore candidates.

No network, key printing, existing database replacement or automatic replay.
Fernet authenticates the entire archive before any SQLite/ZIP parsing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from cryptography.fernet import Fernet, InvalidToken
from .runtime import DatabaseLease

MAX_SNAPSHOT = 256 * 1024 * 1024
MAX_ARCHIVE = MAX_SNAPSHOT + 65536
MAX_ENCRYPTED = (MAX_ARCHIVE + 128) * 4 // 3 + 4096
REQUIRED = {'users','jobs','actions','connections','entitlement_periods','credit_ledger','usage_ledger','media_assets','media_blobs','content_posts'}


def secret():
    try: return Fernet(os.environ['BACKUP_ENCRYPTION_KEY'].encode())
    except (KeyError, ValueError): raise ValueError('Set a valid BACKUP_ENCRYPTION_KEY; do not pass keys on the command line.') from None


def sha(path):
    with open(path,'rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def inspect_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]: raise ValueError('Database integrity check failed.')
        if conn.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('Database references are inconsistent.')
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED.issubset(tables): raise ValueError('Not a supported Isdas database snapshot.')
        # Only counts and schema names, never customer rows or credential values.
        counted=REQUIRED | ({'documents','document_blobs','document_chunks','job_document_sources'} & tables)
        counts={name:conn.execute('SELECT count(*) FROM "'+name+'"').fetchone()[0] for name in sorted(counted)}
    return {'sha256':sha(path),'bytes':Path(path).stat().st_size,'counts':counts}


def private_temp(parent):
    parent=Path(parent);parent.mkdir(parents=True,exist_ok=True)
    return tempfile.TemporaryDirectory(prefix='.isdas-backup-',dir=parent)


def publish_new(source,target):
    """Exclusive destination: never replace an existing backup or database."""
    os.chmod(source,0o600)
    with open(source,'r+b') as ready:os.fsync(ready.fileno())
    # Temp and destination share a local filesystem. Link only a complete file.
    os.link(source,target)


def backup(database,target):
    database=Path(database).resolve();target=Path(target).resolve();cipher=secret()
    if not database.is_file(): raise ValueError('Source database does not exist.')
    if target.exists(): raise ValueError('Backup target already exists.')
    with private_temp(target.parent) as folder:
        temp=Path(folder);snapshot=temp/'database.sqlite3';deadline=time.monotonic()+120
        def progress(status,remaining,total):
            if time.monotonic()>deadline:raise TimeoutError('Database snapshot timed out; no backup published.')
            if snapshot.exists() and snapshot.stat().st_size>MAX_SNAPSHOT:raise ValueError('Snapshot exceeds the 256 MiB pilot backup limit.')
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(snapshot)) as dest:
            source.backup(dest,pages=128,progress=progress,sleep=.1)
        if snapshot.stat().st_size>MAX_SNAPSHOT:raise ValueError('Snapshot exceeds the 256 MiB pilot backup limit.')
        manifest={'format':1,'created_at':time.time(),**inspect_database(snapshot)}
        archive=temp/'backup.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as z:
            z.write(snapshot,'database.sqlite3');z.writestr('manifest.json',json.dumps(manifest))
        if archive.stat().st_size>MAX_ARCHIVE:raise ValueError('Backup archive exceeds its limit.')
        sealed=temp/'sealed';sealed.write_bytes(cipher.encrypt(archive.read_bytes()))
        publish_new(sealed,target)
    return {'backup':str(target),'encrypted':True,**manifest}


def unpack(backup_path,temp):
    backup_path=Path(backup_path)
    if not backup_path.is_file() or backup_path.stat().st_size>MAX_ENCRYPTED:raise ValueError('Backup missing or above the pilot size limit.')
    try: raw=secret().decrypt(backup_path.read_bytes())
    except InvalidToken:raise ValueError('Backup authentication failed. Check the key and file.') from None
    if len(raw)>MAX_ARCHIVE:raise ValueError('Backup archive too large.')
    archive=temp/'archive.zip';archive.write_bytes(raw);del raw
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist()
        if len(entries)!=2 or {e.filename for e in entries}!={'database.sqlite3','manifest.json'}:raise ValueError('Unexpected backup contents.')
        if any(e.compress_type!=zipfile.ZIP_STORED for e in entries):raise ValueError('Unexpected archive compression.')
        if z.getinfo('database.sqlite3').file_size>MAX_SNAPSHOT or z.getinfo('manifest.json').file_size>32768:raise ValueError('Backup contents exceed limits.')
        manifest=json.loads(z.read('manifest.json'))
        snapshot=temp/'database.sqlite3'
        with z.open('database.sqlite3') as inp,open(snapshot,'wb') as output:shutil.copyfileobj(inp,output)
    measured=inspect_database(snapshot)
    if manifest.get('format')!=1 or any(manifest.get(k)!=v for k,v in measured.items()):raise ValueError('Backup manifest mismatch.')
    return snapshot,manifest


def verify(backup_path):
    with private_temp(Path(backup_path).resolve().parent) as folder:
        _,manifest=unpack(backup_path,Path(folder))
    return {'verified':True,**manifest}


def quarantine(snapshot,manifest):
    """Restored data cannot know effects/payments that occurred after its timestamp."""
    with closing(sqlite3.connect(snapshot)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        with conn:
            conn.execute('CREATE TABLE IF NOT EXISTS system_state(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            conn.execute("INSERT OR REPLACE INTO system_state VALUES('recovery_hold',?)",(json.dumps({'restored_at':time.time(),'snapshot_at':manifest['created_at'],'snapshot_sha256':manifest['sha256']}),))
            conn.execute('DELETE FROM sessions');conn.execute('DELETE FROM oauth_states')
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'account_tokens' in tables:conn.execute('DELETE FROM account_tokens')
            if 'membership_invites' in tables:conn.execute("UPDATE membership_invites SET state='revoked',updated=? WHERE state='pending'",(time.time(),))
            if 'client_reviews' in tables:
                review_posts="SELECT post_id FROM client_reviews WHERE status IN ('pending','approved')"
                conn.execute("UPDATE content_posts SET status='draft',revision=revision+1,approved_by=NULL,approved_at=NULL,updated=? WHERE id IN ("+review_posts+")",(time.time(),))
                conn.execute("INSERT INTO content_versions SELECT id,revision,payload,status,'operator_restore',? FROM content_posts WHERE id IN ("+review_posts+")",(time.time(),))
                conn.execute("UPDATE client_reviews SET status='stale',updated=? WHERE status IN ('pending','approved')",(time.time(),))
            if 'mail_outbox' in tables:conn.execute("UPDATE mail_outbox SET state='cancelled',payload='',updated=? WHERE state IN ('queued','sending')",(time.time(),))
            conn.execute('UPDATE routines SET enabled=0')
            conn.execute("UPDATE jobs SET status='interrupted',cancel_requested=1,stage='Yedekten dönen iş; yeniden çalıştırılmadı',updated=? WHERE status IN ('queued','running')",(time.time(),))
            conn.execute("UPDATE usage_ledger SET state='uncertain',note='Yedekten dönüş: sağlayıcı faturasıyla eşleştirme gerekli.',updated=? WHERE state='reserved'",(time.time(),))
            conn.execute("UPDATE credit_ledger SET state='uncertain',updated=? WHERE state='reserved'",(time.time(),))
            conn.execute("UPDATE actions SET status=CASE WHEN status='executing' THEN 'uncertain' ELSE 'invalidated' END,updated=? WHERE status IN ('pending','executing','failed')",(time.time(),))
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.execute('PRAGMA journal_mode=DELETE')


def restore_candidate(backup_path,target):
    target=Path(target).resolve()
    if any(Path(str(target)+suffix).exists() for suffix in ('','-wal','-shm')):raise ValueError('Restore requires a new database path with no existing sidecars.')
    with private_temp(target.parent) as folder:
        snapshot,manifest=unpack(backup_path,Path(folder))
        quarantine(snapshot,manifest);inspect_database(snapshot)
        with DatabaseLease(target):publish_new(snapshot,target)
    return {'database':str(target),'recovery_hold':True,'snapshot_at':manifest['created_at'],'automatic_jobs_enabled':False}


def release_hold(database,evidence):
    if not 20<=len(evidence.strip())<=2000:raise ValueError('Provide the reconciliation record reference (20–2000 characters).')
    database=Path(database).resolve()
    if not database.is_file():raise ValueError('Database does not exist.')
    # Stop the production service first. CLI is never exposed through the API.
    with DatabaseLease(database),closing(sqlite3.connect(database)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute("SELECT value FROM system_state WHERE key='recovery_hold'").fetchone()
        if not row:raise ValueError('No recovery hold exists.')
        if conn.execute("SELECT 1 FROM usage_ledger WHERE state IN ('reserved','uncertain') LIMIT 1").fetchone() or conn.execute("SELECT 1 FROM credit_ledger WHERE state IN ('reserved','uncertain') LIMIT 1").fetchone():
            raise ValueError('Reconcile all unresolved provider/customer receipts before releasing the hold.')
        connections=conn.execute('SELECT encrypted_token FROM connections').fetchall()
        if connections:
            try:
                cipher=Fernet(os.environ['ENCRYPTION_KEY'].encode())
                for row_token in connections:cipher.decrypt(row_token[0].encode())
            except (KeyError,ValueError,InvalidToken):raise ValueError('The original ENCRYPTION_KEY is required for the restored connections.') from None
        import uuid
        conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,None,'operator','recovery.released',json.dumps({'evidence':evidence,'restore':json.loads(row[0])}),time.time()))
        conn.execute("DELETE FROM system_state WHERE key='recovery_hold'");conn.commit()
    return {'recovery_hold':False,'routines_remain_disabled':True}


def reconcile_missing_receipt(database,job_id,cost_usd,period,provider,request_id,evidence):
    """A queued job at snapshot time may have run before the incident."""
    import datetime,re,uuid
    from . import usage,billing
    if len(evidence.strip())<20 or provider not in {'xai','hermes','no_request'}:raise ValueError('Verified billing evidence and provider required.')
    if not re.fullmatch(r'\d{4}-\d{2}',period):raise ValueError('Billing month must be YYYY-MM.')
    datetime.datetime.strptime(period,'%Y-%m')
    ticks=usage.dollars_to_ticks(cost_usd)
    if provider=='no_request' and ticks!=0:raise ValueError('A verified unstarted request cannot have a charge.')
    if provider!='no_request' and not request_id.strip():raise ValueError('Provider receipt/request reference required.')
    database=Path(database).resolve()
    if not database.is_file():raise ValueError('Database does not exist.')
    with DatabaseLease(database),closing(sqlite3.connect(database)) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        if not conn.execute("SELECT 1 FROM system_state WHERE key='recovery_hold'").fetchone():raise ValueError('Only quarantined restores support this operation.')
        credit=conn.execute("SELECT * FROM credit_ledger WHERE job_id=? AND state='uncertain'",(job_id,)).fetchone()
        if not credit or conn.execute('SELECT 1 FROM usage_ledger WHERE job_id=?',(job_id,)).fetchone():raise ValueError('Use normal usage reconciliation for an existing provider receipt.')
        conn.execute('INSERT INTO usage_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?)',(job_id,credit['workspace_id'],period,credit['reserved_milli']*credit['ticks_per_milli'],ticks,'released' if provider=='no_request' else 'settled',provider,request_id[:250],evidence[:500],time.time(),time.time()))
        billing.settle(conn,job_id,ticks,no_request=provider=='no_request')
        conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,credit['workspace_id'],'operator','recovery.receipt_reconstructed',json.dumps({'job_id':job_id,'period':period,'provider':provider,'ticks':ticks,'evidence':evidence[:500]}),time.time()));conn.commit()
    return {'job_id':job_id,'reconciled':True}


def main():
    from dotenv import load_dotenv
    load_dotenv()
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    create=commands.add_parser('backup');create.add_argument('--database',default=os.getenv('DATABASE_PATH','data/isdas.sqlite3'));create.add_argument('--output',required=True)
    check=commands.add_parser('verify');check.add_argument('--backup',required=True)
    restore=commands.add_parser('restore-candidate');restore.add_argument('--backup',required=True);restore.add_argument('--output',required=True)
    release=commands.add_parser('release-hold');release.add_argument('--database',required=True);release.add_argument('--evidence',required=True)
    missing=commands.add_parser('reconcile-missing-receipt');missing.add_argument('--database',required=True);missing.add_argument('--job',required=True);missing.add_argument('--cost-usd',required=True);missing.add_argument('--period',required=True);missing.add_argument('--provider',required=True,choices=['xai','hermes','no_request']);missing.add_argument('--request-id',default='');missing.add_argument('--evidence',required=True)
    args=parser.parse_args()
    try:
        if args.command=='backup':result=backup(args.database,args.output)
        elif args.command=='verify':result=verify(args.backup)
        elif args.command=='restore-candidate':result=restore_candidate(args.backup,args.output)
        elif args.command=='release-hold':result=release_hold(args.database,args.evidence)
        else:result=reconcile_missing_receipt(args.database,args.job,args.cost_usd,args.period,args.provider,args.request_id,args.evidence)
        print(json.dumps(result,ensure_ascii=False))
    except (ValueError,OSError,sqlite3.Error,zipfile.BadZipFile,RuntimeError) as exc:
        parser.exit(1,type(exc).__name__+': '+str(exc)+'\n')

if __name__=='__main__':main()
