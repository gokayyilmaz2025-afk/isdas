import {useEffect,useRef,useState} from 'react';
import {CalendarDays,Check,FileText,LoaderCircle,Plus,X} from 'lucide-react';
import {api} from './api';
type Row=Record<string,any>;
const labels:Record<string,string>={pending:'Onayını bekliyor',executing:'Hesabına kaydediliyor',completed:'Kaydedildi',failed:'Kontrol gerekiyor',uncertain:'Sonucu kontrol et',rejected:'Vazgeçildi',invalidated:'Bağlantı değişti'};
const title=(row:Row)=>row.kind==='google.calendar.create'?'Google Takvim kaydı':'Gmail taslağı';
const displayDate=(value:string)=>{const offset=value.endsWith('Z')?'+00:00':value.slice(-6);const wallTime=value.replace(/(Z|[+-]\d{2}:\d{2})$/,'Z');return new Date(wallTime).toLocaleString('tr-TR',{dateStyle:'long',timeStyle:'short',timeZone:'UTC'})+' (UTC'+offset+')'};

export function ActionsPanel({wid,canApprove,connected,focusId,onFocusClosed}:{wid:string,canApprove:boolean,connected:boolean,focusId?:string,onFocusClosed?:()=>void}){
  const [rows,setRows]=useState<Row[]>([]),[review,setReview]=useState<Row|null>(null),[creating,setCreating]=useState(''),[editing,setEditing]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const alive=useRef(true),dialog=useRef<HTMLDialogElement>(null);const path='/workspaces/'+wid+'/actions';
  async function refresh(){const value=await api(path);if(alive.current)setRows(value)}
  useEffect(()=>{alive.current=true;refresh().catch(e=>setError(e.message));const timer=setInterval(()=>refresh().catch(()=>{}),3500);return()=>{alive.current=false;clearInterval(timer)}},[wid]);
  useEffect(()=>{if(review||creating)dialog.current?.showModal();else dialog.current?.close()},[review,creating]);
  useEffect(()=>{if(!focusId)return;let valid=true;api(path+'/'+encodeURIComponent(focusId)).then(row=>{if(valid&&alive.current){setReview(row);setEditing(false)}}).catch(e=>valid&&alive.current&&setError(e.message));return()=>{valid=false}},[wid,focusId]);
  function close(){if(busy)return;setReview(null);setCreating('');setEditing(false);setError('');onFocusClosed?.()}
  async function act(fn:()=>Promise<void>){if(busy)return;setBusy(true);setError('');try{await fn();await refresh()}catch(e){if(alive.current)setError((e as Error).message)}finally{if(alive.current)setBusy(false)}}
  async function decide(decision:string){if(!review)return;const result=await api(path+'/'+review.id+'/'+decision,'POST',decision==='check'?undefined:{payload_hash:review.payload_hash});if(alive.current)setReview(result)}
  const editable=review&&['pending','failed'].includes(review.status);
  return <section className="actions-section"><div className="section-heading"><div><h2>Hesaplarında yapılacak işler</h2><p>İçeriği ve hesabı incele, ardından onayla.</p></div>{canApprove&&<div className="button-row"><button className="button secondary small" disabled={!connected} onClick={()=>setCreating('google.gmail.draft')}><Plus size={16}/>E-posta taslağı</button><button className="button secondary small" disabled={!connected} onClick={()=>setCreating('google.calendar.create')}><CalendarDays size={16}/>Takvim kaydı</button></div>}</div>
  {!connected&&<p className="muted note">Google hesabını Bağlantılar bölümünden bağladığında bu işlemleri kullanabilirsin.</p>}
  {error&&!review&&!creating&&<p className="error" role="alert">{error}</p>}
  <div className="action-grid">{rows.map(row=><button className={'action-card '+row.status} key={row.id} onClick={()=>{setReview(row);setEditing(false);setError('')}}><span className="role-icon blue">{row.kind==='google.calendar.create'?<CalendarDays size={20}/>:<FileText size={20}/>}</span><span className="pill">{labels[row.status]||row.status}</span><h3>{row.payload.title||row.payload.subject}</h3><p>{title(row)}</p><small>{row.account_label}</small><span className="action-open">Ayrıntıları incele</span></button>)}</div>
  {!rows.length&&<p className="muted note">İşdaşlarının hazırladığı hesap işlemleri burada onayını bekler.</p>}
  {(review||creating)&&<dialog ref={dialog} className="action-dialog" onCancel={e=>{e.preventDefault();close()}}><div className="modal-head"><h2>{creating?(creating==='google.calendar.create'?'Takvim kaydı hazırla':'E-posta taslağı hazırla'):title(review!)}</h2><button className="icon-button" aria-label="Kapat" disabled={busy} onClick={close}><X size={20}/></button></div>
    {error&&<p role="alert" className="error">{error}</p>}
    {(creating||editing)?<ActionForm key={review?.payload_hash||creating} kind={creating||review!.kind} initial={review?.payload} busy={busy} onSave={payload=>act(async()=>{const result=creating?await api(path,'POST',{kind:creating,payload}):await api(path+'/'+review!.id,'PUT',{payload,payload_hash:review!.payload_hash});if(alive.current){setCreating('');setEditing(false);setReview(result)}})}/>:review&&<>
      <span className="pill">{labels[review.status]}</span><div className="action-account"><small>İşlemin yapılacağı hesap</small><strong>{review.account_label}</strong></div>
      {review.kind==='google.calendar.create'?<><h3>{review.payload.title}</h3><dl className="action-times"><dt>Başlangıç</dt><dd>{displayDate(review.payload.starts_at)}</dd><dt>Bitiş</dt><dd>{displayDate(review.payload.ends_at)}</dd></dl><p>Birincil takvimine eklenecek. Katılımcı daveti gönderilmez.</p><pre className="action-body">{review.payload.notes}</pre></>:<><p><strong>Alıcı:</strong> {review.payload.to}</p><h3>{review.payload.subject}</h3><pre className="action-body">{review.payload.body}</pre><p>Gmail taslaklarına kaydedilecek. E-posta gönderilmez.</p></>}
      {review.result?.message&&<p className="action-result" role="status">{review.result.message}</p>}
      {review.result?.url&&<a className="text-link" href={review.result.url} target="_blank" rel="noreferrer">Kaynak hesabı aç</a>}
      {review.status==='uncertain'&&review.kind==='google.gmail.draft'&&<p className="muted note">Gmail taslaklarını kontrol et. Aynı taslağı tekrar oluşturmamak için bu işlem yeniden çalıştırılamaz.</p>}
      {canApprove&&editable&&<div className="button-row action-buttons"><button className="button" disabled={busy} onClick={()=>act(()=>decide('approve'))}>{busy?<LoaderCircle size={17} className="spin"/>:<Check size={17}/>}Onayla ve kaydet</button><button className="button secondary" disabled={busy} onClick={()=>setEditing(true)}>Düzenle</button><button className="text-link" disabled={busy} onClick={()=>act(()=>decide('reject'))}>Vazgeç</button></div>}
      {canApprove&&review.status==='uncertain'&&review.kind==='google.calendar.create'&&<button className="button secondary action-buttons" disabled={busy} onClick={()=>act(()=>decide('check'))}>Google Takvim'de kontrol et</button>}
      {!canApprove&&editable&&<p className="muted note">Bu işlemi çalışma alanı yöneticisi onaylayabilir.</p>}
    </>}
  </dialog>}
  </section>
}

function ActionForm({kind,initial,busy,onSave}:{kind:string,initial?:Row,busy:boolean,onSave:(p:Row)=>Promise<void>}){
  // Preserve the reviewed offset while editing. New times use this browser's zone,
  // and the complete timestamp is shown on the next review screen.
  const local=(v?:string)=>{if(!v)return '';const date=new Date(v);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16)};
  return <form onSubmit={e=>{e.preventDefault();const values=Object.fromEntries(new FormData(e.currentTarget));const payload=kind==='google.calendar.create'?{...values,starts_at:new Date(values.starts_at as string).toISOString(),ends_at:new Date(values.ends_at as string).toISOString()}:values;onSave(payload)}}>
  {kind==='google.calendar.create'?<><label>Başlık<input name="title" defaultValue={initial?.title} required maxLength={200}/></label><div className="form-grid"><label>Başlangıç<input name="starts_at" type="datetime-local" defaultValue={local(initial?.starts_at)} required/></label><label>Bitiş<input name="ends_at" type="datetime-local" defaultValue={local(initial?.ends_at)} required/></label></div><p className="muted">Saat dilimi: {Intl.DateTimeFormat().resolvedOptions().timeZone}</p><label>Not<textarea name="notes" defaultValue={initial?.notes} maxLength={5000}/></label></>:<><label>Alıcı e-posta<input name="to" type="email" defaultValue={initial?.to} required maxLength={254}/></label><label>Konu<input name="subject" defaultValue={initial?.subject} required maxLength={200}/></label><label>İçerik<textarea name="body" className="tall" defaultValue={initial?.body} required maxLength={20000}/></label></>}
  <button className="button" disabled={busy}>Önizlemeyi hazırla</button><p className="muted note">Bu adımda hesabına henüz bir kayıt eklenmez.</p></form>
}
