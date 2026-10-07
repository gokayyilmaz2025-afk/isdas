"""Workspace invitations and revocation. Customer reviewers never gain team access."""
import secrets
from typing import Literal
from fastapi import Depends,HTTPException
from pydantic import BaseModel,Field
from . import db,accounts,mailer,usage

MAX_MEMBERS=25
class InviteIn(BaseModel):
    email:str=Field(min_length=5,max_length=254)
    role:Literal['member','reviewer']='member'
class TokenIn(BaseModel):token:str=Field(min_length=40,max_length=200)


def member(conn,wid,uid,roles=('owner','member')):
    row=conn.execute('SELECT w.*,m.role AS member_role FROM memberships m JOIN workspaces w ON w.id=m.workspace_id WHERE m.workspace_id=? AND m.user_id=?',(wid,uid)).fetchone()
    if not row or row['member_role'] not in roles:raise HTTPException(404,'Çalışma alanı bulunamadı.')
    return row


def check_invitation(conn,token,email=None):
    row=conn.execute("SELECT i.*,w.name AS workspace_name,w.kind FROM membership_invites i JOIN workspaces w ON w.id=i.workspace_id JOIN memberships m ON m.workspace_id=i.workspace_id AND m.user_id=i.invited_by AND m.role='owner' WHERE i.token_hash=? AND i.state='pending' AND i.expires>?",(accounts.digest(token),db.now())).fetchone()
    if not row or (email is not None and row['email']!=email):raise HTTPException(400,'Davet geçersiz, kullanılmış veya süresi dolmuş. Alan yöneticisinden yeni davet iste.')
    if row['role']=='reviewer' and row['kind']!='agency':raise HTTPException(400,'Bu müşteri daveti artık kullanılamıyor.')
    return row


def cancel_invite(conn,iid,state='revoked'):
    row=conn.execute('SELECT token_hash FROM membership_invites WHERE id=?',(iid,)).fetchone()
    if not row:return
    conn.execute('UPDATE membership_invites SET state=?,updated=? WHERE id=?',(state,db.now(),iid))
    conn.execute("UPDATE mail_outbox SET state='cancelled',payload='',updated=? WHERE token_hash=? AND state='queued'",(db.now(),row['token_hash']))


def revoke_work(conn,wid,uid):
    conn.execute('UPDATE routines SET enabled=0 WHERE workspace_id=? AND user_id=?',(wid,uid))
    conn.execute('DELETE FROM oauth_states WHERE workspace_id=? AND user_id=?',(wid,uid))
    for row in conn.execute("SELECT id,status FROM jobs WHERE workspace_id=? AND user_id=? AND status IN ('queued','running')",(wid,uid)).fetchall():
        if row['status']=='queued':
            usage.settle(conn,row['id'],no_request=True)
            conn.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,stage='Alan erişimi kapatıldığı için iptal edildi',updated=? WHERE id=?",(db.now(),row['id']))
        else:conn.execute('UPDATE jobs SET cancel_requested=1,updated=? WHERE id=?',(db.now(),row['id']))
    from . import reviews
    for row in conn.execute("SELECT * FROM client_reviews WHERE workspace_id=? AND reviewer_id=? AND status='pending'",(wid,uid)).fetchall():reviews.cancel(conn,row,uid)


def register_routes(app,user,current_user,origin):
    @app.get('/api/workspaces/{wid}/members')
    def listing(wid,u=Depends(user)):
        with db.connection() as conn:
            ws=member(conn,wid,u['id'])
            members=[dict(r) for r in conn.execute('SELECT u.id,u.name,u.email,m.role FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? ORDER BY m.role,u.name',(wid,))]
            invites=[]
            if ws['member_role']=='owner':
                invites=[dict(r) for r in conn.execute("SELECT i.id,i.email,i.role,i.state,i.expires,i.created,(SELECT m.state FROM mail_outbox m WHERE m.token_hash=i.token_hash ORDER BY m.created DESC LIMIT 1) AS delivery FROM membership_invites i WHERE i.workspace_id=? AND i.state='pending' AND i.expires>? ORDER BY i.created DESC",(wid,db.now()))]
        return {'members':members,'invitations':invites,'max_members':MAX_MEMBERS,'mail_available':mailer.available(),'kind':ws['kind'],'can_manage':ws['member_role']=='owner','self_id':u['id']}

    @app.post('/api/workspaces/{wid}/invitations')
    def invite(wid,body:InviteIn,u=Depends(user)):
        email=accounts.normalize_email(body.email)
        if not u['email_verified']:raise HTTPException(403,'Davet göndermeden önce kendi e-posta adresini doğrula.')
        if not mailer.available():raise HTTPException(503,'Davet e-postaları henüz hazır değil. Lütfen daha sonra yeniden dene.')
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');ws=member(conn,wid,u['id'],('owner',))
            if body.role=='reviewer' and ws['kind']!='agency':raise HTTPException(400,'Müşteri erişimi ajans çalışma alanlarında kullanılabilir.')
            if conn.execute('SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND u.email=?',(wid,email)).fetchone():raise HTTPException(409,'Bu kişi zaten bu alanda. Rol değiştirmek için önce erişimini kaldırıp yeni rolüyle davet et.')
            for row in conn.execute("SELECT id FROM membership_invites WHERE workspace_id=? AND state='pending' AND expires<=?",(wid,db.now())).fetchall():cancel_invite(conn,row['id'],'expired')
            if conn.execute("SELECT 1 FROM membership_invites WHERE workspace_id=? AND email=? AND state='pending'",(wid,email)).fetchone():raise HTTPException(409,'Bu adrese bekleyen davet var. Yenilemek için önce eski daveti iptal et.')
            count=conn.execute('SELECT count(*) FROM memberships WHERE workspace_id=?',(wid,)).fetchone()[0]+conn.execute("SELECT count(*) FROM membership_invites WHERE workspace_id=? AND state='pending'",(wid,)).fetchone()[0]
            if count>=MAX_MEMBERS:raise HTTPException(409,'Bu alanın üye ve bekleyen davet sınırı doldu.')
            # Persist this throttle even when the caller is over its allowance.
            allowed=accounts.throttle(conn,'workspace-invite:'+wid,20,3600)
            if allowed:
                iid=db.uid();token=secrets.token_urlsafe(40);hashed=accounts.digest(token);expiry=db.now()+7*86400
                conn.execute('INSERT INTO membership_invites VALUES(?,?,?,?,?,?,?,?,?,?,?)',(iid,wid,email,body.role,hashed,u['id'],'pending',expiry,db.now(),None,db.now()))
                scope='Yalnızca sana gönderilen içerikleri inceleyip onaylayabilirsin.' if body.role=='reviewer' else 'Bu alanın ortak sohbet, dosya, hafıza ve içeriklerini görebilir; ekip çalışmalarına katılabilirsin.'
                text=f"{u['name']}, seni İşdaş'taki {ws['name']} alanına davet etti.\n\n{scope}\n\nDaveti incele ve kabul et:\n{origin}/account/invite#token={token}\n\nDavet 7 gün geçerlidir. {email} adresli doğrulanmış hesabınla kabul etmelisin. İstemediğin bir davetse bu iletiyi yok sayabilirsin."
                mailer.queue(conn,u['id'],email,'invite','İşdaş · Çalışma alanına davet',text,expiry,hashed)
                conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),wid,u['id'],'member.invited',iid,db.now()))
        if not allowed:raise HTTPException(429,'Davet sınırına ulaşıldı. Biraz sonra yeniden dene.')
        return {'id':iid,'state':'pending','message':'Davet gönderim sırasına alındı.'}

    @app.delete('/api/workspaces/{wid}/invitations/{iid}')
    def revoke_invite(wid,iid,u=Depends(user)):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');member(conn,wid,u['id'],('owner',))
            row=conn.execute('SELECT * FROM membership_invites WHERE id=? AND workspace_id=?',(iid,wid)).fetchone()
            if not row:raise HTTPException(404,'Davet bulunamadı.')
            if row['state']=='pending':cancel_invite(conn,iid)
        return {'ok':True}

    @app.post('/api/invitations/preview')
    def preview(body:TokenIn):
        with db.connection() as conn:
            row=check_invitation(conn,body.token)
            return {'workspace_name':row['workspace_name'],'email':row['email'],'role':row['role'],'expires':row['expires']}

    @app.post('/api/invitations/accept')
    def accept(body:TokenIn,u=Depends(current_user)):
        if not u['email_verified']:raise HTTPException(403,'Daveti kabul etmek için e-posta adresini doğrula.')
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=check_invitation(conn,body.token,u['email'])
            # Re-read verification inside the same transaction as granting access.
            if not conn.execute('SELECT 1 FROM account_state WHERE user_id=? AND verified_at IS NOT NULL',(u['id'],)).fetchone():raise HTTPException(403,'E-posta doğrulaması gerekiyor.')
            existing=conn.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(row['workspace_id'],u['id'])).fetchone()
            if existing:raise HTTPException(409,'Bu alana zaten erişimin var.')
            if conn.execute('SELECT count(*) FROM memberships WHERE workspace_id=?',(row['workspace_id'],)).fetchone()[0]>=MAX_MEMBERS:raise HTTPException(409,'Çalışma alanının üye sınırı doldu.')
            conn.execute('INSERT INTO memberships VALUES(?,?,?)',(row['workspace_id'],u['id'],row['role']))
            cancel_invite(conn,row['id'],'accepted');conn.execute('UPDATE membership_invites SET accepted_by=? WHERE id=?',(u['id'],row['id']))
            conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),row['workspace_id'],u['id'],'member.joined',row['id'],db.now()))
        return {'workspace_id':row['workspace_id'],'role':row['role']}

    @app.delete('/api/workspaces/{wid}/members/{uid}')
    def remove(wid,uid,u=Depends(user)):
        uid=u['id'] if uid=='me' else uid
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');actor=member(conn,wid,u['id'],('owner','member','reviewer'))
            target=conn.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone()
            if not target:raise HTTPException(404,'Üye bulunamadı.')
            if target['role']=='owner':raise HTTPException(403,'Alan yöneticisi bu işlemle kaldırılamaz.')
            if uid!=u['id'] and actor['member_role']!='owner':raise HTTPException(403,'Üye erişimini yalnızca alan yöneticisi kaldırabilir.')
            revoke_work(conn,wid,uid)
            conn.execute('DELETE FROM memberships WHERE workspace_id=? AND user_id=?',(wid,uid))
            conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),wid,u['id'],'member.removed',uid,db.now()))
        return {'ok':True}
