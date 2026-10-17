# Geliştirme durumu

Proje, `src/` düzenli, Python 3.10+ setuptools tabanlı bir pakettir. Komut satırı `easyscrape`, arayüz `easyscrape-gui` adıyla kurulur. Bağımlılıklar `pyproject.toml` içinde tanımlıdır; `yt-dlp[default,curl-cffi]` ekstrası uyumlu `yt-dlp-ejs` paketini ve TikTok için `curl-cffi`'yi getirir.

## Yerel kontroller

```sh
python -m pip install -e ".[dev]"
python -m pytest
ruff check .
mypy src/easyscrape
python -m build
```

Son çalıştırma (Windows, Python 3.13): 225 test geçti, 19 atlandı (çoğu Linux/symlink'e özgü); Ruff ve mypy temiz.

## Canlı doğrulama (Windows, çerezsiz)

Çalışanlar: YouTube (4K dahil, `--audio-only` dahil), YouTube Music bağlantıları, Spotify parça/albüm/playlist, X video, Instagram reel (herkese açık), TikTok video, Pinterest görsel ve video, SoundCloud, archive.org, Wikimedia Commons, `--audio-format mp3`, `failed.json` ile yeniden deneme ve arayüzden indirme.

Sınırlar: giriş gerektiren veya yaş kısıtlı Instagram/Facebook/Threads içeriği, bu ağda Reddit, TikTok fotoğraf slaytları, 100'den uzun Spotify playlistlerinin sonrası, Vimeo/Dailymotion/Bilibili giriş veya bölge kısıtları. Siteler değiştikçe yt-dlp / gallery-dl sürümlerine bağlı olarak davranış değişebilir.

CI matrisi (Linux, Windows, macOS; Python 3.10 ve 3.12) tanımlıdır ancak GitHub Actions üzerinde çalıştırılana kadar doğrulanmış sayılmaz.

## JavaScript çalışma ortamı

YouTube için Deno ya da desteklenen başka bir JavaScript çalışma ortamı ayrıca kurulmalı ve `PATH` üzerinde olmalıdır; bkz. [Windows medya araçları](README.md#windows-medya-araçları).
