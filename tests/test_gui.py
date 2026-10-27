from PySide6.QtCore import Qt

from portfolio.domain.models import TRADE_COLUMNS
from portfolio.ui.main_window import MainWindow


def window(qtbot):
    widget = MainWindow(settings=False)
    widget.navigation.setCurrentRow(0)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def connect(qtbot, widget, path):
    widget.open_database(path)
    qtbot.waitUntil(lambda: widget.current_page is not None, timeout=15000)


def test_window_without_database_never_creates_one(qtbot):
    widget = window(qtbot)
    assert widget.current_page is None and not widget.filters_widget.isEnabled()
    assert "未连接" in widget.source_label.text()
    assert widget.table.model().columnCount() == 21
    widget.close()


def test_window_connect_filter_ledger_and_provenance(qtbot, demo_path):
    widget = window(qtbot)
    connect(qtbot, widget, demo_path)
    assert "合成演示" in widget.source_label.text()
    assert widget.current_page.total == 3
    expected_headers = list(TRADE_COLUMNS)
    expected_headers[11] = '操作后成本($)'
    assert [widget.table.model().headerData(i, Qt.Orientation.Horizontal) for i in range(16)] == expected_headers
    assert not widget.table.isColumnHidden(11)
    widget.person.setCurrentIndex(widget.person.findData(2))
    qtbot.waitUntil(lambda: widget.current_page is not None and widget.current_page.total == 1)
    widget.table.setCurrentIndex(widget.table.model().index(0, 0))
    qtbot.waitUntil(lambda: "已遮盖" in widget.detail.toPlainText())
    assert "demo-secret-not-a-real-key" not in widget.detail.toPlainText()
    widget.ledger.setCurrentIndex(1)
    qtbot.waitUntil(lambda: widget.current_page is not None and widget.current_page.total == 2)
    assert not widget.person.isEnabled()
    widget.accounts.actions_by_id[2].setChecked(True)
    qtbot.waitUntil(lambda: widget.current_page is not None and widget.current_page.total == 0)
    assert "暂无数据" in widget.banner.text()
    widget.close()


def test_bad_source_has_visible_actionable_error(qtbot, tmp_path):
    widget = window(qtbot)
    widget.open_database(tmp_path / "not-created.sqlite3")
    qtbot.waitUntil(lambda: "不存在" in widget.banner.text())
    assert not (tmp_path / "not-created.sqlite3").exists()
    assert widget.current_page is None
    widget.close()


def test_rapid_filter_changes_never_show_previous_scope(qtbot, demo_path):
    widget = window(qtbot)
    connect(qtbot, widget, demo_path)
    for person in (1, 2, 1, 2):
        widget.person.setCurrentIndex(widget.person.findData(person))
    qtbot.waitUntil(lambda: widget.current_page is not None and widget.current_page.scope.person_ids == (2,))
    assert widget.current_page.total == 1
    assert all(row.metadata["person_id"] == 2 for row in widget.current_page.rows)
    widget.navigation.setCurrentRow(1)
    assert widget.pages.currentIndex() == 1
    assert "READ_ONLY" in widget.source_details.toPlainText()
    widget.close()


def test_refresh_preserves_user_scope(qtbot, demo_path):
    widget = window(qtbot)
    connect(qtbot, widget, demo_path)
    widget.person.setCurrentIndex(widget.person.findData(2))
    widget.accounts.actions_by_id[1].setChecked(True)
    qtbot.waitUntil(lambda: widget.current_page is not None and widget.current_page.scope.person_ids == (2,))
    wanted = widget.current_page.scope
    widget.reconnect()
    qtbot.waitUntil(lambda: widget.current_page is not None)
    assert widget.current_page.scope == wanted
    assert widget.current_page.total == 1
    widget.close()
