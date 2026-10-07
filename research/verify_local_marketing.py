import hashlib,json,re,sqlite3,zipfile
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1];base='http://127.0.0.1:8000'
pages=['','kullanim/kisisel','kullanim/isletme','kullanim/ajans','isdaslar','baglantilar','paketler','yardim'];statuses={}
for path in pages:
    r=httpx.get(base+'/'+path);statuses['/'+path]=r.status_code
    assert r.status_code==200 and 'og:title' in r.text
assert httpx.get(base+'/not-a-public-page').status_code==404
catalog=httpx.get(base+'/api/public/catalog').json();assert not catalog['checkout_enabled'] and catalog['pricing_status']=='preview'
archive=ROOT/'output/Isdas-Sunucu-Paketi-20261004-r7.zip'
with zipfile.ZipFile(archive) as z:
    manifest=json.loads(z.read('release-manifest.json'))
    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"',httpx.get(base+'/').text):
        body=httpx.get(base+asset);assert body.status_code==200
        assert hashlib.sha256(body.content).hexdigest()==manifest['files']['frontend/dist'+asset]
with sqlite3.connect(ROOT/'data/isdas.sqlite3') as conn:
    assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    mails=conn.execute('SELECT count(*) FROM mail_outbox').fetchone()[0]
    jobs=conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
result={'port':8000,'pid':14124,'ready_status':httpx.get(base+'/api/ready').status_code,'public_pages':statuses,'catalogue_status':'preview; no checkout','web_assets_match_release_manifest':True,'archive':archive.name,'test_mailbox_status':httpx.post(base+'/api/test/mailbox',headers={'Origin':'http://127.0.0.1:5173'}).status_code,'outbound_mail_rows':mails,'active_jobs':jobs,'backup':json.loads((ROOT/'research/verification/marketing-upgrade-backup.json').read_text())['backup']}
assert result['ready_status']==200 and result['test_mailbox_status'] in {404,405}
(ROOT/'research/verification/marketing-main-app.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
