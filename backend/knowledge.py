"""Workspace-scoped document storage, lexical retrieval and inspectable excerpts."""
import asyncio,hashlib,json,os,re,site,subprocess,sys,tempfile,threading,unicodedata
from pathlib import Path
from fastapi import Depends,File,HTTPException,Query,UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from . import db
from .document_parser import MAX_UPLOAD

MAX_STORAGE=50*1024*1024
MAX_DOCUMENTS=100
PARSER_TIMEOUT=22
_parsers=threading.BoundedSemaphore(2)
STOP=set('bir bu şu su ve veya ile için icin gibi olan olarak mi mı mu mü nedir nasıl nasil bana bize bunu şunu sunu lütfen lutfen hakkında hakkinda benim bizim sen siz ne kaç kac kadar daha'.split())


class SelectionUnavailable(Exception):
    public_message='Seçtiğin belge kaldırılmış veya kullanıma kapatılmış. Belgeleri kontrol edip yeniden dene.'
class SourceChanged(Exception):
    public_message='Yanıt hazırlanırken kullanılan belge kaldırıldı veya kapatıldı. Eski bilgiyle sonuç kaydedilmedi; yeni bir istekle devam edebilirsin.'


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text.casefold().replace('ı','i')) if not unicodedata.combining(c))


def parse(raw,kind):
    if not _parsers.acquire(blocking=False):raise HTTPException(429,'Diğer belgelerin okunmasını bekleyip yeniden yükle.')
    try:
        # The parser receives no application credentials and cannot choose an input path.
        with tempfile.TemporaryDirectory(prefix='isdas-document-') as folder:
            base=Path(folder).resolve()
            assert base.parent==Path(tempfile.gettempdir()).resolve()
            file=base/('input.'+kind);file.write_bytes(raw)
            env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','PATH','LANG','TEMP','TMP'}}
            env['PYTHONIOENCODING']='utf-8'
            executable=sys.executable
            if os.name=='nt':
                # Run the interpreter itself, so timeout kills the parser rather
                # than only a venv launcher process.
                executable=sys._base_executable
                env['PYTHONPATH']=os.pathsep.join([str(Path(__file__).resolve().parents[1]),*site.getsitepackages()])
            try:
                result=subprocess.run([executable,'-m','backend.document_parser',str(file),kind],cwd=Path(__file__).resolve().parents[1],env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=PARSER_TIMEOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            except subprocess.TimeoutExpired:raise HTTPException(422,'Belgeyi okumak çok uzun sürdü. Daha küçük veya sade bir dosya yükle.')
            if result.returncode or len(result.stdout)>2*1024*1024:raise HTTPException(422,'Belge kaynak sınırları içinde okunamadı. Daha sade bir dosya yükle.')
            try:data=json.loads(result.stdout)
            except ValueError:raise HTTPException(422,'Belge okunamadı.')
            if not data.get('ok'):raise HTTPException(422,data.get('error','Belge okunamadı.'))
            return data
    finally:_parsers.release()


def chunks(sections):
    result=[]
    for section in sections:
        text=section['text'];start=0;part=0
        while start<len(text):
            end=min(start+1600,len(text))
            if end<len(text):
                split=text.rfind(' ',start+1000,end)
                if split>start:end=split
            part+=1;result.append({'label':section['label']+(f' · bölüm {part}' if len(text)>1600 else ''),'body':text[start:end]})
            if end==len(text):break
            start=end-160
    if len(result)>2000:raise HTTPException(422,'Belgede çok fazla ayrı bölüm var. Dosyayı bölerek yükle.')
    return result


def public(row):
    return {**{k:row[k] for k in ['id','name','kind','bytes','characters','enabled','created','updated']},'warnings':json.loads(row['extraction'])['warnings']}


def validate_selection(conn,wid,ids):
    for did in ids:
        if not conn.execute('SELECT 1 FROM documents WHERE id=? AND workspace_id=? AND enabled=1',(did,wid)).fetchone():raise SelectionUnavailable()


def retrieve(wid,prompt,ids=(),limit=8):
    tokens=list(dict.fromkeys(w for w in re.findall(r'\w{3,}',fold(prompt)) if w not in STOP))[:32]
    match=' OR '.join('"'+t+'"*' for t in tokens)
    with db.connection() as conn:
        validate_selection(conn,wid,ids)
        where='d.workspace_id=? AND c.workspace_id=? AND d.enabled=1';args=[wid,wid]
        if ids:where+=' AND d.id IN ('+','.join('?' for _ in ids)+')';args+=list(ids)
        cols='c.id AS chunk_id,c.document_id,c.ordinal,c.label,c.body,d.name,d.sha256,d.extraction'
        found=[]
        if match:
            found=[dict(r) for r in conn.execute(f'SELECT {cols} FROM document_search f JOIN document_chunks c ON c.id=f.rowid JOIN documents d ON d.id=c.document_id WHERE {where} AND document_search MATCH ? ORDER BY bm25(document_search,2,1),c.id LIMIT 64',(*args,match))]
        method='keyword'
        if found and ids:
            # Every explicitly selected document gets representation, even when
            # only one document matches the query vocabulary.
            first=[]
            for did in ids:
                candidate=next((r for r in found if r['document_id']==did),None)
                if candidate is None:
                    row=conn.execute(f'SELECT {cols} FROM document_chunks c JOIN documents d ON d.id=c.document_id WHERE d.workspace_id=? AND c.workspace_id=? AND d.id=? AND d.enabled=1 ORDER BY c.ordinal LIMIT 1',(wid,wid,did)).fetchone()
                    if row:candidate={**dict(row),'retrieval':'selected_sample'}
                if candidate:first.append(candidate)
            seen={r['chunk_id'] for r in first};found=first+[r for r in found if r['chunk_id'] not in seen]
        if not found and ids:
            # Explicit selection: sample across a document instead of claiming a full read.
            rows=[dict(r) for r in conn.execute(f'SELECT {cols} FROM document_chunks c JOIN documents d ON d.id=c.document_id WHERE {where} ORDER BY d.id,c.ordinal',args)]
            groups=[[r for r in rows if r['document_id']==did] for did in ids]
            sampled=[]
            for group in groups:
                count=min(limit,len(group))
                sampled.append([group[round(i*(len(group)-1)/max(count-1,1))] for i in range(count)])
            found=[group[i] for i in range(limit) for group in sampled if len(group)>i];method='selected_sample'
        selected=found[:limit]
        return [{'code':f'K{i+1}',**{k:v for k,v in row.items() if k!='extraction'},'warnings':json.loads(row['extraction'])['warnings'],'retrieval':row.get('retrieval',method)} for i,row in enumerate(selected)]


def validate_citations(text,sources):
    known={s['code'] for s in sources};used=set(re.findall(r'\[(K\d+)\]',text));unknown=used-known
    for code in unknown:text=text.replace('['+code+']','[belge atfı doğrulanamadı]')
    return text,[{**s,'cited':s['code'] in used} for s in sources],bool(unknown)


def persist_sources(conn,job,sources):
    for s in sources:
        row=conn.execute('SELECT d.sha256,c.body FROM documents d JOIN document_chunks c ON c.document_id=d.id WHERE d.id=? AND c.id=? AND d.workspace_id=? AND c.workspace_id=? AND d.enabled=1',(s['document_id'],s['chunk_id'],job['workspace_id'],job['workspace_id'])).fetchone()
        if not row or row['sha256']!=s['sha256'] or row['body']!=s['body']:raise SourceChanged()
        payload={k:s[k] for k in ['name','label','sha256','body','retrieval']}
        conn.execute('INSERT INTO job_document_sources VALUES(?,?,?,?,?,?)',(job['id'],s['document_id'],s['chunk_id'],s['code'],json.dumps(payload,ensure_ascii=False),int(s.get('cited',False))))


class EnableIn(BaseModel):enabled:bool


def register_routes(app,user,workspace,owner):
    @app.get('/api/workspaces/{wid}/documents')
    def listing(wid,u=Depends(user)):
        workspace(wid,u);rows=db.query('SELECT * FROM documents WHERE workspace_id=? ORDER BY created DESC',(wid,))
        return {'items':[public(r) for r in rows],'bytes':sum(r['bytes'] for r in rows),'limit_bytes':MAX_STORAGE,'document_limit':MAX_DOCUMENTS,'visibility':'workspace'}

    @app.post('/api/workspaces/{wid}/documents')
    async def upload(wid,file:UploadFile=File(),u=Depends(user)):
        workspace(wid,u);name=(file.filename or '').replace('\\','/').split('/')[-1][:150];kind=Path(name).suffix.lower().lstrip('.')
        if kind not in {'pdf','docx','txt','md','csv'}:raise HTTPException(400,'PDF, DOCX, TXT, CSV veya Markdown yükleyebilirsin.')
        raw=await file.read(MAX_UPLOAD+1)
        if not raw or len(raw)>MAX_UPLOAD:raise HTTPException(413,'Dosya boş olmamalı ve en fazla 8 MB olmalı.')
        digest=hashlib.sha256(raw).hexdigest()
        existing=db.query('SELECT * FROM documents WHERE workspace_id=? AND sha256=?',(wid,digest),one=True)
        if existing:return {**public(existing),'already_exists':True}
        parsed=await asyncio.to_thread(parse,raw,kind);parts=chunks(parsed['sections'])
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing=conn.execute('SELECT * FROM documents WHERE workspace_id=? AND sha256=?',(wid,digest)).fetchone()
            if existing:return {**public(existing),'already_exists':True}
            count,size=conn.execute('SELECT count(*),COALESCE(sum(bytes),0) FROM documents WHERE workspace_id=?',(wid,)).fetchone()
            if count>=MAX_DOCUMENTS or size+len(raw)>MAX_STORAGE:raise HTTPException(409,'Bu alanın belge sınırına ulaştın. Kullanmadığın belgeleri kaldır.')
            did=db.uid();now=db.now()
            conn.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,1,?,?)',(did,wid,u['id'],name,kind,len(raw),digest,parsed['characters'],json.dumps({'warnings':parsed['warnings']},ensure_ascii=False),now,now))
            conn.execute('INSERT INTO document_blobs VALUES(?,?)',(did,raw))
            for i,p in enumerate(parts):conn.execute('INSERT INTO document_chunks(document_id,workspace_id,ordinal,label,body,title,text) VALUES(?,?,?,?,?,?,?)',(did,wid,i,p['label'],p['body'],fold(name),fold(p['body'])))
            return {**public(conn.execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone()),'already_exists':False}

    @app.get('/api/workspaces/{wid}/document-search')
    def search(wid,q:str=Query(min_length=2,max_length=500),u=Depends(user)):
        workspace(wid,u)
        return {'items':retrieve(wid,q),'method':'keyword','visibility':'workspace'}

    def get(conn,wid,did):
        row=conn.execute('SELECT * FROM documents WHERE id=? AND workspace_id=?',(did,wid)).fetchone()
        if not row:raise HTTPException(404,'Belge bulunamadı.')
        return row

    @app.get('/api/workspaces/{wid}/documents/{did}')
    def detail(wid,did,offset:int=Query(default=0,ge=0),u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:
            row=get(conn,wid,did)
            items=[dict(r) for r in conn.execute('SELECT ordinal,label,body FROM document_chunks WHERE document_id=? AND workspace_id=? ORDER BY ordinal LIMIT 21 OFFSET ?',(did,wid,offset))]
            return {'document':public(row),'chunks':items[:20],'next_offset':offset+20 if len(items)>20 else None}

    @app.get('/api/workspaces/{wid}/documents/{did}/download')
    def download(wid,did,u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:
            row=get(conn,wid,did);raw=conn.execute('SELECT data FROM document_blobs WHERE document_id=?',(did,)).fetchone()[0]
        return Response(raw,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="isdas-belge-{did[:8]}.{row["kind"]}"'})

    @app.patch('/api/workspaces/{wid}/documents/{did}')
    def toggle(wid,did,body:EnableIn,u=Depends(user)):
        owner(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');get(conn,wid,did)
            conn.execute('UPDATE documents SET enabled=?,updated=? WHERE id=?',(int(body.enabled),db.now(),did))
        return {'ok':True}

    @app.delete('/api/workspaces/{wid}/documents/{did}')
    def delete(wid,did,u=Depends(user)):
        owner(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');get(conn,wid,did)
            # Keep the fact that a source was supplied, but erase its excerpt on deletion.
            for r in conn.execute('SELECT job_id,code,payload FROM job_document_sources WHERE document_id=?',(did,)).fetchall():
                payload=json.loads(r['payload']);payload.pop('body',None)
                conn.execute('UPDATE job_document_sources SET payload=? WHERE job_id=? AND code=?',(json.dumps(payload,ensure_ascii=False),r['job_id'],r['code']))
            conn.execute('DELETE FROM documents WHERE id=?',(did,))
        return {'ok':True,'previous_answers_deleted':False}

    @app.get('/api/workspaces/{wid}/jobs/{jid}/documents')
    def sources(wid,jid,u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:
            if not conn.execute('SELECT 1 FROM jobs WHERE id=? AND workspace_id=?',(jid,wid)).fetchone():raise HTTPException(404,'İş bulunamadı.')
            result=[]
            for r in conn.execute('SELECT s.*,d.id AS present,d.enabled FROM job_document_sources s LEFT JOIN documents d ON d.id=s.document_id AND d.workspace_id=? WHERE s.job_id=? ORDER BY s.code',(wid,jid)):
                payload=json.loads(r['payload']);result.append({'code':r['code'],'document_id':r['document_id'],'cited':bool(r['cited']),'available':bool(r['present']),'enabled':bool(r['enabled']),**payload})
        return {'items':result,'note':'Bu bölümler yanıt hazırlanırken asistana aktarıldı. Tüm belgenin okunduğu veya yanıtın doğrulandığı anlamına gelmez.'}
