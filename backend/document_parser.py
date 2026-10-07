"""Text extraction subprocess. No models, network, application DB or credentials."""
import csv,io,json,logging,os,re,sys,unicodedata,zipfile
from pathlib import Path

MAX_UPLOAD=8*1024*1024
MAX_TEXT=200000
MAX_PAGES=100


class Unreadable(ValueError):pass


def clean(value):
    return unicodedata.normalize('NFC',re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',' ',value)).strip()


def extract(raw,kind):
    if not raw or len(raw)>MAX_UPLOAD:raise Unreadable('Dosya boş veya 8 MB sınırını aşıyor.')
    sections=[];warnings=[];chars=0
    def add(label,value):
        nonlocal chars
        value=clean(value)
        if not value:return False
        chars+=len(value)
        if chars>MAX_TEXT:raise Unreadable('Belge 200.000 karakter sınırını aşıyor. Daha küçük dosyalara böl; hiçbir bölüm eklenmedi.')
        sections.append({'label':label,'text':value});return True
    if kind=='pdf':
        from pypdf import PdfReader
        if not raw.startswith(b'%PDF-'):raise Unreadable('Dosya geçerli bir PDF değil.')
        reader=PdfReader(io.BytesIO(raw),strict=False)
        if reader.is_encrypted:raise Unreadable('Bu PDF parola korumalı. Okumaya yetkili olduğun parolasız bir kopyasını yükle.')
        if len(reader.pages)>MAX_PAGES:raise Unreadable('PDF en fazla 100 sayfa olabilir.')
        empty=[]
        for i,page in enumerate(reader.pages,1):
            stream=page.get_contents()
            if stream and len(stream.get_data())>3*1024*1024:raise Unreadable('PDF sayfası çok karmaşık. Daha sade bir PDF veya metin dosyası yükle.')
            if not add(f'Sayfa {i}',page.extract_text() or ''):empty.append(i)
        if empty:warnings.append('Metin okunamayan sayfalar: '+', '.join(map(str,empty))+'. Bu sayfalar aramaya eklenmedi; görseller OCR ile okunmuyor.')
        warnings.append('PDF metin sırası ve tablo hizası değişebilir. Sayıları ve tabloları özgün dosyayla karşılaştır.')
    elif kind=='docx':
        from docx import Document
        from docx.text.paragraph import Paragraph
        from docx.table import Table
        from docx.oxml.ns import qn
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            entries=z.infolist()
            if len(entries)>2000 or sum(e.file_size for e in entries)>32*1024*1024:raise Unreadable('Word dosyasının açılmış boyutu çok büyük.')
            if 'word/document.xml' not in z.namelist() or any('vbaProject' in e.filename for e in entries):raise Unreadable('Geçerli, makrosuz bir DOCX dosyası yükle.')
        doc=Document(io.BytesIO(raw));paragraph=0;table=0
        for block in doc.iter_inner_content():
            if isinstance(block,Paragraph):
                paragraph+=1;add(f'Paragraf {paragraph}',block.text)
            elif isinstance(block,Table):
                table+=1
                for i,row in enumerate(block.rows,1):
                    cells=[];seen=set()
                    for cell in row.cells:
                        # Merged cells are returned more than once by python-docx.
                        if cell._tc in seen:continue
                        seen.add(cell._tc)
                        text=' '.join(t.text or '' for t in cell._tc.iter(qn('w:t'))) if cell.tables else cell.text
                        cells.append(text)
                    add(f'Tablo {table} · satır {i}',' | '.join(cells))
        warnings.append('Word ana metni ve tablo hücreleri okundu. Görseller, metin kutuları, yorumlar, dipnotlar ve üst/alt bilgiler bu okumaya dahil değil.')
    elif kind in {'txt','md','csv'}:
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:raise Unreadable('Metin dosyasını UTF-8 biçiminde kaydedip tekrar yükle.')
        if kind=='csv':
            csv.field_size_limit(65536)
            rows=csv.reader(io.StringIO(text))
            for i,row in enumerate(rows,1):
                if i>10000 or len(row)>200:raise Unreadable('CSV en fazla 10.000 satır ve 200 sütun olabilir.')
                add(f'Satır {i}',' | '.join(row))
        else:
            lines=text.splitlines()
            for start in range(0,len(lines),30):add(f'Satır {start+1}–{min(start+30,len(lines))}','\n'.join(lines[start:start+30]))
    else:raise Unreadable('PDF, DOCX, TXT, CSV veya Markdown yükleyebilirsin.')
    if not sections:raise Unreadable('Okunabilir metin bulunamadı. Taranmış PDF için metin içeren veya OCR uygulanmış bir kopya yükle.')
    return {'sections':sections,'warnings':warnings,'characters':chars,'kind':kind}


def main():
    if os.name!='nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(15,15))
    logging.disable(logging.CRITICAL)
    try:
        result=extract(Path(sys.argv[1]).read_bytes(),sys.argv[2])
        print(json.dumps({'ok':True,**result},ensure_ascii=True))
    except Unreadable as e:print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=True))
    except Exception:print(json.dumps({'ok':False,'error':'Belge okunamadı. Dosyayı kontrol edip yeniden kaydederek yükle.'}))

if __name__=='__main__':main()
