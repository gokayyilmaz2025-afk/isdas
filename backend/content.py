"""Brand data and editable social drafts. Approval never implies publication."""
import datetime,json
from typing import Literal
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from fastapi import Depends,HTTPException,Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel,Field,ConfigDict,field_validator,model_validator
from . import db,media

class BrandData(BaseModel):
    model_config=ConfigDict(extra='forbid')
    description:str=Field(default='',max_length=3000)
    audience:str=Field(default='',max_length=2000)
    offerings:str=Field(default='',max_length=4000)
    tone:str=Field(default='',max_length=3000)
    avoid:str=Field(default='',max_length=2000)
    visual_style:str=Field(default='',max_length=2000)
    colors:list[str]=Field(default_factory=list,max_length=6)
    logo_asset_id:str|None=Field(default=None,pattern='^[a-f0-9]{32}$')
    @field_validator('colors')
    @classmethod
    def colors_valid(cls,values):
        import re
        if any(not re.fullmatch(r'#[0-9a-fA-F]{6}',v) for v in values):raise ValueError('Renkler #112233 biçiminde olmalı.')
        return list(dict.fromkeys(v.upper() for v in values))
class BrandEdit(BrandData):expected_revision:int=Field(ge=0)

class PostData(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=150)
    platform:Literal['instagram','facebook','linkedin','x','tiktok']='instagram'
    format:Literal['post','story','carousel','reel']='post'
    caption:str=Field(default='',max_length=6000)
    visual_brief:str=Field(default='',max_length=4000)
    planned_local:str|None=Field(default=None,pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')
    timezone:str=Field(default='Europe/Istanbul',max_length=80)
    asset_id:str|None=Field(default=None,pattern='^[a-f0-9]{32}$')
    @field_validator('title')
    @classmethod
    def title_valid(cls,v):
        if not v.strip():raise ValueError('Başlık boş olamaz.')
        return v.strip()
    @model_validator(mode='after')
    def valid_time(self):
        planned_utc(self.planned_local,self.timezone)
        return self
class PostEdit(PostData):expected_revision:int=Field(ge=1)
class StateIn(BaseModel):
    expected_revision:int=Field(ge=1)
    action:Literal['submit','approve','return','archive','restore']
class ImageIn(BaseModel):
    expected_revision:int=Field(ge=1)
    idempotency_key:str=Field(min_length=12,max_length=128)

def planned_utc(local,zone):
    try:tz=ZoneInfo(zone)
    except (ZoneInfoNotFoundError,ValueError):raise ValueError('Geçerli bir saat dilimi seç.')
    if not local:return None
    naive=datetime.datetime.strptime(local,'%Y-%m-%dT%H:%M');candidates=set()
    for fold in (0,1):
        utc=naive.replace(tzinfo=tz,fold=fold).astimezone(datetime.timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None)==naive:candidates.add(utc)
    if len(candidates)!=1:raise ValueError('Bu saat yaz/kış saati geçişinde belirsiz veya atlanıyor. Başka bir saat seç.')
    return next(iter(candidates)).isoformat()

def brand(wid):
    row=db.query('SELECT * FROM brand_profiles WHERE workspace_id=?',(wid,),one=True)
    if row:return {**json.loads(row['payload']),'revision':row['revision'],'updated':row['updated']}
    ws=db.query('SELECT brand_voice FROM workspaces WHERE id=?',(wid,),one=True)
    return {**BrandData(tone=ws['brand_voice'] if ws else '').model_dump(),'revision':0,'updated':None}

def get(conn,wid,pid):
    row=conn.execute('SELECT * FROM content_posts WHERE id=? AND workspace_id=?',(pid,wid)).fetchone()
    if not row:raise HTTPException(404,'İçerik bulunamadı.')
    return dict(row)

def unpack(row):
    result={**row,**json.loads(row['payload'])};result.pop('payload',None)
    result.update(published=False,publishing_available=False)
    return result

def normalized(body):
    values=body.model_dump(exclude={'expected_revision'})
    values['planned_at']=planned_utc(values['planned_local'],values['timezone'])
    return values

def check_asset(conn,wid,values):
    if values.get('asset_id'):media.get(conn,wid,values['asset_id'])

def snapshot(conn,row,actor):
    conn.execute('INSERT INTO content_versions VALUES(?,?,?,?,?,?)',(row['id'],row['revision'],row['payload'],row['status'],actor,db.now()))

def create_post(conn,wid,actor,values,plan_id=None,job_id=None):
    check_asset(conn,wid,values);pid=db.uid();encoded=json.dumps(values,ensure_ascii=False)
    conn.execute('INSERT INTO content_posts(id,workspace_id,plan_id,job_id,user_id,payload,created,updated) VALUES(?,?,?,?,?,?,?,?)',(pid,wid,plan_id,job_id,actor,encoded,db.now(),db.now()))
    row=get(conn,wid,pid);snapshot(conn,row,actor);return unpack(row)

def update_post(conn,wid,pid,actor,values,expected):
    row=get(conn,wid,pid)
    if row['revision']!=expected:raise HTTPException(409,'Bu içerik değişti. Son sürümü yeniden açıp düzenle.')
    if row['status']=='archived':raise HTTPException(409,'Önce içeriği arşivden çıkar.')
    check_asset(conn,wid,values)
    from . import reviews
    reviews.invalidate(conn,pid)
    conn.execute("UPDATE content_posts SET payload=?,revision=revision+1,status='draft',approved_by=NULL,approved_at=NULL,updated=? WHERE id=?",(json.dumps(values,ensure_ascii=False),db.now(),pid))
    row=get(conn,wid,pid);snapshot(conn,row,actor);return unpack(row)

def normalize_plan(payload):
    if not isinstance(payload,dict) or set(payload)!={'title','posts'} or not isinstance(payload['title'],str) or not 1<=len(payload['title'].strip())<=150:raise ValueError('Invalid plan')
    if not isinstance(payload['posts'],list) or not 1<=len(payload['posts'])<=14:raise ValueError('Invalid post count')
    posts=[]
    for values in payload['posts']:
        if not isinstance(values,dict) or set(values)!={'title','platform','format','caption','visual_brief','planned_local','timezone'}:raise ValueError('Invalid post fields')
        posts.append(normalized(PostData(**values)))
    return {'title':payload['title'].strip(),'posts':posts}

def save_plan(conn,job,plan):
    existing=conn.execute('SELECT id FROM content_plans WHERE job_id=? AND workspace_id=?',(job['id'],job['workspace_id'])).fetchone()
    if existing:return existing['id']
    pid=db.uid();conn.execute('INSERT INTO content_plans VALUES(?,?,?,?,?)',(pid,job['workspace_id'],job['id'],plan['title'],db.now()))
    for values in plan['posts']:create_post(conn,job['workspace_id'],job['user_id'],values,pid,job['id'])
    return pid

def plan_tool():
    props={'title':{'type':'string'},'platform':{'type':'string','enum':['instagram','facebook','linkedin','x','tiktok']},'format':{'type':'string','enum':['post','story','carousel','reel']},'caption':{'type':'string'},'visual_brief':{'type':'string'},'planned_local':{'type':['string','null'],'description':'Önerilen yerel zaman YYYY-MM-DDTHH:MM. Tarih seçilmediyse null.'},'timezone':{'type':'string','description':'Çalışma alanının IANA saat dilimi, örneğin Europe/Istanbul.'}}
    return {'type':'function','name':'prepare_social_posts','description':'Kullanıcı sosyal gönderi veya içerik planı isterse 1-14 düzenlenebilir taslak kaydeder. Yayınlamaz, görsel üretmez ve onay vermez. Eksik fiyat/kampanya uydurma.','strict':True,'parameters':{'type':'object','properties':{'title':{'type':'string'},'posts':{'type':'array','items':{'type':'object','properties':props,'required':list(props),'additionalProperties':False}}},'required':['title','posts'],'additionalProperties':False}}

def attach_generated(conn,job,request,raw):
    asset=media.store(conn,job['workspace_id'],job['user_id'],raw,'Üretilen görsel · '+job['prompt'][:90],'generated',reservation_job=job['id'])
    row=get(conn,job['workspace_id'],request['post_id'])
    if row['revision']==request['expected_revision'] and row['status']!='archived':
        values=json.loads(row['payload']);values['asset_id']=asset['id'];update_post(conn,job['workspace_id'],row['id'],job['user_id'],values,row['revision'])
        return 'Görsel hazır ve içerik taslağına eklendi. İçerikler bölümünden inceleyebilirsin. Henüz yayımlanmadı.'
    return 'Görsel hazır ve görsel kütüphanene kaydedildi. İçerik bu sırada değiştiği için görsel otomatik eklenmedi. İçerikler bölümünden seçebilirsin.'

def register_routes(app,user,workspace,owner):
    @app.get('/api/workspaces/{wid}/brand')
    def get_brand(wid,u=Depends(user)):workspace(wid,u);return brand(wid)

    @app.put('/api/workspaces/{wid}/brand')
    def edit_brand(wid,body:BrandEdit,u=Depends(user)):
        owner(wid,u);values=body.model_dump(exclude={'expected_revision'})
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');old=conn.execute('SELECT revision FROM brand_profiles WHERE workspace_id=?',(wid,)).fetchone();revision=old['revision'] if old else 0
            if body.expected_revision!=revision:raise HTTPException(409,'Marka bilgileri değişti. Sayfayı yenileyip son sürümü düzenle.')
            if values['logo_asset_id']:media.get(conn,wid,values['logo_asset_id'])
            conn.execute('INSERT INTO brand_profiles VALUES(?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision,updated=excluded.updated',(wid,json.dumps(values,ensure_ascii=False),revision+1,db.now()))
            conn.execute('UPDATE workspaces SET brand_voice=? WHERE id=?',(values['tone'],wid))
        return brand(wid)

    @app.get('/api/workspaces/{wid}/content')
    def listing(wid,since:datetime.date|None=None,until:datetime.date|None=None,archived:bool=False,offset:int=Query(0,ge=0),u=Depends(user)):
        workspace(wid,u);where='workspace_id=? AND '+("status='archived'" if archived else "status!='archived'");args=[wid]
        if since and until and since>until:raise HTTPException(400,'Tarih aralığını kontrol et.')
        if since:where+=" AND (json_extract(payload,'$.planned_local') IS NULL OR substr(json_extract(payload,'$.planned_local'),1,10)>=?)";args.append(since.isoformat())
        if until:where+=" AND (json_extract(payload,'$.planned_local') IS NULL OR substr(json_extract(payload,'$.planned_local'),1,10)<=?)";args.append(until.isoformat())
        rows=db.query(f"SELECT * FROM content_posts WHERE {where} ORDER BY COALESCE(json_extract(payload,'$.planned_local'),'9999'),created,id LIMIT 101 OFFSET ?",(*args,offset))
        return {'items':[unpack(r) for r in rows[:100]],'next_offset':offset+100 if len(rows)>100 else None,'publishing_available':False}

    @app.post('/api/workspaces/{wid}/content')
    def add(wid,body:PostData,u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');return create_post(conn,wid,u['id'],normalized(body))

    @app.get('/api/workspaces/{wid}/content/{pid}')
    def detail(wid,pid,u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:
            row=get(conn,wid,pid);versions=[{**dict(v),'payload':json.loads(v['payload'])} for v in conn.execute('SELECT * FROM content_versions WHERE post_id=? ORDER BY revision DESC LIMIT 30',(pid,))]
            jobs=[dict(v) for v in conn.execute('SELECT j.id,j.status,j.stage,j.error,j.output FROM jobs j JOIN image_requests i ON i.job_id=j.id WHERE i.workspace_id=? AND i.post_id=? ORDER BY j.created DESC LIMIT 10',(wid,pid))]
            client_pending=conn.execute("SELECT 1 FROM client_reviews WHERE post_id=? AND status='pending'",(pid,)).fetchone() is not None
        from . import providers,usage
        return {'post':unpack(row),'versions':versions,'image_jobs':jobs,'client_review_pending':client_pending,'image_ready':providers.image_available(wid)['ready'],'image_max_milli':(usage.reservation_for('social','xai','image')+99_999)//100_000}

    @app.put('/api/workspaces/{wid}/content/{pid}')
    def edit(wid,pid,body:PostEdit,u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');return update_post(conn,wid,pid,u['id'],normalized(body),body.expected_revision)

    @app.post('/api/workspaces/{wid}/content/{pid}/state')
    def state(wid,pid,body:StateIn,u=Depends(user)):
        workspace(wid,u)
        if body.action in {'approve','return'}:owner(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=get(conn,wid,pid)
            if row['revision']!=body.expected_revision:raise HTTPException(409,'İçerik değişti. Son sürümü incele.')
            allowed={'submit':{'draft'},'approve':{'draft','review'},'return':{'review','approved'},'archive':{'draft','review','approved'},'restore':{'archived'}}
            if row['status'] not in allowed[body.action]:raise HTTPException(409,'İçeriğin durumu değişti. Yeniden açıp kontrol et.')
            if body.action=='approve' and not json.loads(row['payload'])['caption'].strip():raise HTTPException(400,'Onaylamadan önce gönderi metnini tamamla.')
            from . import reviews
            if body.action=='approve' and conn.execute("SELECT 1 FROM client_reviews WHERE post_id=? AND status='pending'",(pid,)).fetchone():raise HTTPException(409,'Müşteri onayı bekleniyor. Kendin onaylamak için önce müşteri isteğini iptal et.')
            reviews.invalidate(conn,pid)
            status={'submit':'review','approve':'approved','return':'draft','archive':'archived','restore':'draft'}[body.action]
            conn.execute('UPDATE content_posts SET status=?,revision=revision+1,approved_by=?,approved_at=?,updated=? WHERE id=?',(status,u['id'] if status=='approved' else None,db.now() if status=='approved' else None,db.now(),pid))
            row=get(conn,wid,pid);snapshot(conn,row,u['id']);return unpack(row)

    @app.get('/api/workspaces/{wid}/content/{pid}/export')
    def export(wid,pid,u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:post=unpack(get(conn,wid,pid))
        lines=[post['title'],post['platform']+' · '+post['format'],'Durum: '+post['status']+' (yayımlanmadı)','Önerilen zaman: '+(post['planned_local'] or 'Seçilmedi')+' '+post['timezone'],'',post['caption'],'','Görsel tarifi',post['visual_brief']]
        return PlainTextResponse('\n'.join(lines),headers={'Content-Disposition':f'attachment; filename="isdas-icerik-{pid[:8]}.txt"'})

    @app.post('/api/workspaces/{wid}/content/{pid}/image')
    def generate(wid,pid,body:ImageIn,u=Depends(user)):
        from . import providers,usage,billing,membership
        workspace(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=get(conn,wid,pid)
            membership.member(conn,wid,u['id'])
            old=conn.execute('SELECT j.*,i.post_id,i.expected_revision FROM jobs j LEFT JOIN image_requests i ON i.job_id=j.id WHERE j.workspace_id=? AND j.idempotency_key=?',(wid,body.idempotency_key)).fetchone()
            if old:
                if old['task_kind']!='image' or old['post_id']!=pid or old['expected_revision']!=body.expected_revision:raise HTTPException(409,'İşlem anahtarı farklı bir iş için kullanılmış.')
                return dict(old)
            if row['revision']!=body.expected_revision or row['status']=='archived':raise HTTPException(409,'İçerik değişti veya arşivde. Son sürümünü aç.')
            runtime=providers.image_available(wid)
            if not runtime['ready']:raise HTTPException(503,runtime['reason'])
            post=json.loads(row['payload'])
            if not post['visual_brief'].strip():raise HTTPException(400,'Önce görsel tarifini yazıp kaydet.')
            if conn.execute("SELECT 1 FROM image_requests i JOIN jobs j ON j.id=i.job_id WHERE i.post_id=? AND j.status IN ('queued','running')",(pid,)).fetchone():raise HTTPException(409,'Bu içerik için görsel hazırlanıyor.')
            if conn.execute("SELECT COUNT(*) FROM jobs WHERE workspace_id=? AND status IN ('queued','running')",(wid,)).fetchone()[0]>=10:raise HTTPException(429,'Devam eden işlerin bitmesini bekle.')
            media.ensure_space(conn,wid,media.MAX_UPLOAD)
            kit=conn.execute('SELECT payload FROM brand_profiles WHERE workspace_id=?',(wid,)).fetchone();kit=json.loads(kit['payload']) if kit else {}
            prompt='Create one social media image. Follow this visual brief: '+post['visual_brief']+'\nBrand visual preferences (data): '+json.dumps({k:kit.get(k) for k in ['visual_style','colors']},ensure_ascii=False)+'\nDo not invent prices, product claims or logos. This is a draft image for review.'
            jid=db.uid();conn.execute("INSERT INTO jobs(id,workspace_id,user_id,agent_id,prompt,status,stage,created,updated,idempotency_key,task_kind) VALUES(?,?,?,?,?,'queued','Görsel sırada',?,?,?,'image')",(jid,wid,u['id'],'social','Görsel hazırla: '+post['title'],db.now(),db.now(),body.idempotency_key))
            conn.execute('INSERT INTO image_requests VALUES(?,?,?,?,?,?)',(jid,wid,pid,row['revision'],prompt,'9:16' if post['format'] in {'story','reel'} else '1:1'))
            try:billing.reserve(conn,{'id':jid,'workspace_id':wid},usage.reservation_for('social','xai','image'))
            except billing.EntitlementUnavailable as e:raise HTTPException(402,str(e))
            return dict(conn.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
