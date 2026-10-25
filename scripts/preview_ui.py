"""Capture actual native window states; all source reads use the application's read-only service."""
import argparse
import json
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from portfolio.demo import build_demo
from portfolio.ui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    temp = tempfile.TemporaryDirectory(prefix="money3-ui-")
    w = MainWindow(settings=False)
    w.show()
    w.open_database(args.db or build_demo(Path(temp.name) / "demo.sqlite3"))
    start = time.monotonic()
    captures = []
    cases = [(1280, 800, "default"), (1440, 900, "default"), (1920, 1080, "default"), (1280, 800, "drawer"),
             (1440, 900, "advanced"), (1280, 800, "empty"), (1440, 900, "dark"), (1440, 900, "collapsed"), (1280, 800, "error")]

    def capture(width, height, name):
        if w.current_page is None and not (name == "error" and w.banner.property("state") == "error"):
            QTimer.singleShot(60, lambda: capture(width, height, name))
            return
        w.grab().save(str(args.output / f"{name}-{width}x{height}.png"))
        captures.append({"name": name, "requested": [width, height], "actual": [w.width(), w.height()],
                         "table_viewport": [w.table.viewport().width(), w.table.viewport().height()],
                         "platform": app.platformName(), "matched": w.current_page.total if w.current_page else None})
        w.setMinimumSize(1100, 700)
        QTimer.singleShot(100, advance)

    def advance():
        if not cases:
            (args.output / "geometry.json").write_text(json.dumps(captures, ensure_ascii=False, indent=2))
            print(json.dumps(captures, ensure_ascii=False), flush=True)
            w.close()
            app.quit()
            return
        width, height, name = cases.pop(0)
        w.drawer.clear()
        w.table.clearSelection()
        w.setMinimumSize(width, height)
        w.resize(width, height)
        w.advanced_button.setChecked(name == "advanced")
        if name == "drawer" and w.current_page.rows:
            w.table.setCurrentIndex(w.table.model().index(0, 0))
            w.load_provenance()
        elif name == "advanced":
            w.include_reference.setChecked(True)
            w.include_reversed.setChecked(True)
        elif name == "empty":
            w.search.setText("NO_MATCH_FOR_UI_CHECK")
            w.search_timer.stop()
            w.filters_changed()
        elif name == "dark":
            w.reset_filters()
            w._set_theme("dark")
        elif name == "collapsed":
            w._set_theme("light")
            w.navigation.toggle()
        elif name == "error":
            w.open_database(Path(temp.name) / "missing.sqlite3")
        QTimer.singleShot(250, lambda: capture(width, height, name))

    def ready():
        if w.current_page is not None:
            advance()
        elif time.monotonic() - start < 30:
            QTimer.singleShot(50, ready)
        else:
            print(w.banner.text(), flush=True)
            app.quit()
    QTimer.singleShot(50, ready)
    QTimer.singleShot(60000, app.quit)
    app.exec()
    temp.cleanup()


if __name__ == "__main__":
    main()
