"""Workspace conversations and bounded provider context. No cross-thread recall."""
import json
from fastapi import Depends,HTTPException,Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel,Field
from . import db

def get(conn,wid,cid):
    row=conn.execute('SELECT * FROM conversations WHERE id=? AND workspace_id=?',(cid,wid)).fetchone()
    if not row:raise HTTPException(404,'Sohbet bulunamadı.')
    return dict(row)

def allocate(conn,wid,uid,body):
    if body.start_conversation and body.conversation_id:raise HTTPException(400,'Yeni sohbet ve mevcut sohbet aynı anda seçilemez.')
    cid=body.conversation_id
    if body.start_conversation:
        cid=db.uid();stamp=db.now()
        conn.execute('INSERT INTO conversations(id,workspace_id,user_id,title,agent_id,created,updated) VALUES(?,?,?,?,?,?,?)',(cid,wid,uid,body.prompt.strip()[:100],body.agent_id,stamp,stamp))
    if not cid:return None,None
    thread=get(conn,wid,cid)
    if thread['archived']:raise HTTPException(409,'Sohbet arşivde. Devam etmek için arşivden çıkar.')
    if thread['agent_id']!=body.agent_id:raise HTTPException(409,'Bu sohbetin işdaşı farklı. Yeni bir sohbet açabilirsin.')
    if conn.execute("SELECT 1 FROM jobs WHERE conversation_id=? AND workspace_id=? AND status IN ('queued','running')",(cid,wid)).fetchone():
        raise HTTPException(409,'İşdaşının yanıtını tamamlamasını bekle veya devam eden işi durdur.')
    sequence=conn.execute('SELECT COALESCE(MAX(turn_sequence),0)+1 FROM jobs WHERE conversation_id=? AND workspace_id=?',(cid,wid)).fetchone()[0]
    conn.execute('UPDATE conversations SET updated=? WHERE id=?',(db.now(),cid))
    return cid,sequence

def same_request(existing,body):
    return (existing['prompt']==body.prompt and existing['agent_id']==body.agent_id
            and bool(existing['conversation_start'])==body.start_conversation
            and json.loads(existing.get('document_ids','[]'))==getattr(body,'document_ids',[])
            and (body.start_conversation or existing['conversation_id']==body.conversation_id))

def excerpt(text,limit):
    if len(text)<=limit:return text,False
    marker='\n[Uzun metnin bir bölümü bu bağlama alınmadı.]\n'
    room=max(0,limit-len(marker));head=room*2//3
    return text[:head]+marker+text[-(room-head):] if room else marker[:limit],True

def context_for(job,limit=32000,max_turns=12):
    """Only older turns in this workspace/thread. Failed output is never a result."""
    cid=job.get('conversation_id')
    if not cid:return {'messages':[],'resources':{},'info':{'history_turns_used':0,'history_turns_total':0,'history_trimmed':False}}
    with db.connection() as conn:
        get(conn,job['workspace_id'],cid)
        args=(job['workspace_id'],cid,job['turn_sequence'])
        total=conn.execute('SELECT COUNT(*) FROM jobs WHERE workspace_id=? AND conversation_id=? AND turn_sequence<?',args).fetchone()[0]
        rows=conn.execute('SELECT * FROM jobs WHERE workspace_id=? AND conversation_id=? AND turn_sequence<? ORDER BY turn_sequence DESC LIMIT ?',(*args,max_turns)).fetchall()
        pairs=[];remaining=limit;trimmed=False
        for raw in rows:
            row=dict(raw)
            answer=row['output'] if row['status']=='completed' else 'Bu isteğin sonucu oluşturulmadı. İş durumu: '+row['status']+'. Tamamlanmış bir işlem olarak değerlendirme.'
            length=len(row['prompt'])+len(answer)
            if length>remaining:
                if pairs:break
                prompt,short_a=excerpt(row['prompt'],min(len(row['prompt']),remaining//3))
                answer,short_b=excerpt(answer,remaining-len(prompt));trimmed=short_a or short_b
            else:prompt=row['prompt']
            pairs.append([{'role':'user','content':prompt},{'role':'assistant','content':answer}])
            remaining-=len(prompt)+len(answer)
            if remaining<100:break
        # Provider-visible application state is data, never an instruction. It
        # reflects user edits and approvals performed after the original answer.
        linked=conn.execute('SELECT a.id,a.kind,a.payload,a.status,a.account_label FROM actions a JOIN jobs j ON j.id=a.job_id WHERE a.workspace_id=? AND j.workspace_id=? AND j.conversation_id=? AND j.turn_sequence<? ORDER BY a.updated DESC LIMIT 4',
                            (job['workspace_id'],job['workspace_id'],cid,job['turn_sequence'])).fetchall()
        linked_actions=[]
        for a in linked:
            payload=json.loads(a['payload']);cut=False
            for k,v in payload.items():
                if isinstance(v,str):payload[k],short=excerpt(v,2500);cut=cut or short
            linked_actions.append({'id':a['id'],'kind':a['kind'],'status':a['status'],'account':a['account_label'],'payload':payload,'excerpt_only':cut})
        artifact=conn.execute('SELECT a.id,a.title,a.body FROM artifacts a JOIN jobs j ON j.id=a.job_id WHERE a.workspace_id=? AND j.workspace_id=? AND j.conversation_id=? AND j.turn_sequence<? ORDER BY j.turn_sequence DESC,a.updated DESC LIMIT 1',
                              (job['workspace_id'],job['workspace_id'],cid,job['turn_sequence'])).fetchone()
        current_artifact=None
        if artifact:
            text,cut=excerpt(artifact['body'],10000)
            current_artifact={'id':artifact['id'],'title':artifact['title'],'body':text,'excerpt_only':cut}
        linked_posts=[]
        for row in conn.execute('SELECT p.* FROM content_posts p JOIN jobs j ON j.id=p.job_id WHERE p.workspace_id=? AND j.workspace_id=? AND j.conversation_id=? AND j.turn_sequence<? ORDER BY p.updated DESC LIMIT 14',(job['workspace_id'],job['workspace_id'],cid,job['turn_sequence'])):
            values=json.loads(row['payload']);caption,cut=excerpt(values['caption'],1800)
            linked_posts.append({'id':row['id'],'revision':row['revision'],'status':row['status'],'title':values['title'],'platform':values['platform'],'caption':caption,'excerpt_only':cut,'planned_local':values['planned_local'],'timezone':values['timezone'],'published':False})
        linked_agenda=[]
        for row in conn.execute('SELECT p.* FROM agenda_plans p JOIN jobs j ON j.id=p.job_id WHERE p.workspace_id=? AND j.workspace_id=? AND j.conversation_id=? AND j.turn_sequence<? ORDER BY p.created DESC LIMIT 3',(job['workspace_id'],job['workspace_id'],cid,job['turn_sequence'])):
            tasks=[dict(t) for t in conn.execute('SELECT t.id,t.title,t.status,t.due_local,t.timezone,t.revision,u.name AS assignee_name FROM agenda_tasks t JOIN users u ON u.id=t.assignee_id WHERE t.plan_id=? AND t.workspace_id=? ORDER BY t.created,t.id LIMIT 20',(row['id'],job['workspace_id']))]
            linked_agenda.append({'id':row['id'],'title':row['title'],'applied':row['applied_at'] is not None,'tasks':tasks,'selected_excerpt_only':True})
    messages=[message for pair in reversed(pairs) for message in pair]
    return {'messages':messages,'resources':{'actions':linked_actions,'latest_artifact':current_artifact,'content_posts':linked_posts,'agenda_plans':linked_agenda},'info':{'history_turns_used':len(pairs),'history_turns_total':total,'history_trimmed':trimmed or len(pairs)<total}}

class EditIn(BaseModel):
    title:str|None=Field(default=None,min_length=1,max_length=100)
    archived:bool|None=None

def register_routes(app,user,workspace):
    @app.get('/api/workspaces/{wid}/conversations')
    def listing(wid,archived:bool=False,offset:int=Query(default=0,ge=0),u=Depends(user)):
        workspace(wid,u)
        rows=db.query('SELECT c.*,(SELECT COUNT(*) FROM jobs j WHERE j.conversation_id=c.id AND j.workspace_id=c.workspace_id) AS turns FROM conversations c WHERE c.workspace_id=? AND c.archived=? ORDER BY c.updated DESC,c.id LIMIT 51 OFFSET ?',(wid,int(archived),offset))
        return {'items':rows[:50],'has_more':len(rows)>50,'next_offset':offset+50 if len(rows)>50 else None,'visibility':'workspace'}

    @app.get('/api/workspaces/{wid}/conversations/{cid}')
    def detail(wid,cid,before:int|None=Query(default=None,ge=1),u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:
            thread=get(conn,wid,cid)
            rows=[dict(r) for r in conn.execute('SELECT j.*,(SELECT count(*) FROM job_document_sources s WHERE s.job_id=j.id) AS document_source_count FROM jobs j WHERE workspace_id=? AND conversation_id=? AND turn_sequence<? ORDER BY turn_sequence DESC LIMIT 31',(wid,cid,before or 2147483647)).fetchall()]
            turns=list(reversed(rows[:30]));has_more=len(rows)>30
            linked=[dict(r) for r in conn.execute('SELECT a.id,a.job_id,a.kind,a.status FROM actions a JOIN jobs j ON j.id=a.job_id WHERE a.workspace_id=? AND j.workspace_id=? AND j.conversation_id=?',(wid,wid,cid)).fetchall()]
            posts=[dict(r) for r in conn.execute('SELECT p.id,p.job_id,p.status FROM content_posts p JOIN jobs j ON j.id=p.job_id WHERE p.workspace_id=? AND j.workspace_id=? AND j.conversation_id=?',(wid,wid,cid))]
            plans=[dict(r) for r in conn.execute('SELECT p.id,p.job_id,p.applied_at FROM agenda_plans p JOIN jobs j ON j.id=p.job_id WHERE p.workspace_id=? AND j.workspace_id=? AND j.conversation_id=?',(wid,wid,cid))]
        return {'conversation':thread,'turns':turns,'actions':linked,'content_posts':posts,'agenda_plans':plans,'has_more':has_more,'next_before':turns[0]['turn_sequence'] if has_more else None,'visibility':'workspace'}

    @app.patch('/api/workspaces/{wid}/conversations/{cid}')
    def edit(wid,cid,body:EditIn,u=Depends(user)):
        member=workspace(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');thread=get(conn,wid,cid)
            if member['member_role']!='owner' and thread['user_id']!=u['id']:raise HTTPException(403,'Sohbeti oluşturan kişi veya alan yöneticisi düzenleyebilir.')
            if body.archived and conn.execute("SELECT 1 FROM jobs WHERE conversation_id=? AND status IN ('queued','running')",(cid,)).fetchone():raise HTTPException(409,'Devam eden işi durdurduktan sonra sohbeti arşivleyebilirsin.')
            title=thread['title'] if body.title is None else body.title.strip()
            if not title:raise HTTPException(400,'Sohbet başlığı boş olamaz.')
            conn.execute('UPDATE conversations SET title=?,archived=?,updated=? WHERE id=?',(title,thread['archived'] if body.archived is None else int(body.archived),db.now(),cid))
        return {'ok':True}

    @app.get('/api/workspaces/{wid}/conversations/{cid}/export')
    def export(wid,cid,u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:
            thread=get(conn,wid,cid)
            turns=conn.execute('SELECT * FROM jobs WHERE workspace_id=? AND conversation_id=? ORDER BY turn_sequence',(wid,cid)).fetchall()
        lines=[thread['title'],'Bu çalışma alanında paylaşılan sohbet.','']
        for row in turns:
            lines+=['SEN',row['prompt'],'','İŞDAŞ',row['output'] if row['status']=='completed' else '['+row['stage']+'] '+row['error'],'']
        return PlainTextResponse('\n'.join(lines),headers={'Content-Disposition':f'attachment; filename="isdas-sohbet-{cid[:8]}.txt"'})
