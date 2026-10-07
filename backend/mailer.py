"""Encrypted transactional account mail; never log messages, addresses or tokens."""
import json,os,re,smtplib,ssl,threading
from email.message import EmailMessage
from email.utils import formatdate,make_msgid
from cryptography.fernet import Fernet
from . import db,runtime


def config():
    host=os.getenv('SMTP_HOST','').strip()
    sender=os.getenv('SMTP_FROM','').strip()
    mode=os.getenv('SMTP_SECURITY','starttls')
    username=os.getenv('SMTP_USERNAME','')
    password=os.getenv('SMTP_PASSWORD','')
    if not host or not sender:raise ValueError('Account mail is not configured.')
    if not re.fullmatch(r'[A-Za-z0-9.-]+',host):raise ValueError('Invalid SMTP host.')
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+',sender):raise ValueError('Invalid SMTP sender.')
    if mode not in {'starttls','ssl'}:raise ValueError('SMTP must use verified TLS.')
    if bool(username)!=bool(password):raise ValueError('SMTP authentication is incomplete.')
    port=int(os.getenv('SMTP_PORT','465' if mode=='ssl' else '587'))
    if not 1<=port<=65535:raise ValueError('Invalid SMTP port.')
    Fernet(os.getenv('ENCRYPTION_KEY','').encode())
    return dict(host=host,sender=sender,mode=mode,username=username,password=password,port=port)


def available():
    try:config();return True
    except (ValueError,TypeError):return False


def queue(conn,user_id,email,purpose,subject,body,expires,token_hash=None):
    payload=Fernet(os.environ['ENCRYPTION_KEY'].encode()).encrypt(json.dumps({'to':email,'subject':subject,'body':body},ensure_ascii=False).encode()).decode()
    mid=db.uid();now=db.now()
    conn.execute('INSERT INTO mail_outbox VALUES(?,?,?,?,?,?,?,?,?)',(mid,user_id,purpose,payload,'queued',token_hash,expires,now,now))
    return mid


def send(payload,mid):
    cfg=config();context=ssl.create_default_context()
    msg=EmailMessage();msg['From']=cfg['sender'];msg['To']=payload['to'];msg['Subject']=payload['subject']
    msg['Date']=formatdate(localtime=False);msg['Message-ID']=make_msgid(idstring=mid,domain=cfg['sender'].split('@')[1])
    msg.set_content(payload['body'])
    cls=smtplib.SMTP_SSL if cfg['mode']=='ssl' else smtplib.SMTP
    kwargs={'context':context} if cfg['mode']=='ssl' else {}
    with cls(cfg['host'],cfg['port'],timeout=15,**kwargs) as server:
        if cfg['mode']=='starttls':server.ehlo();server.starttls(context=context);server.ehlo()
        if cfg['username']:server.login(cfg['username'],cfg['password'])
        if server.send_message(msg):raise RuntimeError('Recipient rejected.')


def dispatch_one():
    if not available():return False
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if runtime.recovery_hold(conn):return False
        conn.execute("UPDATE mail_outbox SET state='expired',payload='',updated=? WHERE state='queued' AND (expires<=? OR (token_hash IS NOT NULL AND NOT EXISTS(SELECT 1 FROM account_tokens t WHERE t.token_hash=mail_outbox.token_hash) AND NOT EXISTS(SELECT 1 FROM membership_invites i WHERE i.token_hash=mail_outbox.token_hash AND i.state='pending' AND i.expires>?)))",(db.now(),db.now(),db.now()))
        row=conn.execute("SELECT * FROM mail_outbox WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
        if not row:return False
        conn.execute("UPDATE mail_outbox SET state='sending',updated=? WHERE id=?",(db.now(),row['id']))
    # No database lock is held while contacting the SMTP server. A crash after
    # claim is ambiguous; startup will not blindly replay it.
    state='sent'
    try:
        payload=json.loads(Fernet(os.environ['ENCRYPTION_KEY'].encode()).decrypt(row['payload'].encode()))
        send(payload,row['id'])
    except Exception:state='uncertain'
    db.query("UPDATE mail_outbox SET state=?,payload='',updated=? WHERE id=? AND state='sending'",(state,db.now(),row['id']))
    return True


def start():
    stop=threading.Event()
    # Single production process enforced by runtime.DatabaseLease.
    db.query("UPDATE mail_outbox SET state='uncertain',payload='',updated=? WHERE state='sending'",(db.now(),))
    def run():
        while not stop.is_set():
            try:
                if dispatch_one():continue
            except Exception:pass # No credentials or provider exception text in logs.
            stop.wait(1)
    thread=threading.Thread(target=run,name='isdas-account-mail',daemon=True);thread.start()
    return thread,stop
