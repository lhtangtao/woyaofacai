import logging
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from portfolio.db.connection import SourceError


class Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, task):
        super().__init__()
        self.signals = Signals()
        self.cancel = threading.Event()
        self.task = task

    def run(self):
        try:
            result = self.task(self.cancel)
            if not self.cancel.is_set():
                self.signals.done.emit(result)
        except Exception as exc:
            if not self.cancel.is_set():
                frames = [(Path(f.filename).name, f.lineno, f.name) for f in traceback.extract_tb(exc.__traceback__)]
                logging.getLogger("portfolio").error("Background failure type=%s frames=%s", type(exc).__name__, frames)
                # 不把可能含 token/账号的底层异常字符串显示或写日志。
                self.signals.error.emit(str(exc) if isinstance(exc, (SourceError, ValueError)) else
                                        "查询失败或超时。请检查数据源与筛选条件，重新选择数据库或刷新后重试。")
        finally:
            self.signals.finished.emit()
