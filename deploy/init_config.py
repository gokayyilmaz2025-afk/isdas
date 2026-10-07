"""Create a new private config without printing secrets or replacing old keys."""
import argparse,os,re
from pathlib import Path
from cryptography.fernet import Fernet


def create(domain,output):
    if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',domain):
        raise ValueError('Use a lowercase DNS hostname, without scheme, port or path.')
    values={
        'APP_ENV':'production','APP_ORIGIN':'https://'+domain,'PUBLIC_ORIGIN':'https://'+domain,
        'DATABASE_PATH':'/var/lib/isdas/isdas.sqlite3','REGISTRATION_ENABLED':'false','WORKER_ENABLED':'true',
        'ENCRYPTION_KEY':Fernet.generate_key().decode(),'BACKUP_ENCRYPTION_KEY':Fernet.generate_key().decode(),
        'BACKUP_DIRECTORY':'/var/backups/isdas','XAI_API_KEY':'','XAI_MODEL':'grok-4.7','XAI_BASE_URL':'https://api.x.ai/v1',
        'HERMES_RUNTIME_MAP':'/etc/isdas/runtime-map.json',
        'DEFAULT_MONTHLY_BUDGET_USD':'10','GLOBAL_MONTHLY_BUDGET_USD':'100',
        'JOB_RESERVE_USD':'0.25','IMAGE_JOB_RESERVE_USD':'0.10','RESEARCH_JOB_RESERVE_USD':'1','HERMES_JOB_RESERVE_USD':'2',
        'GOOGLE_CLIENT_ID':'','GOOGLE_CLIENT_SECRET':'',
        'EMAIL_VERIFICATION_REQUIRED':'true','MAIL_WORKER_ENABLED':'true',
        'SMTP_HOST':'','SMTP_PORT':'587','SMTP_SECURITY':'starttls','SMTP_FROM':'','SMTP_USERNAME':'','SMTP_PASSWORD':'',
    }
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
        f.write('# Preserve both encryption keys separately from the server. Never regenerate over live data.\n')
        f.write('\n'.join(k+'='+v for k,v in values.items())+'\n')
    return {'created':str(output),'registration_enabled':False,'provider_configured':False}


if __name__=='__main__':
    import json
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--domain',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:print(json.dumps(create(args.domain,args.output)))
    except (ValueError,OSError) as exc:parser.exit(1,str(exc)+'\n')
