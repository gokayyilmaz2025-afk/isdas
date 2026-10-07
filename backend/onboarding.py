"""Resumable persona setup; no model call, free credit or external write is implied."""
import hashlib,json,os,subprocess,sys,threading
from pathlib import Path
from typing import Literal
from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from . import db,content,billing,membership,accounts,runtime
from .site_reader import normalize_url,Unreadable

_readers=threading.BoundedSemaphore(2)
GOALS={'planning','content','research','sales','support','documents'}
class SetupIn(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    kind:Literal['personal','business','agency']
    sector:str=Field(default='',max_length=200)
    website:str=Field(default='',max_length=1000)
    timezone:str=Field(default='Europe/Istanbul',max_length=80)
    goal:Literal['planning','content','research','sales','support','documents']='content'
    idempotency_key:str=Field(min_length=12,max_length=128)
class StepIn(BaseModel):
    step:Literal['profile','knowledge','connections','first_task','complete']
    expected_revision:int=Field(ge=0)
    goal:Literal['planning','content','research','sales','support','documents']
class ProfileIn(content.BrandData):
    expected_revision:int=Field(ge=0)
    expected_brand_revision:int=Field(ge=0)
class SiteIn(BaseModel):url:str=Field(min_length=8,max_length=2000)
class SiteApplyIn(BaseModel):
    preview_id:str=Field(pattern='^[a-f0-9]{32}$')
    title:str=Field(min_length=1,max_length=100)
    text:str=Field(min_length=40,max_length=15000)

def read_site(url):
    try:url=normalize_url(url)
    except Unreadable as e:raise HTTPException(400,str(e))
    if not _readers.acquire(blocking=False):raise HTTPException(429,'Diğer siteler okunuyor. Biraz sonra yeniden dene.')
    try:
        env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','LANG','TEMP','TMP'}};env['PYTHONIOENCODING']='utf-8'
        executable=sys._base_executable if os.name=='nt' else sys.executable
        try:r=subprocess.run([executable,'-I',str(Path(__file__).with_name('site_reader.py'))],input=url.encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=22,env=env,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except subprocess.TimeoutExpired:raise HTTPException(422,'Siteyi okumak uzun sürdü. Daha sonra dene veya bilgilerini kendin ekle.')
        if r.returncode or len(r.stdout)>150000:raise HTTPException(422,'Site kaynak sınırları içinde okunamadı.')
        try:result=json.loads(r.stdout)
        except ValueError:raise HTTPException(422,'Site okunamadı.')
        if not result.get('ok'):raise HTTPException(422,result.get('error','Site okunamadı.'))
        return result
    finally:_readers.release()

def state(conn,wid):
    row=conn.execute('SELECT * FROM workspace_onboarding WHERE workspace_id=?',(wid,)).fetchone()
    if not row:return {'workspace_id':wid,'step':'complete','goal':'content','revision':0,'attempt_key':'','created':None,'updated':None}
    return dict(row)

def expect(conn,wid,revision):
    row=state(conn,wid)
    if row['revision']!=revision:raise HTTPException(409,'Kurulum başka bir sekmede değişti. Sayfayı yenileyip devam et.')
    return row

def register_routes(app,user):
    @app.post('/api/workspace-setup')
    def create(body:SetupIn,u=Depends(user)):
        name=body.name.strip()
        if not name:raise HTTPException(400,'Çalışma alanına bir ad ver.')
        try:content.planned_utc(None,body.timezone)
        except ValueError:raise HTTPException(400,'Geçerli bir saat dilimi seç.')
        if body.website:
            try:normalize_url(body.website)
            except Unreadable as e:raise HTTPException(400,str(e))
        values=body.model_dump(exclude={'idempotency_key'});fingerprint=hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old=conn.execute('SELECT * FROM setup_requests WHERE user_id=? AND request_key=?',(u['id'],body.idempotency_key)).fetchone()
            if old:
                if old['payload_hash']!=fingerprint:raise HTTPException(409,'Bu kurulum isteği farklı bilgilerle kullanılmış.')
                return {'workspace_id':old['workspace_id']}
            try:billing.check_workspace_creation(conn,u['id'])
            except billing.EntitlementUnavailable as e:raise HTTPException(409,str(e))
            wid=db.uid();conn.execute('INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?)',(wid,u['id'],name,body.kind,body.sector,body.website,'',body.timezone,db.now()))
            conn.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'))
            conn.execute('INSERT INTO workspace_onboarding VALUES(?,?,?,?,?,?,?)',(wid,'profile',body.goal,1,db.uid(),db.now(),db.now()))
            conn.execute('INSERT INTO setup_requests VALUES(?,?,?,?,?)',(u['id'],body.idempotency_key,fingerprint,wid,db.now()))
        return {'workspace_id':wid}

    @app.get('/api/workspaces/{wid}/onboarding')
    def get(wid,u=Depends(user)):
        with db.connection() as conn:
            membership.member(conn,wid,u['id'],('owner',));result=state(conn,wid)
            first=conn.execute("SELECT id,conversation_id,status,stage,prompt FROM jobs WHERE workspace_id=? AND idempotency_key=?",(wid,'setup:'+wid+':'+result['attempt_key'])).fetchone()
            result['first_job']=dict(first) if first else None
            result['documents']=conn.execute('SELECT count(*) FROM documents WHERE workspace_id=?',(wid,)).fetchone()[0]
            result['memories']=conn.execute('SELECT count(*) FROM memories WHERE workspace_id=?',(wid,)).fetchone()[0]
            result['first_result_ready']=conn.execute("SELECT 1 FROM jobs WHERE workspace_id=? AND status='completed' LIMIT 1",(wid,)).fetchone() is not None
        return {**result,'brand':content.brand(wid)}

    @app.put('/api/workspaces/{wid}/onboarding')
    def advance(wid,body:StepIn,u=Depends(user)):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');membership.member(conn,wid,u['id'],('owner',));row=expect(conn,wid,body.expected_revision)
            if not row['revision']:
                conn.execute('INSERT INTO workspace_onboarding VALUES(?,?,?,?,?,?,?)',(wid,body.step,body.goal,1,db.uid(),db.now(),db.now()))
            else:conn.execute('UPDATE workspace_onboarding SET step=?,goal=?,revision=revision+1,updated=? WHERE workspace_id=?',(body.step,body.goal,db.now(),wid))
            return state(conn,wid)

    @app.put('/api/workspaces/{wid}/onboarding/profile')
    def profile(wid,body:ProfileIn,u=Depends(user)):
        values=body.model_dump(exclude={'expected_revision','expected_brand_revision'})
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');membership.member(conn,wid,u['id'],('owner',));row=expect(conn,wid,body.expected_revision)
            old=conn.execute('SELECT revision FROM brand_profiles WHERE workspace_id=?',(wid,)).fetchone();revision=old['revision'] if old else 0
            if revision!=body.expected_brand_revision:raise HTTPException(409,'Profil bilgilerin değişti. Sayfayı yenileyip son halini incele.')
            content.check_asset(conn,wid,{'asset_id':values['logo_asset_id']})
            conn.execute('INSERT INTO brand_profiles VALUES(?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision,updated=excluded.updated',(wid,json.dumps(values,ensure_ascii=False),revision+1,db.now()))
            conn.execute('UPDATE workspaces SET brand_voice=? WHERE id=?',(values['tone'],wid))
            if not row['revision']:conn.execute('INSERT INTO workspace_onboarding VALUES(?,?,?,?,?,?,?)',(wid,'knowledge','content',1,db.uid(),db.now(),db.now()))
            else:conn.execute("UPDATE workspace_onboarding SET step='knowledge',revision=revision+1,updated=? WHERE workspace_id=?",(db.now(),wid))
            return state(conn,wid)

    @app.post('/api/workspaces/{wid}/site-preview')
    def preview(wid,body:SiteIn,u=Depends(user)):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');membership.member(conn,wid,u['id'],('owner',));allowed=accounts.throttle(conn,'site-read:'+u['id'],10,3600)
        if not allowed:raise HTTPException(429,'Site okuma sınırına ulaştın. Biraz sonra yeniden dene.')
        result=read_site(body.url)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');membership.member(conn,wid,u['id'],('owner',))
            if runtime.recovery_hold(conn):raise HTTPException(503,'Bakım kontrolü sürüyor. Daha sonra yeniden dene.')
            conn.execute('DELETE FROM site_previews WHERE expires<? OR workspace_id=?',(db.now(),wid))
            pid=db.uid();conn.execute('INSERT INTO site_previews VALUES(?,?,?,?,?,?,?)',(pid,wid,u['id'],json.dumps(result,ensure_ascii=False),db.now()+3600,None,db.now()))
        return {'id':pid,**result,'saved_to_memory':False}

    @app.post('/api/workspaces/{wid}/site-preview/apply')
    def apply(body:SiteApplyIn,wid,u=Depends(user)):
        if not body.title.strip() or len(body.text.strip())<40:raise HTTPException(400,'Başlığı ve eklemek istediğin bilgiyi kontrol et.')
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');membership.member(conn,wid,u['id'],('owner',))
            row=conn.execute('SELECT * FROM site_previews WHERE id=? AND workspace_id=? AND user_id=? AND expires>?',(body.preview_id,wid,u['id'],db.now())).fetchone()
            if not row:raise HTTPException(404,'Önizleme bulunamadı veya süresi doldu. Siteyi yeniden oku.')
            if row['memory_id']:
                old=conn.execute('SELECT title,content FROM memories WHERE id=? AND workspace_id=?',(row['memory_id'],wid)).fetchone()
                if old and old['title']==body.title.strip() and old['content']==body.text.strip():return {'id':row['memory_id']}
                raise HTTPException(409,'Bu önizleme zaten kullanıldı. Kayıtlı bilgiyi Hafıza bölümünden düzenle.')
            source=json.loads(row['payload']);mid=db.uid()
            conn.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?)',(mid,wid,body.title.strip(),body.text.strip(),'website:'+source['url'],db.now(),db.now()))
            conn.execute('UPDATE site_previews SET memory_id=? WHERE id=?',(mid,row['id']))
            conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),wid,u['id'],'knowledge.site_confirmed',mid,db.now()))
        return {'id':mid}
