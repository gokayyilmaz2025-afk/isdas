import threading,time,json
from . import db,providers,usage,actions,conversations,billing,content,runtime,knowledge,agenda
from .catalog import ROLES
stop=threading.Event()
def set_stage(job_id,stage):db.query('UPDATE jobs SET stage=?,updated=? WHERE id=?',(stage,db.now(),job_id))
def claim():
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if runtime.recovery_hold(conn):return None
        for orphan in conn.execute("SELECT id FROM jobs j WHERE status='queued' AND NOT EXISTS(SELECT 1 FROM memberships m WHERE m.workspace_id=j.workspace_id AND m.user_id=j.user_id AND m.role IN ('owner','member'))").fetchall():
            usage.settle(conn,orphan['id'],no_request=True)
            conn.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,stage='Alan erişimi kapatıldı',updated=? WHERE id=?",(db.now(),orphan['id']))
        row=conn.execute("SELECT j.* FROM jobs j WHERE j.status='queued' AND j.cancel_requested=0 AND (j.conversation_id IS NULL OR NOT EXISTS (SELECT 1 FROM jobs prior WHERE prior.conversation_id=j.conversation_id AND prior.workspace_id=j.workspace_id AND prior.turn_sequence<j.turn_sequence AND prior.status IN ('queued','running'))) ORDER BY j.created,j.id LIMIT 1").fetchone()
        if not row:return None
        conn.execute("UPDATE jobs SET status='running',stage='İşletme bilgilerini okuyorum',updated=?,attempt=attempt+1 WHERE id=?",(db.now(),row['id']))
        return dict(row)
def process(job):
    reserved=False;called=False;result=None
    try:
        workspace=db.query('SELECT * FROM workspaces WHERE id=?',(job['workspace_id'],),one=True)
        workspace['_document_ids']=json.loads(job.get('document_ids','[]'))
        workspace['_requester_id']=job['user_id']
        role=next((r for r in ROLES if r['id']==job['agent_id']),None) or db.query('SELECT * FROM agents WHERE id=? AND workspace_id=?',(job['agent_id'],workspace['id']),one=True)
        if not role:raise RuntimeError('Asistan bulunamadı.')
        memories=db.query('SELECT title,content FROM memories WHERE workspace_id=? ORDER BY updated DESC LIMIT 30',(workspace['id'],))
        is_image=job.get('task_kind')=='image'
        runtime=providers.image_available(workspace['id']) if is_image else providers.available(workspace['id'])
        if not runtime['ready']:raise providers.ProviderUnavailable(runtime['reason'])
        reserved=usage.reserve(job,runtime['provider'])
        if not reserved:
            with db.connection() as conn:
                conn.execute('BEGIN IMMEDIATE')
                usage.settle(conn,job['id'],no_request=True)
                conn.execute("UPDATE jobs SET status='cancelled',stage='İptal edildi',updated=? WHERE id=?",(db.now(),job['id']))
            return
        set_stage(job['id'],'Araştırma ve sonuç hazırlığı sürüyor' if role['id']=='research' else 'Sonucunu hazırlıyorum')
        if is_image:
            request=db.query('SELECT * FROM image_requests WHERE job_id=? AND workspace_id=?',(job['id'],workspace['id']),one=True)
            if not request:raise RuntimeError('Görsel isteği bulunamadı.')
            set_stage(job['id'],'Görselini hazırlıyorum');called=True;result=providers.generate_image(request)
        elif job.get('conversation_id'):
            context=conversations.context_for(job)
            db.query('UPDATE jobs SET context_info=? WHERE id=?',(json.dumps(context['info']),job['id']))
            called=True
            result=providers.run(workspace,role,job['prompt'],memories,conversation=context)
        else:
            called=True
            result=providers.run(workspace,role,job['prompt'],memories)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            usage.settle(conn,job['id'],result['usage'],result['provider'],result.get('request_id',''))
            current=conn.execute('SELECT cancel_requested FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if current['cancel_requested']:
                conn.execute("UPDATE jobs SET status='cancelled',stage='İptal edildi',usage=?,updated=? WHERE id=?",(json.dumps(result['usage']),db.now(),job['id']));return
            knowledge.persist_sources(conn,job,result.get('document_sources',[]))
            context_info=json.loads(conn.execute('SELECT context_info FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
            context_info.update(document_excerpts=len(result.get('document_sources',[])),document_citation_warning=bool(result.get('citation_warning')))
            conn.execute('UPDATE jobs SET context_info=? WHERE id=?',(json.dumps(context_info),job['id']))
            for proposal in result.get('proposals',[]):
                actions.prepare(conn,workspace['id'],proposal['kind'],proposal['payload'],job['id'],result.get('connection_id'))
            if result.get('content_plan'):content.save_plan(conn,job,result['content_plan'])
            if result.get('agenda_plan'):agenda.save_plan(conn,job,result['agenda_plan'])
            if is_image:result['text']=content.attach_generated(conn,job,request,result['image_bytes'])
            conn.execute("UPDATE jobs SET status='completed',stage='Sonucun hazır',output=?,provider=?,usage=?,updated=? WHERE id=?",(result['text'],result['provider'],json.dumps(result['usage']),db.now(),job['id']))
            conn.execute('INSERT INTO artifacts(id,workspace_id,job_id,title,kind,body,created,updated) VALUES(?,?,?,?,?,?,?,?)',(db.uid(),workspace['id'],job['id'],job['prompt'][:100],role['id'],result['text'],db.now(),db.now()))
            conn.execute('INSERT INTO audit VALUES(?,?,?,?,?,?)',(db.uid(),workspace['id'],job['user_id'],'job.completed',job['id'],db.now()))
    except (usage.BudgetUnavailable,billing.EntitlementUnavailable) as e:
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM usage_ledger WHERE job_id=?',(job['id'],)).fetchone():billing.settle(conn,job['id'],no_request=True)
            conn.execute("UPDATE jobs SET status='blocked',stage='Kullanım hakkı bekleniyor',error=?,updated=? WHERE id=?",(str(e),db.now(),job['id']))
    except providers.ProviderUnavailable as e:
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            usage.settle(conn,job['id'],no_request=True)
            conn.execute("UPDATE jobs SET status='blocked',stage='Bağlantı bekleniyor',error=?,updated=? WHERE id=?",(str(e),db.now(),job['id']))
    except Exception as e:
        # Do not store request bodies, auth headers, or provider error pages in customer-visible errors.
        receipt=result or {'usage':getattr(e,'usage',{}),'provider':getattr(e,'provider',''),'request_id':getattr(e,'request_id','')}
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            usage.settle(conn,job['id'],receipt['usage'],receipt['provider'],receipt.get('request_id',''),no_request=not called or isinstance(e,knowledge.SelectionUnavailable))
            error=e.public_message if isinstance(e,(knowledge.SelectionUnavailable,knowledge.SourceChanged)) else 'İş tamamlanamadı. Bağlantıyı kontrol edip yeniden deneyebilirsin.'
            conn.execute("UPDATE jobs SET status='failed',stage='İş tamamlanamadı',error=?,usage=?,provider=?,updated=? WHERE id=?",(error,json.dumps(receipt['usage']),receipt['provider'],db.now(),job['id']))
        db.audit(job['workspace_id'],job['user_id'],'job.failed',type(e).__name__)
def tick_routines():
    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if runtime.recovery_hold(conn):return
        for row in conn.execute('SELECT * FROM routines WHERE enabled=1 AND next_run<=?',(db.now(),)).fetchall():
            if not conn.execute("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=? AND role IN ('owner','member')",(row['workspace_id'],row['user_id'])).fetchone():
                conn.execute('UPDATE routines SET enabled=0 WHERE id=?',(row['id'],));continue
            key=f"routine:{row['id']}:{row['next_run']}"
            conn.execute('INSERT OR IGNORE INTO jobs(id,workspace_id,user_id,agent_id,prompt,status,stage,created,updated,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),row['workspace_id'],row['user_id'],row['agent_id'],row['prompt'],'queued','Sırada',db.now(),db.now(),key))
            conn.execute('UPDATE routines SET next_run=? WHERE id=?',(db.now()+row['interval_hours']*3600,row['id']))
def loop():
    while not stop.is_set():
        try:
            tick_routines()
            if stop.is_set():break
            job=claim()
            if job:process(job)
            else:stop.wait(1)
        except Exception:stop.wait(2)
def start():
    # Only one API process is supported. Interrupted operations are visible, never silently replayed.
    usage.recover()
    actions.recover()
    db.query("UPDATE jobs SET status='interrupted',stage='Yeniden başlatılmalı',error='Sunucu yeniden başladı. Görevi kontrol edip yeniden başlatabilirsin.',updated=? WHERE status='running'",(db.now(),))
    stop.clear();thread=threading.Thread(target=loop,daemon=True,name='isdas-worker');thread.start();return thread
