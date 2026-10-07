import os
os.environ['WORKER_ENABLED']='false'
import pytest
from fastapi.testclient import TestClient
from backend.main import app,ORIGIN,attempts
from backend import db,providers,worker,billing

@pytest.fixture
def clients(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',str(tmp_path/'test.sqlite3'))
    monkeypatch.delenv('XAI_API_KEY',raising=False)
    monkeypatch.setenv('HERMES_RUNTIME_MAP',str(tmp_path/'no-runtime.json'))
    attempts.clear()
    with TestClient(app,headers={'Origin':ORIGIN}) as a,TestClient(app,headers={'Origin':ORIGIN}) as b:
        for client,name in [(a,'one'),(b,'two')]:
            r=client.post('/api/auth/register',json={'name':name,'email':name+'@example.test','password':'Testing-only-1234'})
            assert r.status_code==200
            start=db.now();billing.grant_period(r.json()['id'],'pilot',start,start+14*86400,'operator_pilot',r.json()['id'],'Test fixture: no payment and no live provider')
        yield a,b

def create_space(client,name='Örnek alan'):
    r=client.post('/api/workspaces',json={'name':name,'kind':'business'})
    assert r.status_code==200,r.text
    return r.json()['id']

def test_workspace_isolation_for_every_read_surface(clients):
    a,b=clients;wid=create_space(a)
    assert b.get('/api/workspaces').json()==[]
    for route in ['overview','agents','memories','jobs','artifacts','events','events.ics','routines','connections','usage']:
        assert b.get(f'/api/workspaces/{wid}/{route}').status_code==404,route

def test_record_cannot_be_moved_or_read_across_workspaces(clients):
    a,b=clients;first=create_space(a);second=create_space(b)
    record=a.post(f'/api/workspaces/{first}/memories',json={'title':'Özel bilgi','content':'Yalnızca ilk işletmenin bilgisi.'}).json()['id']
    assert b.put(f'/api/workspaces/{second}/memories/{record}',json={'title':'değiştir','content':'başka veri'}).status_code==404
    assert b.delete(f'/api/workspaces/{first}/memories/{record}').status_code==404
    assert a.get(f'/api/workspaces/{first}/memories').json()[0]['content']=='Yalnızca ilk işletmenin bilgisi.'

def test_csrf_and_logout(clients):
    a,_=clients
    assert a.post('/api/workspaces',headers={'Origin':'https://attacker.invalid'},json={'name':'bad','kind':'personal'}).status_code==403
    assert a.post('/api/auth/logout').status_code==200
    assert a.get('/api/me').status_code==401

def test_unconfigured_provider_does_not_fake_completion(clients):
    a,_=clients;wid=create_space(a)
    response=a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Bir araştırma yap','idempotency_key':'missing-provider-key'})
    assert response.status_code==503
    assert a.get(f'/api/workspaces/{wid}/jobs').json()==[]
    assert a.get(f'/api/workspaces/{wid}/artifacts').json()==[]

def test_job_idempotency_completion_and_usage(clients,monkeypatch):
    a,_=clients;wid=create_space(a)
    monkeypatch.setattr(providers,'available',lambda wid:{'ready':True,'provider':'test'})
    calls=[]
    def output(workspace,role,prompt,memories):
        calls.append(workspace['id']);return {'text':'Test sağlayıcısı tarafından üretilen doğrulama çıktısı.','usage':{'input_tokens':23,'output_tokens':12},'provider':'test'}
    monkeypatch.setattr(providers,'run',output)
    body={'prompt':'Test görevini hazırla','agent_id':'social','idempotency_key':'same-delivery-key-123'}
    first=a.post(f'/api/workspaces/{wid}/jobs',json=body);second=a.post(f'/api/workspaces/{wid}/jobs',json=body)
    assert first.json()['id']==second.json()['id']
    changed=a.post(f'/api/workspaces/{wid}/jobs',json={**body,'prompt':'başka görev'})
    assert changed.status_code==409
    worker.process(worker.claim());assert worker.claim() is None
    assert calls==[wid]
    job=a.get(f'/api/workspaces/{wid}/jobs').json()[0]
    assert job['status']=='completed' and 'input_tokens' in job['usage']
    assert len(a.get(f'/api/workspaces/{wid}/artifacts').json())==1

def test_cancelled_job_is_not_executed(clients,monkeypatch):
    a,_=clients;wid=create_space(a)
    monkeypatch.setattr(providers,'available',lambda wid:{'ready':True,'provider':'test'})
    job=a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Test görevi','idempotency_key':'cancel-delivery-key'}).json()
    assert a.post(f"/api/workspaces/{wid}/jobs/{job['id']}/cancel").status_code==200
    assert worker.claim() is None

def test_calendar_persistence_validation_and_export(clients):
    a,_=clients;wid=create_space(a)
    item={'title':'Örnek görüşme','starts_at':'2026-10-05T10:00:00+03:00','ends_at':'2026-10-05T10:30:00+03:00','notes':'Deneme'}
    assert a.post(f'/api/workspaces/{wid}/events',json=item).status_code==200
    assert a.post(f'/api/workspaces/{wid}/events',json={**item,'ends_at':'2026-10-05T09:00:00+03:00'}).status_code==400
    exported=a.get(f'/api/workspaces/{wid}/events.ics')
    assert 'DTSTART:20261005T070000Z' in exported.text
    assert 'Örnek görüşme' in exported.text

def test_oauth_callback_rejects_unbound_state(clients):
    a,_=clients
    assert a.get('/api/oauth/google/callback?state=not-issued&code=not-real').status_code==400

def test_routine_is_claimed_once(clients,monkeypatch):
    a,_=clients;wid=create_space(a)
    monkeypatch.setattr(providers,'available',lambda wid:{'ready':True,'provider':'test'})
    r=a.post(f'/api/workspaces/{wid}/routines',json={'prompt':'Her gün test özeti','interval_hours':24,'next_run':'2025-01-01T00:00:00Z'})
    assert r.status_code==200,r.text
    worker.tick_routines();worker.tick_routines()
    assert len(a.get(f'/api/workspaces/{wid}/jobs').json())==1
