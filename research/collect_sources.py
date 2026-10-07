import pathlib, json, requests, concurrent.futures, datetime
from bs4 import BeautifulSoup
ROOT=pathlib.Path(__file__).resolve().parents[1]
dest=ROOT/'research'/'sources'
sources={
 'marblism-home':'https://www.marblism.com/',
 'marblism-integrations':'https://help.marblism.com/en/articles/15278633-what-integrations-are-available',
 'sintra-home':'https://sintra.ai/',
 'sintra-brain':'https://sintra.ai/features/brain-ai',
 'sintra-integrations':'https://sintra.ai/integrations',
 'sintra-summer':'https://sintra.ai/summer-edition',
 'hermes-license':'https://raw.githubusercontent.com/NousResearch/hermes-agent/main/LICENSE',
 'hermes-architecture':'https://hermes-agent.nousresearch.com/docs/developer-guide/architecture',
 'hermes-security':'https://hermes-agent.nousresearch.com/docs/user-guide/security',
 'hermes-api':'https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server',
 'hermes-profiles':'https://hermes-agent.nousresearch.com/docs/user-guide/profiles',
 'grok-bot-terms':'https://x.ai/legal/grok-bot-terms',
 'grok-api-terms':'https://x.ai/legal/terms-of-service-enterprise',
}
def fetch(entry):
 key,url=entry
 try:
  r=requests.get(url,timeout=40); r.raise_for_status()
  soup=BeautifulSoup(r.text,'html.parser')
  for tag in soup(['script','style','nav','footer']): tag.decompose()
  data={'url':r.url,'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'title':soup.title.get_text() if soup.title else key,'text':soup.get_text(' ',strip=True),'links':[{'text':a.get_text(' ',strip=True),'url':a.get('href')} for a in soup.find_all('a',href=True)]}
  (dest/f'{key}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
  return {'key':key,'status':r.status_code,'chars':len(data['text'])}
 except Exception as e: return {'key':key,'error':str(e)}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
 for result in ex.map(fetch,sources.items()): print(json.dumps(result),flush=True)
