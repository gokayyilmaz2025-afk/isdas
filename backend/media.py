"""Private workspace media. Bytes and metadata commit atomically in local SQLite."""
import hashlib,io,warnings
from PIL import Image,ImageOps,UnidentifiedImageError
from fastapi import Depends,HTTPException,UploadFile,File,Response
from . import db
MAX_UPLOAD=8*1024*1024
MAX_STORAGE=50*1024*1024
MAX_PIXELS=16_000_000

def normalize_image(raw):
    if not raw or len(raw)>MAX_UPLOAD:raise HTTPException(413,'Görsel 8 MB altında olmalı.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {'JPEG','PNG','WEBP'} or getattr(source,'n_frames',1)!=1:raise ValueError('Unsupported image')
                if source.width*source.height>MAX_PIXELS or min(source.size)<16:raise ValueError('Image dimensions')
                source.load();oriented=ImageOps.exif_transpose(source)
                # Rebuild pixels without EXIF, comments, arbitrary chunks or filenames.
                mode='RGBA' if 'A' in oriented.getbands() else 'RGB'
                clean=Image.frombytes(mode,oriented.size,oriented.convert(mode).tobytes())
                out=io.BytesIO();clean.save(out,format='PNG');encoded=out.getvalue()
                if len(encoded)>MAX_UPLOAD:raise HTTPException(413,'İşlenen görsel 8 MB sınırını aşıyor. Daha küçük bir görsel seç.')
                return encoded,clean.width,clean.height
    except HTTPException:raise
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):raise HTTPException(400,'Tek kareli PNG, JPG veya WebP görsel seç. Boyut en az 16 piksel, en fazla 16 milyon piksel olmalı.')

def get(conn,wid,aid):
    row=conn.execute('SELECT * FROM media_assets WHERE id=? AND workspace_id=?',(aid,wid)).fetchone()
    if not row:raise HTTPException(404,'Görsel bulunamadı.')
    return dict(row)

def ensure_space(conn,wid,size,reservation_job=''):
    used=conn.execute('SELECT COALESCE(SUM(bytes),0) FROM media_assets WHERE workspace_id=?',(wid,)).fetchone()[0]
    held=conn.execute("SELECT COUNT(*) FROM image_requests i JOIN jobs j ON j.id=i.job_id WHERE i.workspace_id=? AND j.status IN ('queued','running') AND j.id!=?",(wid,reservation_job)).fetchone()[0]*MAX_UPLOAD
    if used+held+size>MAX_STORAGE:raise HTTPException(409,'Bu alanın 50 MB görsel sınırı doldu veya üretimlere ayrıldı. Kullanmadığın görselleri kaldırabilir veya devam eden üretimi bekleyebilirsin.')

def store(conn,wid,user_id,raw,name,kind='upload',reservation_job=''):
    encoded,width,height=normalize_image(raw);ensure_space(conn,wid,len(encoded),reservation_job)
    aid=db.uid()
    conn.execute('INSERT INTO media_assets VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,wid,user_id,str(name).replace('\x00','')[:150] or 'Görsel',kind,width,height,len(encoded),hashlib.sha256(encoded).hexdigest(),db.now()))
    conn.execute('INSERT INTO media_blobs VALUES(?,?)',(aid,encoded))
    return get(conn,wid,aid)

def register_routes(app,user,workspace,owner):
    @app.get('/api/workspaces/{wid}/media')
    def listing(wid,u=Depends(user)):
        workspace(wid,u)
        return {'items':db.query('SELECT * FROM media_assets WHERE workspace_id=? ORDER BY created DESC LIMIT 200',(wid,)),'storage_limit_bytes':MAX_STORAGE,'used_bytes':db.query('SELECT COALESCE(SUM(bytes),0) AS n FROM media_assets WHERE workspace_id=?',(wid,),one=True)['n']}

    @app.post('/api/workspaces/{wid}/media')
    async def upload(wid,file:UploadFile=File(),u=Depends(user)):
        workspace(wid,u);raw=await file.read(MAX_UPLOAD+1)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');item=store(conn,wid,u['id'],raw,file.filename or 'Görsel')
        return item

    @app.get('/api/workspaces/{wid}/media/{aid}')
    def image(wid,aid,download:bool=False,u=Depends(user)):
        workspace(wid,u)
        with db.connection() as conn:
            get(conn,wid,aid);row=conn.execute('SELECT data FROM media_blobs WHERE asset_id=?',(aid,)).fetchone()
        if not row:raise HTTPException(404,'Görsel dosyası bulunamadı.')
        return Response(row['data'],media_type='image/png',headers={'Content-Disposition':('attachment' if download else 'inline')+f'; filename="isdas-{aid[:8]}.png"'})

    @app.delete('/api/workspaces/{wid}/media/{aid}')
    def remove(wid,aid,u=Depends(user)):
        owner(wid,u)
        with db.connection() as conn:
            conn.execute('BEGIN IMMEDIATE');get(conn,wid,aid)
            if conn.execute("SELECT 1 FROM brand_profiles WHERE workspace_id=? AND json_extract(payload,'$.logo_asset_id')=?",(wid,aid)).fetchone() or conn.execute("SELECT 1 FROM content_posts WHERE workspace_id=? AND json_extract(payload,'$.asset_id')=?",(wid,aid)).fetchone():raise HTTPException(409,'Bu görsel marka kitinde veya bir içerikte kullanılıyor. Önce oradaki bağlantısını kaldır.')
            conn.execute('DELETE FROM media_assets WHERE id=? AND workspace_id=?',(aid,wid))
        return {'ok':True}
