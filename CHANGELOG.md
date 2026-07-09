# Changelog

All notable changes to this project are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-07-09

### Added

- First public release.
- GUI: hashtag search, preview, download queue, settings, setup wizard.
- Preview: streaming cards, filters during load, disk/partial/duplicate badges, color legend.
- Step-by-step preview mode for large channel archives.
- Performance autotune (benchmark + apply recommendations).
- Russian and English localization.
- Windows portable build via PyInstaller (`scripts/build_release.ps1`).
- Crash dumps, download journal, integrity verify, flood-wait handling.
- CLI mode: `python main.py --cli --hashtag TAG`.

### Fixed

- Session status text no longer stuck on "Checking…" after language change.
- Album photo order in preview (1/N … N/N instead of reversed).

[1.0.0]: https://github.com/Zelokdi27/Telegram-Hashtag-Downloader/releases/tag/v1.0.0
