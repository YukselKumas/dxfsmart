# DXFsmart sürüm notları

## 1.1.1
- **Güncelleme düzeltmesi:** v1.0'daki güncelleyici arka planda bir cmd betiği çalıştırıyordu; bazı bilgisayarlarda bu betik konsol pencerelerini sürekli açıp kapatarak döngüye giriyordu. Artık cmd hiç kullanılmıyor: çalışan exe'nin adı değiştirilir, yeni exe yerine konur ve yeni sürüm doğrudan başlatılır. Takas olmazsa döngüye girmeden hata mesajı gösterilir. Açılışta eski sürümün dosyası ve v1.0'dan kalan betik temizlenir; betik silinince eski döngü de durur.

## 1.1.0
- **Yeni yerleşim motoru:** açık kaynak sparrow optimizer'ı (MIT; jagua-rs çarpışma motoru, MPL-2.0) programa gömüldü. sparrow sınırsız tek bir şerit için yazılmıştı. Plaka sınırları şeride yasak bantlar olarak eklendi, böylece optimizer tek aramada hem plaka sayısını hem son plakada kullanılan boyu küçültüyor; hiçbir parça plaka sınırını geçemiyor.
- **Canlı simülasyon:** yerleşim hesaplanırken optimizer'ın denediği yerleşimler ekranda izlenir. Her satır bir plakadır; kırmızı kesik çizgiler o an çözülen çakışmalardır. İlerleme metni kaç plakaya sığdığını ve doluluğu gösterir.
- **Deneyerek en iyiyi seçer:** sparrow sonucu DXFsmart'ın kendi raster motoruyla karşılaştırılır; daha az plaka, eşitse son plakada daha kısa boy kullanan seçilir. Sonuç ekranında iki motorun skoru yazar.
- **Delik içi kitleri:** delikli parçaların (ör. flanş) delikleri önce küçük parçalarla doldurulur; flanş ve içindekiler tek birim olarak yerleşir.
- Örnek projede (210 parça, 1500×3000): 3 plaka, son plaka 2931 mm yerine 2620 mm (Dengeli) / 2507 mm (En iyi sonuç). Daha çok çekirdekli bilgisayarlarda arama daha hızlı ilerler.

## 1.0.0
- **Masaüstü uygulaması:** kendi penceresinde açılır (Edge WebView2). Tarayıcı ve konsol penceresi yok; pencere kapanınca program kapanır.
- **Tek tıkla güncelleme:** açılışta GitHub Releases kontrol edilir, yeni sürüm varsa üstte bildirim çıkar; "Güncelle" yeni exe'yi indirir, eskisinin yerine koyar ve yeniden açar. Ücretsiz, hesap gerektirmez.
- **Yeni tasarım:** gece mavisi zemin, lazer ısı gradyanları (amber, pembe, mor), cam paneller, fırçalanmış çelik sac ve parlak kesilmiş parçalar; yeni uygulama ikonu.
- **Daha iyi yerleşim:** "Dengeli" ve "En iyi sonuç" modlarına iyileştirme döngüsü eklendi. En iyi stratejiden başlayıp parça gruplarının sırasını değiştirir, grupları parçalara bölüp araya serpiştirir, yerleştirme kuralını, doldurma yönünü ve temas ağırlığını değiştirir; sadece iyileşmeleri tutar. "En iyi sonuç" yaklaşık 3 dakika arar.
- **İki doluluk ölçüsü:** net doluluk (malzeme verimi) ve kaplanan alan (parçanın kendi delikleri dahil yerleşim sıkılığı). Flanş gibi büyük delikli parçalarda iki değer arasındaki fark, deliğin içinin fire sayılmasından gelir.
- Parça DXF'leri bellekte üretilir: her işletim sisteminde aynı çıktı.

# Önceki sürüm: prototip v0.9

## v0.9: Gerçek kontur nesting, makine gücüne göre hız, parça inceleme ve ölçüm

### Gerçek kontur nesting (nest_shape.py)
- Parçalar dikdörtgen kutu olarak değil, **gerçek konturlarıyla** yerleştirilir. L, üçgen ve trapez gibi parçalar birbirine geçer.
- **Serbest açı:** döndürme yok / 180° / 90° / 45° / 15° / 5° adım. Simetrik parçalarda aynı sonucu veren açılar otomatik elenir.
- **Delik içine parça:** büyük deliklerin içi küçük parçalarla doldurulur (açılıp kapatılabilir).
- Arama, sacın ızgara haritası üzerinde FFT korelasyonuyla bütün konumlarda aynı anda yapılır. Izgara bilerek "güvenli tarafta" tutulur, böylece bulunan her aday geometrik olarak geçerlidir. Seçilen parça sonra kesin geometriyle (shapely) komşusuna dayanana kadar kaydırılır.
- Puanlama: kullanılan sac boyunu uzatmayan konumlar önce gelir; aralarından komşulara ve sac kenarına en çok temas eden seçilir. Bu kural boşluk doldurmayı ve parçaların birbirine geçmesini sağlar.
- Farklı parça sıraları, doldurma yönleri (uzun kenar, kısa kenar, köşeye çekim) ve karışık sıralar süre bütçesi içinde denenir. En az plaka, eşitlikte son plakada en kısa kullanılan boy seçilir.
- Her kalınlıkta eski MaxRects (dikdörtgen) yöntemi de çalıştırılır ve iyi olan sonuç kullanılır. Sonuç hiçbir zaman dikdörtgen yöntemden kötü olmaz.
- Son yerleşim kesin geometriyle doğrulanır: tüm parçalar kenar payının içinde, aralarındaki mesafe en az parça arası değeri kadar. Yaylar için 0,05 mm güvenlik payı vardır.
- **Optimizasyon seviyesi:** Hızlı ön bakış (yaklaşık 5 sn/kalınlık), Dengeli (yaklaşık 25 sn), En iyi sonuç (yaklaşık 90 sn). Süreler işlemci çekirdek sayısına göre değişir, FFT tüm çekirdekleri kullanır.
- **Yerleşim DXF'i:** Dışa aktar > Yerleşim plakaları. Her plaka ayrı bir DXF'tir: SAC, KENAR_PAYI ve PARCA katmanları var; parçalar orijinal CAD nesneleriyle (yay, daire) döndürülüp yerleştirilmiştir.
- Sonuç ekranında son plakadaki artık sac şeridi (sarı kesikli çizgi), etkin doluluk ve dikdörtgen yönteme göre plaka farkı gösterilir.

Ölçüm (bu paketteki karışık test: 345 parça; L braket, flanş, üçgen, trapez, delikli plaka, pul; 1500×3000 sac, 5 mm aralık):
dikdörtgen yöntem 6 plaka, gerçek kontur 4 plaka. Alan alt sınırı 3 plaka ama bu daire ağırlıklı karışımda %88 doluluk gerektirir. 25 parça başka parçaların deliklerine yerleşti.
Arayüzdeki örnek atölye işinde (210 parça) dikdörtgen yöntem 5 plaka, gerçek kontur 3 plaka (15° adım, Dengeli, iki çekirdekli test makinesinde yaklaşık 30 sn).

**Metalix/Lantek ile karşılaştırma:** Bu motor ticari CAM nesting'lerinin kullandığı yaklaşımlara (gerçek kontur, serbest açı, delik içi, çok stratejili arama) dayanır, ancak onların yerine geçtiği ya da onları geçtiği **iddia edilmez**. Ortak kesim (common-line), kesim sırası, köprü/mikro-bağlantı, iskelet kesimi ve kalan sac (remnant) yönetimi yoktur. Aynı işi Metalix'te de yerleştirip plaka sayısı ile son plakada kalan boyu karşılaştırın.

### Makine gücüne göre kesim hızı (laser_presets.py)
- Makine bölümünden fiber lazer gücü seçilir: 1, 1,5, 2, 3, 4, 6, 8, 10, 12 veya 20 kW. Projedeki her kalınlık için malzemeye göre tipik hız (m/dk) gelir: siyah sac O₂, paslanmaz N₂, alüminyum N₂.
- Tabloda olmayan kalınlıklar log ölçekte enterpolasyonla hesaplanır. Makinenin kesemeyeceği kalınlıklarda "tablo dışı" yazar ve genel hız kullanılır.
- Her hız elle değiştirilebilir; değiştirilen değer "düzenlendi" olarak işaretlenir. Seçilen güç tarayıcıda hatırlanır.
- Kaynaklar: 1–6 kW için IPG kaynak parametre tablosu (MachineMFG), kalın siyah sac için EMP Laser tablosu, 8–12 kW için MachineMFG güç tablosunun 1/3/5/10 mm noktalarından enterpolasyon. 20 kW değerleri kamuya açık verinin az olması nedeniyle tahminidir. Değerler referanstır; kendi makinenizin test değerlerini girin.

### Parça inceleme ve ölçüm
- Soldaki listede ya da plaka üzerinde bir parçaya tıklayınca parça büyük açılır. Tekerlekle yakınlaştırılır, sürüklenerek kaydırılır; "Sığdır" (F) ile sığdırılır.
- **Ölçüler sekmesi:** dış ölçü, en dar döndürülmüş kutu, dış çevre, toplam kesim boyu, net alan, ağırlık, delme sayısı. Çizimde dış ölçü çizgileri ve delik çapı etiketleri gösterilir.
- **Delikler sekmesi:** her deliğin çapı (CAD dairesinden geliyorsa kesin, konturdan uydurulduysa "≈" ile), oval delik ve dikdörtgen pencere ölçüsü, sol alt köşeye göre merkez koordinatı, çap grupları (örn. "6 × Ø18"). Satıra tıklayınca delik vurgulanır ve yakınlaşır.
- **Ölçüm sekmesi:** iki noktaya tıklayarak mesafe ölçülür (M tuşu). İmleç köşelere, uç ve orta noktalara, delik ve yay merkezlerine, daire çeyreklerine ve kenarlara yapışır. Shift yatay/dikey kilitler, Esc yarım ölçümü iptal eder, Backspace son ölçümü siler. Her ölçümde mesafe, ΔX, ΔY ve açı listelenir.
- Önceki ve sonraki parçaya ‹ › düğmeleriyle ya da ok tuşlarıyla geçilir.

### Yeni arayüz
- CAM iş istasyonu düzeni: solda parça listesi, ortada sac tuvali, sağda üretim ayarları. Uzun sac yatay gösterilir.
- Renkler çeliğin tav renklerinden: tav mavisi eylemler, saman sarısı lazer ve ilerleme, mor ölçüm. Yazı tipi Windows'ta yerleşik Bahnschrift (DIN); internet gerekmez.
- Parça listesinde bir satırın üzerine gelince o parçanın plakadaki yerleri vurgulanır.

### Düzeltilen hatalar
- Cıvata delikli flanş gibi yuvarlak parçalar 90° dışındaki açılarda aynı parça olarak tanınmıyor, "ayna eşi" sanılıyordu. Delik konumundan hizalanarak düzgün gruplanıyor.
- Kontur oluşturmada aynı halkanın iki kopyası yuvarlama sınırına denk gelince ayıklanamıyor, delik ayrı bir "ada parça" gibi çıkabiliyordu. Bu durum döndürülmüş çizimlerde (ör. yerleşim DXF'i) görülüyordu. Kopyalar artık toleranslı uzamsal eşleştirmeyle ayıklanıyor.

Kurulum: requirements.txt'ye numpy ve scipy eklendi. Başlatıcı ilk açılışta bunları otomatik kurar. Başlıkta DXFsmart görünür; sürümü `/api/health` verir (0.9.0).


## v0.8: Güvenilir ayırma ve hız

Bu sürüm, daha önce **uyarı vermeden yanlış DXF üretebilen** dört motor hatasını düzeltir:

1. **Extrusion (0,0,-1) / OCS:** SolidWorks ve Inventor açınımlarında sık görülen ters normal yönlü ARC, CIRCLE ve LWPOLYLINE nesneleri artık doğru konumda okunuyor. Eski sürümde bu tür delikler parçadan kopup ayrı disk parçası oluyor ve plaka deliksiz kalıyordu. Tüm eğriler `ezdxf.path` üzerinden dünya koordinatına çevrilir. Eğik düzlemde çizilmiş nesneler uyarıyla atlanır.
2. **Aynalanmış bloklar:** `xscale=-1` ile yerleştirilmiş blok içindeki yaylar artık doğru açılıyor.
3. **Ortak kenar:** İki bitişik parçanın paylaştığı tek LINE artık her parçaya, o parçaya ait kısmından kırpılarak aktarılıyor (SHARED_EDGE uyarısı). Her parçanın seçilen CAD nesnelerinin parçayı **tek başına kapattığı** doğrulanır. Kapatamıyorsa parça analiz konturundan kapalı LWPOLYLINE olarak aktarılır, kritik SAMPLED_EXPORT uyarısı verilir ve Parca_Listesi.csv içinde işaretlenir. Açık konturlu DXF artık sessizce dışa aktarılmaz.
4. **Ayna parçalar:** Sağ ve sol el parçalar artık aynı gruba girmez ve adetleri birleşmez. Büküm yönü, folyolu veya fırçalı sac ve gravürde bu fark önemlidir. Kartlarda "Ayna eşi" notu gösterilir.

Diğer değişiklikler:
- Büyük DXF analizi sırasında sunucu yanıt vermeye devam eder; analiz ayrı bir iş parçacığında çalışır.
- Ayar değişince veya yeni hesap başlatılınca eski nesting işi iptal edilir; yeni iş kuyrukta beklemez.
- MaxRects boş alan temizliği hızlandırıldı. Tek plakada 2400 küçük parça yaklaşık 10 kat daha hızlı hesaplanır, plaka sayıları aynı kalır.
- Bozuk yapılı DXF'ler reddedilmek yerine `ezdxf.recover` ile onarılarak okunur (RECOVERED uyarısı).
- Çerçeveli teknik resim modunda geometri uyarıları (atlanan nesne, kendini kesen nesne) artık raporda görünür. Çözümlenemeyen blok tüm analizi durdurmaz.
- Plaka önizlemesi makine koordinatıyla aynı yönde (sol alt köşe 0,0) çizilir. Parçalar artık plakada aynalı görünmez.
- CSV dosyalarında ondalıklar virgülle yazılır (Türkçe Excel). Ayna ve aktarım notu sütunları eklendi.
- ONCE_OKUYUN.txt yalnızca ilgili durumlarda "ana görünüm adayı" notunu içerir.
- Başka bir web sitesinin tarayıcı üzerinden yerel sunucuya dosya göndermesi engellendi.
- Başlatıcılar ilk açılışta `.venv` sanal ortamı kurar. Paketler yalnızca requirements.txt değişince yeniden kurulur, sonraki açılışlarda internet gerekmez. Windows'ta `py` başlatıcısı da desteklenir.
- Kullanılmayan deneme API'si (`/api/estimate`, estimates.py) kaldırıldı. Paslanmaz yoğunluğu tek kaynakta: 8000 kg/m³.
- Test paketi güncellendi ve yukarıdaki hataların her biri için regresyon testi eklendi (`tests/test_v08_regressions.py`).

Başlıkta **STUDIO / 0.8** görünmelidir.


## v0.7: Studio tasarımı ve çoklu strateji

Yeni arayüz: koyu yeşil yan gezinme, geniş plaka önizlemesi, solda üretim ayarları, yukarıda plaka/kg/süre özeti, kalınlık ve plaka seçimi, aranabilir parça kartları ve açılır geometri raporu. Telefon ve dar ekranlar için ayrı düzen vardır. Sistem yazı tipleri kullanılır; internetten font veya görsel indirilmez. Hareketi azaltma tercihi olan kullanıcılarda animasyonlar durur.

Nesting arka planda çalışır. Hazırlanıyor, Yerleştiriliyor ve Sonuçlar hazırlanıyor aşamaları ile yüzde bilgisi sunucudan alınır; bir zamanlayıcıyla sahte yüzde gösterilmez. Hesap sürerken ekran kilitlenmez; değişen ayarlar eski sonucun ekrana gelmesini engeller. İşlem hatasında yeniden deneme mümkündür.

Motor alan/uzun kenar/genişlik/yükseklik sıralamalarını kısa kenar, boş alan, alt-sol ve uzun kenar yerleştirme puanlarıyla birleştirerek 16 aday planı karşılaştırır. En az plaka kullanan sonuç, eşitlikte son plakada daha çok boş alan bırakan plan seçilir. Kenar payına fazladan kesim boşluğu ekleme sorunu düzeltildi. Hedef verimden türetilen alan tahmini artık gerçekleşen yerleşim sayısını yükseltmez; ayrı bir planlama hedefidir.

Ölçüm: Müşteri örneği 1500×3000 mm, 5 mm boşluk, 10 mm kenar payı, 90° dönüş, çelik ve mm onayı ile toplam 14 plaka / 1201,05 kg brüt sac / 616,815 kg net parça. Önceki sürümün 15 plakasına göre bir plaka azdır; bu değişimde alan hedefinin plaka sayısını artırmasının kaldırılması da etkilidir. 2 mm grubu 9 plakadır. Başka bir 14 parçalık dikdörtgen testinde tek strateji 4 plaka kullanırken yeni karşılaştırma 3 plaka kullanmıştır. Her iki testte de adetler, sınırlar ve parça aralıkları kontrol edilmiştir.

Bu hâlâ yaklaşık bir dikdörtgen sınır kutusu yerleşimidir; gerçek kontur çakışmasına göre iç içe geçen parçalar, delik içine yerleşim, serbest açı veya ortak kesim optimizasyonu yoktur. Global en iyi yerleşimi garanti etmez. Konturlar önizlemede gösterilir. Süre kendi makine hızlarınıza bağlıdır. Aşağıdaki eski sürüm bölümlerindeki 15 plaka ve 1271,7 kg değerleri v0.5/v0.6 geçmiş ölçümleridir.

Windows: ZIP'i yeni klasöre ayıklayın ve start_windows.bat dosyasını açın. Yeni başlatıcı sayfayı sunucu hazır olduktan sonra açar. 8000 doluysa 8001–8010 arasındaki ilk boş portu kullanır; böylece eski sürümün sayfası yanlışlıkla açılmaz. Başlıkta STUDIO / 0.7 görünmelidir.

## v0.6: Ortalama nesting yap

Üretim planlama alanı parça listesinin üstünde geniş bir paneldir. Dosyayı yükledikten sonra plaka ölçüsü, malzeme, boşluk ve kenar payını seçin. Birim tanımsız dosyalarda ölçülerin mm olduğunu doğrulayıp onay kutusunu işaretleyin. Ardından Ortalama nesting yap düğmesine basın. Ayar veya adet değiştiğinde eski sonuçlar kaldırılır; düğmeye tekrar basın.

Sonuçlar: kalınlıklar ayrı hesaplanmış toplam plaka, brüt tam sac kg, adet dahil net parça kg, toplam kesim çevresi ve tahmini kesim süresi. Kalınlık seçimiyle plaka önizlemesi değişir. Önizleme gerçek parça konturlarını gösterir; yerleşim hâlâ dikdörtgen sınır kutularını kullanır. Bu yaklaşık bir planlama hesabıdır; parçaları birbirine geçiren kontur nesting motoru değildir.

Kesim süresi = (adet dahil dış ve iç çevre / kesim hızı + adet dahil delme sayısı × delme saniyesi / 60) × (1 + ek süre yüzdesi / 100). Hız mm/dk, sonuç dakika cinsindedir. Her dış kontur ve her delik için bir delme sayılır. Genel hız 3000 mm/dk, delme 1,5 sn ve ek süre %15 başlangıç değerleridir; makine veya malzeme garantisi değildir. Kalınlığa göre hız bölümünden her kalınlığın hızını ayrı girin; boş alanlar genel hızı kullanır. Sac yükleme/boşaltma ve makineye özel hızlanma, köprüler, ortak hat kesimi modellenmez.

Test örneğinde 711 adet için genel 3000 mm/dk hız, 1,5 sn delme ve %15 ek süreyle yaklaşık 346 dakika bulunmuştur. Bu sayı seçilen hızlara bağlıdır; kendi makine değerlerinizi girin. Çelik ve 1500×3000 mm sac için yaklaşık 15 plaka ve 1271,7 kg brüt sac, 616,815 kg net parça hesaplanmıştır.

## v0.5: plaka, toplam kg ve dosya adları

Malzeme seçimi ile tek parça ve adetle çarpılmış toplam net kg hesaplanır: net alan × kalınlık × adet × yoğunluk. Çelik 7850, paslanmaz 8000, alüminyum 2700 kg/m³ kullanılır. Net kg delikler çıkarıldıktan sonraki parça ağırlığıdır. Alınacak tam plakaların tahmini brüt ağırlığı ayrıca gösterilir.

Her kalınlık ayrı yerleştirilir ve plaka sayıları toplanır. Kalınlık listesinden o kalınlığın temsili yerleşimi seçilebilir. Hesap, dikdörtgen sınır kutularına dayanan yaklaşık yerleşim ve hedef alan verimidir; gerçek kontur nesting optimizasyonu değildir.

DXF birimi tanımsızsa ölçülerin mm olduğunu kontrol ettikten sonra “Birim tanımsızsa mm olarak hesapla” kutusunu işaretleyin. Bu onay koordinatları ölçeklemez ve orijinal DXF birimini değiştirmez. İnç/cm tanımlı çizimler kabul edilmez; CAD'de mm’ye çevrilmelidir. Kalınlık okunamazsa yalnızca eksik parçalar için kullanılacak varsayılan kalınlık girilebilir.

ZIP indirme ekrandaki güncel grupları ve adetleri kullanır; sıfır adetler dışarı aktarılmaz. Aynı gruptaki geometri bir kez kaydedilir ve grup toplam adedi adında yer alır. Örnek: Örnek_Müşteri_P045_5mm_20adet.dxf. CSV listesi de aynı adetleri içerir. Dosya içindeki geometri adet kadar çoğaltılmaz. Adet ve kalınlık doğruluğunu kaynak teknik resimle karşılaştırın.

Müşteri örneği: çelik, koordinatlar mm kabulü, 1500×3000 mm sac, 5 mm aralık, 10 mm kenar payı ve %80 hedef verim ile 711 adet için yaklaşık 15 plaka; net 616,815 kg. Kalınlık bazında plaka sayıları: 1 mm → 2, 2 mm → 10, 3 mm → 1, 5 mm → 1, 6 mm → 1. Geometri ana görünüm adayı seçimine dayandığından bu değerler üretim onayı değildir.

## v0.4: çerçeveli teklif / imalat resimleri

En az üç ADET etiketi ve üç dikdörtgen çerçeve bulunan çizimlerde paket resim analizi devreye girer. Çerçeveler kesimden çıkarılır; yazı bloklarının içindeki adet ve MM kalınlık bilgileri okunur. Aynı blokta ayrı ayrı yazılmış sayı ve MM metinleri de desteklenir. Çizim çizgileri ve blok geometrileri birlikte incelenir; birbirine değen yüzeyler birleştirilerek iç büküm çizgileri dış sınırdan ayrılır. Her etiket bölgesindeki en büyük kapalı görünüm aday olarak seçilir.

Bu seçim bir sezgidir: en büyük görünüm her teknik resimde kesim açınımı olmayabilir. Adaylar arayüzde kontrol işaretli gösterilir. Eşleştirilemeyen etiketler veya belirsiz kalınlıklar uyarı üretir; kalınlık tahmin edilmez. Ölçülerden yeniden açınım hesaplanmaz. Açık ana kontur yerine kapalı bir yan görünüm seçilmesi mümkündür; adayları kaynak teknik resimle karşılaştırın.

Arayüzde planlanan adet yazıdaki adetten başlar; aynı geometri gruplarında toplanır. Farklı kalınlıklar aynı gruba katılmaz. ZIP içindeki Parca_Listesi.csv her dosyanın güncel adet ve kalınlığını içerir. Üretim adedi kadar dosya kopyası oluşturulmaz.

Müşteri örneğinde 47 ADET etiketi, toplam 711 üretim adedi ve 47 görünüm adayı elde edildi. Kalınlıkların tamamı metinlerden okundu. Dosyanın birim başlığı tanımsız olduğu için otomatik olarak mm kabul edilmez ve plaka hesabı bu dosyada birim düzeltilmeden çalışmaz. Orijinal müşteri çizimi uygulama paketine dahil edilmemiştir.

Metalix, Lantek vb. CAD/CAM iş akışlarına hazırlık için geliştirilmiş **yerelde çalışan web uygulaması prototipi**. Kullanıcı tarayıcı üzerinden çalışır; DXF dosyaları internet üzerindeki bir hizmete yüklenmez, bu bilgisayardaki `127.0.0.1` adresinde çalışan Python sunucusunda işlenir.

## Çalıştırma

**Windows:** Python 3.11 veya daha yeni sürümü kurun (kurulumda **Add Python to PATH** işaretli olsun). `start_windows.bat` dosyasına çift tıklayın. Gerekli kütüphaneler kurulup tarayıcı açılır.

**macOS / Linux:** terminalden `bash start_mac_linux.sh` çalıştırın.

**Alternatif (tüm sistemler):**

```bash
python -m pip install -r requirements.txt
python run.py
```

Tarayıcı adresi: **http://127.0.0.1:8000**. Çalıştığı terminali kapatınca uygulama durur.

## Mevcut özellikler

- **DXF / ZIP:** Tek DXF, çoklu DXF veya DXF barındıran ZIP. En çok 40 MB yükleme, 80 DXF ve toplam 100 MB açılmış içerik.
- **Ayırma:** Kapalı dış konturlar ve iç delikler ilişkilendirilir. Ayrılan parçalar bir ZIP içinde, kendi DXF dosyaları olarak indirilir. Çizgiler, yaylar ve daireler DXF CAD nesnesi olarak korunur. Yalnızca ortak kenarlar kırpılır; nesneleri kapanmayan parçalar uyarıyla polyline olarak aktarılır (bkz. v0.8).
- **Kontrol:** Açık / sahipsiz çizgiler, birebir çakışan CAD nesneleri, kendiyle kesişen nesneler, sıfır uzunluk ve desteklenmeyen DXF öğeleri için uyarı.
- **Benzerlik:** Çevirme ve 90° döndürme normalizasyonuyla aynı geometriler bir grup halinde gösterilir. Aynalanmış parçalar ayrı tutulur ve "Ayna eşi" olarak işaretlenir.
- **Plaka tahmini:** 1000×2000, 1250×2500, 1500×3000, 2000×4000 veya özel sac ebadı; değişken üretim adedi; parça boşluğu; kenar payı; 90° dönüş; hedef alan verimi.
- **İki farklı hesap:** `net geometri alanı / (kullanılabilir sac alanı × hedef verim)` ve MaxRects algoritmasıyla **parçaların dikdörtgen sınır kutularının** örnek yerleşimi. Planlama değeri iki yaklaşımın yüksek olanıdır, sonuç **yaklaşıktır**.
- **Yerleşim çizimi:** Plaka plaka temsili dikdörtgen konumları görülebilir.
- **CSV:** Parça listesini ve kullanıcı düzenlediği adetleri CSV olarak indirme.
- **Test paketi:** `examples/Ornek_DXF_Paketi.zip` veya arayüzde **Örnek projeyi dene**.
- **Geliştirici API:** `/docs` adresinde uç noktalar listelenir.

## Üretim açısından mutlaka bilmeniz gerekenler

1. **Bu bir demo ve operatör kontrollü yazılımdır:** ayrıştırılan parçalar CAD/CAM'de incelenmeden lazerde kullanılmamalı. Polylines, splines, dönüşümler, bloklar, iç-dış kontur ve toleranslar kontrol edilmelidir.
2. **Bu gerçek nesting motoru değildir:** parça döndürme 0°/90° ve dikdörtgen sınır kutusu üzerinden simüle edilir. Delik içine parça yerleştirme, parçaların birbirine geçmesi, ortak hat kesimi, fiber sac yönü, iskelet, microjoint ve malzeme kısıtları dikkate alınmaz. Plaka sayısı alım emri ya da kesin maliyet değildir.
3. **Plaka hesabı için DXF birimi milimetre olmalı:** `$INSUNITS=4`. Birimsiz/inç DXF'ler reddedilir; kaynağı CAD'de mm'ye çevirin.
4. **Aynalanan parçalar aynı parça olmayabilir:** grup her zaman teknik resim ile doğrulanmalı.
5. **Büyük/karmaşık DXF'ler:** belirtilen dosya sınırlarına rağmen performans, çizim içindeki geometri karmaşıklığına bağlıdır. Tüm DXF varyasyonlarının doğruluğu garanti edilmez.
6. **Yerel kullanım:** test sunucusu sadece `127.0.0.1` adresinde çalışır. İnternetten erişilebilir üretim sürümü, güvenli oturum, veri yaşam döngüsü, antivirüs taraması, kota ve hata raporu gibi ayrı çalışmalar gerektirir.

## Test

```bash
python -m pytest -q tests
```

**Güvenlik / işletme notu:** Localhost sunucusu yalnızca kendi cihazınızda açılır (127.0.0.1). İnternete açık ticari sürüm geliştirilmeden önce kimlik doğrulama, dosya saklama/silme politikası, tarama, kotalaştırma, işlem kuyruğu ve CAD doğrulaması eklenmelidir.
