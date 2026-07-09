"""Install paths tests · Тесты путей установки"""

from __future__ import annotations

from app.i18n import LOCALES_DIR
from app.paths import BUNDLE_DIR, PROJECT_DIR, app_root, bundle_root
from app.version import __version__


def test_version_is_semver_like() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_project_and_bundle_roots_exist() -> None:
    assert app_root().is_dir()
    assert bundle_root().is_dir()
    assert PROJECT_DIR == app_root()
    assert BUNDLE_DIR == bundle_root()


def test_locales_bundled_next_to_code() -> None:
    assert (LOCALES_DIR / "ru.json").is_file()
    assert (LOCALES_DIR / "en.json").is_file()
