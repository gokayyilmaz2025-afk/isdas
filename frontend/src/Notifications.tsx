import {useEffect,useRef,useState} from 'react';
import {Bell,CalendarDays,Check,ChevronLeft,ChevronRight,Clock3,FileCheck2,LoaderCircle,RefreshCw,ShieldCheck,X} from 'lucide-react';
import {api} from './api';
import './notifications.css';

type Notice={id:string,workspace_name:string,kind:'job'|'action'|'review'|'agenda',state:string,title:string,summary:string,created:number,read:boolean};
type Feed={items:Notice[],unread_count:number,next_cursor:string|null,retention_days:number};
const empty:Feed={items:[],unread_count:0,next_cursor:null,retention_days:90};
const icons={job:FileCheck2,action:ShieldCheck,review:Clock3,agenda:CalendarDays};
const stamp=(value:number)=>new Date(value*1000).toLocaleString('tr-TR',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});

/** Account-wide inbox. Opening a notice rechecks access on the server. */
export function NotificationBell(){
 const [open,setOpen]=useState(false),[feed,setFeed]=useState<Feed>(empty),[error,setError]=useState(''),[loading,setLoading]=useState(true),[busy,setBusy]=useState(''),[unread,setUnread]=useState(false),[kind,setKind]=useState('all'),[pages,setPages]=useState(['']);
 const trigger=useRef<HTMLButtonElement>(null),dialog=useRef<HTMLDialogElement>(null),alive=useRef(false),serial=useRef(0);
 const cursor=pages[pages.length-1];
 async function load(){
  const order=++serial.current;
  try{const r=await api('/notifications?'+new URLSearchParams({cursor,unread:String(unread),kind}));if(alive.current&&order===serial.current){setFeed(r);setError('')}}
  catch(e){if(alive.current&&order===serial.current){setFeed(empty);setError((e as Error).message)}}
  finally{if(alive.current&&order===serial.current)setLoading(false)}
 }
 useEffect(()=>{alive.current=true;setLoading(true);setFeed(empty);load();const refresh=()=>{if(document.visibilityState==='visible')load()};const timer=setInterval(refresh,7000);window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);return()=>{alive.current=false;serial.current++;clearInterval(timer);window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh)}},[cursor,unread,kind]);
 useEffect(()=>{if(open){dialog.current?.showModal();load()}else dialog.current?.close()},[open]);
 async function markVisible(){
  const ids=feed.items.filter(n=>!n.read).map(n=>n.id);if(!ids.length||busy)return;
  setBusy('read');serial.current++;setError('');
  try{await api('/notifications/read','POST',{ids});if(alive.current)await load()}catch(e){if(alive.current){setError((e as Error).message);setFeed(empty)}}finally{if(alive.current)setBusy('')}
 }
 async function follow(id:string){
  if(busy)return;setBusy(id);serial.current++;setError('');
  try{const r=await api('/notifications/'+id+'/open','POST');if(!alive.current)return;const target=new URL(r.href,location.origin);if(target.origin!==location.origin||target.pathname!=='/app')throw Error('Bildirim bağlantısı açılamadı.');location.assign(target.href)}
  catch(e){if(alive.current){setFeed(empty);setError((e as Error).message);setBusy('')}}
 }
 function close(){dialog.current?.close();setOpen(false);trigger.current?.focus()}
 const count=feed.unread_count;
 return <>
  <button ref={trigger} className={'notice-bell icon-button'+(error?' has-error':'')} aria-label={error?'Bildirimler · Yeniden yükle':`Bildirimler${count?' · '+count+' okunmamış':''}`} title="Bildirimler" aria-haspopup="dialog" onClick={()=>setOpen(true)}><Bell size={21}/>{count>0&&<span className="notice-badge" aria-hidden="true">{count>99?'99+':count}</span>}{error&&<span className="notice-unavailable" aria-hidden="true">!</span>}</button>
  {open&&<dialog ref={dialog} className="notice-dialog" aria-labelledby="notice-title" onCancel={e=>{e.preventDefault();close()}} onClick={e=>{if(e.target===e.currentTarget)close()}}>
   <div className="notice-heading"><span className="role-icon blue"><Bell size={23}/></span><div><h2 id="notice-title">İşlerinden haber var.</h2><p>{count?`${count} okunmamış bildirim`:'Tüm çalışma alanlarından gelişmeler'}</p></div><button className="icon-button" aria-label="Bildirimleri kapat" onClick={close}><X size={21}/></button></div>
   <div className="notice-filters"><div className="notice-tabs" role="group" aria-label="Bildirim durumu"><button disabled={!!busy} aria-pressed={!unread} className={!unread?'active':''} onClick={()=>{setUnread(false);setPages([''])}}>Tümü</button><button disabled={!!busy} aria-pressed={unread} className={unread?'active':''} onClick={()=>{setUnread(true);setPages([''])}}>Okunmamış</button></div><select disabled={!!busy} aria-label="Bildirim türü" value={kind} onChange={e=>{setKind(e.target.value);setPages([''])}}><option value="all">Bütün işler</option><option value="job">İş sonuçları</option><option value="action">Hesap işlemleri</option><option value="review">Müşteri onayları</option><option value="agenda">Hatırlatmalar</option></select></div>
   {error&&<div className="notice-error" role="alert"><p>{error}</p><button className="button secondary small" disabled={!!busy} onClick={()=>{setLoading(true);load()}}><RefreshCw size={15}/>Yeniden yükle</button></div>}
   {loading?<div className="notice-empty" role="status"><LoaderCircle className="spin"/><p>Gelişmelere bakıyorum.</p></div>:!feed.items.length&&!error?<div className="notice-empty"><img src="/mascot.png" alt=""/><h3>{unread?'Hepsine göz attın.':pages.length>1?'Bu sayfada bildirim kalmadı.':'Haberleri burada buluşturalım.'}</h3><p>{unread?'Yeni bir gelişme olduğunda burada görünecek.':'İşin bittiğinde, bir işlem onay beklediğinde veya içerik incelemesi geldiğinde burada göreceksin.'}</p></div>:<div className="notice-list" aria-busy={!!busy}>{feed.items.map(n=>{const Icon=icons[n.kind];return <button key={n.id} className={'notice-row'+(!n.read?' unread':'')} disabled={!!busy} onClick={()=>follow(n.id)}><span className={'notice-symbol '+n.kind}>{busy===n.id?<LoaderCircle size={21} className="spin"/>:<Icon size={21}/>}</span><span className="notice-copy"><span className="notice-meta"><span>{n.workspace_name}</span><time dateTime={new Date(n.created*1000).toISOString()}>{stamp(n.created)}</time></span><strong>{n.title}{!n.read&&<span className="notice-dot" aria-label="Okunmamış"/>}</strong><span className="notice-summary">{n.summary}</span></span><ChevronRight className="notice-chevron" size={17}/></button>})}</div>}
   <div className="notice-footer"><button className="text-link" disabled={!!busy||!feed.items.some(n=>!n.read)} onClick={markVisible}><Check size={15}/>{busy==='read'?'Kaydediliyor…':'Listelenenleri okundu işaretle'}</button>{(pages.length>1||feed.next_cursor)&&<div className="notice-pagination"><button className="icon-button" aria-label="Daha yeni bildirimler" disabled={pages.length===1||!!busy} onClick={()=>setPages(p=>p.slice(0,-1))}><ChevronLeft size={19}/></button><span>{pages.length}. sayfa</span><button className="icon-button" aria-label="Daha eski bildirimler" disabled={!feed.next_cursor||!!busy} onClick={()=>feed.next_cursor&&setPages(p=>[...p,feed.next_cursor!])}><ChevronRight size={19}/></button></div>}<small>Son 90 gündeki geçerli gelişmeler. Durumu değişen istekler güncellenir.</small></div>
  </dialog>}
 </>;
}
