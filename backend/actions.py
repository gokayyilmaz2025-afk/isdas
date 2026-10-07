"""Reviewed, tenant-bound external actions. The model can only prepare proposals."""
import base64,hashlib,json,re
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import quote
import httpx
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,ValidationError,field_validator,model_validator
from . import db,integrations

class CalendarEvent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=200)
    starts_at:datetime
    ends_at:datetime
    notes:str=Field(default='',max_length=5000)
    @model_validator(mode='after')
    def dates(self):
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None or self.ends_at<=self.starts_at:
            raise ValueError('Saat dilimi ve geçerli başlangıç/bitiş gerekiyor.')
        return self

class EmailDraft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    to:str=Field(min_length=3,max_length=254)
    subject:str=Field(min_length=1,max_length=200)
    body:str=Field(min_length=1,max_length=20000)
    @field_validator('to')
    @classmethod
    def address(cls,value):
        value=value.strip()
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}",value):
            raise ValueError('Tek bir e-posta adresi gerekiyor.')
        return value
    @field_validator('subject')
    @classmethod
    def header(cls,value):
        if '\r' in value or '\n' in value:raise ValueError('Başlık tek satır olmalı.')
        return value

KINDS={'google.calendar.create':CalendarEvent,'google.gmail.draft':EmailDraft}
TOOLS={'prepare_calendar_event':'google.calendar.create','prepare_email_draft':'google.gmail.draft'}
SCOPES={'google.calendar.create':integrations.CALENDAR_SCOPE,'google.gmail.draft':integrations.GMAIL_SCOPE}

def normalize(kind,payload):
    if kind not in KINDS:raise HTTPException(400,'Bu işlem desteklenmiyor.')
    try:return KINDS[kind].model_validate(payload).model_dump(mode='json')
    except (ValidationError,ValueError):raise HTTPException(400,'İşlemin alanlarını, tarihlerini ve e-posta adresini kontrol et.')

def checksum(kind,payload,connection_id):
    return hashlib.sha256(json.dumps([kind,payload,connection_id],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def public(row):
    return {**{k:row[k] for k in ('id','kind','status','payload_hash','account_label','job_id','created','updated')},'payload':json.loads(row['payload']),'result':json.loads(row['result'] or '{}')}

def get(wid,aid,conn=None):
    if conn:row=conn.execute('SELECT * FROM actions WHERE workspace_id=? AND id=?',(wid,aid)).fetchone()
    else:row=db.query('SELECT * FROM actions WHERE workspace_id=? AND id=?',(wid,aid),one=True)
    if not row:raise HTTPException(404,'İşlem bulunamadı.')
    return dict(row)

def prepare(conn,wid,kind,payload,job_id=None,expected_connection=None):
    payload=normalize(kind,payload)
    connection=conn.execute("SELECT * FROM connections WHERE workspace_id=? AND provider='google'",(wid,)).fetchone()
    if not connection or (expected_connection is not None and connection['id']!=expected_connection):
        raise HTTPException(409,'Google bağlantısı değişti. Yeni hesapla işlemi yeniden hazırla.')
    token=integrations.decode_token(connection['encrypted_token'])
    if SCOPES[kind] not in set(token.get('scope','').split()):raise HTTPException(409,'Bu işlem için Google izni gerekiyor. Bağlantıyı yeniden kur.')
    aid=db.uid();stamp=db.now();version=checksum(kind,payload,connection['id'])
    conn.execute('INSERT INTO actions(id,workspace_id,connection_id,kind,payload,payload_hash,job_id,account_label,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (aid,wid,connection['id'],kind,json.dumps(payload,ensure_ascii=False),version,job_id,connection['account_label'],stamp,stamp))
    return public(get(wid,aid,conn))

def offered_tools(wid):
    c=db.query("SELECT * FROM connections WHERE workspace_id=? AND provider='google'",(wid,),one=True)
    if not c:return [],None
    try:scopes=set(integrations.decode_token(c['encrypted_token']).get('scope','').split())
    except HTTPException:return [],None
    descriptions={'prepare_calendar_event':'Prepare a primary-calendar event for user review; no attendees or invitations. Does not create the event until approved. Require exact dates with UTC offset.',
                  'prepare_email_draft':'Prepare a plain-text Gmail draft for user review. Does not send email or write to Gmail until approved. Require the actual recipient address.'}
    return [{'type':'function','name':name,'description':descriptions[name],'parameters':KINDS[kind].model_json_schema()} for name,kind in TOOLS.items() if SCOPES[kind] in scopes],c['id']

def calendar_result(row,data):
    if data.get('id')!=row['id'] or data.get('extendedProperties',{}).get('private',{}).get('isdas_action')!=row['payload_hash']:
        raise ValueError('Unexpected event identity')
    return {'message':'Google Takvim kaydı oluşturuldu.','external_id':data['id'],'url':'https://calendar.google.com/calendar/u/0/r'}

def perform(row):
    """After claim: a failed token check is safe; any uncertain write is never replayed."""
    try:token=integrations.access_token(row['workspace_id'],row['connection_id'],SCOPES[row['kind']])
    except HTTPException as e:return 'failed',{'message':str(e.detail)}
    payload=json.loads(row['payload'])
    with httpx.Client(timeout=30,follow_redirects=False) as client:
        headers={'Authorization':'Bearer '+token}
        if row['kind']=='google.calendar.create':
            url='https://www.googleapis.com/calendar/v3/calendars/primary/events'
            body={'id':row['id'],'summary':payload['title'],'description':payload['notes'],
                  'start':{'dateTime':payload['starts_at']},'end':{'dateTime':payload['ends_at']},
                  'extendedProperties':{'private':{'isdas_action':row['payload_hash']}}}
        else:
            message=EmailMessage();message['To']=payload['to'];message['Subject']=payload['subject']
            message['Message-ID']=f"<{row['id']}@isdas.local>";message.set_content(payload['body'])
            body={'message':{'raw':base64.urlsafe_b64encode(message.as_bytes()).decode()}}
            url='https://gmail.googleapis.com/gmail/v1/users/me/drafts'
        try:
            response=client.post(url,headers=headers,json=body)
            if response.status_code in (400,401,403,404,422,429):
                return 'failed',{'message':'Google işlemi kabul etmedi. İzinleri ve içeriği kontrol ederek yeniden onaylayabilirsin.'}
            if response.is_success:
                result=response.json()
                if row['kind']=='google.calendar.create':return 'completed',calendar_result(row,result)
                if not isinstance(result.get('id'),str) or not result['id']:raise ValueError('Missing draft ID')
                return 'completed',{'message':'Gmail taslağı oluşturuldu. E-posta gönderilmedi.','external_id':result['id'],'url':'https://mail.google.com/mail/u/?authuser='+quote(row['account_label'],safe='')+'#drafts'}
        except (httpx.HTTPError,ValueError,KeyError):pass
    return 'uncertain',{'message':'Google sonucunu doğrulayamadık. Tekrar oluşturmadık. İlgili hesapta kaydı kontrol et.'}

def finish(wid,aid,status,result,uid):
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('UPDATE actions SET status=?,result=?,updated=? WHERE id=? AND workspace_id=?',(status,json.dumps(result,ensure_ascii=False),db.now(),aid,wid))
        conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),wid,uid,'action.'+status,aid,db.now()))
    return public(get(wid,aid))

def recover():
    db.query("UPDATE actions SET status='uncertain',result=?,updated=? WHERE status='executing'",(json.dumps({'message':'Sunucu işlem sırasında yeniden başladı. Kaynak hesabı kontrol et; işlem tekrarlanmadı.'},ensure_ascii=False),db.now()))

class PrepareIn(BaseModel):
    kind:str
    payload:dict
class ReviewIn(BaseModel):
    payload_hash:str=Field(min_length=64,max_length=64)
class EditIn(ReviewIn):payload:dict

def register_routes(app,user,workspace,owner):
    @app.get('/api/workspaces/{wid}/actions')
    def listing(wid,u=Depends(user)):
        workspace(wid,u);return [public(r) for r in db.query('SELECT * FROM actions WHERE workspace_id=? ORDER BY created DESC LIMIT 100',(wid,))]

    @app.get('/api/workspaces/{wid}/actions/{aid}')
    def detail(wid,aid,u=Depends(user)):
        workspace(wid,u);return public(get(wid,aid))

    @app.post('/api/workspaces/{wid}/actions')
    def create(wid,body:PrepareIn,u=Depends(user)):
        owner(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');return prepare(conn,wid,body.kind,body.payload)

    @app.put('/api/workspaces/{wid}/actions/{aid}')
    def edit(wid,aid,body:EditIn,u=Depends(user)):
        owner(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=get(wid,aid,conn)
            if row['status'] not in ('pending','failed') or row['payload_hash']!=body.payload_hash:raise HTTPException(409,'İşlem değişti. Güncel halini aç.')
            payload=normalize(row['kind'],body.payload);version=checksum(row['kind'],payload,row['connection_id'])
            conn.execute("UPDATE actions SET payload=?,payload_hash=?,status='pending',result='',updated=? WHERE id=?",(json.dumps(payload,ensure_ascii=False),version,db.now(),aid))
            return public(get(wid,aid,conn))

    @app.post('/api/workspaces/{wid}/actions/{aid}/approve')
    def approve(wid,aid,body:ReviewIn,u=Depends(user)):
        owner(wid,u)
        with integrations.workspace_lock(wid):
            with db.connection() as conn:
                conn.execute('BEGIN IMMEDIATE');row=get(wid,aid,conn)
                if row['payload_hash']!=body.payload_hash:raise HTTPException(409,'İçerik değişti. Güncel halini inceleyerek onayla.')
                if row['status']=='completed':return public(row)
                if row['status'] not in ('pending','failed'):raise HTTPException(409,'Bu işlem yeniden çalıştırılamaz. Durumunu kontrol et.')
                connection=conn.execute('SELECT id FROM connections WHERE id=? AND workspace_id=?',(row['connection_id'],wid)).fetchone()
                if not connection:raise HTTPException(409,'Bağlantı değişti. İşlemi yeniden hazırla.')
                conn.execute("UPDATE actions SET status='executing',approved_by=?,updated=? WHERE id=?",(u['id'],db.now(),aid))
            try:status,result=perform(row)
            except Exception:status,result='uncertain',{'message':'İşlemin sonucu doğrulanamadı. Kaynak hesabı kontrol et; otomatik tekrar yapılmadı.'}
            return finish(wid,aid,status,result,u['id'])

    @app.post('/api/workspaces/{wid}/actions/{aid}/reject')
    def reject(wid,aid,body:ReviewIn,u=Depends(user)):
        owner(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=get(wid,aid,conn)
            if row['status'] not in ('pending','failed') or row['payload_hash']!=body.payload_hash:raise HTTPException(409,'İşlem değişti. Güncel halini aç.')
            conn.execute("UPDATE actions SET status='rejected',updated=? WHERE id=?",(db.now(),aid))
        db.audit(wid,u['id'],'action.rejected',aid);return public(get(wid,aid))

    @app.post('/api/workspaces/{wid}/actions/{aid}/check')
    def check(wid,aid,u=Depends(user)):
        owner(wid,u)
        with integrations.workspace_lock(wid):
            row=get(wid,aid)
            if row['status']!='uncertain' or row['kind']!='google.calendar.create':raise HTTPException(409,'Bu işlemi kaynak hesapta kontrol et.')
            token=integrations.access_token(wid,row['connection_id'],SCOPES[row['kind']])
            try:
                with httpx.Client(timeout=25,follow_redirects=False) as client:
                    response=client.get('https://www.googleapis.com/calendar/v3/calendars/primary/events/'+row['id'],headers={'Authorization':'Bearer '+token})
                if response.status_code==200:return finish(wid,aid,'completed',calendar_result(row,response.json()),u['id'])
            except (httpx.HTTPError,ValueError,KeyError):pass
            return public(row)
