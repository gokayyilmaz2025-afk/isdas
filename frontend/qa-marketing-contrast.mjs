import {chromium} from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage(),findings=[];
try{
 for(const path of ['/','/isdaslar','/paketler','/baglantilar','/yardim']){
  await page.goto('http://127.0.0.1:8000'+path,{waitUntil:'networkidle'});
  const rows=await page.evaluate(()=>{
   const luminance=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
   const rgb=s=>s.match(/[\d.]+/g)?.map(Number)||[0,0,0];const found=[];
   for(const e of document.querySelectorAll('.market *')){
    const text=[...e.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).join(' ').trim();const rect=e.getBoundingClientRect();if(!text||rect.width<4||rect.height<4)continue;
    const s=getComputedStyle(e);if(s.visibility==='hidden'||s.display==='none')continue;let bg=[255,255,255],p=e;
    while(p){const b=rgb(getComputedStyle(p).backgroundColor);if(b.length===3||b[3]===1){bg=b.slice(0,3);break}p=p.parentElement}
    const fg=rgb(s.color),a=luminance(fg.slice(0,3)),b=luminance(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05),large=parseFloat(s.fontSize)>=24||(parseFloat(s.fontSize)>=18.66&&parseInt(s.fontWeight)>=700),required=large?3:4.5;
    if(ratio<required-.02)found.push({selector:e.tagName+'.'+String(e.className).replace(/ /g,'.'),parent:e.parentElement.className,color:s.color,background:bg,ratio:+ratio.toFixed(2),text:text.slice(0,65)});
   }
   return found;
  });findings.push({path,findings:rows});
 }
 const total=findings.reduce((n,p)=>n+p.findings.length,0);await fs.writeFile(path.resolve('../research/verification/marketing-contrast-results.json'),JSON.stringify({passed:total===0,scope:'Heuristic computed text contrast on five default public pages, opaque nearest backgrounds; not a full accessibility audit',findings},null,2));console.log(JSON.stringify({total,findings:findings.filter(p=>p.findings.length)}));
}finally{await browser.close()}
