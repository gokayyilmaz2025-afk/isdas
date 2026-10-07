"""Anonymous product catalogue: explicit preview pricing and no account data."""
from . import accounts,billing
from .catalog import ROLES,CONNECTORS

PAGES={
    '':('İşdaş · İşini birlikte halledelim','Kendin, işletmen veya ajansın için bir çalışma alanı. İşini anlat; içerik, araştırma ve planlarını işdaşlarınla hazırla.'),
    'kullanim/kisisel':('Kişisel iş arkadaşın · İşdaş','Gününü planla, konuları araştır, belgelerinle çalış. Tercihlerini bilen işdaşlarınla kendi çalışma alanında ilerle.'),
    'kullanim/isletme':('İşletmen için bir ekip · İşdaş','Markanı bir kez anlat. İçerik planları, müşteri yanıtları ve araştırmaları kendi işletme bilgilerinle hazırla.'),
    'kullanim/ajans':('Her müşteriye ayrı çalışma alanı · İşdaş','Marka bilgileri, içerik taslakları ve müşteri onayları aynı akışta. Ekibinle hazırla, müşterine incelet.'),
    'isdaslar':('İşdaşlarınla tanış · İşdaş','İçerik, araştırma, ajanda, satış ve belge işleri için uzmanlıkları keşfet. Hazır bir işle başlayabilir veya kendi işdaşını tanımlayabilirsin.'),
    'baglantilar':('Bağlantılar ve yetkiler · İşdaş','Google, belgeler ve planlanan bağlantıların neler yapabildiğini gör. Hazırlama, onaylama ve dış işlem adımlarını ayrı ayrı tanı.'),
    'paketler':('Planlanan paketler ve pilot erişimi · İşdaş','Kişisel, işletme ve ajans paketlerinin planlanan kapsamını karşılaştır. Satın alma henüz açık değil; fiyatlar taslak.'),
    'yardim':('Başlangıç ve kullanım rehberi · İşdaş','Kurulum, hafıza, dosyalar, iş sonuçları, müşteri onayı ve kullanım hakları hakkında kısa yanıtlar.'),
}

def catalogue():
    return {'pricing_status':'preview','checkout_enabled':False,'registration_enabled':accounts.registration_enabled(),
            'plans':[{'id':key,'label':p['label'],'monthly_credits':p['credits'],'workspaces':p['workspaces'],'proposed_monthly_usd':p['proposed_usd']} for key,p in billing.PLANS.items() if key!='pilot'],
            'pilot':{'credits':billing.PLANS['pilot']['credits'],'workspaces':billing.PLANS['pilot']['workspaces'],'automatic_grant':False,'automatic_renewal':False},
            'agents':[{k:r[k] for k in ('id','name','title','icon','color','description')} for r in ROLES],
            'connections':[{k:r[k] for k in ('id','name','description','scope','stage')} for r in CONNECTORS]}

def register_routes(app):
    @app.get('/api/public/catalog')
    def get_catalog():return catalogue()

def page_html(index,path):
    """Static, escaped page metadata for link previews; application HTML is CSR."""
    import html,re
    title,description=PAGES.get(path,('Sayfa bulunamadı · İşdaş','İşdaş ana sayfasına veya kendi çalışma alanına dönebilirsin.'))
    text=index.read_text(encoding='utf-8')
    text=re.sub(r'<title>.*?</title>',lambda _:f'<title>{html.escape(title)}</title>',text,count=1,flags=re.S)
    text=re.sub(r'<meta name="description" content="[^"]*"\s*/?>',lambda _:f'<meta name="description" content="{html.escape(description,quote=True)}"/>',text,count=1)
    # No invented public domain, testimonials, ratings or pricing schema.
    extra=f'<meta property="og:type" content="website"/><meta property="og:title" content="{html.escape(title,quote=True)}"/><meta property="og:description" content="{html.escape(description,quote=True)}"/>'
    if path not in PAGES:extra+='<meta name="robots" content="noindex"/>'
    return text.replace('</head>',extra+'</head>',1)
