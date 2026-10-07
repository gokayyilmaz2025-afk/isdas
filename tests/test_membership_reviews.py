import json,io,threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from test_accounts import account_client,signup,token,PASSWORD
from backend.main import app,ORIGIN
from backend import db,membership,accounts,mailer,worker,providers,billing,ops

@pytest.fixture
def people(account_client):
    clients=[account_client]+[TestClient(app,headers={'Origin':ORIGIN}) for _ in range(3)]
    users=[signup(c,name+'@example.test') for c,name in zip(clients,['owner','employee','customer','other'])]
    db.query('UPDATE account_state SET verified_at=?',(db.now(),))
    for u in users:billing.grant_period(u['id'],'pilot',db.now(),db.now()+14*86400,'operator_pilot',u['id'],'Test fixture: no payment, no live provider')
    r=clients[0].post('/api/workspaces',json={'name':'TEST Agency','kind':'agency','brand_voice':'INTERNAL VOICE','sector':'INTERNAL SECTOR'})
    assert r.status_code==200,r.text
    yield clients,users,r.json()['id']
    for c in clients[1:]:c.close()

def invite(people,index,role='member'):
    clients,users,wid=people
    r=clients[0].post(f'/api/workspaces/{wid}/invitations',json={'email':users[index]['email'],'role':role})
    assert r.status_code==200,r.text
    return r.json(),token('invite')

def join(people,index,role='member'):
    invitation,raw=invite(people,index,role);c=people[0][index]
    r=c.post('/api/invitations/accept',json={'token':raw});assert r.status_code==200,r.text
    return invitation,raw

def post(people,asset=None):
    clients,users,wid=people
    r=clients[0].post(f'/api/workspaces/{wid}/content',json={'title':'TEST Approved content','caption':'Customer-visible text','visual_brief':'INTERNAL PROMPT DO NOT EXPOSE','asset_id':asset})
    assert r.status_code==200,r.text
    return r.json()

def request_review(people,item,index=2):
    clients,users,wid=people
    r=clients[0].post(f"/api/workspaces/{wid}/content/{item['id']}/client-reviews",json={'reviewer_id':users[index]['id'],'expected_revision':item['revision']})
    assert r.status_code==200,r.text
    return r.json()

def decision(people,r,decision='approved',comment='',index=2):
    clients,users,wid=people
    return clients[index].post(f"/api/workspaces/{wid}/client-reviews/{r['id']}/decision",json={'decision':decision,'comment':comment,'expected_revision':r['post_revision']})

def test_invitation_is_email_bound_verified_expiring_and_single_use(people):
    clients,users,wid=people;i,raw=invite(people,1)
    assert raw not in json.dumps(i)
    preview=clients[3].post('/api/invitations/preview',json={'token':raw});assert preview.json()['email']==users[1]['email']
    assert clients[3].post('/api/invitations/accept',json={'token':raw}).status_code==400
    db.query('UPDATE account_state SET verified_at=NULL WHERE user_id=?',(users[1]['id'],))
    assert clients[1].post('/api/invitations/accept',json={'token':raw}).status_code==403
    db.query('UPDATE account_state SET verified_at=? WHERE user_id=?',(db.now(),users[1]['id']))
    assert clients[1].post('/api/invitations/accept',json={'token':raw}).status_code==200
    assert clients[1].post('/api/invitations/accept',json={'token':raw}).status_code==400
    assert clients[1].get('/api/workspaces').json()[0]['member_role']=='member'
    assert db.query('SELECT count(*) AS n FROM memberships WHERE workspace_id=?',(wid,),one=True)['n']==2

def test_owner_only_invite_and_no_owner_role_or_invite_leak(people):
    clients,users,wid=people;join(people,1)
    assert clients[1].post(f'/api/workspaces/{wid}/invitations',json={'email':users[2]['email'],'role':'member'}).status_code==404
    assert clients[0].post(f'/api/workspaces/{wid}/invitations',json={'email':users[2]['email'],'role':'owner'}).status_code==422
    invite(people,2)
    assert clients[1].get(f'/api/workspaces/{wid}/members').json()['invitations']==[]
    assert clients[3].get(f'/api/workspaces/{wid}/members').status_code==404
    assert 'token_hash' not in clients[0].get(f'/api/workspaces/{wid}/members').text

def test_revocation_expiry_password_reset_and_capacity(people,monkeypatch):
    clients,users,wid=people;i,raw=invite(people,1)
    assert clients[0].delete(f"/api/workspaces/{wid}/invitations/{i['id']}").status_code==200
    assert clients[1].post('/api/invitations/accept',json={'token':raw}).status_code==400
    i,raw=invite(people,1);db.query('UPDATE membership_invites SET expires=? WHERE id=?',(db.now()-1,i['id']))
    assert clients[1].post('/api/invitations/accept',json={'token':raw}).status_code==400
    i,raw=invite(people,1)
    with db.connection() as conn:accounts.replace_password(conn,users[0]['id'],'New-owner-password-1234')
    assert clients[1].post('/api/invitations/accept',json={'token':raw}).status_code==400
    clients[0].post('/api/auth/login',json={'email':users[0]['email'],'password':'New-owner-password-1234'})
    monkeypatch.setattr(membership,'MAX_MEMBERS',2);invite(people,1)
    assert clients[0].post(f'/api/workspaces/{wid}/invitations',json={'email':users[2]['email'],'role':'member'}).status_code==409

def test_closed_registration_accepts_only_live_invitation_for_matching_email(people,monkeypatch):
    clients,users,wid=people
    r=clients[0].post(f'/api/workspaces/{wid}/invitations',json={'email':'new@example.test','role':'reviewer'});assert r.status_code==200
    raw=token('invite');monkeypatch.setenv('REGISTRATION_ENABLED','false')
    body={'name':'New','email':'new@example.test','password':PASSWORD}
    assert clients[3].post('/api/auth/register',json=body).status_code==403
    assert clients[3].post('/api/auth/register',json={**body,'email':'wrong@example.test','invitation_token':raw}).status_code==400
    result=clients[3].post('/api/auth/register',json={**body,'invitation_token':raw})
    assert result.status_code==200 and not result.json()['email_verified']
    assert clients[3].post('/api/invitations/accept',json={'token':raw}).status_code==403
    verify=token('verify');assert clients[3].post('/api/auth/verify-email',json={'token':verify,'password':PASSWORD}).status_code==200
    assert clients[3].post('/api/invitations/accept',json={'token':raw}).json()['role']=='reviewer'

def test_two_acceptances_grant_one_membership_and_cannot_escalate(people):
    clients,users,wid=people;i,raw=invite(people,1)
    other=TestClient(app,headers={'Origin':ORIGIN});other.cookies.update(clients[1].cookies)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(c.post,'/api/invitations/accept',json={'token':raw,'role':'owner'}) for c in [clients[1],other]]
        assert sorted(f.result().status_code for f in futures)==[200,400]
    assert db.query('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,users[1]['id']),one=True)['role']=='member'
    other.close()

def test_invitation_mail_is_dispatched_and_revoked_mail_never_sends(people,monkeypatch):
    clients,users,wid=people;i,raw=invite(people,1);calls=[]
    # Discard signup verification mail for this delivery-specific check.
    db.query("UPDATE mail_outbox SET state='cancelled',payload='' WHERE purpose='verify'")
    monkeypatch.setattr(mailer,'send',lambda payload,mid:calls.append(payload))
    assert mailer.dispatch_one();assert raw in calls[0]['body']
    i,raw=invite(people,2);clients[0].delete(f"/api/workspaces/{wid}/invitations/{i['id']}")
    assert not mailer.dispatch_one();assert len(calls)==1

def test_member_removal_stops_queued_routines_and_preserves_running_cost(people,monkeypatch):
    clients,users,wid=people;join(people,1)
    monkeypatch.setattr(providers,'available',lambda _: {'ready':True,'provider':'xai'})
    jobs=[]
    for n in range(2):
        r=clients[1].post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Prepare a test report','idempotency_key':'membership-job-'+str(n)})
        assert r.status_code==200,r.text;jobs.append(r.json()['id'])
    db.query('INSERT INTO routines VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),wid,users[1]['id'],'guide','Routine test',1,db.now()-1,1,db.now()))
    running=worker.claim();assert running
    def result(*args,**kwargs):
        assert clients[0].delete(f"/api/workspaces/{wid}/members/{users[1]['id']}").status_code==200
        return {'text':'Do not persist after removal','usage':{'cost_in_usd_ticks':50000},'provider':'xai','request_id':'local-removal-receipt'}
    monkeypatch.setattr(providers,'run',result);worker.process(running)
    assert all(j['status']=='cancelled' and j['output']=='' for j in db.query('SELECT * FROM jobs'))
    assert db.query('SELECT actual_ticks,state FROM usage_ledger WHERE job_id=?',(running['id'],),one=True)=={'actual_ticks':50000,'state':'settled'}
    waiting=next(j for j in jobs if j!=running['id'])
    assert db.query('SELECT state FROM credit_ledger WHERE job_id=?',(waiting,),one=True)['state']=='released'
    assert db.query('SELECT enabled FROM routines',one=True)['enabled']==0
    worker.tick_routines();assert worker.claim() is None
    assert clients[1].get(f'/api/workspaces/{wid}/jobs').status_code==404
    assert clients[1].get('/api/workspaces').json()==[]

def test_owner_cannot_be_removed_member_can_leave_but_not_remove_others(people):
    clients,users,wid=people;join(people,1);join(people,2)
    assert clients[1].delete(f"/api/workspaces/{wid}/members/{users[2]['id']}").status_code==403
    assert clients[0].delete(f"/api/workspaces/{wid}/members/{users[0]['id']}").status_code==403
    assert clients[1].delete(f'/api/workspaces/{wid}/members/me').status_code==200
    assert clients[2].get(f'/api/workspaces/{wid}/members').status_code==200

def test_orphan_routines_and_queued_jobs_never_execute(people,monkeypatch):
    clients,users,wid=people;join(people,1)
    monkeypatch.setattr(providers,'available',lambda _: {'ready':True,'provider':'xai'})
    r=clients[1].post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Queued orphan test','idempotency_key':'orphan-job-test-key'});assert r.status_code==200
    db.query('INSERT INTO routines VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),wid,users[1]['id'],'guide','Orphan routine',1,db.now()-1,1,db.now()))
    db.query('DELETE FROM memberships WHERE workspace_id=? AND user_id=?',(wid,users[1]['id']))
    worker.tick_routines();assert worker.claim() is None
    assert db.query('SELECT count(*) AS n FROM jobs',one=True)['n']==1
    assert db.query('SELECT state FROM credit_ledger',one=True)['state']=='released'

def test_client_isolated_from_all_workspace_surfaces_and_other_reviews(people):
    clients,users,wid=people;join(people,2,'reviewer');join(people,3,'reviewer');item=post(people);r=request_review(people,item)
    ws=clients[2].get('/api/workspaces').json()[0];assert set(ws)=={'id','name','kind','member_role'}
    for route in ['overview','agents','memories','jobs','artifacts','events','events.ics','routines','connections','usage','brand','content','media','documents','members','conversations']:
        assert clients[2].get(f'/api/workspaces/{wid}/{route}').status_code==404,route
    inbox=clients[2].get(f'/api/workspaces/{wid}/client-reviews');assert inbox.status_code==200
    assert 'INTERNAL' not in inbox.text and 'visual_brief' not in inbox.text and 'user_id' not in inbox.text
    assert inbox.json()['items'][0]['content']['caption']=='Customer-visible text'
    assert clients[3].get(f'/api/workspaces/{wid}/client-reviews').json()['items']==[]
    assert decision(people,r,index=3).status_code==404
    assert clients[2].post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Try to read data','idempotency_key':'reviewer-no-jobs'}).status_code==404

def test_review_decision_applies_exact_revision_and_retries_idempotently(people):
    clients,users,wid=people;join(people,2,'reviewer');item=post(people);r=request_review(people,item)
    assert decision(people,r).status_code==200
    assert decision(people,r).status_code==200
    assert decision(people,r,'changes_requested','Change caption').status_code==409
    saved=clients[0].get(f"/api/workspaces/{wid}/content/{item['id']}").json()['post']
    assert saved['status']=='approved' and saved['approved_by']==users[2]['id'] and saved['revision']==r['post_revision']+1
    assert len(db.query("SELECT * FROM audit WHERE event='review.approved'"))==1
    assert saved['published'] is False

def test_changes_requested_and_owner_cannot_bypass_pending_review(people):
    clients,users,wid=people;join(people,2,'reviewer');item=post(people);r=request_review(people,item)
    assert clients[0].post(f"/api/workspaces/{wid}/content/{item['id']}/state",json={'action':'approve','expected_revision':r['post_revision']}).status_code==409
    assert decision(people,r,'changes_requested','').status_code==400
    assert decision(people,r,'changes_requested','Please update the opening sentence.').status_code==200
    saved=clients[0].get(f"/api/workspaces/{wid}/content/{item['id']}").json()['post'];assert saved['status']=='draft'
    history=clients[0].get(f"/api/workspaces/{wid}/content/{item['id']}/client-reviews").json()['items'];assert history[0]['comment'].startswith('Please update')
    new=request_review(people,saved);assert new['id']!=r['id']

def test_edit_invalidates_customer_approval_and_stale_editor_cannot_approve(people):
    clients,users,wid=people;join(people,2,'reviewer');item=post(people);r=request_review(people,item)
    assert decision(people,r).status_code==200
    saved=clients[0].get(f"/api/workspaces/{wid}/content/{item['id']}").json()['post']
    body={k:saved[k] for k in ['title','platform','format','caption','visual_brief','planned_local','timezone','asset_id']};body.update(caption='Changed after approval',expected_revision=saved['revision'])
    assert clients[0].put(f"/api/workspaces/{wid}/content/{item['id']}",json=body).json()['status']=='draft'
    assert decision(people,r).status_code==409
    assert clients[2].get(f'/api/workspaces/{wid}/client-reviews').json()['items'][0]['status']=='stale'

def test_pending_review_cancelled_on_reviewer_removal_and_role_change_is_explicit(people):
    clients,users,wid=people;join(people,2,'reviewer');item=post(people);r=request_review(people,item)
    assert clients[0].put(f'/api/workspaces/{wid}',json={'name':'Changed','kind':'business'}).status_code==409
    assert clients[0].post(f'/api/workspaces/{wid}/invitations',json={'email':users[2]['email'],'role':'member'}).status_code==409
    assert clients[0].delete(f"/api/workspaces/{wid}/members/{users[2]['id']}").status_code==200
    assert decision(people,r).status_code==404
    assert db.query('SELECT status FROM client_reviews',one=True)['status']=='cancelled'
    assert db.query('SELECT status FROM content_posts',one=True)['status']=='draft'
    assert clients[0].put(f'/api/workspaces/{wid}',json={'name':'Changed','kind':'business'}).status_code==200

def test_client_image_is_scoped_to_assigned_snapshot(people):
    clients,users,wid=people;join(people,2,'reviewer');join(people,3,'reviewer')
    image=io.BytesIO();Image.new('RGB',(32,32),'blue').save(image,format='PNG')
    asset=clients[0].post(f'/api/workspaces/{wid}/media',files={'file':('test.png',image.getvalue(),'image/png')}).json()['id']
    item=post(people,asset);r=request_review(people,item)
    assert clients[2].get(f"/api/workspaces/{wid}/client-reviews/{r['id']}/image").content==image.getvalue()
    assert clients[3].get(f"/api/workspaces/{wid}/client-reviews/{r['id']}/image").status_code==404
    assert clients[2].get(f'/api/workspaces/{wid}/media/{asset}').status_code==404

def test_concurrent_client_decisions_only_one_commits(people):
    clients,users,wid=people;join(people,2,'reviewer');r=request_review(people,post(people))
    other=TestClient(app,headers={'Origin':ORIGIN});other.cookies.update(clients[2].cookies)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(decision,people,r)
        b=pool.submit(other.post,f"/api/workspaces/{wid}/client-reviews/{r['id']}/decision",json={'decision':'changes_requested','comment':'Changed request','expected_revision':r['post_revision']})
        assert sorted([a.result().status_code,b.result().status_code])==[200,409]
    assert len(db.query("SELECT * FROM audit WHERE event IN ('review.approved','review.changes_requested')"))==1
    other.close()

def test_restore_revokes_invitations_and_invalidates_customer_approvals(people,monkeypatch,tmp_path):
    clients,users,wid=people;join(people,2,'reviewer');r=request_review(people,post(people));assert decision(people,r).status_code==200
    invite(people,1);monkeypatch.setenv('BACKUP_ENCRYPTION_KEY',Fernet.generate_key().decode())
    backup=tmp_path/'review-backup.enc';restored=tmp_path/'review-restored.sqlite3';ops.backup(db.DB_PATH,backup);ops.restore_candidate(backup,restored)
    monkeypatch.setattr(db,'DB_PATH',str(restored))
    assert db.query("SELECT count(*) AS n FROM membership_invites WHERE state='pending'",one=True)['n']==0
    assert db.query('SELECT status FROM client_reviews',one=True)['status']=='stale'
    assert db.query('SELECT status,approved_by FROM content_posts',one=True)=={'status':'draft','approved_by':None}

def test_member_listing_does_not_mutate_expired_invites_during_recovery_hold(people):
    clients,users,wid=people;i,raw=invite(people,1)
    db.query('UPDATE membership_invites SET expires=? WHERE id=?',(db.now()-1,i['id']))
    db.query("INSERT INTO system_state VALUES('recovery_hold','{}')")
    result=clients[0].get(f'/api/workspaces/{wid}/members')
    assert result.status_code==200 and result.json()['invitations']==[]
    assert db.query('SELECT state FROM membership_invites WHERE id=?',(i['id'],),one=True)['state']=='pending'

def test_review_inbox_paginates_history_and_filters_pending_without_exposing_other_clients(people):
    clients,users,wid=people;join(people,2,'reviewer');join(people,3,'reviewer');r=request_review(people,post(people))
    row=db.query('SELECT * FROM client_reviews WHERE id=?',(r['id'],),one=True)
    with db.connection() as conn:
        for n in range(50):
            conn.execute('INSERT INTO client_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)',(db.uid(),wid,row['post_id'],users[2]['id'],users[0]['id'],row['post_revision'],row['payload'],'changes_requested','Historical test feedback',db.now()-100-n,db.now()-100-n))
    first=clients[2].get(f'/api/workspaces/{wid}/client-reviews').json()
    assert len(first['items'])==50 and first['next_offset']==50
    second=clients[2].get(f'/api/workspaces/{wid}/client-reviews?offset=50').json()
    assert len(second['items'])==1 and second['next_offset'] is None
    assert {x['id'] for x in first['items']}.isdisjoint({x['id'] for x in second['items']})
    pending=clients[2].get(f'/api/workspaces/{wid}/client-reviews?status=pending').json()
    assert [x['id'] for x in pending['items']]==[r['id']]
    assert clients[3].get(f'/api/workspaces/{wid}/client-reviews?offset=50').json()['items']==[]
    assert clients[2].get(f'/api/workspaces/{wid}/client-reviews?offset=-1').status_code==422
