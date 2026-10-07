"""Deployment boundaries and authenticated restore drills; no live provider."""
import json,os,sqlite3,subprocess,sys
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from test_api import clients,create_space
from backend import db,ops,runtime,worker,billing


@pytest.fixture
def backup_space(clients,monkeypatch,tmp_path):
    a,b=clients;wid=create_space(a)
    monkeypatch.setenv('BACKUP_ENCRYPTION_KEY',Fernet.generate_key().decode())
    a.post(f'/api/workspaces/{wid}/memories',json={'title':'Özel test verisi','content':'Özel müşteri bilgisi — yalnızca test'})
    return a,wid,tmp_path


def test_database_context_commits_rolls_back_and_releases_file_handle(backup_space):
    with db.connection() as committed:
        committed.execute("INSERT INTO system_state VALUES('commit-test','kept')")
    assert db.query("SELECT value FROM system_state WHERE key='commit-test'",one=True)['value']=='kept'
    with pytest.raises(sqlite3.ProgrammingError):committed.execute('SELECT 1')
    with pytest.raises(RuntimeError):
        with db.connection() as rolled_back:
            rolled_back.execute("INSERT INTO system_state VALUES('rollback-test','discarded')")
            raise RuntimeError('Synthetic transaction failure')
    assert db.query("SELECT value FROM system_state WHERE key='rollback-test'",one=True) is None
    with pytest.raises(sqlite3.ProgrammingError):rolled_back.execute('SELECT 1')


def test_encrypted_snapshot_preserves_live_wal_and_does_not_touch_source(backup_space):
    a,wid,folder=backup_space;target=folder/'daily.enc'
    import io
    from PIL import Image
    image=io.BytesIO();Image.new('RGB',(32,32),'blue').save(image,format='PNG')
    asset=a.post(f'/api/workspaces/{wid}/media',files={'file':('test.png',image.getvalue(),'image/png')}).json()
    assert asset['id']
    # Keep a reader/writer handle open so data can remain in a live WAL.
    conn=db.connection()
    conn.execute('INSERT INTO system_state VALUES(?,?)',('test-live-wal','committed'));conn.commit()
    result=ops.backup(db.DB_PATH,target)
    assert result['encrypted'] and result['counts']['users']==2
    raw=target.read_bytes();assert b'SQLite format' not in raw and 'Özel müşteri'.encode() not in raw
    assert ops.verify(target)['verified']
    restore=folder/'restored.sqlite3';ops.restore_candidate(target,restore)
    with sqlite3.connect(restore) as restored:
        assert restored.execute("SELECT value FROM system_state WHERE key='test-live-wal'").fetchone()==('committed',)
        assert restored.execute('SELECT content FROM memories').fetchone()[0]=='Özel müşteri bilgisi — yalnızca test'
        assert restored.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
        assert restored.execute('SELECT data FROM media_blobs WHERE asset_id=?',(asset['id'],)).fetchone()[0]==a.get(f"/api/workspaces/{wid}/media/{asset['id']}").content
        assert runtime.recovery_hold(restored)
    assert a.get('/api/me').status_code==200
    assert not runtime.is_held()
    conn.close()


def test_restore_invalidates_old_approvals_and_reconciles_positive_lost_receipt(backup_space,monkeypatch):
    a,wid,folder=backup_space;monkeypatch.setenv('XAI_API_KEY','synthetic-restore-test')
    for status in ['pending','executing','failed','completed']:
        db.query('INSERT INTO actions(id,workspace_id,kind,payload,status,created,updated) VALUES(?,?,?,?,?,?,?)',(db.uid(),wid,'google.gmail.draft','{}',status,db.now(),db.now()))
    job=a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Kayıp makbuz testi','idempotency_key':'positive-lost-receipt'}).json()
    target=folder/'positive.enc';restored=folder/'positive.sqlite3';ops.backup(db.DB_PATH,target);ops.restore_candidate(target,restored)
    ops.reconcile_missing_receipt(restored,job['id'],'0.04','2026-10','xai','test-request-id','TEST-125 confirmed provider receipt amount and month')
    with sqlite3.connect(restored) as c:
        assert sorted(r[0] for r in c.execute('SELECT status FROM actions'))==['completed','invalidated','invalidated','uncertain']
        assert c.execute('SELECT actual_ticks,period,state FROM usage_ledger').fetchone()==(400_000_000,'2026-10','settled')
        assert c.execute('SELECT charged_milli,state FROM credit_ledger').fetchone()==(4000,'settled')
    with pytest.raises(ValueError,match='normal usage'):ops.reconcile_missing_receipt(restored,job['id'],'0.04','2026-10','xai','test-request-id','TEST-125 confirmed provider receipt amount and month')


def test_bad_key_tampering_and_existing_destination_are_rejected(backup_space,monkeypatch):
    _,_,folder=backup_space;target=folder/'original.enc';ops.backup(db.DB_PATH,target)
    original=target.read_bytes();changed=folder/'tampered.enc';changed.write_bytes(original[:-5]+b'AAAAA')
    with pytest.raises(ValueError,match='authentication'):ops.verify(changed)
    monkeypatch.setenv('BACKUP_ENCRYPTION_KEY',Fernet.generate_key().decode())
    with pytest.raises(ValueError,match='authentication'):ops.restore_candidate(target,folder/'bad.sqlite3')
    assert not (folder/'bad.sqlite3').exists()
    with pytest.raises(ValueError,match='exists'):ops.backup(db.DB_PATH,target)
    with pytest.raises(ValueError,match='new database'):ops.restore_candidate(target,db.DB_PATH)
    assert target.read_bytes()==original


def test_snapshot_limit_fails_without_publishing(backup_space,monkeypatch):
    _,_,folder=backup_space;monkeypatch.setattr(ops,'MAX_SNAPSHOT',1024)
    with pytest.raises(ValueError,match='limit'):ops.backup(db.DB_PATH,folder/'oversized.enc')
    assert not (folder/'oversized.enc').exists()
    assert not list(folder.glob('.isdas-backup-*'))


def test_restored_queue_never_replays_and_unresolved_receipts_block_release(backup_space,monkeypatch):
    a,wid,folder=backup_space
    monkeypatch.setenv('XAI_API_KEY','synthetic-operations-test')
    j=a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Yedek alındığında sırada olan test işi','idempotency_key':'operations-restore-test'}).json()
    assert j.get('id')
    a.post(f'/api/workspaces/{wid}/routines',json={'prompt':'Sadece test rutini','interval_hours':24,'next_run':'2020-01-01T12:00:00+03:00'})
    target=folder/'queue.enc';ops.backup(db.DB_PATH,target)
    restore=folder/'queue-restored.sqlite3';ops.restore_candidate(target,restore)
    with sqlite3.connect(restore) as conn:
        assert conn.execute('SELECT status FROM jobs').fetchone()[0]=='interrupted'
        assert conn.execute('SELECT enabled FROM routines').fetchone()[0]==0
        assert conn.execute('SELECT state FROM credit_ledger').fetchone()[0]=='uncertain'
    with pytest.raises(ValueError,match='unresolved'):ops.release_hold(restore,'Provider and payment evidence checked in incident TEST-123')
    # Zero is explicit evidence of no request, never inferred from a missing row.
    ops.reconcile_missing_receipt(restore,j['id'],'0','2026-10','no_request','','TEST-123 verified unstarted request in provider billing')
    assert ops.release_hold(restore,'TEST-123 all provider, payment and post-snapshot activity reconciled')['recovery_hold'] is False
    with sqlite3.connect(restore) as conn:
        assert conn.execute('SELECT state FROM credit_ledger').fetchone()[0]=='released'
        assert conn.execute('SELECT enabled FROM routines').fetchone()[0]==0


def test_recovery_hold_allows_reading_and_login_but_no_mutations_or_worker(backup_space,monkeypatch):
    a,wid,_=backup_space;monkeypatch.setenv('XAI_API_KEY','synthetic-hold-test')
    a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Bakım sırasında çalışmaması gereken iş','idempotency_key':'operations-held-job'})
    db.query("INSERT INTO system_state VALUES('recovery_hold','{}')")
    assert a.get('/api/health').status_code==200 and a.get('/api/ready').status_code==503
    assert a.get(f'/api/workspaces/{wid}/memories').status_code==200
    assert a.post(f'/api/workspaces/{wid}/memories',json={'title':'Yeni','content':'Olmamalı'}).status_code==503
    assert a.post('/api/auth/register',json={'name':'x','email':'new@example.test','password':'Test-pass-123'}).status_code==503
    assert worker.claim() is None
    before=len(db.query('SELECT * FROM jobs'));worker.tick_routines();assert len(db.query('SELECT * FROM jobs'))==before
    assert a.post('/api/auth/logout').status_code==200
    assert a.post('/api/auth/login',json={'email':'one@example.test','password':'Testing-only-1234'}).status_code==200


def test_process_lease_blocks_second_process_and_recovers_after_exit(tmp_path):
    database=tmp_path/'process.sqlite3'
    code="from backend.runtime import DatabaseLease; import sys; lease=DatabaseLease(sys.argv[1]).acquire(); print('acquired',flush=True); sys.stdin.readline()"
    proc=subprocess.Popen([sys.executable,'-c',code,str(database)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert proc.stdout.readline().strip()=='acquired'
        with pytest.raises(RuntimeError,match='one API worker'):runtime.DatabaseLease(database).acquire()
    finally:
        proc.communicate('\n',timeout=10)
    with runtime.DatabaseLease(database):pass


def test_production_requires_https_private_key_static_build_and_absolute_db(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_ENV','production');monkeypatch.setenv('APP_ORIGIN','https://app.example.test');monkeypatch.setenv('PUBLIC_ORIGIN','https://app.example.test');monkeypatch.setenv('ENCRYPTION_KEY',Fernet.generate_key().decode());monkeypatch.setenv('WORKER_ENABLED','true')
    (tmp_path/'index.html').write_text('test',encoding='utf-8');database=tmp_path/'data.sqlite3'
    runtime.production_checks(database,tmp_path)
    with pytest.raises(RuntimeError,match='absolute'):runtime.production_checks('relative.sqlite3',tmp_path)
    for value in ['http://app.example.test','https://user:secret@app.example.test','https://app.example.test/path','https://app.example.test?token=x']:
        monkeypatch.setenv('APP_ORIGIN',value)
        with pytest.raises(RuntimeError,match='HTTPS origin'):runtime.production_checks(database,tmp_path)
    monkeypatch.setenv('APP_ORIGIN','https://other.example.test')
    with pytest.raises(RuntimeError,match='same origin'):runtime.production_checks(database,tmp_path)
    monkeypatch.setenv('APP_ORIGIN','https://app.example.test');monkeypatch.setenv('ENCRYPTION_KEY','invalid-test-value')
    with pytest.raises(RuntimeError,match='ENCRYPTION_KEY'):runtime.production_checks(database,tmp_path)


def test_hold_release_requires_original_connection_key(backup_space,monkeypatch):
    _,wid,_=backup_space;key=Fernet.generate_key();token=Fernet(key).encrypt(b'{"access_token":"test"}').decode()
    db.query('INSERT INTO connections VALUES(?,?,?,?,?,?)',(db.uid(),wid,'google',token,'test@example.test',db.now()))
    db.query("INSERT INTO system_state VALUES('recovery_hold','{}')")
    with pytest.raises(ValueError,match='original ENCRYPTION_KEY'):ops.release_hold(db.DB_PATH,'All TEST-124 provider and payment evidence reviewed')
    assert runtime.is_held()
    monkeypatch.setenv('ENCRYPTION_KEY',key.decode())
    ops.release_hold(db.DB_PATH,'All TEST-124 provider and payment evidence reviewed')
    assert not runtime.is_held()
