# DXFsmart

Lazer kesim için DXF hazırlık ve planlama aracı. Karışık çizimleri parçalara ayırır, parçaları **gerçek konturlarıyla** saca yerleştirir (nesting), sac kg'ını ve kesim süresini hesaplar. Her şey **kendi bilgisayarınızda** çalışır; dosyalar internete gönderilmez.

## İndir ve çalıştır (Windows)

1. Sağdaki **Releases** bölümünden en son sürümün `DXFsmart.exe` dosyasını indirin.
2. Çift tıklayın. DXFsmart kendi penceresinde açılır; tarayıcı ya da siyah komut penceresi açılmaz.
3. İlk açılışta Windows *"bilgisayarınızı korudu"* uyarısı verebilir: **Ek bilgi → Yine de çalıştır**. (Program dijital imzalı olmadığı için çıkan standart uyarıdır.)
4. Yeni sürüm çıktığında program üstte bildirir; **Güncelle**'ye basınca kendini indirip yeniden açılır. Ücretsizdir, GitHub Releases üzerinden çalışır.

Python veya başka bir kurulum gerekmez. Pencere, Windows 10/11'de hazır gelen Microsoft Edge WebView2 ile çizilir.
Bir sorun olursa günlük dosyası: `%LOCALAPPDATA%\DXFsmart\log.txt`

## Ne yapar

- **Ayırma:** Tek DXF, çoklu DXF veya ZIP. Kapalı dış konturları ve delikleri bulur, her parçayı orijinal CAD nesneleriyle (yay, daire) ayrı DXF olarak verir. Açık kontur, çakışan çizgi, kendini kesen nesne gibi sorunları raporlar.
- **Gerçek kontur nesting:** Serbest açı (90°, 45°, 15°, 5° adım), delik içine parça, birbirine geçen parçalar, üçgen/köşebent gibi parçalarda 180° çift yerleştirme. Her yerleşim kesin geometriyle doğrulanır (parça arası boşluk ve kenar payı).
- **Çok-plakalı optimizer (v1.1):** Akademik nesting algoritması [sparrow](https://github.com/JeroenGar/sparrow) (MIT, arXiv 2509.13329), plaka sınırlarını doğrudan arama içine alacak şekilde genişletildi: tek optimizasyonda plaka sayısı ve son plakada kullanılan boy birlikte küçültülür. Arama ekranda canlı izlenir (her satır bir plaka). Sonuç DXFsmart'ın kendi raster motoruyla karşılaştırılır, daha az malzeme harcayan seçilir.
- **Plaka, kg, süre:** Kalınlık başına ayrı yerleşim, brüt sac ve net parça ağırlığı, kesim boyu ve delme sayısı. Lazer gücü (1–20 kW) seçilince malzeme ve kalınlığa göre tipik kesim hızları gelir; hepsi elle değiştirilebilir.
- **Parça inceleme ve ölçüm:** Parçaya tıklayınca büyür; dış ölçüler, delik çapları ve merkezleri listelenir, iki nokta arası mesafe köşe ve merkezlere yapışarak ölçülür.
- **Dışa aktarım:** Parça DXF paketi (adet ve kalınlık dosya adında), yerleşim plakaları DXF, parça listesi CSV.

Hesaplar planlama içindir. Kesimden önce CAD/CAM üzerinde kontrol edin. Ortak kesim, kesim sırası ve mikro bağlantı yoktur.

## Geliştiriciler için

```bash
python -m pip install -r requirements.txt
python run.py            # http://127.0.0.1:8000
python -m pip install pytest httpx && python -m pytest -q tests
```

Python 3.11–3.13 önerilir. `pip install pywebview` ile kendi penceresinde, kurulu değilse tarayıcıda açılır. `main` dalına yapılan her gönderimde GitHub Actions testleri çalıştırır, `DXFsmart.exe`'yi üretir, kısa bir çalıştırma testinden geçirir ve `version.py` içindeki `VERSION` numarasıyla Releases sayfasına koyar. Yeni sürüm için `VERSION`'ı artırın; kullanıcılar programın içinden güncelleme bildirimi alır.

Optimizer'ı geliştirirken derlemek için (Rust 1.90+): `cd third_party/sparrow && cargo build --release --features=live_svg --bin sparrow`, çıkan dosyayı `bin/` klasörüne kopyalayın. Yoksa program yalnızca kendi raster motoruyla çalışır. Değişiklikler: [third_party/sparrow/DXFSMART_CHANGES.md](third_party/sparrow/DXFSMART_CHANGES.md); jagua-rs MPL-2.0, sparrow MIT lisanslıdır.

Sürüm notları: [DEGISIKLIKLER.md](DEGISIKLIKLER.md)
