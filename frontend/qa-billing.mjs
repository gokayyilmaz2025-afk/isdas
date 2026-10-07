import {createAndSkip} from './qa-setup-helper.mjs';
// Real application and credit ledger, isolated local provider only.
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
const out=path.resolve('../research/verification');
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce'});
const page=await context.newPage(),errors=[],checks={};page.on('pageerror',e=>errors.push(e.message));
const call=(url,method='GET',body)=>page.evaluate(async({url,method,body})=>{const r=await fetch('/api'+url,{method,headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined});return {status:r.status,data:await r.json()}},{url,method,body});
try{
 await page.goto('http://127.0.0.1:8001/app',{waitUntil:'networkidle'});
 await page.getByLabel('Adın',{exact:true}).fill('Kullanım testi');await page.getByLabel('E-posta',{exact:true}).fill(`billing-${Date.now()}@example.test`);await page.getByLabel('Parola',{exact:true}).fill('Local-test-'+crypto.randomUUID());await page.getByRole('button',{name:'Çalışma alanımı oluştur',exact:true}).click();
 const dialog=page.getByRole('dialog');await createAndSkip(page,'Test · Birinci marka');
 const wid=(await call('/workspaces')).data[0].id;
 await page.getByRole('button',{name:'Paket ve kullanım',exact:true}).click();
 await expect(page.locator('.allowance-panel')).toContainText('Henüz aktif bir paketin yok.');
 const denied=await call(`/workspaces/${wid}/jobs`,'POST',{prompt:'TEST: Haksız çalışmamalı',idempotency_key:crypto.randomUUID(),start_conversation:true});
 if(denied.status!==402)throw Error('Unfunded job admitted');checks.unfunded_account_blocked=true;
 const pilot=await call('/test/pilot','POST');if(!pilot.data.synthetic_test_only)throw Error('Must run only against isolated browser test app');
 await expect(page.locator('.allowance-panel')).toContainText('Deneme açık',{timeout:10000});
 await expect(page.locator('.allowance-numbers strong').first()).toHaveText('300');checks.pilot_is_explicit_and_not_payment=true;
 const other=(await call('/workspaces','POST',{name:'Test · İkinci marka',kind:'agency'})).data.id;
 const accepted=await call(`/workspaces/${other}/jobs`,'POST',{prompt:'TEST: İkinci marka için bir taslak.',idempotency_key:crypto.randomUUID(),agent_id:'guide'});if(accepted.status!==200)throw Error('Pilot job not accepted');
 await expect.poll(async()=>((await call(`/workspaces/${wid}/billing`)).data.held_milli),{timeout:3000}).toBe(25000);checks.owner_balance_shared_across_workspaces=true;
 await expect(page.locator('.billing-receipt')).toContainText('Kullanım işlendi',{timeout:15000});
 await expect(page.locator('.billing-receipt')).toContainText('Test · İkinci marka');
 await expect(page.locator('.billing-receipt>b')).toHaveText('0,001 puan');
 await expect(page.locator('.allowance-numbers strong').first()).toHaveText('299,999');checks.real_provider_receipt_reconciles_customer_balance=true;
 await page.screenshot({path:path.join(out,'billing-desktop.png'),fullPage:true});
 await page.reload({waitUntil:'networkidle'});await expect(page.locator('.billing-receipt')).toHaveCount(1);checks.reload_preserves_usage=true;
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'billing-mobile.png'),fullPage:true});
 const widths=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));if(widths.page>widths.viewport)throw Error('Mobile horizontal overflow');
 if(errors.length)throw Error(errors.join('; '));
 await fs.writeFile(path.join(out,'billing-browser-results.json'),JSON.stringify({passed:true,checks,widths,errors,live_provider_tested:false,live_payment_tested:false,model:'synthetic local HTTP service'},null,2));
 console.log(JSON.stringify({passed:true,checks,live_payment_tested:false}));
}catch(e){await page.screenshot({path:path.join(out,'billing-failure.png'),fullPage:true});throw e}finally{await browser.close()}
