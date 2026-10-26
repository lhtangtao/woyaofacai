"""
Verification script for MonitoringPage Watchlist Table Layout
Tests:
1. Correct column count (10) and updated concise headers
2. Correct column widths (82, 76, 74, 74, 76, 76, 76, 70, 42, 36)
3. stretchLastSection is False (prevents '操作' column from blowing up)
4. Data rendering with actual market items (no truncation of MA numbers)
5. Off-screen visual render saved to artifacts
"""
import os
import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFontMetrics, QPixmap, QPainter

app = QApplication.instance() or QApplication(sys.argv)

from portfolio.ui.monitor_page import MonitoringPage

def test_table_headers_and_widths():
    print("[1/4] Verifying table headers and widths...")
    page = MonitoringPage(None)
    table = page.table
    assert table.columnCount() == 10, f"Expected 10 columns, got {table.columnCount()}"
    
    expected_headers = [
        "标的 / 来源",
        "现价 / 涨跌",
        "MA20",
        "MA50",
        "MA200",
        "MA250",
        "距MA200",
        "短/中/长",
        "规则",
        "操作",
    ]
    actual_headers = [table.horizontalHeaderItem(i).text() for i in range(10)]
    assert actual_headers == expected_headers, f"Header mismatch: {actual_headers} != {expected_headers}"
    print("  -> Headers match:", actual_headers)
    
    expected_widths = [82, 76, 74, 74, 76, 76, 76, 70, 42, 36]
    actual_widths = [table.columnWidth(i) for i in range(10)]
    assert actual_widths == expected_widths, f"Width mismatch: {actual_widths} != {expected_widths}"
    print("  -> Column widths match:", actual_widths)
    
    assert not table.horizontalHeader().stretchLastSection(), "stretchLastSection must be False!"
    print("  -> stretchLastSection is confirmed False.")

def test_data_population_and_elision():
    print("[2/4] Verifying data population and font metrics safety margins...")
    page = MonitoringPage(None)
    
    page.targets = [
        {"analysis_symbol": "AAOI.US", "sources": [{"held_symbol": "AAOX.US", "analysis_symbol": "AAOI.US"}], "source_type": "HELD", "archive_state": "HELD"},
        {"analysis_symbol": "AVGO.US", "sources": [{"held_symbol": "AVGX.US", "analysis_symbol": "AVGO.US"}], "source_type": "HELD", "archive_state": "HELD"},
        {"analysis_symbol": "GOOGL.US", "sources": [{"held_symbol": "GOLL.US", "analysis_symbol": "GOOGL.US"}], "source_type": "HELD", "archive_state": "HELD"},
        {"analysis_symbol": "LITE.US", "sources": [{"held_symbol": "LITX.US", "analysis_symbol": "LITE.US"}], "source_type": "HELD", "archive_state": "HELD"},
        {"analysis_symbol": "MRVL.US", "sources": [{"held_symbol": "MRVL.US", "analysis_symbol": "MRVL.US"}], "source_type": "HELD", "archive_state": "HELD"},
    ]
    page.market = {
        "AAOI.US": {"price": 65.64, "change": -3.57, "metrics": {"MA20": 98.06, "MA50": 104.25, "MA200": 113.13, "MA250": 98.52, "DEV200": -41.90, "SHORT": "弱", "MEDIUM": "弱", "LONG": "弱"}},
        "AVGO.US": {"price": 322.07, "change": -0.36, "metrics": {"MA20": 348.12, "MA50": 358.45, "MA200": 368.90, "MA250": 385.12, "DEV200": -12.18, "SHORT": "弱", "MEDIUM": "弱", "LONG": "弱"}},
        "GOOGL.US": {"price": 320.85, "change": -1.04, "metrics": {"MA20": 343.20, "MA50": 342.15, "MA200": 341.05, "MA250": 332.80, "DEV200": -5.91, "SHORT": "弱", "MEDIUM": "弱", "LONG": "弱"}},
        "LITE.US": {"price": 747.00, "change": -0.74, "metrics": {"MA20": 953.20, "MA50": 925.10, "MA200": 788.40, "MA250": 690.15, "DEV200": -5.21, "SHORT": "弱", "MEDIUM": "弱", "LONG": "弱"}},
        "MRVL.US": {"price": 225.60, "change": 0.95, "metrics": {"MA20": 256.30, "MA50": 242.10, "MA200": 179.80, "MA250": 161.13, "DEV200": 25.70, "SHORT": "弱", "MEDIUM": "弱", "LONG": "强"}},
    }
    
    page.draw_table()
    
    assert page.table.rowCount() == 5
    fm = QFontMetrics(page.table.font())
    
    # Verify MA columns in AVGO row (row 1)
    ma20_text = page.table.item(1, 2).text()
    ma50_text = page.table.item(1, 3).text()
    ma200_text = page.table.item(1, 4).text()
    ma250_text = page.table.item(1, 5).text()
    dev200_text = page.table.item(1, 6).text()
    rule_text = page.table.item(1, 8).text()
    op_text = page.table.item(1, 9).text()
    
    print(f"  AVGO row data: MA20={ma20_text}, MA50={ma50_text}, MA200={ma200_text}, MA250={ma250_text}, DEV200={dev200_text}, 规则={rule_text}, 操作={op_text}")
    assert ma20_text == "348.12", f"Expected 348.12, got {ma20_text}"
    assert dev200_text == "-12.18%", f"Expected -12.18%, got {dev200_text}"
    assert rule_text == "无", f"Expected 无, got {rule_text}"
    assert op_text == "➕", f"Expected ➕, got {op_text}"
    
    # Check text fits with comfortable safety margin
    for col_idx, text in enumerate([ma20_text, ma50_text, ma200_text, ma250_text, dev200_text], start=2):
        col_w = page.table.columnWidth(col_idx)
        text_w = fm.horizontalAdvance(text)
        margin = col_w - text_w
        print(f"  Col {col_idx} ({text}): column={col_w}px, text={text_w}px, margin={margin}px")
        assert margin >= 20, f"Margin {margin}px is too tight!"

def test_cell_click_handler():
    print("[3/4] Verifying cell clicked handler on columns 8 and 9...")
    page = MonitoringPage(None)
    called = []
    page._open_rule_dialog = lambda default_symbol="": called.append(default_symbol)
    
    page.table.setRowCount(1)
    from PySide6.QtWidgets import QTableWidgetItem
    it = QTableWidgetItem("AAOI")
    it.setData(Qt.ItemDataRole.UserRole, "AAOI.US")
    page.table.setItem(0, 0, it)
    
    # Click col 8 (规则)
    page._on_cell_clicked(0, 8)
    assert called == ["AAOI.US"], f"Expected AAOI.US, got {called}"
    
    # Click col 9 (操作)
    page._on_cell_clicked(0, 9)
    assert called == ["AAOI.US", "AAOI.US"]
    print("  -> Both col 8 and col 9 successfully trigger rule dialog for target stock.")

def test_offscreen_render():
    print("[4/4] Rendering table snapshot to artifacts...")
    page = MonitoringPage(None)
    page.resize(1300, 700)
    page.targets = [
        {"analysis_symbol": "AAOI.US", "sources": [{"held_symbol": "AAOX.US", "analysis_symbol": "AAOI.US"}], "source_type": "HELD", "archive_state": "HELD"},
        {"analysis_symbol": "AVGO.US", "sources": [{"held_symbol": "AVGX.US", "analysis_symbol": "AVGO.US"}], "source_type": "HELD", "archive_state": "HELD"},
        {"analysis_symbol": "GOOGL.US", "sources": [{"held_symbol": "GOLL.US", "analysis_symbol": "GOOGL.US"}], "source_type": "HELD", "archive_state": "HELD"},
        {"analysis_symbol": "LITE.US", "sources": [{"held_symbol": "LITX.US", "analysis_symbol": "LITE.US"}], "source_type": "HELD", "archive_state": "HELD"},
        {"analysis_symbol": "MRVL.US", "sources": [{"held_symbol": "MRVL.US", "analysis_symbol": "MRVL.US"}], "source_type": "HELD", "archive_state": "HELD"},
    ]
    page.market = {
        "AAOI.US": {"price": 65.64, "change": -3.57, "metrics": {"MA20": 98.06, "MA50": 104.25, "MA200": 113.13, "MA250": 98.52, "DEV200": -41.90, "SHORT": "弱", "MEDIUM": "弱", "LONG": "弱"}},
        "AVGO.US": {"price": 322.07, "change": -0.36, "metrics": {"MA20": 348.12, "MA50": 358.45, "MA200": 368.90, "MA250": 385.12, "DEV200": -12.18, "SHORT": "弱", "MEDIUM": "弱", "LONG": "弱"}},
        "GOOGL.US": {"price": 320.85, "change": -1.04, "metrics": {"MA20": 343.20, "MA50": 342.15, "MA200": 341.05, "MA250": 332.80, "DEV200": -5.91, "SHORT": "弱", "MEDIUM": "弱", "LONG": "弱"}},
        "LITE.US": {"price": 747.00, "change": -0.74, "metrics": {"MA20": 953.20, "MA50": 925.10, "MA200": 788.40, "MA250": 690.15, "DEV200": -5.21, "SHORT": "弱", "MEDIUM": "弱", "LONG": "弱"}},
        "MRVL.US": {"price": 225.60, "change": 0.95, "metrics": {"MA20": 256.30, "MA50": 242.10, "MA200": 179.80, "MA250": 161.13, "DEV200": 25.70, "SHORT": "弱", "MEDIUM": "弱", "LONG": "强"}},
    }
    page.draw_table()
    
    # Render table to QPixmap
    page.show()
    app.processEvents()
    page.table.resize(720, 360)
    app.processEvents()
    pix = page.table.grab()
    artifact_dir = "/Users/tangtao/.gemini/antigravity/brain/05473829-95f3-483b-b785-2993eed23d26"
    out_path = os.path.join(artifact_dir, "monitor_table_fixed.png")
    pix.save(out_path)
    print(f"  -> Snapshot successfully saved to: {out_path}")

if __name__ == "__main__":
    test_table_headers_and_widths()
    test_data_population_and_elision()
    test_cell_click_handler()
    test_offscreen_render()
    print("\n✅ All 4 Monitoring Table Layout tests passed successfully!")
