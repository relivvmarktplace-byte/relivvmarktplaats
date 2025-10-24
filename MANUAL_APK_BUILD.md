# 🔧 Manuel APK Oluşturma Rehberi

PWABuilder sorun veriyorsa, işte alternatif yöntemler:

---

## ✅ Yöntem 1: Android Studio ile Build (En Garantili)

### Gereksinimler:
- Android Studio (en son versiyon)
- JDK 17
- Android SDK

### Adımlar:

#### 1. Projeyi İndirin
```bash
# Bu repoyu yerel makinenize klonlayın veya ZIP olarak indirin
# Klasör: /app/twa/
```

#### 2. Android Studio'da Açın
1. Android Studio'yu başlatın
2. "Open an Existing Project"
3. `/app/twa/` klasörünü seçin

#### 3. Gradle Sync
- Android Studio otomatik olarak Gradle sync yapacak
- Eksik SDK'lar varsa, indirme önerisi gösterecek

#### 4. Keystore Yapılandır
`app/build.gradle` dosyasına ekleyin:

```gradle
android {
    signingConfigs {
        release {
            storeFile file('/app/twa-key.keystore')
            storePassword 'relivv2025'
            keyAlias 'relivv'
            keyPassword 'relivv2025'
        }
    }
    
    buildTypes {
        release {
            signingConfig signingConfigs.release
            minifyEnabled true
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
        }
    }
}
```

#### 5. Build AAB
```
Build → Generate Signed Bundle / APK → Android App Bundle → Next
```

Signing key seçin:
- Key store path: `/app/twa-key.keystore`
- Password: `relivv2025`
- Key alias: `relivv`
- Key password: `relivv2025`

Build destination: `release`

#### 6. AAB Dosyası
```
app/build/outputs/bundle/release/app-release.aab
```

---

## 🚀 Yöntem 2: Bubblewrap CLI (Komut Satırı)

### Kurulum:
```bash
npm install -g @bubblewrap/cli
```

### Kullanım:
```bash
cd /app

# İlk kez init (interaktif)
bubblewrap init --manifest https://relivv-marketplace-1.preview.emergentagent.com/manifest.json

# Sorulara cevaplar:
# - JDK install? Y
# - Package ID: nl.relivv.marktplaats
# - App name: Relivv Marktplaats
# - Launcher name: Relivv
# - Keystore password: relivv2025
# - Key password: relivv2025

# Build
bubblewrap build

# Output: app-release-signed.apk
```

**NOT:** Bu yöntem 15-20 dakika sürebilir (JDK ve Android SDK indirme).

---

## 📱 Yöntem 3: PWABuilder Desktop App

### İndirin:
https://github.com/pwa-builder/PWABuilder/releases

### Kullanım:
1. PWABuilder Desktop uygulamasını başlatın
2. URL: `https://relivv-marketplace-1.preview.emergentagent.com`
3. Analyze → Package
4. Android seçin
5. Settings:
   - Package ID: `nl.relivv.marktplaats`
   - Name: `Relivv Marktplaats`
6. Keystore yükleyin (indirilecek link aşağıda)
7. Generate

---

## 🔑 Keystore Bilgileri (Tüm Yöntemler İçin)

### İndirme:
```
https://relivv-marketplace-1.preview.emergentagent.com/relivv-keystore.jks
```

### Bilgiler:
```
Keystore Password: relivv2025
Key Alias: relivv
Key Password: relivv2025

SHA256 Fingerprint:
83:71:81:8B:1E:F3:16:B8:36:45:D9:25:5B:5E:24:2F:BE:AF:AD:4E:EE:0F:46:88:13:E3:A7:B1:CB:E0:17:09
```

---

## 🐛 PWABuilder Hata Giderme

### "Manifest parse error"
✅ **Çözüldü!** Manifest güncelledim:
```
https://relivv-marketplace-1.preview.emergentagent.com/manifest.json
```

Test edin:
```bash
curl https://relivv-marketplace-1.preview.emergentagent.com/manifest.json
```

### "Icons not found"
Icon'lar mevcut:
- https://relivv-marketplace-1.preview.emergentagant.com/logo192.png
- https://relivv-marketplace-1.preview.emergentagent.com/logo512.png

### "Service Worker error"
Service Worker mevcut:
```
https://relivv-marketplace-1.preview.emergentagent.com/sw.js
```

---

## 📋 Build Sonrası Kontrol Listesi

### 1. APK/AAB Test Edin
```bash
# APK yükle (test cihazı)
adb install app-release.apk

# Uygulama çalışıyor mu?
# Deep link test: relivv.nl açılıyor mu?
```

### 2. Digital Asset Links Doğrulayın
```bash
curl https://relivv-marketplace-1.preview.emergentagent.com/.well-known/assetlinks.json
```

Fingerprint eşleşiyor mu?

### 3. Manifest Doğrulayın
Chrome DevTools'da:
```
Application → Manifest
```

Tüm alanlar dolu mu?

---

## 🎯 Hızlı Başvuru

### PWABuilder Yeniden Deneyin (Manifest Düzeltildi!)
1. https://www.pwabuilder.com/
2. URL: `https://relivv-marketplace-1.preview.emergentagent.com`
3. Analyze
4. Package for Stores → Android
5. Options:
   ```
   Package ID: nl.relivv.marktplaats
   Name: Relivv Marktplaats
   Launcher: Relivv
   Version: 1.0.0
   Version code: 1
   Host: relivv-marketplace-1.preview.emergentagent.com
   Start URL: /
   Display: standalone
   Theme: #2563eb
   Background: #ffffff
   ```
6. Signing key: Upload keystore
7. Generate!

---

## 💡 En Hızlı Yöntem (Önerim)

**PWABuilder'ı şimdi tekrar deneyin!**

Manifest hatası düzeltildi. Artık çalışması gerekiyor. Eğer yine hata alırsanız:

1. **Android Studio** kullanın (en garantili)
2. **Bubblewrap CLI** kullanın (otomatik ama yavaş)
3. Bana hata mesajını gönderin, daha detaylı yardım edeyim

---

**Son Güncelleme:** 22 Ocak 2025
**Manifest Düzeltme:** ✅ Tamamlandı
