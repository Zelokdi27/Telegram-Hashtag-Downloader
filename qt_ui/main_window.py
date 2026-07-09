"""Main window · Главное окно"""

from __future__ import annotations

import logging
import math
from logging.handlers import RotatingFileHandler
import queue
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QPushButton,
    QProgressBar,
    QPlainTextEdit,
    QSizePolicy,
    QScrollArea,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

from app.config_store import (
    AUTOTUNE_PROFILE_PATH,
    LOG_FILE,
    LOGS_DIR,
    STATE_DIR,
    SettingsData,
    build_app_config,
    load_settings,
    migrate_data_layout,
    resolve_download_dir,
    safe_int,
    save_settings,
    session_path_for,
)
from app.version import __version__
from app.autotune import (
    format_autotune_summary,
    load_autotune_profile,
    profile_matches_settings,
    run_autotune_sync,
    save_autotune_profile,
)
from app.crash_dump import (
    CRASHES_DIR,
    HEARTBEAT_INTERVAL_SEC,
    CrashRecorder,
    CrashStartupInfo,
    install_crash_hooks,
    set_active_recorder,
    startup_crash_info,
)
from app.search_form import (
    apply_snapshot_to_settings,
    delete_named_template,
    empty_snapshot,
    load_named_templates,
    rename_named_template,
    snapshot_from_mapping,
    snapshot_from_settings,
    template_exists,
    upsert_named_template,
)
from app.download_options import format_batch_search_hint, parse_hashtag_list
from app.progress_coalesce import ProgressCoalescer
from app.hashtag_history import record_hashtags_used
from app.hashtag_queue import load_hashtag_queue, normalize_hashtag_queue, save_hashtag_queue
from app.queue_progress import format_batch_progress_label, queue_overall_percent
from app.tg_hashtag_dl import (
    DownloadStats,
    HashtagDownloader,
    IntegrityStats,
    ProgressState,
    normalize_hashtag,
    safe_name,
)
from app.i18n import set_locale, tr
from app.win_notify import configure_win_notifications, notifications_available, set_tray_fallback, show_win_notification

from .hashtag_history_ui import HashtagHistoryCompleter, HashtagLineEdit
from .bridge import MainThreadInvoker
from .auth_panel import AuthPanelMixin
from .preview_flow import PreviewFlowMixin
from .task_spec import TaskSpec
from .worker_controller import WorkerController
from .collapsible import CollapsibleGroupBox
from .date_widgets import OptionalDatePicker
from .drop_list_line_edit import DropListLineEdit
from .sliding_progress import SlidingProgressBar
from .spinbox_utils import NoWheelSpinBox
from .dialogs import (
    MainThreadPrompter,
    ask_yes_no,
    open_path_in_file_manager,
    show_about_dialog,
    show_autotune_result_dialog,
    show_error,
    show_info,
    show_warning,
)
from .theme import build_stylesheet, palette_for
from .win_chrome import restore_top_level_window, set_titlebar_dark

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATEFMT = "%H:%M:%S"
LOG_VIEW_MAX_BLOCKS = 4000
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
class QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue[str]) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            self.handleError(record)


class HashtagDownloaderWindow(PreviewFlowMixin, AuthPanelMixin, QMainWindow):
    def __init__(self, *, startup_session_check: bool = False) -> None:
        super().__init__()
        self._set_startup_session_check(startup_session_check)
        self._section_groups: dict[str, QGroupBox] = {}
        self._form_labels: dict[str, QLabel] = {}
        self._hint_labels: dict[str, QLabel] = {}
        self._static_labels: dict[str, QLabel] = {}
        self._loading_fields = False
        self._active_preview_dialog = None

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.task_paused = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self._worker_loop = None
        self.login_thread: threading.Thread | None = None
        self._login_cancel = threading.Event()
        self._active_login_client: object | None = None
        self.worker_mode = ""
        self._pending_download_stats: DownloadStats | None = None
        self._pending_integrity_stats: IntegrityStats | None = None
        self._integrity_download_refs: list = []
        self.settings = load_settings(include_session=False)
        self._log_autoscroll = True
        self.auth_username = ""
        self.is_logged_in = False
        self._last_auth_result = None
        self._auth_status_mode = "checking"

        self._invoker = MainThreadInvoker(self)
        self._worker_ctrl = WorkerController(self)
        self._progress_coalescer = ProgressCoalescer(
            lambda state: self._invoker.run(lambda s=state: self._update_progress(s)),
        )
        self.prompter = MainThreadPrompter(self, self._invoker)
        self._login_prompter: MainThreadPrompter | None = None

        self._dark_theme = self.settings.dark_theme
        self._palette = palette_for(dark=self._dark_theme)
        self._last_progress_alert = ""
        self._progress_detail_snapshot = ""
        self._last_progress_state: ProgressState | None = None
        self._queue_download_active = False
        self._pending_crash_info: CrashStartupInfo | None = None
        self._tray_icon: QSystemTrayIcon | None = None
        self._autotune_profile = load_autotune_profile(AUTOTUNE_PROFILE_PATH)
        self._autotune_thread: threading.Thread | None = None
        self._autotune_queue: queue.Queue[tuple] = queue.Queue()
        self._autotune_running = False

        migrate_data_layout()
        install_crash_hooks()
        self._crash_recorder = CrashRecorder(entry="gui")
        set_active_recorder(self._crash_recorder)

        self._setup_logging()
        self._build_ui()
        self._setup_win_notifications()
        self._apply_theme()
        self._load_fields()
        self._set_running_state(False)
        if not startup_session_check:
            self._refresh_auth_status()
        self._check_crash_startup()

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            w = min(980, int(geo.width() * 0.82))
            h = min(900, int(geo.height() * 0.88))
            self.resize(w, h)
            self.move(geo.x() + (geo.width() - w) // 2, geo.y() + (geo.height() - h) // 2)

        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._poll_log)
        self._log_timer.start(200)
        self._autotune_timer = QTimer(self)
        self._autotune_timer.timeout.connect(self._poll_autotune_queue)
        self._autotune_timer.start(150)

        self._flood_timer = QTimer(self)
        self._flood_timer.setInterval(250)
        self._flood_timer.timeout.connect(self._tick_flood_countdown)

        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(int(HEARTBEAT_INTERVAL_SEC * 1000))
        self._heartbeat_timer.timeout.connect(self._tick_crash_heartbeat)

    def _setup_logging(self) -> None:
        formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)
        queue_handler = QueueLogHandler(self.log_queue)
        queue_handler.setFormatter(formatter)
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.addHandler(queue_handler)
        root_logger.addHandler(file_handler)
        root_logger.setLevel(logging.INFO)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)

        header = QHBoxLayout()
        self._brand_label = QLabel()
        self._brand_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        header.addWidget(self._brand_label)
        header.addStretch()
        self.theme_check = QCheckBox()
        self.theme_check.setChecked(self._dark_theme)
        self.theme_check.toggled.connect(self._on_theme_toggle)
        header.addWidget(self.theme_check)
        root.addLayout(header)

        self._subtitle_label = QLabel()
        self._subtitle_label.setObjectName("muted")
        root.addWidget(self._subtitle_label)

        self._build_crash_banner(root)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, stretch=1)

        self._build_main_tab()
        self._build_settings_tab()
        self._build_log_tab()

        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-weight: bold;")
        root.addWidget(self.status_label)

        self._retranslate_ui()

    def _scroll_tab(self) -> tuple[QWidget, QVBoxLayout]:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        outer.addWidget(scroll)
        return tab, layout

    def _section(self, parent_layout: QVBoxLayout, key: str) -> QVBoxLayout:
        group = QGroupBox()
        self._section_groups[key] = group
        inner = QVBoxLayout(group)
        parent_layout.addWidget(group)
        return inner

    def _hint(self, layout: QVBoxLayout, key: str) -> None:
        label = QLabel()
        label.setObjectName("muted")
        label.setWordWrap(True)
        layout.addWidget(label)
        self._hint_labels[key] = label

    def _form_row(self, layout: QVBoxLayout, key: str, widget: QWidget) -> None:
        row = QHBoxLayout()
        lbl = QLabel()
        lbl.setMinimumWidth(120)
        self._form_labels[key] = lbl
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        layout.addLayout(row)

    def _static_label(self, key: str) -> QLabel:
        label = QLabel()
        self._static_labels[key] = label
        return label

    def _build_main_tab(self) -> None:
        tab, layout = self._scroll_tab()

        self._collapse_hint_label = QLabel()
        self._collapse_hint_label.setObjectName("muted")
        self._collapse_hint_label.setWordWrap(True)
        layout.addWidget(self._collapse_hint_label)

        auth = self._section(layout, "auth")
        auth_row = QHBoxLayout()
        self.auth_busy_label = QLabel("")
        self.auth_busy_label.setObjectName("accent")
        self.auth_busy_label.setVisible(False)
        auth_row.addWidget(self.auth_busy_label)
        self.auth_status_label = QLabel()
        auth_row.addWidget(self.auth_status_label, stretch=1)
        self.login_btn = QPushButton()
        self.login_btn.clicked.connect(self._start_login)
        self.qr_login_btn = QPushButton()
        self.qr_login_btn.clicked.connect(self._start_qr_login)
        self.reset_session_btn = QPushButton()
        self.reset_session_btn.clicked.connect(self._reset_session)
        auth_row.addWidget(self.login_btn)
        auth_row.addWidget(self.qr_login_btn)
        auth_row.addWidget(self.reset_session_btn)
        auth.addLayout(auth_row)
        self.auth_hint_label = QLabel()
        self.auth_hint_label.setObjectName("muted")
        self.auth_hint_label.setWordWrap(True)
        auth.addWidget(self.auth_hint_label)

        search_sec = self._section(layout, "search")
        hashtag_row = QHBoxLayout()
        hashtag_row.addWidget(QLabel("#"))
        self.hashtag_entry = HashtagLineEdit()
        hashtag_row.addWidget(self.hashtag_entry, stretch=1)
        search_sec.addLayout(hashtag_row)
        self._hashtag_history = HashtagHistoryCompleter(self.hashtag_entry, self)

        template_row = QHBoxLayout()
        template_row.addWidget(self._static_label("template"))
        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(180)
        self.template_combo.currentIndexChanged.connect(self._on_template_selected)
        template_row.addWidget(self.template_combo, stretch=1)
        self.save_template_btn = QPushButton()
        self.save_template_btn.clicked.connect(self._save_search_template)
        self.rename_template_btn = QPushButton()
        self.rename_template_btn.clicked.connect(self._rename_search_template)
        self.delete_template_btn = QPushButton()
        self.delete_template_btn.clicked.connect(self._delete_search_template)
        self.clear_form_btn = QPushButton()
        self.clear_form_btn.clicked.connect(self._clear_search_form)
        template_row.addWidget(self.save_template_btn)
        template_row.addWidget(self.rename_template_btn)
        template_row.addWidget(self.delete_template_btn)
        template_row.addWidget(self.clear_form_btn)
        search_sec.addLayout(template_row)
        self._hint(search_sec, "template")

        queue_sec = self._section(layout, "queue")
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(96)
        queue_sec.addWidget(self.queue_list)
        queue_btns = QHBoxLayout()
        self.queue_add_btn = QPushButton()
        self.queue_add_btn.clicked.connect(self._queue_add_current)
        self.queue_remove_btn = QPushButton()
        self.queue_remove_btn.clicked.connect(self._queue_remove_selected)
        self.queue_up_btn = QPushButton()
        self.queue_up_btn.clicked.connect(lambda: self._queue_move_selected(-1))
        self.queue_down_btn = QPushButton()
        self.queue_down_btn.clicked.connect(lambda: self._queue_move_selected(1))
        self.queue_clear_btn = QPushButton()
        self.queue_clear_btn.clicked.connect(self._queue_clear)
        self.queue_download_btn = QPushButton()
        self.queue_download_btn.clicked.connect(self._start_queue_download)
        for btn in (
            self.queue_add_btn,
            self.queue_remove_btn,
            self.queue_up_btn,
            self.queue_down_btn,
            self.queue_clear_btn,
            self.queue_download_btn,
        ):
            queue_btns.addWidget(btn)
        queue_sec.addLayout(queue_btns)
        self._hint(queue_sec, "queue")
        self._reload_hashtag_queue_ui()

        actions = self._section(layout, "actions")
        self.once_btn = QPushButton()
        self.once_btn.clicked.connect(self._start_once)
        self.preview_btn = QPushButton()
        self.preview_btn.clicked.connect(self._start_preview)
        self.verify_btn = QPushButton()
        self.verify_btn.clicked.connect(self._start_verify)
        self.stop_btn = QPushButton()
        self.stop_btn.setObjectName("danger")
        self.stop_btn.clicked.connect(self._stop_worker)
        self.pause_btn = QPushButton()
        self.pause_btn.clicked.connect(self._click_pause)
        self.pause_btn.setEnabled(False)
        self.open_downloads_btn = QPushButton()
        self.open_downloads_btn.clicked.connect(self._open_downloads)
        self.reset_journal_btn = QPushButton()
        self.reset_journal_btn.clicked.connect(self._reset_download_journal)

        btn_grid = QGridLayout()
        btn_grid.setHorizontalSpacing(8)
        btn_grid.setVerticalSpacing(8)
        for col in range(3):
            btn_grid.setColumnStretch(col, 1)
        action_buttons = (
            self.once_btn,
            self.preview_btn,
            self.verify_btn,
            self.pause_btn,
            self.stop_btn,
            self.open_downloads_btn,
            self.reset_journal_btn,
        )
        for btn in action_buttons:
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for index, btn in enumerate(action_buttons[:6]):
            btn_grid.addWidget(btn, index // 3, index % 3)
        btn_grid.addWidget(self.reset_journal_btn, 2, 0, 1, 3)
        actions.addLayout(btn_grid)

        filters = self._section(layout, "filters")
        dates_row = QHBoxLayout()
        dates_row.addWidget(self._static_label("date_from"))
        self.date_from_picker = OptionalDatePicker()
        dates_row.addWidget(self.date_from_picker)
        dates_row.addWidget(self._static_label("date_to"))
        self.date_to_picker = OptionalDatePicker()
        dates_row.addWidget(self.date_to_picker)
        dates_row.addStretch()
        filters.addLayout(dates_row)
        self.date_hint_label = QLabel()
        self.date_hint_label.setObjectName("muted")
        self.date_hint_label.setWordWrap(True)
        filters.addWidget(self.date_hint_label)
        self.date_filter_label = QLabel("")
        self.date_filter_label.setObjectName("muted")
        self.date_filter_label.setWordWrap(True)
        self.date_filter_label.setVisible(False)
        filters.addWidget(self.date_filter_label)
        self.date_from_picker.date_changed.connect(self._update_filter_hint)
        self.date_to_picker.date_changed.connect(self._update_filter_hint)

        self.channel_filter_entry = QLineEdit()
        self._form_row(filters, "channel", self.channel_filter_entry)
        self._hint(filters, "channel")

        self.filter_max_posts_spin = NoWheelSpinBox()
        self.filter_max_posts_spin.setRange(0, 999_999)
        self._form_row(filters, "max_media", self.filter_max_posts_spin)
        self._hint(filters, "max_media")

        preview_mode_row = QHBoxLayout()
        self.sequential_preview_check = QCheckBox()
        self.sequential_preview_check.toggled.connect(self._sync_preview_batch_controls)
        preview_mode_row.addWidget(self.sequential_preview_check)
        preview_mode_row.addWidget(self._static_label("batch_size"))
        self.preview_batch_spin = NoWheelSpinBox()
        self.preview_batch_spin.setRange(20, 1000)
        self.preview_batch_spin.setValue(200)
        preview_mode_row.addWidget(self.preview_batch_spin)
        preview_mode_row.addStretch()
        filters.addLayout(preview_mode_row)
        self._hint(filters, "sequential")

        extra_filters = CollapsibleGroupBox(
            "",
            start_collapsed=True,
            tool_tip="",
        )
        self._extra_filters_box = extra_filters
        extra_layout = extra_filters.content_layout()
        layout.addWidget(extra_filters)

        self.extra_hashtags_entry = DropListLineEdit(for_hashtags=True)
        self._form_row(extra_layout, "extra_hashtags", self.extra_hashtags_entry)
        self._hint(extra_layout, "extra_hashtags")

        self.required_hashtags_entry = QLineEdit()
        self._form_row(extra_layout, "required", self.required_hashtags_entry)
        self._hint(extra_layout, "required")

        self.exclude_hashtags_entry = QLineEdit()
        self._form_row(extra_layout, "exclude", self.exclude_hashtags_entry)
        self._hint(extra_layout, "exclude")

        self.extra_channels_entry = DropListLineEdit(for_hashtags=False)
        self._form_row(extra_layout, "extra_channels", self.extra_channels_entry)
        self._hint(extra_layout, "extra_channels")
        self.batch_hint_label = QLabel("")
        self.batch_hint_label.setObjectName("muted")
        self.batch_hint_label.setWordWrap(True)
        extra_layout.addWidget(self.batch_hint_label)
        for entry in (
            self.hashtag_entry,
            self.extra_hashtags_entry,
            self.channel_filter_entry,
            self.extra_channels_entry,
        ):
            entry.textChanged.connect(self._update_batch_hint)

        media_grid = QGridLayout()
        self.media_photo_check = QCheckBox()
        self.media_video_check = QCheckBox()
        self.media_animation_check = QCheckBox()
        self.media_audio_check = QCheckBox()
        self.media_document_check = QCheckBox()
        for i, cb in enumerate(
            (
                self.media_photo_check,
                self.media_video_check,
                self.media_animation_check,
                self.media_audio_check,
                self.media_document_check,
            ),
        ):
            media_grid.addWidget(cb, i // 3, i % 3)
        extra_layout.addLayout(media_grid)

        progress = self._section(layout, "progress")
        self.progress_phase_label = QLabel()
        self.progress_phase_label.setStyleSheet("font-weight: bold;")
        progress.addWidget(self.progress_phase_label)
        self.progress_bar = SlidingProgressBar()
        progress.addWidget(self.progress_bar)
        self.queue_batch_bar = QProgressBar()
        self.queue_batch_bar.setMaximum(100)
        self.queue_batch_bar.setTextVisible(True)
        self.queue_batch_bar.setFixedHeight(18)
        self.queue_batch_bar.setVisible(False)
        progress.addWidget(self.queue_batch_bar)
        self.queue_progress_label = QLabel("")
        self.queue_progress_label.setObjectName("muted")
        self.queue_progress_label.setWordWrap(True)
        self.queue_progress_label.setVisible(False)
        progress.addWidget(self.queue_progress_label)
        self.progress_flood_label = QLabel("")
        self.progress_flood_label.setObjectName("flood_wait")
        self.progress_flood_label.setWordWrap(True)
        self.progress_flood_label.setVisible(False)
        progress.addWidget(self.progress_flood_label)
        self.progress_detail_label = QLabel()
        self.progress_detail_label.setObjectName("muted")
        self.progress_detail_label.setWordWrap(True)
        progress.addWidget(self.progress_detail_label)
        self.progress_stats_label = QLabel("")
        self.progress_stats_label.setObjectName("muted")
        progress.addWidget(self.progress_stats_label)

        layout.addStretch()
        self._main_tab = tab
        self.tabs.addTab(tab, "")

    def _build_settings_tab(self) -> None:
        tab, layout = self._scroll_tab()

        search_prefs = self._section(layout, "search_prefs")
        self.remember_last_search_check = QCheckBox()
        search_prefs.addWidget(self.remember_last_search_check)
        self._hint(search_prefs, "remember_form")

        lang_row = QHBoxLayout()
        self._language_label = QLabel()
        lang_row.addWidget(self._language_label)
        self.language_combo = QComboBox()
        self.language_combo.addItem("", "system")
        self.language_combo.addItem("", "ru")
        self.language_combo.addItem("", "en")
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_row.addWidget(self.language_combo, stretch=1)
        search_prefs.addLayout(lang_row)

        api = self._section(layout, "api")
        self._hint(api, "api")
        self.api_id_entry = QLineEdit()
        self._form_row(api, "api_id", self.api_id_entry)
        self.api_hash_entry = QLineEdit()
        self._form_row(api, "api_hash", self.api_hash_entry)
        self.page_limit_spin = NoWheelSpinBox()
        self.page_limit_spin.setRange(1, 100)
        self._form_row(api, "page_limit", self.page_limit_spin)
        self._hint(api, "page_limit")

        paths = self._section(layout, "files")
        dir_row = QHBoxLayout()
        self.download_dir_entry = QLineEdit()
        self._browse_btn = QPushButton()
        self._browse_btn.clicked.connect(self._browse_download_dir)
        dir_row.addWidget(self._static_label("download_dir"))
        dir_row.addWidget(self.download_dir_entry, stretch=1)
        dir_row.addWidget(self._browse_btn)
        paths.addLayout(dir_row)
        self.session_name_entry = QLineEdit()
        self._form_row(paths, "session_name", self.session_name_entry)

        proxy = self._section(layout, "proxy")
        self.proxy_enabled_check = QCheckBox()
        self.proxy_enabled_check.toggled.connect(self._sync_proxy_fields_enabled)
        proxy.addWidget(self.proxy_enabled_check)
        self.proxy_type_entry = QLineEdit()
        self._form_row(proxy, "proxy_type", self.proxy_type_entry)
        self.proxy_host_entry = QLineEdit()
        self._form_row(proxy, "proxy_host", self.proxy_host_entry)
        self.proxy_port_spin = NoWheelSpinBox()
        self.proxy_port_spin.setRange(1, 65535)
        self._form_row(proxy, "proxy_port", self.proxy_port_spin)
        self._hint(proxy, "proxy")

        files = self._section(layout, "naming")
        self.folder_by_date_check = QCheckBox()
        files.addWidget(self.folder_by_date_check)
        self.caption_in_filename_check = QCheckBox()
        files.addWidget(self.caption_in_filename_check)
        self.caption_max_len_spin = NoWheelSpinBox()
        self.caption_max_len_spin.setRange(0, 80)
        self._form_row(files, "caption_max_len", self.caption_max_len_spin)
        self.dedup_by_hash_check = QCheckBox()
        files.addWidget(self.dedup_by_hash_check)

        performance = self._section(layout, "performance")
        self.preview_parallel_spin = NoWheelSpinBox()
        self.preview_parallel_spin.setRange(1, 6)
        self._form_row(performance, "preview_threads", self.preview_parallel_spin)
        self.download_parallel_spin = NoWheelSpinBox()
        self.download_parallel_spin.setRange(1, 3)
        self._form_row(performance, "download_threads", self.download_parallel_spin)
        self._hint(performance, "performance")
        perf_row = QHBoxLayout()
        self._autotune_run_btn = QPushButton()
        self._autotune_run_btn.clicked.connect(self._start_autotune_check)
        perf_row.addWidget(self._autotune_run_btn)
        self._autotune_apply_btn = QPushButton()
        self._autotune_apply_btn.clicked.connect(self._apply_autotune_recommendations)
        perf_row.addWidget(self._autotune_apply_btn)
        perf_row.addStretch()
        performance.addLayout(perf_row)
        self._autotune_summary_label = QLabel()
        self._autotune_summary_label.setObjectName("muted")
        self._autotune_summary_label.setWordWrap(True)
        performance.addWidget(self._autotune_summary_label)

        reliability = self._section(layout, "reliability")
        self.download_retries_spin = NoWheelSpinBox()
        self.download_retries_spin.setRange(0, 10)
        self._form_row(reliability, "retries", self.download_retries_spin)

        notify = self._section(layout, "notify")
        self.win_notify_enabled_check = QCheckBox()
        notify.addWidget(self.win_notify_enabled_check)
        self.win_notify_success_check = QCheckBox()
        notify.addWidget(self.win_notify_success_check)
        self.win_notify_errors_check = QCheckBox()
        notify.addWidget(self.win_notify_errors_check)
        self._hint(notify, "notify")

        wizard_row = QHBoxLayout()
        self._wizard_btn = QPushButton()
        self._wizard_btn.clicked.connect(self._show_setup_wizard)
        wizard_row.addWidget(self._wizard_btn)
        self._about_btn = QPushButton()
        self._about_btn.clicked.connect(self._show_about_dialog)
        wizard_row.addWidget(self._about_btn)
        wizard_row.addStretch()
        layout.addLayout(wizard_row)

        self._hint(layout, "autosave")
        layout.addStretch()
        self._settings_tab = tab
        self.tabs.addTab(tab, "")

    def _build_crash_banner(self, parent_layout: QVBoxLayout) -> None:
        self.crash_banner = QFrame()
        self.crash_banner.setObjectName("crashBanner")
        self.crash_banner.setVisible(False)
        banner_layout = QVBoxLayout(self.crash_banner)
        banner_layout.setContentsMargins(10, 10, 10, 10)
        self.crash_banner_title = QLabel()
        self.crash_banner_title.setWordWrap(True)
        banner_layout.addWidget(self.crash_banner_title)
        self.crash_banner_detail = QLabel()
        self.crash_banner_detail.setObjectName("muted")
        self.crash_banner_detail.setWordWrap(True)
        banner_layout.addWidget(self.crash_banner_detail)
        banner_btns = QHBoxLayout()
        self._crash_open_dump_btn = QPushButton()
        self._crash_open_dump_btn.clicked.connect(self._open_crash_dump_file)
        banner_btns.addWidget(self._crash_open_dump_btn)
        self._crash_restore_form_btn = QPushButton()
        self._crash_restore_form_btn.clicked.connect(self._restore_form_from_crash_dump)
        banner_btns.addWidget(self._crash_restore_form_btn)
        self._crash_open_folder_btn = QPushButton()
        self._crash_open_folder_btn.clicked.connect(self._open_crashes_dir)
        banner_btns.addWidget(self._crash_open_folder_btn)
        self._crash_dismiss_btn = QPushButton()
        self._crash_dismiss_btn.clicked.connect(self._dismiss_crash_banner)
        banner_btns.addWidget(self._crash_dismiss_btn)
        banner_btns.addStretch()
        banner_layout.addLayout(banner_btns)
        parent_layout.addWidget(self.crash_banner)

    def _build_log_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        self._log_clear_btn = QPushButton()
        self._log_clear_btn.clicked.connect(self._clear_log)
        toolbar.addWidget(self._log_clear_btn)
        self._log_open_file_btn = QPushButton()
        self._log_open_file_btn.clicked.connect(self._open_app_log_file)
        toolbar.addWidget(self._log_open_file_btn)
        self._log_open_dir_btn = QPushButton()
        self._log_open_dir_btn.clicked.connect(self._open_logs_dir)
        toolbar.addWidget(self._log_open_dir_btn)
        self.log_autoscroll_check = QCheckBox()
        self.log_autoscroll_check.setChecked(True)
        self.log_autoscroll_check.toggled.connect(self._set_log_autoscroll)
        toolbar.addWidget(self.log_autoscroll_check)
        toolbar.addStretch()
        self._log_file_label = QLabel(objectName="muted")
        toolbar.addWidget(self._log_file_label)
        layout.addLayout(toolbar)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas"))
        self.log_text.setMaximumBlockCount(LOG_VIEW_MAX_BLOCKS)
        layout.addWidget(self.log_text)
        self._log_tab = tab
        self.tabs.addTab(tab, "")

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(f"{tr('main.window.title')} · v{__version__}")
        self._brand_label.setText(tr("main.window.brand"))
        self.theme_check.setText(tr("main.theme.dark"))
        self._subtitle_label.setText(tr("main.subtitle"))
        self._collapse_hint_label.setText(tr("main.hint.collapsible"))

        section_titles = {
            "auth": tr("main.section.auth"),
            "search": tr("main.section.search"),
            "queue": tr("main.section.queue"),
            "actions": tr("main.section.actions"),
            "filters": tr("main.section.filters"),
            "progress": tr("main.section.progress"),
            "search_prefs": tr("main.section.search_prefs"),
            "api": tr("main.section.api"),
            "files": tr("main.section.files"),
            "proxy": tr("main.section.proxy"),
            "naming": tr("main.section.naming"),
            "performance": tr("main.section.performance"),
            "reliability": tr("main.section.reliability"),
            "notify": tr("main.section.notify"),
        }
        for key, group in self._section_groups.items():
            if key in section_titles:
                group.setTitle(section_titles[key])

        if hasattr(self, "_extra_filters_box"):
            self._extra_filters_box.setTitle(tr("main.section.extra_filters"))
            self._extra_filters_box.setToolTip(tr("main.filter.extra_filters_tip"))

        self.login_btn.setText(tr("main.auth.login"))
        self.qr_login_btn.setText(tr("main.auth.login_qr"))
        self.reset_session_btn.setText(tr("main.auth.reset_session"))
        self.reset_session_btn.setToolTip(tr("main.auth.reset_session_tip"))
        self._retranslate_auth_status()

        self._static_labels["template"].setText(tr("main.template.label"))
        self.save_template_btn.setText(tr("main.template.save"))
        self.rename_template_btn.setText(tr("main.template.rename"))
        self.delete_template_btn.setText(tr("main.template.delete"))
        self.clear_form_btn.setText(tr("main.template.clear_form"))

        self.queue_list.setToolTip(tr("main.queue.tip"))
        self.queue_add_btn.setText(tr("main.queue.add"))
        self.queue_remove_btn.setText(tr("common.delete"))
        self.queue_up_btn.setText(tr("common.up"))
        self.queue_down_btn.setText(tr("common.down"))
        self.queue_clear_btn.setText(tr("common.clear"))
        self.queue_download_btn.setText(tr("main.queue.download"))
        self.queue_download_btn.setToolTip(tr("main.queue.download_tip"))

        self.once_btn.setText(tr("main.action.download_once"))
        self.once_btn.setToolTip(tr("main.action.download_once_tip"))
        self.preview_btn.setText(tr("main.action.preview"))
        self.preview_btn.setToolTip(tr("main.action.preview_tip"))
        self.verify_btn.setText(tr("main.action.verify"))
        self.verify_btn.setToolTip(tr("main.action.verify_tip"))
        self.stop_btn.setText(tr("main.action.stop"))
        self.pause_btn.setToolTip(tr("main.action.pause_tip"))
        self.open_downloads_btn.setText(tr("main.action.open_folder"))
        self.reset_journal_btn.setText(tr("main.action.reset_journal"))
        self.reset_journal_btn.setToolTip(tr("main.action.reset_journal_tip"))
        self._sync_pause_button()

        self._static_labels["date_from"].setText(tr("main.filter.date_from"))
        self._static_labels["date_to"].setText(tr("main.filter.date_to"))
        self.date_hint_label.setText(tr("main.filter.date_hint"))
        self._form_labels["channel"].setText(tr("main.filter.channel"))
        self._form_labels["max_media"].setText(tr("main.filter.max_media"))
        self.filter_max_posts_spin.setSpecialValueText(tr("main.filter.max_media_unlimited"))
        self.filter_max_posts_spin.setToolTip(tr("main.filter.max_media_tip"))
        self.sequential_preview_check.setText(tr("main.filter.sequential_preview"))
        self.sequential_preview_check.setToolTip(tr("main.filter.sequential_preview_tip"))
        self._static_labels["batch_size"].setText(tr("main.filter.batch_size"))
        self.preview_batch_spin.setSuffix(tr("main.filter.batch_size_suffix"))
        self.preview_batch_spin.setToolTip(tr("main.filter.batch_size_tip"))

        self._form_labels["extra_hashtags"].setText(tr("main.filter.extra_hashtags"))
        self._form_labels["required"].setText(tr("main.filter.required"))
        self._form_labels["exclude"].setText(tr("main.filter.exclude"))
        self._form_labels["extra_channels"].setText(tr("main.filter.extra_channels"))

        self.media_photo_check.setText(tr("main.media.photo"))
        self.media_video_check.setText(tr("main.media.video"))
        self.media_animation_check.setText(tr("main.media.gif"))
        self.media_audio_check.setText(tr("main.media.audio"))
        self.media_document_check.setText(tr("main.media.files"))

        hint_map = {
            "template": tr("main.template.hint"),
            "queue": tr("main.queue.hint"),
            "channel": tr("main.filter.channel_hint"),
            "max_media": tr("main.filter.max_media_hint"),
            "sequential": tr("main.filter.sequential_hint"),
            "extra_hashtags": tr("main.filter.extra_hashtags_hint"),
            "required": tr("main.filter.required_hint"),
            "exclude": tr("main.filter.exclude_hint"),
            "extra_channels": tr("main.filter.extra_channels_hint"),
            "remember_form": tr("main.prefs.remember_form_hint"),
            "api": tr("main.settings.api_hint"),
            "page_limit": tr("main.settings.page_limit_hint"),
            "proxy": tr("main.settings.proxy_hint"),
            "performance": tr("main.settings.performance_hint"),
            "notify": tr("main.settings.notify_hint"),
            "autosave": tr("main.settings.autosave_hint"),
        }
        for key, text in hint_map.items():
            if key in self._hint_labels:
                self._hint_labels[key].setText(text)

        self.remember_last_search_check.setText(tr("main.prefs.remember_form"))
        self._language_label.setText(tr("main.settings.language"))
        self._autotune_run_btn.setText(tr("autotune.button.run"))
        self._autotune_apply_btn.setText(tr("autotune.button.apply"))
        self._refresh_autotune_summary()
        lang = getattr(self, "language_combo", None)
        if lang is not None:
            current = lang.currentData()
            lang.blockSignals(True)
            lang.setItemText(0, tr("main.settings.language.system"))
            lang.setItemText(1, tr("main.settings.language.ru"))
            lang.setItemText(2, tr("main.settings.language.en"))
            if current is not None:
                idx = lang.findData(current)
                if idx >= 0:
                    lang.setCurrentIndex(idx)
            lang.blockSignals(False)

        form_map = {
            "api_id": tr("main.settings.api_id"),
            "api_hash": tr("main.settings.api_hash"),
            "page_limit": tr("main.settings.page_limit"),
            "session_name": tr("main.settings.session_name"),
            "proxy_type": tr("main.settings.proxy_type"),
            "proxy_host": tr("main.settings.proxy_host"),
            "proxy_port": tr("main.settings.proxy_port"),
            "caption_max_len": tr("main.settings.caption_max_len"),
            "preview_threads": tr("main.settings.preview_threads"),
            "download_threads": tr("main.settings.download_threads"),
            "retries": tr("main.settings.retries"),
        }
        for key, text in form_map.items():
            if key in self._form_labels:
                self._form_labels[key].setText(text)

        self._static_labels["download_dir"].setText(tr("main.settings.download_dir"))
        self._browse_btn.setText(tr("main.settings.browse"))
        self.proxy_enabled_check.setText(tr("main.settings.proxy_enable"))
        self.folder_by_date_check.setText(tr("main.settings.folder_by_month"))
        self.caption_in_filename_check.setText(tr("main.settings.caption_in_filename"))
        self.dedup_by_hash_check.setText(tr("main.settings.dedup"))
        self.preview_parallel_spin.setSuffix(tr("main.settings.threads_suffix"))
        self.download_parallel_spin.setSuffix(tr("main.settings.threads_suffix"))
        self.download_parallel_spin.setToolTip(tr("main.settings.download_threads_tip"))
        self.win_notify_enabled_check.setText(tr("main.settings.notify_enable"))
        self.win_notify_success_check.setText(tr("main.settings.notify_success"))
        self.win_notify_errors_check.setText(tr("main.settings.notify_errors"))
        self._wizard_btn.setText(tr("main.settings.wizard"))
        self._about_btn.setText(tr("about.button"))

        self._crash_open_dump_btn.setText(tr("main.crash.open_dump"))
        self._crash_restore_form_btn.setText(tr("main.crash.restore_form"))
        self._crash_open_folder_btn.setText(tr("main.crash.open_folder"))
        self._crash_dismiss_btn.setText(tr("main.crash.dismiss"))

        self._log_clear_btn.setText(tr("main.log.clear"))
        self._log_clear_btn.setToolTip(tr("main.log.clear_tip"))
        self._log_open_file_btn.setText(tr("main.log.open_file"))
        self._log_open_dir_btn.setText(tr("main.log.open_dir"))
        self.log_autoscroll_check.setText(tr("main.log.autoscroll"))
        self._log_file_label.setText(tr("main.log.file_label", name=LOG_FILE.name))

        main_idx = self.tabs.indexOf(self._main_tab)
        if main_idx >= 0:
            self.tabs.setTabText(main_idx, tr("main.tab.main"))
        settings_idx = self.tabs.indexOf(self._settings_tab)
        if settings_idx >= 0:
            self.tabs.setTabText(settings_idx, tr("main.tab.settings"))
        log_idx = self.tabs.indexOf(self._log_tab)
        if log_idx >= 0:
            alert = self.tabs.tabText(log_idx).startswith("⚠")
            self.tabs.setTabText(log_idx, tr("main.tab.log_alert") if alert else tr("main.tab.log"))

        self._reload_template_combo()
        self._update_filter_hint()
        if not (self.worker_thread and self.worker_thread.is_alive()):
            if hasattr(self, "crash_banner") and self.crash_banner.isVisible():
                self.status_label.setText(tr("main.status.crash"))
            elif not self.login_in_progress():
                self.status_label.setText(tr("main.status.ready"))

        if self._last_progress_state is not None:
            self._update_progress(self._last_progress_state)
        elif hasattr(self, "progress_phase_label"):
            self.progress_phase_label.setText(tr("main.progress.phase.idle"))

        self.queue_batch_bar.setFormat(tr("main.progress.queue_format"))
        dash = tr("common.dash")
        if not self.progress_detail_label.text() or self.progress_detail_label.text() == "—":
            self.progress_detail_label.setText(dash)

        if self._tray_icon is not None:
            self._tray_icon.setToolTip(tr("main.tray.tooltip"))
            if hasattr(self, "_tray_open_action"):
                self._tray_open_action.setText(tr("main.tray.open"))
            if hasattr(self, "_tray_quit_action"):
                self._tray_quit_action.setText(tr("main.tray.quit"))

        self.date_from_picker.retranslate_ui()
        self.date_to_picker.retranslate_ui()
        self.extra_hashtags_entry.retranslate_ui()
        self.extra_channels_entry.retranslate_ui()

        dialog = self._active_preview_dialog
        if dialog is not None and hasattr(dialog, "_retranslate_ui"):
            dialog._retranslate_ui()

    def _on_language_changed(self, index: int) -> None:
        if self._loading_fields or index < 0:
            return
        lang = self.language_combo.itemData(index)
        if lang is None:
            return
        set_locale(str(lang))
        self._retranslate_ui()
        self._persist_settings()

    def _apply_theme(self) -> None:
        self._palette = palette_for(dark=self._dark_theme)
        self.setStyleSheet(build_stylesheet(dark=self._dark_theme))
        # Titlebar after stylesheet · Шапка в следующем цикле событий
        QTimer.singleShot(0, lambda: set_titlebar_dark(self._dark_theme))
        bar_text_track = self._palette["fg"] if self._dark_theme else "#141414"
        bar_text_chunk = "#1a1a1a" if self._dark_theme else "#ffffff"
        self.progress_bar.set_theme_colors(
            track=self._palette["panel"],
            accent=self._palette["accent"],
            border=self._palette["border"],
            text=bar_text_track,
            text_on_chunk=bar_text_chunk,
        )
        queue_accent = self._palette["accent"]
        self.queue_batch_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {queue_accent}; }}",
        )
        flood_color = "#e3a008" if self._dark_theme else "#9a6700"
        self.progress_flood_label.setStyleSheet(
            f"color: {flood_color}; font-weight: bold;",
        )
        for picker in (self.date_from_picker, self.date_to_picker):
            picker.apply_calendar_theme(self._palette)
        self._hashtag_history.apply_popup_theme(dark=self._dark_theme)

    def _apply_search_form_from_settings(self, settings: SettingsData) -> None:
        self.hashtag_entry.setText(settings.hashtag)
        self.extra_hashtags_entry.setText(settings.extra_hashtags)
        self.exclude_hashtags_entry.setText(settings.exclude_hashtags)
        self.required_hashtags_entry.setText(settings.required_hashtags)
        self.extra_channels_entry.setText(settings.extra_channels)
        self.channel_filter_entry.setText(settings.channel_filter)
        self.date_from_picker.set_value(settings.date_from)
        self.date_to_picker.set_value(settings.date_to)
        self.filter_max_posts_spin.setValue(max(0, int(settings.max_posts)))
        self.sequential_preview_check.setChecked(settings.sequential_preview)
        self.preview_batch_spin.setValue(max(20, min(int(settings.preview_batch_size), 1000)))
        self._sync_preview_batch_controls(settings.sequential_preview)
        self.media_photo_check.setChecked(settings.media_photo)
        self.media_video_check.setChecked(settings.media_video)
        self.media_animation_check.setChecked(settings.media_animation)
        self.media_audio_check.setChecked(settings.media_audio)
        self.media_document_check.setChecked(settings.media_document)
        self._update_filter_hint()
        self._update_batch_hint()

    def _clear_search_form(self) -> None:
        blank = SettingsData()
        apply_snapshot_to_settings(blank, empty_snapshot())
        self._apply_search_form_from_settings(blank)
        self.template_combo.blockSignals(True)
        self.template_combo.setCurrentIndex(0)
        self.template_combo.blockSignals(False)

    def _reload_template_combo(self) -> None:
        current = self.template_combo.currentData()
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem(tr("main.template.none"), "")
        for item in load_named_templates():
            self.template_combo.addItem(item.name, item.name)
        if current:
            index = self.template_combo.findData(current)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
        self.template_combo.blockSignals(False)

    def _on_template_selected(self, index: int) -> None:
        if index <= 0:
            return
        name = self.template_combo.itemData(index)
        if not name:
            return
        for item in load_named_templates():
            if item.name == name:
                draft = SettingsData()
                apply_snapshot_to_settings(draft, item.form)
                self._apply_search_form_from_settings(draft)
                break

    def _save_search_template(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, tr("main.template.dialog.save_title"), tr("main.template.dialog.name"))
        if not ok:
            return
        clean = name.strip()
        if not clean:
            show_warning(self, tr("main.template.label"), tr("main.template.warn.empty_name"))
            return
        if template_exists(clean) and not ask_yes_no(
            self,
            tr("main.template.dialog.overwrite_title"),
            tr("main.template.dialog.overwrite_body", name=clean),
        ):
            return
        upsert_named_template(clean, snapshot_from_settings(self._collect_settings()))
        self._reload_template_combo()
        pick = self.template_combo.findData(clean)
        if pick >= 0:
            self.template_combo.setCurrentIndex(pick)
        show_info(self, tr("main.template.label"), tr("main.template.info.saved", name=clean))

    def _rename_search_template(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        index = self.template_combo.currentIndex()
        if index <= 0:
            show_warning(self, tr("main.template.label"), tr("main.template.warn.select_first"))
            return
        old_name = str(self.template_combo.itemData(index) or "").strip()
        if not old_name:
            return
        new_name, ok = QInputDialog.getText(
            self,
            tr("main.template.dialog.rename_title"),
            tr("main.template.dialog.new_name"),
            text=old_name,
        )
        if not ok:
            return
        clean = new_name.strip()
        if not clean:
            show_warning(self, tr("main.template.label"), tr("main.template.warn.empty_name"))
            return
        if clean == old_name:
            return
        if template_exists(clean):
            show_warning(self, tr("main.template.label"), tr("main.template.warn.exists", name=clean))
            return
        if not rename_named_template(old_name, clean):
            show_warning(self, tr("main.template.label"), tr("main.template.warn.rename_failed"))
            return
        self._reload_template_combo()
        pick = self.template_combo.findData(clean)
        if pick >= 0:
            self.template_combo.setCurrentIndex(pick)
        show_info(
            self,
            tr("main.template.label"),
            tr("main.template.info.renamed", old=old_name, new=clean),
        )

    def _delete_search_template(self) -> None:
        index = self.template_combo.currentIndex()
        if index <= 0:
            show_warning(self, tr("main.template.label"), tr("main.template.warn.select_first"))
            return
        name = str(self.template_combo.itemData(index) or "").strip()
        if not name:
            return
        if not ask_yes_no(
            self,
            tr("main.template.dialog.delete_title"),
            tr("main.template.dialog.delete_body", name=name),
        ):
            return
        delete_named_template(name)
        self._reload_template_combo()
        show_info(self, tr("main.template.label"), tr("main.template.info.deleted", name=name))

    def _collect_queue_tags_from_ui(self) -> list[str]:
        tags = [self.queue_list.item(i).text() for i in range(self.queue_list.count())]
        return normalize_hashtag_queue(tags)

    def _set_queue_tags(self, tags: list[str]) -> None:
        clean = normalize_hashtag_queue(tags)
        self.queue_list.clear()
        for tag in clean:
            self.queue_list.addItem(tag)
        save_hashtag_queue(clean)

    def _reload_hashtag_queue_ui(self) -> None:
        self._set_queue_tags(load_hashtag_queue())

    def _queue_add_current(self) -> None:
        raw = self.hashtag_entry.text().strip()
        if not raw:
            show_warning(self, tr("main.section.queue"), tr("main.queue.warn.no_hashtag"))
            return
        try:
            tag = normalize_hashtag(raw)
        except ValueError as exc:
            show_error(self, tr("main.section.queue"), str(exc))
            return
        tags = self._collect_queue_tags_from_ui()
        if tag in tags:
            show_info(self, tr("main.section.queue"), tr("main.queue.info.duplicate", tag=tag))
            return
        tags.append(tag)
        self._set_queue_tags(tags)

    def _queue_remove_selected(self) -> None:
        row = self.queue_list.currentRow()
        if row < 0:
            show_warning(self, tr("main.section.queue"), tr("main.queue.warn.select"))
            return
        self.queue_list.takeItem(row)
        self._set_queue_tags(self._collect_queue_tags_from_ui())

    def _queue_move_selected(self, delta: int) -> None:
        row = self.queue_list.currentRow()
        if row < 0:
            show_warning(self, tr("main.section.queue"), tr("main.queue.warn.select"))
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self.queue_list.count():
            return
        item = self.queue_list.takeItem(row)
        if item is None:
            return
        self.queue_list.insertItem(new_row, item)
        self.queue_list.setCurrentRow(new_row)
        self._set_queue_tags(self._collect_queue_tags_from_ui())

    def _queue_clear(self) -> None:
        if self.queue_list.count() == 0:
            return
        if not ask_yes_no(self, tr("main.queue.dialog.clear_title"), tr("main.queue.dialog.clear_body")):
            return
        self._set_queue_tags([])

    def _validate_settings_for_hashtags(self, hashtags: list[str]) -> SettingsData | None:
        tags = normalize_hashtag_queue(hashtags)
        if not tags:
            show_error(self, tr("main.section.queue"), tr("main.queue.error.empty"))
            return None
        try:
            settings = self._collect_settings()
            settings.hashtag = tags[0]
            settings.extra_hashtags = ""
            build_app_config(settings, hashtag=tags[0])
            return settings
        except ValueError as exc:
            show_error(self, tr("main.error.validation_title"), str(exc))
            return None
        except Exception as exc:
            show_error(self, tr("main.error.title"), str(exc))
            return None

    def _sync_proxy_fields_enabled(self, enabled: bool | None = None) -> None:
        active = self.proxy_enabled_check.isChecked() if enabled is None else enabled
        for widget in (self.proxy_type_entry, self.proxy_host_entry, self.proxy_port_spin):
            widget.setEnabled(active)

    def _load_fields(self) -> None:
        self._loading_fields = True
        s = self.settings
        self.api_id_entry.setText(s.api_id)
        self.api_hash_entry.setText(s.api_hash)
        self.download_dir_entry.setText(s.download_dir)
        self.session_name_entry.setText(s.session_name)
        self.page_limit_spin.setValue(max(1, int(s.page_limit)))

        self.proxy_enabled_check.setChecked(s.proxy_enabled)
        self.proxy_type_entry.setText(s.proxy_type)
        self.proxy_host_entry.setText(s.proxy_host)
        self.proxy_port_spin.setValue(max(1, int(s.proxy_port)))
        self._sync_proxy_fields_enabled()
        self.remember_last_search_check.setChecked(s.remember_last_search)
        self._apply_search_form_from_settings(s)
        self.folder_by_date_check.setChecked(s.folder_by_date)
        self.caption_in_filename_check.setChecked(s.caption_in_filename)
        self.caption_max_len_spin.setValue(max(0, int(s.caption_max_len)))
        self.dedup_by_hash_check.setChecked(s.dedup_by_hash)
        self.preview_parallel_spin.setValue(max(1, min(int(s.preview_parallel_workers), 6)))
        self.download_parallel_spin.setValue(max(1, min(int(s.download_parallel_workers), 3)))
        self.download_retries_spin.setValue(max(0, int(s.download_retries)))
        self.win_notify_enabled_check.setChecked(s.win_notify_enabled)
        self.win_notify_success_check.setChecked(s.win_notify_success)
        self.win_notify_errors_check.setChecked(s.win_notify_errors)
        self.theme_check.setChecked(s.dark_theme)
        lang = (s.ui_language or "system").strip().lower()
        lang_idx = self.language_combo.findData(lang)
        if lang_idx < 0:
            lang_idx = self.language_combo.findData("system")
        if lang_idx >= 0:
            self.language_combo.setCurrentIndex(lang_idx)
        self._reload_template_combo()
        self._loading_fields = False

    def _update_batch_hint(self, *_args: object) -> None:
        hint = format_batch_search_hint(
            self.hashtag_entry.text(),
            self.extra_hashtags_entry.text(),
            self.channel_filter_entry.text(),
            self.extra_channels_entry.text(),
        )
        self.batch_hint_label.setText(hint)
        self.batch_hint_label.setVisible(bool(hint))

    def _sync_preview_batch_controls(self, checked: bool | None = None) -> None:
        if checked is None:
            checked = self.sequential_preview_check.isChecked()
        self.preview_batch_spin.setEnabled(checked)

    def _update_filter_hint(self, *_args: object) -> None:
        parts: list[str] = []
        if self.date_from_picker.is_active():
            parts.append(tr("main.filter.date_from_part", date=self.date_from_picker.display_text()))
        if self.date_to_picker.is_active():
            parts.append(tr("main.filter.date_to_part", date=self.date_to_picker.display_text()))
        if parts:
            self.date_filter_label.setText(
                tr("main.filter.date_active", range=" ".join(parts)),
            )
            self.date_filter_label.setObjectName("error")
            self.date_filter_label.setVisible(True)
        else:
            self.date_filter_label.clear()
            self.date_filter_label.setVisible(False)
        self.date_filter_label.style().unpolish(self.date_filter_label)
        self.date_filter_label.style().polish(self.date_filter_label)

    def _collect_settings(self) -> SettingsData:
        return SettingsData(
            api_id=self.api_id_entry.text().strip(),
            api_hash=self.api_hash_entry.text().strip(),
            hashtag=self.hashtag_entry.text().strip(),
            download_dir=self.download_dir_entry.text().strip() or "data/downloads",
            page_limit=self.page_limit_spin.value(),
            max_posts=self.filter_max_posts_spin.value(),
            sequential_preview=self.sequential_preview_check.isChecked(),
            preview_batch_size=self.preview_batch_spin.value(),
            date_from=self.date_from_picker.get(),
            date_to=self.date_to_picker.get(),
            channel_filter=self.channel_filter_entry.text().strip(),
            session_name=self.session_name_entry.text().strip() or "hashtag_session",
            proxy_enabled=self.proxy_enabled_check.isChecked(),
            proxy_type=self.proxy_type_entry.text().strip() or "socks5",
            proxy_host=self.proxy_host_entry.text().strip() or "127.0.0.1",
            proxy_port=self.proxy_port_spin.value(),
            media_photo=self.media_photo_check.isChecked(),
            media_video=self.media_video_check.isChecked(),
            media_animation=self.media_animation_check.isChecked(),
            media_audio=self.media_audio_check.isChecked(),
            media_document=self.media_document_check.isChecked(),
            extra_hashtags=self.extra_hashtags_entry.text().strip(),
            exclude_hashtags=self.exclude_hashtags_entry.text().strip(),
            required_hashtags=self.required_hashtags_entry.text().strip(),
            extra_channels=self.extra_channels_entry.text().strip(),
            folder_by_date=self.folder_by_date_check.isChecked(),
            caption_in_filename=self.caption_in_filename_check.isChecked(),
            caption_max_len=self.caption_max_len_spin.value(),
            dedup_by_hash=self.dedup_by_hash_check.isChecked(),
            preview_parallel_workers=self.preview_parallel_spin.value(),
            download_parallel_workers=self.download_parallel_spin.value(),
            download_retries=self.download_retries_spin.value(),
            dark_theme=self._dark_theme,
            remember_last_search=self.remember_last_search_check.isChecked(),
            win_notify_enabled=self.win_notify_enabled_check.isChecked(),
            win_notify_success=self.win_notify_success_check.isChecked(),
            win_notify_errors=self.win_notify_errors_check.isChecked(),
            setup_wizard_completed=self.settings.setup_wizard_completed,
            ui_language=str(self.language_combo.currentData() or "system"),
        )

    def _session_path(self, settings: SettingsData | None = None) -> Path:
        data = settings or self._collect_settings()
        return session_path_for(data.session_name)

    def _persist_settings(self) -> bool:
        try:
            settings = self._collect_settings()
            save_settings(settings)
            self.settings = settings
            logging.info(tr("log.settings.saved"))
            return True
        except ValueError as exc:
            logging.warning(tr("log.settings.save_failed", exc=exc))
            return False
        except Exception as exc:
            logging.warning(tr("log.settings.save_error", exc=exc))
            return False

    def _refresh_autotune_summary(self) -> None:
        if not hasattr(self, "_autotune_summary_label"):
            return
        current = self._collect_settings() if hasattr(self, "preview_parallel_spin") else self.settings
        self._autotune_summary_label.setText(
            format_autotune_summary(self._autotune_profile, current=current),
        )
        has_profile = self._autotune_profile is not None
        self._autotune_apply_btn.setVisible(has_profile)
        self._autotune_apply_btn.setEnabled(
            has_profile
            and not self._autotune_running
            and not profile_matches_settings(self._autotune_profile, current)
        )

    def _start_autotune_check(self) -> None:
        if self._autotune_running:
            return
        settings = self._collect_settings()
        self._autotune_running = True
        self._autotune_run_btn.setEnabled(False)
        self._autotune_apply_btn.setEnabled(False)
        self._update_progress(
            ProgressState(
                phase="benchmark",
                total=6,
                processed=0,
                current=tr("autotune.progress.starting"),
            ),
        )

        def _progress(text: str, step: int, total: int) -> None:
            self._autotune_queue.put(("progress", str(text), int(step), int(total)))

        def _worker() -> None:
            try:
                profile = run_autotune_sync(settings, progress=_progress)
                self._autotune_queue.put(("done", profile, None))
            except Exception as exc:
                self._autotune_queue.put(("done", None, str(exc)))

        self._autotune_thread = threading.Thread(target=_worker, daemon=True)
        self._autotune_thread.start()

    def _poll_autotune_queue(self) -> None:
        while True:
            try:
                event = self._autotune_queue.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "progress":
                _, text, step, total = event
                self._update_progress(
                    ProgressState(
                        phase="benchmark",
                        total=max(1, total),
                        processed=max(0, step),
                        current=text,
                    ),
                )
            elif kind == "done":
                _, profile, error = event
                self._autotune_running = False
                self._autotune_run_btn.setEnabled(True)
                if error:
                    self._update_progress(ProgressState(phase="idle", current=tr("autotune.progress.failed")))
                    show_error(self, tr("autotune.dialog.title"), str(error))
                    self._refresh_autotune_summary()
                    continue
                self._autotune_profile = profile
                save_autotune_profile(profile, AUTOTUNE_PROFILE_PATH)
                self._refresh_autotune_summary()
                self._update_progress(
                    ProgressState(phase="done", total=1, processed=1, current=tr("autotune.progress.done")),
                )
                action = show_autotune_result_dialog(
                    self,
                    tr("autotune.dialog.title"),
                    self._format_autotune_result(profile),
                    can_apply=not profile_matches_settings(profile, self._collect_settings()),
                )
                if action == "apply":
                    self._apply_autotune_recommendations()

    def _format_autotune_result(self, profile) -> str:
        rec = profile.recommendation
        lines = [
            tr("autotune.result.generated", date=profile.created_at.replace("T", " ").replace("+00:00", " UTC")),
            "",
            tr("autotune.result.preview_workers", n=rec.preview_parallel_workers),
            tr("autotune.result.download_workers", n=rec.download_parallel_workers),
            tr("autotune.result.batch_size", n=rec.preview_batch_size),
            tr(
                "autotune.result.preview_mode",
                mode=tr("autotune.result.mode.sequential") if rec.sequential_preview else tr("autotune.result.mode.regular"),
            ),
            "",
            tr("autotune.result.rationale"),
        ]
        lines.extend(f"- {item}" for item in rec.rationale)
        return "\n".join(lines)

    def _apply_autotune_recommendations(self) -> None:
        if self._autotune_profile is None:
            return
        rec = self._autotune_profile.recommendation
        self.preview_parallel_spin.setValue(max(1, min(int(rec.preview_parallel_workers), 6)))
        self.download_parallel_spin.setValue(max(1, min(int(rec.download_parallel_workers), 3)))
        self.preview_batch_spin.setValue(max(20, min(int(rec.preview_batch_size), 1000)))
        self.sequential_preview_check.setChecked(bool(rec.sequential_preview))
        self._sync_preview_batch_controls()
        self._persist_settings()
        self._refresh_autotune_summary()

    def _browse_download_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            tr("main.dialog.pick_download_dir"),
            str(resolve_download_dir(self.download_dir_entry.text())),
        )
        if selected:
            self.download_dir_entry.setText(selected)

    def _open_downloads(self) -> None:
        path = resolve_download_dir(self.download_dir_entry.text())
        tag = self.hashtag_entry.text().strip().lstrip("#")
        if tag:
            path = path / safe_name(normalize_hashtag(tag))
        open_path_in_file_manager(path)

    def _show_setup_wizard(self) -> None:
        from .setup_wizard import run_setup_wizard

        run_setup_wizard(
            self,
            settings=self._collect_settings(),
            skip_welcome=True,
        )

    def _show_about_dialog(self) -> None:
        show_about_dialog(self)

    def _sync_pause_button(self) -> None:
        if self.task_paused.is_set():
            self.pause_btn.setText(tr("common.resume"))
        else:
            self.pause_btn.setText(tr("main.action.pause"))

    def _click_pause(self) -> None:
        if not self.worker_thread or not self.worker_thread.is_alive():
            return
        self._toggle_pause(not self.task_paused.is_set())
        self._sync_pause_button()

    def _toggle_pause(self, paused: bool) -> None:
        mode_labels = {
            "once": tr("main.status.paused_once"),
            "preview": tr("main.status.paused_preview"),
            "verify": tr("main.status.paused_verify"),
            "integrity_download": tr("main.status.paused_integrity"),
        }
        resume_labels = {
            "once": tr("main.status.running_once"),
            "preview": tr("main.status.running_preview"),
            "verify": tr("main.status.running_verify"),
            "integrity_download": tr("main.status.running_integrity"),
        }
        if paused:
            self.task_paused.set()
            self.status_label.setText(mode_labels.get(self.worker_mode, tr("main.status.paused")))
            self._update_progress(
                ProgressState(
                    current=tr("main.status.paused_detail"),
                ),
            )
            logging.info(tr("log.task.paused"))
        else:
            self.task_paused.clear()
            if self.worker_thread and self.worker_thread.is_alive():
                self.status_label.setText(
                    resume_labels.get(self.worker_mode, tr("main.status.running")),
                )
            logging.info(tr("log.task.resumed"))

    def _require_logged_in(self) -> bool:
        if self.is_logged_in:
            return True
        show_error(
            self,
            tr("main.auth.required_title"),
            tr("main.auth.required_body"),
        )
        return False

    def _validate_before_start(self) -> SettingsData | None:
        try:
            settings = self._collect_settings()
            if not settings.hashtag.strip():
                raise ValueError(tr("main.error.no_hashtag"))
            settings.hashtag = normalize_hashtag(settings.hashtag)
            build_app_config(settings, hashtag=settings.hashtag)
            return settings
        except ValueError as exc:
            show_error(self, tr("main.error.validation_title"), str(exc))
            return None
        except Exception as exc:
            show_error(self, tr("main.error.title"), str(exc))
            return None

    def _on_theme_toggle(self, checked: bool) -> None:
        self._dark_theme = checked
        self.theme_check.blockSignals(True)
        self.theme_check.setChecked(checked)
        self.theme_check.blockSignals(False)
        self._apply_theme()
        running = bool(self.worker_thread and self.worker_thread.is_alive())
        self._set_running_state(running)
        try:
            settings = self._collect_settings()
            save_settings(settings)
            self.settings = settings
        except Exception as exc:
            logging.warning(tr("log.settings.theme_failed", exc=exc))

    def _start_once(self) -> None:
        self._start_worker_mode("once")

    def _start_queue_download(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            show_warning(self, tr("main.busy.title"), tr("main.busy.stop_first"))
            return
        if not self._persist_settings():
            return
        tags = self._collect_queue_tags_from_ui()
        settings = self._validate_settings_for_hashtags(tags)
        if not settings or not self._require_logged_in():
            return
        self._launch_worker(settings, "once", queue_hashtags=tags)

    def _start_preview(self) -> None:
        self._start_worker_mode("preview")

    def _start_verify(self) -> None:
        self._start_worker_mode("verify")

    def _start_worker_mode(self, mode: str) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            show_warning(self, tr("main.busy.title"), tr("main.busy.stop_first"))
            return
        if not self._persist_settings():
            return
        self.worker_mode = mode
        settings = self._validate_before_start()
        if not settings or not self._require_logged_in():
            return
        self._launch_worker(settings, mode)

    def _start_integrity_download(self, integrity: IntegrityStats) -> None:
        if not integrity.missing_refs:
            return
        if self.worker_thread and self.worker_thread.is_alive():
            show_warning(self, tr("main.busy.title"), tr("main.busy.stop_first"))
            return
        if not self._persist_settings():
            return
        if not self._require_logged_in():
            return
        self._integrity_download_refs = list(integrity.missing_refs)
        self.worker_mode = "integrity_download"
        self._launch_worker(self.settings, "integrity_download")

    def _launch_worker(
        self,
        settings: SettingsData,
        mode: str,
        *,
        queue_hashtags: list[str] | None = None,
    ) -> None:
        integrity_refs = (
            list(self._integrity_download_refs)
            if mode == "integrity_download"
            else []
        )
        spec = TaskSpec(
            mode=mode,
            settings=settings,
            integrity_refs=integrity_refs,
            queue_hashtags=list(queue_hashtags or []),
        )
        self._worker_ctrl.launch(spec)

    def _batch_downloader(self, client, settings: SettingsData, tag: str, channel: str) -> HashtagDownloader:
        return self._worker_ctrl._batch_downloader(client, settings, tag, channel)

    def _stop_worker(self) -> None:
        if not self.worker_thread or not self.worker_thread.is_alive():
            self.status_label.setText(tr("main.status.no_task"))
            return
        self.stop_event.set()
        self.status_label.setText(tr("main.status.stopping"))
        logging.info(tr("log.task.waiting_stop"))

    def _setup_win_notifications(self) -> None:
        configure_win_notifications()
        if notifications_available() and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = QSystemTrayIcon(self)
            if not self.windowIcon().isNull():
                self._tray_icon.setIcon(self.windowIcon())
            else:
                self._tray_icon.setIcon(
                    self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon),
                )
            self._tray_icon.setToolTip(tr("main.tray.tooltip"))
            self._tray_icon.setVisible(True)
            set_tray_fallback(self._tray_icon)
            self._tray_icon.activated.connect(self._on_tray_activated)

            tray_menu = QMenu(self)
            self._tray_open_action = QAction(tr("main.tray.open"), self)
            self._tray_open_action.triggered.connect(self._restore_from_tray)
            tray_menu.addAction(self._tray_open_action)
            self._tray_quit_action = QAction(tr("main.tray.quit"), self)
            self._tray_quit_action.triggered.connect(self.close)
            tray_menu.addAction(self._tray_quit_action)
            self._tray_icon.setContextMenu(tray_menu)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Context:
            return
        # Deferred restore · Отложенное восстановление на Windows
        QTimer.singleShot(0, self._restore_from_tray)

    def _restore_from_tray(self) -> None:
        self._bring_app_to_front(notify=False)

    def _should_notify(self, *, success: bool = False, error: bool = False) -> bool:
        settings = self.settings
        if not settings.win_notify_enabled:
            return False
        if error:
            return settings.win_notify_errors
        if success:
            return settings.win_notify_success
        return False

    def notify_task_finished(
        self,
        *,
        mode: str,
        stats: DownloadStats | None = None,
        integrity: IntegrityStats | None = None,
        stopped: bool = False,
    ) -> None:
        if not self._should_notify(success=True):
            return
        if integrity is not None:
            title = tr("main.notify.integrity_done")
            missing = len(integrity.missing_refs)
            body = (
                tr("main.notify.files_on_disk", n=integrity.files_on_disk)
                if not missing
                else tr("main.notify.files_missing", n=missing)
            )
        elif stats is not None:
            if stopped or stats.stopped:
                title = tr("main.notify.stopped")
            elif mode == "preview":
                title = tr("main.notify.preview_done")
            else:
                title = tr("main.notify.download_done")
            body = tr(
                "main.notify.stats",
                files=stats.files,
                skipped=stats.skipped,
                errors=stats.errors,
            )
        else:
            title = tr("main.notify.generic_done")
            body = tr("main.notify.return")
        show_win_notification(title, body)

    def notify_task_error(self, message: str) -> None:
        if not self._should_notify(error=True):
            return
        show_win_notification(tr("main.error.title"), message or tr("main.error.unknown"), error=True)

    def _bring_app_to_front(self, *, notify: bool = False) -> None:
        restore_top_level_window(self)
        if notify and sys.platform.startswith("win"):
            self._flash_taskbar()

    def _flash_taskbar(self) -> None:
        try:
            import ctypes

            hwnd = int(self.winId())
            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("hwnd", ctypes.c_void_p),
                    ("dwFlags", ctypes.c_uint),
                    ("uCount", ctypes.c_uint),
                    ("dwTimeout", ctypes.c_uint),
                ]

            info = FLASHWINFO()
            info.cbSize = ctypes.sizeof(FLASHWINFO)
            info.hwnd = hwnd
            info.dwFlags = 0x00000003 | 0x0000000C
            info.uCount = 4
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass

    def _show_worker_error(self, message: str) -> None:
        self._bring_app_to_front(notify=True)
        self.notify_task_error(message)
        show_error(self, tr("main.error.title"), message or tr("main.error.unknown"))

    def _progress_indeterminate(self, state: ProgressState) -> bool:
        if state.phase in {"search", "preview"}:
            return state.total <= 0
        return state.phase == "download" and state.total <= 0

    @staticmethod
    def _format_speed_bps(bps: float) -> str:
        if bps <= 0:
            return ""
        if bps >= 1_000_000:
            return tr("main.speed.mbps", n=f"{bps / 1_000_000:.1f}")
        if bps >= 1_000:
            return tr("main.speed.kbps", n=f"{bps / 1_000:.0f}")
        return tr("main.speed.bps", n=f"{bps:.0f}")

    def _flood_remaining(self, deadline: float) -> int:
        if deadline <= 0:
            return 0
        return max(0, math.ceil(deadline - time.monotonic()))

    def _flood_wait_text(self, remaining: int, phase: str = "") -> str:
        if remaining <= 0:
            return ""
        phase_action = {
            "search": tr("main.flood.search"),
            "preview": tr("main.flood.preview"),
            "download": tr("main.flood.download"),
            "verify": tr("main.flood.verify"),
        }.get(phase, tr("main.flood.generic"))
        return tr("main.flood.message", action=phase_action, sec=remaining)

    def _update_flood_label(self, state: ProgressState) -> None:
        remaining = self._flood_remaining(state.flood_wait_deadline)
        if remaining > 0:
            self.progress_flood_label.setText(self._flood_wait_text(remaining, state.phase))
            self.progress_flood_label.setVisible(True)
            if not self._flood_timer.isActive():
                self._flood_timer.start()
            return
        self.progress_flood_label.clear()
        self.progress_flood_label.setVisible(False)
        self._flood_timer.stop()

    def _tick_flood_countdown(self) -> None:
        state = self._last_progress_state
        if state is None or self._flood_remaining(state.flood_wait_deadline) <= 0:
            self._flood_timer.stop()
            self.progress_flood_label.clear()
            self.progress_flood_label.setVisible(False)
            return
        self._update_progress(state)

    def _handle_progress_alert(self, state: ProgressState) -> None:
        alert = (state.alert or "").strip()
        if not alert or alert == self._last_progress_alert:
            return
        self._last_progress_alert = alert
        lowered = alert.casefold()
        if "подожд" in lowered or "wait" in lowered or "flood" in lowered:
            return
        show_warning(self, tr("main.progress.alert_title"), alert)

    def _highlight_queue_hashtag(self, tag: str) -> None:
        if not tag:
            return
        accent = QColor(self._palette.get("accent", "#1a7f37"))
        accent.setAlpha(72)
        brush = QBrush(accent)
        target = tag.casefold()
        for row in range(self.queue_list.count()):
            item = self.queue_list.item(row)
            if item.text().casefold() == target:
                item.setBackground(brush)
                self.queue_list.setCurrentRow(row)
                self.queue_list.scrollToItem(item)
            else:
                item.setBackground(QBrush())

    def _clear_queue_highlight(self) -> None:
        for row in range(self.queue_list.count()):
            self.queue_list.item(row).setBackground(QBrush())
        self.queue_list.clearSelection()

    def _update_queue_batch_ui(self, state: ProgressState) -> None:
        batch_active = state.batch_total > 1 and state.batch_index > 0
        self.queue_batch_bar.setVisible(batch_active)
        self.queue_progress_label.setVisible(batch_active)
        if not batch_active:
            if not self._queue_download_active:
                self._clear_queue_highlight()
            return
        pct = queue_overall_percent(state)
        self.queue_batch_bar.setValue(pct)
        self.queue_batch_bar.setFormat(
            tr(
                "main.progress.queue_bar",
                i=state.batch_index,
                total=state.batch_total,
                pct=pct,
            ),
        )
        label = format_batch_progress_label(state)
        self.queue_progress_label.setText(label)
        if self._queue_download_active and state.batch_hashtag:
            self._highlight_queue_hashtag(state.batch_hashtag)

    def _update_progress(self, state: ProgressState) -> None:
        self._last_progress_state = state
        phase_labels = {
            "idle": tr("main.progress.phase.idle"),
            "benchmark": tr("autotune.progress.phase"),
            "search": tr("main.progress.phase.search"),
            "preview": tr("main.progress.phase.preview"),
            "download": tr("main.progress.phase.download"),
            "verify": tr("main.progress.phase.verify"),
            "done": tr("main.progress.phase.done"),
            "stopped": tr("main.progress.phase.stopped"),
        }
        self.progress_phase_label.setText(phase_labels.get(state.phase, state.phase))

        if self._progress_indeterminate(state):
            self.progress_bar.set_indeterminate(True)
            self.progress_bar.setFormat("%p%")
        else:
            self.progress_bar.set_indeterminate(False)
            if state.total > 0:
                if state.phase in {"search", "preview"}:
                    progress_value = state.found
                else:
                    progress_value = state.processed
                pct = min(100, int(100 * progress_value / state.total))
                self.progress_bar.setValue(pct)
            elif state.phase == "done":
                self.progress_bar.setValue(100)
            else:
                self.progress_bar.setValue(0)
                self.progress_bar.setTextVisible(False)
            if state.phase == "download":
                speed = self._format_speed_bps(state.speed_bps)
                count = tr("main.progress.download_count", files=state.files, total=state.total)
                if speed:
                    self.progress_bar.setFormat(f"%p% · {count} · {speed}")
                elif state.total > 0:
                    self.progress_bar.setFormat(f"%p% · {count}")
                else:
                    self.progress_bar.setFormat("%p%")
            elif state.phase in {"search", "preview"} and state.total > 0:
                self.progress_bar.setFormat(
                    tr("main.progress.media_count", found=state.found, total=state.total),
                )
            else:
                self.progress_bar.setFormat("%p%")

        self._update_flood_label(state)
        current = (state.current or "").strip()
        flood_active = self._flood_remaining(state.flood_wait_deadline) > 0
        dash = tr("common.dash")
        if flood_active:
            self.progress_detail_label.setText(self._progress_detail_snapshot or dash)
        else:
            if current and "floodwait" not in current.casefold():
                self._progress_detail_snapshot = current
            self.progress_detail_label.setText(current or dash)
        if state.phase == "verify":
            self.progress_stats_label.setText(
                tr(
                    "main.progress.verify_stats",
                    proc=state.processed,
                    total=state.total or dash,
                    files=state.files,
                    media=state.media_total or dash,
                    skipped=state.skipped,
                ),
            )
        elif state.phase == "search" and state.total > 0:
            self.progress_stats_label.setText(
                tr(
                    "main.progress.search_stats",
                    found=state.found,
                    total=state.total,
                    proc=state.processed,
                ),
            )
        elif state.phase == "preview" and state.total > 0:
            self.progress_stats_label.setText(
                tr(
                    "main.progress.preview_stats",
                    found=state.found,
                    total=state.total,
                    proc=state.processed,
                ),
            )
        elif state.phase == "download" and state.total > 0:
            self.progress_stats_label.setText(
                tr(
                    "main.progress.download_stats",
                    proc=state.processed,
                    total=state.total,
                    files=state.files,
                    skipped=state.skipped,
                ),
            )
        elif state.phase == "benchmark" and state.total > 0:
            self.progress_stats_label.setText(
                tr("autotune.progress.status", step=state.processed, total=state.total),
            )
        else:
            self.progress_stats_label.setText(
                tr(
                    "main.progress.generic_stats",
                    found=state.found,
                    proc=state.processed,
                    total=state.total or dash,
                    files=state.files,
                    skipped=state.skipped,
                ),
            )
        self._handle_progress_alert(state)
        self._update_queue_batch_ui(state)
        if self.worker_thread and self.worker_thread.is_alive():
            self._crash_recorder.note_progress(state)

    def _tick_crash_heartbeat(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self._crash_recorder.heartbeat(force=True)

    def _check_crash_startup(self) -> None:
        info = startup_crash_info()
        if not info:
            return
        self._pending_crash_info = info
        self._show_crash_banner(info)
        logging.warning(tr("log.crash.dump_found", path=info.dump_path))

    def _journal_tab_index(self) -> int | None:
        for index in range(self.tabs.count()):
            if self.tabs.widget(index) is self._log_tab:
                return index
        return None

    def _set_journal_tab_alert(self, active: bool) -> None:
        index = self._journal_tab_index()
        if index is None:
            return
        self.tabs.setTabText(index, tr("main.tab.log_alert") if active else tr("main.tab.log"))

    def _show_crash_banner(self, info: CrashStartupInfo) -> None:
        self.crash_banner_title.setText(info.summary)
        self.crash_banner_detail.setText(info.detail)
        self.crash_banner.setVisible(True)
        self._set_journal_tab_alert(True)
        self.status_label.setText(tr("main.status.crash"))
        QTimer.singleShot(
            0,
            lambda: show_warning(
                self,
                tr("main.crash.dialog_title"),
                tr("main.crash.dialog_body", summary=info.summary, detail=info.detail),
            ),
        )

    def _dismiss_crash_banner(self) -> None:
        self.crash_banner.setVisible(False)
        self._set_journal_tab_alert(False)
        if not (self.worker_thread and self.worker_thread.is_alive()):
            self.status_label.setText(tr("main.status.ready"))

    def _open_crash_dump_file(self) -> None:
        import os

        if not self._pending_crash_info:
            return
        path = self._pending_crash_info.dump_path
        if not path.is_file():
            show_warning(self, tr("main.crash.dialog_title"), tr("main.dump.warn_not_found"))
            return
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            open_path_in_file_manager(path.parent)

    def _open_crashes_dir(self) -> None:
        open_path_in_file_manager(CRASHES_DIR)

    def _restore_form_from_crash_dump(self) -> None:
        if not self._pending_crash_info or not self._pending_crash_info.form_snapshot:
            show_warning(self, tr("main.crash.dialog_title"), tr("main.dump.warn_no_form"))
            return
        snapshot = snapshot_from_mapping(self._pending_crash_info.form_snapshot)
        if snapshot is None:
            show_warning(self, tr("main.crash.dialog_title"), tr("main.dump.warn_parse_failed"))
            return
        blank = SettingsData()
        apply_snapshot_to_settings(blank, snapshot)
        self._apply_search_form_from_settings(blank)
        self.tabs.setCurrentIndex(0)
        show_info(
            self,
            tr("main.dump.info_restored_title"),
            tr("main.dump.info_restored_body"),
        )

    def _reset_progress(self) -> None:
        self._flood_timer.stop()
        self.progress_flood_label.clear()
        self.progress_flood_label.setVisible(False)
        self._progress_detail_snapshot = ""
        self._last_progress_state = None
        self.progress_bar.set_indeterminate(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFormat("%p%")
        self.queue_batch_bar.setVisible(False)
        self.queue_batch_bar.setValue(0)
        self.queue_progress_label.clear()
        self.queue_progress_label.setVisible(False)
        self._queue_download_active = False
        self._clear_queue_highlight()
        self._update_progress(ProgressState())

    def _set_running_state(self, running: bool) -> None:
        self.login_btn.setEnabled(not running)
        self.qr_login_btn.setEnabled(not running)
        self.reset_session_btn.setEnabled(not running)
        self.reset_journal_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.hashtag_entry.setEnabled(not running)
        self.date_from_picker.set_enabled(not running)
        self.date_to_picker.set_enabled(not running)
        self.filter_max_posts_spin.setEnabled(not running)
        self.channel_filter_entry.setEnabled(not running)
        self.pause_btn.setEnabled(running)
        self._autotune_run_btn.setEnabled(not running and not self._autotune_running)
        self._autotune_apply_btn.setEnabled(not running and not self._autotune_running and self._autotune_profile is not None)
        for btn in (
            self.queue_add_btn,
            self.queue_remove_btn,
            self.queue_up_btn,
            self.queue_down_btn,
            self.queue_clear_btn,
            self.queue_download_btn,
            self.save_template_btn,
            self.rename_template_btn,
            self.delete_template_btn,
        ):
            btn.setEnabled(not running)
        self.template_combo.setEnabled(not running)
        self.queue_list.setEnabled(not running)
        if running:
            for btn in (self.once_btn, self.preview_btn, self.verify_btn):
                btn.setEnabled(False)
        else:
            self.task_paused.clear()
            self._sync_pause_button()
            self._update_download_buttons()

    def _clear_log(self) -> None:
        self.log_text.clear()

    def _open_app_log_file(self) -> None:
        import os

        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not LOG_FILE.exists():
            LOG_FILE.touch()
        if sys.platform.startswith("win"):
            os.startfile(LOG_FILE)  # type: ignore[attr-defined]
        else:
            open_path_in_file_manager(LOG_FILE.parent)

    def _open_logs_dir(self) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        open_path_in_file_manager(LOGS_DIR)

    def _set_log_autoscroll(self, checked: bool) -> None:
        self._log_autoscroll = checked

    def _poll_log(self) -> None:
        lines: list[str] = []
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            lines.append(message)
        if lines:
            self.log_text.appendPlainText("\n".join(lines))
        if lines and self._log_autoscroll:
            bar = self.log_text.verticalScrollBar()
            bar.setValue(bar.maximum())

    def showEvent(self, event) -> None:
        super().showEvent(event)

    def _remember_hashtags_from_settings(self, settings: SettingsData) -> None:
        tags = parse_hashtag_list(settings.hashtag, settings.extra_hashtags)
        self._remember_hashtag_tags(tags)

    def _remember_hashtag_tags(self, tags: list[str]) -> None:
        clean = normalize_hashtag_queue(tags)
        if not clean:
            return
        record_hashtags_used(clean)
        self._hashtag_history.refresh()

    def closeEvent(self, event) -> None:
        if self.login_thread and self.login_thread.is_alive():
            if not ask_yes_no(self, tr("main.exit.login_title"), tr("main.exit.login_body")):
                event.ignore()
                return
            self.cancel_login()
            self.login_thread.join(timeout=5)
        if self.worker_thread and self.worker_thread.is_alive():
            if not ask_yes_no(
                self,
                tr("main.exit.login_title"),
                tr("main.exit.worker_body"),
            ):
                event.ignore()
                return
            self.stop_event.set()
            self.worker_thread.join(timeout=15)
        self._crash_recorder.finish_ok()
        self._persist_settings()
        event.accept()
