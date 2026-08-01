# 🚀 Space Kids Studio

Çocuklar için uzay temalı, **çok dilli** (TR + EN) YouTube videoları üreten bir hat.

Bir konu seçersin — sistem sırayla senaryoyu yazar, seslendirir, görselleri toplar,
altyazılı videoyu kurgular ve kapak görseli + başlık/açıklama/etiket paketini hazırlar.

> **Bu bir "bas-gitsin" kutusu değil, bir üretim bandı.** Her aşama çıktısını diske
> yazar; araya girip senaryoyu düzeltip kaldığın yerden devam edebilirsin. Bu bilinçli
> bir tasarım tercihi: YouTube'un otomatik seri üretim içeriğine yaklaşımı ve çocuk
> içeriğinin hassasiyeti, insan denetimini zorunlu kılıyor.

---

## Hızlı başlangıç

```bash
cd space-kids-studio
python3 -m venv .venv
.venv/bin/pip install -e ".[voice,dev]"

.venv/bin/spacekids topics                      # konuları listele
.venv/bin/spacekids run --topic saturn-rings    # uçtan uca bir bölüm üret
```

Sonuç `episodes/ep-001/out/` altında: `video.tr.mp4`, `video.en.mp4`, altyazılar,
kapak görselleri ve YouTube'a yüklemeye hazır metadata.

**API anahtarı gerekmez.** Sistem anahtarsız da uçtan uca çalışır: senaryo konu
kütüphanesindeki bilgi kartlarından kurulur, görseller NASA arşivinden gelir,
seslendirme ücretsiz Edge TTS ile yapılır.

---

## Komutlar

| Komut | Ne yapar |
|---|---|
| `spacekids topics` | Konu kütüphanesini listeler |
| `spacekids script --topic mars` | Senaryo üretir → `episode.json` |
| `spacekids check ep-001` | Yaşa uygunluk denetimi çalıştırır |
| `spacekids render ep-001` | Ses + görsel + video + altyazı + kapak |
| `spacekids run --topic mars` | Hepsini sırayla |
| `spacekids batch --count 5` | Tek komutta 5 bölüm üretir |
| `spacekids clips ep-001` | Yıldız sahneler için AI klibi üretir |
| `spacekids providers` | AI klip sağlayıcılarını listeler |
| `spacekids info ep-001` | Bölümün mevcut durumu |

Yararlı bayraklar:

- `--offline` — hiçbir dış servise çıkma (görseller yerel üretilir, ses sessiz olur)
- `--languages tr` — sadece bir dil render et
- `--llm` — senaryoyu şablon yerine Claude ile yaz
- `--no-strict` — denetim hatalarına rağmen render et
- `--overwrite` — var olan senaryonun üzerine yaz

---

## Toplu üretim

Bir kanalın en çok ihtiyaç duyduğu şey içerik hacmi. `batch` kullanılmamış
konuları sırayla alır ve hepsini üretir:

```bash
spacekids batch --count 5                 # 5 bölüm, TR + EN
spacekids batch --topics mars,the-moon    # belirli konular, sırayla
spacekids batch --retry                   # yarım kalanları tamamla
```

**Hata toleranslı.** Bir bölüm denetimden kalırsa ya da render'da patlarsa
diğerleri devam eder; hata sonunda özetlenir. Gece boyu süren beş bölümlük bir
işin üçüncüde durup kalması, tek bozuk konudan çok daha pahalıya mal olur.

```
📊 Özet: 4 başarılı, 1 başarısız
   ep-001   saturn-rings     55 sn video (tr, en) — 41 sn'de
   ep-002   mars             54 sn video (tr, en) — 40 sn'de
   ...
   ep-005   milky-way        HATA: Güvenlik denetimi başarısız (1 hata)

   toplam 7 dk 12 sn video, 2 dk 44 sn sürede üretildi

   Başarısızları tamamlamak için: spacekids batch --retry
```

Başarısızlık varsa **çıkış kodu 1** olur — cron ya da CI bunu fark edebilir.
Konu seçimi rastgele değil kütüphane sırasına göredir: aynı komutun ne
üreteceğini önceden bilmek, gece çalışan bir işte rastgelelikten daha değerli.

Kütüphanede yeterli kullanılmamış konu yoksa sessizce az üretmez, söyler.

---

## Nasıl çalışır

```
konu → senaryo → denetim → görsel ─┐
                          ses ─────┼→ sahne klipleri → birleştir → video.<dil>.mp4
                          AI klip ─┘                              + altyazı
                                                                  + kapak
                                                                  + metadata
```

**Görseller ve AI klipleri diller arasında paylaşılır**, sadece ses ve metin değişir.
Yani TR ve EN videoları aynı görsel kurguyu taşır — bir kez indirilir, iki kez kullanılır.

### Bölüm klasörü

```
episodes/ep-001/
├── episode.json          # senaryo + yerelleştirmeler  ← elle düzenlenebilir
├── images/s01.jpg        # diller arasında paylaşılır
├── clips/s03.mp4         # AI klipleri (varsa)
├── audio/tr/s01.mp3
├── scenes/tr/s01.mp4     # ara ürün: görsel + ses birleşmiş sahne
└── out/
    ├── video.tr.mp4
    ├── subtitles.tr.srt
    ├── thumbnail.tr.jpg
    ├── metadata.tr.json  # YouTube'a yüklemeye hazır
    └── manifest.tr.json  # sahne zamanlamaları
```

Her adım **idempotent**: çıktısı zaten varsa yeniden üretmez. Yarıda kalan bir render
aynı komutla kaldığı yerden devam eder — pahalı adımlar (AI klip, seslendirme) tekrarlanmaz.

---

## Güvenlik denetimi

Çocuk içeriğinin en kritik parçası. Her senaryo videoya dönüşmeden önce denetlenir:

| Denetim | Seviye |
|---|---|
| Korkutucu / uygunsuz temalar (canavar, ölüm, savaş…) | ❌ Hata |
| Ticari yönlendirme ("satın al", "linke tıkla") | ❌ Hata |
| Anlatımda bağlantı/adres | ❌ Hata |
| Çok kısa sahne (< 6 kelime) | ❌ Hata |
| Tonu sertleştirebilecek kelimeler (patlama, çarpışma) | ⚠️ Uyarı |
| Çok uzun cümle (TR > 13, EN > 17 kelime) | ⚠️ Uyarı |
| Okunabilirlik: ortalama cümle uzunluğu | ⚠️ Uyarı |
| Hedef süre dışı (45 sn – 10 dk) | ⚠️ Uyarı |

Hata varsa render durur; uyarılar yalnızca bilgilendirir. Uzay konusu doğası gereği
"patlama" gibi kelimeler içerebildiği için bu ayrım önemli.

Terim eşleştirmesi **kelime sınırına saygılıdır**: Türkçe eklemeli bir dil olduğu
için `öldür*` ekli hâlleri yakalar, ama düz alt-dize araması `bölüm` içinde `ölüm`,
`kanatları` içinde `kan` bulup yanlış alarm verirdi. Bu ayrım testlerle korunuyor.

---

## Sağlayıcılar

Tüm dış servisler bir protokolün arkasında. Testler sahte sağlayıcılarla çalışır,
`--offline` modda yerel üretim devreye girer, gerçek sağlayıcı hata verirse
otomatik olarak yedeğe düşülür — **ağ sorunu pipeline'ı asla durdurmaz.**

| Katman | Birincil | Yedek |
|---|---|---|
| Görsel | NASA Image Library (kamu malı, anahtarsız) | Prosedürel yıldız alanı (Pillow) |
| Ses | Edge TTS (ücretsiz, TR + EN) | Tahmini süre kadar sessizlik |
| Senaryo | Claude (`--llm`) | Bilgi kartlarından şablon |
| AI klip | Seedance (kota + istem süzgeci) | Yok — durağan görsele düşer |

## AI klipleri (Seedance)

Seedance klip başına **5-10 saniye** üretir. 5 dakikalık bir bölüm 30-60 klip demek —
ne ücretsiz kotaya sığar ne de bütçeye. Bu yüzden sistem hibrit çalışır:

- **Omurga**: NASA görselleri + Ken Burns hareketi (bedava, telifsiz)
- **Yıldız sahneler**: `hero` işaretli sahneler için AI klibi (varsayılan tavan: 3)

### Erişim durumu — okuman gereken kısım

Seedance'in erişimi 2026 boyunca çalkantılıydı ve bu, hangi servisi seçeceğini
doğrudan etkiliyor:

| Tarih | Ne oldu |
|---|---|
| 12 Şub 2026 | Seedance 2.0 çıktı |
| ~Mart 2026 | Warner Bros. ve Disney telif ihtarı gönderdi: modelin mimari düzeyde telifli karakter tasarımları taşıdığı iddiası |
| 15 Mart 2026 | ByteDance **uluslararası API'yi askıya aldı** |
| 16 Tem 2026 | Seedance **2.5** BytePlus üzerinden yeniden açıldı — ihtarlar hâlâ yanıtsız |

Askı döneminde ortaya çıkan aracı servislerin bir kısmı Çin içi uç noktalara
yönlendirme yapıyor. Bu yüzden:

- **Resmi yolu tercih et** (`byteplus`) — birinci parti, kayıtta ücretsiz token,
  kart istemiyor.
- Aracı servislere kart bilgisi vermeden önce iki kez düşün; erişimleri bir
  gecede kesilebilir.
- **Çocuk kanalı için ek risk**: üretilen bir karenin tanınabilir bir çizgi film
  karakterine benzemesi hem telif hem kanal güvenilirliği açısından en kötü
  senaryo. Sistem bunu istem süzgeciyle azaltıyor (aşağıda) ama **her klibi
  gözle kontrol et**.

### Sağlayıcı seçimi

```bash
.venv/bin/spacekids providers        # seçenekleri ve ücretsiz kullanım notlarını gör
```

Wire formatı Python'a gömülü değil, `config/clip_providers.yaml` içinde **veri
olarak** duruyor. Bir servis alan adını ya da alan adlandırmasını değiştirirse
YAML'ı düzeltmen yeterli. Hazır profiller: `byteplus`, `fal`, `wavespeed`,
`piapi`, `kie`.

> ⚠️ **Profillerin hiçbiri canlı anahtarla doğrulanmadı** (`verified: false`) —
> bu depoda anahtar yok ve geliştirme ortamından bu hostlara erişim kapalıydı.
> Şekiller belgelenmiş iş akışlarına göre yazıldı. Bu yüzden `--dry-run` var.

### Kullanım

```bash
export SEEDANCE_API_KEY=...              # anahtar bulunması klip üretimini açar
export SPACEKIDS_CLIP_PROVIDER=byteplus  # opsiyonel, varsayılan zaten byteplus

spacekids clips ep-001 --dry-run         # 1) kota harcamadan ne gönderileceğini gör
spacekids clips ep-001 --limit 1         # 2) tek klip üret, gözle kontrol et
spacekids render ep-001                  # 3) beğendiysen videoya al
```

`--dry-run` ağa hiç çıkmaz; tam URL'yi, gövdeyi ve süzgeçten geçmiş istemi basar.
Bir alan uyuşmuyorsa YAML'ı düzeltip tekrar denersin — anahtar yakmadan.

### İstem süzgeci

Klip istemleri modele gitmeden önce denetlenir:

- **Reddedilir**: marka/franchise/karakter adı geçen istemler (Disney, Pixar,
  Elsa, Paw Patrol, Pikachu…), gerçek kişiye benzetme talepleri
- **Eklenir**: "özgün eser, tanınabilir karakter yok, logo yok, ekran yazısı yok"
  kısıtları

Sessizce temizlemek yerine **reddediyoruz** — istemi yazan kişinin bundan haberi
olmalı. Üretilen klipler `manifest.<dil>.json` içindeki `ai_clip_scenes` alanında
işaretlenir, insan kontrolü için.

### Kendi klibini koy (API gerekmez)

Bir sahne için `episodes/<bölüm>/clips/<sahne>.mp4` yolunda bir dosya varsa
pipeline onu **olduğu gibi kullanır** — AI sağlayıcısına hiç gitmez, `hero`
işareti aramaz, kotadan düşmez. Klibi nerede ürettiğin sistemin umurunda değil.

```bash
spacekids script --topic mars --episode ep-002
# klibi istediğin araçla üret, sonra:
cp ~/indirilenler/roket.mp4 episodes/ep-002/clips/s01.mp4
spacekids render ep-002
```

Klip videoya sığacak şekilde ölçeklenir, kırpılır ve sahne süresini dolduracak
kadar döngüye alınır — uzunluğunun tam tutması gerekmez.

> ⚠️ Ücretsiz katmanlarla üretilen klipler genelde **filigranlı ve yalnızca
> kişisel kullanım** içindir (Dreamina'nın ücretsiz katmanı böyle). Para
> kazanan bir kanalda kullanmadan önce o aracın lisans şartlarını oku.

### Korumalar

| Koruma | Davranış |
|---|---|
| Kota | Bölüm başına tavan (`max_clips_per_episode`, varsayılan 3) |
| Yeniden çalıştırma | Var olan klip kotadan düşmez, yeniden üretilmez |
| Hata | `None` döner → sahne durağan görsele düşer, pipeline durmaz |
| Anahtar yok | Modül tamamen devre dışı |
| Elle konmuş klip | Her şeyin önünde gelir, API'ye gidilmez |

---

## Kendi konunu ekleme

`config/topics.yaml` içine bir giriş ekle:

```yaml
- id: jupiter
  emoji: "🌕"
  title:
    tr: "Jüpiter'in Fırtınaları"
    en: "Jupiter's Storms"
  hook:
    tr: "Dev Kırmızı Leke!"
    en: "The Great Red Spot!"
  hero_prompt: >-
    A cartoon rocket circling Jupiter's swirling clouds, children's book style
  keywords:
    tr: ["jüpiter", "gezegen", "uzay"]
    en: ["jupiter", "planet", "space"]
  facts:
    - visual: "Jupiter Great Red Spot"      # NASA araması — daima İngilizce
      motion: zoom_in
      tr: "Jüpiter en büyük gezegendir. Üzerinde dev bir fırtına vardır."
      en: "Jupiter is the biggest planet. A giant storm swirls on it."
```

`facts` iki işe yarar: anahtarsız çalışırken şablon üreteci doğrudan bunlardan
senaryo kurar; `--llm` ile çalışırken aynı kartlar modele **doğruluk çıpası**
olarak verilir, böylece uydurma bilgi riski düşer.

---

## Ayarlar

`settings.toml` (isteğe bağlı) veya ortam değişkenleri:

```toml
languages = ["tr", "en"]
age_min = 4
age_max = 8
fact_scene_count = 6

[video]
width = 1920
height = 1080
fps = 30
scene_padding = 0.6        # sahne sonu nefes payı
fade_duration = 0.4        # sahne geçişi karartması
music_path = "assets/music/ambient.mp3"   # opsiyonel fon müziği
music_volume = 0.12

[clips]
enabled = false
provider = "byteplus"        # bkz. config/clip_providers.yaml
model = ""                   # boşsa profilin varsayılanı
max_clips_per_episode = 3
clip_seconds = 5
resolution = "720p"
```

**API anahtarları yalnızca ortam değişkenlerinden okunur** (`ANTHROPIC_API_KEY`,
`SEEDANCE_API_KEY` / `SPACEKIDS_CLIP_API_KEY`) — kazara commit'lenmesin diye TOML'dan anahtar okunmuyor.

Kurumsal TLS proxy'si arkasındaysan `SSL_CERT_FILE` (veya `SPACEKIDS_CA_BUNDLE`)
ayarlaman yeterli; seslendirme katmanı bunu güven deposuna ekler.

---

## Testler

```bash
.venv/bin/python -m pytest              # 226 test, ~35 saniye
.venv/bin/python -m pytest -m "not slow"  # ffmpeg render'larını atla
.venv/bin/python -m pytest --cov=spacekids
```

Hiçbir test ağa çıkmaz: NASA ve klip sağlayıcıları sahte HTTP taşımasıyla,
seslendirme sessizlik üreticisiyle, Claude ise sahte istemciyle test edilir.
Beş klip profilinin her biri kendi yanıt yapısıyla ayrı ayrı doğrulanır.

---

## Bilinen sınırlar

- **Video tamamen otomatik üretilir ama insan denetimi şart.** Çocuk içeriğinde
  bu bir tercih değil, gereklilik.
- **Made-for-kids videolarda kişiselleştirilmiş reklam kapalıdır** — RPM belirgin
  şekilde düşüktür. Bu sistem hız kazandırır, gelir garanti etmez.
- **YouTube'a otomatik yükleme henüz yok** — metadata paketi hazır, yükleme adımı
  sonraki fazda.
- **ffmpeg gerekmiyor**: `imageio-ffmpeg` paketiyle gelen statik binary kullanılır.

## Sonraki adımlar

1. YouTube Data API ile taslak yükleme (OAuth)
2. Klip profillerinin canlı anahtarla doğrulanması (`verified: true` işaretlemesi)
4. Web arayüzü — çekirdek mantık zaten arayüzden bağımsız

---

## Örnek çıktı

`examples/ep-001/` — Satürn'ün Halkaları bölümünün senaryosu, altyazıları,
kapak görselleri ve YouTube metadata paketi. (Video dosyaları boyutları
nedeniyle depoda tutulmuyor; `spacekids run --topic saturn-rings` ile
yeniden üretebilirsin.)
