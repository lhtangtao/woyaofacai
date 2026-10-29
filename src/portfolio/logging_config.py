"""Unified operational and diagnostics logging for Money Portfolio.

Supports:
- Dual logging: project root logs/ directory + AppLocalDataLocation
- RotatingFileHandler with size limit and backups
- Thread-aware and module-aware formatting
- Console output (StreamHandler)
- Timing tracking helper for diagnosing UI hangs and slow queries
"""
import logging
import sys
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from portfolio.paths import DATABASE_PATH

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(threadName)s] [%(name)s]: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


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
