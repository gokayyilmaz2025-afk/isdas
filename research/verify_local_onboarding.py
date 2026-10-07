import json,sqlite3
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];base='http://127.0.0.1:8000'
result={'port':8000,'pid':10928,'ready_status':httpx.get(base+'/api/ready').status_code,'private_setup_status':httpx.get(base+'/api/workspaces/none/onboarding').status_code,'test_mailbox_status':httpx.post(base+'/api/test/mailbox',headers={'Origin':'http://127.0.0.1:5173'}).status_code}
assert result['ready_status']==200 and result['private_setup_status']==401 and result['test_mailbox_status'] in {404,405}
root=httpx.get(base+'/app');assert root.status_code==200 and 'index-BfvjIRv9.js' in root.text
with sqlite3.connect(ROOT/'data/isdas.sqlite3') as conn:
    assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    for name in ['workspace_onboarding','setup_requests','site_previews']:assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()
    result['outbound_mail_rows']=conn.execute('SELECT count(*) FROM mail_outbox').fetchone()[0]
    result['active_jobs']=conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
result['backup']=json.loads((ROOT/'research/verification/onboarding-upgrade-backup.json').read_text())['backup']
(ROOT/'research/verification/onboarding-main-app.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
