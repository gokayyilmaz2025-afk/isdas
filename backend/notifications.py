"""In-app notices from committed source transitions. Never sends email or push."""
import base64,json,math
from typing import Literal
from urllib.parse import urlencode
from fastapi import Depends,HTTPException,Query
from pydantic import BaseModel,Field
from . import db

KEEP_DAYS=90
class ReadIn(BaseModel):ids:list[str]=Field(min_length=1,max_length=100)

def install(conn):
    statements=[
        'CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,kind TEXT NOT NULL,source_id TEXT NOT NULL,state TEXT NOT NULL,revision TEXT NOT NULL,created REAL NOT NULL,read_at REAL,UNIQUE(user_id,kind,source_id,state,revision))',
        'CREATE INDEX IF NOT EXISTS notification_user_created ON notifications(user_id,created DESC,id DESC)',
        'CREATE INDEX IF NOT EXISTS notification_retention ON notifications(created)',
        "CREATE TRIGGER IF NOT EXISTS notification_retention_prune AFTER INSERT ON notifications BEGIN DELETE FROM notifications WHERE created<CAST(strftime('%s','now') AS REAL)-7776000; END;"
    ]
    # The notices and source change commit together, including worker completion.
    # A replay of the same transition cannot duplicate a recipient's notice.
    job_insert="""INSERT OR IGNORE INTO notifications SELECT lower(hex(randomblob(16))),NEW.user_id,NEW.workspace_id,'job',NEW.id,NEW.status,'',NEW.updated,NULL
      FROM memberships m WHERE m.workspace_id=NEW.workspace_id AND m.user_id=NEW.user_id AND m.role IN ('owner','member');"""
    action_insert="""INSERT OR IGNORE INTO notifications SELECT lower(hex(randomblob(16))),m.user_id,NEW.workspace_id,'action',NEW.id,NEW.status,NEW.payload_hash,NEW.updated,NULL
      FROM memberships m WHERE m.workspace_id=NEW.workspace_id AND (m.role='owner' OR (NEW.status!='pending' AND m.role='member' AND EXISTS(SELECT 1 FROM jobs j WHERE j.id=NEW.job_id AND j.workspace_id=NEW.workspace_id AND j.user_id=m.user_id)));"""
    review_insert="""INSERT OR IGNORE INTO notifications SELECT lower(hex(randomblob(16))),m.user_id,NEW.workspace_id,'review',NEW.id,NEW.status,CAST(NEW.post_revision AS TEXT),NEW.updated,NULL
      FROM memberships m WHERE m.workspace_id=NEW.workspace_id AND ((m.user_id=NEW.reviewer_id AND m.role='reviewer' AND NEW.status IN ('pending','stale','cancelled')) OR (m.user_id=NEW.requested_by AND m.role='owner' AND NEW.status IN ('approved','changes_requested')));"""
    for table,kind,states,body,changed in [
        ('jobs','job',"'completed','failed','blocked','interrupted','cancelled'",job_insert,'NEW.status!=OLD.status'),
        ('actions','action',"'pending','completed','failed','uncertain','rejected','invalidated'",action_insert,'NEW.status!=OLD.status OR NEW.payload_hash!=OLD.payload_hash'),
        ('client_reviews','review',"'pending','approved','changes_requested','stale','cancelled'",review_insert,'NEW.status!=OLD.status')]:
        statements.append(f"CREATE TRIGGER IF NOT EXISTS notification_{kind}_insert AFTER INSERT ON {table} WHEN NEW.status IN ({states}) BEGIN {body} END;")
        columns='status,payload_hash' if kind=='action' else 'status'
        statements.append(f"CREATE TRIGGER IF NOT EXISTS notification_{kind}_update AFTER UPDATE OF {columns} ON {table} WHEN NEW.status IN ({states}) AND ({changed}) BEGIN {body} END;")
    for sql in statements:conn.execute(sql)

# Notice payloads are reconstructed from current authorized sources. A removed
# member, changed assignment/hash, stale approval or deleted source disappears
# immediately, including from badge counts and mutation authorization.
VISIBLE="""WITH visible AS (
 SELECT n.*,w.name AS workspace_name,j.prompt AS summary,j.task_kind,
        j.conversation_id,'' AS action_kind,NULL AS content_payload,
        (SELECT i.post_id FROM image_requests i JOIN content_posts p ON p.id=i.post_id AND p.workspace_id=i.workspace_id WHERE i.job_id=j.id AND i.workspace_id=j.workspace_id) AS post_id
 FROM notifications n JOIN memberships m ON m.workspace_id=n.workspace_id AND m.user_id=n.user_id AND m.role IN ('owner','member')
 JOIN workspaces w ON w.id=n.workspace_id JOIN jobs j ON n.kind='job' AND j.id=n.source_id AND j.workspace_id=n.workspace_id AND j.user_id=n.user_id AND j.status=n.state
 WHERE n.user_id=:uid AND n.created>=:since
 UNION ALL
 SELECT n.*,w.name AS workspace_name,'' AS summary,'' AS task_kind,NULL AS conversation_id,a.kind AS action_kind,a.payload AS content_payload,NULL AS post_id
 FROM notifications n JOIN memberships m ON m.workspace_id=n.workspace_id AND m.user_id=n.user_id
 JOIN workspaces w ON w.id=n.workspace_id JOIN actions a ON n.kind='action' AND a.id=n.source_id AND a.workspace_id=n.workspace_id AND a.status=n.state AND a.payload_hash=n.revision
 WHERE n.user_id=:uid AND n.created>=:since AND (m.role='owner' OR (a.status!='pending' AND m.role='member' AND EXISTS(SELECT 1 FROM jobs j WHERE j.id=a.job_id AND j.workspace_id=a.workspace_id AND j.user_id=n.user_id)))
 UNION ALL
 SELECT n.*,w.name AS workspace_name,'' AS summary,'' AS task_kind,NULL AS conversation_id,'' AS action_kind,r.payload AS content_payload,r.post_id
 FROM notifications n JOIN memberships m ON m.workspace_id=n.workspace_id AND m.user_id=n.user_id
 JOIN workspaces w ON w.id=n.workspace_id JOIN client_reviews r ON n.kind='review' AND r.id=n.source_id AND r.workspace_id=n.workspace_id AND r.status=n.state AND CAST(r.post_revision AS TEXT)=n.revision
 JOIN content_posts p ON p.id=r.post_id AND p.workspace_id=r.workspace_id
 WHERE n.user_id=:uid AND n.created>=:since AND ((m.role='reviewer' AND r.reviewer_id=n.user_id AND r.status IN ('pending','stale','cancelled')) OR (m.role='owner' AND r.requested_by=n.user_id AND r.status IN ('approved','changes_requested')))
 UNION ALL
 SELECT n.*,w.name AS workspace_name,t.title AS summary,'' AS task_kind,NULL AS conversation_id,'' AS action_kind,NULL AS content_payload,NULL AS post_id
 FROM notifications n JOIN memberships m ON m.workspace_id=n.workspace_id AND m.user_id=n.user_id AND m.role IN ('owner','member')
 JOIN workspaces w ON w.id=n.workspace_id JOIN agenda_tasks t ON n.kind='agenda' AND t.id=n.source_id AND t.workspace_id=n.workspace_id AND t.assignee_id=n.user_id AND t.status='open' AND CAST(t.reminder_generation AS TEXT)=n.revision AND t.remind_at IS NOT NULL
 WHERE n.user_id=:uid AND n.created>=:since
 ) """

def params(uid):return {'uid':uid,'since':db.now()-KEEP_DAYS*86400}
def get(conn,uid,nid):
    row=conn.execute(VISIBLE+'SELECT * FROM visible WHERE id=:id',{**params(uid),'id':nid}).fetchone()
    if not row:raise HTTPException(404,'Bildirim bulunamadı veya artık geçerli değil.')
    return dict(row)

def present(row):
    kind,state=row['kind'],row['state'];payload=json.loads(row['content_payload'] or '{}')
    query={'workspace':row['workspace_id']}
    if kind=='job':
        titles={'completed':'İşin tamamlandı','failed':'İş tamamlanamadı','blocked':'İş başlatılamadı','interrupted':'İş kesintiye uğradı','cancelled':'İş durduruldu'}
        title=titles[state];summary=row['summary'];query.update(view='jobs',job=row['source_id'])
        if row['task_kind']=='image' and row['post_id']:query.update(view='content',content=row['post_id']);query.pop('job')
    elif kind=='action':
        titles={'pending':'Bir işlem onayını bekliyor','completed':'Hesabındaki işlem tamamlandı','failed':'İşlemi kontrol etmen gerekiyor','uncertain':'İşlemin sonucu doğrulanamadı','rejected':'İşlemden vazgeçildi','invalidated':'İşlemin bağlantısı değişti'}
        title=titles[state];summary=payload.get('title') or payload.get('subject') or 'Hesap işlemi';query.update(view='jobs',action=row['source_id'])
    elif kind=='agenda':
        title='Ajandandan bir hatırlatma';summary=row['summary'];query.update(view='calendar',task=row['source_id'])
    else:
        titles={'pending':'İncelemen için yeni içerik','approved':'Müşterin bu sürümü onayladı','changes_requested':'Müşterin değişiklik istedi','stale':'İncelediğin içerik değişti','cancelled':'İnceleme isteği kapatıldı'}
        title=titles[state];summary=payload.get('title') or 'İçerik incelemesi'
        if state in {'approved','changes_requested'}:query.update(view='content',content=row['post_id'])
        else:query.update(view='reviews',review=row['source_id'])
    return {'id':row['id'],'workspace_id':row['workspace_id'],'workspace_name':row['workspace_name'],'kind':kind,'state':state,'title':title,'summary':str(summary)[:220],'created':row['created'],'read':row['read_at'] is not None,'href':'/app?'+urlencode(query)}

def cursor_value(cursor):
    if not cursor:return None
    try:
        data=json.loads(base64.urlsafe_b64decode(cursor+'='*((-len(cursor))%4)))
        if not isinstance(data,list) or len(data)!=2 or type(data[0]) not in (int,float) or not math.isfinite(data[0]) or not isinstance(data[1],str) or len(data[1])!=32 or any(c not in '0123456789abcdef' for c in data[1]):raise ValueError()
        return data
    except (ValueError,TypeError,UnicodeError):raise HTTPException(400,'Bildirim sayfası geçersiz. En yeni bildirimlere dön.') from None

def register_routes(app,user):
    @app.get('/api/notifications')
    def listing(cursor:str=Query('',max_length=300),unread:bool=False,kind:Literal['all','job','action','review','agenda']='all',u=Depends(user)):
        before=cursor_value(cursor);p=params(u['id']);where=[]
        if before:where.append('(created<:at OR (created=:at AND id<:before))');p.update(at=before[0],before=before[1])
        if unread:where.append('read_at IS NULL')
        if kind!='all':where.append('kind=:kind');p['kind']=kind
        with db.connection() as conn:
            conn.execute('BEGIN')
            count=conn.execute(VISIBLE+'SELECT count(*) FROM visible WHERE read_at IS NULL',p).fetchone()[0]
            rows=conn.execute(VISIBLE+'SELECT * FROM visible'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY created DESC,id DESC LIMIT 31',p).fetchall()
        items=rows[:30];next_cursor=base64.urlsafe_b64encode(json.dumps([items[-1]['created'],items[-1]['id']]).encode()).decode().rstrip('=') if len(rows)>30 else None
        return {'items':[present(dict(r)) for r in items],'unread_count':count,'next_cursor':next_cursor,'retention_days':KEEP_DAYS}

    @app.post('/api/notifications/read')
    def read(body:ReadIn,u=Depends(user)):
        ids=list(dict.fromkeys(body.ids))
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for nid in ids:get(conn,u['id'],nid)
            changed=0
            for nid in ids:changed+=conn.execute('UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL',(db.now(),nid,u['id'])).rowcount
        return {'marked':changed}

    @app.post('/api/notifications/{nid}/open')
    def open_notice(nid,u=Depends(user)):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=get(conn,u['id'],nid)
            conn.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=? AND user_id=?',(db.now(),nid,u['id']))
            return {'href':present(row)['href']}
