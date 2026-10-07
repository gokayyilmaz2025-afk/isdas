"""Evidence-based Turkish PDF briefing. All forecasts are explicit scenarios."""
from pathlib import Path
import sys, json, html, math
ROOT=Path(__file__).resolve().parents[1]
OLD=Path('C:/Users/gokay/Documents/Ajan-Urun-Arastirmasi-2026-10-03')
sys.path.insert(0,str(OLD/'.deps'))
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph,Table,TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader

OUT=ROOT/'output/pdf';OUT.mkdir(parents=True,exist_ok=True)
W,H=960,600
INK='#172C48'; BLUE='#245AF2'; MUTED='#63758D'; PALE='#F3F6FC'; LINE='#DCE4F0'; ORANGE='#F0A247'
for name,file in [('Body','arial.ttf'),('Bold','arialbd.ttf'),('Italic','ariali.ttf')]:
    pdfmetrics.registerFont(TTFont(name,'C:/Windows/Fonts/'+file))
pdfmetrics.registerFontFamily('Body',normal='Body',bold='Bold',italic='Italic',boldItalic='Bold')
S={}
def src(key,title,url):S[key]={'title':title,'url':url};return key
src('marb','Marblism ürün','https://www.marblism.com/')
for name in ['eva','sonny','stan','penny','walter','rachel','linda']:
    src(name,'Marblism '+name.title(),'https://www.marblism.com/ai-employees/'+name)
src('marbprice','Marblism fiyatlar ve iş saati hesabı','https://help.marblism.com/en/articles/15361461-marblism-pricing-plans-billing-cadence')
src('marbspaces','Marblism çalışma alanı sınırları','https://help.marblism.com/en/articles/15688766-when-should-i-create-a-new-workspace-best-practices')
src('marbint','Marblism entegrasyonlar','https://help.marblism.com/en/articles/15278633-what-integrations-are-available')
src('sintra','Sintra ürün ve rol kataloğu','https://sintra.ai/')
src('summer','Sintra Summer Edition 2026','https://sintra.ai/summer-edition')
src('sintraprice','Sintra fiyatlar','https://help.sintra.ai/en/articles/12606895-sintra-ai-plans-and-pricing')
src('sintralang','Sintra arayüz dilleri','https://help.sintra.ai/en/articles/12448626-what-languages-is-the-ui-interface-available-in')
src('sintraint','Sintra entegrasyonlar','https://sintra.ai/integrations')
src('brain','Sintra Brain AI','https://sintra.ai/features/brain-ai')
for key,title in [('DdzWKQYJIK-','Marblism karakter reklamı'),('Dd9gNr3il86','Marblism ilk kurulum'),('DeAj77dJiED','Marblism emlak videosu'),('Dd6pwG_JVZV','Marblism trend video anlatımı'),('DZM_braAGgS','Sintra çalışma alanı turu'),('Dbkaa1oNW5h','Sintra Brain ve Brand Kit'),('Db5DRlatzUa','Sintra sosyal medya iş akışı'),('DZfbmUOiYEL','Sintra özel yardımcı oluşturma')]:
    src(key,title,'https://www.instagram.com/p/'+key+'/')
src('marbad','Marblism örnek aktif reklam','https://www.facebook.com/ads/library/?id=1769320344267627')
src('sintraad','Sintra örnek aktif reklam','https://www.facebook.com/ads/library/?id=1827557191940323')
src('cash','Marblism kurucusunun Ağustos 2026 nakit beyanı','https://www.linkedin.com/pulse/after-20-years-i-finally-made-my-first-internet-dollar-ulric-musset-nlyif')
src('arr','Earlybird: Sintra Haziran 2025 beyanları','https://www.linkedin.com/pulse/earlybird-edition-june-2025-earlybird-venture-capital-3xofc')
src('bot','Grok Bot çalışma ortamı','https://docs.x.ai/grok-bot/overview')
src('botterms','Grok Bot kullanım koşulları','https://x.ai/legal/grok-bot-terms')
src('api','xAI ticari API koşulları','https://x.ai/legal/terms-of-service-enterprise')
src('price','xAI API fiyatları','https://docs.x.ai/developers/pricing')
src('costtracking','xAI gerçek istek maliyeti','https://docs.x.ai/developers/cost-tracking')
src('plans','Grok Bot kota ve faturalama','https://cursor.com/help/grok-bot/plans')
src('sdk','Cursor TypeScript SDK','https://cursor.com/docs/sdk/typescript')
src('hermes','Hermes Agent kaynak kodu','https://github.com/NousResearch/hermes-agent')
src('hermesapi','Hermes API sunucusu','https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server')
src('hermesprofiles','Hermes profil ayrımı','https://hermes-agent.nousresearch.com/docs/user-guide/profiles')
src('hermessec','Hermes güvenlik modeli','https://hermes-agent.nousresearch.com/docs/user-guide/security')
src('license','Hermes MIT lisansı','https://raw.githubusercontent.com/NousResearch/hermes-agent/main/LICENSE')

SLIDES=[]
def slide(title,lead='',bullets=None,table=None,widths=None,refs=None,note='',image=None,kind='normal'):
    SLIDES.append(dict(title=title,lead=lead,bullets=bullets or [],table=table,widths=widths,refs=refs or [],note=note,image=image,kind=kind))

slide('İşini anlat. Sonucunu al.','İşdaş için rakip incelemesi, ürün tasarımı, maliyet modeli ve uygulama planı',
      bullets=['Marblism ve Sintra: hizmetlerin ve gerçek panel akışlarının incelemesi','Grok Bot, Grok API ve Hermes: ticari ürün için altyapı seçimi','4 Ekim 2026 · Araştırma ve yerel uygulama durumu'],kind='cover')
slide('Önerilen ürün ve altyapı','Kendi markamız ve müşteri alanlarımız üzerinde, değiştirilebilir model ve ajan bağlantılarıyla ilerlemek.',
      table=[['Katman','Karar','Gerekçe'],['Müşterinin gördüğü ürün','İşdaş web ve mobil deneyimi','Tek konuşma alanı; yapılan işi ve teslimi görünür kılma.'],['Kontrollü işler','Grok API + uygulamanın araçları','İş başına yetki, kayıt, tekrar deneme ve maliyet kontrolü.'],['Daha serbest ajan işleri','İzole Hermes ortamları - pilot sonrası','Hazır ajan yeteneklerini kullanabilme; daha yüksek işletme yükü.'],['Grok Bot aboneliği','Ürün denemesi / iç çalışma için değerlendirme','Standart hesabı çok müşterili hizmetin temeli saymama.']],widths=[160,240,464],refs=['api','botterms','hermesapi'],
      note='Grok ve Hermes arasında canlı başarı testi henüz yapılmadı. Bu seçim, doğrulanmış kalite üstünlüğü iddiası değil, kontrol ve işletim gereksinimlerine dayalı mimari öneridir.')
slide('İncelemenin kanıt sınırları','Ürün beyanı, videoda görülen akış, reklam kaydı ve bizim testimiz ayrı kanıt türleridir.',
      bullets=['Resmî Instagram hesaplarından 8 video: görüntü kareleri incelendi; ses içeren 7 kayıt makineyle yazıya çevrildi. Marka adlarındaki yazım hataları bağlamla kontrol edildi.','Bir Sintra videosunun indirilen kopyasında ses izi yoktu; bulgular görüntü ve açıklamaya dayanıyor.','Apify reklam örneklemi: resmî sayfa kimliği ve hedef alan adı eşleşen 10 Marblism + 10 Sintra kaydı. Diğer markalara ait sonuçlar ayrıldı.','Rakiplerin ücretli hesaplarında uçtan uca iş çalıştırılmadı. Tanıtımdaki özellikler, bizim canlı doğruladığımız entegrasyonlar değildir.'],
      note='Apify Instagram run: 0VaUSIlmFLbPMrzEz, SUCCEEDED. Reklam run: KaUZNC776WaHaW2w4, SUCCEEDED. Sonuçlar 3 Ekim 2026; durumları 4 Ekim yeniden kontrol edildi.')
slide('Marblism nasıl bir deneyim satıyor?','Müşteriye uzman çalışan seçtiriyor; iş yükünü sohbet üzerinden teslim alıyor.',
      bullets=['İlk seçim: işletmem, işim veya kişisel yaşamım. İşletme seçeneğinde site adresiyle bağlam toplama; kişisel kullanımda site zorunluluğu yok.','Çalışma alanı, marka bilgisi ve iş rolü: kullanıcıya model seçtirmek yerine Eva, Sonny veya Stan gibi görev sahibi sunuluyor.','Her çalışanın kurulumunda ilgili hesaplar bağlanıyor. İşin türüne göre taslak, plan, dosya veya uygulamada işlem elde ediliyor.','Sonuç gözden geçiriliyor, düzeltiliyor ve uygun işlerde planlanıyor. Tanıtım videolarında kullanıcı müdahalesi ve üretim düğmesi de var.'],refs=['Dd9gNr3il86','marbint','DeAj77dJiED'])
slide('Marblism: e-posta, sosyal medya ve satış','Her karakterin rolü somut bir iş çıktısına bağlanıyor.',
      table=[['Çalışan','Sunduğu işler','Bizde eşdeğer hizmetin kabul şartı'],['Eva · yönetici asistanı','Gelen kutusu düzeni, cevap taslağı, takvim ve toplantı notu.','Gerçek posta bağlantısı; taslak önizleme; takvimde doğrulanmış kayıt; işlem geçmişi.'],['Sonny · sosyal medya','Markaya uygun plan, görsel/video, platformlara yayın ve içerik takibi.','Marka kiti, düzenlenebilir içerik takvimi, medya üretimi, onay ve doğrulanmış yayın bağlantısı.'],['Stan · satış','Uygun müşteri araştırması, kişiselleştirilmiş iletişim, takip ve randevu.','Kaynaklı kişi/şirket listesi; izinli veri kaynağı; gönderim onayı; durdurulabilir takip.']],widths=[168,328,368],refs=['eva','sonny','stan'],
      note='Veri tabanı büyüklüğü, yanıt artışı ve kazanılan müşteri gibi pazarlama iddiaları bağımsız doğrulanmadı; kendi performans hedefimiz olarak alınmadı.')
slide('Marblism: diğer dört iş kolu','Sadece sohbet cevabı, bu hizmetlerin karşılığı sayılmaz.',
      table=[['Çalışan','Ürün sayfasındaki hizmet','Bizim uygulama gereksinimi'],['Penny · SEO','Konu/anahtar kelime araştırması, yazı ve siteye yayın.','Kaynaklı taslak, düzenleme, CMS taslak/yayın bağlantısı.'],['Walter · web sitesi','Site oluşturma/değiştirme; ziyaretçi verisiyle deneyler.','Önizleme, alan adı, yayın/geri alma, ölçüm ve yeterli örneklem.'],['Rachel · telefon','Arama yanıtlama, randevu, yönlendirme ve görüşme özeti.','Telefon sağlayıcısı; Türkçe ses testi; gerçek takvim; maliyet limiti.'],['Linda · belge','Sözleşme açıklaması, risk işaretleme ve belge taslağı.','Dosya okuma ve dışa aktarma; kaynak bölümü; uzman incelemesine elverişli çıktı.']],widths=[159,327,378],refs=['penny','walter','rachel','linda'],
      note='Türkiye telefon numarası ve yerel arama koşulları doğrulanmadı. Hukuki belge çıktısı profesyonel incelemenin yerine geçecek şekilde pazarlanmamalı.')
slide('Sintra: 12 uzman ve ortak işletme bilgisi','Aynı çalışma alanındaki uzmanlar Brain AI içeriğinden yararlanıyor.',
      table=[['Uzman','Rol','Uzman','Rol'],['Soshie','Sosyal medya','Seomi','SEO'],['Milli','Satış','Penn','Metin yazarlığı'],['Emmie','E-posta','Cassie','Müşteri desteği'],['Dexter','Veri analizi','Scouty','İşe alım'],['Buddy','İş stratejisi','Commet','E-ticaret'],['Gigi','Kişisel planlama','Vizzy','Yönetici / sanal asistan']],widths=[120,312,120,312],refs=['sintra','summer','DZM_braAGgS'],
      note='Rol adları ürün kataloğundan alındı. Bir rolün varlığı, her harici sistemde sınırsız işlem yapabildiği anlamına gelmez; bağlantı ve yetki ayrıca gerekir.')
slide('Sintra sosyal medya akışının ayrıntısı','Videoda planlama, düzenleme, marka bilgisi ve yayın ayarları birlikte gösteriliyor.',
      bullets=['00:30 civarı: her pazar gelecek iki haftanın gönderileri hazırlanıyor; kullanıcı gözden geçiriyor.','Görseli yeniden üretme, gerçek fotoğraf/medya yükleme; açıklamayı elle veya yapay zekâyla düzeltme gösteriliyor. Tarih, platform, gönderi türü ve sayfa seçimi bulunuyor.','01:30 civarı: içerik temaları. İlk kurulumda 6 tema anlatılıyor; yeni tema için konu, amaç, sıklık ve platform konuşularak belirleniyor.','02:28 civarı: marka kiti. Logo, renk paleti, ton, işletme açıklaması ve değer önerisi; ardından referans medya kütüphanesi gösteriliyor.'],refs=['Db5DRlatzUa'],
      note='Zamanlar makine transkriptine göre yaklaşık. Video, arayüz akışını kanıtlıyor; gerçek hesapta başarılı yayın bizim tarafımızdan denenmedi.')
slide('Sintra hafıza, çalışma alanı ve özel yardımcı','Kullanıcının tek seferlik açıklamalarını tekrar kullanılabilir işletme bağlamına dönüştürüyor.',
      bullets=['Çalışma alanları farklı proje veya işletmeleri ayırıyor. Her alanın kendi Brain içeriği, yardımcıları ve bağlantıları bulunuyor.','Brain görünümünde belge, yükleme, bağlantı ve klasör ekleme; Brand Kit bölümünde markanın dili ve görselleri gösteriliyor.','Özel yardımcı videosu: kullanıcı görevi ve kişiliği konuşarak tarif ediyor; yardımcı kalıcı olarak kaydediliyor. Pazar yerine sunma da anlatılıyor.','Ekip üyeleri aynı alanın kapasitesini paylaşabiliyor. Bizde de üye daveti, rol ve müşteri onayı sadece görsel bir panel değil, gerçek yetki modeli olmalı.'],refs=['DZM_braAGgS','Dbkaa1oNW5h','DZfbmUOiYEL','brain'],
      note='Yardımcı satışının kazanç şartları ve pazar yeri ticari işleyişi ayrıca doğrulanmadı. Bizim kapsamımıza otomatik gelir vaadi olarak eklenmedi.')
slide('Marblism videolarında ne görüldü?',table=[['Tarih / kayıt','Gözlem','Çıkarımın sınırı'],['27 Eylül · DdzWKQYJIK-','Oyuncak ambalajı görünümünde rol karakterleri; kısa görev ve fayda anlatımı.','Marka ve reklam dili kanıtı; panel işleyişi kanıtı değil.'],['1 Ekim · Dd9gNr3il86','İşletme / iş / kişisel yaşam seçenekleri; web sitesiyle ilk kurulum.','Kullanıcı yolculuğu görüldü; site analiz kalitesi ölçülmedi.'],['2 Ekim · DeAj77dJiED','Emlak ilanı bağlantısı, senaryo/sahne önerisi, düzeltme, video üretimi ve sonuç.','Üretimden önce düzenleme var. Tek adımda kusursuz otomasyon iddiası kurulamaz.'],['30 Eylül · Dd6pwG_JVZV','Kurucu anlatımı ve trend video üretimi; 113 milyon görüntülenme beyanı.','Görüntülenmenin gelir ve reklam kaynaklı payı doğrulanmadı.']],widths=[210,335,319],refs=['DdzWKQYJIK-','Dd9gNr3il86','DeAj77dJiED','Dd6pwG_JVZV'])
slide('Sintra videolarında ne görüldü?',table=[['Tarih / kayıt','Gözlem','Bizim tasarıma etkisi'],['5 Haziran · DZM_braAGgS','Alan, Brain, ekip, yardımcı ve bağlantı turu.','Müşteri bilgisi ve yetkiler alan düzeyinde ayrılmalı.'],['3 Ağustos · Dbkaa1oNW5h','Brain yüklemeleri, klasörler ve Brand Kit.','Sadece düz not kutusu, kapsam eşitliği sağlamaz.'],['11 Ağustos · Db5DRlatzUa','Takvim, içerik temaları, medya ve düzenleme.','Sohbet yanında yapılandırılmış içerik görünümü gerekli.'],['12 Haziran · DZfbmUOiYEL','Konuşarak özel yardımcı ve pazar yeri anlatımı.','Özel asistan kurulumunu uzun teknik form yerine diyalogla sunma.']],widths=[210,335,319],refs=['DZM_braAGgS','Dbkaa1oNW5h','Db5DRlatzUa','DZfbmUOiYEL'])
slide('Karakter, hizmeti anlaşılır kılıyor','Marblism karakterleri ve Sintra robotları iş rolünü görsel olarak taşıyor; gerçek kişiler de paneli gösteriyor.',
      table=[['Karar','İşdaş için uygulama'],['Özgün karakter','Piko: mavi kuş, sıcak ve becerikli. Rakip karakterlerini veya isimlerini kopyalamama. İşdaş adı henüz marka uygunluğu araştırmasından geçmedi.'],['Görsel durum','Dinliyorum, hazırlıyorum, onayın gerekiyor, hazır, bağlantı kesildi. Animasyonu gerçek iş olaylarına bağlama.'],['Dil','“İşini anlat”, “İçeriğin hazır”, “Şu hesabına yayımlayacağım”. Kullanıcıya token, MCP ve ajan yönlendirme terimleri gösterilmeyecek.'],['Reklam anlatımı','Kısa karakter hikâyesi + gerçek panel + somut teslim. Üretim hatasını animasyonla saklamama.']],widths=[175,689],
      note='Karakterin dönüşümü artırması bir tasarım hipotezidir. Aynı teklif ve kitleyle karakterli / gerçek panel ağırlıklı reklamları karşılaştırmak gerekir.')
slide('Türkçe tek başına farklılaşma değil','Sintra arayüzünde Türkçe zaten var; hafıza, mobil kullanım ve özel yardımcılar da rakiplerde bulunuyor.',
      table=[['Fırsat','Dayanak','Doğrulama'],['Sektöre göre hazır işler','İşletmeyi tekrar anlatmadan doğru teslim almak.','Kafe, emlak ve hizmet işletmesinde 5’er gerçek görev.'],['Ajans müşteri onayı','Hangi müşteri için hangi hesabın değiştiğini açık göstermek.','Ajans çalışanı ve müşteriyle uçtan uca onay testi.'],['Aynı platformda çoklu hesap','Marblism aynı platformun her hesabı için ayrı alan öneriyor.','Tek müşteri alanında birden çok hesap seçimi; karışma testi.'],['Kaynak ve sonuç kanıtı','Araştırmada bağlantı; yayında gönderi adresi; takvimde kayıt kimliği.','Sonuç gösterildiğinde kaynak sisteminde yeniden kontrol.']],widths=[193,337,334],refs=['sintralang','marbspaces'],
      note='Bunlar fırsat hipotezleridir; ilk ve son satırın rakiplerde bulunmadığı iddia edilmiyor. Yerel prototip şu anda platform başına tek bağlantı tutuyor; çoklu hesap farkı henüz uygulanmadı.')
slide('İlk kullanım: anlat, bağla, sonucu gör','Kişisel, çalışan, işletme ve ajans seçenekleri aynı sade akışa açılmalı.',
      bullets=['1. “Ne için yardım istiyorsun?” Kullanıcı kendi cümlesiyle anlatır; başlangıç seçenekleri örnek verir.','2. İzinle site veya dosyadan bilgi toplama. Çıkarılan işletme özeti kullanıcıya doğrulatılır; yanlış bilgi kalıcı hafızaya sessizce yazılmaz.','3. Yalnızca ilk işin gerektirdiği hesabı bağlama. “Google bağla” yerine “Bu toplantıyı takvimine eklemek için Google’ı bağla.”','4. Somut ilk sonuç: 3 içerik taslağı, kaynaklı kısa araştırma veya ajanda planı. Yayımlama/gönderme öncesinde hedef hesap ve içerik gösterilir.','5. “Bunu her hafta yap” ile tekrar eden iş. Kullanıcı zamanı, kapsamı ve kullanım sınırını görür.'])
slide('Yerel uygulamanın mevcut görünümü','Gerçek yerel arayüz: kayıt, çalışma alanı, hafıza ve ajanda akışları tarayıcıda sınandı.',
      image='research/verification/workspace-desktop.png',
      note='Bu ekran üretimde çalışan tüm hizmetleri kanıtlamaz. Model anahtarı ve canlı bağlantılar henüz eklenmediği için yeni yapay zekâ işi başlatma kapalı; örnek işletme test verisidir.')
slide('İlk satış paketleri: fiyat deneyi','Paketler kullanım kapasitesi ve teslim kapsamıyla ayrılmalı. Aşağıdakiler öneridir; satışa açılmış teklifler değildir.',
      table=[['Paket','Aylık fiyat deneyi','Kapsam / sınır'],['Kişisel','19 USD','Kişisel hafıza, kaynaklı araştırma, ajanda, yazı ve dosya taslakları. Ses için ayrı dakika sınırı.'],['İşletme','49 USD','Marka kiti; içerik takvimi; e-posta ve takvim işleri; düzenli araştırma. Medya ve yayın işlemleri ayrı tüketim kalemleri.'],['Ajans','149 USD','5 müşteri alanı için başlangıç teklifi; ekip rolleri, müşteri onayı ve teslim geçmişi. Ek alan ve yoğun kullanım ayrıca.']],widths=[135,165,564],
      note='Türkiye fiyatı ödeme isteği, kur ve vergiler doğrulandıktan sonra belirlenmeli. 19/49/149 USD test önerisi; sonraki projeksiyondaki 29 USD karma gelir varsayımı aynı şey değildir. Limitsiz video/telefon vaadi yok.')
slide('Grok Bot aboneliği ve Grok API farklı ürünler','OAuth hesabı bağlar; yeni müşteri kotası veya yeniden satış lisansı oluşturmaz.',
      table=[['Seçenek','Avantaj','Sınırlama'],['Kişisel Grok Bot hesabı','Hazır bilgisayar, tarayıcı ve iş deneyimini hızlı deneme.','Aynı hesaptaki botlar ortamı paylaşır. Kota ortak; standart koşullar iç iş kullanımını tanımlar.'],['Grok API','Kendi ürününde kullanım; sunucudan model ve araç çağrısı.','Uygulama hesabı, hafıza, iş kuyruğu, onay ve bağlantılar bize ait geliştirmedir.'],['Cursor SDK','Resmî programatik ajan arayüzü; araştırılabilir alternatif.','SDK varlığı, bütün Grok Bot özelliklerinin white-label sunulduğunu kanıtlamaz. Ticari kapsam ayrıca kontrol edilmeli.']],widths=[170,330,364],refs=['bot','botterms','api','sdk','plans'],
      note='Tam Grok Bot hizmetini üçüncü taraf uygulamalara sunan kamuya açık white-label görev API’si doğrulanmadı. Özel anlaşma ve fiyat varsa ayrı değerlendirilir.')
slide('Hermes ne kazandırır, neyi çözmez?','Ajan çalıştırma altyapısı sağlayabilir; çok müşterili ticari ürün katmanı yine kurulmalıdır.',
      bullets=['MIT lisanslı kaynak kodu: lisans bildirimleri korunarak ticari kullanım mümkün. Bağlı model, veri ve diğer servislerin lisansları ayrıca geçerlidir.','API sunucusunda sohbet/yanıt uçları, kalıcı oturumlar ve çalışma olayları bulunuyor. Hazır hafıza, beceriler, zamanlama ve araç bağlantıları geliştirme yükünü azaltabilir.','Profiller yapılandırma ve hafızayı ayırıyor; tek başına işletim sistemi güvenlik sınırı değiller. Müşteri başına ayrı konteyner/volume ve dar araç izinleri gerekiyor.','Grok API anahtarı ve Hermes API anahtarı kullanıcı tarayıcısına verilmemeli. Hermes’in terminal yetkisi nedeniyle ortak ana bilgisayara sınırsız erişim verilmemeli.'],refs=['license','hermesapi','hermesprofiles','hermessec'],
      note='Kaynak sürümü incelendi: v2026.9.24. Bu bilgisayarda Hermes çalışma ortamı kurulup canlı görevle doğrulanmadı. Ücretsiz yazılım, ücretsiz model kullanımı anlamına gelmez.')
slide('İşin arka plandaki yürüyüşü','Önerilen mimari; müşteriye tek konuşma alanı, sunucuya ölçülebilir işlemler.',kind='architecture',
      note='Dış eylem başarısı kaynak sistemindeki kayıt kimliği / yayın bağlantısıyla doğrulanır. Uzmanlar yalnızca ihtiyaç olan görevlerde çağrılır; her istekte tüm ekip çalıştırılmaz.')
slide('Hafıza ve entegrasyon tasarımı','Müşteri bilgisi, bağlantı yetkisi ve dış işlem onayı ayrı tutulmalı.',
      bullets=['Hafıza: kullanıcı tercihleri, işletme bilgisi, marka kiti ve iş geçmişi. Kaynağı ve güncellenme tarihi tutulur; kullanıcı görüntüler, düzeltir ve siler.','Belge yükleme: dosya tipi ve boyutu denetlenir. Metin güvenli biçimde çıkarılır; tüm arşiv her soruda modele gönderilmez. İlgili parçalar seçilir.','Bağlantı: müşteri ve alan kimliğine bağlı şifreli token; kısa yetki kapsamı; yenileme ve bağlantıyı iptal etme. Her platformun yayınlama koşulları ayrı uygulanır.','Onay: ne yapılacak, hangi hesapta, içerik/alıcısı/tutarı ne? Onaylanan sürüm sabitlenir. İçerik sonradan değişirse yeniden onay gerekir.','Teslim: istek → işlem kaydı → harici sonuç kimliği → kullanıcıya sonuç. Kesintiden sonra aynı mesajı veya gönderiyi iki kez oluşturmama.'])
slide('Altyapı seçimini 20 görevle sınama','Aynı görev, aynı girdi ve aynı yetkiyle Grok araç akışı ile izole Hermes karşılaştırılmalı.',
      table=[['Grup','4 görev örneği','Ölçüm'],['İçerik','Haftalık plan, görsel düzeltme, çok platform uyarlama, planlı yayın.','Marka tutarlılığı; gerçek yayın; tekrar üretim.'],['Araştırma','Rakip karşılaştırma, güncel fiyat, kaynak çelişkisi, rapor dosyası.','Kaynak doğruluğu; güncellik; eksik bilgi beyanı.'],['Asistan','Saat dilimi, çakışan toplantı, e-posta taslağı, yinelenen görev.','Doğru kayıt; onay; tekrar işlem olmaması.'],['İşletme','Müşteri listesi, CSV analizi, destek taslağı, site değişiklik önizlemesi.','Sonucun kullanılabilirliği; veri ve yetki sınırı.'],['Dayanıklılık','Bağlantı süresi dolması, iptal, sunucu kesintisi, müşteri alanı değiştirme.','Geri kazanım; ücret takibi; veri karışmaması.']],widths=[125,461,278],
      note='Pilot kabul önerisi: en az 18/20 görevin doğru teslimi; hiçbir müşteriler arası veri sızıntısı veya onaysız dış yazma olmaması. Süre, dolar maliyeti ve insan düzeltmesi ayrıca ölçülür. Sonuç henüz yok.')
slide('Rakip fiyatları ve kullanım sınırları','4 Ekim 2026 resmî yardım sayfası görünümü; kampanya ve ödeme ekranı değişebilir.',
      table=[['Ürün','Gözlenen fiyat','Kapasite ve yorum'],['Marblism temel plan','Aylık 44 USD; yıllık ödeme seçeneğinde aylık eşdeğer 24 USD.','7 çalışan, 50 iş saati/ay. “Saat” gerçek sunucu süresi değil, görev tipine verilen tüketim değeri.'],['Marblism tüketim örneği','Sosyal gönderi: 30 dakika; blog: 1 saat.','Alanlar sahibin ortak havuzunu kullanıyor. Video için ayrıca havuz içi sınır var.'],['Sintra X başlangıç','Aylık 48,50 USD; yıllık tabloda 187,20 USD peşin / 15,60 USD eşdeğer.','12 uzman, 250 kredi/ay. İndirimli fiyatlar; KDV ödeme sırasında ekleniyor.']],widths=[178,314,372],refs=['marbprice','sintraprice'],
      note='Sintra aynı sayfada yıllık ödemeyi bir yerde 187 USD diye yuvarlıyor; tabloda 187,20 USD var. Kendi ürünümüzde peşin tutar ve aylık karşılığı açık gösterilmeli. Eski bireysel uzman planları güncel teklif gibi kullanılmadı.')
slide('Aktif reklam var; harcama tutarı bilinmiyor','3 Ekim 2026 Apify çıktısında resmî kimlik ve alan adıyla eşleşen örnekler.',
      table=[['Marka / örneklem','Mesaj ve hedef','Ne gösterir?'],['Marblism · 10 aktif kayıt','LinkedIn satış takibi; trend video ve sosyal medya. Marblism site/ilk kurulum sayfasına yönlendirme.','Facebook ve Instagram dahil reklam yayını. 10, toplam reklam sayısı değildir.'],['Sintra · 10 aktif kayıt','İşletme sahibinin yükünü azaltan uzman ekibi. quiz-claude ve quiz-gpt gibi test sayfalarına yönlendirme.','Test akışlı satış yaklaşımı. Sonuç kaydı, testin dönüşüm oranını göstermez.']],widths=[200,361,303],refs=['marbad','sintraad'],
      bullets=['Seçilen kayıtların spend ve reachEstimate alanları boş. Bütçe, ROAS, CAC, ödeme dönüşümü veya kâr reklam sayısından türetilemez.','Reklam kopyasındaki yüksek görüntülenme iddiaları sağlayıcı beyanıdır; bizim ürünümüz için başarı kanıtı değildir.'])
slide('Rakiplerin kazancı hakkında bildiklerimiz','Tarih ve ölçü birimi belirtilmeden ARR, aylık gelir ve banka hareketi karşılaştırılamaz.',
      table=[['Kaynak','Beyan','Doğru yorum'],['Marblism kurucusu · 3 Eylül 2026','Ağustos: 622.000 USD giriş, 620.000 USD çıkış; yaklaşık 2.000 USD fark.','Kurucu banka hareketi anlatıyor. Denetlenmiş net kâr veya API/reklam gider dağılımı değil.'],['Earlybird · 9 Temmuz 2025','Sintra: 12 milyon USD üzeri ARR ve ilk yılda 40 bin ödeme yapan müşteri.','Yatırımcı tarafından aktarılan tarihsel şirket beyanı. 2026 güncel geliri ve net kârı kanıtlamaz.']],widths=[236,289,339],refs=['cash','arr'],
      bullets=['Sintra için güncel ayrıntılı maliyet dökümü doğrulanmadı. Marblism için 620 bin doların kaçının reklama veya modele gittiği bu kaynakta yok.','Sonuç: pazar talebi ve büyüme işareti var; “onlar bu kadar kazanıyor, biz de kazanırız” şeklinde gelir tahmini kurulamaz.'])
slide('Kullanıcı başına örnek teknik tüketim','Grok 4.7 standart global API; her istekte giriş bağlamı 200 bin token altında. Önbellek indirimi varsayılmadı.',
      table=[['Kalem','Aylık varsayım','USD'],['Giriş tokenı','Tüm çağrılarda toplam 2 milyon','4,00'],['Çıktı tokenı','Düşünme dahil 400 bin ücretli token','2,40'],['Web aracı','50 çağrı','0,25'],['Görsel','20 adet Image 2.0 / 1K Low, metinden','0,80'],['Toplam','Örnek tüketim sepeti','7,45']],widths=[216,518,130],refs=['price'],
      note='Bu sepet müşteri ortalaması değildir. Ses, video, tarayıcı çalıştırma, Apify, sunucu, destek ve vergiler hariç. Paket fiyatından önce pilot görevlerde tüm alt çağrılar ve yeniden denemeler ölçülmeli.')
slide('Maliyet kontrolünün ürün gereksinimleri','Bir müşteri yoğun iş yaptığında diğer müşterilerin bütçesi tüketilmemeli.',
      bullets=['Müşteri hesabına dönemsel kullanım hakkı; aynı sahibin alanları ortak hakkı paylaşır. İş başlamadan puan ayrılır; yeni alan açmak kotayı artırmaz.','Gerçek token, araç, medya ve çalışma süresi kaydı. Başarısız veya iptal edilmiş ama ücret doğurmuş çağrılar da maliyete dahil.','Yeni görev başlamadan sınır kontrolü. Devam eden harici isteğin iptali, sağlayıcının ücretini sıfırladı diye varsayılmamalı.','Görsel, video, telefon ve tarayıcı işleri ayrı kotalı. Çok ajanlı işte uzman başına çağrı sayısı ve bağlam maliyeti görünür.','Müşteri ekranında anlaşılır kalan hak; yönetici ekranında gerçek dolar maliyeti. Model tur limiti tek başına kesin araç çağrısı üst sınırı değildir.'],
      refs=['costtracking'],note='Ortak müşteri puanı, kullanım ekranı, iptal ve dönem geçişi eklendi. Müşteri puanı işin başında ayrılan miktarı aşmaz; gerçek sağlayıcı aşımı işletmeye kalır. Canlı ödeme, Hermes toplam tutarı ve gerçek maliyet ölçümü henüz tamamlanmadı.')

def forecast(cac,churn,organic=5):
    active=0.;cum=0.;rows=[]
    for m,(ad,fixed) in enumerate(zip([500,750,1000,1500,2000,2500],[100,120,150,200,250,300]),1):
        new=ad/cac+organic;active=active*(1-churn)+new;gross=active*29
        api_cost=active*8;reserve=gross*.065;support=active*1.5
        result=gross-api_cost-reserve-support-ad-fixed;cum+=result
        rows.append(dict(month=m,ads=ad,new=new,active=active,gross=gross,technical=api_cost,fees_refunds=reserve,support=support,fixed=fixed,result=result,cumulative=cum))
    return rows
BASE=forecast(50,.08)
SCENARIOS={'Temkinli':forecast(80,.12,2),'Çalışma senaryosu':BASE,'İyi pilot sonucu':forecast(35,.05,8)}
(OUT/'gelir-gider-varsayimlari.json').write_text(json.dumps({'not_a_forecast_of_competitor_or_guarantee':True,'currency':'USD','blended_monthly_revenue_per_customer':29,'cost_per_active_customer':8,'support_reserve_per_active_customer':1.5,'fees_and_refund_reserve_rate':.065,'timing':'Yeni üyelerin ay başında geldiği basitleştirilmiş aylık kohort modeli; kesirli üyeler beklenen değer.','excluded':['tax','founder salary','initial development labor','annual prepayments'],'scenarios':SCENARIOS},ensure_ascii=False,indent=2),encoding='utf-8')
slide('Altı aylık hesabın varsayımları','Reklam verilerinden çıkarılmış sonuç değil; kendi ürünümüz için değiştirilebilir planlama modeli.',
      table=[['Değişken','Çalışma senaryosu'],['Ortalama aylık abonelik geliri','29 USD / aktif ödeme yapan müşteri; paket karışımı varsayımı.'],['Edinme','Reklamdan ödeme yapan müşteri CAC = 50 USD; ayda 5 organik yeni müşteri.'],['Aylık kayıp','%8. Her ay önce eski abonelerin kaybı, ardından yeni üyeler.'],['Değişken gider','8 USD teknik tüketim + 1,50 USD destek karşılığı / aktif müşteri.'],['Tahsilat ve iade karşılığı','Brüt gelirin %6,5’i; sağlayıcı teklifi değil, model varsayımı.'],['Zamanlama','Yeni üyeler ay başında kabul edilir. Kesirli aboneler beklenen değer; gerçek kişi sayısı değildir.']],widths=[241,623],
      note='Vergiler, kurucunun maaşı ve başlangıç geliştirme emeği hariç. Yıllık peşin tahsilat kullanılmadı. Bu nedenle aşağıdaki sonuç şirketin muhasebe net kârı değildir.')
def usd(v):return f'{v:,.0f}'.replace(',','.')
slide('Altı aylık çalışma senaryosu','Reklam bütçesi, kullanıcı kaybı ve devam eden servis maliyeti birlikte hesaplandı.',
      table=[['Ay','Reklam','Yeni','Aktif','Brüt gelir','Diğer gider*','Sonuç']]+[[str(r['month']),usd(r['ads']),f"{r['new']:.1f}",f"{r['active']:.1f}",usd(r['gross']),usd(r['technical']+r['fees_refunds']+r['support']+r['fixed']),usd(r['result'])] for r in BASE],
      widths=[45,115,80,95,130,170,229],
      bullets=[f"6. ay aylık gelir: {usd(BASE[-1]['gross'])} USD. Altı ay toplam sonuç: {usd(BASE[-1]['cumulative'])} USD.",f"Modelde en yüksek birikimli açık: {usd(-min(r['cumulative'] for r in BASE))} USD. Başlangıç geliştirme gideri ve ek nakit tamponu buna dahil değil."],
      note='*Teknik tüketim, destek karşılığı, tahsilat/iade karşılığı ve ortak altyapı. Tabloda yuvarlama yapılır; hesap dosyası tam hassasiyetlidir. MRR burada model varsayımındaki aylık abonelik geliriyle aynı kabul edildi.')
slide('Gelir sonucu en çok edinme ve tutunmaya bağlı','Aynı reklam bütçeleri ve birim fiyatlarla üç farklı varsayım.',
      table=[['Senaryo','CAC / kayıp / organik','6. ay aktif','6. ay gelir','6 ay toplam sonuç']]+[[k,('80 USD / %12 / 2' if k=='Temkinli' else '50 USD / %8 / 5' if k=='Çalışma senaryosu' else '35 USD / %5 / 8'),f"{v[-1]['active']:.1f}",usd(v[-1]['gross']),usd(v[-1]['cumulative'])] for k,v in SCENARIOS.items()],
      widths=[181,241,115,137,190],
      bullets=['Reklamı izlenme veya indirme ucuz diye büyütme. İlk yararlı işe, ödemeye ve ikinci ay devam eden müşteriye kadar ölç.','Kârlılık tarihi varsayımlara duyarlı. İlk tabloda kullanılan sabit CPI ve dönüşüm oranları bu ürün için doğrulanmış kabul edilmemeli.','Yıllık peşin ödeme nakit girişini hızlandırabilir; henüz sunulmamış ayların gelirini ve servis yükümlülüğünü ortadan kaldırmaz.'])
slide('İlk reklam ve satış deneyi','Bir sektörde üç farklı mesajı, aynı ürün deneyimine bağlayarak denemek.',
      table=[['Yaratıcı fikir','20-30 saniyelik anlatım','Ölçülecek sonuç'],['Kafe: içerik hazırlama','Piko sipariş yoğunluğunda yardım teklif eder → gerçek panelde haftalık plan → düzenlenebilir gönderi.','İlk içeriği kaydetme, bağlantı kurma, ücretli devam.'],['Emlak: ilanı anlatma','İlan bağlantısı → sahne/metin taslağı → kullanıcının düzeltmesi → izinli görsellerle video.','Üretim maliyeti, düzeltme sayısı, çıktı indirme.'],['Çalışan: haftayı düzenleme','Dağınık notlar → öncelikli işler → takvim önizlemesi → onay sonrası kayıt.','İlk plan, ikinci haftada kullanım, takvim doğruluğu.']],widths=[180,444,240],
      note='Canlı özellik çalışmadan gerçek sonuç gibi reklamda gösterilmemeli. İlk reklamlarda müşteri kazancı veya zaman tasarrufu sayısı ancak ölçülmüşse kullanılmalı. Reklam bütçesi onayı ve platform hesabı henüz yok; reklam yayımlanmadı.')
slide('Yerel uygulamada bugün ne çalışıyor?','Bu durum kaynak dosyaları, API testleri ve gerçek yerel tarayıcı akışıyla kontrol edildi.',
      table=[['Alan','Mevcut kanıt','Kalan iş'],['Hesap ve kullanım','Alan ayrımı; ortak puan; atomik rezervasyon; kullanım ekranı.','Üye daveti, e-posta doğrulama; ödeme alma ve webhooks.'],['Sohbet ve hafıza','Kalıcı konuşma, arşiv/dışa aktarım; seçerek hafızaya kayıt.','PDF/DOCX, belge arama; gerçek kullanıcı kalite ölçümü.'],['Google işlemleri','Onaylı Gmail taslağı ve takvim kaydı; OAuth/yenileme testleri.','Canlı Google hesabında kabul; diğer araçlar; takvim eşitleme.'],['Görev ve arayüz','Kuyruk/iptal; masaüstü ve 390 px mobil; gerçek HTTP test akışı.','Canlı model, medya/yayın, mobil mağaza paketi, cihazda ses.']],widths=[187,336,341],
      note='85 test geçti. Gerçek uygulama kuyruğu yerel test modeliyle sınandı; Google çağrıları test yanıtlarıyla doğrulandı. Canlı model, Google ve ödeme denenmedi. Deneme hakkı operatörce açılır; kayıt olmak otomatik kullanım veya ödeme başlatmaz.')
slide('Katalogdaki rol, tamamlanmış hizmet demek değil','Yerel prototipte 14 rol tanımı var; aşağıdaki işlerin canlı entegrasyonu ayrıca tamamlanmalı.',
      table=[['Hizmet','Uçtan uca tamamlanma koşulu'],['Sosyal medya + SEO','Gerçek marka kiti, medya, takvim, CMS/sosyal hesap bağlantısı, onay ve yayın sonucu.'],['E-posta + ajanda + telefon','Gerçek yetkilendirme, token yenileme, taslak/gönderim ayrımı, takvim çakışması, telefon uçları.'],['Satış + destek + işe alım','Yetkili veri kaynakları, CRM/kutu bağlantısı, hedef doğru kişi, onaylı iletişim ve takip durdurma.'],['Analiz + belge + site','Dosya okuma, hesap doğrulama, indirilebilir belge, site önizleme/yayın/geri alma.'],['Ajans ve kişisel hizmet','Müşteri/üye sınırları, konuşma hafızası, bildirim, güvenilir düzenli işler ve mobil kullanım.']],widths=[252,612],
      note='Kapsam bu tabloyla korunuyor. Sadece üç işin pilotta öne alınması, diğer istenen işlevlerin tamamlandığı veya kapsamdan çıkarıldığı anlamına gelmez.')
slide('Uygulama sırası ve kabul kapıları','Takvim bir tahmindir; dış platform onayları ve gerçek görev sonuçları süreyi değiştirebilir.',
      table=[['Aşama','Yapılacak iş','Çıkış kanıtı'],['1 · Ürün temeli','Hesap/roller, konuşmalar, hafıza, kuyruk, kullanım defteri, ödeme yaşam döngüsü.','Müşteri ayrımı; ödeme tekrarları; bütçe eşzamanlılık testleri.'],['2 · İlk gerçek işler','Araştırma, içerik düzenleme, Google taslak ve takvim, onay ekranı.','Gerçek pilot hesabında kaynak ve kayıt kimliğiyle doğru teslim.'],['3 · Medya ve yayın','Görsel/video; sosyal platformlar; CMS; düzenli iş; ajans onayı.','Planlı gönderi bir kez yayımlanır; kullanıcı durdurabilir.'],['4 · Tam hizmet kataloğu','Satış, destek, işe alım, analiz, belge, site, telefon ve mobil paket.','Her hizmet için canlı kabul senaryosu, bütçe ve hata geri kazanımı.'],['5 · Yayın ve büyüme','Sunucu, yedek/geri yükleme, izleme, destek akışı; kontrollü müşteri alımı.','Canlı uçtan uca satın alma → bağlantı → görev → iptal akışı.']],widths=[147,409,308],
      note='Tek geliştirici için tüm kapsamı birkaç günde üretime hazır kabul etmek gerçekçi değil. İlk gerçek işler 2-4 haftalık planlama aralığında denenebilir; tam kapsamın süresi pilot ölçümünden sonra yeniden hesaplanmalı.')
slide('Kendi sunucuna dağıtım planı','Uygulama ve müşteri verisi kendi sunucunda tutulabilir; bulut modeline gönderilen içerik ayrıca dışarı çıkar.',
      bullets=['Alan adı ve HTTPS; uygulama/API; veritabanı; iş kuyruğu; dosya deposu. Geliştirme sunucusu doğrudan internete açılmamalı.','İlk yerel yapı SQLite ve tek işçi süreci kullanıyor. Çoklu sunucu/işçi ve artan yük için PostgreSQL ile dayanıklı kuyruk geçişi planlanmalı.','Gizli anahtarlar sunucunun gizli ayarlarında; yedekte şifreleme anahtarı ayrı korunmalı. Yedek almak kadar geri yüklemeyi denemek de gerekli.','Hermes kullanılacak alanlar için ayrı konteyner ve disk, dar ağ/araç yetkisi; sıradan sohbet için sürekli açık tarayıcı bilgisayarı zorunlu değil.','Ödeme, Google ve sosyal platformların üretim ayarları; hata takibi; kullanıcı verisi silme ve bağlantı iptali akışları.'],
      note='Sunucu erişimi, DNS, model bağlantısı ve üretim ödeme hesabı henüz sağlanmadı. Yerel uygulama hazırlanması, canlı yayın yapıldığı anlamına gelmez.')
slide('Yayına hazır demek için kalan doğrulamalar','Hiçbir kapı, yalnızca arayüzün görünmesiyle tamamlanmış sayılmıyor.',
      table=[['Kapı','Gerekli gerçek kanıt'],['Model ve ajan','20 görev karşılaştırması; gerçek maliyet; kaynak doğruluğu; araç izinleri.'],['Müşteri güvenliği','Alan/üye ayrımı; OAuth geri dönüşü; token yenileme/iptal; onaylı işlem sürümü.'],['Ticari yaşam döngüsü','Ödeme, yenileme, iade, iptal, paket değişimi ve limit aşımında doğru davranış.'],['Entegrasyon ve mobil','Seçilen tüm hizmetlerde gerçek kaynak kaydı; mağaza paketi/izinler ve cihazda ses.'],['İşletim','Yedekten geri yükleme; kesintiden sonra doğru devam; hata uyarısı; destek sorumlusu.']],widths=[220,644],
      note='Şu anki sonuç: kaynaklı rekabet araştırması + yerel uygulama temeli + kapsam ve kabul planı. Tam kapsamlı hizmetin canlı ve doğrulanmış biçimde yayını henüz tamamlanmadı.')

def paragraph(text,width,size=14,color=INK,bold=False,leading=None):
    style=ParagraphStyle('p',fontName='Bold' if bold else 'Body',fontSize=size,leading=leading or size*1.42,textColor=colors.HexColor(color),spaceAfter=0)
    p=Paragraph(text,style);_,h=p.wrap(width,10000);return p,h
def drawp(c,text,x,y,width,size=14,color=INK,bold=False):
    p,h=paragraph(text,width,size,color,bold);p.drawOn(c,x,y-h);return y-h
def textsafe(s):return html.escape(str(s)).replace('\n','<br/>')

for start in range(0,len(S),9):
    keys=list(S)[start:start+9]
    slide('Kaynaklar '+str(start//9+1),'Bağlantılar tıklanabilir. Ürün ve fiyat sayfaları 4 Ekim 2026 itibarıyla incelendi.',
          bullets=[f'<b>{textsafe(S[k]["title"])}</b><br/><link href="{html.escape(S[k]["url"],quote=True)}" color="{BLUE}">{textsafe(S[k]["url"])}</link>' for k in keys],kind='sources')

path=OUT/'Isdas-Rakip-Incelemesi-ve-Uygulama-Plani.pdf'
c=canvas.Canvas(str(path),pagesize=(W,H));c.setTitle('İşdaş - Rakip incelemesi ve uygulama planı');c.setAuthor('İşdaş ürün araştırması')
layout=[]
for index,s in enumerate(SLIDES,1):
    c.setFillColor(colors.HexColor('#FFFFFF'));c.rect(0,0,W,H,fill=1,stroke=0)
    c.setFillColor(colors.HexColor(BLUE));c.rect(0,H-9,W,9,fill=1,stroke=0)
    if s['kind']=='cover':
        c.setFillColor(colors.HexColor(PALE));c.rect(600,9,360,H-18,fill=1,stroke=0)
        y=drawp(c,'işdaş.',48,530,520,28,BLUE,True)
        y=drawp(c,textsafe(s['title']),48,440,570,42,INK,True)
        y=drawp(c,textsafe(s['lead']),48,y-24,540,19,MUTED)
        for b in s['bullets']:y=drawp(c,textsafe(b),48,y-24,540,13,MUTED)
        c.drawImage(str(ROOT/'frontend/public/mascot.png'),596,160,350,350,mask='auto',preserveAspectRatio=True)
    else:
        y=drawp(c,textsafe(s['title']),48,H-39,864,27,INK,True)
        if s['lead']:y=drawp(c,textsafe(s['lead']),48,y-12,864,14,MUTED)
        y-=22
        if s['kind']=='architecture':
            blocks=[('Kullanıcı','Konuşma · yazı · dosya · sonuç'),('Ürün sunucusu','Kimlik · alan · hafıza · onay · bütçe'),('İş kuyruğu','Durum · tekrar deneme · zamanlama'),('İş yürütme','Grok API + araçlar / izole Hermes'),('Teslim','Kaynak · dosya · gerçek işlem kaydı')]
            for j,(name,detail) in enumerate(blocks):
                boxy=y-54;c.setFillColor(colors.HexColor(PALE));c.roundRect(118,boxy,724,53,8,fill=1,stroke=0)
                drawp(c,name,138,boxy+37,185,14,BLUE,True);drawp(c,detail,338,boxy+37,483,14)
                if j<len(blocks)-1:
                    c.setStrokeColor(colors.HexColor(BLUE));c.line(480,boxy,480,boxy-13);c.line(480,boxy-13,476,boxy-8);c.line(480,boxy-13,484,boxy-8)
                y=boxy-14
        if s['table']:
            contents=[]
            for rowi,row in enumerate(s['table']):
                contents.append([paragraph(textsafe(v),w-20,11.8 if rowi else 12.2,'#FFFFFF' if rowi==0 else INK,rowi==0,16)[0] for v,w in zip(row,s['widths'])])
            t=Table(contents,colWidths=s['widths'],hAlign='LEFT')
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor(INK)),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor(PALE),colors.white]),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10),('LINEBELOW',(0,0),(-1,-1),.5,colors.HexColor(LINE))]))
            tw,th=t.wrap(864,10000);t.drawOn(c,48,y-th);y-=th+11
        if s['image']:
            # Show the top of the actual desktop screenshot at readable scale.
            from PIL import Image
            shot=Image.open(ROOT/s['image']);crop=shot.crop((0,0,shot.width,min(850,shot.height)))
            temp=ROOT/'tmp/pdfs';temp.mkdir(parents=True,exist_ok=True);impath=temp/'workspace-crop.png';crop.save(impath)
            c.drawImage(str(impath),160,y-370,width=640,height=370,preserveAspectRatio=True,anchor='c');y-=382
        for bullet in s['bullets']:
            if s['kind']=='sources':
                y=drawp(c,bullet,48,y,864,10.5,MUTED)-9
            else:
                c.setFillColor(colors.HexColor(BLUE));c.circle(53,y-8,2.5,fill=1,stroke=0)
                y=drawp(c,textsafe(bullet),68,y,844,13.8)-15
    bottom=54
    refs=' · '.join(f'<link href="{html.escape(S[k]["url"],quote=True)}" color="{BLUE}">{textsafe(S[k]["title"])}</link>' for k in s['refs'])
    footertext=(textsafe(s['note'])+('<br/>' if s['note'] and refs else '')+refs)
    if footertext:
        p,ph=paragraph(footertext,864,9.1,MUTED,leading=12.5);p.drawOn(c,48,bottom);footer_top=bottom+ph
    else:footer_top=bottom
    if y<footer_top+14:raise ValueError(f'Layout overflow slide {index}: body={y:.1f}, footer={footer_top:.1f} {s["title"]}')
    c.setStrokeColor(colors.HexColor(LINE));c.line(48,38,912,38)
    c.setFont('Body',8.5);c.setFillColor(colors.HexColor(MUTED));c.drawString(48,23,'İşdaş · 4 Ekim 2026 · Araştırma ve uygulama planı');c.drawRightString(912,23,f'{index} / {len(SLIDES)}')
    layout.append({'slide':index,'title':s['title'],'body_bottom':round(y,1),'footer_top':round(footer_top,1)})
    c.showPage()
c.save()
reader=PdfReader(path)
assert len(reader.pages)==len(SLIDES)
assert all(len(p.extract_text())>60 for p in reader.pages)
(OUT/'report-layout-check.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2),encoding='utf-8')
(ROOT/'research/decision-report-content.json').write_text(json.dumps({'sources':S,'slides':SLIDES,'current_state':'local prototype, not fully deployed service'},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pdf':str(path),'pages':len(SLIDES),'bytes':path.stat().st_size,'base_scenario_month6':BASE[-1]},ensure_ascii=False))
