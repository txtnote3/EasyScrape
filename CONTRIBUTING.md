# Katkı rehberi

## Geliştirme ortamı

Python 3.10 veya üstünü kullanın. Sanal ortam oluşturup geliştirme araçlarıyla birlikte düzenlenebilir kurulum yapın:

```sh
python -m venv .venv
source .venv/bin/activate  # Windows'ta: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Değişiklik açmadan önce sürekli entegrasyonda kullanılan kontrolleri çalıştırın:

```sh
python -m pytest
ruff check .
mypy src/easyscrape
python -m build
```

Testler belirleyici olmalı; canlı medya indirmemeli ve hesap bilgisi gerektirmemelidir. Ağa bağlanan çıkarıcıları sahte (mock) veya yerel örnek verilerle test edin. Çerez dosyalarını, tarayıcı verilerini, indirilen medyayı ve kimlik bilgilerini commit'lere koymayın.

## Çıkarıcı ekleme

`src/easyscrape/extractors/` altına `PLATFORM`, `MATCHERS` ve bir `handle(url, context)` işlevi olan bir modül ekleyin. Çıktı yolları ve seçenekler için ortak `DownloadContext` nesnesini kullanın, tamamlanan dosyalar için `MediaResult` örnekleri döndürün. İşlenemeyen URL için `DownloadError`, yalnızca geçici bir hatada yeniden denemek işe yarıyorsa `RetryableError` fırlatın. Eşleştiricileri dar tutun ki genel yt-dlp yedeğinden önce çalışsınlar.

yt-dlp bir kaynağı işleyebiliyorsa, eklenti mevcut adaptöre devrederek özel bir yol kaydedebilir:

```python
from easyscrape.extractors.yt_dlp_adapter import handle as download_with_ytdlp
from easyscrape.models import DownloadContext, MediaResult

PLATFORM = "example"
MATCHERS = [r"^https://media\.example\.org/"]


def handle(url: str, context: DownloadContext) -> list[MediaResult]:
    return download_with_ytdlp(url, context)
```

yt-dlp siteyi desteklemiyorsa siteye özel bir yukarı akış adaptörü kullanın. Eklenti URL'leri yönlendirmeli ve ortak bağlamı iletmelidir; medya çıkarma işi mevcut ya da ayrı bir adaptörde kalmalıdır.

URL eşleştirme, çıktı adlandırma, hata yönetimi ve metadata davranışı için çevrimdışı testler ekleyin. Rutin testleri kimlik bilgilerine, tarayıcı profillerine veya canlı servis erişimine bağımlı yapmayın.

## Kapsam ve güvenlik

EasyScrape yukarı akış çıkarıcıların yeteneklerini kullanır; DRM'yi, ücretli duvarları veya erişim kısıtlarını aşmaz. Bir servisin erişim korumalarını dolanan kod eklemeyin. Çerez verisini gizli tutun: yalnızca yerelde kullanın, asla günlüğe yazmayın, örnek verilere veya raporlara koymayın. Telemetri göndermeme davranışını koruyun.
