"""Explicit isolated UI fixtures; never installs an HTTP fixture route."""
import argparse,json,sqlite3,time,uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--workspace',required=True);p.add_argument('--expire');args=p.parse_args()
database=Path(args.database).resolve()
if not database.is_relative_to(root/'tmp') or not database.is_file():raise SystemExit('Existing isolated tmp database required.')
uid=lambda:uuid.uuid4().hex
with sqlite3.connect(database) as c:
    c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
    ws=c.execute('SELECT * FROM workspaces WHERE id=?',(args.workspace,)).fetchone()
    if not ws or not ws['name'].startswith('TEST'):raise SystemExit('Explicit TEST workspace required.')
    if args.expire:
        c.execute("UPDATE jobs SET status='cancelled',updated=? WHERE id=? AND workspace_id=?",(time.time(),args.expire,ws['id']))
        result={'synthetic_fixture':True,'expired':args.expire}
    else:
        now=time.time();jobs=[]
        for i in range(35):
            jid=uid();jobs.append(jid)
            c.execute('INSERT INTO jobs(id,workspace_id,user_id,agent_id,prompt,status,stage,output,created,updated,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(jid,ws['id'],ws['owner_id'],'guide',f'TEST · Tamamlanan çalışma {i+1}','completed','TEST hazır','TEST fixture result',now+i/100,now+i/100,jid))
        connection=uid()
        c.execute('INSERT INTO connections VALUES(?,?,?,?,?,?)',(connection,ws['id'],'google','INVALID LOCAL FIXTURE; NO REAL CREDENTIAL','TEST · Google hesabı',now))
        action=uid()
        for i in range(102):
            aid=action if i==0 else uid()
            payload={'to':'fixture@example.test','subject':'TEST · Bildirimden açılan eski taslak' if i==0 else f'TEST · Diğer işlem {i}','body':'Bu yalnızca yerel arayüz testi. Gerçek hesap bağlı değil.'}
            c.execute('INSERT INTO actions(id,workspace_id,connection_id,kind,payload,status,payload_hash,account_label,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,ws['id'],connection,'google.gmail.draft',json.dumps(payload,ensure_ascii=False),'pending' if i==0 else 'rejected',uid(),'TEST · Google hesabı',now-1000 if i==0 else now,now+1 if i==0 else now-2))
        result={'synthetic_fixture':True,'jobs':jobs,'action':action,'connection':connection}
print(json.dumps(result))
