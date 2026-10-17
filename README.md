# EasyScrape

EasyScrape, bir bağlantıdan ya da bağlantı listesinden medya indiren bir Python aracıdır. Hem **komut satırı** hem de küçük bir **arayüz** ile kullanılır. İndirmeyi kendisi yapmaz; video ve ses için **yt-dlp**, görsel galerileri için **gallery-dl** kullanır ve her bağlantıyı uygun eklentiye yönlendirir. DRM veya erişim kısıtlarını aşmaz, telemetri göndermez.

## Özellikler

- **Tek araçta çok platform:** YouTube, YouTube Music, Spotify, X, Instagram, TikTok, Pinterest, SoundCloud ve yt-dlp / gallery-dl'nin desteklediği diğer siteler.
- **Arayüz:** Linkleri yapıştırın ya da `.txt` / `.md` / `.json` dosyası seçin, "İndir"e basın. Varsayılan kayıt yeri Masaüstü'dür ve yalnızca indirilen dosyalar kaydedilir; rapor, metadata veya durum dosyası bırakılmaz. Her bölümün yanındaki **i** düğmesi kısa açıklama verir. Ctrl+Enter ile de indirilir.
- **Çoklu kaynak:** Aynı anda birden çok URL ve dosya verilebilir. Metin dosyalarında bağlantıların çevresinde not ya da yorum olabilir; satırdaki tüm `http(s)://` bağlantıları alınır. Yinelenen bağlantılar bir kez indirilir.
- **Spotify:** Parça, albüm ve playlist desteklenir. Spotify'dan metadata alınır; ses, başlık + sanatçı + süre eşleşmesiyle YouTube Music'ten indirilir (Spotify'ın kendi akışı değildir) ve etiketler yazılır. Remaster/yeniden basım sürümleri, başlık ve sanatçı aynıysa daha geniş süre toleransıyla eşleşir ve "yaklaşık eşleşme" uyarısı düşülür.
- **Hata toleransı:** Başarısız bir bağlantı kaydedilir, kalanlar devam eder. `failed.json` dosyası verilerek yalnızca başarısızlar yeniden denenir. Daha önce inenler atlanır (`--force` ile yeniden indirilir).
- **Ses seçenekleri:** `--audio-only` ve `--audio-format mp3|m4a|opus|flac|wav`. Yalnızca-ses kaynaklar (ör. SoundCloud) başarısız olmak yerine otomatik ses olarak iner.
- **Kalite ve boyut sınırı:** `--max-height` ile video yüksekliği, `--max-items` ile koleksiyon başına öğe sayısı sınırlanır.
- **Genel yedek:** yt-dlp başarısız olursa gallery-dl'nin desteklediği her site (Imgur, Tumblr, Bluesky, RedGifs...) denenir.
- **Windows desteği:** Deno, Node.js, Bun veya QuickJS otomatik bulunup yt-dlp'ye verilir; Windows yolları temizlenir; indirilen videoda ffprobe ile video akışı doğrulanır.
- **Çerez desteği:** Giriş gerektiren siteler için çerez dosyası ya da yerel tarayıcı profili kullanılabilir.

## Kurulum

Python 3.10 veya üstü gerekir.

```sh
python -m pip install .
easyscrape --help
```

Geliştirme için:

```sh
python -m pip install -e ".[dev]"
```

Çalışma zamanı bağımlılıkları: yt-dlp, gallery-dl, Rich, HTTPX, Mutagen, ytmusicapi ve TikTok için `curl-cffi`. `dev` ekstrası pytest, Ruff, mypy ve build araçlarını ekler.

## Arayüzü açma

```sh
easyscrape-gui
```

ya da `python -m easyscrape.gui`. Pencere ekranın sağ alt köşesinde açılır. Windows'ta bir kısayol dosyasının "Kısayol tuşu" alanına bir tuş kombinasyonu (ör. Ctrl+Alt+E) atayarak arayüzü klavyeden de başlatabilirsiniz.

Arayüzdeki seçenekler: link kutusu, dosya seçici, kayıt klasörü ve "Yalnızca ses". Diğer seçenekler (çözünürlük sınırı, çerez vb.) komut satırındadır.

## Komut satırı

Bir URL, bir metin dosyası ya da birkaçını birden verin. Boş satırlar ve `#` ile başlayan satırlar yok sayılır.

```sh
easyscrape "https://www.youtube.com/watch?v=VIDEO_ID"
easyscrape links.txt --out ./indirilenler
easyscrape links.txt --audio-only --limit-platform youtube,spotify
easyscrape links.txt daha_fazla.txt "https://youtu.be/ID"   # birden çok kaynak
easyscrape EasyScrape_Downloads/failed.json                  # yalnızca başarısızları yeniden dene
easyscrape playlist.txt --max-items 5 --max-height 720       # koleksiyon ve çözünürlük sınırı
easyscrape playlist.txt --audio-format mp3                   # sesi dönüştür (ffmpeg gerekir)
```

Varsayılan çıktı klasörü, girdi metin dosyasının yanındaki `EasyScrape_Downloads` klasörüdür (tek URL'de geçerli klasör). Medya platforma göre klasörlenir; metadata açıkken her öğenin yanına `metadata.json` yazılır. Çıktı kökünde ayrıca son çalıştırmanın `report.txt` ve `failed.json` dosyaları ile tamamlananları izleyen `.easyscrape-state.json` bulunur.

Diğer seçenekler: `--no-metadata`, `--quiet`, `--limit-platform AD[,AD...]`, `--force`, `--version`. Tümü için `--help`.

### Çerezler

Bazı siteler giriş gerektirir. `--cookies DOSYA` ile Netscape biçiminde bir çerez dosyası verebilir ya da `--cookies-from-browser TARAYICI` ile yerel bir tarayıcı profilini okutabilirsiniz (firefox, chrome, edge, brave, chromium, opera, vivaldi, safari). İkisi birlikte kullanılamaz. Çerez dosyaları hesap erişimi sağlar: gizli tutun, depo dışında saklayın, asla commit etmeyin veya paylaşmayın. EasyScrape çerezleri kendi bir servisine yüklemez; ancak eklentiler, erişilen siteye gönderebilir.

## Desteklenen kaynaklar ve sınırlar

Destek, ilgili adaptöre, yukarı akış (upstream) çıkarıcıya ve belirli URL'ye bağlıdır.

| Kaynak | Nasıl işlenir | Sınırlar |
| --- | --- | --- |
| YouTube | yt-dlp video ve koleksiyonları indirir. | Biçimler kaynağa bağlıdır; ayrı video ve ses akışlarını birleştirmek için ffmpeg gerekebilir. |
| YouTube Music | `music.youtube.com` bağlantıları yt-dlp ile indirilir; Spotify eşleştirmesi de YouTube Music'te arar. | Sonuçlar herkese açık kataloğa ve kaynak biçimlerine bağlıdır. |
| Diğer siteler | Genel yedek önce yt-dlp'yi, sonra gallery-dl'nin desteklediği siteleri dener. | Yalnızca iki aracın çıkarabildiği içerik. |
| TikTok | yt-dlp ile; tarayıcı taklidi için `curl-cffi` (kurulumla gelir) kullanılır. | Fotoğraf slaytlarında video akışı yoktur, başarısız sayılır. |
| Spotify parça, albüm, playlist | Herkese açık metadata, başlık, sanatçı ve süre (±2 sn) ile YouTube Music sonuçlarına eşleştirilir. İnen ses o eşleşmeden gelir. | Albüm ve playlistler Spotify'ın herkese açık gömme sayfasından okunur; bir playlistin en fazla ilk 100 parçası görünür, daha büyükse rapor uyarır. Eşleşme bulunamayan parça inmez. Özel akışlara erişilmez. Spotify FLAC sunmaz; ses yeniden kodlanmadan M4A/Opus olarak alınır, kalite kaynağa bağlıdır (çoğu zaman ~256 kbps). |
| Instagram | Fotoğraf gönderileri gallery-dl, reel ve videolar yt-dlp ile. | Herkese açık reel'ler girişsiz denenir. Fotoğraf gönderileri, özel veya yaş kısıtlı reel'ler için çerez gerekir. |
| Facebook | Fotoğraflar gallery-dl, videolar yt-dlp ile. | Giriş yapılmış tarayıcı oturumu gerekir. |
| X ve Reddit | Karma medya gönderileri gallery-dl ile. | X herkese açık gönderilerde girişsiz çalışır. Reddit birçok ağda anonim istekleri engeller; çerez kullanın. |
| Pinterest | Pinler gallery-dl ile. | gallery-dl'nin sunduğu içerikle sınırlıdır. |
| Threads | Şu an hiçbir adaptör Threads indirmesini desteklemiyor. | Threads giriş ister ve yt-dlp ile gallery-dl'de uygun çıkarıcı yoktur. |

`--audio-only`, uygun çıkarıcılardan ses ister; kaynağın kalitesini artırmaz. Bir site medyayı desteklenen çıkarıcılara açmıyorsa servisin resmi uygulamasını kullanın.

### Windows medya araçları

yt-dlp, YouTube için bir JavaScript çalışma ortamına ihtiyaç duyabilir. EasyScrape `PATH` üzerindeki Deno, Node.js, Bun veya QuickJS'i otomatik bulup yt-dlp'ye iletir; çalışma ortamını kendisi kurmaz. Windows'ta Deno için:

```powershell
winget install DenoLand.Deno
```

Kurulumdan sonra PowerShell'i kapatıp açın ve kontrol edin:

```powershell
deno --version
```

yt-dlp çalışma ortamı bulamazsa rapora bir uyarı düşülür. Deno ya da başka bir ortam kurduktan sonra, önbelleğe alınmış bir öğeyi yeniden denemek için `--force` kullanın.

Video indirmeleri için `PATH` üzerinde hem `ffmpeg` hem `ffprobe` sağlayan bir FFmpeg sürümü kurun:

```powershell
ffmpeg -version
ffprobe -version
```

`ffprobe` varsa EasyScrape biten dosyada video akışı olup olmadığını kontrol eder. Yoksa yt-dlp'nin seçtiği biçim bilgisine bakar; video akışı seçildiği doğrulanamazsa indirmeyi reddeder.

## Eklentiler (extractor)

Çıkarıcı modülleri `src/easyscrape/extractors/` altındadır. Kayıt sistemi `MATCHERS` ve çağrılabilir bir `handle(url, context)` işlevi sunan modülleri bulur. Özel URL eşleştiricileri genel yedekten önce değerlendirilir. Her işleyici bir `MediaResult` listesi döndürür ve çıkarmayı bir yukarı akış adaptörüne devreder. Örnek için [CONTRIBUTING.md](CONTRIBUTING.md#çıkarıcı-ekleme) dosyasına bakın.

## Geliştirme

Yerel kontroller:

```sh
python -m pytest
ruff check .
mypy src/easyscrape
python -m build
```

GitHub Actions iş akışı bu kontrolleri Linux, Windows ve macOS üzerinde Python 3.10 ve 3.12 ile çalıştıracak şekilde ayarlıdır. CI canlı medya indirmez; çıkarıcı davranışları yerel örnek veriler ve sahte (mock) çağrılarla test edilmelidir.
