"""Timer entry point. Retains backups; off-server copy/retention is operator-owned."""
import datetime,json,os,uuid
from pathlib import Path
from backend.ops import backup,verify

if __name__=='__main__':
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target=Path(os.environ['BACKUP_DIRECTORY'])/('isdas-'+stamp+'-'+uuid.uuid4().hex[:8]+'.enc')
    result=backup(os.environ['DATABASE_PATH'],target)
    verify(target)
    print(json.dumps({'verified':True,'backup':str(target),'snapshot_bytes':result['bytes'],'off_server_copy_completed':False}))
