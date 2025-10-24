# Relivv Marktplaats - TWA (Trusted Web Activity) Android App

## 📱 Genel Bakış

Bu, Relivv Marktplaats'ın Google Play Store için TWA (Trusted Web Activity) uygulamasıdır.

**Uygulama Bilgileri:**
- **Uygulama Adı:** Relivv Marktplaats
- **Package Name:** nl.relivv.marktplaats
- **Developer ID:** 7474874603789179697
- **Website:** https://relivv.nl
- **Backend:** https://relivv-marketplace-1.preview.emergentagent.com

## 🚀 Build Süreci

### Gereksinimler
- Android Studio Arctic Fox veya daha yeni
- JDK 11 veya daha yeni
- Android SDK (API 34)

### 1. Proje Kurulumu

```bash
# Android Studio'da projeyi açın
# File -> Open -> /app/twa dizinini seçin
```

### 2. Keystore Oluşturma

Play Store'a yüklemek için bir signing key oluşturmalısınız:

```bash
keytool -genkey -v -keystore relivv-release-key.keystore -alias relivv -keyalg RSA -keysize 2048 -validity 10000
```

Sorulara cevap verin:
- Password: [güvenli bir şifre seçin]
- Name: Relivv B.V.
- Organization: Relivv
- City: Amsterdam
- Country: NL

### 3. SHA256 Fingerprint Alma

```bash
keytool -list -v -keystore relivv-release-key.keystore -alias relivv
```

SHA256 fingerprint'i kopyalayın ve `assetlinks.json` dosyasındaki PLACEHOLDER yerine yapıştırın.

### 4. Digital Asset Links Yükleme

`assetlinks.json` dosyasını web sitenizin `.well-known` dizinine yükleyin:

**Frontend'e eklemek için:**
```bash
# Frontend'de public/.well-known klasörü oluşturun
mkdir -p /app/frontend/public/.well-known

# assetlinks.json'u kopyalayın
cp /app/twa/assetlinks.json /app/frontend/public/.well-known/assetlinks.json
```

URL: `https://relivv.nl/.well-known/assetlinks.json` üzerinden erişilebilir olmalı.

### 5. Build APK/AAB

#### Debug Build (Test için)
```bash
cd /app/twa
./gradlew assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
```

#### Release Build (Play Store için)
```bash
cd /app/twa

# AAB (Android App Bundle) - Play Store önerilir
./gradlew bundleRelease

# APK (alternatif)
./gradlew assembleRelease
```

Output:
- AAB: `app/build/outputs/bundle/release/app-release.aab`
- APK: `app/build/outputs/apk/release/app-release.apk`

### 6. Signing Configuration

`app/build.gradle` dosyasına signing config ekleyin:

```gradle
android {
    signingConfigs {
        release {
            storeFile file('path/to/relivv-release-key.keystore')
            storePassword 'YOUR_PASSWORD'
            keyAlias 'relivv'
            keyPassword 'YOUR_PASSWORD'
        }
    }
    
    buildTypes {
        release {
            signingConfig signingConfigs.release
            // ...
        }
    }
}
```

## 📦 Google Play Store Yükleme

### 1. Play Console'a Giriş
https://play.google.com/console adresinden giriş yapın
Developer ID: 7474874603789179697

### 2. Yeni Uygulama Oluştur
- "Create app" butonuna tıklayın
- App name: **Relivv Marktplaats**
- Default language: **Dutch (Netherlands)**
- App type: **App**
- Category: **Shopping**
- Free or paid: **Free**

### 3. Store Listing

**App details:**
- Short description (80 chars):
  ```
  Tweedehands marktplaats voor Nederland. Veilig kopen en verkopen.
  ```

- Full description (4000 chars):
  ```
  Welkom bij Relivv Marktplaats - Nederlands betrouwbare platform voor tweedehands artikelen!

  🛍️ VEILIG KOPEN EN VERKOPEN
  • Beveiligde betalingen via Stripe
  • Escrow systeem voor bescherming
  • Geverifieerde verkopers
  • Beoordelingen en reviews

  📱 EENVOUDIG TE GEBRUIKEN
  • Snelle productlijsten met foto's
  • Categorieën voor makkelijk zoeken
  • Directe messaging tussen kopers en verkopers
  • Locatie-gebaseerd zoeken

  💰 FUNCTIES
  • Producten toevoegen in enkele seconden
  • Meerdere foto's per product
  • Realtime notificaties
  • Bestelgeschiedenis en facturen
  • Favorieten lijst
  • Verkoperspaneel

  🌍 LOKAAL & DUURZAAM
  • Vind artikelen bij u in de buurt
  • Draag bij aan duurzaamheid
  • Steun lokale gemeenschappen

  Download nu en begin met kopen of verkopen!
  ```

**Graphics:**
- App icon: 512x512 PNG (wordt gegeven)
- Feature graphic: 1024x500 PNG (wordt gegeven)
- Screenshots: Min 2, max 8 (telefoon)
  - 16:9 aspect ratio
  - PNG or JPEG
  - Min: 320px, Max: 3840px

**Contact details:**
- Email: support@relivv.nl
- Website: https://relivv.nl
- Privacy policy: https://relivv.nl/privacy-policy

### 4. App Content

**Privacy policy:**
URL: https://relivv.nl/privacy-policy

**App access:**
- [ ] All functionality is available without restrictions

**Ads:**
- [x] No, this app does not contain ads

**Content ratings:**
Fill out the questionnaire (likely: Everyone/PEGI 3)

**Target audience:**
- Target age: 18+

**Data safety:**
- Data collection: Yes (account, payment, location)
- Data sharing: Payment processors (Stripe)
- Security: Encryption in transit and at rest

### 5. Production Track Upload

1. "Production" sekmesine gidin
2. "Create new release" tıklayın
3. AAB dosyasını yükleyin
4. Release notes ekleyin (Dutch & English):
   ```
   Version 1.0.0
   - Eerste release
   - Veilige betalingen
   - Product lijsten
   - Messaging systeem
   ```

### 6. Publish

- "Review release" tıklayın
- "Start rollout to Production" tıklayın

## ⚙️ Digital Asset Links Doğrulama

Uygulama yayınlandıktan sonra doğrulama:

```bash
# Web'den erişimi test et
curl https://relivv.nl/.well-known/assetlinks.json

# Android Studio'dan test et
# Tools -> App Links Assistant -> Test App Links
```

## 🎨 Store Listing Görselleri

### Gerekli Görseller:
1. **App Icon** - 512x512 PNG
2. **Feature Graphic** - 1024x500 PNG
3. **Screenshots** - Min 2 adet (telefon)
   - 1080x1920 (9:16) veya 1080x2340 (9:19.5)

### Ekran Görüntüsü Önerileri:
1. Ana sayfa (ürün listesi)
2. Ürün detay sayfası
3. Checkout/ödeme
4. Mesajlaşma
5. Profil/Dashboard

## 📝 Önemli Notlar

1. **Domain Verification:** assetlinks.json dosyası HTTPS üzerinden erişilebilir olmalı
2. **App Signing:** Play App Signing kullanmanız önerilir
3. **Testing:** Internal testing track kullanarak önce test edin
4. **Updates:** Version code'u her güncellemede artırın
5. **Production URL:** Production'a geçerken AndroidManifest.xml'deki URL'yi güncelleyin

## 🔧 Troubleshooting

**Problem: "Digital Asset Links verification failed"**
- assetlinks.json doğru konumda mı? (/.well-known/assetlinks.json)
- SHA256 fingerprint doğru mu?
- Domain HTTPS mi?

**Problem: "App not verified"**
- 48 saat bekleyin (Google verification süreci)
- Play Console'dan "App integrity" kontrol edin

**Problem: "Can't upload AAB"**
- Version code artırdınız mı?
- Signing config doğru mu?

## 📞 Destek

İletişim: support@relivv.nl

---

**Son Güncelleme:** 22 Ocak 2025
