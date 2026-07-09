#!/usr/bin/env python3
"""CLI entry · Точка входа CLI"""

from __future__ import annotations

import argparse
import logging

from app.win_asyncio import fix_windows_asyncio

fix_windows_asyncio()

from app.config_store import ENV_EXAMPLE_PATH, ENV_PATH, build_app_config, load_settings, session_path_for
from app.crash_dump import CrashRecorder, install_crash_hooks, set_active_recorder
from app.i18n import set_locale, tr
from app.search_form import snapshot_from_settings
from app.download_options import parse_channel_list, parse_hashtag_list
from app.telethon_loop import run_async
from app.telegram_auth import adisconnect_quietly, aconnect_client, make_client
from app.tg_hashtag_dl import IntegrityStats
from app.tg_hashtag_dl import (
    HashtagDownloader,
    format_download_summary,
    format_integrity_summary,
    merge_integrity_stats,
    normalize_hashtag,
)


def parse_args() -> argparse.Namespace:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--cli", action="store_true")
    pre_args, _ = pre.parse_known_args()
    if pre_args.cli:
        set_locale(load_settings().ui_language)

    parser = argparse.ArgumentParser(
        description=tr("log.cli.description"),
    )
    parser.add_argument("--cli", action="store_true", help=tr("log.cli.help_cli"))
    parser.add_argument("--hashtag", "-t", help=tr("log.cli.help_hashtag"))
    parser.add_argument("--verify", action="store_true", help=tr("log.cli.help_verify"))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


async def _acli_run(settings, config, *, verify: bool) -> None:
    session_path = session_path_for(config.session_name)
    client = make_client(session_path, config.api_id, config.api_hash, settings)
    try:
        error = await aconnect_client(client)
        if error:
            raise SystemExit(error.error)

        if not await client.is_user_authorized():
            raise SystemExit(tr("log.cli.need_gui_login"))

        me = await client.get_me()
        logging.info(tr("log.cli.logged_in", user=getattr(me, "username", None) or me.id))

        hashtags = parse_hashtag_list(settings.hashtag, settings.extra_hashtags)
        channels = parse_channel_list(settings.channel_filter, settings.extra_channels) or [""]
        multi_batch = len(hashtags) > 1 or len(channels) > 1

        downloader = HashtagDownloader(client, config)

        if verify:
            if multi_batch:
                combined = IntegrityStats(download_dir=str(config.download_dir))
                for tag in hashtags:
                    for channel in channels:
                        batch_config = build_app_config(
                            settings,
                            hashtag=tag,
                            channel_filter=channel,
                        )
                        worker = HashtagDownloader(client, batch_config)
                        merge_integrity_stats(combined, await worker.verify_integrity())
                print(format_integrity_summary(combined))
            else:
                print(format_integrity_summary(await downloader.verify_integrity()))
        else:
            if multi_batch:
                stats = await downloader.run_batch(hashtags, channels)
            else:
                stats = await downloader.run_once()
            logging.info(
                tr(
                    "log.cli.done",
                    posts=stats.posts,
                    files=stats.files,
                    skipped=stats.skipped,
                ),
            )
            print(format_download_summary(stats))
    finally:
        await adisconnect_quietly(client)


def run_cli(hashtag: str | None, *, verify: bool = False) -> None:
    install_crash_hooks()
    recorder = CrashRecorder(entry="cli")
    set_active_recorder(recorder)

    settings = load_settings()
    if hashtag:
        settings.hashtag = normalize_hashtag(hashtag)
    config = build_app_config(settings, hashtag=settings.hashtag)

    worker_mode = "verify" if verify else "once"
    recorder.begin_session(
        worker_mode=worker_mode,
        form_snapshot=snapshot_from_settings(settings),
    )

    try:
        run_async(_acli_run(settings, config, verify=verify))
    except SystemExit:
        raise
    except Exception as exc:
        recorder.write_crash(exc, code="CLI_EXCEPTION")
        raise
    finally:
        recorder.finish_ok()


def main() -> None:
    args = parse_args()
    if args.cli:
        logging.basicConfig(
            level=getattr(logging, args.log_level),
            format="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        )

        settings = load_settings()
        set_locale(settings.ui_language)

        if not ENV_PATH.exists():
            logging.warning(tr("log.cli.no_env"))

        if args.hashtag:
            settings.hashtag = normalize_hashtag(args.hashtag)
        if not settings.hashtag.strip():
            raise SystemExit(tr("log.cli.need_hashtag"))

        if args.verify:
            logging.info(tr("log.cli.integrity_for", tag=settings.hashtag))
        else:
            logging.info(tr("log.cli.hashtag", tag=settings.hashtag))

        try:
            run_cli(settings.hashtag, verify=args.verify)
        except KeyboardInterrupt:
            logging.info(tr("log.cli.stopped"))
        return

    from gui_qt import run_gui

    run_gui()


if __name__ == "__main__":
    main()