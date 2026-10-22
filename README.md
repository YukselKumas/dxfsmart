# DXFsmart

Lazer kesim için DXF hazırlık ve planlama aracı. Karışık çizimleri parçalara ayırır, parçaları **gerçek konturlarıyla** saca yerleştirir (nesting), sac kg'ını ve kesim süresini hesaplar. Her şey **kendi bilgisayarınızda** çalışır; dosyalar internete gönderilmez.

## İndir ve çalıştır (Windows)

1. Sağdaki **Releases** bölümünden en son sürümün `DXFsmart.exe` dosyasını indirin.
2. Dosyaya çift tıklayın. Program birkaç saniyede tarayıcıda açılır.
3. İlk açılışta Windows *"bilgisayarınızı korudu"* uyarısı verebilir: **Ek bilgi → Yine de çalıştır**. (Program dijital imzalı olmadığı için çıkan standart uyarıdır.)
4. Açılan siyah pencere programın kendisidir: kapatınca program da kapanır. O pencerede **Ctrl+C**'ye basmayın.

Python veya başka bir kurulum gerekmez.

## Ne yapar

- **Ayırma:** Tek DXF, çoklu DXF veya ZIP. Kapalı dış konturları ve delikleri bulur, her parçayı orijinal CAD nesneleriyle (yay, daire) ayrı DXF olarak verir. Açık kontur, çakışan çizgi, kendini kesen nesne gibi sorunları raporlar.
- **Gerçek kontur nesting:** Serbest açı (90°, 45°, 15°, 5° adım), delik içine parça, birbirine geçen parçalar, üçgen/köşebent gibi parçalarda 180° çift yerleştirme. Her yerleşim kesin geometriyle doğrulanır (parça arası boşluk ve kenar payı).
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

Python 3.11–3.13 önerilir. `main` dalına yapılan her gönderimde GitHub Actions testleri çalıştırır, `DXFsmart.exe`'yi üretir, kısa bir çalıştırma testinden geçirir ve `run.py` içindeki `VERSION` numarasıyla Releases sayfasına koyar. Yeni sürüm için `VERSION`'ı artırın.

Sürüm notları: [DEGISIKLIKLER.md](DEGISIKLIKLER.md)
