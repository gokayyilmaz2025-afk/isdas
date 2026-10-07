"""Real SQLite transitions and API authorization; no live provider or mail."""
import base64,json
import pytest
from test_membership_reviews import people,join,post,request_review,decision,account_client
from backend import db,worker,providers,integrations
from backend.main import app,ORIGIN
from fastapi.testclient import TestClient

def feed(c,query=''):
    r=c.get('/api/notifications'+query);assert r.status_code==200,r.text;return r.json()

def job(wid,uid,state='completed',at=None,prompt='TEST job',conn=None):
    jid=db.uid();at=at or db.now()
    sql='INSERT INTO jobs(id,workspace_id,user_id,agent_id,prompt,status,stage,output,error,created,updated,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)'
    args=(jid,wid,uid,'guide',prompt,state,'TEST stage','TEST output','PRIVATE RAW ERROR',at,at,jid)
    if conn:conn.execute(sql,args)
    else:db.query(sql,args)
    return jid

def connection(wid,uid):
    integrations.save_connection(wid,{'access_token':'TEST PRIVATE TOKEN','refresh_token':'TEST REFRESH','scope':integrations.GOOGLE_SCOPES,'expires_at':db.now()+3600},'test@example.test','test-google-subject',uid)

def action(c,wid):
    r=c.post(f'/api/workspaces/{wid}/actions',json={'kind':'google.gmail.draft','payload':{'to':'private@example.test','subject':'TEST draft','body':'PRIVATE BODY'}})
    assert r.status_code==200,r.text;return r.json()

def test_worker_completion_notifies_requester_once_without_email(people,monkeypatch):
    cs,us,wid=people;join(people,1);c=cs[1]
    monkeypatch.setattr(providers,'available',lambda _: {'ready':True,'provider':'xai'})
    monkeypatch.setattr(providers,'run',lambda *a,**k:{'text':'TEST complete','provider':'xai','usage':{'cost_in_usd_ticks':50000}})
    before=db.query('SELECT count(*) n FROM mail_outbox',one=True)['n']
    r=c.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'TEST complete a report','idempotency_key':'notification-worker-test','start_conversation':True});assert r.status_code==200,r.text
    j=r.json();assert feed(c)['items']==[]
    claimed=worker.claim();assert claimed['id']==j['id'];assert feed(c)['items']==[]
    worker.process(claimed)
    f=feed(c);assert f['unread_count']==1 and len(f['items'])==1
    n=f['items'][0];assert n['title']=='İşin tamamlandı' and 'job='+j['id'] in n['href']
    assert feed(cs[0])['items']==[] and feed(cs[2])['items']==[]
    db.query('UPDATE jobs SET updated=? WHERE id=?',(db.now(),j['id']))
    db.query("UPDATE jobs SET status='completed' WHERE id=?",(j['id'],))
    assert len(feed(c)['items'])==1
    assert c.get(f"/api/workspaces/{wid}/jobs/{j['id']}").json()['conversation_id']==j['conversation_id']
    opened=c.post('/api/notifications/'+n['id']+'/open');assert opened.json()['href']==n['href']
    assert feed(c)['unread_count']==0 and feed(c)['items'][0]['read']
    assert db.query('SELECT count(*) n FROM mail_outbox',one=True)['n']==before

def test_failed_job_safe_notice_and_atomic_source_rollback(people):
    cs,us,wid=people;c=cs[0];jid=job(wid,us[0]['id'],'running')
    with pytest.raises(RuntimeError):
        with db.connection() as conn:
            conn.execute("UPDATE jobs SET status='failed' WHERE id=?",(jid,));raise RuntimeError('rollback fixture')
    assert feed(c)['items']==[]
    db.query("UPDATE jobs SET status='failed' WHERE id=?",(jid,))
    n=feed(c)['items'][0];assert n['state']=='failed' and 'PRIVATE RAW ERROR' not in json.dumps(n)
    assert c.post('/api/notifications/'+n['id']+'/open').status_code==200
    assert db.query('SELECT status FROM jobs WHERE id=?',(jid,),one=True)['status']=='failed'
    db.query("UPDATE jobs SET status='cancelled' WHERE id=?",(jid,))
    assert feed(c)['items'][0]['state']=='cancelled' and len(feed(c)['items'])==1
    assert c.post('/api/notifications/'+n['id']+'/open').status_code==404

def test_action_revision_and_completion_recipients(people):
    cs,us,wid=people;join(people,1);connection(wid,us[0]['id']);a=action(cs[0],wid)
    n=feed(cs[0])['items'][0];assert n['kind']=='action' and n['state']=='pending'
    assert all(s not in json.dumps(n) for s in ['PRIVATE BODY','private@example.test','TEST PRIVATE TOKEN','payload_hash'])
    assert feed(cs[1])['items']==[]
    cs[0].post('/api/notifications/'+n['id']+'/open')
    assert db.query('SELECT status FROM actions WHERE id=?',(a['id'],),one=True)['status']=='pending'
    changed=cs[0].put(f"/api/workspaces/{wid}/actions/{a['id']}",json={'payload_hash':a['payload_hash'],'payload':{**a['payload'],'subject':'TEST edited'}}).json()
    f=feed(cs[0]);assert f['unread_count']==1 and f['items'][0]['summary']=='TEST edited'
    assert cs[0].post('/api/notifications/'+n['id']+'/open').status_code==404
    # Fixture completion proves recipients only; no Google write is performed.
    jid=job(wid,us[1]['id']);db.query("UPDATE actions SET job_id=?,status='completed',updated=? WHERE id=?",(jid,db.now(),a['id']))
    for c in cs[:2]:
        f=feed(c,'?kind=action');assert len(f['items'])==1 and f['items'][0]['state']=='completed'
    assert feed(cs[2])['items']==[]
    assert changed['payload_hash']!=a['payload_hash']

def test_client_assignment_feedback_exact_content_and_stale_approval(people):
    cs,us,wid=people;join(people,2,'reviewer');join(people,3,'reviewer');p=post(people);r=request_review(people,p)
    n=feed(cs[2])['items'][0];assert n['kind']=='review' and 'review='+r['id'] in n['href']
    assert feed(cs[0])['items']==[] and feed(cs[3])['items']==[]
    assert 'INTERNAL PROMPT' not in json.dumps(n)
    assert cs[2].get(f"/api/workspaces/{wid}/client-reviews/{r['id']}").status_code==200
    assert cs[3].get(f"/api/workspaces/{wid}/client-reviews/{r['id']}").status_code==404
    assert cs[2].post('/api/notifications/'+n['id']+'/open').status_code==200
    assert decision(people,r,'changes_requested','Make it shorter').status_code==200
    owner=feed(cs[0])['items'][0];assert 'content='+p['id'] in owner['href'] and owner['state']=='changes_requested'
    assert feed(cs[2])['items']==[] and cs[2].post('/api/notifications/'+n['id']+'/open').status_code==404
    current=cs[0].get(f"/api/workspaces/{wid}/content/{p['id']}").json()['post']
    r2=request_review(people,current);assert decision(people,r2).status_code==200
    approved=next(n for n in feed(cs[0])['items'] if n['state']=='approved')
    detail=cs[0].get(f"/api/workspaces/{wid}/content/{p['id']}").json()['post']
    payload={k:detail[k] for k in ['title','platform','format','caption','visual_brief','planned_local','timezone','asset_id']}
    edited=cs[0].put(f"/api/workspaces/{wid}/content/{p['id']}",json={**payload,'caption':'Changed after approval','expected_revision':detail['revision']});assert edited.status_code==200,edited.text
    assert cs[0].post('/api/notifications/'+approved['id']+'/open').status_code==404
    assert any(n['state']=='stale' for n in feed(cs[2])['items'])

def test_removed_members_lose_badge_open_read_and_exact_sources(people):
    cs,us,wid=people;join(people,1);join(people,2,'reviewer')
    jid=job(wid,us[1]['id']);p=post(people);r=request_review(people,p)
    for index in [1,2]:
        c=cs[index];n=feed(c)['items'][0]
        assert cs[0].delete(f"/api/workspaces/{wid}/members/{us[index]['id']}").status_code==200
        assert feed(c)['items']==[] and feed(c)['unread_count']==0
        assert c.post('/api/notifications/'+n['id']+'/open').status_code==404
        assert c.post('/api/notifications/read',json={'ids':[n['id']]}).status_code==404
    assert cs[1].get(f'/api/workspaces/{wid}/jobs/{jid}').status_code==404
    assert cs[2].get(f"/api/workspaces/{wid}/client-reviews/{r['id']}").status_code==404

def test_keyset_ties_new_arrivals_read_batch_and_filters(people):
    cs,us,wid=people;c=cs[0];at=db.now()
    with db.connection() as conn:
        for _ in range(65):job(wid,us[0]['id'],at=at,conn=conn)
    f=feed(c);ids=[n['id'] for n in f['items']];assert len(ids)==30 and f['unread_count']==65
    job(wid,us[0]['id'],at=at+1,prompt='NEW ARRIVAL')
    second=feed(c,'?cursor='+f['next_cursor']);third=feed(c,'?cursor='+second['next_cursor'])
    assert len(second['items'])==30 and len(third['items'])==5 and third['next_cursor'] is None
    assert len(set(ids+[n['id'] for n in second['items']+third['items']]))==65
    assert c.post('/api/notifications/read',json={'ids':ids+[ids[0]]}).json()['marked']==30
    assert c.post('/api/notifications/read',json={'ids':ids}).json()['marked']==0
    unreads=feed(c,'?unread=true&kind=job');assert unreads['unread_count']==36 and unreads['items'][0]['summary']=='NEW ARRIVAL'
    assert not any(n['read'] for n in unreads['items'])
    assert feed(c,'?kind=action')['items']==[] and feed(c,'?kind=action')['unread_count']==36

def test_forged_batch_rolls_back_all_and_deleted_source_disappears(people):
    cs,us,wid=people;c=cs[0];jid=job(wid,us[0]['id']);n=feed(c)['items'][0]
    assert c.post('/api/notifications/read',json={'ids':[n['id'],db.uid()]}).status_code==404
    assert feed(c)['unread_count']==1
    assert cs[3].post('/api/notifications/read',json={'ids':[n['id']]}).status_code==404
    assert cs[3].post('/api/notifications/'+n['id']+'/open').status_code==404
    db.query('DELETE FROM jobs WHERE id=?',(jid,));assert feed(c)['items']==[]
    assert c.post('/api/notifications/'+n['id']+'/open').status_code==404

def test_retention_does_not_delete_sources_and_install_is_idempotent(people):
    cs,us,wid=people;old=job(wid,us[0]['id'],at=db.now()-91*86400)
    new=job(wid,us[0]['id']);db.init();db.init()
    assert len(feed(cs[0])['items'])==1 and feed(cs[0])['retention_days']==90
    assert db.query('SELECT id FROM jobs WHERE id=?',(old,),one=True)
    assert db.query('SELECT count(*) n FROM notifications',one=True)['n']==1
    assert cs[0].get(f'/api/workspaces/{wid}/jobs/{new}').status_code==200

def test_old_targets_outside_first_pages_are_scoped(people):
    cs,us,wid=people;join(people,1);join(people,2,'reviewer');connection(wid,us[0]['id'])
    jid=job(wid,us[0]['id'],at=db.now()-1000);a=action(cs[0],wid)
    with db.connection() as conn:
        for _ in range(102):job(wid,us[0]['id'],conn=conn)
        # Same valid payload, distinct IDs and no provider call.
        template=dict(conn.execute('SELECT * FROM actions WHERE id=?',(a['id'],)).fetchone())
        for _ in range(101):
            row={**template,'id':db.uid(),'created':db.now(),'updated':db.now()}
            conn.execute('INSERT INTO actions('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',list(row.values()))
    assert jid not in [r['id'] for r in cs[0].get(f'/api/workspaces/{wid}/jobs').json()]
    assert a['id'] not in [r['id'] for r in cs[0].get(f'/api/workspaces/{wid}/actions').json()]
    for c in cs[:2]:
        assert c.get(f'/api/workspaces/{wid}/jobs/{jid}').status_code==200
        assert c.get(f"/api/workspaces/{wid}/actions/{a['id']}").status_code==200
    for c in cs[2:]:
        assert c.get(f'/api/workspaces/{wid}/jobs/{jid}').status_code==404
        assert c.get(f"/api/workspaces/{wid}/actions/{a['id']}").status_code==404
    # Wrong workspace must not turn an ID into a cross-tenant lookup.
    other=cs[0].post('/api/workspaces',json={'name':'Other','kind':'personal'}).json()['id']
    assert cs[0].get(f'/api/workspaces/{other}/jobs/{jid}').status_code==404
    assert cs[0].get(f"/api/workspaces/{other}/actions/{a['id']}").status_code==404

def test_auth_verification_csrf_validation_and_bad_cursors(people):
    cs,us,wid=people;c=cs[0];job(wid,us[0]['id']);nid=feed(c)['items'][0]['id']
    for raw in ['***','a','true','[true,"'+'a'*32+'"]','[NaN,"'+'a'*32+'"]']:
        cursor=raw if raw in ['***','a'] else base64.urlsafe_b64encode(raw.encode()).decode()
        assert c.get('/api/notifications',params={'cursor':cursor}).status_code==400
    assert c.get('/api/notifications?kind=secret').status_code==422
    assert c.post('/api/notifications/read',json={'ids':[]}).status_code==422
    assert c.post('/api/notifications/read',json={'ids':[nid]*101}).status_code==422
    assert c.post('/api/notifications/'+nid+'/open',headers={'Origin':'https://attacker.invalid'}).status_code==403
    db.query('UPDATE account_state SET verified_at=NULL WHERE user_id=?',(us[0]['id'],))
    assert c.get('/api/notifications').status_code==403
    assert c.post('/api/notifications/'+nid+'/open').status_code==403
    with TestClient(app,headers={'Origin':ORIGIN}) as anon:assert anon.get('/api/notifications').status_code==401

def test_exact_old_review_and_image_content_target(people):
    cs,us,wid=people;join(people,2,'reviewer');p=post(people);r=request_review(people,p)
    with db.connection() as conn:
        template=dict(conn.execute('SELECT * FROM client_reviews WHERE id=?',(r['id'],)).fetchone())
        for _ in range(52):
            row={**template,'id':db.uid(),'status':'cancelled','created':db.now(),'updated':db.now()}
            conn.execute('INSERT INTO client_reviews('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',list(row.values()))
    assert r['id'] not in [row['id'] for row in cs[2].get(f'/api/workspaces/{wid}/client-reviews').json()['items']]
    assert cs[2].get(f"/api/workspaces/{wid}/client-reviews/{r['id']}").json()['content']['title']==p['title']
    jid=job(wid,us[0]['id'],'running')
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET task_kind='image' WHERE id=?",(jid,))
        conn.execute('INSERT INTO image_requests VALUES(?,?,?,?,?,?)',(jid,wid,p['id'],p['revision'],'PRIVATE image prompt','1:1'))
        conn.execute("UPDATE jobs SET status='completed' WHERE id=?",(jid,))
    n=feed(cs[0])['items'][0];assert 'view=content' in n['href'] and 'content='+p['id'] in n['href'] and 'PRIVATE image prompt' not in json.dumps(n)

def test_account_wide_inbox_preserves_workspace_and_live_assignment(people):
    cs,us,wid=people;c=cs[0];jid=job(wid,us[0]['id'])
    other=c.post('/api/workspaces',json={'name':'Second area','kind':'personal'}).json()['id'];job(other,us[0]['id'])
    assert {n['workspace_id'] for n in feed(c)['items']}=={wid,other}
    join(people,2,'reviewer');join(people,3,'reviewer');p=post(people);r=request_review(people,p);n=feed(cs[2])['items'][0]
    # Assignment is not currently editable via API, but historical/maintenance changes
    # must not leave a notice authorized to a previous reviewer.
    db.query('UPDATE client_reviews SET reviewer_id=? WHERE id=?',(us[3]['id'],r['id']))
    assert feed(cs[2])['items']==[] and cs[2].post('/api/notifications/'+n['id']+'/open').status_code==404
    assert cs[2].get(f"/api/workspaces/{wid}/client-reviews/{r['id']}").status_code==404
