# 🚀 Relivv Marktplaats - Production Deployment Rehberi

relivv.nl domain'i ile canlıya alma adım adım rehber.

---

## 📋 GENEL PLAN

**Mimari:**
```
Frontend (React) → Vercel → relivv.nl
Backend (FastAPI) → Railway → api.relivv.nl
Database → MongoDB Atlas
```

**Neden Bu Mimari?**
- Frontend: Vercel (React için en iyi, otomatik CDN, ücretsiz)
- Backend: Railway (kolay setup, auto-scaling, €25/ay)
- Database: MongoDB Atlas (managed, backup, €57/ay veya free başlangıç)

---

## ADIM 1: MONGODB ATLAS KURULUMU (15 dakika)

### 1.1. Hesap Oluştur
1. https://www.mongodb.com/cloud/atlas/register adresine git
2. Email ile kayıt ol veya Google ile giriş yap
3. Email'i doğrula

### 1.2. Cluster Oluştur
1. "Build a Database" tıkla
2. **FREE** tier seç (M0 Sandbox)
   - Cloud Provider: **AWS**
   - Region: **Frankfurt (eu-central-1)** (Hollanda'ya en yakın)
3. Cluster Name: **relivv-production**
4. "Create" tıkla (2-3 dakika sürer)

### 1.3. Güvenlik Ayarları
1. **Database Access** → "Add New Database User"
   - Authentication Method: Password
   - Username: `relivv_admin`
   - Password: Güçlü bir şifre oluştur (kaydet!)
   - Database User Privileges: **Read and write to any database**
   - "Add User"

2. **Network Access** → "Add IP Address"
   - "Allow Access from Anywhere" (0.0.0.0/0)
   - Confirm

### 1.4. Connection String Al
1. Cluster'a "Connect" tıkla
2. "Drivers" seç
3. Python driver, version 3.12+
4. Connection string'i kopyala:
   ```
   mongodb+srv://relivv_admin:<password>@relivv-production.xxxxx.mongodb.net/?retryWrites=true&w=majority
   ```
5. `<password>` yerine şifrenizi yazın
6. Database name ekleyin (sonunda):
   ```
   mongodb+srv://relivv_admin:YOURPASSWORD@relivv-production.xxxxx.mongodb.net/relivv_db?retryWrites=true&w=majority
   ```

**✅ KAYDET:** Bu connection string'i bir yere not alın!

---

## ADIM 2: GITHUB REPOSITORY HAZIRLIK (10 dakika)

### 2.1. GitHub Hesabı
- Varsa: Login ol
- Yoksa: https://github.com/signup

### 2.2. Repository Oluştur
1. "New repository" tıkla
2. Repository name: **relivv-marketplace**
3. Private veya Public (tercihe göre)
4. "Create repository"

### 2.3. Kodu Yükle

**ÖNEMLİ:** Mevcut kodu GitHub'a yüklemeliyiz.

**Seçenek A: Manuel (Basit)**
1. `/app` klasöründeki tüm dosyaları indir (ZIP olarak)
2. GitHub'da "Upload files" ile yükle

**Seçenek B: Git ile (Doğru yöntem)**
```bash
cd /app
git init
git add .
git commit -m "Initial commit - Relivv Marktplaats"
git branch -M main
git remote add origin https://github.com/YOURUSERNAME/relivv-marketplace.git
git push -u origin main
```

**NOT:** `.git` ve `.emergent` klasörlerini silmeyin!

### 2.4. .gitignore Kontrol
`.gitignore` dosyası olmalı ve şunları içermeli:
```
.env
.env.local
.env.production
node_modules/
__pycache__/
*.pyc
.DS_Store
```

---

## ADIM 3: BACKEND DEPLOYMENT - RAILWAY (20 dakika)

### 3.1. Railway Hesabı
1. https://railway.app/ adresine git
2. "Login" → **GitHub ile giriş yap**
3. Railway'e GitHub repository erişimi ver

### 3.2. Yeni Proje Oluştur
1. "New Project" tıkla
2. "Deploy from GitHub repo" seç
3. **relivv-marketplace** repository'sini seç
4. "Deploy Now" tıkla

### 3.3. Backend Service Ayarları
1. Deployment başladı ama **durduracağız**, önce ayar yapacağız
2. Sağ üstteki ⚙️ "Settings" tıkla

**Root Directory:**
```
backend
```

**Build Command:** (boş bırak, requirements.txt otomatik)

**Start Command:**
```
uvicorn server:app --host 0.0.0.0 --port $PORT
```

### 3.4. Environment Variables
1. "Variables" sekmesine git
2. Şu değişkenleri ekle:

```
MONGO_URL=mongodb+srv://relivv_admin:YOURPASSWORD@relivv-production.xxxxx.mongodb.net/relivv_db?retryWrites=true&w=majority

STRIPE_API_KEY=sk_test_XXXXXXXX
STRIPE_WEBHOOK_SECRET=whsec_XXXXXXXX

SENDGRID_API_KEY=SG.XXXXXXXXX

GOOGLE_MAPS_API_KEY=AIzaSyXXXXXXXXXX

JWT_SECRET=GENERATE_A_RANDOM_SECRET_HERE

FRONTEND_URL=https://relivv.nl
```

**NOT:** 
- MONGO_URL: Adım 1'deki connection string
- Diğer API keys: Mevcut .env dosyanızdan alın
- JWT_SECRET: `openssl rand -hex 32` ile oluştur

### 3.5. Deploy
1. "Deploy" butonuna bas
2. 2-3 dakika bekle
3. ✅ "Success" görmelisiniz

### 3.6. Domain Bağla (Railway'de)
1. "Settings" → "Domains"
2. "Custom Domain" tıkla
3. **api.relivv.nl** yaz
4. CNAME record bilgisi verecek:
   ```
   CNAME: api
   Value: xxxxx.up.railway.app
   ```
5. **Bu bilgiyi not al!** (Adım 5'te kullanacağız)

**✅ Backend URL:** `https://xxxxx.up.railway.app` (geçici)
**✅ Final URL:** `https://api.relivv.nl` (domain bağlandıktan sonra)

---

## ADIM 4: FRONTEND DEPLOYMENT - VERCEL (15 dakika)

### 4.1. Vercel Hesabı
1. https://vercel.com/signup adresine git
2. **GitHub ile giriş yap**

### 4.2. Yeni Proje
1. "Add New" → "Project"
2. **relivv-marketplace** repository'sini import et
3. "Import" tıkla

### 4.3. Proje Ayarları
**Framework Preset:** Create React App

**Root Directory:** 
```
frontend
```

**Build Command:**
```
npm run build
```

**Output Directory:**
```
build
```

**Install Command:** (otomatik)

### 4.4. Environment Variables
"Environment Variables" bölümüne ekle:

```
REACT_APP_BACKEND_URL=https://api.relivv.nl
REACT_APP_GOOGLE_MAPS_KEY=AIzaSyXXXXXXXXXX
```

**NOT:** Backend URL'i Railway'den aldığınız geçici URL yerine `api.relivv.nl` yazın (domain bağladıktan sonra çalışacak)

### 4.5. Deploy
1. "Deploy" tıkla
2. 3-5 dakika bekle
3. ✅ "Your project is ready" göreceksiniz

**✅ Geçici URL:** `https://relivv-marketplace-xxxxx.vercel.app`

### 4.6. Domain Bağla (Vercel'de)
1. "Settings" → "Domains"
2. **relivv.nl** ekle
3. **www.relivv.nl** ekle
4. DNS kayıtlarını gösterecek:
   ```
   A Record:
   Name: @
   Value: 76.76.21.21

   CNAME:
   Name: www
   Value: cname.vercel-dns.com
   ```
5. **Bu bilgileri not al!** (Adım 5'te kullanacağız)

---

## ADIM 5: VERSIO DNS AYARLARI (10 dakika)

### 5.1. Versio Panel
1. https://www.versio.nl/customer/login.php adresine git
2. Giriş yap

### 5.2. DNS Yönetimi
1. "Mijn diensten" → "Domeinen"
2. **relivv.nl** seç
3. "DNS Beheer" veya "DNS Management" tıkla

### 5.3. DNS Kayıtları Ekle

**Mevcut kayıtları yedekle!** (screenshot al)

**Eklenecek Kayıtlar:**

#### 1. Ana Domain (Frontend - Vercel)
```
Type: A
Name: @
Value: 76.76.21.21
TTL: 3600
```

#### 2. WWW Subdomain (Frontend - Vercel)
```
Type: CNAME
Name: www
Value: cname.vercel-dns.com
TTL: 3600
```

#### 3. API Subdomain (Backend - Railway)
```
Type: CNAME
Name: api
Value: xxxxx.up.railway.app  (Railway'den aldığınız)
TTL: 3600
```

#### 4. SSL için (opsiyonel ama önerilen)
```
Type: CAA
Name: @
Value: 0 issue "letsencrypt.org"
TTL: 3600
```

### 5.4. Kaydet ve Bekle
- Kayıtları kaydet
- **Propagation süresi:** 15 dakika - 24 saat
- Genellikle 1-2 saat içinde çalışır

---

## ADIM 6: SSL VE DOĞRULAMA (30 dakika - 2 saat)

### 6.1. DNS Propagation Kontrol
```bash
# Terminal'de test et
nslookup relivv.nl
nslookup api.relivv.nl

# Veya online tool:
# https://dnschecker.org/
```

### 6.2. SSL Sertifikaları
**Vercel:** Otomatik SSL, domain bağlandıktan 5-10 dakika sonra aktif
**Railway:** Otomatik SSL, custom domain ekledikten sonra aktif

### 6.3. Test Et
1. **Frontend:** https://relivv.nl
   - Anasayfa açılmalı
   - SSL kilidi görmeli (🔒)

2. **Backend:** https://api.relivv.nl/docs
   - Swagger docs açılmalı
   - API endpoints görmeli

3. **Full Test:** Frontend'den bir işlem yap
   - Kayıt ol
   - Giriş yap
   - Ürün ekle

---

## ADIM 7: TWA APP GÜNCELLEME

### 7.1. Manifest Güncelle
`/app/frontend/public/manifest.json`:
```json
{
  "start_url": "https://relivv.nl",
  "scope": "https://relivv.nl"
}
```

### 7.2. Asset Links Güncelle
`/app/frontend/public/.well-known/assetlinks.json`:
Domain zaten doğru, değişiklik gerekmez.

### 7.3. Yeni TWA Build
1. PWABuilder'a git: https://www.pwabuilder.com/
2. URL: **https://relivv.nl** (artık production URL!)
3. Package for Android
4. Ayarlar:
   - Package ID: `nl.relivv.marktplaats`
   - Host: `relivv.nl` (artık production!)
5. Generate

### 7.4. Play Store Güncelle
1. Play Console'a git
2. "Production" → "Create new release"
3. Yeni AAB'yi yükle
4. Release notes:
   ```
   Version 1.0.1
   - Production domain'e geçiş (relivv.nl)
   - Performans iyileştirmeleri
   - Hata düzeltmeleri
   ```
5. "Review release" → "Start rollout"

---

## ADIM 8: POST-DEPLOYMENT KONTROLLER

### 8.1. Functionality Checklist
- [ ] Homepage yükleniyor
- [ ] Kayıt olma çalışıyor
- [ ] Giriş yapma çalışıyor
- [ ] Ürün ekleme çalışıyor
- [ ] Ödeme çalışıyor (test mode)
- [ ] Email gidiyor
- [ ] Bildirimler çalışıyor

### 8.2. Performance Test
- [ ] PageSpeed Insights: https://pagespeed.web.dev/
  - Target: 90+ score
- [ ] GTmetrix: https://gtmetrix.com/
- [ ] Mobile test: Chrome DevTools

### 8.3. Security Check
- [ ] SSL aktif (🔒)
- [ ] HTTPS zorlaması
- [ ] CORS ayarları doğru
- [ ] API rate limiting var mı?

---

## ADIM 9: MONITORING KURULUMU

### 9.1. Sentry (Error Tracking)
1. https://sentry.io/signup/
2. Create new project: **Relivv Backend**
3. DSN al
4. Railway'de environment variable ekle:
   ```
   SENTRY_DSN=https://xxxxx@sentry.io/xxxxx
   ```

### 9.2. Google Analytics
1. https://analytics.google.com/
2. Create property: **Relivv Marktplaats**
3. Measurement ID al (G-XXXXXXXXXX)
4. Frontend'e ekle (index.html)

### 9.3. Uptime Monitoring
1. https://uptimerobot.com/ (free)
2. Monitor ekle:
   - Type: HTTPS
   - URL: https://relivv.nl
   - Alert: Email (down olunca)

---

## BACKUP & RECOVERY

### Database Backup (MongoDB Atlas)
- Otomatik backup aktif (M10+ tier)
- Manuel backup: Cluster → ... → "Take Snapshot"

### Code Backup
- ✅ GitHub'da zaten
- Branch oluştur her major release'de

### Rollback Stratejisi
**Frontend:** Vercel'de "Deployments" → Previous version → "Promote to Production"
**Backend:** Railway'de "Deployments" → Previous → "Redeploy"

---

## TROUBLESHOOTING

### Domain bağlanmıyor
- DNS propagation bekle (24 saat)
- nslookup ile kontrol et
- Vercel/Railway'de domain doğrulaması yap

### SSL çalışmıyor
- 10-15 dakika bekle
- DNS kayıtları doğru mu kontrol et
- Cloudflare kullanıyorsan: SSL mode "Full"

### Backend'e bağlanamıyor
- Railway logs kontrol et
- Environment variables doğru mu?
- CORS ayarları backend'de güncelle

### Database bağlanamıyor
- IP whitelist kontrol et (0.0.0.0/0)
- Connection string doğru mu?
- Username/password doğru mu?

---

## MALIYET ÖZET

**Aylık:**
- Railway (Backend): €25
- Vercel (Frontend): €0 (Free tier yeterli başlangıç için)
- MongoDB Atlas: €0 (Free tier 512MB) veya €57 (M10)
- **Toplam: €25/ay** (başlangıç)

**Yıllık:**
- Domain (Versio): €10-15/yıl
- SSL: €0 (Let's Encrypt free)

---

## SONRAKI ADIMLAR

✅ Domain & Hosting: COMPLETE
⏭️ Güvenlik (Rate limiting, Headers)
⏭️ Stripe Live Keys
⏭️ Terms of Service
⏭️ Monitoring & Analytics

---

**Bu deployment'tan sonra:**
- ✅ relivv.nl canlı!
- ✅ TWA app çalışıyor
- ✅ Production ready
- ✅ Play Store onaylanabilir

🎉 **BAŞARILAR!**
