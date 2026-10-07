import {useEffect,useState} from 'react';
import {Check,Clock3,Info,LoaderCircle,WalletCards} from 'lucide-react';
import {api} from './api';
import './Billing.css';
type Row=Record<string,any>;
const points=(milli:number)=>new Intl.NumberFormat('tr-TR',{maximumFractionDigits:3}).format(milli/1000);
const date=(time:number)=>new Date(time*1000).toLocaleDateString('tr-TR',{day:'numeric',month:'long',year:'numeric'});
const states:Record<string,string>={reserved:'İş için ayrıldı',settled:'Kullanım işlendi',uncertain:'Doğrulanıyor',released:'Geri bırakıldı'};

export function Billing({wid}:{wid:string}){
 const [data,setData]=useState<Row|null>(null),[error,setError]=useState('');
 useEffect(()=>{let active=true,busy=false;
  async function refresh(){if(busy)return;busy=true;try{const r=await api(`/workspaces/${wid}/billing`);if(active){setData(r);setError('')}}catch(e){if(active)setError((e as Error).message)}finally{busy=false}}
  setData(null);refresh();const timer=setInterval(refresh,5000);return()=>{active=false;clearInterval(timer)};
 },[wid]);
 if(!data)return <section className="panel">{error?<p role="alert" className="error">{error}</p>:<p className="muted"><LoaderCircle className="spin" size={18}/> Kullanım bilgilerin yükleniyor.</p>}</section>;
 const p=data.period,used=data.spent_milli+data.held_milli,percent=data.limit_milli?Math.min(100,used/data.limit_milli*100):0;
 return <div className="billing-page"><div className="page-title"><div><span className="eyebrow">NE KADAR KALDIĞINI BİL</span><h1>Paket ve kullanım</h1><p>İşdaşlarının kullanımını ve kalan hakkını buradan takip et.</p></div></div>
 {error&&<p role="alert" className="error">{error}</p>}
 <section className="panel allowance-panel"><div className="allowance-heading"><span className="role-icon blue"><WalletCards/></span><div><h2>{p?p.label+' kullanımı':'Henüz aktif bir paketin yok.'}</h2><p>{data.active?`${date(p.starts_at)} – ${date(p.ends_at)}`:p?.revoked?'Bu kullanım dönemi kapatıldı.':'İşlerini başlatmak için kullanım hakkının açılması gerekiyor.'}</p></div><span className="pill">{data.active?(p.kind==='pilot'?'Deneme açık':'Aktif'):'Kullanım kapalı'}</span></div>
 {!data.active&&<div className="setup-note"><Info size={20}/><div><strong>Kayıtların burada kalır.</strong><p>Hafızanı, ajandanı ve hazırlanan sonuçlarını kullanmaya devam edebilirsin. Deneme erişimi ekip tarafından açılır. Satın alma henüz etkin değil.</p></div></div>}
 <div className="allowance-numbers"><div><strong>{points(data.remaining_milli)}</strong><span>Kalan puan</span></div><div><strong>{points(data.spent_milli)}</strong><span>Kullanılan</span></div><div><strong>{points(data.held_milli)}</strong><span>İşler için ayrılan</span></div></div>
 <div className="allowance-meter" role="progressbar" aria-label="Ayrılan ve kullanılan puan" aria-valuenow={used/1000} aria-valuemin={0} aria-valuemax={data.limit_milli/1000}><span style={{width:percent+'%'}}/></div>
 <p className="allowance-caption">{p?`Bu dönemin toplam hakkı: ${points(data.limit_milli)} puan.`:'Henüz puan tanımlanmadı.'} {data.active&&p.kind==='pilot'?'Deneme otomatik yenilenmez.':''}</p>
 {data.unresolved_count>0&&<div className="setup-note"><Clock3 size={20}/><div><strong>{data.unresolved_count} işin kullanımı doğrulanıyor.</strong><p>Bu işler için ayrılan puan tutuluyor. Tutar netleşene kadar bu hesabın yeni işleri bekler.</p></div></div>}
 </section>
 <div className="billing-info-grid"><section className="panel"><h3>Bir hesap, ortak kullanım</h3><p>Bu hesabın açtığı tüm çalışma alanları ve işdaşlar aynı puanı paylaşır. Yeni alan veya işdaş açmak puanı artırmaz.</p>{data.can_manage&&p&&<p className="allowance-space-count"><Check size={17}/>{data.workspace_count} / {p.workspace_limit} çalışma alanı</p>}{!data.can_manage&&<p>Paketi çalışma alanının yöneticisi yönetir. Aşağıda yalnızca bu alanın işleri görünür.</p>}</section><section className="panel"><h3>İş başlamadan ayırırız.</h3><p>Her iş için gösterilen üst sınır kadar puan ayrılır; iş bitince kullanılmayan bölüm geri bırakılır. Kullanım, işin uzunluğuna ve kullandığı araçlara göre değişir.</p><p>Başlamış bir işi iptal edersen o ana kadarki kullanım puandan düşebilir.</p></section></div>
 <section className="panel billing-history"><div className="section-heading"><div><h2>Son kullanım</h2><p>{data.can_manage?'Hesabının çalışma alanlarındaki son 50 iş.':'Bu çalışma alanındaki son 50 iş.'}</p></div></div>
 {data.receipts.length?<div className="billing-receipts">{data.receipts.map((r:Row)=><article className="billing-receipt" key={r.job_id}><div><strong>{r.prompt}</strong><small>{r.workspace_name} · {date(r.created)}</small></div><span className={'pill '+(r.state==='uncertain'?'billing-warning':'')}>{states[r.state]}</span><b>{points(r.charged_milli??r.reserved_milli)} <small>puan</small></b></article>)}</div>:<div className="empty compact"><WalletCards/><p>Bir iş başladığında kullanımı burada görünür.</p></div>}
 </section></div>;
}

export function JobAllowance({wid,agent,revision='',onQuote}:{wid:string,agent:string,revision?:string,onQuote?:(quote:Row|null)=>void}){
 const [quote,setQuote]=useState<Row|null>(null);
 useEffect(()=>{let active=true;setQuote(null);onQuote?.(null);const load=()=>api(`/workspaces/${wid}/billing/quote?agent_id=${encodeURIComponent(agent)}`).then(r=>{if(active){setQuote(r);onQuote?.(r)}}).catch(()=>{if(active){setQuote(null);onQuote?.(null)}});load();const timer=setInterval(load,5000);return()=>{active=false;clearInterval(timer)}},[wid,agent,revision,onQuote]);
 if(!quote)return null;
 return <p className={'job-allowance '+(quote.allowed?'':'allowance-unavailable')}><Info size={14}/><span>{quote.allowed?`Bu iş için en fazla ${points(quote.max_milli)} puan ayrılır. Kullanılmayan bölüm geri bırakılır.`:quote.reason}</span></p>;
}
