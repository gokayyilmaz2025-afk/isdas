"""Real adapters with synthetic HTTP responses; no external generation or publishing."""
import base64,io,json
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from PIL import Image,PngImagePlugin
from test_api import clients,create_space
from backend import db,providers,worker,usage,content,media,conversations

def png():
    out=io.BytesIO();info=PngImagePlugin.PngInfo();info.add_text('private-comment','strip-me')
    Image.new('RGB',(32,32),(24,90,200)).save(out,format='PNG',pnginfo=info);return out.getvalue()
def draft(**extra):return {'title':'Kahve molası','platform':'instagram','format':'post','caption':'Bir kahve molası verelim.','visual_brief':'Mavi masada beyaz kahve fincanı.','planned_local':'2026-10-12T10:00','timezone':'Europe/Istanbul','asset_id':None,**extra}
def ai_plan():return {'title':'Haftalık plan','posts':[{k:v for k,v in draft().items() if k!='asset_id'}]}
def complete():
    job=worker.claim();assert job;worker.process(job);return db.query('SELECT * FROM jobs WHERE id=?',(job['id'],),one=True)
def add(a,wid,**extra):
    r=a.post(f'/api/workspaces/{wid}/content',json=draft(**extra));assert r.status_code==200,r.text;return r.json()
def image_job(a,wid,p,**extra):return a.post(f"/api/workspaces/{wid}/content/{p['id']}/image",json={'expected_revision':p['revision'],'idempotency_key':db.uid(),**extra})

@pytest.fixture
def studio(clients,monkeypatch):
    a,b=clients;wid=create_space(a);other=create_space(b);calls=[]
    monkeypatch.setenv('XAI_API_KEY','local-test-only');monkeypatch.setenv('XAI_BASE_URL','https://model.example.test/v1')
    original=httpx.Client
    def reply(request):
        body=json.loads(request.content);calls.append((request.url.path,body))
        if request.url.path=='/v1/images/generations':return httpx.Response(200,json={'id':'image-test','data':[{'b64_json':base64.b64encode(png()).decode()}],'usage':{'cost_in_usd_ticks':400_000_000}})
        assert 'prepare_social_posts' in [t.get('name') for t in body['tools']]
        return httpx.Response(200,json={'id':'plan-test','status':'completed','output':[{'type':'function_call','name':'prepare_social_posts','arguments':json.dumps(ai_plan())}],'usage':{'cost_in_usd_ticks':50_000_000}})
    monkeypatch.setattr(providers.httpx,'Client',lambda *args,**kwargs:original(transport=httpx.MockTransport(reply)))
    return a,b,wid,other,calls

def test_brand_revision_owner_scope_and_legacy_voice_sync(studio):
    a,b,wid,other,_=studio;base=f'/api/workspaces/{wid}'
    kit=a.get(base+'/brand').json();assert kit['revision']==0
    body={k:v for k,v in kit.items() if k not in {'revision','updated'}};body.update(audience='Mahalle sakinleri',tone='Samimi ve kısa',colors=['#aa1122'],expected_revision=0)
    saved=a.put(base+'/brand',json=body);assert saved.status_code==200
    assert saved.json()['colors']==['#AA1122'] and saved.json()['revision']==1
    assert a.get('/api/workspaces').json()[0]['brand_voice']=='Samimi ve kısa'
    assert a.put(base+'/brand',json=body).status_code==409
    assert b.get(base+'/brand').status_code==404 and b.put(base+'/brand',json=body).status_code==404
    uid=b.get('/api/me').json()['id'];db.query('INSERT INTO memberships VALUES(?,?,?)',(wid,uid,'member'))
    body['expected_revision']=1;assert b.put(base+'/brand',json=body).status_code==403
    ws=a.get('/api/workspaces').json()[0];ws['brand_voice']='Yeni dil'
    assert a.put(base,json=ws).status_code==200
    assert a.get(base+'/brand').json()['tone']=='Yeni dil' and a.get(base+'/brand').json()['revision']==2

def test_private_media_decodes_reencodes_and_rejects_foreign_asset(studio):
    a,b,wid,other,_=studio
    r=a.post(f'/api/workspaces/{wid}/media',files={'file':('../../unsafe-name.png',png(),'image/png')});assert r.status_code==200
    aid=r.json()['id'];download=a.get(f'/api/workspaces/{wid}/media/{aid}')
    assert download.headers['content-type']=='image/png'
    assert 'private-comment' not in Image.open(io.BytesIO(download.content)).info
    assert b.get(f'/api/workspaces/{wid}/media/{aid}').status_code==404
    assert b.get(f'/api/workspaces/{other}/media/{aid}').status_code==404
    assert b.post(f'/api/workspaces/{other}/content',json=draft(asset_id=aid)).status_code==404
    p=add(a,wid,asset_id=aid)
    assert a.delete(f'/api/workspaces/{wid}/media/{aid}').status_code==409
    assert a.put(f"/api/workspaces/{wid}/content/{p['id']}",json={**draft(asset_id=None),'expected_revision':p['revision']}).status_code==200
    assert a.delete(f'/api/workspaces/{wid}/media/{aid}').status_code==200
    assert a.get(f'/api/workspaces/{wid}/media/{aid}').status_code==404

def test_upload_validation_and_atomic_quota(studio,monkeypatch):
    a,_,wid,_,_=studio
    for raw,mime in [(b'<svg><script>bad()</script></svg>','image/svg+xml'),(b'not an image','image/png')]:
        assert a.post(f'/api/workspaces/{wid}/media',files={'file':('fake.png',raw,mime)}).status_code==400
    clean,*_=media.normalize_image(png());monkeypatch.setattr(media,'MAX_STORAGE',len(clean))
    def upload(_):return a.post(f'/api/workspaces/{wid}/media',files={'file':('test.png',png(),'image/png')})
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(upload,range(2)))
    assert sorted(r.status_code for r in results)==[200,409]
    assert len(db.query('SELECT * FROM media_assets'))==len(db.query('SELECT * FROM media_blobs'))==1

def test_content_version_conflict_and_approval_invalidation(studio):
    a,_,wid,_,_=studio;p=add(a,wid);base=f"/api/workspaces/{wid}/content/{p['id']}"
    approved=a.post(base+'/state',json={'action':'approve','expected_revision':1}).json()
    assert approved['status']=='approved' and approved['published'] is False
    assert a.put(base,json={**draft(caption='Stale overwrite'),'expected_revision':1}).status_code==409
    edited=a.put(base,json={**draft(caption='Düzenlenmiş metin'),'expected_revision':approved['revision']}).json()
    assert edited['status']=='draft' and edited['approved_by'] is None
    history=a.get(base).json()['versions'];assert [r['revision'] for r in history]==[3,2,1]
    assert history[0]['payload']['caption']=='Düzenlenmiş metin' and history[-1]['payload']['caption']=='Bir kahve molası verelim.'
    assert a.post(base+'/state',json={'action':'approve','expected_revision':2}).status_code==409
    assert 'Düzenlenmiş metin' in a.get(base+'/export').text

def test_members_can_draft_but_only_owner_approves_and_tenants_stay_separate(studio):
    a,b,wid,other,_=studio;p=add(a,wid);path=f"/api/workspaces/{wid}/content/{p['id']}"
    assert b.get(path).status_code==404 and b.get(path+'/export').status_code==404
    assert b.get(f"/api/workspaces/{other}/content/{p['id']}").status_code==404
    assert b.post(path+'/image',json={'expected_revision':1,'idempotency_key':db.uid()}).status_code==404
    db.query('INSERT INTO memberships VALUES(?,?,?)',(wid,b.get('/api/me').json()['id'],'member'))
    assert b.post(path+'/state',json={'action':'submit','expected_revision':1}).status_code==200
    assert b.post(path+'/state',json={'action':'approve','expected_revision':2}).status_code==403
    assert a.post(path+'/state',json={'action':'approve','expected_revision':2}).status_code==200

def test_timezone_roundtrip_and_dst_gaps_are_explicit(studio):
    a,_,wid,_,_=studio;p=add(a,wid)
    assert p['planned_at']=='2026-10-12T07:00:00+00:00'
    for zone,time in [('Unknown/Zone','2026-10-12T10:00'),('America/New_York','2026-03-08T02:30'),('America/New_York','2026-11-01T01:30')]:
        assert a.post(f'/api/workspaces/{wid}/content',json=draft(timezone=zone,planned_local=time)).status_code==422
    p=add(a,wid,planned_local=None);assert p['planned_at'] is None

def test_calendar_filter_archive_and_restore(studio):
    a,_,wid,_,_=studio;inside=add(a,wid);add(a,wid,planned_local='2026-11-12T10:00');add(a,wid,planned_local=None)
    base=f'/api/workspaces/{wid}/content'
    r=a.get(base+'?since=2026-10-10&until=2026-10-15').json();assert len(r['items'])==2
    p=a.post(base+'/'+inside['id']+'/state',json={'action':'archive','expected_revision':1}).json()
    assert p['status']=='archived'
    assert len(a.get(base+'?archived=true').json()['items'])==1
    assert a.put(base+'/'+p['id'],json={**draft(),'expected_revision':p['revision']}).status_code==409
    restored=a.post(base+'/'+p['id']+'/state',json={'action':'restore','expected_revision':p['revision']}).json()
    assert restored['status']=='draft' and restored['approved_by'] is None

def test_model_plan_creates_real_editable_rows_and_current_context(studio):
    a,_,wid,_,calls=studio
    kit=a.get(f'/api/workspaces/{wid}/brand').json();kit={k:v for k,v in kit.items() if k not in {'revision','updated'}}
    a.put(f'/api/workspaces/{wid}/brand',json={**kit,'audience':'Yalnızca bu markanın hedef kitlesi','expected_revision':0})
    first=a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Bir içerik planı hazırla.','agent_id':'social','idempotency_key':db.uid(),'start_conversation':True}).json()
    done=complete();assert done['status']=='completed' and 'taslağın hazır' in done['output']
    posts=a.get(f'/api/workspaces/{wid}/content').json()['items'];assert len(posts)==1
    assert usage.summary(wid)['spent_ticks']==50_000_000
    assert 'Yalnızca bu markanın hedef kitlesi' in calls[0][1]['input'][0]['content']
    p=posts[0];a.put(f"/api/workspaces/{wid}/content/{p['id']}",json={**draft(caption='Kullanıcının son düzenlediği metin'),'expected_revision':1})
    second=a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Son düzenlediğim içeriği dikkate al.','agent_id':'social','idempotency_key':db.uid(),'conversation_id':first['conversation_id']}).json()
    ctx=conversations.context_for(second);assert ctx['resources']['content_posts'][0]['caption']=='Kullanıcının son düzenlediği metin'
    detail=a.get(f"/api/workspaces/{wid}/conversations/{first['conversation_id']}").json();assert detail['content_posts'][0]['id']==p['id']

def test_invalid_plan_keeps_cost_without_partial_posts(studio,monkeypatch):
    a,_,wid,_,_=studio;original=httpx.Client
    # Capture the unpatched class from httpx's module implementation.
    from httpx._client import Client
    payload=ai_plan();payload['posts'].append({**payload['posts'][0],'status':'published'})
    def reply(request):return httpx.Response(200,json={'id':'bad-plan','status':'completed','output':[{'type':'function_call','name':'prepare_social_posts','arguments':json.dumps(payload)}],'usage':{'cost_in_usd_ticks':123456}})
    monkeypatch.setattr(providers.httpx,'Client',lambda *args,**kwargs:Client(transport=httpx.MockTransport(reply)))
    a.post(f'/api/workspaces/{wid}/jobs',json={'prompt':'Plan hazırla.','agent_id':'social','idempotency_key':db.uid()})
    assert complete()['status']=='failed';assert db.query('SELECT * FROM content_posts')==[]
    assert usage.summary(wid)['spent_ticks']==123456

def test_image_adapter_receipt_private_asset_and_idempotency(studio):
    a,_,wid,_,calls=studio;p=add(a,wid);key=db.uid()
    first=image_job(a,wid,p,idempotency_key=key);again=image_job(a,wid,p,idempotency_key=key)
    assert first.status_code==again.status_code==200 and first.json()['id']==again.json()['id']
    assert image_job(a,wid,p).status_code==409
    done=complete();assert done['status']=='completed'
    post=a.get(f"/api/workspaces/{wid}/content/{p['id']}").json()['post'];assert post['asset_id'] and post['revision']==2
    assert a.get(f"/api/workspaces/{wid}/media/{post['asset_id']}").status_code==200
    imagecalls=[b for path,b in calls if path.endswith('/images/generations')];assert len(imagecalls)==1
    assert imagecalls[0]['response_format']=='b64_json' and imagecalls[0]['quality']=='low' and imagecalls[0]['n']==1
    assert usage.summary(wid)['spent_ticks']==400_000_000
    assert a.get(f'/api/workspaces/{wid}/billing').json()['spent_milli']==4000
    assert image_job(a,wid,p,idempotency_key=key).json()['id']==first.json()['id']

def test_generation_does_not_overwrite_a_changed_or_archived_post(studio,monkeypatch):
    a,_,wid,_,_=studio;p=add(a,wid);image_job(a,wid,p)
    def generate(request):
        a.put(f"/api/workspaces/{wid}/content/{p['id']}",json={**draft(caption='Benim yeni metnim'),'expected_revision':1})
        return {'text':'test','provider':'xai','image_bytes':png(),'usage':{'cost_in_usd_ticks':400_000_000}}
    monkeypatch.setattr(providers,'generate_image',generate);done=complete()
    post=a.get(f"/api/workspaces/{wid}/content/{p['id']}").json()['post']
    assert post['asset_id'] is None and post['caption']=='Benim yeni metnim'
    assert 'otomatik eklenmedi' in done['output'] and len(a.get(f'/api/workspaces/{wid}/media').json()['items'])==1

def test_cancelled_image_preserves_charge_but_discards_media(studio,monkeypatch):
    a,_,wid,_,_=studio;p=add(a,wid);j=image_job(a,wid,p).json()
    def generate(request):
        a.post(f"/api/workspaces/{wid}/jobs/{j['id']}/cancel")
        return {'text':'test','provider':'xai','image_bytes':png(),'usage':{'cost_in_usd_ticks':400_000_000}}
    monkeypatch.setattr(providers,'generate_image',generate);assert complete()['status']=='cancelled'
    assert db.query('SELECT * FROM media_assets')==[] and db.query('SELECT * FROM media_blobs')==[]
    assert usage.summary(wid)['spent_ticks']==400_000_000

def test_invalid_image_keeps_receipt_and_rolls_back_asset(studio,monkeypatch):
    a,_,wid,_,_=studio;p=add(a,wid);image_job(a,wid,p)
    monkeypatch.setattr(providers,'generate_image',lambda request:{'text':'test','provider':'xai','image_bytes':b'not png','usage':{'cost_in_usd_ticks':400_000_000}})
    assert complete()['status']=='failed' and db.query('SELECT * FROM media_assets')==[]
    assert usage.summary(wid)['spent_ticks']==400_000_000

def test_image_slot_reserves_storage_and_releases_it_on_cancel(studio,monkeypatch):
    a,_,wid,_,_=studio;monkeypatch.setattr(media,'MAX_STORAGE',media.MAX_UPLOAD)
    p=add(a,wid);j=image_job(a,wid,p).json();another=add(a,wid)
    assert image_job(a,wid,another).status_code==409
    assert a.post(f'/api/workspaces/{wid}/media',files={'file':('test.png',png(),'image/png')}).status_code==409
    a.post(f"/api/workspaces/{wid}/jobs/{j['id']}/cancel")
    assert image_job(a,wid,another).status_code==200

def test_no_image_provider_does_not_create_job_or_charge(studio,monkeypatch):
    a,_,wid,_,_=studio;monkeypatch.delenv('XAI_API_KEY');p=add(a,wid)
    assert image_job(a,wid,p).status_code==503
    assert db.query('SELECT * FROM image_requests')==[] and db.query('SELECT * FROM credit_ledger')==[]

def test_plan_validation_limits_posts_and_rejects_fake_publication():
    payload=ai_plan();payload['posts']*=15
    with pytest.raises(ValueError):content.normalize_plan(payload)
    payload=ai_plan();payload['posts'][0]['asset_id']='a'*32
    with pytest.raises(ValueError):content.normalize_plan(payload)
