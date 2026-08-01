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
| `spacekids info ep-001` | Bölümün mevcut durumu |

Yararlı bayraklar:

- `--offline` — hiçbir dış servise çıkma (görseller yerel üretilir, ses sessiz olur)
- `--languages tr` — sadece bir dil render et
- `--llm` — senaryoyu şablon yerine Claude ile yaz
- `--no-strict` — denetim hatalarına rağmen render et
- `--overwrite` — var olan senaryonun üzerine yaz

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
| AI klip | Seedance (kota korumalı) | Yok — durağan görsele düşer |

### Seedance hakkında

Seedance klip başına **5-10 saniye** üretir. 5 dakikalık bir bölüm 30-60 klip demek —
ne ücretsiz kotaya sığar ne de bütçeye. Bu yüzden sistem hibrit çalışır:

- **Omurga**: NASA görselleri + Ken Burns hareketi (bedava, telifsiz)
- **Yıldız sahneler**: `hero` işaretli 3-5 sahne için Seedance klibi

Kota koruması bölüm başına sabittir (`max_clips_per_episode`, varsayılan 3) ve
önceden üretilmiş klipler kotadan düşmez. Anahtar yoksa modül tamamen devre dışıdır.

```bash
export SEEDANCE_API_KEY=...     # veya BYTEPLUS_API_KEY
.venv/bin/spacekids run --topic mars
```

> ⚠️ Seedance istek/yanıt şekilleri sağlayıcıların belgelenmiş iş akışına göre
> yazıldı ama **canlı bir anahtarla doğrulanmadı** (bu depoda anahtar yok).
> Uyumsuzluk çıkarsa `providers/seedance.py` içindeki `_submit` ve
> `_extract_video_url` noktalarını ayarlaman yeterli.

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

[seedance]
enabled = false
max_clips_per_episode = 3
clip_seconds = 5
```

**API anahtarları yalnızca ortam değişkenlerinden okunur** (`ANTHROPIC_API_KEY`,
`SEEDANCE_API_KEY`) — kazara commit'lenmesin diye TOML'dan anahtar okunmuyor.

Kurumsal TLS proxy'si arkasındaysan `SSL_CERT_FILE` (veya `SPACEKIDS_CA_BUNDLE`)
ayarlaman yeterli; seslendirme katmanı bunu güven deposuna ekler.

---

## Testler

```bash
.venv/bin/python -m pytest              # 155 test, ~10 saniye
.venv/bin/python -m pytest -m "not slow"  # ffmpeg render'larını atla
.venv/bin/python -m pytest --cov=spacekids
```

Hiçbir test ağa çıkmaz: NASA ve Seedance sahte HTTP taşımasıyla, seslendirme
sessizlik üreticisiyle, Claude ise sahte istemciyle test edilir.

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
2. Toplu bölüm üretimi (bir komutta N bölüm)
3. Seedance'in canlı anahtarla doğrulanması
4. Web arayüzü — çekirdek mantık zaten arayüzden bağımsız

---

## Örnek çıktı

`examples/ep-001/` — Satürn'ün Halkaları bölümünün senaryosu, altyazıları,
kapak görselleri ve YouTube metadata paketi. (Video dosyaları boyutları
nedeniyle depoda tutulmuyor; `spacekids run --topic saturn-rings` ile
yeniden üretebilirsin.)
