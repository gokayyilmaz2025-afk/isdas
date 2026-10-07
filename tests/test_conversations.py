import json
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from test_api import clients,create_space
from backend import db,conversations,providers,worker
from backend.catalog import ROLES

@pytest.fixture
def chatting(clients,monkeypatch):
    a,b=clients;wid=create_space(a);calls=[]
    monkeypatch.setattr(providers,'available',lambda wid:{'ready':True,'provider':'xai'})
    def answer(workspace,role,prompt,memories,conversation=None):
        calls.append({'workspace':workspace['id'],'prompt':prompt,'conversation':conversation})
        return {'text':'Deneme yanıtı: '+prompt,'provider':'xai','usage':{'cost_in_usd_ticks':100},'request_id':'test-'+db.uid()}
    monkeypatch.setattr(providers,'run',answer)
    return a,b,wid,calls

def send(a,wid,prompt='Kahve dükkanım için içerik hazırla.',cid=None,key=None,agent='social'):
    return a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':prompt,'agent_id':agent,'idempotency_key':key or db.uid(),'start_conversation':cid is None,'conversation_id':cid})

def complete():
    job=worker.claim();assert job is not None;worker.process(job);return db.query('SELECT * FROM jobs WHERE id=?',(job['id'],),one=True)

def test_first_turn_concurrent_retries_are_atomic(chatting):
    a,_,wid,_=chatting;key=db.uid()
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:send(a,wid,key=key),range(4)))
    assert all(r.status_code==200 for r in results)
    assert len({r.json()['id'] for r in results})==1
    assert len(db.query('SELECT * FROM conversations'))==1
    assert len(db.query('SELECT * FROM jobs'))==1
    assert send(a,wid,prompt='Bu farklı bir istek.',key=key).status_code==409

def test_followup_receives_prior_dialogue_and_latest_edited_artifact(chatting):
    a,_,wid,calls=chatting;first=send(a,wid).json();cid=first['conversation_id']
    done=complete();assert done['status']=='completed'
    artifact=a.get(f'/api/workspaces/{wid}/artifacts').json()[0]
    a.put(f"/api/workspaces/{wid}/artifacts/{artifact['id']}",json={'title':'Son içerik','body':'Kullanıcının düzenlediği güncel metin.'})
    second=send(a,wid,'Bunu daha kısa yaz.',cid).json();assert second['turn_sequence']==2
    complete();context=calls[-1]['conversation']
    assert context['messages']==[{'role':'user','content':first['prompt']},{'role':'assistant','content':done['output']}]
    assert context['resources']['latest_artifact']['body']=='Kullanıcının düzenlediği güncel metin.'
    detail=a.get(f'/api/workspaces/{wid}/conversations/{cid}').json()
    assert [x['turn_sequence'] for x in detail['turns']]==[1,2]
    assert detail['visibility']=='workspace'

def test_conversation_requires_tenant_membership_and_matching_workspace(chatting):
    a,b,wid,_=chatting;cid=send(a,wid).json()['conversation_id'];other=create_space(b)
    assert b.get(f'/api/workspaces/{wid}/conversations').status_code==404
    for suffix in ['', '/export']:
        assert b.get(f'/api/workspaces/{wid}/conversations/{cid}'+suffix).status_code==404
        assert b.get(f'/api/workspaces/{other}/conversations/{cid}'+suffix).status_code==404
    assert send(b,other,cid=cid).status_code==404
    assert len(db.query('SELECT * FROM jobs'))==1

def test_separate_thread_never_gets_other_thread_history(chatting):
    a,_,wid,calls=chatting
    first=send(a,wid,'İlk konuşmanın ayrı konusu.').json();complete()
    second=send(a,wid,'İkinci konuşmanın ayrı konusu.').json();complete()
    assert first['conversation_id']!=second['conversation_id']
    assert calls[-1]['conversation']['messages']==[]
    assert calls[-1]['conversation']['resources']['latest_artifact'] is None

def test_next_turn_waits_for_previous_reply(chatting):
    a,_,wid,_=chatting;cid=send(a,wid).json()['conversation_id']
    assert send(a,wid,'Bunu kısalt.',cid).status_code==409
    claimed=worker.claim()
    assert send(a,wid,'Bunu kısalt.',cid).status_code==409
    worker.process(claimed)
    assert send(a,wid,'Bunu kısalt.',cid).status_code==200
    assert len(db.query('SELECT * FROM jobs'))==2

def test_failed_turn_is_not_presented_as_completed_history(chatting):
    a,_,wid,calls=chatting;first=send(a,wid).json();cid=first['conversation_id']
    db.query("UPDATE jobs SET status='failed',output='FAILED_OUTPUT_MUST_NOT_BE_USED' WHERE id=?",(first['id'],))
    send(a,wid,'Başka bir plan hazırlayalım.',cid);complete()
    history=json.dumps(calls[-1]['conversation'],ensure_ascii=False)
    assert 'FAILED_OUTPUT_MUST_NOT_BE_USED' not in history
    assert 'sonucu oluşturulmadı' in history

def test_archive_restore_rename_and_export(chatting):
    a,_,wid,_=chatting;cid=send(a,wid).json()['conversation_id'];route=f'/api/workspaces/{wid}/conversations/{cid}'
    assert a.patch(route,json={'archived':True}).status_code==409
    complete();assert a.patch(route,json={'title':'Kahve içerikleri','archived':True}).status_code==200
    assert a.get(f'/api/workspaces/{wid}/conversations').json()['items']==[]
    assert a.get(f'/api/workspaces/{wid}/conversations?archived=true').json()['items'][0]['title']=='Kahve içerikleri'
    assert send(a,wid,'Kısa sürümü hazırla.',cid).status_code==409
    assert a.patch(route,json={'archived':False}).status_code==200
    assert send(a,wid,'Kısa sürümü hazırla.',cid).status_code==200
    complete()
    exported=a.get(route+'/export')
    assert exported.status_code==200 and 'Kahve içerikleri' in exported.text and 'Kısa sürümü hazırla.' in exported.text
    assert a.patch(route,json={'title':'   '}).status_code==400

def test_workspace_members_share_chat_but_only_creator_or_owner_can_archive(chatting):
    a,b,wid,_=chatting;cid=send(a,wid).json()['conversation_id'];complete()
    uid=b.get('/api/me').json()['id'];db.query('INSERT INTO memberships VALUES(?,?,?)',(wid,uid,'member'))
    assert b.get(f'/api/workspaces/{wid}/conversations/{cid}').status_code==200
    assert b.patch(f'/api/workspaces/{wid}/conversations/{cid}',json={'archived':True}).status_code==403
    assert send(b,wid,'Bu alandaki plana devam edelim.',cid).status_code==200

def test_thread_agent_cannot_change_mid_conversation(chatting):
    a,_,wid,_=chatting;cid=send(a,wid).json()['conversation_id'];complete()
    assert send(a,wid,'Başka görev',cid,agent='research').status_code==409

def test_new_conversation_and_existing_id_cannot_be_combined(chatting):
    a,_,wid,_=chatting;cid=send(a,wid).json()['conversation_id'];complete()
    response=a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Deneme işi','agent_id':'social','idempotency_key':db.uid(),'start_conversation':True,'conversation_id':cid})
    assert response.status_code==400 and len(db.query('SELECT * FROM conversations'))==1

def test_unconfigured_provider_creates_no_empty_conversation(clients):
    a,_=clients;wid=create_space(a)
    assert send(a,wid).status_code==503
    assert db.query('SELECT * FROM conversations')==[]

def test_history_window_and_pagination_keep_order(chatting):
    a,_,wid,_=chatting;first=send(a,wid).json();cid=first['conversation_id']
    db.query("UPDATE jobs SET status='completed',output='Yanıt 1' WHERE id=?",(first['id'],))
    for seq in range(2,37):
        db.query('INSERT INTO jobs(id,workspace_id,user_id,agent_id,prompt,status,stage,output,created,updated,idempotency_key,conversation_id,turn_sequence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                 (db.uid(),wid,first['user_id'],'social',f'İstek {seq}','completed','Hazır',f'Yanıt {seq}',db.now(),db.now(),db.uid(),cid,seq))
    route=f'/api/workspaces/{wid}/conversations/{cid}'
    page=a.get(route).json();older=a.get(route+'?before='+str(page['next_before'])).json()
    assert [x['turn_sequence'] for x in page['turns']]==list(range(7,37))
    assert [x['turn_sequence'] for x in older['turns']]==list(range(1,7))
    assert not older['has_more']
    latest=send(a,wid,'Son planı kısalt.',cid).json();context=conversations.context_for(latest)
    assert context['info']=={'history_turns_used':12,'history_turns_total':36,'history_trimmed':True}
    assert context['messages'][0]['content']=='İstek 25' and context['messages'][-1]['content']=='Yanıt 36'

def test_very_long_previous_turn_is_excerpted_and_reported(chatting):
    a,_,wid,_=chatting;first=send(a,wid).json();cid=first['conversation_id']
    db.query("UPDATE jobs SET status='completed',output=? WHERE id=?",('Başlangıç '+'uzunmetin '*12000+' SON',first['id']))
    follow=send(a,wid,'Özeti hazırla.',cid).json();context=conversations.context_for(follow,limit=2000)
    assert sum(len(m['content']) for m in context['messages'])<=2000
    assert context['info']['history_trimmed'] and 'bağlama alınmadı' in context['messages'][-1]['content']
    assert context['messages'][-1]['content'].endswith(' SON')

def test_action_state_is_current_and_limited_to_thread(chatting):
    a,_,wid,_=chatting;first=send(a,wid).json();cid=first['conversation_id'];complete()
    db.query('INSERT INTO actions(id,workspace_id,kind,payload,status,created,updated,job_id) VALUES(?,?,?,?,?,?,?,?)',
             (db.uid(),wid,'google.gmail.draft',json.dumps({'subject':'Kullanıcı düzenlemesi','body':'Güncel taslak'}),'completed',db.now(),db.now(),first['id']))
    other=send(a,wid,'Diğer konuşma').json();complete()
    follow=send(a,wid,'Bu işlemin durumunu açıkla.',cid).json();context=conversations.context_for(follow)
    assert context['resources']['actions'][0]['status']=='completed'
    assert context['resources']['actions'][0]['payload']['body']=='Güncel taslak'
    other_job={**other,'turn_sequence':2};assert conversations.context_for(other_job)['resources']['actions']==[]

def test_provider_uses_explicit_history_without_shared_hermes_session(clients,monkeypatch):
    a,_=clients;wid=create_space(a);workspace=db.query('SELECT * FROM workspaces WHERE id=?',(wid,),one=True)
    real_client=httpx.Client;requests=[]
    def handle(request):requests.append(request);return httpx.Response(200,json={'choices':[{'message':{'content':'Mock yanıt'}}],'usage':{}})
    monkeypatch.setattr(httpx,'Client',lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw))
    monkeypatch.setattr(providers,'runtime_for',lambda wid:{'name':'hermes','url':'http://test-runtime.invalid/v1','api_key':'test'})
    history={'messages':[{'role':'user','content':'Önceki istek'},{'role':'assistant','content':'Önceki yanıt'}],'resources':{},'info':{}}
    providers.run(workspace,ROLES[0],'Devam et.',[],conversation=history)
    body=json.loads(requests[0].content)
    assert [m['content'] for m in body['messages'][1:]]==['Önceki istek','Önceki yanıt','Devam et.']
    assert 'X-Hermes-Session-Id' not in requests[0].headers
