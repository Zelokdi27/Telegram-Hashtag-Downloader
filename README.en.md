# Telegram Hashtag Downloader

[Русский](README.ru.md) · [Home](README.md)

Desktop app for searching posts by hashtag and downloading media from **public Telegram channels**.

**Version:** 1.0.0  
**Platform:** Windows 10/11 (primary). Source install may work on other OS with manual setup.  
**Author:** Zelokdi · Telegram: [@Zelokdi](https://t.me/Zelokdi)

---

## Disclaimer

This is an **unofficial** client built on the [Telegram API](https://core.telegram.org/api) and [Telethon](https://github.com/LonamiWebs/Telethon).

- You need your own **API ID** and **API Hash** from [my.telegram.org/apps](https://my.telegram.org/apps).
- Respect Telegram [Terms of Service](https://telegram.org/tos) and rate limits.
- Heavy or automated use may trigger **FloodWait** or account restrictions.
- The author is not affiliated with Telegram.

---

## Two ways to run

| Audience | What to use |
|---|---|
| Most users | **Portable Windows build** — folder with `TelegramHashtagDownloader.exe` |
| Developers / transparency | **Source code** — Python 3.10+ and `pip install -r requirements.txt` |

Settings, sessions and downloads live in a `data/` folder next to the executable (or project root in source mode).

---

## Quick start (Windows .exe)

1. Download `TelegramHashtagDownloader-v1.0.0-win64.zip` from [Releases](https://github.com/Zelokdi27/Telegram-Hashtag-Downloader/releases).
2. Extract to any folder (e.g. `C:\Apps\TelegramHashtagDownloader\`).
3. Run `TelegramHashtagDownloader.exe`.
4. Complete the **setup wizard**: language & theme → welcome → API keys → login → download folder.
5. On the **Home** tab enter a hashtag and use **Preview** or **Download**.

On first launch the app creates:

- `.env` — settings
- `data/sessions/` — Telegram session
- `data/downloads/` — downloaded files
- `data/logs/` — application log

---

## Install from source

**Requirements:** Python 3.10+, Windows recommended.

```bash
git clone https://github.com/Zelokdi27/Telegram-Hashtag-Downloader.git
cd telegram-hashtag-downloader
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Optional CLI:

```bash
python main.py --cli --hashtag mytag
python main.py --cli --hashtag mytag --verify
```

### Development / tests

```bash
pip install -r requirements-dev.txt
pytest
```

---

## Telegram API keys

1. Open [my.telegram.org/apps](https://my.telegram.org/apps).
2. Create an application and copy **API ID** and **API Hash**.
3. Paste them in **Settings** or the setup wizard.

Keys are stored locally in `.env` on your machine only.

---

## Main features

- Hashtag search with filters (channel, date, media type, limits)
- **Preview** with thumbnails, disk status, duplicates, album support
- Queue of multiple hashtags
- Step-by-step preview for large archives
- Download journal — skip already downloaded files
- Russian and English UI
- Dark theme, Windows notifications

---

## Project layout

```
app/           Core logic (download, preview, auth, i18n)
qt_ui/         PySide6 interface
locales/       ru.json, en.json
main.py        Entry point (GUI or --cli)
data/          Runtime data (created automatically)
packaging/     PyInstaller spec
scripts/       build_release.ps1
```

---

## License

[MIT](LICENSE) — free to use, modify and distribute with copyright notice preserved.
