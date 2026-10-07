"""Explicit isolated account acceptance app: captures mail locally, no network send."""
import argparse,os,sys
from pathlib import Path
from cryptography.fernet import Fernet

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--port',type=int,default=8001);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];database=Path(args.database).resolve()
    if not database.is_relative_to(root/'tmp') or database.exists():raise SystemExit('Choose a fresh database inside project tmp.')
    os.environ.update(APP_ENV='development',DATABASE_PATH=str(database),APP_ORIGIN=f'http://127.0.0.1:{args.port}',PUBLIC_ORIGIN=f'http://127.0.0.1:{args.port}',REGISTRATION_ENABLED='true',EMAIL_VERIFICATION_REQUIRED='true',WORKER_ENABLED='false',MAIL_WORKER_ENABLED='false',SMTP_HOST='smtp.example.test',SMTP_FROM='accounts@example.test',SMTP_PORT='587',SMTP_SECURITY='starttls',SMTP_USERNAME='',SMTP_PASSWORD='',ENCRYPTION_KEY=Fernet.generate_key().decode(),XAI_API_KEY='',GOOGLE_CLIENT_ID='',GOOGLE_CLIENT_SECRET='')
    sys.path.insert(0,str(root))
    import uvicorn
    from backend.main import app
    from backend import mailer
    delivered=[]
    mailer.send=lambda payload,mid:delivered.append(payload)
    @app.post('/api/test/mailbox')
    def mailbox():
        while mailer.dispatch_one():pass
        return {'synthetic_test_only':True,'messages':delivered}
    print('ISOLATED_ACCOUNT_TEST: local captured mail only; no live SMTP.',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',access_log=False)

if __name__=='__main__':main()
