"""Crash dump · Диагностика аварийного завершения"""

from __future__ import annotations

import json
import logging
import platform
import sys
import threading
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_store import LOGS_DIR
from .download_options import batch_search_count
from .i18n import tr
from .search_form import SearchFormSnapshot, snapshot_from_mapping

logger = logging.getLogger(__name__)

DUMP_VERSION = 1
APP_NAME = "telegram-hashtag-downloader"
LAST_RUN_PATH = LOGS_DIR / "last_run.json"
CRASHES_DIR = LOGS_DIR / "crashes"
HEARTBEAT_INTERVAL_SEC = 3.0
MAX_CRASH_FILES = 10
MAX_STACK_LINES = 30

_prev_excepthook = sys.excepthook
_prev_threading_excepthook = getattr(threading, "excepthook", None)


@dataclass
class CrashStartupInfo:
    dump_path: Path
    summary: str
    detail: str
    form_snapshot: dict[str, Any] | None


def snapshot_to_mapping(snapshot: SearchFormSnapshot) -> dict[str, Any]:
    return asdict(snapshot)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def format_dump_datetime(value: str) -> str:
    """Dump datetime · ISO дампа для UI"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return text.replace("T", " ").split("+")[0].split("Z")[0].strip()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _truncate_stack(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_STACK_LINES:
        return lines
    head = MAX_STACK_LINES - 1
    return [*lines[:head], f"... ({len(lines) - head} строк скрыто)"]


def _fault_from_traceback(tb: traceback.StackSummary | None) -> str:
    if not tb:
        return ""
    for frame in reversed(tb):
        filename = frame.filename.replace("\\", "/")
        if "/app/" in filename or filename.endswith(".py"):
            module = Path(filename).stem
            return f"{module}.{frame.name}"
    if tb:
        frame = tb[-1]
        return f"{Path(frame.filename).name}.{frame.name}"
    return ""


def _batch_info(form: dict[str, Any]) -> dict[str, int]:
    tag_n, ch_n, total = batch_search_count(
        str(form.get("hashtag", "") or ""),
        str(form.get("extra_hashtags", "") or ""),
        str(form.get("channel_filter", "") or ""),
        str(form.get("extra_channels", "") or ""),
    )
    return {"tags": tag_n, "channels": ch_n, "total": total}


def _app_meta() -> dict[str, str]:
    return {
        "name": APP_NAME,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _progress_mapping(progress: Any | None) -> dict[str, Any]:
    if progress is None:
        return {}
    fields = (
        "phase",
        "found",
        "total",
        "processed",
        "files",
        "media_total",
        "skipped",
        "current",
    )
    result: dict[str, Any] = {}
    for name in fields:
        value = getattr(progress, name, None)
        if value is not None and value != "" and value != 0:
            result[name] = value
    if getattr(progress, "flood_wait_deadline", 0.0):
        result["flood_wait"] = True
    return result


class CrashRecorder:
    """Crash recorder · Heartbeat и дампы в data/logs/crashes"""

    def __init__(self, *, entry: str = "gui") -> None:
        self._entry = entry
        self._lock = threading.Lock()
        self._worker_mode = ""
        self._form_snapshot: dict[str, Any] = {}
        self._batch_active: dict[str, str] = {}
        self._last_progress: dict[str, Any] = {}
        self._last_heartbeat_monotonic = 0.0
        self._heartbeat_started_at = ""
        self._running = False

    def begin_session(
        self,
        *,
        worker_mode: str,
        form_snapshot: SearchFormSnapshot | dict[str, Any],
    ) -> None:
        if isinstance(form_snapshot, SearchFormSnapshot):
            form_data = snapshot_to_mapping(form_snapshot)
        else:
            form_data = dict(form_snapshot)
        with self._lock:
            self._worker_mode = worker_mode
            self._form_snapshot = form_data
            self._batch_active = {}
            self._last_progress = {}
            self._running = True
            self._heartbeat_started_at = _utc_now_iso()
            self._last_heartbeat_monotonic = 0.0
            self._write_heartbeat_locked(status="running")

    def update_batch_active(self, hashtag: str, channel: str) -> None:
        with self._lock:
            if not self._running:
                return
            self._batch_active = {
                "hashtag": hashtag,
                "channel": channel or "",
            }
            self._write_heartbeat_locked(status="running", force=True)

    def note_progress(self, progress: Any | None = None) -> None:
        """Progress snapshot · Снимок прогресса в памяти"""
        with self._lock:
            if not self._running or progress is None:
                return
            mapped = _progress_mapping(progress)
            if mapped:
                self._last_progress = mapped

    def heartbeat(self, progress: Any | None = None, *, force: bool = False) -> None:
        with self._lock:
            if not self._running:
                return
            if progress is not None:
                mapped = _progress_mapping(progress)
                if mapped:
                    self._last_progress = mapped
            now = __import__("time").monotonic()
            if not force and now - self._last_heartbeat_monotonic < HEARTBEAT_INTERVAL_SEC:
                return
            self._write_heartbeat_locked(status="running", force=force)

    def finish_ok(self) -> None:
        with self._lock:
            self._running = False
            self._remove_last_run_locked()

    def write_crash(
        self,
        exc: BaseException | None = None,
        *,
        code: str,
        summary: str | None = None,
    ) -> Path | None:
        with self._lock:
            payload = self._build_dump_locked(
                kind="crash",
                code=code,
                exc=exc,
                summary=summary,
            )
            path = self._write_crash_file_locked(payload)
            self._running = False
            self._remove_last_run_locked()
            return path

    def write_crash_from_exc(self, exc: BaseException, *, code: str) -> Path | None:
        return self.write_crash(exc, code=code)

    def _build_dump_locked(
        self,
        *,
        kind: str,
        code: str,
        exc: BaseException | None,
        summary: str | None,
    ) -> dict[str, Any]:
        stack_lines: list[str] = []
        fault = ""
        if exc is not None:
            stack_lines = _truncate_stack(
                traceback.format_exception(type(exc), exc, exc.__traceback__),
            )
            fault = _fault_from_traceback(traceback.extract_tb(exc.__traceback__))
            if summary is None:
                summary = f"{type(exc).__name__}: {exc}"
        summary = summary or code

        session: dict[str, Any] = {
            "entry": self._entry,
            "worker_mode": self._worker_mode,
            "form_snapshot": self._form_snapshot,
            "batch": _batch_info(self._form_snapshot),
        }
        if self._batch_active:
            session["batch_active"] = dict(self._batch_active)

        payload: dict[str, Any] = {
            "dump_version": DUMP_VERSION,
            "kind": kind,
            "status": "running" if kind == "heartbeat" else "crash",
            "created_at": _utc_now_iso(),
            "app": _app_meta(),
            "bugcheck": {
                "code": code,
                "summary": summary,
                "fault": fault,
                "stack": stack_lines,
            },
            "session": session,
            "progress": dict(self._last_progress),
        }
        if kind == "heartbeat":
            payload["heartbeat_started_at"] = self._heartbeat_started_at
        return payload

    def _write_heartbeat_locked(self, *, status: str, force: bool = False) -> None:
        import time

        now = time.monotonic()
        if not force and now - self._last_heartbeat_monotonic < HEARTBEAT_INTERVAL_SEC:
            return
        payload = self._build_dump_locked(kind="heartbeat", code="", exc=None, summary="")
        payload["status"] = status
        _atomic_write_json(LAST_RUN_PATH, payload)
        self._last_heartbeat_monotonic = now

    def _write_crash_file_locked(self, payload: dict[str, Any]) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = CRASHES_DIR / f"crash_{stamp}.json"
        suffix = 1
        while path.exists():
            path = CRASHES_DIR / f"crash_{stamp}_{suffix}.json"
            suffix += 1
        _atomic_write_json(path, payload)
        _rotate_crash_files()
        logger.warning(tr("log.crash_dump.written", path=path))
        return path

    def _remove_last_run_locked(self) -> None:
        try:
            LAST_RUN_PATH.unlink(missing_ok=True)
        except OSError:
            pass


def _rotate_crash_files() -> None:
    if not CRASHES_DIR.is_dir():
        return
    files = sorted(
        CRASHES_DIR.glob("crash_*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old in files[MAX_CRASH_FILES:]:
        try:
            old.unlink()
        except OSError:
            pass


_active_recorder: CrashRecorder | None = None


def set_active_recorder(recorder: CrashRecorder | None) -> None:
    global _active_recorder
    _active_recorder = recorder


def get_active_recorder() -> CrashRecorder | None:
    return _active_recorder


def install_crash_hooks() -> None:
    def main_excepthook(exc_type, exc, tb) -> None:
        if exc_type is KeyboardInterrupt:
            if _prev_excepthook:
                _prev_excepthook(exc_type, exc, tb)
            return
        if _active_recorder is not None and exc is not None:
            try:
                _active_recorder.write_crash(exc, code="MAIN_THREAD_EXCEPTION")
            except Exception:
                logger.exception(tr("log.crash_dump.write_failed"))
        if _prev_excepthook:
            _prev_excepthook(exc_type, exc, tb)

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if _active_recorder is not None and args.exc_value is not None:
            try:
                _active_recorder.write_crash(args.exc_value, code="THREAD_EXCEPTION")
            except Exception:
                logger.exception(tr("log.crash_dump.write_failed_thread"))
        if _prev_threading_excepthook is not None:
            _prev_threading_excepthook(args)

    sys.excepthook = main_excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_excepthook  # type: ignore[attr-defined]


def promote_stale_heartbeat() -> Path | None:
    """Stale heartbeat · ABRUPT_EXIT при обрыве без finish_ok"""
    payload = _read_json(LAST_RUN_PATH)
    if not payload or payload.get("status") != "running":
        return None

    started = payload.get("heartbeat_started_at") or payload.get("created_at")
    age_sec = None
    if started:
        try:
            started_dt = datetime.fromisoformat(str(started))
            age_sec = max(0.0, (datetime.now(started_dt.tzinfo) - started_dt).total_seconds())
        except ValueError:
            age_sec = None

    crash_payload = dict(payload)
    crash_payload.update(
        {
            "kind": "crash",
            "status": "crash",
            "created_at": _utc_now_iso(),
            "bugcheck": {
                "code": "ABRUPT_EXIT",
                "summary": tr("crash.abrupt"),
                "fault": "",
                "stack": [],
            },
        },
    )
    if age_sec is not None:
        crash_payload["heartbeat_age_sec"] = round(age_sec, 1)

    CRASHES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = CRASHES_DIR / f"crash_{stamp}.json"
    _atomic_write_json(path, crash_payload)
    try:
        LAST_RUN_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    _rotate_crash_files()
    logger.warning(tr("log.crash_dump.stale_heartbeat", path=path))
    return path


def load_crash_dump(path: Path) -> dict[str, Any] | None:
    return _read_json(path)


def format_crash_banner(dump: dict[str, Any]) -> tuple[str, str]:
    bugcheck = dump.get("bugcheck") or {}
    session = dump.get("session") or {}
    progress = dump.get("progress") or {}
    form = session.get("form_snapshot") or {}

    code = str(bugcheck.get("code") or "UNKNOWN")
    summary = str(bugcheck.get("summary") or tr("log.unknown_error"))
    created = format_dump_datetime(str(dump.get("created_at") or ""))
    mode = str(session.get("worker_mode") or tr("log.none"))
    hashtag = str(form.get("hashtag") or tr("log.none"))
    channel = str(form.get("channel_filter") or tr("crash.all_channels"))

    mode_label = tr(f"crash.mode.{mode}", default=mode)

    progress_bits: list[str] = []
    if progress.get("phase"):
        progress_bits.append(tr("crash.progress.phase", phase=progress["phase"]))
    if progress.get("current"):
        progress_bits.append(str(progress["current"]))
    elif progress.get("processed") and progress.get("total"):
        progress_bits.append(
            tr(
                "crash.progress.processed",
                processed=progress["processed"],
                total=progress["total"],
            ),
        )
    progress_text = " · ".join(progress_bits) if progress_bits else tr("log.none")

    title = tr("crash.title", date=created) if created else tr("crash.title_plain")
    body = tr(
        "crash.detail",
        mode=mode_label,
        tag=hashtag,
        channel=channel,
        progress=progress_text,
    )
    if code != "UNKNOWN" or summary:
        detail = f"{code}: {summary}\n{body}"
    else:
        detail = body
    return title, detail


def startup_crash_info() -> CrashStartupInfo | None:
    path = promote_stale_heartbeat()
    if path is None:
        return None
    dump = load_crash_dump(path)
    if not dump:
        return None
    title, detail = format_crash_banner(dump)
    session = dump.get("session") or {}
    form = session.get("form_snapshot")
    return CrashStartupInfo(
        dump_path=path,
        summary=title,
        detail=detail,
        form_snapshot=form if isinstance(form, dict) else None,
    )