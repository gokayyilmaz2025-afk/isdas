import os,json,secrets,hashlib,hmac,time,sqlite3,datetime,io,csv
from pathlib import Path
from contextlib import asynccontextmanager
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI,HTTPException,Request,Response,Depends,UploadFile,File
from fastapi.responses import FileResponse,PlainTextResponse,HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from . import db,providers,worker,usage,actions,integrations,conversations,billing,content,media,runtime,knowledge,accounts,mailer,membership,reviews,onboarding,public_site,notifications,agenda
from .catalog import ROLES,CONNECTORS

ORIGIN=os.getenv('APP_ORIGIN','http://127.0.0.1:5173').rstrip('/')
PUBLIC_ORIGIN=os.getenv('PUBLIC_ORIGIN','http://127.0.0.1:8000').rstrip('/')
attempts=defaultdict(list)
from .accounts import password_hash,verify_password,digest

def current_user(request:Request):
    token=request.cookies.get('isdas_session','')
    row=db.query('SELECT u.id,u.email,u.name FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?',(digest(token),db.now()),one=True)
    if not row:raise HTTPException(401,'Oturum açman gerekiyor.')
    with db.connection() as conn:return accounts.public_user(row,conn)
def user(request:Request):
    row=current_user(request)
    if row['verification_required'] and not row['email_verified']:raise HTTPException(403,'Devam etmek için e-posta adresini doğrula.')
    return row
def workspace(wid,u):
    result=db.query('SELECT w.*,m.role AS member_role FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE w.id=? AND m.user_id=?',(wid,u['id']),one=True)
    if not result or result['member_role'] not in {'owner','member'}:raise HTTPException(404,'Çalışma alanı bulunamadı.')
    return result
def owner(wid,u):
    result=workspace(wid,u)
    if result['member_role']!='owner':raise HTTPException(403,'Bu işlem çalışma alanı yöneticisine ait.')
    return result
def scoped(table,item_id,wid):
    if table not in {'jobs','artifacts','events','memories','routines','agents','actions','connections'}:raise ValueError('invalid table')
    row=db.query(f'SELECT * FROM {table} WHERE id=? AND workspace_id=?',(item_id,wid),one=True)
    if not row:raise HTTPException(404,'Kayıt bulunamadı.')
    return row
def session(response,u,conn):
    token=secrets.token_urlsafe(40)
    conn.execute('INSERT INTO sessions VALUES(?,?,?)',(digest(token),u['id'],db.now()+86400*7))
    response.set_cookie('isdas_session',token,httponly=True,secure=PUBLIC_ORIGIN.startswith('https://'),samesite='lax',max_age=86400*7,path='/')
def rate_auth(request):
    key=request.client.host if request.client else 'unknown';t=db.now();attempts[key]=[a for a in attempts[key] if a>t-900]
    if len(attempts[key])>=12:raise HTTPException(429,'Çok fazla deneme yapıldı. Biraz sonra yeniden dene.')
    attempts[key].append(t)
@asynccontextmanager
async def lifespan(app):
    import asyncio
    runtime.production_checks(db.DB_PATH,Path(__file__).resolve().parents[1]/'frontend'/'dist')
    lease=runtime.DatabaseLease(db.DB_PATH).acquire() if os.getenv('APP_ENV')=='production' else None
    thread=None;mail_thread=None;mail_stop=None;agenda_thread=None;agenda_stop=None
    try:
        db.init()
        if os.getenv('WORKER_ENABLED','true')=='true':
            thread=worker.start();agenda_thread,agenda_stop=agenda.start()
        if mailer.available() and os.getenv('MAIL_WORKER_ENABLED','true')=='true':mail_thread,mail_stop=mailer.start()
        app.state.agenda_thread=agenda_thread
        app.state.queue_thread=thread
        app.state.mail_thread=mail_thread
        yield
    finally:
        worker.stop.set()
        if agenda_stop:agenda_stop.set()
        if agenda_thread:await asyncio.to_thread(agenda_thread.join,30)
        if mail_stop:mail_stop.set()
        if mail_thread:await asyncio.to_thread(mail_thread.join,60)
        if thread:await asyncio.to_thread(thread.join,300)
        # Retain the lease until process exit if an in-flight call outlives drain.
        if lease:
            if (not thread or not thread.is_alive()) and (not mail_thread or not mail_thread.is_alive()) and (not agenda_thread or not agenda_thread.is_alive()):lease.close()
            else:app.state.draining_lease=lease
app=FastAPI(title='İşdaş',lifespan=lifespan,docs_url=None,redoc_url=None)
@app.middleware('http')
async def protections(request,call_next):
    if request.method not in {'GET','HEAD','OPTIONS'} and request.url.path.startswith('/api/'):
        if request.headers.get('origin') not in {ORIGIN,PUBLIC_ORIGIN}:
            return Response('İstek kaynağı doğrulanamadı.',status_code=403)
        if request.url.path not in {'/api/auth/login','/api/auth/logout'} and runtime.is_held():
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'Bakım kontrolü sürüyor. Kayıtlarını inceleyebilirsin; yeni işlemler kontrol tamamlanınca açılacak.'},status_code=503,headers={'Cache-Control':'no-store'})
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['X-Frame-Options']='DENY'
    if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
    return response

class AuthIn(BaseModel):
    email:str=Field(min_length=5,max_length=254)
    password:str=Field(min_length=10,max_length=128)
    name:str=Field(default='',max_length=80)
    invitation_token:str|None=Field(default=None,min_length=40,max_length=200)
class WorkspaceIn(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    kind:str=Field(pattern='^(personal|business|agency)$')
    sector:str=Field(default='',max_length=200)
    website:str=Field(default='',max_length=1000)
    brand_voice:str=Field(default='',max_length=3000)
    timezone:str=Field(default='Europe/Istanbul',max_length=80)
class MemoryIn(BaseModel):
    title:str=Field(min_length=1,max_length=100)
    content:str=Field(min_length=1,max_length=15000)
class AgentIn(BaseModel):
    name:str=Field(min_length=1,max_length=40)
    title:str=Field(min_length=1,max_length=80)
    prompt:str=Field(min_length=10,max_length=5000)
class JobIn(BaseModel):
    prompt:str=Field(min_length=3,max_length=12000)
    agent_id:str='guide'
    idempotency_key:str=Field(min_length=12,max_length=128)
    conversation_id:str|None=Field(default=None,pattern='^[a-f0-9]{32}$')
    start_conversation:bool=False
    document_ids:list[str]=Field(default_factory=list,max_length=5)
class ArtifactIn(BaseModel):
    title:str=Field(min_length=1,max_length=150)
    body:str=Field(max_length=100000)
class EventIn(BaseModel):
    title:str=Field(min_length=1,max_length=200)
    starts_at:datetime.datetime
    ends_at:datetime.datetime
    notes:str=Field(default='',max_length=5000)
class RoutineIn(BaseModel):
    prompt:str=Field(min_length=3,max_length=12000)
    agent_id:str='guide'
    interval_hours:int=Field(ge=1,le=8760)
    next_run:datetime.datetime
class ToggleIn(BaseModel):enabled:bool

@app.get('/api/health')
def health():return {'ok':True,'service':'isdas'}
@app.get('/api/ready')
def ready():
    try:
        with db.connection() as conn:
            conn.execute('SELECT 1 FROM users LIMIT 1')
            if runtime.recovery_hold(conn):raise RuntimeError()
        thread=getattr(app.state,'queue_thread',None)
        if os.getenv('WORKER_ENABLED','true')=='true' and (not thread or not thread.is_alive()):raise RuntimeError()
        agenda_thread=getattr(app.state,'agenda_thread',None)
        if os.getenv('WORKER_ENABLED','true')=='true' and (not agenda_thread or not agenda_thread.is_alive()):raise RuntimeError()
        mail_thread=getattr(app.state,'mail_thread',None)
        if mailer.available() and os.getenv('MAIL_WORKER_ENABLED','true')=='true' and (not mail_thread or not mail_thread.is_alive()):raise RuntimeError()
    except Exception:raise HTTPException(503,'Hizmet hazırlanıyor.')
    return {'ok':True,'service':'isdas'}
@app.post('/api/auth/register')
def register(body:AuthIn,request:Request,response:Response):
    rate_auth(request)
    email=accounts.normalize_email(body.email)
    if not accounts.registration_enabled() and not body.invitation_token:raise HTTPException(403,'Yeni kayıtlar şu anda kapalı.')
    if not body.name.strip():raise HTTPException(400,'Adını ve geçerli e-posta adresini gir.')
    if (accounts.required() or body.invitation_token) and not mailer.available():raise HTTPException(503,'E-posta hizmeti henüz hazır değil. Kayıt işlemi daha sonra açılacak.')
    item={'id':db.uid(),'email':email,'name':body.name.strip()}
    try:
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if body.invitation_token:membership.check_invitation(conn,body.invitation_token,email)
            conn.execute('INSERT INTO users VALUES(?,?,?,?,?)',(item['id'],email,item['name'],password_hash(body.password),db.now()))
            result=accounts.public_user(item,conn)
            if mailer.available():
                accounts.throttle(conn,'verify-cooldown:'+item['id'],1,60)
                accounts.issue(conn,item,'verify',ORIGIN)
            session(response,item,conn)
    except sqlite3.IntegrityError:raise HTTPException(409,'Bu e-posta kullanılıyor. Oturum açmayı dene.')
    return result
@app.post('/api/auth/login')
def login(body:AuthIn,request:Request,response:Response):
    rate_auth(request)
    with db.connection() as conn:
        # Serialize with reset so a stale password cannot create a new session
        # after a concurrent successful recovery revoked the old sessions.
        conn.execute('BEGIN IMMEDIATE')
        record=conn.execute('SELECT * FROM users WHERE email=?',(body.email.strip().lower(),)).fetchone()
        valid=accounts.verify_password(body.password,record['password'] if record else '0'*32+':'+'0'*128)
        if not record or not valid:raise HTTPException(401,'E-posta veya parola doğru değil.')
        session(response,record,conn);return accounts.public_user(record,conn)
@app.post('/api/auth/logout')
def logout(request:Request,response:Response):
    db.query('DELETE FROM sessions WHERE token=?',(digest(request.cookies.get('isdas_session','')),));response.delete_cookie('isdas_session');return {'ok':True}
@app.get('/api/me')
def me(u=Depends(current_user)):return u

accounts.register_routes(app,current_user,ORIGIN)
@app.get('/api/workspaces')
def workspaces(u=Depends(user)):
    rows=db.query("SELECT w.*,m.role AS member_role,CASE WHEN m.role='owner' THEN o.step ELSE NULL END AS setup_step FROM workspaces w JOIN memberships m ON m.workspace_id=w.id LEFT JOIN workspace_onboarding o ON o.workspace_id=w.id WHERE m.user_id=? ORDER BY w.created",(u['id'],))
    return [{k:r[k] for k in ('id','name','kind','member_role')} if r['member_role']=='reviewer' else r for r in rows]
@app.post('/api/workspaces')
def add_workspace(body:WorkspaceIn,u=Depends(user)):
    try:content.planned_utc(None,body.timezone)
    except ValueError:raise HTTPException(400,'Geçerli bir saat dilimi seç.')
    wid=db.uid()
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:billing.check_workspace_creation(conn,u['id'])
        except billing.EntitlementUnavailable as e:raise HTTPException(409,str(e))
        conn.execute('INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?)',(wid,u['id'],body.name,body.kind,body.sector,body.website,body.brand_voice,body.timezone,db.now()))
        conn.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'))
    return workspace(wid,u)
@app.put('/api/workspaces/{wid}')
def edit_workspace(wid,body:WorkspaceIn,u=Depends(user)):
    owner(wid,u)
    try:content.planned_utc(None,body.timezone)
    except ValueError:raise HTTPException(400,'Geçerli bir saat dilimi seç.')
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if body.kind!='agency' and (conn.execute("SELECT 1 FROM memberships WHERE workspace_id=? AND role='reviewer'",(wid,)).fetchone() or conn.execute("SELECT 1 FROM membership_invites WHERE workspace_id=? AND role='reviewer' AND state='pending' AND expires>?",(wid,db.now())).fetchone()):raise HTTPException(409,'Alan türünü değiştirmeden önce müşteri erişimlerini ve bekleyen müşteri davetlerini kaldır.')
        conn.execute('UPDATE workspaces SET name=?,kind=?,sector=?,website=?,brand_voice=?,timezone=? WHERE id=?',(body.name,body.kind,body.sector,body.website,body.brand_voice,body.timezone,wid))
        kit=conn.execute('SELECT payload FROM brand_profiles WHERE workspace_id=?',(wid,)).fetchone()
        if kit:
            values=json.loads(kit['payload'])
            if values['tone']!=body.brand_voice:
                values['tone']=body.brand_voice;conn.execute('UPDATE brand_profiles SET payload=?,revision=revision+1,updated=? WHERE workspace_id=?',(json.dumps(values,ensure_ascii=False),db.now(),wid))
    return workspace(wid,u)
@app.get('/api/workspaces/{wid}/overview')
def overview(wid,u=Depends(user)):
    workspace(wid,u)
    return {'runtime':providers.available(wid),'jobs':db.query('SELECT status,COUNT(*) AS count FROM jobs WHERE workspace_id=? GROUP BY status',(wid,)),'memories':db.query('SELECT COUNT(*) AS count FROM memories WHERE workspace_id=?',(wid,),one=True)['count'],'usage':db.query('SELECT usage,provider,created FROM jobs WHERE workspace_id=? AND created>?',(wid,db.now()-30*86400))}
@app.get('/api/workspaces/{wid}/agents')
def agents(wid,u=Depends(user)):
    workspace(wid,u)
    return [{k:v for k,v in r.items() if k!='prompt'} for r in ROLES]+[{**r,'icon':'sparkles','color':'blue','description':r['prompt'][:150],'custom':True} for r in db.query('SELECT * FROM agents WHERE workspace_id=?',(wid,))]

@app.get('/api/workspaces/{wid}/usage')
def workspace_usage(wid,u=Depends(user)):
    owner(wid,u);return usage.summary(wid)
@app.post('/api/workspaces/{wid}/agents')
def add_agent(wid,body:AgentIn,u=Depends(user)):
    owner(wid,u);aid=db.uid();db.query('INSERT INTO agents VALUES(?,?,?,?,?,?)',(aid,wid,body.name,body.title,body.prompt,db.now()));return {'id':aid}
@app.get('/api/workspaces/{wid}/memories')
def memories(wid,u=Depends(user)):
    workspace(wid,u);return db.query('SELECT * FROM memories WHERE workspace_id=? ORDER BY updated DESC',(wid,))
@app.post('/api/workspaces/{wid}/memories')
def add_memory(wid,body:MemoryIn,u=Depends(user)):
    workspace(wid,u);mid=db.uid();db.query('INSERT INTO memories VALUES(?,?,?,?,?,?,?)',(mid,wid,body.title,body.content,'manual',db.now(),db.now()));return {'id':mid}
@app.put('/api/workspaces/{wid}/memories/{mid}')
def edit_memory(wid,mid,body:MemoryIn,u=Depends(user)):
    workspace(wid,u);scoped('memories',mid,wid);db.query('UPDATE memories SET title=?,content=?,updated=? WHERE id=? AND workspace_id=?',(body.title,body.content,db.now(),mid,wid));return {'ok':True}
@app.delete('/api/workspaces/{wid}/memories/{mid}')
def delete_memory(wid,mid,u=Depends(user)):
    owner(wid,u);scoped('memories',mid,wid);db.query('DELETE FROM memories WHERE id=? AND workspace_id=?',(mid,wid));return {'ok':True}
@app.post('/api/workspaces/{wid}/knowledge-upload')
async def knowledge_upload(wid,file:UploadFile=File(),u=Depends(user)):
    workspace(wid,u)
    if Path(file.filename or '').suffix.lower() not in {'.txt','.csv','.md'}:raise HTTPException(400,'Şimdilik TXT, CSV veya Markdown dosyası ekleyebilirsin.')
    content=await file.read(60001)
    if len(content)>60000:raise HTTPException(413,'Dosya 60 KB altında olmalı.')
    try:text=content.decode('utf-8-sig')
    except UnicodeDecodeError:raise HTTPException(400,'Dosya UTF-8 biçiminde olmalı.')
    if not text.strip():raise HTTPException(400,'Dosya boş olmamalı.')
    if len(text)>15000:raise HTTPException(413,'Dosya 15.000 karakteri aşıyor. İçeriği daha küçük dosyalara böl; hiçbir bölümü kaydedilmedi.')
    return add_memory(wid,MemoryIn(title=Path(file.filename).name[:100],content=text),u)
def check_agent(wid,aid):
    if not any(r['id']==aid for r in ROLES) and not db.query('SELECT id FROM agents WHERE id=? AND workspace_id=?',(aid,wid),one=True):raise HTTPException(404,'Asistan bulunamadı.')
@app.get('/api/workspaces/{wid}/jobs')
def jobs(wid,u=Depends(user)):
    workspace(wid,u);return db.query('SELECT * FROM jobs WHERE workspace_id=? ORDER BY created DESC LIMIT 100',(wid,))
@app.get('/api/workspaces/{wid}/jobs/{jid}')
def job_detail(wid,jid,u=Depends(user)):
    workspace(wid,u);return scoped('jobs',jid,wid)
@app.post('/api/workspaces/{wid}/jobs')
def add_job(wid,body:JobIn,u=Depends(user)):
    workspace(wid,u);check_agent(wid,body.agent_id)
    existing=db.query('SELECT * FROM jobs WHERE workspace_id=? AND idempotency_key=?',(wid,body.idempotency_key),one=True)
    if existing:
        if not conversations.same_request(existing,body):raise HTTPException(409,'Bu işlem anahtarı farklı bir görev için kullanıldı.')
        return existing
    state=providers.available(wid)
    if not state['ready']:raise HTTPException(503,state['reason'])
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing=conn.execute('SELECT * FROM jobs WHERE workspace_id=? AND idempotency_key=?',(wid,body.idempotency_key)).fetchone()
        membership.member(conn,wid,u['id'])
        if existing:
            if not conversations.same_request(dict(existing),body):raise HTTPException(409,'Bu işlem anahtarı farklı bir görev için kullanıldı.')
            return dict(existing)
        if conn.execute("SELECT COUNT(*) FROM jobs WHERE workspace_id=? AND status IN ('queued','running')",(wid,)).fetchone()[0]>=10:raise HTTPException(429,'Devam eden işlerin tamamlanmasını bekle.')
        if len(set(body.document_ids))!=len(body.document_ids):raise HTTPException(400,'Aynı belgeyi bir kez seç.')
        try:knowledge.validate_selection(conn,wid,body.document_ids)
        except knowledge.SelectionUnavailable:raise HTTPException(404,'Seçtiğin belge bulunamadı veya kullanıma kapalı.')
        cid,sequence=conversations.allocate(conn,wid,u['id'],body)
        jid=db.uid();conn.execute('INSERT INTO jobs(id,workspace_id,user_id,agent_id,prompt,status,stage,created,updated,idempotency_key,conversation_id,turn_sequence,conversation_start) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(jid,wid,u['id'],body.agent_id,body.prompt,'queued','Sırada',db.now(),db.now(),body.idempotency_key,cid,sequence,int(body.start_conversation)))
        conn.execute('UPDATE jobs SET document_ids=? WHERE id=?',(json.dumps(body.document_ids),jid))
        try:billing.reserve(conn,{'id':jid,'workspace_id':wid},usage.reservation_for(body.agent_id,state.get('provider','xai')))
        except billing.EntitlementUnavailable as e:raise HTTPException(402,str(e))
    return scoped('jobs',jid,wid)
@app.post('/api/workspaces/{wid}/jobs/{jid}/cancel')
def cancel_job(wid,jid,u=Depends(user)):
    workspace(wid,u);scoped('jobs',jid,wid)
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT status FROM jobs WHERE id=? AND workspace_id=?',(jid,wid)).fetchone()
        if row['status'] in ['queued','running']:
            if row['status']=='queued':billing.settle(conn,jid,no_request=True)
            conn.execute("UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,stage='İptal istendi',updated=? WHERE id=? AND workspace_id=?",(db.now(),jid,wid))
    return {'ok':True}
@app.get('/api/workspaces/{wid}/artifacts')
def artifacts(wid,u=Depends(user)):
    workspace(wid,u);return db.query('SELECT * FROM artifacts WHERE workspace_id=? ORDER BY created DESC',(wid,))
@app.put('/api/workspaces/{wid}/artifacts/{aid}')
def edit_artifact(wid,aid,body:ArtifactIn,u=Depends(user)):
    workspace(wid,u);scoped('artifacts',aid,wid);db.query('UPDATE artifacts SET title=?,body=?,updated=? WHERE id=? AND workspace_id=?',(body.title,body.body,db.now(),aid,wid));return {'ok':True}
@app.get('/api/workspaces/{wid}/artifacts/{aid}/download')
def download_artifact(wid,aid,u=Depends(user)):
    workspace(wid,u);a=scoped('artifacts',aid,wid)
    return PlainTextResponse(a['body'],headers={'Content-Disposition':f'attachment; filename="isdas-{aid[:8]}.txt"'})
@app.get('/api/workspaces/{wid}/events')
def events(wid,u=Depends(user)):
    workspace(wid,u);return db.query('SELECT * FROM events WHERE workspace_id=? ORDER BY starts_at',(wid,))
@app.post('/api/workspaces/{wid}/events')
def add_event(wid,body:EventIn,u=Depends(user)):
    workspace(wid,u)
    if body.starts_at.tzinfo is None or body.ends_at.tzinfo is None:raise HTTPException(400,'Saat dilimi belirtilmeli.')
    if body.ends_at<=body.starts_at:raise HTTPException(400,'Bitiş, başlangıçtan sonra olmalı.')
    eid=db.uid();db.query('INSERT INTO events(id,workspace_id,title,starts_at,ends_at,notes,created) VALUES(?,?,?,?,?,?,?)',(eid,wid,body.title,body.starts_at.isoformat(),body.ends_at.isoformat(),body.notes,db.now()));return {'id':eid}
@app.delete('/api/workspaces/{wid}/events/{eid}')
def delete_event(wid,eid,u=Depends(user)):
    workspace(wid,u);scoped('events',eid,wid);db.query('DELETE FROM events WHERE id=? AND workspace_id=?',(eid,wid));return {'ok':True}
@app.get('/api/workspaces/{wid}/events.ics')
def calendar_export(wid,u=Depends(user)):
    rows=events(wid,u)
    def esc(v):return v.replace('\\','\\\\').replace('\n','\\n').replace('\r','').replace(';','\\;').replace(',','\\,')
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Isdas//Ajanda//TR']
    for e in rows:
        lines+=['BEGIN:VEVENT','UID:'+e['id']+'@isdas','DTSTAMP:'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'),'DTSTART:'+datetime.datetime.fromisoformat(e['starts_at']).astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'),'DTEND:'+datetime.datetime.fromisoformat(e['ends_at']).astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'),'SUMMARY:'+esc(e['title']),'DESCRIPTION:'+esc(e['notes']),'END:VEVENT']
    lines+=['END:VCALENDAR'];return Response('\r\n'.join(lines)+'\r\n',media_type='text/calendar',headers={'Content-Disposition':'attachment; filename="isdas-ajanda.ics"'})
@app.get('/api/workspaces/{wid}/routines')
def routines(wid,u=Depends(user)):
    workspace(wid,u);return db.query('SELECT * FROM routines WHERE workspace_id=? ORDER BY created DESC',(wid,))
@app.post('/api/workspaces/{wid}/routines')
def add_routine(wid,body:RoutineIn,u=Depends(user)):
    workspace(wid,u);check_agent(wid,body.agent_id)
    if not providers.available(wid)['ready']:raise HTTPException(503,'Asistan bağlantısı tamamlandıktan sonra düzenli işleri açabilirsin.')
    if body.next_run.tzinfo is None:raise HTTPException(400,'Saat dilimi belirtilmeli.')
    rid=db.uid();db.query('INSERT INTO routines VALUES(?,?,?,?,?,?,?,?,?)',(rid,wid,u['id'],body.agent_id,body.prompt,body.interval_hours,body.next_run.timestamp(),1,db.now()));return {'id':rid}
@app.put('/api/workspaces/{wid}/routines/{rid}')
def toggle_routine(wid,rid,body:ToggleIn,u=Depends(user)):
    workspace(wid,u);scoped('routines',rid,wid);db.query('UPDATE routines SET enabled=? WHERE id=? AND workspace_id=?',(int(body.enabled),rid,wid));return {'ok':True}
@app.get('/api/workspaces/{wid}/connections')
def connections(wid,u=Depends(user)):
    workspace(wid,u);existing={r['provider']:r for r in db.query('SELECT provider,account_label,updated FROM connections WHERE workspace_id=?',(wid,))}
    return [{**c,'connected':c['id'] in existing,'account':existing.get(c['id'],{}).get('account_label'),'available':c['id']=='google' and all(os.getenv(k) for k in ['GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','ENCRYPTION_KEY'])} for c in CONNECTORS]
@app.delete('/api/workspaces/{wid}/connections/{provider}')
def disconnect(wid,provider,u=Depends(user)):
    owner(wid,u);return integrations.disconnect(wid,provider,u['id'])

from .integrations import register_routes
register_routes(app,user,workspace,owner)
actions.register_routes(app,user,workspace,owner)
conversations.register_routes(app,user,workspace)
billing.register_routes(app,user,workspace)
content.register_routes(app,user,workspace,owner)
media.register_routes(app,user,workspace,owner)
knowledge.register_routes(app,user,workspace,owner)
membership.register_routes(app,user,current_user,ORIGIN)
reviews.register_routes(app,user,ORIGIN)
onboarding.register_routes(app,user)
public_site.register_routes(app)
notifications.register_routes(app,user)
agenda.register_routes(app,user)
static=Path(__file__).resolve().parents[1]/'frontend'/'dist'
if static.exists():
    app.mount('/assets',StaticFiles(directory=static/'assets'),name='assets')
    @app.get('/{path:path}')
    def ui(path:str):
        if path.startswith('api/'):raise HTTPException(404)
        candidate=(static/path).resolve()
        if candidate.is_relative_to(static.resolve()) and candidate.is_file():return FileResponse(candidate)
        if path=='app' or path.startswith(('app/','account/')):return FileResponse(static/'index.html',headers={'X-Robots-Tag':'noindex'})
        clean=path.rstrip('/')
        return HTMLResponse(public_site.page_html(static/'index.html',clean),status_code=200 if clean in public_site.PAGES else 404)
