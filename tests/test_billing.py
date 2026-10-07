"""Customer entitlement invariants. All receipts and payments here are synthetic."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from test_api import clients,create_space
from backend import billing,db,providers,usage,worker
from backend.main import app,ORIGIN

@pytest.fixture
def metered(clients,monkeypatch):
    a,b=clients;wid=create_space(a);other=create_space(a,'İkinci marka');foreign=create_space(b,'Başka hesap')
    # Time-travel billing tests keep their test sessions valid across renewals.
    db.query('UPDATE sessions SET expires=?',(db.now()+90*86400,))
    monkeypatch.setattr(providers,'available',lambda wid:{'ready':True,'provider':'xai'})
    monkeypatch.setattr(providers,'run',lambda *args,**kwargs:{'text':'Yerel test yanıtı','provider':'xai','usage':{'cost_in_usd_ticks':3_451_001},'request_id':'synthetic-test'})
    return a,b,wid,other,foreign

def send(client,wid,agent='guide',key=None):
    return client.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Bu deneme işini hazırla.','agent_id':agent,'idempotency_key':key or db.uid(),'start_conversation':True})

def summary(client,wid):return client.get(f'/api/workspaces/{wid}/billing').json()
def complete():
    job=worker.claim();assert job;worker.process(job);return db.query('SELECT * FROM jobs WHERE id=?',(job['id'],),one=True)
def period_for(wid):
    owner=db.query('SELECT owner_id FROM workspaces WHERE id=?',(wid,),one=True)['owner_id']
    return db.query('SELECT * FROM entitlement_periods WHERE account_id=? ORDER BY starts_at DESC',(owner,),one=True)

def test_new_registration_has_no_automatic_credit_and_one_setup_space(metered):
    with TestClient(app,headers={'Origin':ORIGIN}) as c:
        assert c.post('/api/auth/register',json={'name':'New','email':'unfunded@example.test','password':'Local-test-123456'}).status_code==200
        wid=create_space(c);state=summary(c,wid)
        assert state['active'] is False and state['remaining_milli']==0 and state['checkout_available'] is False
        assert c.post('/api/workspaces',json={'name':'Too many','kind':'agency'}).status_code==409
        assert send(c,wid).status_code==402
        assert db.query('SELECT * FROM jobs WHERE workspace_id=?',(wid,))==[]
        assert db.query('SELECT * FROM conversations WHERE workspace_id=?',(wid,))==[]
        assert c.post(f'/api/workspaces/{wid}/billing',json={'plan_id':'agency','credits':999999}).status_code in {404,405}

def test_concurrent_admission_shares_one_balance_across_workspaces(metered):
    a,b,wid,other,foreign=metered
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda n:send(a,wid if n%2 else other,'research'),range(8)))
    assert sorted(r.status_code for r in results)==[200]*3+[402]*5
    assert summary(a,wid)['held_milli']==300_000
    assert summary(a,other)['remaining_milli']==0
    assert summary(b,foreign)['remaining_milli']==300_000
    assert len(db.query('SELECT * FROM jobs'))==3 and len(db.query('SELECT * FROM conversations'))==3

def test_job_replay_reserves_once_and_quote_matches_job_limit(metered):
    a,_,wid,_,_=metered;key=db.uid()
    quote=a.get(f'/api/workspaces/{wid}/billing/quote?agent_id=research').json()
    first=send(a,wid,'research',key);again=send(a,wid,'research',key)
    assert first.status_code==again.status_code==200 and first.json()['id']==again.json()['id']
    assert len(db.query('SELECT * FROM credit_ledger'))==1
    assert summary(a,wid)['held_milli']==quote['max_milli']==100_000
    assert a.get(f'/api/workspaces/{wid}/billing/quote?agent_id=made-up').status_code==404

def test_receipt_rounding_raw_accuracy_and_release_of_unused_hold(metered):
    a,_,wid,_,_=metered;send(a,wid);done=complete()
    assert done['status']=='completed'
    state=summary(a,wid)
    assert state['spent_milli']==35 and state['held_milli']==0 and state['remaining_milli']==299965
    assert usage.summary(wid)['spent_ticks']==3_451_001
    with db.connection() as conn:usage.settle(conn,done['id'],{'cost_in_usd_ticks':999999999},'xai')
    assert summary(a,wid)['spent_milli']==35

def test_provider_overrun_is_not_an_unapproved_customer_credit_charge(metered,monkeypatch):
    a,_,wid,_,_=metered
    monkeypatch.setattr(providers,'run',lambda *args,**kwargs:{'text':'test','provider':'xai','usage':{'cost_in_usd_ticks':2*usage.TICKS}})
    send(a,wid);complete()
    assert summary(a,wid)['spent_milli']==25_000
    assert usage.summary(wid)['spent_ticks']==2*usage.TICKS

def test_cancel_before_execution_releases_hold_and_calls_no_provider(metered):
    a,_,wid,_,_=metered;job=send(a,wid).json()
    assert summary(a,wid)['held_milli']==25_000
    assert a.post(f"/api/workspaces/{wid}/jobs/{job['id']}/cancel").status_code==200
    assert summary(a,wid)['held_milli']==0 and summary(a,wid)['spent_milli']==0
    assert worker.claim() is None and db.query('SELECT * FROM usage_ledger')==[]

def test_cancel_after_provider_started_preserves_incurred_charge(metered,monkeypatch):
    a,_,wid,_,_=metered;job=send(a,wid).json()
    def answer(*args,**kwargs):
        db.query('UPDATE jobs SET cancel_requested=1 WHERE id=?',(job['id'],))
        return {'text':'Discard me','provider':'xai','usage':{'cost_in_usd_ticks':50000}}
    monkeypatch.setattr(providers,'run',answer);done=complete()
    assert done['status']=='cancelled' and done['output']==''
    assert summary(a,wid)['spent_milli']==1

def test_provider_disappearing_or_operator_capacity_releases_queued_hold(metered,monkeypatch):
    a,_,wid,_,_=metered;send(a,wid)
    monkeypatch.setenv('GLOBAL_MONTHLY_BUDGET_USD','0')
    assert complete()['status']=='blocked'
    assert summary(a,wid)['held_milli']==0
    send(a,wid)
    monkeypatch.setattr(providers,'available',lambda wid:{'ready':False,'reason':'Test: no provider'})
    assert complete()['status']=='blocked' and summary(a,wid)['held_milli']==0

def test_expired_queued_job_does_not_charge_or_move_to_new_period(metered,monkeypatch):
    a,_,wid,_,_=metered;send(a,wid);old=period_for(wid)
    monkeypatch.setattr(db,'now',lambda:old['ends_at']+1)
    billing.grant_period(old['account_id'],'business',old['ends_at'],old['ends_at']+30*86400,'verified_payment','synthetic-renewal','Test fixture: verified payment simulation, no payment collected')
    assert complete()['status']=='blocked'
    state=summary(a,wid)
    assert state['remaining_milli']==1_200_000 and state['spent_milli']==0
    assert db.query('SELECT state FROM credit_ledger',one=True)['state']=='released'

def test_running_job_finishes_against_original_period(metered,monkeypatch):
    a,_,wid,_,_=metered;old=period_for(wid);job=send(a,wid).json()
    def answer(*args,**kwargs):
        monkeypatch.setattr(db,'now',lambda:old['ends_at']+2)
        billing.grant_period(old['account_id'],'business',old['ends_at'],old['ends_at']+30*86400,'verified_payment','synthetic-renewal','Test-only payment simulation')
        return {'text':'test','provider':'xai','usage':{'cost_in_usd_ticks':100000}}
    monkeypatch.setattr(providers,'run',answer);assert complete()['status']=='completed'
    record=db.query('SELECT * FROM credit_ledger WHERE job_id=?',(job['id'],),one=True)
    assert record['period_id']==old['id'] and record['charged_milli']==1
    assert summary(a,wid)['spent_milli']==0 and summary(a,wid)['remaining_milli']==1_200_000

def test_unknown_cost_blocks_other_workspace_and_renewal_until_reconciled(metered,monkeypatch):
    a,_,wid,other,_=metered;old=period_for(wid)
    def lost(*args,**kwargs):raise RuntimeError('Synthetic lost provider receipt')
    monkeypatch.setattr(providers,'run',lost);job=send(a,wid).json();assert complete()['status']=='failed'
    assert summary(a,wid)['unresolved_count']==1 and summary(a,wid)['held_milli']==25000
    assert send(a,other).status_code==402
    monkeypatch.setattr(db,'now',lambda:old['ends_at']+1)
    billing.grant_period(old['account_id'],'business',old['ends_at'],old['ends_at']+30*86400,'verified_payment','simulated-paid-period','Test-only provider confirmation')
    assert send(a,other).status_code==402
    usage.reconcile(job['id'],'0.10','Synthetic test invoice reference, not a real charge')
    assert summary(a,wid)['unresolved_count']==0 and summary(a,wid)['spent_milli']==0
    assert send(a,other).status_code==200

def test_grants_are_idempotent_do_not_overlap_or_restore_revoked_credit(metered):
    a,_,wid,_,foreign=metered;p=period_for(wid)
    replay=billing.grant_period(p['account_id'],p['plan_id'],p['starts_at'],p['ends_at'],p['source'],p['external_ref'],'Replay test evidence')
    assert replay['id']==p['id']
    with pytest.raises(ValueError):billing.grant_period(p['account_id'],'pilot',p['starts_at'],p['ends_at'],'operator_pilot','another-trial','Repeat trial rejected')
    with pytest.raises(ValueError):billing.grant_period(p['account_id'],'business',p['starts_at'],p['ends_at'],'verified_payment','overlap','Overlap test evidence')
    with pytest.raises(ValueError):billing.grant_period(period_for(foreign)['account_id'],p['plan_id'],p['starts_at'],p['ends_at'],p['source'],p['external_ref'],'Cross-account replay test')
    billing.revoke(p['id'],'Synthetic refund/revocation test')
    replay=billing.grant_period(p['account_id'],p['plan_id'],p['starts_at'],p['ends_at'],p['source'],p['external_ref'],'Late replay test evidence')
    assert replay['revoked']==1 and send(a,wid).status_code==402
    assert summary(a,wid)['remaining_milli']==0

def test_revocation_while_queued_stops_execution_and_preserves_artifacts(metered):
    a,_,wid,_,_=metered;send(a,wid);assert complete()['status']=='completed'
    send(a,wid);billing.revoke(period_for(wid)['id'],'Operator test cancellation evidence')
    assert complete()['status']=='blocked'
    assert summary(a,wid)['held_milli']==0
    assert len(a.get(f'/api/workspaces/{wid}/artifacts').json())==1

def test_workspace_creation_limit_atomic_and_member_spaces_do_not_count(metered):
    a,b,wid,_,foreign=metered;uid=a.get('/api/me').json()['id']
    db.query('INSERT INTO memberships VALUES(?,?,?)',(foreign,uid,'member'))
    def add(_):return a.post('/api/workspaces',json={'name':'New brand','kind':'agency'})
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(add,range(4)))
    assert sorted(r.status_code for r in results)==[200,409,409,409]
    assert summary(a,wid)['workspace_count']==3

def test_member_receipts_never_include_other_customer_names_or_prompts(metered):
    a,b,wid,other,foreign=metered
    send(a,wid);send(a,other)
    assert b.get(f'/api/workspaces/{wid}/billing').status_code==404
    assert b.get(f'/api/workspaces/{wid}/billing/quote').status_code==404
    db.query('INSERT INTO memberships VALUES(?,?,?)',(wid,b.get('/api/me').json()['id'],'member'))
    data=summary(b,wid)
    assert data['can_manage'] is False and data['workspace_count'] is None
    assert len(data['receipts'])==1 and data['receipts'][0]['workspace_name']!='İkinci marka'
    assert data['held_milli']==50_000 # Shared balance intentionally visible.
    assert send(b,wid).status_code==200
    assert summary(b,foreign)['held_milli']==0 # Charged to workspace owner, not requester.

def test_restart_keeps_queued_holds_and_marks_inflight_unknown(metered):
    a,_,wid,other,_=metered;send(a,wid);send(a,other)
    job=worker.claim();usage.reserve(job,'xai');usage.recover()
    records=db.query('SELECT state FROM credit_ledger ORDER BY state')
    assert [r['state'] for r in records]==['reserved','uncertain']
    assert summary(a,wid)['unresolved_count']==1 and summary(a,wid)['held_milli']==50_000

def test_calendar_month_change_does_not_reset_customer_allowance(metered,monkeypatch):
    a,_,wid,_,_=metered;send(a,wid);complete();before=summary(a,wid)
    monkeypatch.setattr(usage,'period',lambda:'2099-01')
    after=summary(a,wid)
    assert after['remaining_milli']==before['remaining_milli'] and after['period']==before['period']
