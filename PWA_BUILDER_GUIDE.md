# 🚀 Relivv Marktplaats - APK Oluşturma Rehberi

## ⚠️ Durum Güncellemesi

Android SDK kurulumu bu ortamda 15-20 dakika sürebilir. Size **PWABuilder.com** ile çok daha hızlı (5 dakika) bir yöntem öneriyorum.

---

## 📦 Yöntem 1: PWABuilder.com (ÖNERİLEN - 5 Dakika)

### Adım 1: PWABuilder'a Git
https://www.pwabuilder.com/

### Adım 2: URL Gir
```
https://relivv-marketplace-1.preview.emergentagent.com
```
"Start" butonuna tıklayın.

### Adım 3: Package For Stores
"Package For Stores" butonuna tıklayın.

### Adım 4: Android Seç
"Android" platformunu seçin.

### Adım 5: Ayarları Yapılandır

**App Settings:**
- Package ID: `nl.relivv.marktplaats`
- App name: `Relivv Marktplaats`
- Launcher name: `Relivv`
- App version: `1.0.0`
- Version code: `1`
- Host: `relivv-marketplace-1.preview.emergentagent.com`
- Start URL: `/`

**Display:**
- Display mode: `standalone`

**Theme:**
- Theme color: `#2563eb`
- Background color: `#ffffff`
- Navigation color: `#2563eb`

**Icon:**
- Icon URL: `https://relivv-marketplace-1.preview.emergentagent.com/logo512.png`
- Maskable icon: Same URL

**Advanced Settings:**
- Enable notifications: `Yes`
- Enable location delegation: `Yes`
- Fallback behavior: `Custom Tabs`

### Adım 6: Signing Key Kullan

**İKİ SEÇENEK VAR:**

#### Seçenek A: Hazır Keystore'u Kullan (Önerilen)
Ben size bir keystore oluşturdum:

**İndir:**
```
https://relivv-marketplace-1.preview.emergentagent.com/relivv-keystore.jks
```

**Bilgiler:**
- Keystore Password: `relivv2025`
- Key Alias: `relivv`
- Key Password: `relivv2025`
- SHA256 Fingerprint: `83:71:81:8B:1E:F3:16:B8:36:45:D9:25:5B:5E:24:2F:BE:AF:AD:4E:EE:0F:46:88:13:E3:A7:B1:CB:E0:17:09`

#### Seçenek B: Yeni Keystore Oluştur
PWABuilder'ın "Generate new signing key" özelliğini kullanın.

### Adım 7: Generate
"Generate" veya "Download" butonuna tıklayın.

### Adım 8: İndir
- AAB dosyası indirilecek (Google Play için)
- Signing key bilgilerini kaydedin

---

## 🔧 Yöntem 2: Bu Ortamda Build (Uzun Sürer - 15-20 Dakika)

Eğer bu ortamda build almak istiyorsanız:

```bash
# 1. Android SDK kurulumu devam ediyor (arka planda)
# Log kontrol:
tail -f /tmp/bubblewrap-init.log

# 2. Kurulum tamamlandığında:
cd /app
bubblewrap build

# 3. APK dosyası:
# app-release-signed.apk
```

**NOT:** Bu yöntem çok uzun sürüyor, PWABuilder kullanmanızı öneriyorum.

---

## 📱 Keystore Bilgileri (Önemli - Kaydedin!)

```
Keystore Location: /app/twa-key.keystore
Download URL: https://relivv-marketplace-1.preview.emergentagent.com/relivv-keystore.jks

Keystore Password: relivv2025
Key Alias: relivv
Key Password: relivv2025

SHA256 Fingerprint:
83:71:81:8B:1E:F3:16:B8:36:45:D9:25:5B:5E:24:2F:BE:AF:AD:4E:EE:0F:46:88:13:E3:A7:B1:CB:E0:17:09
```

⚠️ **ÖNEMLİ:** Bu keystore'u güvenli bir yerde saklayın! Her uygulama güncellemesinde aynı keystore gerekli.

---

## 🎯 Digital Asset Links (Otomatik Hazır)

Asset links dosyası zaten yüklü ve doğru fingerprint ile yapılandırılmış:
```
https://relivv-marketplace-1.preview.emergentagent.com/.well-known/assetlinks.json
```

Kontrol etmek için:
```bash
curl https://relivv-marketplace-1.preview.emergentagent.com/.well-known/assetlinks.json
```

---

## 📤 Play Console'a Yükleme

### Adım 1: Play Console'a Giriş
https://play.google.com/console
Developer ID: `7474874603789179697`

### Adım 2: Yeni Uygulama Oluştur
1. "Create app" tıklayın
2. App details:
   - **App name:** Relivv Marktplaats
   - **Default language:** Dutch (Netherlands)
   - **App or game:** App
   - **Free or paid:** Free

### Adım 3: Store Listing Doldur
`/app/twa/PLAY_STORE_LISTING.md` dosyasında tüm bilgiler hazır:
- App descriptions (Dutch & English)
- Screenshots gereksinimleri
- Privacy policy URL: https://relivv-marketplace-1.preview.emergentagent.com/privacy-policy
- Contact: support@relivv.nl

### Adım 4: AAB Yükle
1. "Production" → "Create new release"
2. AAB dosyasını yükle
3. Release notes ekle (PLAY_STORE_LISTING.md'de hazır)

### Adım 5: Review & Publish
- Tüm zorunlu bölümleri doldur
- "Send for review"
- Google incelemesi 1-7 gün sürer

---

## ✅ Kontrol Listesi

**Teknik:**
- [x] Keystore oluşturuldu
- [x] SHA256 fingerprint alındı
- [x] Digital Asset Links yapılandırıldı (assetlinks.json)
- [x] Privacy policy yayınlandı
- [ ] AAB dosyası oluşturulacak (PWABuilder)
- [ ] Play Console'a yüklenecek

**Store Listing:**
- [x] App descriptions hazır (PLAY_STORE_LISTING.md)
- [ ] App icon (512x512 PNG) - oluşturulacak
- [ ] Feature graphic (1024x500 PNG) - oluşturulacak
- [ ] Screenshots (4-8 adet) - oluşturulacak

---

## 🎨 Görseller İçin

### App Icon (512x512 PNG)
SVG template hazır: `/app/twa/app-icon.svg`

Online converter kullanarak PNG'ye çevirin:
- https://cloudconvert.com/svg-to-png
- 512x512 boyutunda export edin

### Screenshots
Playwright ile otomatik screenshot alabiliriz veya manuel olarak:
1. Uygulamayı açın: https://relivv-marketplace-1.preview.emergentagent.com
2. Chrome DevTools → Device Toolbar (Ctrl+Shift+M)
3. Pixel 5 veya benzer cihaz seç (1080x1920)
4. Önemli sayfaların ekran görüntüsünü alın:
   - Homepage
   - Product detail
   - Shopping cart
   - Messages
   - Profile

---

## 🆘 Sorun Giderme

**"Digital Asset Links verification failed"**
- assetlinks.json erişilebilir mi kontrol et
- SHA256 fingerprint doğru mu?
- Domain HTTPS mi?

**"Can't upload AAB"**
- Version code artır (1, 2, 3...)
- Doğru keystore kullanıldı mı?

**"App not verified"**
- 48 saat bekle (Google verification)
- Play Console → App integrity kontrol et

---

## 📞 Yardım

Bu rehberle ilgili sorularınız için: support@relivv.nl

---

**Son Güncelleme:** 22 Ocak 2025
