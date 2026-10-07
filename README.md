# Isdas

Isdas, kişisel kullanıcılar ve küçük ekipler için konuşarak veya yazarak görev çalıştıran çok kiracılı yapay zekâ çalışma alanıdır. Uygulama; sohbet, hafıza, belgeler, içerik stüdyosu, ajanda, ekip/müşteri onayı, bildirimler, Google bağlantıları ve kendi sunucusunda çalıştırma akışlarını tek bir üründe birleştirir.

## Yerel çalıştırma

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Frontend geliştirme sunucusu için:

```powershell
cd frontend
npm install
npm run dev
```

Üretim dağıtımı için `deploy/` klasöründeki örnek yapılandırma ve `output/` içindeki işletim notları kullanılabilir. Gerçek API anahtarları, veritabanı dosyaları ve ortam dosyaları repoya eklenmez; `.env.example` yalnızca boş yapılandırma şablonudur.

## Durum

Bu depo yerel pilot ve kendi sunucusunda çalıştırma için hazırlanmıştır. Canlı model, ödeme, Google/SMTP, sosyal medya yayınlama, mobil uygulama paketleri ve gerçek alan adı/TLS bilgileri kurulum ortamına göre ayrıca etkinleştirilir.

## Test

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Bu depo özeldir. Üretim sırlarını GitHub’a göndermeyin; anahtar sızıntısı olursa sağlayıcı panelinden hemen iptal edip yenileyin.
