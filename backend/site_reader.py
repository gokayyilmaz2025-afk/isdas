"""Bounded public HTTPS page reader. Run in a credential-free subprocess."""
import http.client,ipaddress,json,re,socket,ssl,sys,time
from html.parser import HTMLParser
from urllib.parse import urlsplit,urlunsplit,urljoin,quote

MAX_BYTES=1024*1024
MAX_TEXT=15000
class Unreadable(ValueError):pass

def normalize_url(value):
    if not isinstance(value,str) or not 1<=len(value)<=2000 or any(ord(c)<33 for c in value) or '\\' in value:raise Unreadable('Geçerli, herkese açık bir HTTPS adresi gir.')
    try:
        p=urlsplit(value)
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):raise ValueError()
        host=p.hostname.encode('idna').decode('ascii').lower().rstrip('.')
        if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',host):raise ValueError()
        path=quote(p.path or '/',safe='/%:@-._~!$&\x27()*+,;=')
        query=quote(p.query,safe='=&%/:@-._~!$\x27()*+,;?')
        return urlunsplit(('https',host,path,query,''))
    except (ValueError,UnicodeError):raise Unreadable('HTTPS kullanan bir alan adı gir. Özel ağ, IP adresi, kullanıcı bilgisi veya farklı port kullanılamaz.') from None

def public_addresses(host):
    try:rows=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    except OSError:raise Unreadable('Site adresi çözümlenemedi. Adresi kontrol et.') from None
    results=[]
    for family,kind,proto,canon,sockaddr in rows:
        ip=ipaddress.ip_address(sockaddr[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or getattr(ip,'ipv4_mapped',None) or getattr(ip,'sixtofour',None) or getattr(ip,'teredo',None):raise Unreadable('Bu adres herkese açık bir web sitesi olarak okunamıyor.')
        value=(family,sockaddr)
        if value not in results:results.append(value)
    if not results:raise Unreadable('Site adresi bulunamadı.')
    return results

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,address):super().__init__(host,timeout=6,context=ssl.create_default_context());self.address=address
    def connect(self):
        family,sockaddr=self.address;sock=socket.socket(family,socket.SOCK_STREAM);sock.settimeout(6)
        try:
            sock.connect(sockaddr)
            self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except BaseException:sock.close();raise

class TextPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.title=[];self.description='';self.stack=[];self.in_title=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {'script','style','noscript','template','svg','nav','footer','header','iframe','form'}:self.stack.append(tag)
        if tag=='title':self.in_title=True
        if tag=='meta' and (attrs.get('name','').lower()=='description' or attrs.get('property','').lower()=='og:description') and not self.description:self.description=attrs.get('content','')[:2000]
        if not self.stack and tag in {'p','div','section','article','main','li','h1','h2','h3','br'}:self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in self.stack:
            i=len(self.stack)-1-self.stack[::-1].index(tag);self.stack=self.stack[:i]
        if tag=='title':self.in_title=False
        if not self.stack and tag in {'p','div','li','h1','h2','h3'}:self.parts.append('\n')
    def handle_data(self,data):
        if self.in_title:self.title.append(data)
        elif not self.stack:self.parts.append(data+' ')

def extract(raw,content_type):
    if not raw or len(raw)>MAX_BYTES:raise Unreadable('Sayfa boş veya 1 MB sınırını aşıyor.')
    mime=content_type.split(';')[0].strip().lower()
    if mime not in {'text/html','text/plain','application/xhtml+xml'}:raise Unreadable('Bu adres bir metin/HTML sayfası değil. Belgeyse dosya olarak yükleyebilirsin.')
    charset=re.search(r'charset=([\w-]+)',content_type,re.I)
    try:html=raw.decode(charset.group(1) if charset else 'utf-8',errors='replace')
    except LookupError:html=raw.decode('utf-8',errors='replace')
    page=TextPage()
    if mime=='text/plain':text=html;title='';description=''
    else:
        page.feed(html);text=''.join(page.parts);title=' '.join(''.join(page.title).split())[:300];description=' '.join(page.description.split())
    text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',' ',text)
    text='\n'.join(' '.join(line.split()) for line in text.splitlines() if line.strip())
    if len(text.strip())<40:raise Unreadable('Yeterli metin okunamadı. Sayfa giriş veya JavaScript gerektiriyor olabilir. Bilgileri kendin yazabilir ya da belge yükleyebilirsin.')
    warnings=['Yalnızca bu sayfanın sunucudan gelen metni okundu. Alt sayfalar, görseller ve JavaScript ile sonradan gelen içerikler okunmadı.']
    if len(text)>MAX_TEXT:warnings.append('Sayfa uzun olduğu için ilk 15.000 karakter gösteriliyor. Eksik bölümleri ayrıca ekleyebilirsin.')
    return {'title':title,'description':description,'text':text[:MAX_TEXT],'warnings':warnings}

def read(value):
    url=normalize_url(value);started=time.monotonic();visited=set()
    for hop in range(4):
        if url in visited:raise Unreadable('Site yönlendirmesi döngüye girdi.')
        visited.add(url);p=urlsplit(url);addresses=public_addresses(p.hostname);response=None;connection=None
        for address in addresses[:2]:
            try:
                connection=PinnedHTTPS(p.hostname,address)
                connection.request('GET',p.path+('?' + p.query if p.query else ''),headers={'User-Agent':'Isdas-Site-Reader/1.0','Accept':'text/html,text/plain','Accept-Encoding':'identity','Connection':'close'})
                response=connection.getresponse();break
            except (OSError,http.client.HTTPException):
                if connection:connection.close()
        if response is None:raise Unreadable('Siteye güvenli bağlantı kurulamadı. Daha sonra dene veya bilgilerini kendin ekle.')
        try:
            if response.status in {301,302,303,307,308}:
                url=normalize_url(urljoin(url,response.getheader('Location','')));continue
            if response.status!=200:raise Unreadable('Site bu sayfanın okunmasına izin vermedi veya sayfa bulunamadı.')
            if response.getheader('Content-Encoding','identity').lower() not in {'','identity'}:raise Unreadable('Site beklenen metin biçimini göndermedi. Bilgilerini kendin ekleyebilirsin.')
            data=bytearray()
            while True:
                if time.monotonic()-started>18:raise Unreadable('Siteyi okumak uzun sürdü. Daha sonra tekrar dene.')
                block=response.read1(min(65536,MAX_BYTES+1-len(data)))
                if not block:break
                data.extend(block)
                if len(data)>MAX_BYTES:raise Unreadable('Sayfa 1 MB sınırını aşıyor. Daha kısa bir sayfa adresi kullan.')
            return {'url':url,**extract(bytes(data),response.getheader('Content-Type',''))}
        finally:connection.close()
    raise Unreadable('Site çok fazla yönlendirme yapıyor. Son sayfanın adresini kullan.')

if __name__=='__main__':
    # -I intentionally ignores PYTHONIOENCODING; the parent protocol is UTF-8
    # regardless of the Windows console's legacy code page.
    sys.stdin.reconfigure(encoding='utf-8',errors='strict')
    sys.stdout.reconfigure(encoding='utf-8',errors='strict')
    try:
        if sys.platform!='win32':
            import resource
            resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024));resource.setrlimit(resource.RLIMIT_CPU,(12,12))
        result={'ok':True,**read(sys.stdin.read(2001).strip())}
    except Unreadable as e:result={'ok':False,'error':str(e)}
    except Exception:result={'ok':False,'error':'Site okunamadı. Bilgilerini kendin yazabilir veya belge yükleyebilirsin.'}
    print(json.dumps(result,ensure_ascii=False))
