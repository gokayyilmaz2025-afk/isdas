"""Shared workspace tasks, reviewed plans and durable in-app reminders."""
import datetime,hashlib,json,threading
from typing import Literal
from zoneinfo import ZoneInfo
from fastapi import Depends,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from . import db,content,membership,runtime

MAX_TASKS=5000
class TaskData(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=200)
    notes:str=Field(default='',max_length=5000)
    due_local:str|None=Field(default=None,pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')
    timezone:str=Field(default='Europe/Istanbul',max_length=80)
    priority:Literal['low','normal','high']='normal'
    estimate_minutes:int|None=Field(default=None,ge=5,le=1440)
    remind_before:int|None=Field(default=None)
    assignee_id:str|None=Field(default=None,pattern='^[a-f0-9]{32}$')
    @field_validator('title')
    @classmethod
    def title_valid(cls,v):
        if not v.strip():raise ValueError('Yapılacak işin adını yaz.')
        return v.strip()
    @model_validator(mode='after')
    def dates(self):
        content.planned_utc(self.due_local,self.timezone)
        if self.remind_before not in [None,0,5,15,30,60,1440]:raise ValueError('Geçerli bir hatırlatma zamanı seç.')
        if self.remind_before is not None and self.due_local is None:raise ValueError('Hatırlatma için tarih ve saat seç.')
        return self
class CreateIn(TaskData):request_key:str=Field(min_length=12,max_length=128)
class EditIn(TaskData):expected_revision:int=Field(ge=1)
class StateIn(BaseModel):
    expected_revision:int=Field(ge=1)
    action:Literal['done','reopen','archive','restore']
class ApplyIn(BaseModel):items:list[TaskData]=Field(min_length=1,max_length=20)
class EventIn(BaseModel):
    title:str=Field(min_length=1,max_length=200)
    notes:str=Field(default='',max_length=5000)
    starts_local:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')
    ends_local:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')
    timezone:str=Field(max_length=80)
    expected_revision:int|None=Field(default=None,ge=1)
    request_key:str|None=Field(default=None,min_length=12,max_length=128)

def install(conn):
    for sql in [
        "CREATE TABLE IF NOT EXISTS agenda_plans(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),user_id TEXT NOT NULL REFERENCES users(id),title TEXT NOT NULL,items TEXT NOT NULL,created REAL NOT NULL,applied_at REAL,applied_by TEXT,applied_hash TEXT,task_ids TEXT NOT NULL DEFAULT '[]')",
        "CREATE TABLE IF NOT EXISTS agenda_tasks(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id),assignee_id TEXT NOT NULL REFERENCES users(id),plan_id TEXT REFERENCES agenda_plans(id),title TEXT NOT NULL,notes TEXT NOT NULL,due_local TEXT,timezone TEXT NOT NULL,due_at REAL,priority TEXT NOT NULL,estimate_minutes INTEGER,remind_before INTEGER,remind_at REAL,status TEXT NOT NULL DEFAULT 'open',revision INTEGER NOT NULL DEFAULT 1,reminder_generation INTEGER NOT NULL DEFAULT 1,reminder_sent INTEGER NOT NULL DEFAULT 0,completed_at REAL,created REAL NOT NULL,updated REAL NOT NULL,request_key TEXT,request_hash TEXT,UNIQUE(workspace_id,user_id,request_key))",
        'CREATE INDEX IF NOT EXISTS agenda_workspace_due ON agenda_tasks(workspace_id,status,due_at,id)',
        'CREATE INDEX IF NOT EXISTS agenda_reminder_queue ON agenda_tasks(remind_at) WHERE status=\'open\' AND remind_at IS NOT NULL AND reminder_generation!=reminder_sent',
        'CREATE INDEX IF NOT EXISTS agenda_plan_workspace ON agenda_plans(workspace_id,created)',
        'CREATE TABLE IF NOT EXISTS agenda_event_requests(workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id),request_key TEXT NOT NULL,event_id TEXT REFERENCES events(id) ON DELETE SET NULL,request_hash TEXT NOT NULL,PRIMARY KEY(workspace_id,user_id,request_key))'
    ]:conn.execute(sql)
    if 'revision' not in {r['name'] for r in conn.execute('PRAGMA table_info(events)')}:conn.execute('ALTER TABLE events ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')

def member(conn,wid,uid):return membership.member(conn,wid,uid,('owner','member'))
def get(conn,wid,tid):
    row=conn.execute('SELECT t.*,u.name AS assignee_name FROM agenda_tasks t JOIN users u ON u.id=t.assignee_id WHERE t.id=? AND t.workspace_id=?',(tid,wid)).fetchone()
    if not row:raise HTTPException(404,'Yapılacak iş bulunamadı.')
    return dict(row)
def public(row):return {k:v for k,v in dict(row).items() if k not in {'request_hash','request_key','reminder_sent'}}
def values(body,actor):
    data=body.model_dump(exclude={'request_key','expected_revision'});data['assignee_id']=data['assignee_id'] or actor
    iso=content.planned_utc(data['due_local'],data['timezone']);data['due_at']=datetime.datetime.fromisoformat(iso).timestamp() if iso else None
    data['remind_at']=data['due_at']-data['remind_before']*60 if data['remind_before'] is not None else None
    return data
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def capacity(conn,wid,count):
    if conn.execute('SELECT count(*) FROM agenda_tasks WHERE workspace_id=?',(wid,)).fetchone()[0]+count>MAX_TASKS:raise HTTPException(409,'Bu alanın ajanda kayıt sınırına ulaşıldı. Destek isteyebilirsin.')
def insert(conn,wid,actor,data,plan_id=None,request_key=None):
    member(conn,wid,data['assignee_id']);tid=db.uid();stamp=db.now()
    row={'id':tid,'workspace_id':wid,'user_id':actor,'plan_id':plan_id,**data,'created':stamp,'updated':stamp,'request_key':request_key,'request_hash':digest(data)}
    conn.execute('INSERT INTO agenda_tasks('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',list(row.values()))
    return public(get(conn,wid,tid))
def plan(conn,wid,pid):
    row=conn.execute('SELECT p.*,j.conversation_id FROM agenda_plans p JOIN jobs j ON j.id=p.job_id AND j.workspace_id=p.workspace_id WHERE p.id=? AND p.workspace_id=?',(pid,wid)).fetchone()
    if not row:raise HTTPException(404,'Ajanda planı bulunamadı.')
    result=dict(row);result['items']=json.loads(result['items']);result['task_ids']=json.loads(result['task_ids']);result.pop('applied_hash',None)
    return result
def normalize_plan(payload):
    if not isinstance(payload,dict) or set(payload)!={'title','items'} or not isinstance(payload['title'],str) or not 1<=len(payload['title'].strip())<=150:raise ValueError('Invalid agenda plan')
    if not isinstance(payload['items'],list) or not 1<=len(payload['items'])<=20:raise ValueError('Invalid task count')
    items=[]
    for item in payload['items']:
        if not isinstance(item,dict) or 'assignee_id' in item:raise ValueError('Model cannot assign people')
        items.append(TaskData(**item).model_dump(exclude={'assignee_id'}))
    return {'title':payload['title'].strip(),'items':items}
def save_plan(conn,job,data):
    data=normalize_plan(data)
    conn.execute('INSERT INTO agenda_plans(id,workspace_id,job_id,user_id,title,items,created) VALUES(?,?,?,?,?,?,?)',(db.uid(),job['workspace_id'],job['id'],job['user_id'],data['title'],json.dumps(data['items'],ensure_ascii=False),db.now()))
def plan_tool():
    properties={'title':{'type':'string','maxLength':200},'notes':{'type':'string','maxLength':5000},'due_local':{'type':['string','null'],'description':'YYYY-MM-DDTHH:mm; bilinmiyorsa null'},'timezone':{'type':'string'},'priority':{'type':'string','enum':['low','normal','high']},'estimate_minutes':{'type':['integer','null'],'minimum':5,'maximum':1440},'remind_before':{'type':['integer','null'],'enum':[None,0,5,15,30,60,1440]}}
    return {'type':'function','name':'prepare_agenda_plan','description':'Kullanıcının inceleyip seçerek ajandasına ekleyebileceği yapılacaklar planı hazırlar. İşleri otomatik eklemez; hatırlatma başlatmaz.','parameters':{'type':'object','additionalProperties':False,'required':['title','items'],'properties':{'title':{'type':'string','maxLength':150},'items':{'type':'array','minItems':1,'maxItems':20,'items':{'type':'object','additionalProperties':False,'required':list(properties),'properties':properties}}}}}
def context(wid,uid):
    with db.connection() as conn:
        tasks=[{k:r[k] for k in ['id','title','due_local','timezone','priority','estimate_minutes','status']} for r in conn.execute("SELECT * FROM agenda_tasks WHERE workspace_id=? AND assignee_id=? AND status='open' ORDER BY due_at IS NULL,due_at,id LIMIT 20",(wid,uid))]
    return {'assigned_open_tasks':tasks,'selected_excerpt_only':True,'changes_require_user_review':True}

def tick_reminders():
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if runtime.recovery_hold(conn):return 0
        rows=conn.execute("SELECT t.* FROM agenda_tasks t JOIN memberships m ON m.workspace_id=t.workspace_id AND m.user_id=t.assignee_id AND m.role IN ('owner','member') WHERE t.status='open' AND t.remind_at<=? AND t.reminder_generation!=t.reminder_sent ORDER BY t.remind_at,t.id LIMIT 200",(db.now(),)).fetchall()
        for t in rows:
            conn.execute('INSERT OR IGNORE INTO notifications(id,user_id,workspace_id,kind,source_id,state,revision,created) VALUES(?,?,?,?,?,?,?,?)',(db.uid(),t['assignee_id'],t['workspace_id'],'agenda',t['id'],'reminder',str(t['reminder_generation']),db.now()))
            conn.execute('UPDATE agenda_tasks SET reminder_sent=reminder_generation WHERE id=?',(t['id'],))
        return len(rows)
def start():
    stop=threading.Event()
    def loop():
        while not stop.is_set():
            try:tick_reminders()
            except Exception:pass
            stop.wait(5)
    thread=threading.Thread(target=loop,daemon=True,name='isdas-agenda');thread.start();return thread,stop

def register_routes(app,user):
    @app.get('/api/workspaces/{wid}/agenda')
    def listing(wid,day:datetime.date,scope:Literal['today','week','all','done','archived']='today',mine:bool=False,q:str=Query('',max_length=100),offset:int=Query(0,ge=0),u=Depends(user)):
        with db.connection() as conn:
            member(conn,wid,u['id']);ws=conn.execute('SELECT timezone FROM workspaces WHERE id=?',(wid,)).fetchone();zone=ZoneInfo(ws['timezone'])
            start=datetime.datetime.combine(day,datetime.time.min,tzinfo=zone).timestamp();end=datetime.datetime.combine(day+datetime.timedelta(days=1),datetime.time.min,tzinfo=zone).timestamp();weekend=datetime.datetime.combine(day+datetime.timedelta(days=7),datetime.time.min,tzinfo=zone).timestamp()
            base='t.workspace_id=?'+(' AND t.assignee_id=?' if mine else '');args=[wid]+([u['id']] if mine else [])
            status=scope if scope in {'done','archived'} else 'open';where=base+' AND t.status=?';params=args+[status]
            if scope in {'today','week'}:where+=' AND (t.due_at IS NULL OR t.due_at<?)';params.append(end if scope=='today' else weekend)
            if q:where+=' AND (instr(lower(t.title),lower(?))>0 OR instr(lower(t.notes),lower(?))>0)';params.extend([q,q])
            rows=conn.execute('SELECT t.*,a.name AS assignee_name FROM agenda_tasks t JOIN users a ON a.id=t.assignee_id WHERE '+where+" ORDER BY CASE WHEN t.status='open' THEN 0 ELSE 1 END,t.due_at IS NULL,t.due_at,CASE t.priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,t.id LIMIT 51 OFFSET ?",(*params,offset)).fetchall()
            totals=conn.execute("SELECT count(*) AS open,sum(due_at<?) AS overdue,sum(due_at>=? AND due_at<?) AS today,sum(due_at IS NULL) AS undated,sum(due_at<?) AS due_by FROM agenda_tasks t WHERE "+base+" AND status='open'",(db.now(),start,end,end,*args)).fetchone()
            people=[dict(r) for r in conn.execute("SELECT u.id,u.name FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.role IN ('owner','member') ORDER BY u.name",(wid,))]
            plans=[plan(conn,wid,r['id']) for r in conn.execute('SELECT id FROM agenda_plans WHERE workspace_id=? AND applied_at IS NULL ORDER BY created DESC LIMIT 20',(wid,))]
            events=[dict(r) for r in conn.execute("SELECT * FROM events WHERE workspace_id=? AND julianday(starts_at)<julianday(?,'unixepoch') AND julianday(ends_at)>julianday(?,'unixepoch') ORDER BY julianday(starts_at),id",(wid,end,start))]
        return {'items':[public(r) for r in rows[:50]],'next_offset':offset+50 if len(rows)>50 else None,'summary':{k:v or 0 for k,v in dict(totals).items()},'members':people,'plans':plans,'events':events,'timezone':ws['timezone'],'self_id':u['id'],'visibility':'workspace','max_tasks':MAX_TASKS}

    @app.get('/api/workspaces/{wid}/agenda/tasks/{tid}')
    def detail(wid,tid,u=Depends(user)):
        with db.connection() as conn:member(conn,wid,u['id']);return public(get(conn,wid,tid))

    @app.post('/api/workspaces/{wid}/agenda/tasks')
    def create(wid,body:CreateIn,u=Depends(user)):
        data=values(body,u['id'])
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');member(conn,wid,u['id'])
            old=conn.execute('SELECT * FROM agenda_tasks WHERE workspace_id=? AND user_id=? AND request_key=?',(wid,u['id'],body.request_key)).fetchone()
            if old:
                if old['request_hash']!=digest(data):raise HTTPException(409,'Bu istek başka bir kayıt için kullanıldı.')
                return public(get(conn,wid,old['id']))
            capacity(conn,wid,1);return insert(conn,wid,u['id'],data,request_key=body.request_key)

    @app.put('/api/workspaces/{wid}/agenda/tasks/{tid}')
    def edit(wid,tid,body:EditIn,u=Depends(user)):
        data=values(body,u['id'])
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');member(conn,wid,u['id']);old=get(conn,wid,tid);member(conn,wid,data['assignee_id'])
            if old['revision']!=body.expected_revision:raise HTTPException(409,'Bu iş değişti. Son sürümü açıp tekrar düzenle.')
            if old['status']=='archived':raise HTTPException(409,'Önce işi arşivden çıkar.')
            reset=any(old[k]!=data[k] for k in ['due_at','remind_at','assignee_id'])
            conn.execute('UPDATE agenda_tasks SET '+','.join(k+'=?' for k in data)+',revision=revision+1,reminder_generation=reminder_generation+?,updated=? WHERE id=?',(*data.values(),int(reset),db.now(),tid))
            return public(get(conn,wid,tid))

    @app.post('/api/workspaces/{wid}/agenda/tasks/{tid}/state')
    def state(wid,tid,body:StateIn,u=Depends(user)):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');member(conn,wid,u['id']);old=get(conn,wid,tid)
            if old['revision']!=body.expected_revision:raise HTTPException(409,'Bu iş değişti. Son sürümü açıp tekrar dene.')
            allowed={'done':{'open'},'reopen':{'done'},'archive':{'open','done'},'restore':{'archived'}}
            if old['status'] not in allowed[body.action]:raise HTTPException(409,'İşin durumu bu işlem için uygun değil.')
            target={'done':'done','reopen':'open','archive':'archived','restore':'done' if old['completed_at'] else 'open'}[body.action]
            completed=db.now() if target=='done' and old['status']=='open' else None if target=='open' else old['completed_at']
            conn.execute('UPDATE agenda_tasks SET status=?,completed_at=?,revision=revision+1,reminder_generation=reminder_generation+?,updated=? WHERE id=?',(target,completed,int(target=='open'),db.now(),tid));return public(get(conn,wid,tid))

    @app.get('/api/workspaces/{wid}/agenda/plans/{pid}')
    def plan_detail(wid,pid,u=Depends(user)):
        with db.connection() as conn:member(conn,wid,u['id']);return plan(conn,wid,pid)

    @app.post('/api/workspaces/{wid}/agenda/plans/{pid}/apply')
    def apply(wid,pid,body:ApplyIn,u=Depends(user)):
        items=[values(item,u['id']) for item in body.items];checksum=digest(items)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');member(conn,wid,u['id']);row=plan(conn,wid,pid)
            if row['applied_at'] is not None:
                original=conn.execute('SELECT applied_hash FROM agenda_plans WHERE id=?',(pid,)).fetchone()[0]
                if original!=checksum:raise HTTPException(409,'Bu plan zaten ajandaya eklendi. İşleri Ajandam bölümünden düzenle.')
                return {'plan':row,'tasks':[public(get(conn,wid,tid)) for tid in row['task_ids']]}
            capacity(conn,wid,len(items));tasks=[insert(conn,wid,u['id'],item,plan_id=pid) for item in items]
            conn.execute('UPDATE agenda_plans SET applied_at=?,applied_by=?,applied_hash=?,task_ids=? WHERE id=?',(db.now(),u['id'],checksum,json.dumps([t['id'] for t in tasks]),pid))
            return {'plan':plan(conn,wid,pid),'tasks':tasks}

    @app.post('/api/workspaces/{wid}/agenda/events')
    @app.put('/api/workspaces/{wid}/agenda/events/{eid}')
    def event(wid,body:EventIn,eid:str|None=None,u=Depends(user)):
        try:start=content.planned_utc(body.starts_local,body.timezone);end=content.planned_utc(body.ends_local,body.timezone)
        except ValueError as e:raise HTTPException(400,str(e))
        if not body.title.strip() or end<=start:raise HTTPException(400,'Başlığı kontrol et; bitiş başlangıçtan sonra olmalı.')
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');member(conn,wid,u['id'])
            if eid:
                old=conn.execute('SELECT * FROM events WHERE id=? AND workspace_id=?',(eid,wid)).fetchone()
                if not old:raise HTTPException(404,'Takvim kaydı bulunamadı.')
                if body.expected_revision!=old['revision']:raise HTTPException(409,'Takvim kaydı değişti. Son sürümü açıp tekrar düzenle.')
                conn.execute('UPDATE events SET title=?,notes=?,starts_at=?,ends_at=?,revision=revision+1 WHERE id=?',(body.title.strip(),body.notes,start,end,eid))
            else:
                if not body.request_key:raise HTTPException(400,'Kayıt isteğini yenileyip tekrar dene.')
                checksum=digest([body.title.strip(),body.notes,start,end])
                old=conn.execute('SELECT * FROM agenda_event_requests WHERE workspace_id=? AND user_id=? AND request_key=?',(wid,u['id'],body.request_key)).fetchone()
                if old:
                    if not old['event_id'] or old['request_hash']!=checksum:raise HTTPException(409,'Bu istek zaten kullanıldı veya kayıt kaldırıldı.')
                    return dict(conn.execute('SELECT * FROM events WHERE id=? AND workspace_id=?',(old['event_id'],wid)).fetchone())
                eid=db.uid();conn.execute('INSERT INTO events(id,workspace_id,title,starts_at,ends_at,notes,created) VALUES(?,?,?,?,?,?,?)',(eid,wid,body.title.strip(),start,end,body.notes,db.now()))
                conn.execute('INSERT INTO agenda_event_requests VALUES(?,?,?,?,?)',(wid,u['id'],body.request_key,eid,checksum))
            return dict(conn.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone())

    @app.delete('/api/workspaces/{wid}/agenda/events/{eid}')
    def delete_event(wid,eid,expected_revision:int=Query(ge=1),u=Depends(user)):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');member(conn,wid,u['id'])
            row=conn.execute('SELECT revision FROM events WHERE id=? AND workspace_id=?',(eid,wid)).fetchone()
            if not row:raise HTTPException(404,'Takvim kaydı bulunamadı.')
            if row['revision']!=expected_revision:raise HTTPException(409,'Takvim kaydı değişti. Son sürümü açıp tekrar dene.')
            conn.execute('DELETE FROM events WHERE id=?',(eid,))
        return {'ok':True}
