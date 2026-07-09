"""Merge remaining i18n keys into locale JSON files."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATCHES = {
    "en": {
        "auth": {
            "login": {
                "dc_switch_failed": "Failed to switch to DC{dc}",
            },
        },
        "errors": {
            "channel_resolve": "Channel «{name}» not found. Specify @username or a t.me/… link",
            "preview_channel_unknown": "Could not determine channel for post {id}",
        },
        "sequential": {
            "title": "Step-by-step preview: {batch}",
            "batch_approx": "batch {n} of ~{total}",
            "batch_exact": "batch {n} of {total}",
            "media_limit": "media {shown}/{limit}",
            "media_at_least": "media {shown}, in search ≥{estimate}",
            "media_estimate": "media {shown}/{estimate}",
            "publications": "publications {cursor}/{total}",
            "downloaded": "downloaded {n}",
        },
        "preview_flow": {
            "indexing_tag": "Indexing #{tag}{channel}: {n} publications with media",
            "sequential_start": "Step-by-step preview: {publications} publications, {media}",
            "sequential_media_limit": "limit {n} media files",
            "sequential_media_at_least": "at least {n} media files",
            "sequential_media_count": "{n} media files",
            "batch_downloaded": "Batch {n}: downloaded {files} files (total {total})",
            "opening_preview": "Found {n} posts for #{tag}, opening preview…",
        },
        "progress": {
            "detail": {
                "download_progress": (
                    "Processed {accounted}/{goal} · new files: {files} · publications {posts}/{pubs}"
                ),
            },
        },
        "log": {
            "auth_telegram": {
                "dc_try": "Trying DC{dc} ({ip}) {mode} ipv6={ipv6}…",
                "dc_connected": "Connected via DC{dc} ({ip}) {mode}",
                "dc_fail": "DC{dc} ({ip}) {mode}: {exc}",
                "phone_dc_switch": "Number {phone} → DC{target} (current DC{current}), switching early…",
                "code_requested_detail": "Code requested: {type}, resend in {timeout} sec.",
                "dc_unreachable": "DC{dc} unreachable",
            },
            "download": {
                "post_failed": "Failed to process post {id}: {type}: {exc}",
                "album_expanded": "Album {id}: loaded {n} frames (expected {expected})",
                "album_short": "Album {id}: Telegram returned {n} of {expected} frames",
                "retry": "Attempt {attempt}/{attempts} for post {id}: {exc}",
                "retry_exc": "Attempt {attempt}/{attempts} for post {id}: {type}: {exc}",
            },
            "dl_utils": {
                "hash_skip_large": "Skipping hash dedup for large file ({mb} MB): {name}",
            },
            "integrity": {
                "channel_mode": (
                    "Mode: searching #{tag} INSIDE channel «{title}» (@{filter}) — not global Telegram"
                ),
                "global_search": "Global search #{tag} across all public Telegram channels…",
                "totals": "Total: found {found}, after filters {filtered} (with media: {media})",
            },
            "preview_index": {
                "fetch_failed": "Failed to load post {id} ({tag}): {exc}",
            },
        },
    },
    "ru": {
        "auth": {
            "login": {
                "dc_switch_failed": "Не удалось переключиться на DC{dc}",
            },
        },
        "errors": {
            "channel_resolve": "Канал «{name}» не найден. Укажите @username или ссылку t.me/…",
            "preview_channel_unknown": "Не удалось определить канал для поста {id}",
        },
        "sequential": {
            "title": "Пошаговый предпросмотр: {batch}",
            "batch_approx": "партия {n} из ~{total}",
            "batch_exact": "партия {n} из {total}",
            "media_limit": "медиа {shown}/{limit}",
            "media_at_least": "медиа {shown}, в поиске ≥{estimate}",
            "media_estimate": "медиа {shown}/{estimate}",
            "publications": "публикаций {cursor}/{total}",
            "downloaded": "скачано {n}",
        },
        "preview_flow": {
            "indexing_tag": "Индексация #{tag}{channel}: {n} публикаций с медиа",
            "sequential_start": "Пошаговый предпросмотр: {publications} публикаций, {media}",
            "sequential_media_limit": "лимит {n} медиафайлов",
            "sequential_media_at_least": "не менее {n} медиафайлов",
            "sequential_media_count": "{n} медиафайлов",
            "batch_downloaded": "Партия {n}: скачано {files} файлов (всего {total})",
            "opening_preview": "Найдено {n} постов для #{tag}, открываем превью…",
        },
        "progress": {
            "detail": {
                "download_progress": (
                    "Обработано {accounted}/{goal} · новых файлов: {files} · публикаций {posts}/{pubs}"
                ),
            },
        },
        "log": {
            "auth_telegram": {
                "dc_try": "Пробуем DC{dc} ({ip}) {mode} ipv6={ipv6}…",
                "dc_connected": "Подключено через DC{dc} ({ip}) {mode}",
                "dc_fail": "DC{dc} ({ip}) {mode}: {exc}",
                "phone_dc_switch": "Номер {phone} → DC{target} (сейчас DC{current}), переключаемся заранее…",
                "code_requested_detail": "Код запрошен: {type}, повтор через {timeout} сек.",
                "dc_unreachable": "DC{dc} недоступен",
            },
            "download": {
                "post_failed": "Не удалось обработать пост {id}: {type}: {exc}",
                "album_expanded": "Альбом {id}: догружено {n} кадров (ожидалось {expected})",
                "album_short": "Альбом {id}: Telegram отдал {n} из {expected} кадров",
                "retry": "Попытка {attempt}/{attempts} для поста {id}: {exc}",
                "retry_exc": "Попытка {attempt}/{attempts} для поста {id}: {type}: {exc}",
            },
            "dl_utils": {
                "hash_skip_large": "Пропуск хеш-дедупа для большого файла ({mb} МБ): {name}",
            },
            "integrity": {
                "channel_mode": (
                    "Режим: поиск #{tag} ВНУТРИ канала «{title}» (@{filter}) — не по всему Telegram"
                ),
                "global_search": "Глобальный поиск #{tag} по всем публичным каналам Telegram…",
                "totals": "Итого: найдено {found}, после фильтров {filtered} (с медиа: {media})",
            },
            "preview_index": {
                "fetch_failed": "Не удалось загрузить пост {id} ({tag}): {exc}",
            },
        },
    },
}


def _deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def main() -> None:
    for lang, patch in PATCHES.items():
        path = ROOT / "locales" / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        _deep_merge(data, patch)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {path}")


if __name__ == "__main__":
    main()
