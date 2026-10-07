import hashlib,json,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];research=ROOT/'research';v=research/'verification'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
suite=ET.parse(v/'tests.xml').getroot().find('testsuite');assert suite.attrib['tests']=='180' and suite.attrib['errors']=='0' and suite.attrib['failures']=='0'
archive=ROOT/'output/Isdas-Sunucu-Paketi-20261004-r7.zip';browser=read(v/'marketing-browser-results.json');verification=read(v/'marketing-verification-browser-results.json');deployment=read(v/'deployment-results.json');main=read(v/'marketing-main-app.json')
assert all(r['passed'] for r in [browser,verification,deployment]) and deployment['archive']==archive.name and main['web_assets_match_release_manifest']
next_flow='In-app task completion/action notifications and direct result handoff; live payment, social publishing, full voice/native mobile and own-server launch remain.'
evidence={'date':'2026-10-04','goal_status':'active','public_site_tests':4,'backend_tests':180,'test_suite':suite.attrib,'browser':browser,'email_gate_intent':verification,'onboarding_regression':read(v/'onboarding-browser-results.json'),'contrast':read(v/'marketing-contrast-results.json'),'deployment':deployment,'release':{'file':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size},'main_app':main,'screenshots_reviewed':['marketing-home-desktop.png','marketing-home-mobile.png','marketing-tour-desktop.png','marketing-pricing-desktop.png','marketing-pricing-mobile.png','marketing-menu-mobile.png'],'live_checkout_tested':False,'live_model_tested':False,'live_smtp_tested':False,'own_server_deployed':False,'next_product_flow':next_flow,'still_required':['Actual payment collection and subscription lifecycle','Final commercial/legal/support information','Live model/Google/SMTP acceptance','Social publishing and complete video/carousel output','Native app and real-device full voice/push','Own-server TLS deployment and off-server restore','Paid pilot outcome and operating-cost validation']}
write(v/'marketing-implementation.json',evidence)
p=research/'implementation-status.json';s=read(p);s['previous_goal_turn']=s['this_goal_turn'];s['this_goal_turn']='progress: eight-page original-character public site, read-only product tour, 14-expert filter/details, honest preview plans backed by billing policy, connection capability page, searchable help and persona/goal handoff through signup/verification; 180 tests, responsive browser QA, text contrast improvements; r7 archive drill and local app upgrade verified'
for req in s['requirements']:
 if req['id']=='R04':
  req['status']='original_brand_guided_setup_and_public_site_implemented_locally_full_product_and_live_pilot_pending'
  req['evidence']+=['../frontend/src/Marketing.tsx','../frontend/src/Marketing.css','../frontend/src/setupIntent.ts','../backend/public_site.py','../tests/test_public_site.py','verification/marketing-implementation.json','verification/marketing-browser-results.json','verification/marketing-verification-browser-results.json','../output/Tanitim-Sitesi-ve-Kayit-Akisi-Notlari.txt']
  req['limit']='Sekiz kamu sayfası, örnek iş turu, uzman kataloğu, bağlantı durumları, yardım ve kurulum geçişi yerel tarayıcıda doğrulandı. Örnek tur gerçek iş başlatmaz. Tam ürün, marka uygunluğu, canlı ticari/hukuki koşullar ve kullanıcı pilotu bekliyor.'
 if req['id']=='R11':
  req['evidence']+=['../backend/public_site.py','../frontend/src/Marketing.tsx','verification/marketing-browser-results.json']
  req['limit']=req.get('limit','')+' Kamu paket sayfası mevcut plan kapsamını açıkça taslak olarak gösterir; seçim kurulum açar, ödeme veya kullanım hakkı oluşturmaz.'
 if '../output/Isdas-Sunucu-Paketi-20261004-r6.zip' in req.get('evidence',[]):req['evidence']=[x.replace('Isdas-Sunucu-Paketi-20261004-r6.zip',archive.name) for x in req['evidence']]
checks=next(x for x in s.values() if isinstance(x,dict) and 'backend_tests' in x);checks['backend_tests']=180
checks['browser_checks']+=['eight public pages and labelled read-only interactive tour','expert filtering/modal and goal handoff','preview pricing sourced from actual policy without checkout','mobile menu Escape/focus, help search, empty recovery and catalogue retry','public intent survives signup and separate email verification gate','existing account opens explicit new-area form; no automatic workspace grant','public 404 and individual server-supplied title/description metadata']
checks['isolated_browser_test_server']='stopped after marketing, verification-intent and onboarding acceptance';checks['public_text_contrast']='heuristic computed contrast on five default pages passes; not a full accessibility audit'
s['user_steering']['next']=next_flow;write(p,s)
p=research/'product-experience-backlog.json';s=read(p);s['current_verified_milestone']='Eight public pages link to persona-specific setup through registration/email verification; original character, clear product tour, truthful preview plans and existing result/client approval flows tested locally. This is not full launch readiness.';s['next_product_flow']=next_flow
for row in s['acceptance']:
 if row['flow']=='Landing to first setup':row['state']='eight-page public site, original character, read-only example tour, policy-sourced preview plans, capability status and searchable help now link to persisted persona/goal setup through signup/verification; actual checkout and final legal/commercial content remain'
write(p,s);print(json.dumps({'recorded':True,'backend_tests':180,'release':archive.name,'goal_status':'active'}))
