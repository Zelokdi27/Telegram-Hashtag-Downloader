"""Merge log + progress.detail strings into locale JSON files."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATCH_EN = {
    "log": {
        "settings": {
            "saved": "Settings saved.",
            "save_failed": "Settings not saved: {exc}",
            "save_error": "Failed to save settings: {exc}",
            "theme_failed": "Failed to save theme: {exc}",
        },
        "task": {
            "paused": "Task paused.",
            "resumed": "Task resumed.",
            "waiting_stop": "Waiting for task to stop…",
        },
        "crash": {
            "dump_found": "Diagnostic dump from previous run found: {path}",
        },
        "worker": {
            "integrity_refs": "Top-up missing: {n} post(s)",
            "queue_list": "Queue: {tags}",
            "date_filter": "Date filter: from {date_from} to {date_to}",
            "execution_error": "Execution error: {exc}",
            "running_as": "Running as: {user}",
            "integrity_finished": "Integrity check finished.",
            "topup_cancelled": "Top-up cancelled.",
            "topup_nothing": "Nothing selected for top-up.",
            "sequential_cancelled": "Step-by-step preview cancelled.",
            "sequential_nothing": "Step-by-step preview: nothing downloaded.",
            "no_preview_posts": "No posts with media for preview.",
            "preview_cancelled": "Preview cancelled.",
            "nothing_selected": "Nothing selected for download.",
        },
        "auth": {
            "session_reset": "Session reset. Files removed: {files}",
            "session_none": "none",
            "journal_reset": "Download journals reset: {files}",
            "journal_not_found": "no files found",
            "login_start": "Starting Telegram sign-in…",
            "qr_start": "Starting QR sign-in…",
        },
        "preview_flow": {
            "loading_media": "Loading preview media ({n})…",
            "dialog_error": "Preview window error",
            "dialog_timeout": "Preview dialog did not open in time",
            "sequential_dialog_error": "Step-by-step preview window error",
            "sequential_dialog_timeout": "Step-by-step preview dialog did not open in time",
            "batch_skipped": "Batch {n} skipped",
            "batch_memory_freed": "Batch {n}: preview memory freed",
            "parallel_thumbs": "Parallel thumbnail loading while collecting cards…",
            "list_ready": "Preview list ready: {n} media files",
            "cancelled_by_user": "Preview cancelled by user ({n} media files collected)",
        },
        "download": {
            "parallel_workers": "Parallel download: up to {n} concurrent downloads",
            "sequential": "Download: sequential (1 thread)",
            "saved": "Downloaded: {path}",
            "hash_duplicate": "Hash duplicate, using: {path}",
            "file_error": "Failed to save file for post {id}: {exc}",
            "telegram_error": "Telegram error for post {id}: {exc}",
            "channel_flood": "FloodWait getting channel for post {id}: wait {sec} sec.",
            "channel_retry_failed": "Failed to get channel for post {id}: {exc}",
            "channel_failed": "Failed to get channel for post {id}: {exc}",
            "neighbors_failed": "Failed to read neighboring posts {id}: {exc}",
            "album_failed": "Failed to get album {id}: {exc}",
            "album_redownload": "Album top-up: {on_disk} of {total} slots on disk — post {id}",
            "redownload": "Re-download: files missing — post {id}",
        },
        "search": {
            "in_channel": "Searching #{tag} inside channel (not global)…",
            "channel_flood": "FloodWait during channel search: wait {sec} sec.",
            "date_boundary": "Lower date boundary reached — search complete.",
            "flood_wait": "FloodWait: wait {sec} sec.",
            "channel_done": "Channel search complete: {messages} messages, {media} media",
            "global_found": "Found (global search): {messages} messages, {media} media",
        },
        "integrity": {
            "channel_not_found": "Could not find channel «{key}» for top-up",
        },
        "preview": {
            "albums_fetched": "Fetched {n} albums for preview (in place, before showing cards)",
            "optimize_failed": "Preview optimization {source}: {exc}",
            "refresh_failed": "Refresh post {id} for original: {exc}",
            "original_failed": "Original for post {id}: {error}",
            "preview_flood": "FloodWait during preview post {id}: wait {sec} sec.",
            "preview_thumb_failed": "Preview for post {id} (thumb={thumb}): {exc}",
            "pool_shutdown_failed": "Failed to shut down preview pool cleanly: {exc}",
            "original_shown": "Post {id} showing {label} (full file unavailable)",
        },
        "auth_telegram": {
            "broken_session": "Broken session on DC{dc} — clearing",
            "dc_pick": "Telegram requests DC{dc} — picking working server…",
            "dc_switch": "Telegram requests switch to DC{dc}",
            "code_after_migration": "Code requested after migration: {type}",
            "connecting": "Connecting to Telegram…",
            "already_authorized": "Already authorized as: {user}",
            "code_request": "Requesting code for {phone}…",
            "code_resend": "Resending code…",
            "login_success": "Sign-in successful: {user}",
            "connecting_qr": "Connecting to Telegram (QR)…",
            "qr_waiting": "QR code shown, waiting for scan…",
            "qr_success": "QR sign-in successful: {user}",
            "login_error": "Sign-in error",
            "qr_login_error": "QR sign-in error",
        },
        "notify": {
            "app_model_failed": "AppUserModelID not set",
            "toast_failed": "PowerShell toast failed",
            "tray_fallback_failed": "Tray fallback failed",
        },
        "state": {
            "journal_migrated": "Journal migrated to SQLite: {name} (backup: {backup})",
            "journal_rename_failed": "Failed to rename JSON journal {path}: {exc}",
        },
        "disk": {
            "index_sidecar": "Disk index from sidecar: {root} ({n} id)",
            "index_rebuilt": "Disk index rebuilt: {root} ({n} id)",
        },
        "control": {
            "flood_wait": "FloodWait: wait {sec} sec.",
        },
        "crash_dump": {
            "written": "Diagnostic dump written: {path}",
            "stale_heartbeat": "Incomplete previous run detected, dump written: {path}",
            "write_failed": "Failed to write crash dump",
            "write_failed_thread": "Failed to write crash dump from thread",
        },
        "history": {
            "read_failed": "Hashtag history not read ({target}): {exc}",
        },
        "cli": {
            "logged_in": "Signed in as: {user}",
            "no_env": "No .env file found. Create it via GUI.",
            "integrity_for": "Integrity check for #{tag}",
            "hashtag": "Hashtag: #{tag}",
            "stopped": "Stopped by user.",
            "done": "Done. Posts with media: {posts}, files: {files}, skipped: {skipped}",
            "need_gui_login": "Sign in to Telegram via the GUI first (Sign in or QR).",
            "need_hashtag": "Specify a hashtag: python main.py --cli --hashtag NAME",
            "description": "Global hashtag search in Telegram and media download.",
            "help_cli": "Run in console instead of GUI",
            "help_hashtag": "Hashtag without #",
            "help_verify": "Integrity check without downloading",
        },
        "none": "—",
        "unknown_error": "Unknown error",
    },
}

PATCH_RU = {
    "log": {
        "settings": {
            "saved": "Настройки сохранены.",
            "save_failed": "Настройки не сохранены: {exc}",
            "save_error": "Не удалось сохранить настройки: {exc}",
            "theme_failed": "Не удалось сохранить тему: {exc}",
        },
        "task": {
            "paused": "Задача поставлена на паузу.",
            "resumed": "Задача возобновлена.",
            "waiting_stop": "Ожидание остановки задачи…",
        },
        "crash": {
            "dump_found": "Обнаружен диагностический дамп предыдущего запуска: {path}",
        },
        "worker": {
            "integrity_refs": "Докачка недостающих: {n} пост(ов)",
            "queue_list": "Очередь: {tags}",
            "date_filter": "Фильтр по дате: с {date_from} по {date_to}",
            "execution_error": "Ошибка выполнения: {exc}",
            "running_as": "Работаем как: {user}",
            "integrity_finished": "Проверка целостности завершена.",
            "topup_cancelled": "Докачка отменена.",
            "topup_nothing": "Ничего не выбрано для докачки.",
            "sequential_cancelled": "Пошаговый предпросмотр отменён.",
            "sequential_nothing": "Пошаговый предпросмотр: ничего не скачано.",
            "no_preview_posts": "Нет постов с медиа для предпросмотра.",
            "preview_cancelled": "Предпросмотр отменён.",
            "nothing_selected": "Ничего не выбрано для скачивания.",
        },
        "auth": {
            "session_reset": "Сессия сброшена. Удалено файлов: {files}",
            "session_none": "нет",
            "journal_reset": "Журналы скачивания сброшены: {files}",
            "journal_not_found": "файлы не найдены",
            "login_start": "Запуск входа в Telegram…",
            "qr_start": "Запуск входа по QR…",
        },
        "preview_flow": {
            "loading_media": "Загрузка превью медиа ({n})…",
            "dialog_error": "Ошибка окна предпросмотра",
            "dialog_timeout": "Диалог предпросмотра не открылся вовремя",
            "sequential_dialog_error": "Ошибка окна пошагового предпросмотра",
            "sequential_dialog_timeout": "Диалог пошагового предпросмотра не открылся вовремя",
            "batch_skipped": "Партия {n} пропущена",
            "batch_memory_freed": "Партия {n}: память превью освобождена",
            "parallel_thumbs": "Параллельная загрузка миниатюр при сборе карточек…",
            "list_ready": "Список превью готов: {n} медиафайлов",
            "cancelled_by_user": "Предпросмотр отменён пользователем ({n} медиафайлов собрано)",
        },
        "download": {
            "parallel_workers": "Параллельное скачивание: до {n} одновременных загрузок",
            "sequential": "Скачивание: последовательно (1 поток)",
            "saved": "Скачано: {path}",
            "hash_duplicate": "Дубликат по хешу, используем: {path}",
            "file_error": "Не удалось сохранить файл для поста {id}: {exc}",
            "telegram_error": "Ошибка Telegram для поста {id}: {exc}",
            "channel_flood": "FloodWait при получении канала для поста {id}: ждём {sec} сек.",
            "channel_retry_failed": "Не удалось получить канал для поста {id}: {exc}",
            "channel_failed": "Не удалось получить канал для поста {id}: {exc}",
            "neighbors_failed": "Не удалось прочитать соседние посты {id}: {exc}",
            "album_failed": "Не удалось получить альбом {id}: {exc}",
            "album_redownload": "Докачка альбома: на диске {on_disk} из {total} слотов — пост {id}",
            "redownload": "Повторное скачивание: файлы отсутствуют — пост {id}",
        },
        "search": {
            "in_channel": "Поиск #{tag} внутри канала (не глобально)…",
            "channel_flood": "FloodWait при поиске в канале: ждём {sec} сек.",
            "date_boundary": "Достигнута нижняя граница даты — поиск завершён.",
            "flood_wait": "FloodWait: ждём {sec} сек.",
            "channel_done": "Поиск в канале завершён: {messages} сообщений, {media} медиа",
            "global_found": "Найдено (глобальный поиск): {messages} сообщений, {media} медиа",
        },
        "integrity": {
            "channel_not_found": "Не удалось найти канал «{key}» для докачки",
        },
        "preview": {
            "albums_fetched": "Догружено {n} альбомов для превью (на месте, до показа карточек)",
            "optimize_failed": "Оптимизация превью {source}: {exc}",
            "refresh_failed": "Обновление поста {id} для оригинала: {exc}",
            "original_failed": "Оригинал для поста {id}: {error}",
            "preview_flood": "FloodWait при превью поста {id}: ждём {sec} сек.",
            "preview_thumb_failed": "Превью для поста {id} (thumb={thumb}): {exc}",
            "pool_shutdown_failed": "Не удалось корректно остановить пул превью: {exc}",
            "original_shown": "Для поста {id} показана {label} (полный файл недоступен)",
        },
        "auth_telegram": {
            "broken_session": "Битая сессия на DC{dc} — очищаем",
            "dc_pick": "Telegram просит DC{dc} — подбираем рабочий сервер…",
            "dc_switch": "Telegram просит перейти на DC{dc}",
            "code_after_migration": "Код запрошен после миграции: {type}",
            "connecting": "Подключение к Telegram…",
            "already_authorized": "Уже авторизованы как: {user}",
            "code_request": "Запрос кода для номера {phone}…",
            "code_resend": "Повторная отправка кода…",
            "login_success": "Успешный вход: {user}",
            "connecting_qr": "Подключение к Telegram (QR)…",
            "qr_waiting": "QR-код показан, ожидание сканирования…",
            "qr_success": "Успешный вход по QR: {user}",
            "login_error": "Ошибка входа",
            "qr_login_error": "Ошибка входа по QR",
        },
        "notify": {
            "app_model_failed": "AppUserModelID не задан",
            "toast_failed": "PowerShell toast не удался",
            "tray_fallback_failed": "Tray fallback не удался",
        },
        "state": {
            "journal_migrated": "Журнал мигрирован в SQLite: {name} (резерв: {backup})",
            "journal_rename_failed": "Не удалось переименовать JSON-журнал {path}: {exc}",
        },
        "disk": {
            "index_sidecar": "Индекс диска из sidecar: {root} ({n} id)",
            "index_rebuilt": "Индекс диска пересобран: {root} ({n} id)",
        },
        "control": {
            "flood_wait": "FloodWait: ждём {sec} сек.",
        },
        "crash_dump": {
            "written": "Записан диагностический дамп: {path}",
            "stale_heartbeat": "Обнаружен незавершённый запуск, записан дамп: {path}",
            "write_failed": "Не удалось записать crash dump",
            "write_failed_thread": "Не удалось записать crash dump из потока",
        },
        "history": {
            "read_failed": "История хештегов не прочитана ({target}): {exc}",
        },
        "cli": {
            "logged_in": "Вошли как: {user}",
            "no_env": "Файл .env не найден. Создайте его через GUI.",
            "integrity_for": "Проверка целостности для #{tag}",
            "hashtag": "Хештег: #{tag}",
            "stopped": "Остановлено пользователем.",
            "done": "Готово. Постов с медиа: {posts}, файлов: {files}, пропущено: {skipped}",
            "need_gui_login": "Сначала войдите в Telegram через GUI (Войти в Telegram или QR).",
            "need_hashtag": "Укажите хештег: python main.py --cli --hashtag ИМЯ",
            "description": "Глобальный поиск постов по хештегу в Telegram и скачивание медиа.",
            "help_cli": "Запуск через консоль вместо GUI",
            "help_hashtag": "Хештег без #",
            "help_verify": "Проверка целостности без скачивания",
        },
        "none": "—",
        "unknown_error": "Неизвестная ошибка",
    },
}

PROGRESS_DETAIL_EN = {
    "checking_disk": "Checking files on disk…",
    "verified_posts": "Verified {done} of {total} posts",
    "verify_done": "Verification complete",
    "batch_start": "Starting #{tag}…",
    "album_done": "Album already processed: {index}/{total}",
    "skip_on_disk": "Skip (already on disk): {index}/{total}",
    "skip_no_media": "Skip (no media): {index}/{total}",
    "skip": "Skip: {index}/{total}",
    "file_error": "File error: post {id}",
    "post_error": "Post {id} error: {notice}",
    "post_error_continue": "Post {id} error, continuing…",
    "parse_albums": "Parsing albums before download…",
    "prep_download": "Preparing download…",
    "done_new_files": "Done · new files on disk: {n}",
    "downloading_file": "Downloading: {filename}",
    "paused": "Paused — click «Resume» to continue",
    "flood_wait": "Waiting for Telegram (FloodWait): {sec} sec…",
    "search_scope_media": "{scope}: {used}/{limit} media",
    "search_scope_messages": "{scope}: {messages} messages",
    "search_in_channel": "Searching in channel…",
    "search_posts": "Searching posts…",
    "stopped": "Stopped",
    "scope_global": "Global search",
    "scope_channel": "Channel search",
}

PROGRESS_DETAIL_RU = {
    "checking_disk": "Проверка файлов на диске…",
    "verified_posts": "Проверено {done} из {total} постов",
    "verify_done": "Проверка завершена",
    "batch_start": "Запуск #{tag}…",
    "album_done": "Альбом уже обработан: {index}/{total}",
    "skip_on_disk": "Пропуск (уже на диске): {index}/{total}",
    "skip_no_media": "Пропуск (без медиа): {index}/{total}",
    "skip": "Пропуск: {index}/{total}",
    "file_error": "Ошибка файла: пост {id}",
    "post_error": "Ошибка поста {id}: {notice}",
    "post_error_continue": "Ошибка поста {id}, продолжаем…",
    "parse_albums": "Разбор альбомов перед скачиванием…",
    "prep_download": "Подготовка к скачиванию…",
    "done_new_files": "Готово · новых файлов на диске: {n}",
    "downloading_file": "Скачивание: {filename}",
    "paused": "На паузе — нажмите «Возобновить», чтобы продолжить",
    "flood_wait": "Ожидание Telegram (FloodWait): {sec} сек…",
    "search_scope_media": "{scope}: {used}/{limit} медиа",
    "search_scope_messages": "{scope}: {messages} сообщений",
    "search_in_channel": "Поиск в канале…",
    "search_posts": "Поиск постов…",
    "stopped": "Остановлено",
    "scope_global": "Глобальный поиск",
    "scope_channel": "Поиск в канале",
}


def _deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def main() -> None:
    for lang, patch in (("en", PATCH_EN), ("ru", PATCH_RU)):
        path = ROOT / "locales" / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        _deep_merge(data, patch)
        detail = PROGRESS_DETAIL_EN if lang == "en" else PROGRESS_DETAIL_RU
        data.setdefault("progress", {})
        data["progress"].setdefault("detail", {})
        _deep_merge(data["progress"]["detail"], detail)
        # worker.log templates: use {tag} instead of %s
        wl = data.setdefault("worker", {}).setdefault("log", {})
        if lang == "en":
            wl.update({
                "once": "Starting one-time download for #{tag}",
                "verify": "Starting integrity check for #{tag}",
                "preview": "Starting preview for #{tag}",
                "integrity": "Downloading missing files for #{tag}",
                "queue": "Starting queue download: {n} hashtags",
                "task": "Starting task for #{tag}",
                "integrity_count": "Top-up missing: {n} post(s)",
            })
        else:
            wl.update({
                "once": "Запуск разового скачивания для #{tag}",
                "verify": "Запуск проверки целостности для #{tag}",
                "preview": "Запуск предпросмотра для #{tag}",
                "integrity": "Докачка недостающих файлов для #{tag}",
                "queue": "Запуск очереди скачивания: {n} хештегов",
                "task": "Запуск задачи для #{tag}",
                "integrity_count": "Докачка недостающих: {n} пост(ов)",
            })
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {path}")


if __name__ == "__main__":
    main()
