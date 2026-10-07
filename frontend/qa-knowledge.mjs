import {createAndSkip} from './qa-setup-helper.mjs';
// Real browser / backend / isolated parser and synthetic HTTP model acceptance.
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
const out=path.resolve('../research/verification'),browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1100},reducedMotion:'reduce'}),page=await context.newPage(),errors=[],checks={};
page.on('pageerror',e=>errors.push(e.message));
const call=(url,method='GET',body)=>page.evaluate(async({url,method,body})=>{const r=await fetch('/api'+url,{method,headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined});return {status:r.status,data:await r.json()}},{url,method,body});
try{
 await page.goto('http://127.0.0.1:8001/app',{waitUntil:'networkidle'});
 await page.getByLabel('Adın',{exact:true}).fill('Belge testi');await page.getByLabel('E-posta',{exact:true}).fill(`documents-${Date.now()}@example.test`);await page.getByLabel('Parola',{exact:true}).fill('Test-only-'+crypto.randomUUID());await page.getByRole('button',{name:'Çalışma alanımı oluştur',exact:true}).click();
 const modal=page.getByRole('dialog');await createAndSkip(page,'TEST · Belge Atölyesi');
 const wid=(await call('/workspaces')).data[0].id,base=`/workspaces/${wid}`;
 if(!(await call('/test/pilot','POST')).data.synthetic_test_only)throw Error('Not the isolated test app');
 await page.getByRole('button',{name:'Hafıza',exact:true}).click();
 await page.getByLabel('Belge yükle',{exact:true}).setInputFiles({name:'TEST İşletme.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:await fs.readFile('../tmp/knowledge-fixture.docx')});
 await expect(modal).toBeVisible();await expect(modal).toContainText('İade süremiz 21 gündür.');await expect(modal).toContainText('Tablo 1 · satır 2');await expect(modal).toContainText('45 TL');
 await page.screenshot({path:path.join(out,'knowledge-document-desktop.png')});checks.docx_text_and_table_visible=true;
 await modal.getByRole('button',{name:'Kapat',exact:true}).click();await expect(page.locator('.document-card')).toHaveCount(1);
 await page.getByLabel('Belge yükle',{exact:true}).setInputFiles({name:'TEST Kosullar.pdf',mimeType:'application/pdf',buffer:await fs.readFile('../tmp/knowledge-fixture.pdf')});
 await expect(modal).toContainText('Sayfa 2');await expect(modal).toContainText('21 days');await modal.getByRole('button',{name:'Kapat',exact:true}).click();checks.pdf_pages_visible=true;
 const docs=(await call(base+'/documents')).data.items,did=docs.find(d=>d.kind==='docx').id;
 const anonymous=await browser.newContext();const privateResult=await anonymous.request.get(`http://127.0.0.1:8001/api${base}/documents/${did}/download`);if(privateResult.status()!==401)throw Error('Unauthenticated document exposed');await anonymous.close();checks.download_requires_session=true;
 await page.getByLabel('Belgelerde ara',{exact:true}).fill('İADE');await page.locator('.knowledge-search').getByRole('button',{name:'Ara',exact:true}).click();await expect(page.locator('.knowledge-hit')).toHaveCount(1);await expect(page.locator('.knowledge-hit')).toContainText('21 gündür');checks.search_finds_turkish_text=true;
 await page.getByRole('button',{name:'Sohbetler',exact:true}).click();await page.locator('.document-picker summary').click();await page.getByLabel('TEST İşletme.docx',{exact:true}).check();
 await page.getByLabel('Sohbet mesajı',{exact:true}).fill('TEST_DOCUMENT_QUERY İade süresi ve kargo hazırlığı ne kadar?');await page.getByRole('button',{name:'Gönder',exact:true}).click();
 await expect(page.locator('.chat-answer')).toContainText('TEST BELGE YANITI',{timeout:15000});await expect(page.locator('.chat-answer')).toContainText('[K1]');await expect(page.locator('.chat-answer')).toContainText('21 gündür');
 await page.locator('.document-sources>button').click();await expect(page.locator('.document-sources summary')).toContainText('TEST İşletme.docx');await page.locator('.document-sources summary').first().click();await expect(page.locator('.document-sources details p').first()).toContainText('21 gündür');checks.chat_uses_selected_document_and_exposes_source=true;
 await page.screenshot({path:path.join(out,'knowledge-chat-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'knowledge-chat-mobile.png'),fullPage:true});
 let width=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));if(width.page>width.viewport)throw Error('Mobile chat overflow');
 // Use URL view switch to avoid dependence on desktop-only navigation.
 await page.goto(`http://127.0.0.1:8001/app?view=memory&workspace=${wid}`,{waitUntil:'networkidle'});await expect(page.locator('.document-card')).toHaveCount(2);await page.screenshot({path:path.join(out,'knowledge-library-mobile.png'),fullPage:true});
 width=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));if(width.page>width.viewport)throw Error('Mobile library overflow');checks.mobile_chat_and_library_fit=true;
 await page.locator('.document-card').filter({hasText:'TEST İşletme.docx'}).click();await modal.getByLabel('İşdaşlarım yeni yanıtlarda kullanabilsin',{exact:true}).uncheck();await expect(modal.getByLabel('İşdaşlarım yeni yanıtlarda kullanabilsin',{exact:true})).not.toBeChecked();await expect(modal.getByLabel('İşdaşlarım yeni yanıtlarda kullanabilsin',{exact:true})).toBeEnabled();
 if((await call(base+'/document-search?q=iade')).data.items.length!==0)throw Error('Disabled document still retrieved');checks.disabled_document_not_searched=true;
 await modal.getByRole('button',{name:'Belgeyi kaldır',exact:true}).click();await modal.getByRole('button',{name:'Evet, belgeyi kaldır',exact:true}).click();await expect(modal).not.toBeVisible();await expect(page.locator('.document-card')).toHaveCount(1);
 const cid=(await call(base+'/conversations')).data.items[0].id;
 await page.goto(`http://127.0.0.1:8001/app?view=chat&workspace=${wid}&conversation=${cid}`,{waitUntil:'networkidle'});await page.locator('.document-sources>button').click();await page.locator('.document-sources summary').first().click();await expect(page.locator('.document-sources')).toContainText('Belge kaldırıldı.');await expect(page.locator('.chat-answer')).toContainText('21 gündür');checks.deleted_document_source_removed_history_explicitly_retained=true;
 if(errors.length)throw Error(errors.join('; '));
 await fs.writeFile(path.join(out,'knowledge-browser-results.json'),JSON.stringify({passed:true,checks,errors,live_model_tested:false,parser:'actual pypdf/python-docx isolated subprocess',model:'synthetic local HTTP response'},null,2));console.log(JSON.stringify({passed:true,checks}));
}catch(e){await page.screenshot({path:path.join(out,'knowledge-failure.png'),fullPage:true});throw e}finally{await browser.close()}
