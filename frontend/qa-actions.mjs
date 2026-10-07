import {createAndSkip} from './qa-setup-helper.mjs';
// Browser rendering and interaction test. Google and action routes below are
// explicitly mocked; no token is installed and no external account is changed.
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
const out=path.resolve('../research/verification');
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1050},reducedMotion:'reduce',timezoneId:'America/New_York'});
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
let approvals=0;const checks={};
try{
 await page.goto('http://127.0.0.1:8001/app',{waitUntil:'networkidle'});
 await page.getByLabel('Adın',{exact:true}).fill('Onay testi');
 await page.getByLabel('E-posta',{exact:true}).fill(`action-qa-${Date.now()}@example.test`);
 await page.getByLabel('Parola',{exact:true}).fill('Local-test-'+crypto.randomUUID());
 await page.getByRole('button',{name:'Çalışma alanımı oluştur',exact:true}).click();
 const dialog=page.getByRole('dialog');await createAndSkip(page,'Test A · Onay akışı');
 const pilot=await page.evaluate(()=>fetch('/api/test/pilot',{method:'POST'}).then(r=>r.json()));if(!pilot.synthetic_test_only)throw Error('Requires isolated browser test server');
 await page.getByRole('button',{name:'Çalışma alanı ekle',exact:true}).click();
 await createAndSkip(page,'Test B · Ayrı alan');
 const spaces=await page.evaluate(()=>fetch('/api/workspaces').then(r=>r.json()));
 const first=spaces.find(s=>s.name.startsWith('Test A')),second=spaces.find(s=>s.name.startsWith('Test B'));
 await page.getByLabel('Çalışma alanı',{exact:true}).selectOption(second.id);
 let release,captured=false,delayed=false;
 const gate=new Promise(resolve=>release=resolve);
 await page.route(`**/api/workspaces/${first.id}/jobs`,async route=>{
  if(!delayed){delayed=true;captured=true;await gate;await route.fulfill({json:[{id:'late-job',prompt:'A ALANININ GECİKMİŞ ÖZEL SONUCU',status:'completed',stage:'Hazır',output:'Sadece A',created:Date.now()/1000}]})}
  else await route.continue();
 });
 await page.getByLabel('Çalışma alanı',{exact:true}).selectOption(first.id);
 await expect.poll(()=>captured).toBe(true);
 await page.getByLabel('Çalışma alanı',{exact:true}).selectOption(second.id);
 const delivered=page.waitForResponse(r=>r.url().endsWith('/'+first.id+'/jobs'));release();await delivered;await page.getByRole('navigation').getByRole('button',{name:'İşlerim',exact:true}).click();
 await expect(page.getByText('A ALANININ GECİKMİŞ ÖZEL SONUCU',{exact:true})).toHaveCount(0);
 await expect(page.locator('.app-header')).toContainText('Test B');
 checks.late_workspace_response_ignored=true;
 const time=Date.now()/1000;
 let rows=[{id:'mock-email',kind:'google.gmail.draft',status:'pending',payload_hash:'a'.repeat(64),account_label:'TEST · hesap@example.test',job_id:null,created:time,updated:time,payload:{to:'alici@example.test',subject:'Test: Haftalık içerik planı',body:'Merhaba,\nBu yalnızca arayüz doğrulaması için hazırlanmış bir örnektir.\nGerçek e-posta gönderilmez.'},result:{}},
 {id:'mock-calendar',kind:'google.calendar.create',status:'pending',payload_hash:'b'.repeat(64),account_label:'TEST · hesap@example.test',job_id:null,created:time,updated:time,payload:{title:'Test: İçerik değerlendirme',starts_at:'2026-10-10T10:00:00+03:00',ends_at:'2026-10-10T10:30:00+03:00',notes:'Örnek plan; gerçek takvime yazılmaz.'},result:{}}];
 await page.route(`**/api/workspaces/${second.id}/connections`,r=>r.fulfill({json:[{id:'google',name:'Google',description:'TEST bağlantısı',connected:true,available:true,account:'TEST · hesap@example.test'}]}));
 await page.route(`**/api/workspaces/${second.id}/actions**`,async route=>{
  const request=route.request(),tail=new URL(request.url()).pathname.split('/actions')[1];
  if(request.method()==='GET')return route.fulfill({json:rows});
  const body=request.postDataJSON(),id=tail.split('/')[1],row=rows.find(r=>r.id===id);
  if(request.method()==='PUT'){row.payload=body.payload;row.payload_hash='c'.repeat(64);return route.fulfill({json:row})}
  if(tail.endsWith('/approve')){approvals++;if(body.payload_hash!==row.payload_hash)throw Error('Stale review sent');row.status='completed';row.result={message:'TEST: Taslak oluşturma yanıtı. Gerçek hesaba yazılmadı.'};return route.fulfill({json:row})}
  throw Error('Unexpected mocked action request');
 });
 await page.getByLabel('Çalışma alanı',{exact:true}).selectOption(first.id);
 await page.getByLabel('Çalışma alanı',{exact:true}).selectOption(second.id);
 await expect(page.getByRole('heading',{name:'Test: Haftalık içerik planı',exact:true})).toBeVisible();
 await page.screenshot({path:path.join(out,'actions-desktop.png'),fullPage:true});
 await page.getByRole('button',{name:/Test: Haftalık içerik planı/}).click();
 await expect(dialog).toContainText('TEST · hesap@example.test');
 await expect(dialog).toContainText('E-posta gönderilmez.');
 await page.screenshot({path:path.join(out,'action-review-desktop.png'),fullPage:true});
 await dialog.getByRole('button',{name:'Düzenle',exact:true}).click();
 await dialog.getByLabel('Konu',{exact:true}).fill('Test: Düzenlenen başlık');
 await dialog.getByRole('button',{name:'Önizlemeyi hazırla',exact:true}).click();
 await expect(dialog.getByRole('heading',{name:'Test: Düzenlenen başlık',exact:true})).toBeVisible();
 if(approvals!==0)throw Error('Editing unexpectedly executed action');
 await dialog.getByRole('button',{name:'Onayla ve kaydet',exact:true}).click();
 await expect(dialog).toContainText('Gerçek hesaba yazılmadı.');
 await expect(dialog.getByRole('button',{name:'Onayla ve kaydet',exact:true})).toHaveCount(0);
 if(approvals!==1)throw Error('Unexpected approval count');
 checks.review_edit_approve=true;
 await dialog.getByRole('button',{name:'Kapat',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:/Test: İçerik değerlendirme/}).click();
 await expect(dialog).toContainText('Katılımcı daveti gönderilmez.');await expect(dialog).toContainText('10:00 (UTC+03:00)');checks.original_timezone_preserved=true;
 await page.screenshot({path:path.join(out,'action-review-mobile.png'),fullPage:false});
 const overflow=await page.evaluate(()=>({width:innerWidth,document:document.documentElement.scrollWidth,dialog:document.querySelector('dialog[open]').getBoundingClientRect().width}));
 if(overflow.document>overflow.width||overflow.dialog>overflow.width)throw Error('Mobile overflow');
 if(errors.length)throw Error(errors.join('; '));
 await fs.writeFile(path.join(out,'actions-browser-results.json'),JSON.stringify({passed:true,checks,approvals,overflow,errors,google_api:'mocked',live_google_tested:false},null,2));
 console.log(JSON.stringify({passed:true,checks,approvals,live_google_tested:false}));
}finally{await browser.close()}
