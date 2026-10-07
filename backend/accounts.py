"""Account recovery with explicit confirmation, expiring one-use tokens and session revocation."""
import hashlib,hmac,os,re,secrets,time
from fastapi import HTTPException,Request,Response,Depends
from pydantic import BaseModel,Field,model_validator
from . import db,mailer


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value):
    salt=secrets.token_hex(16)
    return salt+':'+hashlib.scrypt(value.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()


def verify_password(value,stored):
    try:
        salt,expected=stored.split(':')
        actual=hashlib.scrypt(value.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
        return hmac.compare_digest(actual,expected)
    except (ValueError,TypeError):return False


def required():return os.getenv('EMAIL_VERIFICATION_REQUIRED','true' if os.getenv('APP_ENV')=='production' else 'false')=='true'


def registration_enabled():return os.getenv('REGISTRATION_ENABLED','false' if os.getenv('APP_ENV')=='production' else 'true')=='true'


def normalize_email(value):
    value=value.strip().lower()
    if len(value)>254 or not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}',value):
        raise HTTPException(400,'Geçerli bir e-posta adresi gir.')
    return value


def public_user(record,conn):
    conn.execute('INSERT OR IGNORE INTO account_state(user_id) VALUES(?)',(record['id'],))
    state=conn.execute('SELECT verified_at FROM account_state WHERE user_id=?',(record['id'],)).fetchone()
    return {**{k:record[k] for k in ('id','email','name')},'email_verified':state['verified_at'] is not None,'verification_required':required()}


def throttle(conn,bucket,limit,window):
    now=db.now();conn.execute('DELETE FROM account_throttles WHERE created<?',(now-86400,))
    key=digest(bucket)
    count=conn.execute('SELECT count(*) FROM account_throttles WHERE bucket=? AND created>?',(key,now-window)).fetchone()[0]
    if count>=limit:return False
    conn.execute('INSERT INTO account_throttles VALUES(?,?)',(key,now));return True


def confirm_rate(request,token):
    ip=request.client.host if request.client else 'unknown'
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        allowed=throttle(conn,'confirm-ip:'+ip,20,900) and throttle(conn,'confirm-token:'+token,6,900)
    if not allowed:raise HTTPException(429,'Çok fazla deneme yapıldı. Biraz sonra yeniden dene.')


def invalidate_links(conn,uid,purpose=None):
    args=(uid,purpose) if purpose else (uid,)
    clause='user_id=?'+(' AND purpose=?' if purpose else '')
    conn.execute('DELETE FROM account_tokens WHERE '+clause,args)
    conn.execute("UPDATE mail_outbox SET state='cancelled',payload='',updated=? WHERE state='queued' AND "+clause,(db.now(),*args))
    if purpose is None:conn.execute("UPDATE membership_invites SET state='revoked',updated=? WHERE invited_by=? AND state='pending'",(db.now(),uid))


def issue(conn,record,purpose,origin):
    conn.execute('INSERT OR IGNORE INTO account_state(user_id) VALUES(?)',(record['id'],))
    version=conn.execute('SELECT credential_version FROM account_state WHERE user_id=?',(record['id'],)).fetchone()[0]
    invalidate_links(conn,record['id'],purpose)
    token=secrets.token_urlsafe(40);hashed=digest(token);expires=db.now()+(1800 if purpose=='reset' else 86400)
    conn.execute('INSERT INTO account_tokens VALUES(?,?,?,?,?,?,?)',(hashed,record['id'],purpose,record['email'],version,expires,db.now()))
    url=origin+'/account/'+purpose+'#token='+token
    title='Parolanı yenile' if purpose=='reset' else 'E-posta adresini doğrula'
    duration='30 dakika' if purpose=='reset' else '24 saat'
    body=f"İşdaş hesabın için {title.lower()}:\n\n{url}\n\nBu bağlantı {duration} geçerlidir ve yalnızca bir kez kullanılabilir. Bağlantıyı açmak tek başına hesabında değişiklik yapmaz.\n\nBu işlemi sen istemediysen bu iletiyi yok sayabilirsin."
    mailer.queue(conn,record['id'],record['email'],purpose,'İşdaş · '+title,body,expires,hashed)


def token_record(conn,raw,purpose):
    row=conn.execute('SELECT t.*,u.password FROM account_tokens t JOIN users u ON u.id=t.user_id JOIN account_state s ON s.user_id=t.user_id WHERE t.token_hash=? AND t.purpose=? AND t.expires>? AND t.credential_version=s.credential_version AND t.email=u.email',(digest(raw),purpose,db.now())).fetchone()
    if not row:raise HTTPException(400,'Bağlantı geçersiz, kullanılmış veya süresi dolmuş. Yeni bir bağlantı iste.')
    return row


def replace_password(conn,uid,password):
    conn.execute('UPDATE users SET password=? WHERE id=?',(password_hash(password),uid))
    conn.execute('UPDATE account_state SET credential_version=credential_version+1 WHERE user_id=?',(uid,))
    conn.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
    conn.execute('DELETE FROM oauth_states WHERE user_id=?',(uid,))
    invalidate_links(conn,uid)
    row=conn.execute('SELECT email FROM users WHERE id=?',(uid,)).fetchone()
    if mailer.available():
        mailer.queue(conn,uid,row['email'],'password_changed','İşdaş · Parolan değiştirildi','Hesabının parolası değiştirildi ve tüm oturumlar kapatıldı. Bu işlemi sen yapmadıysan İşdaş giriş sayfasındaki “Parolamı unuttum” seçeneğiyle hesabını geri al.',db.now()+86400)
    conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),None,uid,'account.password_changed','All sessions and recovery tokens revoked',db.now()))


class EmailIn(BaseModel):email:str=Field(min_length=5,max_length=254)
class VerifyIn(BaseModel):
    token:str=Field(min_length=40,max_length=200)
    password:str=Field(min_length=10,max_length=128)
class PasswordIn(BaseModel):
    password:str=Field(min_length=10,max_length=128)
    confirm_password:str=Field(min_length=10,max_length=128)
    @model_validator(mode='after')
    def match(self):
        if self.password!=self.confirm_password:raise ValueError('Parolalar aynı olmalı.')
        return self
class ResetIn(PasswordIn):token:str=Field(min_length=40,max_length=200)
class ChangeIn(PasswordIn):current_password:str=Field(min_length=10,max_length=128)


def register_routes(app,current_user,origin):
    @app.get('/api/auth/options')
    def options():
        return {'registration_enabled':registration_enabled(),'verification_required':required(),'mail_available':mailer.available()}

    @app.post('/api/auth/forgot-password')
    def forgot(body:EmailIn,request:Request):
        start=time.monotonic();email=normalize_email(body.email)
        if not mailer.available():raise HTTPException(503,'E-posta hizmeti henüz hazır değil. Lütfen daha sonra yeniden dene.')
        ip=request.client.host if request.client else 'unknown'
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            allowed_ip=throttle(conn,'request-ip:'+ip,12,900)
            allowed_email=throttle(conn,'request-email:'+email,3,3600) and throttle(conn,'request-cooldown:'+email,1,60)
            if allowed_ip and allowed_email:
                row=conn.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                if row:issue(conn,row,'reset',origin)
        # Keep the common account/non-account path at the same minimum duration;
        # network delivery runs independently, never on this response path.
        time.sleep(max(0,0.2-(time.monotonic()-start)))
        if not allowed_ip:raise HTTPException(429,'Çok fazla deneme yapıldı. Biraz sonra yeniden dene.')
        return {'ok':True,'message':'Bu adresle bir hesap varsa parola yenileme bağlantısı gönderim sırasına alındı. Gelen kutunu ve spam klasörünü kontrol et.'}

    @app.get('/api/account')
    def account(u=Depends(current_user)):
        row=db.query("SELECT state,created FROM mail_outbox WHERE user_id=? AND purpose='verify' ORDER BY created DESC LIMIT 1",(u['id'],),one=True)
        return {**u,'mail_available':mailer.available(),'verification_delivery':row}

    @app.post('/api/account/send-verification')
    def send_verification(u=Depends(current_user)):
        if u['email_verified']:return {'ok':True,'message':'E-posta adresin zaten doğrulanmış.'}
        if not mailer.available():raise HTTPException(503,'E-posta hizmeti henüz hazır değil. Lütfen daha sonra yeniden dene.')
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            allowed=throttle(conn,'verify-cooldown:'+u['id'],1,60) and throttle(conn,'verify-hour:'+u['id'],5,3600)
            if allowed:issue(conn,u,'verify',origin)
        if not allowed:raise HTTPException(429,'Yeni bağlantı için biraz bekle. Gelen kutunu ve spam klasörünü kontrol edebilirsin.')
        return {'ok':True,'message':'Doğrulama bağlantısı gönderim sırasına alındı. Gelen kutunu ve spam klasörünü kontrol et.'}

    @app.post('/api/auth/verify-email')
    def verify(body:VerifyIn,request:Request):
        confirm_rate(request,body.token)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=token_record(conn,body.token,'verify')
            # Email possession + existing password prevents takeover by someone
            # who pre-registered another person's email address.
            if not verify_password(body.password,row['password']):raise HTTPException(400,'Parola doğru değil. Hatırlamıyorsan parola yenileme bağlantısı iste.')
            conn.execute('UPDATE account_state SET verified_at=? WHERE user_id=?',(db.now(),row['user_id']))
            invalidate_links(conn,row['user_id'],'verify')
        return {'ok':True}

    @app.post('/api/auth/reset-password')
    def reset(body:ResetIn,request:Request,response:Response):
        confirm_rate(request,body.token)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');row=token_record(conn,body.token,'reset')
            replace_password(conn,row['user_id'],body.password)
            conn.execute('UPDATE account_state SET verified_at=? WHERE user_id=?',(db.now(),row['user_id']))
        response.delete_cookie('isdas_session',path='/');return {'ok':True}

    @app.post('/api/account/change-password')
    def change(body:ChangeIn,request:Request,response:Response,u=Depends(current_user)):
        confirm_rate(request,u['id'])
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            record=conn.execute('SELECT password FROM users WHERE id=?',(u['id'],)).fetchone()
            if not verify_password(body.current_password,record['password']):raise HTTPException(400,'Mevcut parola doğru değil.')
            replace_password(conn,u['id'],body.password)
        response.delete_cookie('isdas_session',path='/');return {'ok':True}
