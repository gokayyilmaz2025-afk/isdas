import datetime,hashlib,json,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];research=ROOT/'research';v=research/'verification'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(p,data):p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
suite=ET.parse(v/'tests.xml').getroot().find('testsuite')
assert suite.attrib['tests']=='176' and suite.attrib['errors']=='0' and suite.attrib['failures']=='0'
release=ROOT/'output/Isdas-Sunucu-Paketi-20261004-r6.zip'
browser=read(v/'onboarding-browser-results.json');deployment=read(v/'deployment-results.json')
assert browser['passed'] and deployment['passed'] and deployment['archive']==release.name
evidence={'date':'2026-10-04','goal_status':'active','onboarding_tests':15,'backend_tests':176,'test_suite':suite.attrib,'browser':browser,'real_public_page':read(v/'site-reader-public-result.json'),'deployment':deployment,'release':{'file':release.name,'sha256':hashlib.sha256(release.read_bytes()).hexdigest(),'bytes':release.stat().st_size},'main_app':read(v/'onboarding-main-app.json'),'browser_regressions':{name:read(v/(name+'-browser-results.json'))['passed'] for name in ['chat','content','membership','accounts']},'screenshots_reviewed':['setup-persona-desktop.png','setup-persona-mobile.png','setup-website-review-mobile.png','setup-first-task-desktop.png','setup-first-task-mobile.png'],'live_model_tested':False,'live_smtp_tested':False,'own_server_deployed':False,'next_product_flow':'Public commercial pages and consistent result/notification experience; integrations, payment lifecycle and native/mobile work remain.','still_required':['Actual payment checkout and subscription lifecycle','Social publishing and complete content formats','Live model/Google/SMTP acceptance','Native app and real-device full voice/push','Own-server deployment and off-server restore','End-to-end paid customer pilot']}
write(v/'onboarding-implementation.json',evidence)
p=research/'implementation-status.json';s=read(p)
s['previous_goal_turn']=s['this_goal_turn'];s['this_goal_turn']='progress: guided personal/business/agency setup, explicit reviewed HTTPS page-to-memory intake, resumable first real queued conversation with lost-response recovery, 176 tests and three-persona browser acceptance; chat/content/account/customer-review regressions; r6 isolated archive drill and main app migration with backup'
for req in s['requirements']:
    if req['id'] in {'R06','R07'}:
        req['evidence']+=['../backend/onboarding.py','../backend/site_reader.py','../frontend/src/Onboarding.tsx','../frontend/src/Onboarding.css','../tests/test_onboarding.py','verification/onboarding-implementation.json','verification/onboarding-browser-results.json','verification/site-reader-public-result.json','../output/Baslangic-Rehberi-ve-Site-Okuma-Notlari.txt']
    if req['id']=='R07':req['limit']+=' Rehberde HTTPS tek sayfa metni, kullanıcının açık inceleme/onayıyla hafızaya alınır. Tam site/JavaScript taraması değildir.'
    if '../output/Isdas-Sunucu-Paketi-20261004-r5.zip' in req.get('evidence',[]):req['evidence']=[e.replace('Isdas-Sunucu-Paketi-20261004-r5.zip',release.name) for e in req['evidence']]
verification=next(value for value in s.values() if isinstance(value,dict) and 'backend_tests' in value)
verification['backend_tests']=176
verification['browser_checks']+=['guided persona setup and profile revision persistence','explicit reviewed and edited website-to-memory intake; no implicit saving','real document upload inside setup','first job actual queue and synthetic HTTP provider; lost enqueue response recovered without duplicate','no entitlement means disabled job start; setup may be explicitly skipped','completed guide can be reopened and dismissed','390px responsive setup and first-task layout']
verification['isolated_browser_test_server']='stopped after onboarding, chat, content, account and membership acceptance'
verification['real_public_https_reader_tested']=True
s['user_steering']['next']=evidence['next_product_flow']
write(p,s)
p=research/'product-experience-backlog.json';s=read(p);s['current_verified_milestone']='Guided persona setup through first real queued conversation, explicit website review, document upload and existing client approvals work locally. Live providers and commercial/native launch remain.'
s['next_product_flow']=evidence['next_product_flow']
for flow in s['acceptance']:
    if flow['flow']=='Landing to first setup':flow['state']='guided personal/business/agency setup and original character UI implemented and browser-tested; complete commercial public pages and actual purchase flow remain'
    if flow['flow']=='Business information to personalized assistant':flow['state']='structured profile, explicit single-page HTTPS review, actual documents and optional connections implemented locally; live model quality and wider site ingestion unverified'
write(p,s)
print(json.dumps({'recorded':True,'tests':176,'release':release.name,'goal_status':'active'}))
