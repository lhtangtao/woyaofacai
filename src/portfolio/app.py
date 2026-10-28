import argparse
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication

from portfolio.demo import build_demo
from portfolio.paths import DATABASE_PATH
from portfolio.ui.main_window import MainWindow


def main(argv=None):
    parser = argparse.ArgumentParser(description="Money Portfolio：本地只读投资管理桌面应用。")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db", type=Path, help="只读打开选中的 SQLite，不创建或迁移源库")
    source.add_argument("--demo", action="store_true", help="创建临时合成示例库，不显示为真实资产")
    parser.add_argument("--no-settings", action="store_true", help="不读取或保存窗口设置")
    parser.add_argument("--smoke-ms", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--smoke-page", type=int, default=2, help=argparse.SUPPRESS)
    parser.add_argument("--smoke-report", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-export", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--rebuild-derived", choices=("changed", "all"), help="启动前安全生成全部历史人员派生数据")
    args = parser.parse_args(argv)
    if not args.demo and args.rebuild_derived:
        from portfolio.derived import prepare_startup
        prepare_startup(args.db or DATABASE_PATH,args.rebuild_derived,background=True)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("我要发财")
    app.setApplicationDisplayName("我要发财")
    app.setOrganizationName("MoneyPortfolio")
    from portfolio.ui.icons import app_icon
    app.setWindowIcon(app_icon())
    if not args.no_settings:
        from portfolio.logging_config import configure_logging
        configure_logging(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs")
    window = MainWindow(settings=not args.no_settings)
    temporary = tempfile.TemporaryDirectory(prefix="money3-demo-") if args.demo else None
    if temporary:
        args.db = build_demo(Path(temporary.name) / "合成示例.sqlite3")
    elif args.db is None:
        # Local Money3 data wins over a previously saved path into another project.
        if DATABASE_PATH.is_file() and DATABASE_PATH.stat().st_size:
            args.db = DATABASE_PATH
        elif window.settings:
            saved = Path(str(window.settings.value('default_database', '')))
            if saved.is_file() and saved.is_relative_to(DATABASE_PATH.parent.parent):
                args.db = saved
    window.show()
    if args.db:
        window.open_database(args.db)
    if args.smoke_ms > 0:
        window.navigation.select(args.smoke_page, args.smoke_report)
        def finish():
            window.navigation.select(args.smoke_page, args.smoke_report)
            app.processEvents()
            if args.smoke_export and window.analytics:
                from portfolio.services.export_service import export_report
                export_report(window.analytics.reports["当前持仓"], args.smoke_export,
                              parameters={"scope": str(window.analytics.scope), "source_identity": window.analytics.source_identity})
            if args.screenshot:
                window.grab().save(str(args.screenshot))
            window.close()
            app.quit()
        QTimer.singleShot(args.smoke_ms, finish)
    result = app.exec()
    if temporary:
        temporary.cleanup()
    return result
