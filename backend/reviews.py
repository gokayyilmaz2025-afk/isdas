"""Assigned client approval of an exact content revision, with no workspace data access."""
import json
from typing import Literal
from fastapi import Depends,HTTPException,Response,Query
from pydantic import BaseModel,Field
from . import db,content,membership,mailer

class RequestIn(BaseModel):
    reviewer_id:str=Field(pattern='^[a-f0-9]{32}$')
    expected_revision:int=Field(ge=1)
class DecisionIn(BaseModel):
    decision:Literal['approved','changes_requested']
    comment:str=Field(default='',max_length=3000)
    expected_revision:int=Field(ge=1)


def invalidate(conn,pid):
    conn.execute("UPDATE client_reviews SET status='stale',updated=? WHERE post_id=? AND status IN ('pending','approved')",(db.now(),pid))


def cancel(conn,row,actor):
    if row['status']!='pending':return
    conn.execute("UPDATE client_reviews SET status='cancelled',updated=? WHERE id=?",(db.now(),row['id']))
    post=content.get(conn,row['workspace_id'],row['post_id'])
    if post['revision']==row['post_revision']:
        conn.execute("UPDATE content_posts SET status='draft',revision=revision+1,approved_by=NULL,approved_at=NULL,updated=? WHERE id=?",(db.now(),post['id']))
        content.snapshot(conn,content.get(conn,row['workspace_id'],post['id']),actor)


def public(row):
    return {**{k:row[k] for k in ('id','post_revision','status','comment','created','updated')},'content':json.loads(row['payload']),'published':False}


def assigned(conn,wid,rid,uid):
    membership.member(conn,wid,uid,('reviewer',))
    row=conn.execute('SELECT * FROM client_reviews WHERE id=? AND workspace_id=? AND reviewer_id=?',(rid,wid,uid)).fetchone()
    if not row:raise HTTPException(404,'Onay isteği bulunamadı.')
    return row


def register_routes(app,user,origin):
    @app.get('/api/workspaces/{wid}/content/{pid}/client-reviews')
    def history(wid,pid,u=Depends(user)):
        with db.connection() as conn:
            ws=membership.member(conn,wid,u['id']);content.get(conn,wid,pid)
            rows=[{**public(r),'reviewer_name':r['reviewer_name']} for r in conn.execute('SELECT r.*,u.name AS reviewer_name FROM client_reviews r JOIN users u ON u.id=r.reviewer_id WHERE r.workspace_id=? AND r.post_id=? ORDER BY r.created DESC LIMIT 50',(wid,pid))]
            recipients=[dict(r) for r in conn.execute("SELECT u.id,u.name,u.email FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.role='reviewer' ORDER BY u.name",(wid,))] if ws['member_role']=='owner' else []
        return {'items':rows,'recipients':recipients,'available':ws['kind']=='agency','can_request':ws['member_role']=='owner'}

    @app.post('/api/workspaces/{wid}/content/{pid}/client-reviews')
    def request_review(wid,pid,body:RequestIn,u=Depends(user)):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');ws=membership.member(conn,wid,u['id'],('owner',))
            if ws['kind']!='agency':raise HTTPException(400,'Müşteri onayı ajans çalışma alanlarında kullanılabilir.')
            membership.member(conn,wid,body.reviewer_id,('reviewer',));post=content.get(conn,wid,pid)
            if post['revision']!=body.expected_revision or post['status']=='archived':raise HTTPException(409,'İçerik değişti veya arşivde. Son sürümü yeniden aç.')
            if conn.execute("SELECT 1 FROM client_reviews WHERE post_id=? AND status='pending'",(pid,)).fetchone():raise HTTPException(409,'Bu içerik zaten müşteri onayında. Önce isteği iptal et.')
            values=json.loads(post['payload'])
            if not values['caption'].strip():raise HTTPException(400,'Müşteriye göndermeden önce gönderi metnini tamamla.')
            content.check_asset(conn,wid,values);invalidate(conn,pid)
            conn.execute("UPDATE content_posts SET status='review',revision=revision+1,approved_by=NULL,approved_at=NULL,updated=? WHERE id=?",(db.now(),pid))
            post=content.get(conn,wid,pid);content.snapshot(conn,post,u['id'])
            shared={k:values.get(k) for k in ('title','platform','format','caption','asset_id','planned_local','planned_at','timezone')}
            rid=db.uid();conn.execute('INSERT INTO client_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?)',(rid,wid,pid,body.reviewer_id,u['id'],post['revision'],json.dumps(shared,ensure_ascii=False),'pending','',db.now(),db.now()))
            # A dashboard request remains usable without SMTP; never claim mail
            # delivery. An optional notification contains no private draft text.
            notification=False
            if mailer.available():
                recipient=conn.execute('SELECT email FROM users WHERE id=?',(body.reviewer_id,)).fetchone()[0]
                mailer.queue(conn,u['id'],recipient,'client_review','İşdaş · İncelemen için yeni içerik',f"{ws['name']} alanında incelemen için yeni bir içerik var.\n\n{origin}/app?workspace={wid}&view=reviews\n\nKendi hesabınla giriş yaparak inceleyebilirsin.",db.now()+7*86400);notification=True
            conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),wid,u['id'],'review.requested',rid,db.now()))
        return {'id':rid,'post_revision':post['revision'],'notification_queued':notification}

    @app.delete('/api/workspaces/{wid}/content/{pid}/client-reviews/{rid}')
    def cancel_review(wid,pid,rid,u=Depends(user)):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');membership.member(conn,wid,u['id'],('owner',))
            row=conn.execute('SELECT * FROM client_reviews WHERE id=? AND workspace_id=? AND post_id=?',(rid,wid,pid)).fetchone()
            if not row:raise HTTPException(404,'Onay isteği bulunamadı.')
            cancel(conn,row,u['id'])
        return {'ok':True}

    @app.get('/api/workspaces/{wid}/client-reviews')
    def inbox(wid,offset:int=Query(0,ge=0),status:Literal['all','pending']='all',u=Depends(user)):
        with db.connection() as conn:
            membership.member(conn,wid,u['id'],('reviewer',))
            where=" AND status='pending'" if status=='pending' else ''
            rows=conn.execute('SELECT * FROM client_reviews WHERE workspace_id=? AND reviewer_id=?'+where+' ORDER BY created DESC,id DESC LIMIT 51 OFFSET ?',(wid,u['id'],offset)).fetchall()
            return {'items':[public(r) for r in rows[:50]],'next_offset':offset+50 if len(rows)>50 else None}

    @app.get('/api/workspaces/{wid}/client-reviews/{rid}/image')
    def image(wid,rid,u=Depends(user)):
        with db.connection() as conn:
            row=assigned(conn,wid,rid,u['id']);aid=json.loads(row['payload']).get('asset_id')
            data=conn.execute('SELECT b.data FROM media_blobs b JOIN media_assets m ON m.id=b.asset_id WHERE m.id=? AND m.workspace_id=?',(aid,wid)).fetchone()
            if not data:raise HTTPException(404,'Bu içerik sürümünün görseli artık bulunmuyor.')
        return Response(data['data'],media_type='image/png')

    @app.get('/api/workspaces/{wid}/client-reviews/{rid}')
    def detail(wid,rid,u=Depends(user)):
        with db.connection() as conn:return public(assigned(conn,wid,rid,u['id']))

    @app.post('/api/workspaces/{wid}/client-reviews/{rid}/decision')
    def decide(wid,rid,body:DecisionIn,u=Depends(user)):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=assigned(conn,wid,rid,u['id']);comment=body.comment.strip()
            if body.expected_revision!=row['post_revision']:raise HTTPException(409,'Onay isteğinin sürümü değişti. Yeniden aç.')
            if row['status']==body.decision and row['comment']==comment:return public(row)
            if row['status']!='pending':raise HTTPException(409,'Bu istek artık onay beklemiyor. Güncel içeriği yeniden aç.')
            post=content.get(conn,wid,row['post_id'])
            if post['revision']!=row['post_revision'] or post['status']!='review':raise HTTPException(409,'İçerik bu sırada değişti. Ajansın yeni sürümü göndermesini bekle.')
            if body.decision=='changes_requested' and len(comment)<3:raise HTTPException(400,'İstediğin değişikliği kısaca yaz.')
            content.check_asset(conn,wid,json.loads(post['payload']))
            status='approved' if body.decision=='approved' else 'draft'
            conn.execute('UPDATE client_reviews SET status=?,comment=?,updated=? WHERE id=?',(body.decision,comment,db.now(),rid))
            conn.execute('UPDATE content_posts SET status=?,revision=revision+1,approved_by=?,approved_at=?,updated=? WHERE id=?',(status,u['id'] if status=='approved' else None,db.now() if status=='approved' else None,db.now(),post['id']))
            content.snapshot(conn,content.get(conn,wid,post['id']),u['id'])
            conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),wid,u['id'],'review.'+body.decision,rid,db.now()))
            return public(conn.execute('SELECT * FROM client_reviews WHERE id=?',(rid,)).fetchone())
