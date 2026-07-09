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

Используйте на свой риск.

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

> Windows SmartScreen может предупредить о неизвестном издателе — это нормально для неподписанного exe. Подробнее в разделе «Подпись» ниже.

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
- **Проверка производительности** (autotune) — рекомендации по потокам и партиям
- Журнал скачивания — пропуск уже скачанного
- Интерфейс на русском и английском
- Тёмная тема, уведомления Windows

---

## Сборка .exe для Windows

На машине с Python:

```powershell
.\scripts\build_release.ps1
```

Результат: `dist\TelegramHashtagDownloader\` — эту папку zipуете для Releases.

Перед публикацией релиза:

1. Укажите автора в `app/version.py` (`APP_AUTHOR`, `APP_CONTACT_TELEGRAM`, `APP_URL`).
2. Обновите версию в `app/version.py`.
3. Запустите `pytest`.
4. Проверьте exe на **чистом** ПК без Python.
5. Выложите zip на GitHub Release с тегом `v1.0.0`.

### Подпись exe (опционально)

Платный сертификат code signing уменьшает предупреждения SmartScreen. Самоподписанный сертификат для публики почти не помогает.

Бесплатные метки автора уже в сборке: свойства файла exe и «О программе…» в настройках.

```powershell
$env:SIGN_PFX_PATH = "C:\path\to\cert.pfx"
$env:SIGN_PFX_PASSWORD = "password"
.\scripts\build_release.ps1
.\scripts\sign_release.ps1
```

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

Сторонние библиотеки: Telethon, PySide6, Pillow и др. — см. их лицензии.
