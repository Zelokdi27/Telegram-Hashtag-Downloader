# Telegram Hashtag Downloader

[English](README.en.md) · [Главная](README.md)

Десктопное приложение для поиска постов по хештегу и скачивания медиа из **публичных каналов** Telegram.

**Версия:** 1.0.0  
**Платформа:** Windows 10/11 (основная). Из исходников может запускаться и на других ОС с ручной настройкой.  
**Автор:** Zelokdi · связь в Telegram: [@Zelokdi](https://t.me/Zelokdi)

---

## Важно

Это **неофициальный** клиент на базе [Telegram API](https://core.telegram.org/api) и [Telethon](https://github.com/LonamiWebs/Telethon).

- Нужны свои **API ID** и **API Hash** с [my.telegram.org/apps](https://my.telegram.org/apps).
- Соблюдайте [правила Telegram](https://telegram.org/tos) и лимиты запросов.
- Частые или агрессивные загрузки могут вызвать **FloodWait** или ограничения аккаунта.
- Автор не связан с Telegram.

---

## Два способа запуска

| Кому | Что использовать |
|---|---|
| Большинству пользователей | **Portable-сборка Windows** — папка с `TelegramHashtagDownloader.exe` |
| Разработчикам / прозрачность | **Исходники** — Python 3.10+ и `pip install -r requirements.txt` |

Настройки, сессия и загрузки хранятся в папке `data/` рядом с exe (или в корне проекта при запуске из исходников).

---

## Быстрый старт (Windows .exe)

1. Скачайте архив релиза `TelegramHashtagDownloader-v1.0.0-win64.zip` со [страницы Releases](https://github.com/Zelokdi27/Telegram-Hashtag-Downloader/releases).
2. Распакуйте в любую папку (например `C:\Apps\TelegramHashtagDownloader\`).
3. Запустите `TelegramHashtagDownloader.exe`.
4. Пройдите **мастер настройки**: язык и тема → приветствие → API-ключи → вход → папка загрузок.
5. На вкладке **Главная** введите хештег и нажмите **Предпросмотр** или **Скачать**.

При первом запуске создаются:

- `.env` — настройки
- `data/sessions/` — сессия Telegram
- `data/downloads/` — скачанные файлы
- `data/logs/` — журнал приложения

---

## Установка из исходников

**Требования:** Python 3.10+, рекомендуется Windows.

```bash
git clone https://github.com/Zelokdi27/Telegram-Hashtag-Downloader.git
cd telegram-hashtag-downloader
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

CLI (опционально):

```bash
python main.py --cli --hashtag mytag
python main.py --cli --hashtag mytag --verify
```

### Разработка и тесты

```bash
pip install -r requirements-dev.txt
pytest
```

---

## Ключи Telegram API

1. Откройте [my.telegram.org/apps](https://my.telegram.org/apps).
2. Создайте приложение и скопируйте **API ID** и **API Hash**.
3. Вставьте в **Настройки** или в мастере первого запуска.

Ключи хранятся только локально в `.env` на вашем компьютере.

---

## Основные возможности

- Поиск по хештегу с фильтрами (канал, даты, тип медиа, лимиты)
- **Предпросмотр** с миниатюрами, статусом «на диске», дубликатами, альбомами
- Очередь из нескольких хештегов
- Пошаговый предпросмотр для больших архивов
- Журнал скачивания — пропуск уже скачанного
- Интерфейс на русском и английском
- Тёмная тема, уведомления Windows

---

## Структура проекта

```
app/           Логика (скачивание, превью, авторизация, i18n)
qt_ui/         Интерфейс PySide6
locales/       ru.json, en.json
main.py        Точка входа (GUI или --cli)
data/          Данные приложения (создаётся автоматически)
packaging/     Спецификация PyInstaller
scripts/       build_release.ps1
```

---

## Лицензия

[MIT](LICENSE) — можно свободно использовать, изменять и распространять с сохранением копирайта.
