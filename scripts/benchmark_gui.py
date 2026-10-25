"""Measure GUI heartbeat while the actual background pipeline reads a synthetic DB."""
import argparse
import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from portfolio.ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--raw-only", action="store_true", help="只测交易首屏，不进入财务报表")
    args = parser.parse_args()
    app = QApplication([])
    window = MainWindow(settings=False)
    window.show()
    started, ticks = time.perf_counter(), []
    state = {}
    def tick():
        now = time.perf_counter()
        ticks.append(now)
        if window.current_page is not None and "first_page_seconds" not in state:
            state["first_page_seconds"] = now - started
        complete = window.current_page is not None if args.raw_only else window.analytics is not None
        if complete or now - started > 120:
            gaps = [b - a for a, b in zip(ticks, ticks[1:])]
            state.update(elapsed_seconds=now - started, timer_ticks=len(ticks), max_heartbeat_gap_seconds=max(gaps, default=0),
                         completed=complete, raw_only=args.raw_only, mode="offscreen" if app.platformName() == "offscreen" else app.platformName())
            with args.report.open("x", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
            print(json.dumps(state, indent=2), flush=True)
            window.close()
            app.quit()
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(tick)
    timer.start()
    if not args.raw_only:
        window.navigation.setCurrentRow(2)
    window.open_database(args.db)
    app.exec()


if __name__ == "__main__":
    main()
