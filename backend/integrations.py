import os,secrets,hashlib,base64,json,threading
from urllib.parse import urlencode
import httpx
from cryptography.fernet import Fernet,InvalidToken
from fastapi import HTTPException,Depends
from fastapi.responses import RedirectResponse
from . import db

CALENDAR_SCOPE='https://www.googleapis.com/auth/calendar.events'
GMAIL_SCOPE='https://www.googleapis.com/auth/gmail.compose'
GOOGLE_SCOPES=CALENDAR_SCOPE+' '+GMAIL_SCOPE+' https://www.googleapis.com/auth/userinfo.email'
# One API process: serialize refresh, account replacement and writes per workspace.
_locks={};_locks_guard=threading.Lock()
def workspace_lock(wid):
    with _locks_guard:return _locks.setdefault(wid,threading.RLock())

def vault():
    try:return Fernet(os.environ['ENCRYPTION_KEY'].encode())
    except (KeyError,ValueError):raise HTTPException(503,'Bağlantılar henüz etkin değil.')

def decode_token(encrypted):
    try:return json.loads(vault().decrypt(encrypted.encode()))
    except (InvalidToken,ValueError,TypeError):raise HTTPException(503,'Bağlantı okunamadı. Yönetici ayarları kontrol etmeli.')

def encode_token(token):return vault().encrypt(json.dumps(token).encode()).decode()

def access_token(wid,connection_id,required_scope):
    with workspace_lock(wid):
        connection=db.query('SELECT * FROM connections WHERE id=? AND workspace_id=?',(connection_id,wid),one=True)
        if not connection:raise HTTPException(409,'Google bağlantısı değişti. Yeniden bağla.')
        token=decode_token(connection['encrypted_token'])
        if required_scope not in token.get('scope','').split():raise HTTPException(409,'Bu işlem için Google izni verilmemiş. Hesabı yeniden bağla.')
        if token.get('expires_at',0)>db.now()+60 and token.get('access_token'):return token['access_token']
        if not token.get('refresh_token'):raise HTTPException(409,'Google oturumu yenilenmeli. Hesabı yeniden bağla.')
        if not os.getenv('GOOGLE_CLIENT_ID') or not os.getenv('GOOGLE_CLIENT_SECRET'):raise HTTPException(503,'Google bağlantı ayarları eksik.')
        try:
            with httpx.Client(timeout=25,follow_redirects=False) as client:
                response=client.post('https://oauth2.googleapis.com/token',data={'client_id':os.environ['GOOGLE_CLIENT_ID'],'client_secret':os.environ['GOOGLE_CLIENT_SECRET'],'refresh_token':token['refresh_token'],'grant_type':'refresh_token'})
            if response.status_code!=200:raise ValueError()
            fresh=response.json()
            if not isinstance(fresh.get('access_token'),str) or not fresh['access_token']:raise ValueError()
            token.update(fresh);token['expires_at']=db.now()+int(fresh.get('expires_in',3600))
            if required_scope not in token.get('scope','').split():raise ValueError()
        except (httpx.HTTPError,ValueError,TypeError):raise HTTPException(409,'Google oturumu yenilenemedi. Hesabı yeniden bağla.')
        db.query('UPDATE connections SET encrypted_token=?,updated=? WHERE id=? AND workspace_id=?',(encode_token(token),db.now(),connection_id,wid))
        return token['access_token']

def invalidate(conn,wid,connection_id):
    # Completed history stays; pending approvals never carry over to a new account.
    conn.execute("UPDATE actions SET connection_id=NULL,status=CASE WHEN status IN ('pending','failed') THEN 'invalidated' ELSE status END,updated=? WHERE workspace_id=? AND connection_id=?",(db.now(),wid,connection_id))

def disconnect(wid,provider,uid):
    with workspace_lock(wid):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            connection=conn.execute('SELECT * FROM connections WHERE workspace_id=? AND provider=?',(wid,provider)).fetchone()
            if not connection:return {'ok':True,'message':'Bağlantı zaten kaldırılmış.'}
            if conn.execute("SELECT 1 FROM actions WHERE connection_id=? AND status='executing'",(connection['id'],)).fetchone():raise HTTPException(409,'Devam eden işlemin bitmesini bekle.')
            # Revoking a Google grant affects all workspaces using that account.
            # This endpoint promises to disconnect only the current workspace.
            invalidate(conn,wid,connection['id'])
            conn.execute('DELETE FROM connections WHERE id=?',(connection['id'],))
            conn.execute('DELETE FROM oauth_states WHERE workspace_id=? AND provider=?',(wid,provider))
        db.audit(wid,uid,'connection.disconnected',provider)
        return {'ok':True,'message':'Bu alandaki bağlantı kaldırıldı. Google genel erişim iznini Google Hesabı → Güvenlik bölümünden ayrıca kaldırabilirsin.' if provider=='google' else 'Bağlantı kaldırıldı.'}

def callback_url():return os.getenv('PUBLIC_ORIGIN','http://127.0.0.1:8000').rstrip('/')+'/api/oauth/google/callback'

def save_connection(wid,token,label,account_id,uid):
    if not account_id or not label or not token.get('access_token'):raise HTTPException(502,'Google hesap kimliği doğrulanamadı.')
    token={**token,'account_id':account_id}
    with workspace_lock(wid):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous=conn.execute("SELECT * FROM connections WHERE workspace_id=? AND provider='google'",(wid,)).fetchone()
            if previous:
                if conn.execute("SELECT 1 FROM actions WHERE connection_id=? AND status='executing'",(previous['id'],)).fetchone():raise HTTPException(409,'Devam eden işlemin bitmesini bekle.')
                old=decode_token(previous['encrypted_token'])
                if old.get('account_id')==account_id and not token.get('refresh_token') and old.get('refresh_token'):token['refresh_token']=old['refresh_token']
                invalidate(conn,wid,previous['id']);conn.execute('DELETE FROM connections WHERE id=?',(previous['id'],))
            conn.execute('INSERT INTO connections VALUES(?,?,?,?,?,?)',(db.uid(),wid,'google',encode_token(token),label,db.now()))
            conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),wid,uid,'connection.connected','google',db.now()))

def register_routes(app,user,workspace,owner):
    @app.post('/api/workspaces/{wid}/connections/google/start')
    def google_start(wid,u=Depends(user)):
        owner(wid,u);vault()
        if not os.getenv('GOOGLE_CLIENT_ID') or not os.getenv('GOOGLE_CLIENT_SECRET'):raise HTTPException(503,'Google bağlantısı henüz etkin değil.')
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        with db.connection() as conn:
            conn.execute("DELETE FROM oauth_states WHERE expires<? OR (user_id=? AND workspace_id=? AND provider='google')",(db.now(),u['id'],wid))
            conn.execute('INSERT INTO oauth_states VALUES(?,?,?,?,?,?)',(state,u['id'],wid,'google',verifier,db.now()+600))
        return {'url':'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({'client_id':os.environ['GOOGLE_CLIENT_ID'],'redirect_uri':callback_url(),'response_type':'code','scope':GOOGLE_SCOPES,'state':state,'access_type':'offline','prompt':'consent','code_challenge':challenge,'code_challenge_method':'S256','include_granted_scopes':'true'})}

    @app.get('/api/oauth/google/callback')
    def google_callback(state:str,code:str='',error:str='',u=Depends(user)):
        candidate=db.query('SELECT workspace_id FROM oauth_states WHERE state=? AND user_id=? AND expires>?',(state,u['id'],db.now()),one=True)
        if not candidate:raise HTTPException(400,'Bağlantı isteğinin süresi doldu veya geçersiz.')
        with workspace_lock(candidate['workspace_id']):
            return complete_callback(state,code,error,u)

    def complete_callback(state,code,error,u):
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM oauth_states WHERE state=? AND user_id=? AND expires>?',(state,u['id'],db.now())).fetchone()
            if not row:raise HTTPException(400,'Bağlantı isteğinin süresi doldu veya geçersiz.')
            row=dict(row);conn.execute('DELETE FROM oauth_states WHERE state=?',(state,))
        owner(row['workspace_id'],u)
        if error or not code:raise HTTPException(400,'Google bağlantısı tamamlanmadı.')
        try:
            with httpx.Client(timeout=25,follow_redirects=False) as client:
                response=client.post('https://oauth2.googleapis.com/token',data={'code':code,'client_id':os.environ['GOOGLE_CLIENT_ID'],'client_secret':os.environ['GOOGLE_CLIENT_SECRET'],'redirect_uri':callback_url(),'grant_type':'authorization_code','code_verifier':row['verifier']})
                if response.status_code!=200:raise ValueError()
                token=response.json();token['expires_at']=db.now()+int(token.get('expires_in',3600))
                info=client.get('https://www.googleapis.com/oauth2/v2/userinfo',headers={'Authorization':'Bearer '+token['access_token']})
                if info.status_code!=200:raise ValueError()
                account=info.json()
                if not account.get('verified_email') or not account.get('id') or not account.get('email'):raise ValueError()
            save_connection(row['workspace_id'],token,account['email'],account['id'],u['id'])
        except (httpx.HTTPError,KeyError,ValueError,TypeError):raise HTTPException(502,'Google bağlantısı doğrulanamadı. Yeniden bağlamayı dene.')
        return RedirectResponse(os.getenv('APP_ORIGIN','http://127.0.0.1:5173')+'/app?'+urlencode({'view':'connections','workspace':row['workspace_id']}),status_code=303)
