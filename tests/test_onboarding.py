import json,os,socket,subprocess
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_accounts import account_client,signup
from backend.main import app,ORIGIN
from backend import db,billing,onboarding,site_reader

@pytest.fixture
def setup_client(account_client):
    u=signup(account_client)
    db.query('UPDATE account_state SET verified_at=? WHERE user_id=?',(db.now(),u['id']))
    return account_client,u

def create(c,**kwargs):
    return c.post('/api/workspace-setup',json={'name':'TEST Mola','kind':'business','goal':'content','idempotency_key':'setup-test-123456',**kwargs})

def state(c,wid):return c.get(f'/api/workspaces/{wid}/onboarding').json()
def advance(c,wid,step,revision,goal='content'):return c.put(f'/api/workspaces/{wid}/onboarding',json={'step':step,'expected_revision':revision,'goal':goal})
def preview(c,wid):return c.post(f'/api/workspaces/{wid}/site-preview',json={'url':'https://example.com/'})
def apply(c,wid,pid,**kwargs):return c.post(f'/api/workspaces/{wid}/site-preview/apply',json={'preview_id':pid,'title':'Confirmed website','text':'This confirmed public information describes our products and working hours.',**kwargs})

def test_atomic_creation_replay_and_no_free_credit(setup_client):
    c,u=setup_client
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:create(c),range(2)))
    assert all(r.status_code==200 for r in results)
    wid=results[0].json()['workspace_id'];assert results[1].json()['workspace_id']==wid
    assert c.get('/api/workspaces').json()[0]['setup_step']=='profile'
    assert state(c,wid)['revision']==1
    assert db.query('SELECT role FROM memberships WHERE workspace_id=?',(wid,),one=True)['role']=='owner'
    assert db.query('SELECT count(*) AS n FROM entitlement_periods',one=True)['n']==0
    assert db.query('SELECT count(*) AS n FROM jobs',one=True)['n']==0
    assert create(c,name='Different').status_code==409
    assert create(c,idempotency_key='new-setup-123456').status_code==409
    assert db.query('SELECT count(*) AS n FROM workspace_onboarding',one=True)['n']==1

def test_personas_validation_and_existing_workspace_remains_complete(setup_client):
    c,u=setup_client
    for values in [{'name':'   '},{'website':'http://example.com'},{'website':'https://localhost'},{'timezone':'Not/AZone'},{'kind':'admin'}]:assert create(c,**values).status_code in {400,422}
    billing.grant_period(u['id'],'pilot',db.now(),db.now()+86400,'operator_pilot',u['id'],'Local test')
    for kind in ['personal','business','agency']:
        r=create(c,kind=kind,idempotency_key='different-key-'+kind);assert r.status_code==200,r.text
    assert create(c,idempotency_key='fourth-space-1234').status_code==409
    db.query('DELETE FROM workspace_onboarding')
    for ws in c.get('/api/workspaces').json():
        assert ws['setup_step'] is None
        assert state(c,ws['id'])['step']=='complete'
        r=advance(c,ws['id'],'profile',0);assert r.status_code==200
        assert r.json()['revision']==1 and r.json()['attempt_key']

def test_profile_and_progress_revisions_prevent_lost_updates(setup_client):
    c,u=setup_client;wid=create(c).json()['workspace_id'];s=state(c,wid)
    body={k:v for k,v in s['brand'].items() if k not in {'revision','updated'}}
    body.update(description='Local bakery',tone='Kısa ve samimi',expected_revision=1,expected_brand_revision=0)
    r=c.put(f'/api/workspaces/{wid}/onboarding/profile',json=body);assert r.status_code==200,r.text
    assert r.json()['step']=='knowledge'
    assert c.put(f'/api/workspaces/{wid}/onboarding/profile',json=body).status_code==409
    body.update(expected_revision=2);assert c.put(f'/api/workspaces/{wid}/onboarding/profile',json=body).status_code==409
    assert state(c,wid)['brand']['tone']=='Kısa ve samimi'
    assert c.get('/api/workspaces').json()[0]['brand_voice']=='Kısa ve samimi'
    assert advance(c,wid,'connections',2).status_code==200
    assert advance(c,wid,'complete',2).status_code==409
    assert advance(c,wid,'complete',3).status_code==200
    assert not state(c,wid)['first_result_ready']

def test_setup_is_owner_only_and_preview_not_shared(setup_client,monkeypatch):
    c,u=setup_client;wid=create(c).json()['workspace_id']
    with TestClient(app,headers={'Origin':ORIGIN}) as other:
        outsider=signup(other,'outside@example.test');db.query('UPDATE account_state SET verified_at=?',(db.now(),))
        for role in [None,'member','reviewer']:
            if role:db.query('INSERT OR REPLACE INTO memberships VALUES(?,?,?)',(wid,outsider['id'],role))
            assert other.get(f'/api/workspaces/{wid}/onboarding').status_code==404
            assert advance(other,wid,'complete',1).status_code==404
            assert preview(other,wid).status_code==404
            assert apply(other,wid,'a'*32).status_code==404
        assert set(other.get('/api/workspaces').json()[0])=={'id','name','kind','member_role'}

@pytest.fixture
def public_page(monkeypatch):
    result={'ok':True,'url':'https://example.com/about','title':'Test public page','description':'Example description','text':'This is public information about the products and services of our example company.','warnings':['Only this page was read.']}
    reader=Mock(return_value=result);monkeypatch.setattr(onboarding,'read_site',reader);return reader

def test_preview_is_explicit_editable_tenant_bound_and_idempotent(setup_client,public_page):
    c,u=setup_client;wid=create(c).json()['workspace_id'];r=preview(c,wid)
    assert r.status_code==200,r.text
    assert r.json()['saved_to_memory'] is False
    assert state(c,wid)['memories']==0
    assert state(c,wid)['brand']['description']==''
    pid=r.json()['id'];saved=apply(c,wid,pid);assert saved.status_code==200
    assert apply(c,wid,pid).json()==saved.json()
    assert state(c,wid)['memories']==1
    m=db.query('SELECT * FROM memories',one=True)
    assert m['source']=='website:https://example.com/about'
    assert m['content'].startswith('This confirmed')
    assert apply(c,wid,pid,text='Changed but sufficiently long confirmed text from a website.').status_code==409
    assert state(c,wid)['brand']['description']==''
    db.query('DELETE FROM memories WHERE id=?',(m['id'],))
    assert apply(c,wid,pid).status_code==409

def test_expiry_latest_preview_and_rate_limit(setup_client,public_page):
    c,u=setup_client;wid=create(c).json()['workspace_id'];first=preview(c,wid).json()['id'];second=preview(c,wid).json()['id']
    assert apply(c,wid,first).status_code==404
    db.query('UPDATE site_previews SET expires=?',(db.now()-1,))
    assert apply(c,wid,second).status_code==404
    for _ in range(8):assert preview(c,wid).status_code==200
    assert preview(c,wid).status_code==429
    assert public_page.call_count==10

def test_site_reader_rechecks_recovery_after_network(setup_client,public_page,monkeypatch):
    c,u=setup_client;wid=create(c).json()['workspace_id']
    monkeypatch.setattr(onboarding.runtime,'recovery_hold',lambda *args,**kwargs:False if not public_page.called else True)
    assert preview(c,wid).status_code==503
    assert not db.query('SELECT * FROM site_previews')

def test_first_job_recovery_does_not_claim_completion(setup_client):
    c,u=setup_client;wid=create(c).json()['workspace_id'];s=state(c,wid)
    # An actual queued row is sufficient to recover a lost enqueue response.
    jid=db.uid();db.query('INSERT INTO jobs(id,workspace_id,user_id,agent_id,prompt,status,stage,created,updated,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?)',(jid,wid,u['id'],'guide','First test job','queued','Queued',db.now(),db.now(),'setup:'+wid+':'+s['attempt_key']))
    assert state(c,wid)['first_job']['id']==jid
    assert not state(c,wid)['first_result_ready']
    assert advance(c,wid,'complete',1).status_code==200
    assert not state(c,wid)['first_result_ready']
    db.query("UPDATE jobs SET status='completed' WHERE id=?",(jid,))
    assert state(c,wid)['first_result_ready']

def test_url_validation_denies_non_public_url_forms():
    bad=['http://example.com','https://127.0.0.1','https://127.1','https://2130706433','https://[::1]','https://localhost','https://example.com:444','https://user:password@example.com','https://example.com\\@localhost','https://example.com/\r\nX-Evil: 1','https://example.com/%20 space','https://example.com%00.localhost','file:///etc/passwd']
    for url in bad:
        with pytest.raises(site_reader.Unreadable):site_reader.normalize_url(url)
    assert site_reader.normalize_url('https://Example.COM:443/ürün?a=ç#fragment')=='https://example.com/%C3%BCr%C3%BCn?a=%C3%A7'

def test_dns_mixed_public_private_and_tunnel_addresses_denied(monkeypatch):
    for bad in ['127.0.0.1','10.0.0.3','169.254.169.254','100.64.0.1','0.0.0.0','224.0.0.1','::1','fe80::1','fc00::1','::ffff:8.8.8.8','2002:0808:0808::1']:
        monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443)),(socket.AF_INET6 if ':' in bad else socket.AF_INET,socket.SOCK_STREAM,6,'',(bad,443))])
        with pytest.raises(site_reader.Unreadable):site_reader.public_addresses('example.com')

def test_connect_pins_validated_ip_and_verifies_original_hostname(monkeypatch):
    raw=Mock();monkeypatch.setattr(socket,'socket',Mock(return_value=raw))
    conn=site_reader.PinnedHTTPS('example.com',(socket.AF_INET,('8.8.8.8',443)))
    assert conn._context.check_hostname and conn._context.verify_mode==__import__('ssl').CERT_REQUIRED
    tls=Mock();conn._context=tls;conn.connect()
    raw.connect.assert_called_once_with(('8.8.8.8',443))
    tls.wrap_socket.assert_called_once_with(raw,server_hostname='example.com')
    conn.close()

def test_redirect_target_is_revalidated_before_connect(monkeypatch):
    checked=[]
    def dns(host):
        checked.append(host)
        if host=='internal.example.com':raise site_reader.Unreadable('Private target')
        return [(socket.AF_INET,('8.8.8.8',443))]
    response=Mock(status=302);response.getheader=lambda name,default='':'https://internal.example.com/' if name=='Location' else default
    connection=Mock();connection.getresponse.return_value=response;factory=Mock(return_value=connection)
    monkeypatch.setattr(site_reader,'public_addresses',dns);monkeypatch.setattr(site_reader,'PinnedHTTPS',factory)
    with pytest.raises(site_reader.Unreadable):site_reader.read('https://example.com/')
    assert checked==['example.com','internal.example.com'];assert factory.call_count==1;connection.close.assert_called_once()

def test_html_extraction_ignores_executable_navigation_and_limits_output():
    raw=b'<title>Test &amp; Company</title><script>secret instructions</script><nav>menu</nav><main><h1>About</h1><p>We make useful things and serve our customers every weekday.</p></main>'
    r=site_reader.extract(raw,'text/html; charset=utf-8')
    assert r['title']=='Test & Company' and 'We make useful' in r['text']
    assert 'secret instructions' not in r['text'] and 'menu' not in r['text']
    assert len(site_reader.extract(b'a'*20000,'text/plain')['text'])==15000
    for raw,mime in [(b'x'*(site_reader.MAX_BYTES+1),'text/html'),(b'<html>empty</html>','text/html'),(b'a'*50,'application/pdf')]:
        with pytest.raises(site_reader.Unreadable):site_reader.extract(raw,mime)

def test_subprocess_has_deadline_isolation_and_no_credentials(monkeypatch):
    monkeypatch.setenv('XAI_API_KEY','PRIVATE-MODEL-KEY');monkeypatch.setenv('SMTP_PASSWORD','PRIVATE-MAIL-PASSWORD');monkeypatch.setenv('HTTPS_PROXY','http://secret-proxy')
    def run(args,**kwargs):
        assert args[1]=='-I' and args[2].endswith('site_reader.py')
        assert kwargs['timeout']==22 and kwargs['input']==b'https://example.com/'
        assert not any(k in kwargs['env'] for k in ['XAI_API_KEY','SMTP_PASSWORD','HTTPS_PROXY','ENCRYPTION_KEY'])
        return subprocess.CompletedProcess(args,0,b'{"ok":true,"text":"test"}')
    monkeypatch.setattr(subprocess,'run',run)
    assert onboarding.read_site('https://example.com')['ok']
    def timeout(*a,**k):raise subprocess.TimeoutExpired(a[0],22)
    monkeypatch.setattr(subprocess,'run',timeout)
    with pytest.raises(HTTPException) as e:onboarding.read_site('https://example.com')
    assert e.value.status_code==422
    monkeypatch.setattr(subprocess,'run',run)
    assert onboarding.read_site('https://example.com')['ok'] # semaphore released after failure

def test_isolated_reader_uses_utf8_protocol_independent_of_console():
    import sys
    from pathlib import Path
    result=subprocess.run([sys.executable,'-I',str(Path(site_reader.__file__).resolve())],input='http://örnek.test/'.encode('utf-8'),capture_output=True,timeout=25,env={**os.environ,'PYTHONIOENCODING':'ascii'})
    assert result.returncode==0
    parsed=json.loads(result.stdout.decode('utf-8'))
    assert parsed['ok'] is False and 'kullanılamaz' in parsed['error']
