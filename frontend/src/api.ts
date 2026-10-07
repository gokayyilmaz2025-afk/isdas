export async function api(path:string,method='GET',body?:unknown){
  const response=await fetch('/api'+path,{method,credentials:'include',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined});
  const data=await response.json().catch(()=>({detail:'İşlem tamamlanamadı.'}));
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Bilgileri kontrol edip yeniden dene.');
  return data;
}
