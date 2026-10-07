"""One read-only public HTTPS acceptance check; stores metadata, not page content."""
import datetime,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.onboarding import read_site
r=read_site('https://www.iana.org/domains/reserved')
assert r['ok'] and len(r['text'])>=40 and r['warnings']
result={'passed':True,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'url':r['url'],'title':r['title'],'characters':len(r['text']),'warnings_roundtrip_utf8':r['warnings'],'scope':'single public HTTPS page through real isolated reader; no JavaScript, login or full site crawl'}
(ROOT/'research/verification/site-reader-public-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='warnings_roundtrip_utf8'}))
