import json,threading,time
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend import accounts,db,mailer,runtime
from backend.main import app,ORIGIN,attempts

PASSWORD='Testing-account-1234'
NEW_PASSWORD='Updated-account-5678'

@pytest.fixture
def account_client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',str(tmp_path/'accounts.sqlite3'))
    for key,value in dict(APP_ENV='development',WORKER_ENABLED='false',MAIL_WORKER_ENABLED='false',REGISTRATION_ENABLED='true',EMAIL_VERIFICATION_REQUIRED='true',SMTP_HOST='smtp.example.test',SMTP_FROM='accounts@example.test',SMTP_SECURITY='starttls',SMTP_PORT='587',SMTP_USERNAME='',SMTP_PASSWORD='',ENCRYPTION_KEY=Fernet.generate_key().decode()).items():monkeypatch.setenv(key,value)
    attempts.clear()
    def never_live(*args,**kwargs):raise AssertionError('Unexpected live SMTP call')
    monkeypatch.setattr(mailer,'send',never_live)
    with TestClient(app,headers={'Origin':ORIGIN}) as client:yield client

def signup(client,email='owner@example.test'):
    r=client.post('/api/auth/register',json={'email':email,'password':PASSWORD,'name':'Hesap testi'})
    assert r.status_code==200,r.text
    return r.json()

def mail(purpose):
    row=db.query('SELECT * FROM mail_outbox WHERE purpose=? ORDER BY created DESC LIMIT 1',(purpose,),one=True)
    payload=json.loads(Fernet(__import__('os').environ['ENCRYPTION_KEY'].encode()).decrypt(row['payload'].encode()))
    return row,payload

def token(purpose):return mail(purpose)[1]['body'].split('#token=')[1].split()[0]
def reset(client,raw):return client.post('/api/auth/reset-password',json={'token':raw,'password':NEW_PASSWORD,'confirm_password':NEW_PASSWORD})
def forgot(client,email='owner@example.test'):return client.post('/api/auth/forgot-password',json={'email':email})

def test_registration_gates_workspace_but_allows_account_and_atomic_verification(account_client):
    c=account_client;user=signup(c)
    assert user['verification_required'] and not user['email_verified']
    assert c.get('/api/me').status_code==200
    assert c.get('/api/account').json()['verification_delivery']['state']=='queued'
    assert c.get('/api/workspaces').status_code==403
    assert c.post('/api/workspaces',json={'name':'test','kind':'personal'}).status_code==403
    raw=token('verify')
    assert raw not in str(db.query('SELECT * FROM account_tokens'))
    assert raw not in str(db.query('SELECT payload FROM mail_outbox'))
    assert raw not in c.get('/api/account').text
    assert c.post('/api/auth/verify-email',json={'token':raw,'password':NEW_PASSWORD}).status_code==400
    assert c.get('/api/me').json()['email_verified'] is False
    assert c.post('/api/auth/verify-email',json={'token':raw,'password':PASSWORD}).status_code==200
    assert c.get('/api/me').json()['email_verified'] is True
    assert c.get('/api/workspaces').status_code==200
    assert c.post('/api/auth/verify-email',json={'token':raw,'password':PASSWORD}).status_code==400

def test_reset_is_private_single_use_and_revokes_all_sessions_and_links(account_client):
    c=account_client;u=signup(c);verify=token('verify');first=c.cookies.get('isdas_session')
    assert c.post('/api/auth/login',json={'email':u['email'],'password':PASSWORD}).status_code==200
    second=c.cookies.get('isdas_session');assert second!=first
    missing=forgot(c,'missing@example.test');existing=forgot(c)
    assert missing.status_code==existing.status_code==200 and missing.json()==existing.json()
    raw=token('reset');assert raw not in existing.text
    assert c.get('/api/me').status_code==200 # Request alone does not lock the user.
    assert reset(c,raw).status_code==200
    assert db.query('SELECT * FROM sessions')==[]
    assert c.get('/api/me').status_code==401
    for old in (first,second):
        assert c.get('/api/me',headers={'Cookie':'isdas_session='+old}).status_code==401
    assert reset(c,raw).status_code==400
    assert c.post('/api/auth/verify-email',json={'token':verify,'password':PASSWORD}).status_code==400
    assert c.post('/api/auth/login',json={'email':u['email'],'password':PASSWORD}).status_code==401
    assert c.post('/api/auth/login',json={'email':u['email'],'password':NEW_PASSWORD}).json()['email_verified']
    notification=mail('password_changed')[1]
    assert PASSWORD not in notification['body'] and NEW_PASSWORD not in notification['body']

def test_expired_wrong_purpose_wrong_version_and_mismatched_password(account_client):
    c=account_client;signup(c);verify=token('verify');forgot(c);raw=token('reset')
    assert reset(c,verify).status_code==400
    assert c.post('/api/auth/reset-password',json={'token':raw,'password':NEW_PASSWORD,'confirm_password':PASSWORD}).status_code==422
    db.query('UPDATE account_tokens SET expires=? WHERE purpose=?',(db.now()-1,'reset'))
    assert reset(c,raw).status_code==400
    db.query('UPDATE account_state SET credential_version=credential_version+1')
    assert c.post('/api/auth/verify-email',json={'token':verify,'password':PASSWORD}).status_code==400

def test_resend_invalidates_previous_link_and_is_rate_limited(account_client):
    c=account_client;signup(c);old=token('verify')
    assert c.post('/api/account/send-verification').status_code==429
    db.query('DELETE FROM account_throttles')
    assert c.post('/api/account/send-verification').status_code==200
    new=token('verify');assert old!=new
    assert c.post('/api/auth/verify-email',json={'token':old,'password':PASSWORD}).status_code==400
    assert c.post('/api/auth/verify-email',json={'token':new,'password':PASSWORD}).status_code==200

def test_change_password_requires_current_password_and_signs_out(account_client):
    c=account_client;signup(c);forgot(c);old=token('reset')
    body={'current_password':NEW_PASSWORD,'password':NEW_PASSWORD,'confirm_password':NEW_PASSWORD}
    assert c.post('/api/account/change-password',json=body).status_code==400
    body['current_password']=PASSWORD
    assert c.post('/api/account/change-password',json=body).status_code==200
    assert c.get('/api/me').status_code==401
    assert reset(c,old).status_code==400

def test_mail_sent_payload_erased_without_token_consumption(account_client,monkeypatch):
    signup(account_client);raw=token('verify');sent=[]
    monkeypatch.setattr(mailer,'send',lambda payload,mid:sent.append(payload))
    assert mailer.dispatch_one();assert not mailer.dispatch_one()
    assert len(sent)==1 and raw in sent[0]['body']
    assert db.query('SELECT state,payload FROM mail_outbox',one=True)=={'state':'sent','payload':''}
    assert len(db.query('SELECT * FROM account_tokens'))==1

def test_ambiguous_smtp_failure_is_not_replayed_or_exposed(account_client,monkeypatch):
    signup(account_client);raw=token('verify');calls=[]
    def fail(payload,mid):calls.append(mid);raise TimeoutError('private provider text '+raw)
    monkeypatch.setattr(mailer,'send',fail)
    assert mailer.dispatch_one();assert not mailer.dispatch_one();assert len(calls)==1
    info=account_client.get('/api/account');assert info.json()['verification_delivery']['state']=='uncertain'
    assert raw not in info.text and 'private provider' not in info.text

def test_expired_mail_and_recovery_hold_never_send(account_client,monkeypatch):
    signup(account_client);calls=[];monkeypatch.setattr(mailer,'send',lambda *a:calls.append(a))
    db.query("INSERT INTO system_state VALUES('recovery_hold','{}')")
    assert not mailer.dispatch_one();assert not calls
    assert forgot(account_client).status_code==503
    db.query('DELETE FROM system_state');db.query('UPDATE mail_outbox SET expires=?',(db.now()-1,))
    assert not mailer.dispatch_one();assert not calls
    assert db.query('SELECT payload,state FROM mail_outbox',one=True)=={'payload':'','state':'expired'}

def test_restart_cannot_replay_claimed_mail(account_client,monkeypatch):
    signup(account_client);db.query("UPDATE mail_outbox SET state='sending'")
    monkeypatch.setattr(mailer,'dispatch_one',lambda:False)
    thread,stop=mailer.start();stop.set();thread.join(2)
    assert not thread.is_alive()
    assert db.query('SELECT state,payload FROM mail_outbox',one=True)=={'state':'uncertain','payload':''}

def test_reset_email_throttle_is_persistent_and_non_enumerating(account_client):
    c=account_client;signup(c);first=forgot(c);raw=token('reset')
    for _ in range(4):assert forgot(c).json()==first.json()
    assert token('reset')==raw
    assert len(db.query("SELECT * FROM mail_outbox WHERE purpose='reset'"))==1
    assert 'owner@example.test' not in str(db.query('SELECT * FROM account_throttles'))

def test_unconfigured_mail_reports_unavailable_and_production_requires_it(account_client,monkeypatch,tmp_path):
    c=account_client;monkeypatch.setenv('SMTP_HOST','')
    assert not c.get('/api/auth/options').json()['mail_available']
    assert c.post('/api/auth/register',json={'email':'user@example.test','password':PASSWORD,'name':'A'}).status_code==503
    assert forgot(c).status_code==503
    assert db.query('SELECT * FROM users')==[]
    monkeypatch.setenv('APP_ENV','production');monkeypatch.setenv('APP_ORIGIN','https://app.example.test');monkeypatch.setenv('PUBLIC_ORIGIN','https://app.example.test');monkeypatch.setenv('WORKER_ENABLED','true')
    (tmp_path/'index.html').write_text('test')
    with pytest.raises(RuntimeError,match='Public production registration'):runtime.production_checks(tmp_path/'db.sqlite3',tmp_path)

def test_login_and_reset_serialize_session_creation(account_client,monkeypatch):
    c=account_client;signup(c);forgot(c);raw=token('reset');entered=threading.Event();release=threading.Event();original=accounts.verify_password
    def waiting(value,stored):
        entered.set();assert release.wait(5);return original(value,stored)
    monkeypatch.setattr(accounts,'verify_password',waiting)
    # Separate client avoids sharing a cookie jar across worker threads.
    login_client=TestClient(app,headers={'Origin':ORIGIN})
    with ThreadPoolExecutor(max_workers=2) as pool:
        login=pool.submit(login_client.post,'/api/auth/login',json={'email':'owner@example.test','password':PASSWORD})
        assert entered.wait(5)
        recovery=pool.submit(reset,c,raw);time.sleep(.1);release.set()
        assert login.result().status_code==200 and recovery.result().status_code==200
    assert db.query('SELECT * FROM sessions')==[]
    assert login_client.get('/api/me').status_code==401
    login_client.close()

def test_simultaneous_reset_consumes_token_once(account_client):
    signup(account_client);forgot(account_client);raw=token('reset')
    with TestClient(app,headers={'Origin':ORIGIN}) as other,ThreadPoolExecutor(max_workers=2) as pool:
        results=[pool.submit(reset,c,raw) for c in (account_client,other)]
        assert sorted(r.result().status_code for r in results)==[200,400]
    assert len(db.query("SELECT * FROM audit WHERE event='account.password_changed'"))==1

def test_email_and_origin_validation(account_client):
    for email in ['bad@example.test\r\nBcc:evil@example.test','bad@example','bad@','@example.test']:
        assert account_client.post('/api/auth/register',json={'email':email,'password':PASSWORD,'name':'A'}).status_code in {400,422}
    assert account_client.post('/api/auth/forgot-password',json={'email':'owner@example.test'},headers={'Origin':'https://evil.example'}).status_code==403

def test_smtp_uses_verified_tls_before_credentials(monkeypatch):
    for key,value in dict(SMTP_HOST='smtp.example.test',SMTP_FROM='accounts@example.test',SMTP_PORT='587',SMTP_SECURITY='starttls',SMTP_USERNAME='sender',SMTP_PASSWORD='test-only',ENCRYPTION_KEY=Fernet.generate_key().decode()).items():monkeypatch.setenv(key,value)
    calls=[]
    class Server:
        def __init__(self,*args,**kwargs):calls.append('connect')
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def ehlo(self):calls.append('ehlo')
        def starttls(self,context):assert context.check_hostname;calls.append('tls')
        def login(self,*args):calls.append('login')
        def send_message(self,msg):assert msg['To']=='recipient@example.test';calls.append('send');return {}
    monkeypatch.setattr(mailer.smtplib,'SMTP',Server)
    mailer.send({'to':'recipient@example.test','subject':'test','body':'test'},'local-test')
    assert calls==['connect','ehlo','tls','ehlo','login','send']
    monkeypatch.setenv('SMTP_SECURITY','none');assert not mailer.available()

def test_restore_invalidates_recovery_links_and_pending_mail(account_client,monkeypatch,tmp_path):
    from backend import ops
    signup(account_client);forgot(account_client)
    monkeypatch.setenv('BACKUP_ENCRYPTION_KEY',Fernet.generate_key().decode())
    target=tmp_path/'backup.enc';ops.backup(db.DB_PATH,target)
    restored=tmp_path/'restored.sqlite3';ops.restore_candidate(target,restored)
    monkeypatch.setattr(db,'DB_PATH',str(restored))
    assert db.query('SELECT * FROM account_tokens')==[]
    assert all(r['state']=='cancelled' and r['payload']=='' for r in db.query('SELECT * FROM mail_outbox'))
    assert runtime.is_held()
