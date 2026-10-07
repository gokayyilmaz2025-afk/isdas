import argparse,hashlib,json,re,sqlite3,zipfile
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];base='http://127.0.0.1:8000';p=argparse.ArgumentParser();p.add_argument('--pid',type=int,required=True);args=p.parse_args()
archive=ROOT/'output/Isdas-Sunucu-Paketi-20261004-r9.zip';backup=json.loads((ROOT/'research/verification/agenda-upgrade-backup.json').read_text())['backup']
with zipfile.ZipFile(archive) as z:
    manifest=json.loads(z.read('release-manifest.json'));assert 'backend/agenda.py' in manifest['files']
    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"',httpx.get(base+'/').text):
        r=httpx.get(base+asset);assert r.status_code==200 and hashlib.sha256(r.content).hexdigest()==manifest['files']['frontend/dist'+asset]
routes={}
for route in ['/agenda?day=2026-10-04','/agenda/tasks/missing','/agenda/plans/missing']:
    routes[route]=httpx.get(base+'/api/workspaces/none'+route).status_code;assert routes[route]==401
with sqlite3.connect(ROOT/'data/isdas.sqlite3') as conn,sqlite3.connect(ROOT/backup) as old:
    assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    fields='id,workspace_id,title,starts_at,ends_at,notes,created'
    existing=conn.execute('SELECT '+fields+' FROM events ORDER BY id').fetchall()
    assert existing==old.execute('SELECT '+fields+' FROM events ORDER BY id').fetchall()
    tables={name:conn.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ['agenda_tasks','agenda_plans','agenda_event_requests']};assert not any(tables.values())
    assert 'revision' in {r[1] for r in conn.execute('PRAGMA table_info(events)')}
    jobs=conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0];actions=conn.execute("SELECT count(*) FROM actions WHERE status='executing'").fetchone()[0];mail=conn.execute('SELECT count(*) FROM mail_outbox').fetchone()[0]
result={'pid':args.pid,'port':8000,'ready_status':httpx.get(base+'/api/ready').status_code,'archive':archive.name,'web_assets_match_release_manifest':True,'authorized_routes':routes,'new_table_counts':tables,'existing_events_preserved':True,'existing_event_count':len(existing),'backup':backup,'active_jobs':jobs,'executing_actions':actions,'outbound_mail_rows':mail,'test_mailbox_status':httpx.post(base+'/api/test/mailbox',headers={'Origin':'http://127.0.0.1:5173'}).status_code}
assert result['ready_status']==200 and result['test_mailbox_status'] in {404,405} and jobs==actions==mail==0
(ROOT/'research/verification/agenda-main-app.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
