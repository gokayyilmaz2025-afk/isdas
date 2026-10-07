import datetime,json,threading,time
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from test_membership_reviews import people,join,account_client
from backend import agenda,db,providers,worker,conversations
from backend import ops,runtime
from cryptography.fernet import Fernet

FIELDS=['title','notes','due_local','timezone','priority','estimate_minutes','remind_before','assignee_id']
def base(wid):return f'/api/workspaces/{wid}/agenda'
def create(c,wid,**values):
    r=c.post(base(wid)+'/tasks',json={'title':'TEST · Sunumu hazırla','request_key':db.uid(),**values});assert r.status_code==200,r.text;return r.json()
def edit(c,wid,t,**values):return c.put(base(wid)+'/tasks/'+t['id'],json={**{k:t[k] for k in FIELDS},'expected_revision':t['revision'],**values})
def state(c,wid,t,action):return c.post(base(wid)+'/tasks/'+t['id']+'/state',json={'action':action,'expected_revision':t['revision']})
def listing(c,wid,**params):
    r=c.get(base(wid),params={'day':datetime.date.today().isoformat(),**params});assert r.status_code==200,r.text;return r.json()
def past():return (datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M')
def notices(c):return c.get('/api/notifications?kind=agenda').json()
def plan_fixture(wid,uid):
    stamp=db.now();jid=db.uid()
    with db.connection() as conn:
        conn.execute('INSERT INTO jobs(id,workspace_id,user_id,agent_id,prompt,status,stage,created,updated,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?)',(jid,wid,uid,'assistant','TEST plan','completed','TEST ready',stamp,stamp,jid))
        agenda.save_plan(conn,{'id':jid,'workspace_id':wid,'user_id':uid},{'title':'TEST day','items':[{'title':'First'},{'title':'Second'}]})
    return db.query('SELECT id FROM agenda_plans WHERE job_id=?',(jid,),one=True)['id']

def test_task_edit_state_lifecycle_and_conflicting_revision(people):
    cs,us,wid=people;join(people,1);t=create(cs[0],wid,assignee_id=us[1]['id'],estimate_minutes=30,priority='high')
    assert t['assignee_name']=='Hesap testi' and 'request_key' not in t
    changed=edit(cs[1],wid,t,title='Edited by team').json();assert changed['revision']==2
    assert edit(cs[0],wid,t,title='Stale overwrite').status_code==409
    done=state(cs[1],wid,changed,'done').json();assert done['status']=='done' and done['completed_at']
    assert state(cs[0],wid,changed,'done').status_code==409
    archived=state(cs[0],wid,done,'archive').json();assert archived['status']=='archived'
    assert edit(cs[0],wid,archived,title='Should not edit').status_code==409
    restored=state(cs[0],wid,archived,'restore').json();assert restored['status']=='done'
    reopened=state(cs[0],wid,restored,'reopen').json();assert reopened['status']=='open' and reopened['completed_at'] is None
    assert listing(cs[1],wid,mine=True)['summary']['open']==1
    assert listing(cs[0],wid,mine=True)['summary']['open']==0

def test_create_replay_not_duplicate_even_after_edit_or_archive(people):
    cs,us,wid=people;key=db.uid();t=create(cs[0],wid,request_key=key)
    changed=edit(cs[0],wid,t,title='User changed original').json();archived=state(cs[0],wid,changed,'archive').json()
    replay=create(cs[0],wid,request_key=key)
    assert replay['id']==t['id'] and replay['status']=='archived' and replay['title']==archived['title']
    assert cs[0].post(base(wid)+'/tasks',json={'title':'Different input','request_key':key}).status_code==409
    assert db.query('SELECT count(*) n FROM agenda_tasks',one=True)['n']==1

def test_workspace_reviewer_assignee_and_csrf_boundaries(people):
    cs,us,wid=people;join(people,2,'reviewer');t=create(cs[0],wid)
    for c in cs[1:]:
        assert c.get(base(wid),params={'day':'2026-10-04'}).status_code==404
        assert c.get(base(wid)+'/tasks/'+t['id']).status_code==404
        assert edit(c,wid,t,title='No').status_code==404
        assert state(c,wid,t,'done').status_code==404
    for uid in [us[1]['id'],us[2]['id'],db.uid()]:
        assert cs[0].post(base(wid)+'/tasks',json={'title':'Wrong assignee','request_key':db.uid(),'assignee_id':uid}).status_code==404
    other=cs[0].post('/api/workspaces',json={'name':'Other','kind':'personal'}).json()['id']
    assert cs[0].get(base(other)+'/tasks/'+t['id']).status_code==404
    assert cs[0].post(base(wid)+'/tasks',headers={'Origin':'https://attacker.invalid'},json={'title':'No','request_key':db.uid()}).status_code==403

@pytest.mark.parametrize('values',[
    {'title':'   '},{'due_local':'2026-03-29T02:30','timezone':'Europe/Berlin'},
    {'due_local':'2026-10-25T02:30','timezone':'Europe/Berlin'},
    {'timezone':'not-a-zone'},{'remind_before':15},{'remind_before':12,'due_local':'2026-10-04T10:00'},
    {'priority':'critical'},{'estimate_minutes':1441},{'due_local':'2026-99-99T99:99'}])
def test_invalid_dates_fields_and_ambiguous_dst_are_rejected(people,values):
    cs,us,wid=people;r=cs[0].post(base(wid)+'/tasks',json={'title':'Test','request_key':db.uid(),**values});assert r.status_code==422
    assert db.query('SELECT * FROM agenda_tasks')==[]

def test_timezone_day_boundaries_paging_search_and_exact_old_task(people,monkeypatch):
    cs,us,wid=people;c=cs[0]
    before=create(c,wid,title='Before midnight',due_local='2026-10-03T20:59',timezone='UTC')
    inside=create(c,wid,title='After midnight',due_local='2026-10-03T21:01',timezone='UTC')
    after=create(c,wid,title='Tomorrow',due_local='2026-10-04T21:01',timezone='UTC')
    for i in range(52):create(c,wid,title=f'Undated {i}')
    monkeypatch.setattr(db,'now',lambda:datetime.datetime(2026,10,4,10,tzinfo=datetime.timezone.utc).timestamp())
    r=listing(c,wid,day='2026-10-04');assert r['summary']=={'open':55,'overdue':2,'today':1,'undated':52,'due_by':2}
    assert len(r['items'])==50 and r['next_offset']==50
    second=listing(c,wid,day='2026-10-04',offset=50);assert len(second['items'])==4
    assert after['id'] not in [t['id'] for t in r['items']+second['items']]
    assert listing(c,wid,scope='all',q='Tomorrow')['items'][0]['id']==after['id']
    assert c.get(base(wid)+'/tasks/'+after['id']).json()['id']==after['id']

def test_reminder_assignee_generation_read_completion_and_reopen(people):
    cs,us,wid=people;join(people,1)
    t=create(cs[0],wid,assignee_id=us[1]['id'],due_local=past(),timezone='UTC',remind_before=0)
    mail_count=db.query('SELECT count(*) n FROM mail_outbox',one=True)['n']
    assert agenda.tick_reminders()==1 and agenda.tick_reminders()==0
    assert notices(cs[0])['items']==[];n=notices(cs[1])['items'][0];assert 'task='+t['id'] in n['href'] and n['kind']=='agenda'
    assert cs[1].post('/api/notifications/'+n['id']+'/open').status_code==200
    assert notices(cs[1])['unread_count']==0
    changed=edit(cs[0],wid,t,title='Same reminder, new title').json();assert agenda.tick_reminders()==0
    assert notices(cs[1])['items'][0]['summary']=='Same reminder, new title'
    done=state(cs[1],wid,changed,'done').json();assert notices(cs[1])['items']==[]
    assert cs[1].post('/api/notifications/'+n['id']+'/open').status_code==404
    reopened=state(cs[1],wid,done,'reopen').json();assert agenda.tick_reminders()==1 and notices(cs[1])['unread_count']==1
    reassigned=edit(cs[0],wid,reopened,assignee_id=us[0]['id']).json();assert notices(cs[1])['items']==[]
    assert agenda.tick_reminders()==1 and notices(cs[0])['items'][0]['summary']==reassigned['title']
    assert db.query('SELECT count(*) n FROM mail_outbox',one=True)['n']==mail_count

def test_future_disabled_revoked_and_restore_hold_reminders(people):
    cs,us,wid=people;join(people,1)
    future=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
    create(cs[0],wid,due_local=future,timezone='UTC',remind_before=0)
    create(cs[0],wid,due_local=past(),timezone='UTC')
    t=create(cs[0],wid,due_local=past(),timezone='UTC',remind_before=15,assignee_id=us[1]['id'])
    db.query("INSERT OR REPLACE INTO system_state VALUES('recovery_hold','true')")
    assert agenda.tick_reminders()==0
    db.query("DELETE FROM system_state WHERE key='recovery_hold'")
    assert cs[0].delete(f"/api/workspaces/{wid}/members/{us[1]['id']}").status_code==200
    assert agenda.tick_reminders()==0 and notices(cs[1])['items']==[]
    assert cs[0].get(base(wid)+'/tasks/'+t['id']).status_code==200

def test_two_scheduler_ticks_and_two_plan_applies_are_atomic(people):
    cs,us,wid=people;t=create(cs[0],wid,due_local=past(),timezone='UTC',remind_before=0)
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(lambda _:agenda.tick_reminders(),[1,2]))==[0,1]
    assert len(notices(cs[0])['items'])==1
    pid=plan_fixture(wid,us[0]['id']);body={'items':[{'title':'First'},{'title':'Second'}]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:cs[0].post(base(wid)+f'/plans/{pid}/apply',json=body),[1,2]))
    assert all(r.status_code==200 for r in results)
    assert results[0].json()['plan']['task_ids']==results[1].json()['plan']['task_ids']
    assert db.query('SELECT count(*) n FROM agenda_tasks WHERE plan_id=?',(pid,),one=True)['n']==2
    assert cs[0].post(base(wid)+f'/plans/{pid}/apply',json={'items':[{'title':'Different'}]}).status_code==409

def test_plan_invalid_assignee_rolls_back_whole_batch_and_capacity(people,monkeypatch):
    cs,us,wid=people;pid=plan_fixture(wid,us[0]['id'])
    r=cs[0].post(base(wid)+f'/plans/{pid}/apply',json={'items':[{'title':'Good'},{'title':'Bad','assignee_id':us[3]['id']}]});assert r.status_code==404
    assert db.query('SELECT * FROM agenda_tasks')==[]
    assert cs[0].get(base(wid)+f'/plans/{pid}').json()['applied_at'] is None
    monkeypatch.setattr(agenda,'MAX_TASKS',1)
    assert cs[0].post(base(wid)+f'/plans/{pid}/apply',json={'items':[{'title':'First'},{'title':'Second'}]}).status_code==409
    assert db.query('SELECT * FROM agenda_tasks')==[]

def test_real_provider_plan_tool_worker_and_current_agenda_context(people,monkeypatch):
    cs,us,wid=people;c=cs[0];task=create(c,wid,title='Existing personal work');seen=[]
    monkeypatch.setenv('XAI_API_KEY','TEST NO REAL KEY');real=httpx.Client
    payload={'title':'My day','items':[{'title':'Prepare report','notes':'From the request','due_local':None,'timezone':'Europe/Istanbul','priority':'high','estimate_minutes':30,'remind_before':None}]}
    def handle(request):
        body=json.loads(request.content);seen.append(body)
        return httpx.Response(200,json={'id':'local-agenda-receipt','status':'completed','output':[{'type':'function_call','name':'prepare_agenda_plan','arguments':json.dumps(payload)}],'usage':{'cost_in_usd_ticks':50000}})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    r=c.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Prepare my tasks','agent_id':'assistant','start_conversation':True,'idempotency_key':db.uid()});assert r.status_code==200,r.text
    j=r.json();worker.process(worker.claim());detail=c.get(f"/api/workspaces/{wid}/conversations/{j['conversation_id']}").json()
    assert detail['turns'][0]['status']=='completed' and len(detail['agenda_plans'])==1
    assert db.query('SELECT count(*) n FROM agenda_tasks',one=True)['n']==1,'Planning must not activate tasks/reminders'
    assert any(t.get('name')=='prepare_agenda_plan' for t in seen[0]['tools'])
    assert 'Existing personal work' in seen[0]['input'][0]['content']
    pid=detail['agenda_plans'][0]['id'];r=c.post(base(wid)+f'/plans/{pid}/apply',json={'items':[{**payload['items'][0],'title':'User edited task'}]});assert r.status_code==200
    assert r.json()['tasks'][0]['title']=='User edited task' and r.json()['tasks'][0]['assignee_id']==us[0]['id']
    completed=state(c,wid,r.json()['tasks'][0],'done').json()
    latest=conversations.context_for({**j,'turn_sequence':2})['resources']['agenda_plans'][0]
    assert latest['applied'] and latest['tasks'][0]['status']=='done' and latest['tasks'][0]['title']==completed['title']

def test_unoffered_or_malicious_plan_fails_with_usage_receipt(people,monkeypatch):
    cs,us,wid=people;monkeypatch.setenv('XAI_API_KEY','TEST NO REAL KEY');real=httpx.Client
    bad={'title':'Bad plan','items':[{'title':'Secret task','assignee_id':us[3]['id']}]}
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'status':'completed','id':'bad-plan-receipt','usage':{'cost_in_usd_ticks':1234},'output':[{'type':'function_call','name':'prepare_agenda_plan','arguments':json.dumps(bad)}]})),**kw))
    for role in ['assistant','social']:
        cs[0].post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Bad provider fixture','agent_id':role,'idempotency_key':db.uid()});worker.process(worker.claim())
    assert all(j['status']=='failed' for j in db.query('SELECT * FROM jobs'))
    assert db.query('SELECT * FROM agenda_plans')==[] and db.query('SELECT * FROM agenda_tasks')==[]
    assert all(r['actual_ticks']==1234 for r in db.query('SELECT actual_ticks FROM usage_ledger'))

def test_event_local_timezone_edit_conflict_and_legacy_export(people):
    cs,us,wid=people;c=cs[0];body={'title':'Planning time','starts_local':'2026-10-04T10:00','ends_local':'2026-10-04T11:00','timezone':'Europe/Istanbul','request_key':db.uid()}
    r=c.post(base(wid)+'/events',json=body);assert r.status_code==200,r.text;e=r.json();assert e['starts_at']=='2026-10-04T07:00:00+00:00'
    assert c.post(base(wid)+'/events',json=body).json()['id']==e['id']
    changed=c.put(base(wid)+'/events/'+e['id'],json={**body,'title':'Updated','expected_revision':1});assert changed.status_code==200
    assert c.put(base(wid)+'/events/'+e['id'],json={**body,'expected_revision':1}).status_code==409
    assert c.delete(base(wid)+f"/events/{e['id']}?expected_revision=1").status_code==409
    assert listing(c,wid,day='2026-10-04')['events'][0]['title']=='Updated'
    export=c.get(f'/api/workspaces/{wid}/events.ics');assert 'DTSTART:20261004T070000Z' in export.text
    assert c.delete(base(wid)+f"/events/{e['id']}?expected_revision=2").status_code==200
    assert c.post(base(wid)+'/events',json=body).status_code==409,'Deleted receipt must not recreate event'
    legacy=c.post(f'/api/workspaces/{wid}/events',json={'title':'Legacy','starts_at':'2026-10-04T10:00:00+03:00','ends_at':'2026-10-04T11:00:00+03:00'});assert legacy.status_code==200

def test_reminder_scheduler_runs_while_model_thread_is_waiting(people,monkeypatch):
    cs,us,wid=people;entered=threading.Event();release=threading.Event()
    monkeypatch.setattr(providers,'available',lambda _: {'ready':True,'provider':'xai'})
    def held(*a,**k):entered.set();assert release.wait(12);return {'text':'TEST only','usage':{'cost_in_usd_ticks':5000},'provider':'xai'}
    monkeypatch.setattr(providers,'run',held)
    cs[0].post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Held local model fixture','idempotency_key':db.uid()});claimed=worker.claim();t=threading.Thread(target=worker.process,args=(claimed,));t.start();assert entered.wait(2)
    create(cs[0],wid,due_local=past(),timezone='UTC',remind_before=0)
    scheduler,stop=agenda.start()
    try:
        deadline=time.monotonic()+6
        while time.monotonic()<deadline and not notices(cs[0])['items']:time.sleep(.05)
        assert notices(cs[0])['items'] and t.is_alive()
    finally:stop.set();scheduler.join(3);release.set();t.join(3)

def test_failed_delivery_transaction_rolls_back_notice_and_can_retry(people):
    cs,us,wid=people;t=create(cs[0],wid,due_local=past(),timezone='UTC',remind_before=0)
    db.query("CREATE TRIGGER agenda_test_abort BEFORE UPDATE OF reminder_sent ON agenda_tasks BEGIN SELECT RAISE(ABORT,'Synthetic write failure'); END")
    with pytest.raises(__import__('sqlite3').IntegrityError):agenda.tick_reminders()
    assert notices(cs[0])['items']==[]
    assert db.query('SELECT reminder_sent FROM agenda_tasks WHERE id=?',(t['id'],),one=True)['reminder_sent']==0
    db.query('DROP TRIGGER agenda_test_abort')
    assert agenda.tick_reminders()==1 and len(notices(cs[0])['items'])==1

def test_encrypted_restore_keeps_tasks_receipts_and_holds_reminders(people,monkeypatch,tmp_path):
    cs,us,wid=people;sent=create(cs[0],wid,due_local=past(),timezone='UTC',remind_before=0);assert agenda.tick_reminders()==1
    pending=create(cs[0],wid,title='Pending after snapshot',due_local=past(),timezone='UTC',remind_before=0)
    monkeypatch.setenv('BACKUP_ENCRYPTION_KEY',Fernet.generate_key().decode());snapshot=tmp_path/'agenda.enc';restored=tmp_path/'agenda-restored.sqlite3'
    ops.backup(db.DB_PATH,snapshot);ops.restore_candidate(snapshot,restored)
    monkeypatch.setattr(db,'DB_PATH',str(restored))
    with db.connection() as conn:
        assert runtime.recovery_hold(conn)
        assert conn.execute('SELECT reminder_sent FROM agenda_tasks WHERE id=?',(sent['id'],)).fetchone()[0]==1
        assert conn.execute('SELECT reminder_sent FROM agenda_tasks WHERE id=?',(pending['id'],)).fetchone()[0]==0
    assert agenda.tick_reminders()==0
    assert db.query('SELECT count(*) n FROM notifications',one=True)['n']==1
