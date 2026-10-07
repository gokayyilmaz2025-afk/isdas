"""Budget and billing invariants; no paid provider requests are made."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend import db,usage,providers,worker,billing

@pytest.fixture
def spaces(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',str(tmp_path/'usage.sqlite3'))
    for key,value in {'DEFAULT_MONTHLY_BUDGET_USD':'1','GLOBAL_MONTHLY_BUDGET_USD':'10','JOB_RESERVE_USD':'0.25','RESEARCH_JOB_RESERVE_USD':'0.5'}.items():monkeypatch.setenv(key,value)
    db.init()
    for name in ['a','b']:
        db.query('INSERT INTO users VALUES(?,?,?,?,?)',(name,name+'@example.test',name,'unused',db.now()))
        db.query('INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?)',(name,name,name,'business','','','','Europe/Istanbul',db.now()))
        db.query('INSERT INTO memberships VALUES(?,?,?)',(name,name,'owner'))
        start=db.now();billing.grant_period(name,'pilot',start,start+14*86400,'operator_pilot',name,'Test fixture: no payment and no live provider')
    return 'a','b'

def job(wid='a',agent='guide'):
    jid=db.uid()
    db.query('INSERT INTO jobs(id,workspace_id,user_id,agent_id,prompt,status,stage,created,updated,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?)',(jid,wid,wid,agent,'Deneme görevi','running','Çalışıyor',db.now(),db.now(),db.uid()))
    return db.query('SELECT * FROM jobs WHERE id=?',(jid,),one=True)

def settle(item,ticks):
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        usage.settle(conn,item['id'],{'cost_in_usd_ticks':ticks},'xai','test-request')

def test_concurrent_jobs_cannot_reserve_same_balance(spaces):
    jobs=[job() for _ in range(12)]
    def reserve(item):
        try:return usage.reserve(item,'xai')
        except usage.BudgetUnavailable:return False
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(reserve,jobs))
    assert sum(results)==4
    assert usage.summary('a')['held_ticks']==usage.TICKS
    assert usage.summary('b')['held_ticks']==0

def test_global_limit_applies_across_workspaces(spaces,monkeypatch):
    monkeypatch.setenv('GLOBAL_MONTHLY_BUDGET_USD','0.25')
    usage.reserve(job('a'),'xai')
    with pytest.raises(usage.BudgetUnavailable):usage.reserve(job('b'),'xai')

def test_receipt_is_exact_idempotent_and_releases_reservation(spaces):
    item=job();usage.reserve(item,'xai')
    settle(item,37_756_001);settle(item,999_999_999)
    balance=usage.summary('a')
    assert balance['spent_ticks']==37_756_001
    assert balance['held_ticks']==0
    assert balance['remaining_ticks']==usage.TICKS-37_756_001

def test_real_cost_over_reservation_is_recorded_and_blocks_next_job(spaces):
    item=job();usage.reserve(item,'xai');settle(item,2*usage.TICKS)
    assert usage.summary('a')['spent_ticks']==2*usage.TICKS
    with pytest.raises(usage.BudgetUnavailable):usage.reserve(job(),'xai')

def test_unknown_cost_blocks_own_future_work_until_audited_reconciliation(spaces,monkeypatch):
    item=job();usage.reserve(item,'xai')
    with db.connection() as conn:usage.settle(conn,item['id'],{},'xai')
    assert usage.summary('a')['unresolved_count']==1
    # The calendar reset must not make an unknown bill disappear.
    monkeypatch.setattr(usage,'period',lambda:'2099-02')
    with pytest.raises(usage.BudgetUnavailable):usage.reserve(job(),'xai')
    assert usage.reserve(job('b'),'xai')
    usage.reconcile(item['id'],'0.125','Provider invoice reference: test-only')
    assert usage.summary('a')['unresolved_count']==0
    assert usage.reserve(job(),'xai')
    assert db.query("SELECT COUNT(*) AS n FROM audit WHERE event='usage.reconciled'",one=True)['n']==1

def test_restart_preserves_uncertain_spending(spaces):
    item=job();usage.reserve(item,'xai');usage.recover()
    assert usage.summary('a')['held_ticks']==usage.TICKS//4
    assert usage.summary('a')['unresolved_count']==1

def test_cancelled_inflight_job_keeps_actual_charge_without_artifact(spaces,monkeypatch):
    item=job();monkeypatch.setattr(providers,'available',lambda wid:{'ready':True,'provider':'xai'})
    def complete(*args):
        db.query('UPDATE jobs SET cancel_requested=1 WHERE id=?',(item['id'],))
        return {'text':'Discarded after cancellation','usage':{'cost_in_usd_ticks':42},'provider':'xai'}
    monkeypatch.setattr(providers,'run',complete);worker.process(item)
    assert db.query('SELECT status FROM jobs WHERE id=?',(item['id'],),one=True)['status']=='cancelled'
    assert usage.summary('a')['spent_ticks']==42
    assert db.query('SELECT * FROM artifacts')==[]

def test_failed_provider_response_with_receipt_is_still_charged(spaces,monkeypatch):
    item=job();monkeypatch.setattr(providers,'available',lambda wid:{'ready':True,'provider':'xai'})
    def fail(*args):raise providers.ProviderFailure({'cost_in_usd_ticks':600},'xai','request-test')
    monkeypatch.setattr(providers,'run',fail);worker.process(item)
    assert usage.summary('a')['spent_ticks']==600
    assert db.query('SELECT status FROM jobs WHERE id=?',(item['id'],),one=True)['status']=='failed'

def test_network_failure_never_assumes_free_call(spaces,monkeypatch):
    item=job();monkeypatch.setattr(providers,'available',lambda wid:{'ready':True,'provider':'xai'})
    def fail(*args):raise TimeoutError('test')
    monkeypatch.setattr(providers,'run',fail);worker.process(item)
    assert usage.summary('a')['unresolved_count']==1
    assert usage.summary('a')['held_ticks']==usage.TICKS//4

def test_cancel_before_reserve_and_missing_provider_do_not_consume_budget(spaces,monkeypatch):
    item=job();db.query('UPDATE jobs SET cancel_requested=1 WHERE id=?',(item['id'],))
    assert usage.reserve(item,'xai') is False
    monkeypatch.setattr(providers,'available',lambda wid:{'ready':False,'reason':'test missing provider'})
    worker.process(job())
    assert usage.summary('a')['receipts']==[]

@pytest.mark.parametrize('value',[True,-1,1.2,'not-a-number',None])
def test_invalid_cost_is_unknown_not_zero(value):
    assert usage.reported_ticks({'cost_in_usd_ticks':value},'xai') is None

def test_hermes_final_call_usage_is_not_assumed_to_cover_whole_run():
    assert usage.reported_ticks({'cost_in_usd_ticks':100},'hermes') is None

def test_memory_context_budget_retains_relevant_excerpt():
    items=[{'title':'Genel','content':'Arşiv '*10000},{'title':'Kahve fiyatı','content':'Kahve fiyatı 100 TL. '*1000}]
    selected=providers.bounded_memories(items,'Kahve fiyatı nedir?',limit=5000)
    assert selected[0]['title']=='Kahve fiyatı'
    assert sum(len(i['content'])+len(i['title']) for i in selected)<=5000
    assert selected[0]['excerpt_only']

def test_exhausted_budget_prevents_provider_execution(spaces,monkeypatch):
    first=job();usage.reserve(first,'xai');settle(first,usage.TICKS)
    next_job=job();calls=[]
    monkeypatch.setattr(providers,'available',lambda wid:{'ready':True,'provider':'xai'})
    monkeypatch.setattr(providers,'run',lambda *args:calls.append(args))
    worker.process(next_job)
    assert calls==[]
    assert db.query('SELECT status FROM jobs WHERE id=?',(next_job['id'],),one=True)['status']=='blocked'

@pytest.mark.parametrize('response_status,text,http_status',[('completed','A real parsed test result',200),('incomplete','',200),('failed','',400)])
def test_provider_http_parser_preserves_receipt_on_success_and_failure(spaces,monkeypatch,response_status,text,http_status):
    import httpx
    factory=httpx.Client
    def respond(request):
        assert request.url.path=='/v1/responses'
        return httpx.Response(http_status,json={'id':'resp-test','status':response_status,'usage':{'cost_in_usd_ticks':123},'output':[{'type':'message','content':[{'type':'output_text','text':text}]}]})
    monkeypatch.setattr(providers,'runtime_for',lambda wid:{'name':'xai','url':'https://example.test/v1','api_key':'test-only-not-a-secret','model':'test-model'})
    monkeypatch.setattr(providers.httpx,'Client',lambda **kwargs:factory(transport=httpx.MockTransport(respond),**kwargs))
    workspace=db.query('SELECT * FROM workspaces WHERE id=?',('a',),one=True)
    role={'id':'guide','prompt':'A test role'}
    if response_status=='completed':
        result=providers.run(workspace,role,'A test task',[])
        assert result['usage']['cost_in_usd_ticks']==123 and result['request_id']=='resp-test'
    else:
        with pytest.raises(providers.ProviderFailure) as error:providers.run(workspace,role,'A test task',[])
        assert error.value.usage['cost_in_usd_ticks']==123
