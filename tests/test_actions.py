import base64,json,threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs,urlparse
import httpx,pytest
from cryptography.fernet import Fernet
from test_api import clients,create_space
from backend import db,actions,integrations,providers,worker

EVENT={'title':'Örnek görüşme','starts_at':'2026-10-10T10:00:00+03:00','ends_at':'2026-10-10T10:30:00+03:00','notes':'Yalnızca test'}
EMAIL={'to':'recipient@example.test','subject':'Örnek konu','body':'Yalnızca doğrulama taslağı.'}
REAL_CLIENT=httpx.Client

@pytest.fixture
def connected(clients,monkeypatch):
    a,b=clients;wid=create_space(a)
    monkeypatch.setenv('ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setenv('GOOGLE_CLIENT_ID','test-client')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET','test-secret')
    uid=a.get('/api/me').json()['id']
    token={'access_token':'test-access','refresh_token':'test-refresh','scope':integrations.GOOGLE_SCOPES,'expires_at':db.now()+3600}
    integrations.save_connection(wid,token,'owner@example.test','google-subject-test',uid)
    return a,b,wid,uid,token

def mock_http(monkeypatch,handler):
    monkeypatch.setattr(httpx,'Client',lambda **kw:REAL_CLIENT(transport=httpx.MockTransport(handler),**kw))

def prepare(a,wid,kind='google.calendar.create',payload=None):
    r=a.post(f'/api/workspaces/{wid}/actions',json={'kind':kind,'payload':payload or (EVENT if kind=='google.calendar.create' else EMAIL)})
    assert r.status_code==200,r.text
    return r.json()

def approve(a,wid,row):return a.post(f"/api/workspaces/{wid}/actions/{row['id']}/approve",json={'payload_hash':row['payload_hash']})

def test_review_edit_reject_and_tenant_boundaries(connected,monkeypatch):
    a,b,wid,_,_=connected;posts=[]
    mock_http(monkeypatch,lambda r:posts.append(r) or httpx.Response(500))
    row=prepare(a,wid)
    assert row['account_label']=='owner@example.test' and row['status']=='pending' and posts==[]
    assert b.get(f'/api/workspaces/{wid}/actions').status_code==404
    assert approve(b,wid,row).status_code==404
    assert b.put(f"/api/workspaces/{wid}/actions/{row['id']}",json={'payload_hash':row['payload_hash'],'payload':EVENT}).status_code==404
    changed=a.put(f"/api/workspaces/{wid}/actions/{row['id']}",json={'payload_hash':row['payload_hash'],'payload':{**EVENT,'title':'Yeni başlık'}}).json()
    assert changed['payload_hash']!=row['payload_hash']
    assert approve(a,wid,row).status_code==409 and posts==[]
    assert a.post(f"/api/workspaces/{wid}/actions/{row['id']}/reject",json={'payload_hash':changed['payload_hash']}).json()['status']=='rejected'
    assert approve(a,wid,changed).status_code==409 and posts==[]

def test_calendar_double_approval_creates_once(connected,monkeypatch):
    a,_,wid,_,_=connected;writes=[]
    def handle(request):
        assert request.url.host=='www.googleapis.com'
        body=json.loads(request.content);writes.append(body)
        assert 'attendees' not in body and body['summary']==EVENT['title']
        return httpx.Response(200,json={'id':body['id'],'extendedProperties':body['extendedProperties']})
    mock_http(monkeypatch,handle);row=prepare(a,wid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:approve(a,wid,row),range(2)))
    assert all(r.status_code==200 and r.json()['status']=='completed' for r in responses)
    assert len(writes)==1 and writes[0]['id']==row['id']

def test_draft_creates_only_draft_with_reviewed_recipient(connected,monkeypatch):
    a,_,wid,_,_=connected;calls=[]
    def handle(request):
        assert request.url.path=='/gmail/v1/users/me/drafts' and request.method=='POST'
        message=base64.urlsafe_b64decode(json.loads(request.content)['message']['raw']).decode()
        assert 'To: recipient@example.test' in message and 'Message-ID:' in message
        calls.append(message);return httpx.Response(200,json={'id':'draft-test-id'})
    mock_http(monkeypatch,handle);row=prepare(a,wid,'google.gmail.draft')
    result=approve(a,wid,row).json()
    assert result['status']=='completed' and 'gönderilmedi' in result['result']['message']
    approve(a,wid,row);assert len(calls)==1

@pytest.mark.parametrize('kind,payload',[
    ('google.gmail.send',EMAIL),
    ('google.gmail.draft',{**EMAIL,'to':'a@example.test\r\nBcc: b@example.test'}),
    ('google.gmail.draft',{**EMAIL,'subject':'Konu\nBcc: b@example.test'}),
    ('google.calendar.create',{**EVENT,'starts_at':'2026-10-10T10:00:00'}),
    ('google.calendar.create',{**EVENT,'ends_at':'2026-10-10T09:00:00+03:00'}),
    ('google.calendar.create',{**EVENT,'attendees':['unreviewed@example.test']}),
])
def test_invalid_external_payload_rejected(connected,kind,payload):
    a,_,wid,_,_=connected
    assert a.post(f'/api/workspaces/{wid}/actions',json={'kind':kind,'payload':payload}).status_code==400
    assert db.query('SELECT * FROM actions')==[]

def test_partial_consent_limits_available_tools(connected):
    a,_,wid,uid,token=connected
    integrations.save_connection(wid,{**token,'scope':integrations.CALENDAR_SCOPE},'owner@example.test','google-subject-test',uid)
    offered,_=actions.offered_tools(wid)
    assert [x['name'] for x in offered]==['prepare_calendar_event']
    assert a.post(f'/api/workspaces/{wid}/actions',json={'kind':'google.gmail.draft','payload':EMAIL}).status_code==409

def test_reconnect_invalidates_review_and_does_not_transfer_refresh_token(connected):
    a,_,wid,uid,token=connected;row=prepare(a,wid)
    token.pop('refresh_token')
    integrations.save_connection(wid,token,'different@example.test','different-subject',uid)
    assert approve(a,wid,row).status_code==409
    assert a.get(f'/api/workspaces/{wid}/actions').json()[0]['status']=='invalidated'
    stored=db.query('SELECT * FROM connections WHERE workspace_id=?',(wid,),one=True)
    assert 'refresh_token' not in integrations.decode_token(stored['encrypted_token'])

def test_disconnect_removes_local_access_preserves_history(connected,monkeypatch):
    a,_,wid,_,_=connected;row=prepare(a,wid)
    mock_http(monkeypatch,lambda r:pytest.fail('Workspace disconnect must not revoke other workspaces'))
    r=a.delete(f'/api/workspaces/{wid}/connections/google')
    assert r.status_code==200
    assert db.query('SELECT * FROM connections')==[]
    assert a.get(f'/api/workspaces/{wid}/actions').json()[0]['status']=='invalidated'
    assert approve(a,wid,row).status_code==409

def test_expired_access_refreshes_once_encrypted(connected,monkeypatch):
    a,_,wid,uid,token=connected;token['expires_at']=0
    integrations.save_connection(wid,token,'owner@example.test','google-subject-test',uid)
    calls=[]
    def handle(request):
        calls.append(request.url.path)
        if request.url.path=='/token':
            assert parse_qs(request.content.decode())['grant_type']==['refresh_token']
            return httpx.Response(200,json={'access_token':'renewed-test-access','expires_in':3600})
        assert request.headers['Authorization']=='Bearer renewed-test-access'
        return httpx.Response(200,json={'id':'draft-test'})
    mock_http(monkeypatch,handle)
    for _ in range(2):assert approve(a,wid,prepare(a,wid,'google.gmail.draft')).json()['status']=='completed'
    assert calls.count('/token')==1
    stored=db.query('SELECT encrypted_token FROM connections',one=True)['encrypted_token']
    assert 'renewed-test-access' not in stored and integrations.decode_token(stored)['refresh_token']=='test-refresh'

def test_refresh_failure_never_sends_stale_token_to_gmail(connected,monkeypatch):
    a,_,wid,uid,token=connected;token['expires_at']=0
    integrations.save_connection(wid,token,'owner@example.test','google-subject-test',uid)
    calls=[]
    def handle(request):
        calls.append(request.url.path);return httpx.Response(400,json={'error':'invalid_grant','sensitive':'must-not-leak'})
    mock_http(monkeypatch,handle)
    response=approve(a,wid,prepare(a,wid,'google.gmail.draft'))
    assert response.json()['status']=='failed' and calls==['/token'] and 'must-not-leak' not in response.text

@pytest.mark.parametrize('kind',['google.gmail.draft','google.calendar.create'])
def test_timeout_never_replays_external_write(connected,monkeypatch,kind):
    a,_,wid,_,_=connected;calls=[]
    def handle(request):
        calls.append(request);raise httpx.ReadTimeout('test unknown outcome',request=request)
    mock_http(monkeypatch,handle);row=prepare(a,wid,kind)
    assert approve(a,wid,row).json()['status']=='uncertain'
    assert approve(a,wid,row).status_code==409 and len(calls)==1
    actions.recover();assert len(calls)==1

def test_uncertain_calendar_reconciles_by_id_without_post(connected,monkeypatch):
    a,_,wid,_,_=connected;row=prepare(a,wid)
    db.query("UPDATE actions SET status='executing' WHERE id=?",(row['id'],));actions.recover()
    def handle(request):
        assert request.method=='GET' and request.url.path.endswith('/'+row['id'])
        return httpx.Response(200,json={'id':row['id'],'extendedProperties':{'private':{'isdas_action':row['payload_hash']}}})
    mock_http(monkeypatch,handle)
    r=a.post(f"/api/workspaces/{wid}/actions/{row['id']}/check")
    assert r.json()['status']=='completed'

def test_members_can_view_but_not_approve(connected):
    a,b,wid,_,_=connected;row=prepare(a,wid);uid=b.get('/api/me').json()['id']
    db.query('INSERT INTO memberships VALUES(?,?,?)',(wid,uid,'member'))
    assert b.get(f'/api/workspaces/{wid}/actions').status_code==200
    assert approve(b,wid,row).status_code==403

def test_model_proposal_waits_for_user_and_retains_usage(connected,monkeypatch):
    a,_,wid,_,_=connected
    monkeypatch.setenv('XAI_API_KEY','test-xai')
    calls=[]
    def handle(request):
        assert request.url.host=='api.x.ai'
        body=json.loads(request.content)
        assert 'prepare_calendar_event' in [t.get('name') for t in body['tools']]
        calls.append(request)
        return httpx.Response(200,json={'id':'test-response','status':'completed','output':[{'type':'function_call','name':'prepare_calendar_event','arguments':json.dumps(EVENT)}],'usage':{'cost_in_usd_ticks':1234}})
    mock_http(monkeypatch,handle)
    r=a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'10 Ekim görüşmesini takvimime eklemek üzere hazırla.','idempotency_key':'calendar-proposal-test'})
    assert r.status_code==200
    worker.process(worker.claim())
    assert len(calls)==1
    assert a.get(f'/api/workspaces/{wid}/actions').json()[0]['status']=='pending'
    job=a.get(f'/api/workspaces/{wid}/jobs').json()[0]
    assert job['status']=='completed' and 'Henüz Google' in job['output']
    assert db.query('SELECT actual_ticks FROM usage_ledger',one=True)['actual_ticks']==1234

def test_file_upload_does_not_silently_truncate(clients):
    a,_=clients;wid=create_space(a)
    r=a.post(f'/api/workspaces/{wid}/knowledge-upload',files={'file':('notes.txt','x'*15001,'text/plain')})
    assert r.status_code==413 and a.get(f'/api/workspaces/{wid}/memories').json()==[]

def test_oauth_pkce_one_use_and_verified_account(connected,monkeypatch):
    a,b,wid,_,_=connected
    started=a.post(f'/api/workspaces/{wid}/connections/google/start').json()
    params=parse_qs(urlparse(started['url']).query);state=params['state'][0]
    assert params['code_challenge_method']==['S256']
    assert b.get('/api/oauth/google/callback',params={'state':state,'code':'test-code'}).status_code==400
    def handle(request):
        if request.url.path=='/token':
            data=parse_qs(request.content.decode());assert data['code']==['test-code'] and data['code_verifier']
            return httpx.Response(200,json={'access_token':'new-oauth-test','expires_in':3600,'scope':integrations.CALENDAR_SCOPE})
        return httpx.Response(200,json={'id':'verified-id','email':'verified@example.test','verified_email':True})
    mock_http(monkeypatch,handle)
    response=a.get('/api/oauth/google/callback',params={'state':state,'code':'test-code'},follow_redirects=False)
    assert response.status_code==303
    assert parse_qs(__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(response.headers['location']).query)=={'view':['connections'],'workspace':[wid]}
    assert a.get('/api/oauth/google/callback',params={'state':state,'code':'test-code'}).status_code==400
    connection=db.query('SELECT * FROM connections WHERE workspace_id=?',(wid,),one=True)
    assert connection['account_label']=='verified@example.test'
    assert integrations.decode_token(connection['encrypted_token'])['scope']==integrations.CALENDAR_SCOPE

def test_oauth_unverified_email_does_not_replace_connection(connected,monkeypatch):
    a,_,wid,_,_=connected
    state=parse_qs(urlparse(a.post(f'/api/workspaces/{wid}/connections/google/start').json()['url']).query)['state'][0]
    def handle(request):
        if request.url.path=='/token':return httpx.Response(200,json={'access_token':'test','scope':integrations.GOOGLE_SCOPES})
        return httpx.Response(200,json={'id':'other','email':'other@example.test','verified_email':False})
    mock_http(monkeypatch,handle)
    assert a.get('/api/oauth/google/callback',params={'state':state,'code':'test-code'}).status_code==502
    assert db.query('SELECT account_label FROM connections',one=True)['account_label']=='owner@example.test'

def test_model_invalid_proposal_keeps_charge_but_has_no_external_effect(connected,monkeypatch):
    a,_,wid,_,_=connected;monkeypatch.setenv('XAI_API_KEY','test-xai')
    def handle(request):
        assert request.url.host=='api.x.ai'
        return httpx.Response(200,json={'id':'bad-proposal-test','status':'completed','output':[{'type':'function_call','name':'send_email','arguments':json.dumps(EMAIL)}],'usage':{'cost_in_usd_ticks':4567}})
    mock_http(monkeypatch,handle)
    assert a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Bir e-posta taslağı hazırla.','idempotency_key':'bad-proposal-key-001'}).status_code==200
    worker.process(worker.claim())
    assert db.query('SELECT * FROM actions')==[]
    assert db.query('SELECT actual_ticks FROM usage_ledger',one=True)['actual_ticks']==4567
    assert a.get(f'/api/workspaces/{wid}/jobs').json()[0]['status']=='failed'
