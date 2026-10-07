"""Isolated browser acceptance app. Synthetic local model; no external billing.

Run explicitly with --database under tmp; never points at the user's database.
"""
import argparse,json,os,sys,threading,time,base64,io,datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);parser.add_argument('--port',type=int,default=8001);parser.add_argument('--model-port',type=int,default=8012);parser.add_argument('--evidence',required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    database=Path(args.database).resolve();evidence=Path(args.evidence).resolve()
    if not database.is_relative_to(root/'tmp'):raise SystemExit('Use a database inside the project tmp directory.')
    if database.exists():raise SystemExit('Choose a fresh test database; existing data will not be overwritten.')
    database.parent.mkdir(parents=True,exist_ok=True);evidence.parent.mkdir(parents=True,exist_ok=True)
    requests=[];guard=threading.Lock()
    class Model(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if self.path not in {'/v1/responses','/v1/images/generations'}:self.send_error(404);return
            if self.headers.get('Authorization')!='Bearer local-browser-test':self.send_error(403);return
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            if self.path=='/v1/images/generations':
                from PIL import Image,ImageDraw
                picture=Image.new('RGB',(256,256),(38,91,220));draw=ImageDraw.Draw(picture);draw.rectangle((32,32,224,224),outline='white',width=3);draw.text((60,114),'LOCAL TEST IMAGE',fill='white')
                stream=io.BytesIO();picture.save(stream,format='PNG')
                with guard:
                    requests.append({'synthetic_image':True,'request':body})
                    evidence.write_text(json.dumps({'synthetic_local_model':True,'requests':requests},ensure_ascii=False,indent=2),encoding='utf-8')
                time.sleep(4)
                encoded=json.dumps({'id':'image-browser-test','data':[{'b64_json':base64.b64encode(stream.getvalue()).decode()}],'usage':{'cost_in_usd_ticks':400000000}}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded);return
            messages=body.get('input',[]);prompt=messages[-1]['content'];prior=sum(m['role']=='assistant' for m in messages)
            with guard:
                requests.append({'input':messages,'tools':body.get('tools',[]),'store':body.get('store')})
                evidence.write_text(json.dumps({'synthetic_local_model':True,'requests':requests},ensure_ascii=False,indent=2),encoding='utf-8')
            # A predictable visible running state for cancellation/animation QA.
            time.sleep(4)
            output=f'TEST YANITI — gerçek model çıktısı değildir.\nÖnceki yanıt sayısı: {prior}\nSon istek: {prompt}\n\nBu yerel kabul testi, konuşma geçmişinin gerçek uygulama kuyruğundan geçmesini doğrular.'
            result=[{'type':'message','role':'assistant','content':[{'type':'output_text','text':output}]}]
            if 'Marka kitimi kullanarak' in prompt or 'TEST_CONTENT_PLAN' in prompt:
                posts=[{'title':f'TEST · Kahve molası {i+1}','platform':'instagram','format':'post','caption':f'TEST İÇERİĞİ {i+1}: Bu metin gerçek model üretimi değildir.','visual_brief':'TEST: Mavi masada beyaz kahve fincanı.','planned_local':(datetime.date.today()+datetime.timedelta(days=i)).isoformat()+'T10:00','timezone':'Europe/Istanbul'} for i in range(7)]
                result=[{'type':'function_call','name':'prepare_social_posts','arguments':json.dumps({'title':'TEST · Haftalık içerikler','posts':posts},ensure_ascii=False)}]
            if 'TEST_DOCUMENT_QUERY' in prompt:
                system=messages[0]['content'];start=system.index('{"workspace":')
                context=json.JSONDecoder().raw_decode(system[start:])[0]
                snippets=context.get('document_excerpts',[])
                output='TEST BELGE YANITI — gerçek model çıktısı değildir.\n'+'\n'.join(s['body'][:200]+' ['+s['code']+']' for s in snippets)
                result=[{'type':'message','role':'assistant','content':[{'type':'output_text','text':output}]}]
            if 'TEST_AGENDA_PLAN' in prompt:
                plan={'title':'TEST · Günlük planım','items':[{'title':'Sunumu hazırla','notes':'TEST: Yalnızca yerel örnek plan.','due_local':None,'timezone':'Europe/Istanbul','priority':'high','estimate_minutes':45,'remind_before':None},{'title':'Müşteri teklifini kontrol et','notes':'TEST: Gerçek müşteri verisi değildir.','due_local':None,'timezone':'Europe/Istanbul','priority':'normal','estimate_minutes':30,'remind_before':None},{'title':'Haftalık içerik fikirlerini yaz','notes':'TEST: Yerel örnek.','due_local':None,'timezone':'Europe/Istanbul','priority':'low','estimate_minutes':20,'remind_before':None}]}
                result=[{'type':'function_call','name':'prepare_agenda_plan','arguments':json.dumps(plan,ensure_ascii=False)}]
            encoded=json.dumps({'id':'browser-test-'+str(len(requests)),'status':'completed','output':result,'usage':{'input_tokens':100,'output_tokens':80,'cost_in_usd_ticks':50000}},ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    server=ThreadingHTTPServer(('127.0.0.1',args.model_port),Model)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    os.environ.update(DATABASE_PATH=str(database),APP_ORIGIN=f'http://127.0.0.1:{args.port}',PUBLIC_ORIGIN=f'http://127.0.0.1:{args.port}',XAI_API_KEY='local-browser-test',XAI_BASE_URL=f'http://127.0.0.1:{args.model_port}/v1',WORKER_ENABLED='true',REGISTRATION_ENABLED='true',HERMES_RUNTIME_MAP=str(root/'tmp'/'no-browser-hermes.json'))
    for key in ('GOOGLE_CLIENT_ID','GOOGLE_CLIENT_SECRET','ENCRYPTION_KEY'):os.environ[key]=''
    sys.path.insert(0,str(root))
    import uvicorn
    from backend.main import app,user
    from backend import billing,db,onboarding
    from fastapi import Depends,HTTPException
    # Deterministic public-page fixture for onboarding UI checks, never packaged.
    def synthetic_page(url):
        if url!='https://onboarding.example.test/':raise HTTPException(422,'This isolated test accepts only the synthetic website fixture.')
        return {'ok':True,'url':url,'title':'TEST · Mola Kahve','description':'Synthetic public-page fixture','text':'TEST WEB SAYFASI. Mola Kahve hafta içi 09.00–18.00 arasında açık olan örnek bir işletmedir. Bu metin gerçek bir web sitesinden alınmamıştır.','warnings':['Yerel test sayfası; gerçek site okuması değildir.']}
    onboarding.read_site=synthetic_page
    @app.post('/api/test/pilot')
    def test_pilot(u=Depends(user)):
        # Registered ONLY by this isolated test launcher, never by backend.main.
        existing=db.query("SELECT id FROM entitlement_periods WHERE account_id=? AND source='operator_pilot'",(u['id'],),one=True)
        if not existing:
            start=db.now();billing.grant_period(u['id'],'pilot',start,start+14*86400,'operator_pilot',u['id'],'Isolated browser test: no payment collected')
        return {'ok':True,'synthetic_test_only':True}
    # The production SPA catch-all is GET-only, so this test POST still matches.
    print('ISOLATED_BROWSER_TEST_APP: local synthetic model, no live credentials.',flush=True)
    try:uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')
    finally:server.shutdown()

if __name__=='__main__':main()
