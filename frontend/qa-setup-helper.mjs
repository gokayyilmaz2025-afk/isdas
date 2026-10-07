import {expect} from '@playwright/test';
// Exercise explicit guided setup/skip; fixtures must follow the current UI.
export async function createAndSkip(page,name,kind='business',profile={}){
 await expect(page.getByRole('heading',{name:"İşdaş'ı nasıl kullanacaksın?",exact:true})).toBeVisible();
 await page.getByRole('radio',{name:new RegExp({personal:'Kendim için',business:'İşletmem için',agency:'Ajansım için'}[kind])}).check();
 await page.getByLabel('Çalışma alanının adı',{exact:true}).fill(name);
 if(profile.sector)await page.getByLabel('İşin veya sektörün',{exact:false}).fill(profile.sector);
 await page.getByRole('button',{name:'Alanımı hazırlayalım',exact:true}).click();
 if(profile.tone){await page.getByLabel('Nasıl bir dil kullanalım?',{exact:true}).fill(profile.tone);await page.getByRole('button',{name:'Kaydet ve devam et',exact:true}).click()}
 else await page.getByRole('button',{name:'Şimdilik geç',exact:true}).click();
 await page.getByRole('button',{name:'Bağlantılara geç',exact:true}).click();
 await page.getByRole('button',{name:'İlk işimi seçeyim',exact:true}).click();
 await page.getByRole('button',{name:'Şimdilik çalışma alanıma geç',exact:true}).click();
 await expect(page.getByRole('button',{name:'Sohbetler',exact:true})).toBeVisible();
}
