import argparse,hashlib,json,re,sqlite3,zipfile
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];base='http://127.0.0.1:8000'
p=argparse.ArgumentParser();p.add_argument('--pid',type=int,required=True);args=p.parse_args()
archive=ROOT/'output/Isdas-Sunucu-Paketi-20261004-r8.zip'
with zipfile.ZipFile(archive) as z:
    manifest=json.loads(z.read('release-manifest.json'))
    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"',httpx.get(base+'/').text):
        r=httpx.get(base+asset);assert r.status_code==200
        assert hashlib.sha256(r.content).hexdigest()==manifest['files']['frontend/dist'+asset]
    assert 'backend/notifications.py' in manifest['files']
auth={}
for route in ['/notifications','/workspaces/none/jobs/missing','/workspaces/none/actions/missing','/workspaces/none/client-reviews/missing']:
    auth[route]=httpx.get(base+'/api'+route).status_code;assert auth[route]==401
assert httpx.post(base+'/api/notifications/read',headers={'Origin':'http://127.0.0.1:5173'},json={'ids':['0'*32]}).status_code==401
with sqlite3.connect(ROOT/'data/isdas.sqlite3') as conn:
    assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    triggers=conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'notification_%'").fetchall()
    assert len(triggers)==7
    assert conn.execute('SELECT count(*) FROM notifications').fetchone()[0]==0,'No history backfill or test data expected in local main DB.'
    mails=conn.execute('SELECT count(*) FROM mail_outbox').fetchone()[0]
    jobs=conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
    actions=conn.execute("SELECT count(*) FROM actions WHERE status='executing'").fetchone()[0]
result={'pid':args.pid,'port':8000,'ready_status':httpx.get(base+'/api/ready').status_code,'authenticated_routes':auth,'notification_triggers':len(triggers),'history_backfilled':False,'web_assets_match_release_manifest':True,'archive':archive.name,'outbound_mail_rows':mails,'active_jobs':jobs,'executing_actions':actions,'backup':json.loads((ROOT/'research/verification/notifications-upgrade-backup.json').read_text())['backup'],'test_mailbox_status':httpx.post(base+'/api/test/mailbox',headers={'Origin':'http://127.0.0.1:5173'}).status_code}
assert result['ready_status']==200 and result['test_mailbox_status'] in {404,405} and jobs==actions==mails==0
(ROOT/'research/verification/notifications-main-app.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
