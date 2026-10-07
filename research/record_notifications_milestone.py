import hashlib,json,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];research=ROOT/'research';v=research/'verification'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
suite=ET.parse(v/'tests.xml').getroot().find('testsuite');assert suite.attrib['tests']=='192' and suite.attrib['errors']=='0' and suite.attrib['failures']=='0'
archive=ROOT/'output/Isdas-Sunucu-Paketi-20261004-r8.zip'
browser=read(v/'notifications-browser-results.json');review=read(v/'notifications-review-browser-results.json');deployment=read(v/'deployment-results.json');main=read(v/'notifications-main-app.json')
assert all(x['passed'] for x in [browser,review,deployment]) and deployment['archive']==archive.name and main['web_assets_match_release_manifest']
next_flow='Complete personal agenda/task/reminder flow; real subscription payment, social publication, full voice/native mobile and own-server live acceptance remain. Existing requests for secure provider and commercial configuration remain pending.'
evidence={'date':'2026-10-04','goal_status':'active','notification_backend_tests':12,'backend_tests':192,'suite':suite.attrib,'browser':browser,'review_browser':review,'deployment':deployment,'release':{'file':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size},'main_app':main,'screenshots_reviewed':['notifications-desktop.png','notifications-mobile.png','notification-review-mobile.png'],'native_push_implemented':False,'live_model_tested':False,'live_google_tested':False,'live_smtp_tested':False,'own_server_deployed':False,'next_product_flow':next_flow,'boundaries':['in-app only; no new email or operating-system push','current authorized source states in last 90 days; not permanent audit history','seven-second active-tab polling, no immediate closed-app delivery','exact source open never executes or approves work','fixture Google data and local synthetic model/mail do not prove live service quality'],'still_required':['Actual payment collection and subscription lifecycle','Final commercial/legal/support information','Live model/Google/SMTP acceptance','Social publishing and complete video/carousel output','Native app and real-device full voice/push','Own-server TLS deployment and off-server restore','Paid pilot outcome and operating-cost validation']}
write(v/'notifications-implementation.json',evidence)
p=research/'implementation-status.json';s=read(p);s['previous_goal_turn']=s['this_goal_turn'];s['this_goal_turn']='progress: account-wide authorized in-app notifications and exact result/action/content/review handoff; real source transactions, read state, 90-day visibility, keyset pagination, source/role recheck, mobile keyboard and mixed-role workspace QA; 192 tests and r8 archive/local app upgrade with backup verified'
new_evidence=['../backend/notifications.py','../frontend/src/Notifications.tsx','../frontend/src/notifications.css','../tests/test_notifications.py','verification/notifications-implementation.json','verification/notifications-browser-results.json','verification/notifications-review-browser-results.json','../output/Bildirimler-ve-Sonuca-Gecis-Notlari.txt']
for req in s['requirements']:
 if req['id'] in {'R04','R06','R07','R10','R12'}:req['evidence']=list(dict.fromkeys(req.get('evidence',[])+new_evidence))
 if req['id']=='R10':
  req['status']='queue_and_authorized_in_app_result_handoff_implemented_locally_live_acceptance_pending'
  req['limit']=req.get('limit','')+' Yeni kaynak geçişleri uygulama içi bildirilir; doğru sonuç ve sohbet bağlantısı, okunma ve geçersiz erişim yerel testlerde doğrulandı. Canlı sağlayıcı kalitesi ve gerçek operasyon kabulü bekliyor.'
 if req['id']=='R12':
  req['status']='responsive_web_and_in_app_notifications_implemented_native_voice_push_pending'
  req['limit']='Masaüstü ve mobil web bildirimi, klavye odağı, tam kaynak bağlantıları ve farklı roller arası alan geçişi doğrulandı. Native uygulama, gerçek cihaz ses ve kapalı uygulama push bildirimi hazır değildir.'
 if '../output/Isdas-Sunucu-Paketi-20261004-r7.zip' in req.get('evidence',[]):req['evidence']=[x.replace('Isdas-Sunucu-Paketi-20261004-r7.zip',archive.name) for x in req['evidence']]
checks=s['current_verification'];checks['backend_tests']=192
checks['browser_checks']+=['in-app completion notice to exact result and continuing same conversation','notification read state, keyset pages and visible-only read batch','cross-workspace and owner/reviewer role-switch handoff','exact older action beyond first 100 opens without approval','source changed after notice is rejected and retry refreshes','client feedback/fresh approval to exact editor; stale review and revoked access cleared']
checks['isolated_browser_test_server']='notification model and captured-mail servers stopped after acceptance; no live provider writes'
checks['latest_release']=archive.name;s['user_steering']['next']=next_flow;write(p,s)
p=research/'product-experience-backlog.json';s=read(p);s['current_verified_milestone']='Public site and guided setup now lead to persistent work and account-wide in-app result/action/client-review notifications with exact source handoff, mobile layout, current role checks and read state. 192 tests and r8 local release verified; full launch readiness remains incomplete.';s['next_product_flow']=next_flow
for row in s['acceptance']:
 if row['flow']=='Speak or write to a finished result':row['state']='persistent text conversations, real queue/status, in-app completion/failure notice, exact result and same-conversation continuation implemented locally; live model quality and full voice pending'
 if row['flow']=='Personal assistant and employee productivity':row['state']='local calendar/routines, Google draft/calendar review adapter and in-app outcome notifications exist; complete tasks/reminders, live integration acceptance and additional integrations pending'
 if row['flow']=='Agency collaboration':row['state']='owner/member/reviewer lifecycle, exact-version customer review, feedback/approval notifications to exact editor and mixed-role workspace switch implemented locally; live customer pilot and advanced permissions remain'
 if row['flow']=='Mobile and accessibility':row['state']='responsive desktop/mobile flows, notification dialog keyboard focus and exact source links verified in headless Chrome; native, real-device voice and OS push missing'
write(p,s);print(json.dumps({'recorded':True,'backend_tests':192,'release':archive.name,'goal_status':'active'}))
