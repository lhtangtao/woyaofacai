"""Unified operational and diagnostics logging for Money Portfolio.

Supports:
- Dual logging: project root logs/ directory + AppLocalDataLocation
- RotatingFileHandler with size limit and backups
- Thread-aware and module-aware formatting
- Console output (StreamHandler)
- Timing tracking helper for diagnosing UI hangs and slow queries
"""
import faulthandler
import logging
import os
import platform
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from portfolio.paths import DATABASE_PATH

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(threadName)s] [%(name)s]: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_FAULT_HANDLER_STREAM = None
_CRASH_WRITE_LOCK = threading.Lock()
_QT_MSG_HANDLER_INSTALLED = False


def get_project_logs_dir() -> Path:
    """Return logs directory inside the project root."""
    try:
        project_root = DATABASE_PATH.parent.parent
        logs_dir = project_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir
    except Exception:
        fallback = Path("./logs").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def configure_logging(directory=None, app_name="money3"):
    """Configure system-wide loggers with rotating file handlers and console stream."""
    try:
        formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

        portfolio_logger = logging.getLogger("portfolio")
        portfolio_logger.setLevel(logging.INFO)

        handlers = []

        # 1. Project-root log file: <project_root>/logs/money3.log
        project_logs = get_project_logs_dir()
        proj_handler = RotatingFileHandler(
            project_logs / f"{app_name}.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        proj_handler.setFormatter(formatter)
        handlers.append(proj_handler)

        # 2. System AppData log file (if directory provided)
        if directory:
            try:
                sys_dir = Path(directory)
                sys_dir.mkdir(parents=True, exist_ok=True)
                sys_handler = RotatingFileHandler(
                    sys_dir / f"{app_name}.log",
                    maxBytes=2 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
                sys_handler.setFormatter(formatter)
                handlers.append(sys_handler)
            except Exception:
                pass

        # 3. Console stream handler (stdout/stderr)
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

        # Remove existing handlers to avoid duplicates on re-configure
        for h in list(portfolio_logger.handlers):
            portfolio_logger.removeHandler(h)

        for h in handlers:
            portfolio_logger.addHandler(h)

        portfolio_logger.propagate = False
        portfolio_logger.info(f"=== {app_name} 日志系统初始化完成 | 日志文件: {project_logs / f'{app_name}.log'} ===")
        return project_logs / f"{app_name}.log"
    except Exception as e:
        sys.stderr.write(f"日志系统初始化异常: {e}\n")
        return None


@contextmanager
def log_timing(stage_name: str, logger: logging.Logger | None = None, warn_threshold_ms: float = 300.0):
    """Context manager for tracking elapsed time and detecting potential UI freeze/hangs."""
    log = logger or logging.getLogger("portfolio.timing")
    start = time.monotonic()
    log.debug(f"▶ 开始执行: {stage_name}")
    try:
        yield
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        log.error(f"✖ 执行失败: {stage_name} (耗时 {elapsed:.1f}ms): {e}", exc_info=True)
        raise
    else:
        elapsed = (time.monotonic() - start) * 1000
        if elapsed >= warn_threshold_ms:
            log.warning(f"⚠️ [慢操作警告] {stage_name} 耗时较长: {elapsed:.1f}ms (预警阈值: {warn_threshold_ms}ms)")
        else:
            log.info(f"✔ 执行完成: {stage_name} (耗时 {elapsed:.1f}ms)")


def enable_fault_handler(logs_dir: Path | None = None, filename: str = "crash_fault.log") -> Path | None:
    """启用 Python faulthandler，将原生崩溃（如 SIGSEGV/SIGABRT）堆栈记录到日志文件。"""
    global _FAULT_HANDLER_STREAM
    try:
        target_dir = Path(logs_dir) if logs_dir is not None else get_project_logs_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        stream = target.open("a", encoding="utf-8")
        stream.write(f"\n=== [{datetime.now().strftime(DATE_FORMAT)}] 启用原生崩溃堆栈采集 ===\n")
        stream.flush()
        faulthandler.enable(file=stream, all_threads=True)
        old_stream = _FAULT_HANDLER_STREAM
        _FAULT_HANDLER_STREAM = stream
        if old_stream is not None and old_stream is not stream:
            try:
                old_stream.close()
            except Exception:
                pass
        return target
    except Exception:
        return None


def _append_crash_record(
    crash_log: Path,
    app_name: str,
    source: str,
    exc_type,
    exc_value,
    exc_tb,
    *,
    thread_name: str | None = None,
) -> None:
    """将未捕获异常的完整上下文与堆栈追加写入 crash.log。"""
    if exc_type is not None and isinstance(exc_type, type) and issubclass(exc_type, KeyboardInterrupt):
        return
    try:
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now().strftime(DATE_FORMAT)
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip()
        header = (
            f"\n==================== [{now_str}] 未捕获异常 ({source}) ====================\n"
            f"组件: {app_name} | PID: {os.getpid()} | 线程: {thread_name or threading.current_thread().name}\n"
            f"Python: {sys.version.split()[0]} | 平台: {platform.platform()}\n"
            f"------------------------------------------------------------------------\n"
            f"{tb_text}\n"
            f"========================================================================\n"
        )
        with _CRASH_WRITE_LOCK:
            with crash_log.open("a", encoding="utf-8") as fh:
                fh.write(header)
                fh.flush()
    except Exception:
        pass
    try:
        logging.getLogger("portfolio.crash").critical(
            "捕获到未处理异常 (%s | 线程=%s): %s: %s\n%s",
            source,
            thread_name or threading.current_thread().name,
            getattr(exc_type, "__name__", str(exc_type)),
            exc_value,
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
    except Exception:
        pass


def install_crash_hooks(
    app_name: str = "money3",
    logs_dir: Path | None = None,
    *,
    enable_faulthandler_log: bool = True,
) -> Path | None:
    """安装主线程与子线程未捕获异常钩子，并按需开启 faulthandler。"""
    try:
        target_dir = Path(logs_dir) if logs_dir is not None else get_project_logs_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        crash_log = target_dir / "crash.log"

        prev_sys_hook = sys.excepthook
        prev_thread_hook = threading.excepthook

        def _sys_excepthook(exc_type, exc_value, exc_tb) -> None:
            _append_crash_record(
                crash_log,
                app_name,
                "sys.excepthook",
                exc_type,
                exc_value,
                exc_tb,
                thread_name=threading.current_thread().name,
            )
            if prev_sys_hook is not None and prev_sys_hook is not _sys_excepthook:
                try:
                    prev_sys_hook(exc_type, exc_value, exc_tb)
                except Exception:
                    pass

        def _threading_excepthook(args) -> None:
            thread_obj = getattr(args, "thread", None)
            t_name = getattr(thread_obj, "name", "unknown") if thread_obj is not None else "unknown"
            _append_crash_record(
                crash_log,
                app_name,
                "threading.excepthook",
                args.exc_type,
                args.exc_value,
                args.exc_traceback,
                thread_name=t_name,
            )
            if prev_thread_hook is not None and prev_thread_hook is not _threading_excepthook:
                try:
                    prev_thread_hook(args)
                except Exception:
                    pass

        sys.excepthook = _sys_excepthook
        threading.excepthook = _threading_excepthook

        if enable_faulthandler_log:
            enable_fault_handler(logs_dir=target_dir, filename="crash_fault.log")

        return crash_log
    except Exception:
        return None


def install_qt_message_handler(logs_dir: Path | None = None) -> None:
    """安装 Qt 消息处理器，将 QtCritical / QtFatal 级错误写入 crash.log。"""
    global _QT_MSG_HANDLER_INSTALLED
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        target_dir = Path(logs_dir) if logs_dir is not None else get_project_logs_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        crash_log = target_dir / "crash.log"

        def _qt_message_handler(mode, context, message) -> None:
            level_map = {
                QtMsgType.QtCriticalMsg: "QtCritical",
                QtMsgType.QtFatalMsg: "QtFatal",
            }
            level_label = level_map.get(mode)
            if level_label:
                now_str = datetime.now().strftime(DATE_FORMAT)
                ctx_file = getattr(context, "file", None) or "-"
                ctx_line = getattr(context, "line", 0) or 0
                ctx_func = getattr(context, "function", None) or "-"
                line = (
                    f"\n[{now_str}] [{level_label}] [PID:{os.getpid()}] "
                    f"[{ctx_file}:{ctx_line} {ctx_func}] {message}\n"
                )
                try:
                    with _CRASH_WRITE_LOCK:
                        with crash_log.open("a", encoding="utf-8") as fh:
                            fh.write(line)
                            fh.flush()
                except Exception:
                    pass
            try:
                sys.stderr.write(f"[Qt] {message}\n")
                sys.stderr.flush()
            except Exception:
                pass

        qInstallMessageHandler(_qt_message_handler)
        _QT_MSG_HANDLER_INSTALLED = True
    except Exception:
        pass
