import {useEffect,useRef,useState} from 'react';
import type {FormEvent} from 'react';
import {Archive,ArrowLeft,Brain,Check,ChevronDown,Download,Edit3,LoaderCircle,MessageCircle,Mic,Plus,Send,Square,X} from 'lucide-react';
import {api} from './api';
import {JobAllowance} from './Billing';
import {DocumentPicker,DocumentSources} from './Knowledge';
type Row=Record<string,any>;
const stamp=(n:number)=>new Date(n*1000).toLocaleString('tr-TR',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
const isPending=(row:Row)=>['queued','running'].includes(row.status);

export function Chat({wid,cid,agents,ready,onSelect,onActions,onContent,onAgenda}:{wid:string,cid:string,agents:Row[],ready:boolean,onSelect:(id:string)=>void,onActions:(id?:string)=>void,onContent:()=>void,onAgenda:(target:Row)=>void}){
 const [items,setItems]=useState<Row[]>([]),[archived,setArchived]=useState(false),[more,setMore]=useState<number|null>(null),[error,setError]=useState(''),[showList,setShowList]=useState(false);
 const alive=useRef(true),serial=useRef(0);
 async function load(offset=0){const order=++serial.current;const result=await api(`/workspaces/${wid}/conversations?archived=${archived}&offset=${offset}`);if(!alive.current||order!==serial.current)return;setItems(old=>offset?[...old,...result.items.filter((r:Row)=>!old.some(x=>x.id===r.id))]:result.items);setMore(result.next_offset)}
 useEffect(()=>{alive.current=true;load().catch(e=>setError(e.message));return()=>{alive.current=false;serial.current++}},[wid,archived]);
 useEffect(()=>{load().catch(()=>{});setShowList(false)},[cid]);
 return <><div className="page-title chat-page-title"><div><span className="eyebrow">KALDIĞIN YERDEN DEVAM ET</span><h1>Sohbetler</h1><p>Konuş, birlikte hazırla, sonucunu kullan.</p></div><button className="button secondary" onClick={()=>{onSelect('');setShowList(false)}}><Plus size={17}/>Yeni sohbet</button></div>
 <div className={'chat-shell '+(showList?'show-chat-list':'show-chat-content')}>
  <aside className="chat-list"><div className="chat-list-top"><strong>Konuşmaların</strong><label><input type="checkbox" checked={archived} onChange={e=>setArchived(e.target.checked)}/>Arşiv</label></div>
   {error&&<p className="error" role="alert">{error}</p>}
   {items.map(item=><button key={item.id} className={cid===item.id?'chat-list-item active':'chat-list-item'} onClick={()=>{onSelect(item.id);setShowList(false)}}><MessageCircle size={17}/><span><strong>{item.title}</strong><small>{item.turns} mesaj · {stamp(item.updated)}</small></span></button>)}
   {!items.length&&<p className="chat-list-empty">{archived?'Arşivlenmiş sohbet yok.':'İlk konuşman burada saklanacak.'}</p>}
   {more!==null&&<button className="text-link" onClick={()=>load(more).catch(e=>setError(e.message))}>Daha fazlasını göster</button>}
   <p className="chat-sharing">Sohbetler bu çalışma alanının üyeleriyle paylaşılır.</p>
  </aside>
  <Conversation key={cid||'new'} wid={wid} cid={cid} agents={agents} ready={ready} onSelect={onSelect} onActions={onActions} onContent={onContent} onAgenda={onAgenda} onList={()=>{setShowList(true);load().catch(()=>{})}} onChanged={()=>load().catch(()=>{})}/>
 </div></>
}

function Conversation({wid,cid,agents,ready,onSelect,onActions,onContent,onAgenda,onList,onChanged}:{wid:string,cid:string,agents:Row[],ready:boolean,onSelect:(id:string)=>void,onActions:(id?:string)=>void,onContent:()=>void,onAgenda:(target:Row)=>void,onList:()=>void,onChanged:()=>void}){
 const [detail,setDetail]=useState<Row|null>(null),[text,setText]=useState(''),[agent,setAgent]=useState('guide'),[error,setError]=useState(''),[sending,setSending]=useState(false),[editing,setEditing]=useState(false),[name,setName]=useState(''),[memory,setMemory]=useState<Row|null>(null),[notice,setNotice]=useState(''),[listening,setListening]=useState(false);
 const alive=useRef(true),speech=useRef<any>(null),sequence=useRef(0),applied=useRef(0),attempt=useRef<{signature:string,key:string}|null>(null),bottom=useRef<HTMLDivElement>(null),scroll=useRef<HTMLDivElement>(null),stick=useRef(true);
 const [selectedDocuments,setSelectedDocuments]=useState<string[]>([]),documentsInitialized=useRef(!cid);
 const base='/workspaces/'+wid;
 async function refresh(before?:number){if(!cid)return;const order=++sequence.current;const result=await api(base+'/conversations/'+cid+(before?'?before='+before:''));if(!alive.current)return;
  // Earlier-page requests only add history. Polls cannot overwrite a newer poll.
  if(!before&&order<applied.current)return;if(!before)applied.current=order;
  if(!before&&!documentsInitialized.current){setSelectedDocuments(JSON.parse(result.turns.at(-1)?.document_ids||'[]'));documentsInitialized.current=true}
  setDetail(old=>{const merged=new Map((old?.turns||[]).map((t:Row)=>[t.id,t]));for(const turn of result.turns)if(!before||!merged.has(turn.id))merged.set(turn.id,turn);
   const page=before?result:old||result;
   return {...(!before?result:old||result),turns:[...merged.values()].sort((a:any,b:any)=>a.turn_sequence-b.turn_sequence),has_more:page.has_more,next_before:page.next_before};});
 }
 useEffect(()=>{alive.current=true;refresh().catch(e=>alive.current&&setError(e.message));const timer=cid?setInterval(()=>refresh().catch(()=>{}),2200):null;return()=>{alive.current=false;speech.current?.abort();if(timer)clearInterval(timer)}},[wid,cid]);
 const turns:Row[]=detail?.turns||[],pending=turns.find(isPending),thread=detail?.conversation;
 useEffect(()=>{if(stick.current)bottom.current?.scrollIntoView({block:'nearest',behavior:'auto'})},[turns.length,turns.at(-1)?.status]);
 useEffect(()=>{const node=scroll.current;if(!node)return;const observer=new ResizeObserver(()=>{if(stick.current)node.scrollTop=node.scrollHeight});observer.observe(node);return()=>observer.disconnect()},[]);
 const selectedAgent=agents.find(a=>a.id===(thread?.agent_id||agent));
 async function send(e:FormEvent){e.preventDefault();if(sending||pending||!text.trim())return;setSending(true);setError('');const prompt=text.trim(),aid=thread?.agent_id||agent;
  const signature=JSON.stringify([wid,cid,aid,prompt,selectedDocuments]);if(attempt.current?.signature!==signature)attempt.current={signature,key:crypto.randomUUID()};
  try{const row=await api(base+'/jobs','POST',{prompt,agent_id:aid,idempotency_key:attempt.current.key,conversation_id:cid||null,start_conversation:!cid,document_ids:selectedDocuments});if(!alive.current)return;attempt.current=null;setText('');stick.current=true;
   if(!cid)onSelect(row.conversation_id);else await refresh();onChanged();
  }catch(e){if(alive.current)setError((e as Error).message)}finally{if(alive.current)setSending(false)}
 }
 async function change(body:Row){setError('');try{await api(base+'/conversations/'+cid,'PATCH',body);await refresh();onChanged();setEditing(false)}catch(e){if(alive.current)setError((e as Error).message)}}
 async function stop(){if(!pending)return;try{await api(base+'/jobs/'+pending.id+'/cancel','POST');await refresh()}catch(e){if(alive.current)setError((e as Error).message)}}
 function dictate(){if(listening){speech.current?.stop();return}const C=(window as any).SpeechRecognition||(window as any).webkitSpeechRecognition;if(!C){setNotice('Bu tarayıcı sesle yazmayı desteklemiyor. Yazıyla devam edebilirsin.');return}
  const recognition=new C();speech.current=recognition;recognition.lang='tr-TR';recognition.interimResults=false;recognition.onresult=(e:any)=>{if(alive.current)setText(t=>t+(t?' ':'')+e.results[0][0].transcript)};recognition.onend=()=>alive.current&&setListening(false);recognition.onerror=()=>{if(alive.current){setListening(false);setNotice('Ses alınamadı. Mikrofon iznini kontrol et.')}};setListening(true);recognition.start();
 }
 return <section className="chat-conversation"><header className="chat-top"><button className="icon-button chat-back" aria-label="Sohbet listesini aç" onClick={onList}><ArrowLeft size={20}/></button><div><strong>{thread?.title||'Yeni bir konuşma'}</strong><small>{selectedAgent?.name||'Piko'} · {selectedAgent?.title||'İş arkadaşın'}</small></div>{thread&&<div className="chat-controls"><button className="icon-button" aria-label="Sohbet başlığını düzenle" onClick={()=>{setName(thread.title);setEditing(!editing)}}><Edit3 size={17}/></button><a className="icon-button" aria-label="Sohbeti indir" href={'/api'+base+'/conversations/'+cid+'/export'}><Download size={17}/></a><button className="icon-button" aria-label={thread.archived?'Arşivden çıkar':'Sohbeti arşivle'} disabled={!!pending} onClick={()=>change({archived:!thread.archived})}><Archive size={17}/></button></div>}</header>
  {editing&&<form className="chat-rename" onSubmit={e=>{e.preventDefault();change({title:name})}}><input aria-label="Sohbet başlığı" value={name} onChange={e=>setName(e.target.value)} required maxLength={100}/><button className="button small">Kaydet</button></form>}
  {error&&<div className="error" role="alert">{error}</div>}
  <div className="chat-scroll" ref={scroll} onScroll={()=>{const node=scroll.current;if(node)stick.current=node.scrollHeight-node.scrollTop-node.clientHeight<100}}>
   {detail?.has_more&&<button className="button secondary small load-history" onClick={()=>{stick.current=false;refresh(detail.next_before).catch(e=>setError(e.message))}}><ChevronDown size={16}/>Önceki mesajları göster</button>}
   {!cid&&<div className="chat-welcome"><img src="/mascot.png" alt="Piko"/><h2>İşini birlikte halledelim.</h2><p>Ne yapmak istediğini anlat. Sonra aynı konuşmada düzenleyerek ilerleyelim.</p><div>{['Bir haftalık içerik planı hazırlayalım.','Bir konuyu kaynaklarıyla araştıralım.','Bugünkü işlerimi sıraya koyalım.'].map(p=><button key={p} onClick={()=>setText(p)}>{p}</button>)}</div></div>}
   {cid&&!detail&&!error&&<div className="chat-loading"><LoaderCircle className="spin" size={22}/>Sohbet açılıyor…</div>}
   {turns.map(turn=><div className="chat-turn" key={turn.id}><article className="chat-message from-user"><div className="chat-message-label">Sen <time>{stamp(turn.created)}</time></div><p>{turn.prompt}</p></article>
    <article className={'chat-message from-assistant '+(isPending(turn)?'working':'')}><div className="chat-message-label"><img className={turn.status==='running'?'piko-working':''} src="/mascot.png" alt=""/>{selectedAgent?.name||'İşdaş'}{turn.status==='completed'&&<Check size={15}/>}</div>
     {turn.status==='completed'?<><div className="chat-answer">{turn.output}</div>{JSON.parse(turn.context_info||'{}').history_trimmed&&<p className="chat-context-note">Bu yanıt hazırlanırken eski konuşmaların bir bölümü bağlama alınmadı. Gerekli ayrıntıyı yeniden belirtebilirsin.</p>}<DocumentSources wid={wid} jid={turn.id} count={turn.document_source_count||0}/>{JSON.parse(turn.context_info||'{}').document_citation_warning&&<p className="chat-context-note">Bir belge atfı doğrulanamadı. Yanıtı belge bölümleriyle karşılaştır.</p>}<div className="chat-message-tools"><button onClick={()=>setMemory({title:turn.prompt.slice(0,100),content:turn.output})}><Brain size={15}/>Hafızaya ekle</button>{detail?.agenda_plans?.filter((p:Row)=>p.job_id===turn.id).map((p:Row)=><button key={p.id} onClick={()=>onAgenda({plan:p.id})}>{p.applied_at?'Ajandadaki planı aç':'Ajanda planını incele'}</button>)}{!detail?.agenda_plans?.some((p:Row)=>p.job_id===turn.id)&&<button onClick={()=>onAgenda({draft:{title:turn.prompt.slice(0,200),notes:turn.output.slice(0,5000)}})}>Yapılacak iş oluştur</button>}{detail?.content_posts?.some((p:Row)=>p.job_id===turn.id)&&<button onClick={onContent}>İçerikleri aç</button>}{detail?.actions.filter((a:Row)=>a.job_id===turn.id).map((a:Row)=><button key={a.id} onClick={()=>onActions(a.id)}><Check size={15}/>{a.status==='pending'?'İşlemi incele ve onayla':'İşlem ayrıntıları'}</button>)}</div></>:
      isPending(turn)?<div className="chat-progress" role="status"><span className="working-dots"><i/><i/><i/></span><div><strong>{turn.cancel_requested?'İptal isteğin işleniyor.':turn.stage}</strong><p>{turn.status==='queued'?'Sıran geldiğinde başlayacağım.':'Sonuç hazır olduğunda burada göreceksin.'}</p></div></div>:
      <><p className="chat-failure">{turn.error||turn.stage}</p><button className="text-link" disabled={!!pending} onClick={()=>{setText(turn.prompt);setNotice('İsteği kontrol edip yeniden gönderebilirsin.')}}>İsteği yeniden hazırla</button></>}
    </article></div>)}<div ref={bottom}/>
  </div>
  {notice&&<p className="chat-notice" role="status">{notice}</p>}
  {thread?.archived?<div className="chat-archived"><Archive size={19}/><span>Bu sohbet arşivde.</span><button className="text-link" onClick={()=>change({archived:false})}>Devam etmek için aç</button></div>:<form className="chat-composer" onSubmit={send}><DocumentPicker wid={wid} value={selectedDocuments} onChange={setSelectedDocuments}/><textarea aria-label="Sohbet mesajı" value={text} onChange={e=>setText(e.target.value)} placeholder={pending?'Yanıt hazırlanıyor. Sonraki mesajını yazabilirsin.':'Ne yapmak istediğini anlat…'} maxLength={12000} required/><div className="chat-composer-bottom">{!cid?<select aria-label="Sohbet işdaşı" value={agent} onChange={e=>setAgent(e.target.value)}>{agents.map(a=><option key={a.id} value={a.id}>{a.name} · {a.title}</option>)}</select>:<small>Bu çalışma alanında saklanır.</small>}<div><button type="button" className={listening?'voice-button listening':'voice-button'} aria-label="Sohbette sesle yaz" onClick={dictate}><Mic size={18}/>{listening?'Dinliyorum':'Konuş'}</button>{pending?<button type="button" className="button secondary" disabled={!!pending.cancel_requested} onClick={stop}><Square size={16}/>Durdur</button>:<button className="button" disabled={sending||!text.trim()||!ready}>{sending?<LoaderCircle className="spin" size={17}/>:<Send size={17}/>}Gönder</button>}</div></div>{ready&&<JobAllowance wid={wid} agent={thread?.agent_id||agent} revision={pending?.id||''}/>}{!ready&&<p className="chat-unavailable">Asistan bağlantısı hazırlanıyor. Geçmiş sohbetlerini okuyabilir ve hafızanı düzenleyebilirsin.</p>}</form>}
  {memory&&<Remember wid={wid} initial={memory} onClose={()=>setMemory(null)} onSaved={()=>{setMemory(null);setNotice('Bilgi bu çalışma alanının hafızasına eklendi.')}}/>}
 </section>
}

function Remember({wid,initial,onClose,onSaved}:{wid:string,initial:Row,onClose:()=>void,onSaved:()=>void}){
 const ref=useRef<HTMLDialogElement>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[content,setContent]=useState(initial.content);
 useEffect(()=>{ref.current?.showModal();return()=>ref.current?.close()},[]);
 return <dialog ref={ref} onCancel={e=>{e.preventDefault();if(!busy)onClose()}}><div className="modal-head"><h2>İşdaşların bunu hatırlasın.</h2><button className="icon-button" aria-label="Kapat" disabled={busy} onClick={onClose}><X size={20}/></button></div><p>Bilgiyi kontrol et. Kaydettiğinde bu çalışma alanındaki sonraki işlerde kullanılabilir.</p><form onSubmit={async e=>{e.preventDefault();setBusy(true);setError('');const title=new FormData(e.currentTarget).get('title');try{await api('/workspaces/'+wid+'/memories','POST',{title,content});onSaved()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}}><label>Başlık<input name="title" defaultValue={initial.title} required maxLength={100}/></label><label htmlFor="remember-content">Hatırlanacak bilgi</label><textarea id="remember-content" className="tall" value={content} onChange={e=>setContent(e.target.value)} required maxLength={15000}/>{content.length>15000&&<p className="error">Metni 15.000 karakterin altına kısaltarak hangi bilgilerin hatırlanacağını seç.</p>}{error&&<p className="error" role="alert">{error}</p>}<button className="button" disabled={busy||content.length>15000}>Hafızaya kaydet</button></form></dialog>
}
