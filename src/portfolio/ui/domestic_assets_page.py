"""Mac view of the separate local CNY fund ledger."""
from datetime import date
import json
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTabBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from portfolio.domestic.alipay import KINDS, parse_pdf
from portfolio.domestic.query import read_transaction, read_view
from portfolio.domestic.service import publish, recalculate

VIEWS = ("positions", "transactions", "monthly", "imports")


class DomesticAssetsPage(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.page = 1
        self.rows = []
        self.busy = False
        layout = QVBoxLayout(self)
        self.tabs = QTabBar()
        for name in ("基金持仓", "基金交易明细", "月度资产变化", "数据导入与校验"):
            self.tabs.addTab(name)
        layout.addWidget(self.tabs)
        filters = QHBoxLayout()
        self.as_of = QLineEdit(placeholderText="持仓日期 YYYY-MM-DD，留空取最近确认日")
        self.start = QLineEdit(placeholderText="确认起日 YYYY-MM-DD")
        self.end = QLineEdit(placeholderText="确认止日 YYYY-MM-DD")
        self.fund = QLineEdit(placeholderText="基金代码")
        self.kind = QComboBox()
        self.kind.addItem("全部类型", "")
        for kind in sorted(KINDS):
            self.kind.addItem(kind, kind)
        self.sort = QComboBox()
        self.sort.addItem("交易时间从新到旧", False)
        self.sort.addItem("交易时间从旧到新", True)
        for widget in (self.as_of, self.start, self.end, self.fund, self.kind, self.sort):
            filters.addWidget(widget)
        search = QPushButton("查询")
        search.clicked.connect(self.reset_page)
        filters.addWidget(search)
        layout.addLayout(filters)
        self.import_controls = QWidget()
        import_layout = QHBoxLayout(self.import_controls)
        import_layout.setContentsMargins(0, 0, 0, 0)
        for label, callback in (
            ("预检期初证明…", lambda: self.choose_pdf("opening_asset")),
            ("预检交易明细…", lambda: self.choose_pdf("transaction")),
            ("全量重算", self.rebuild),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            import_layout.addWidget(button)
        import_layout.addStretch()
        layout.addWidget(self.import_controls)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self.show_source)
        layout.addWidget(self.table, 1)
        pager = QHBoxLayout()
        self.previous = QPushButton("上一页")
        self.following = QPushButton("下一页")
        self.page_text = QLabel()
        self.previous.clicked.connect(lambda: self.move_page(-1))
        self.following.clicked.connect(lambda: self.move_page(1))
        pager.addWidget(self.previous)
        pager.addWidget(self.page_text)
        pager.addWidget(self.following)
        pager.addStretch()
        layout.addLayout(pager)
        self.tabs.currentChanged.connect(self.reset_page)

    def reset_page(self, *_):
        self.page = 1
        self.reload()

    def move_page(self, step):
        self.page = max(1, self.page + step)
        self.reload()

    def reload(self):
        view = VIEWS[self.tabs.currentIndex()]
        for widget in (self.start, self.end, self.kind, self.sort):
            widget.setVisible(view == "transactions")
        self.as_of.setVisible(view == "positions")
        self.fund.setVisible(view != "imports")
        self.import_controls.setVisible(view == "imports")
        options = dict(page=self.page)
        if view != "imports":
            options["fund"] = self.fund.text().strip()
        if view == "positions":
            options["as_of"] = self.as_of.text().strip()
        if view == "transactions":
            options.update(start=self.start.text().strip(), end=self.end.text().strip(),
                           kind=self.kind.currentData(), ascending=self.sort.currentData())
        def show(data):
            self.rows = data["rows"]
            columns = data["columns"]
            self.table.setColumnCount(len(columns))
            self.table.setHorizontalHeaderLabels([label for _, label in columns])
            self.table.setRowCount(len(self.rows))
            for i, row in enumerate(self.rows):
                for j, (key, _) in enumerate(columns):
                    value = row.get(key)
                    self.table.setItem(i, j, QTableWidgetItem(str(value) if value is not None else "—"))
            self.table.resizeColumnsToContents()
            self.previous.setEnabled(data["page"] > 1)
            self.following.setEnabled(data["page"] * data["size"] < data["total"])
            self.page_text.setText(f'第 {data["page"]} 页 · 共 {data["total"]} 条')
            lines = [data["raw_path"], data["calc_path"]]
            if data["as_of"]:
                lines.append(f'持仓日期 {data["as_of"]}')
            if data["stale"]:
                lines.append("原始数据已更新，当前计算结果尚未发布。")
            if view == "transactions":
                lines.append("双击交易查看原始记录与 PDF 页码。")
            if data["run"]:
                lines.append(f'最近计算 {data["run"]["completed_at"]} UTC · {data["run"]["status"]}')
            lines.extend(f'{row["check_code"]} {row["fund_code"] or ""} {row["detail"]}' for row in data["checks"])
            lines.extend(f'{row["source_filename"]} · 第 {row["source_page"]} 页 · {row["reason"]}' for row in data["issues"])
            self.status.setText("\n".join(lines))
        self.submit("domestic_read", lambda: read_view(view, **options), show)

    def submit(self, lane, task, on_success):
        def run(_cancel):
            try:
                return task(), ""
            except Exception as exc:
                return None, str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "操作未完成，请检查文件或数据库状态。"
        def ready(result):
            if lane == "domestic_write":
                self.busy = False
                self.import_controls.setEnabled(True)
            value, error = result
            if error:
                self.status.setText(error)
            else:
                on_success(value)
        self.host._submit(lane, run, ready)

    def choose_pdf(self, source_type):
        filename, _ = QFileDialog.getOpenFileName(self, "选择支付宝 PDF", "", "PDF (*.pdf)")
        if not filename:
            return
        snapshot = None
        if source_type == "opening_asset":
            value, ok = QInputDialog.getText(self, "期初日期", "填写证明的截止日", text="2021-11-11")
            if not ok:
                return
            try:
                snapshot = date.fromisoformat(value)
            except ValueError:
                self.status.setText("日期格式应为 YYYY-MM-DD。")
                return

        def confirm(doc):
            summary = doc.summary()
            issues = "\n".join(f'第 {entry["source_page"]} 页：{entry["reason"]}' for entry in doc.issues[:12])
            report = json.dumps(summary, ensure_ascii=False, indent=2)
            if QMessageBox.question(self, "确认导入国内资产", f"{report}\n{issues}\n\n正式导入此文件？") == QMessageBox.StandardButton.Yes:
                self.write(lambda: publish([doc]))
        self.submit("domestic_preview", lambda: parse_pdf(Path(filename), source_type, snapshot), confirm)

    def write(self, action):
        if self.busy:
            return
        self.busy = True
        self.import_controls.setEnabled(False)
        self.status.setText("正在后台处理国内资产…")
        self.submit("domestic_write", action, lambda _: self.reload())

    def rebuild(self):
        self.write(recalculate)

    def show_source(self, index, _column):
        if self.tabs.currentIndex() != 1 or not 0 <= index < len(self.rows):
            return
        raw_id = self.rows[index]["id"]
        def show(row):
            if row is not None:
                dialog = QMessageBox(self)
                dialog.setWindowTitle("支付宝原始交易")
                dialog.setTextFormat(Qt.TextFormat.PlainText)
                dialog.setText(f'{row["source_filename"]} · 第 {row["source_page"]} 页 · 表格第 {row["source_row_no"]} 行')
                dialog.setDetailedText(json.dumps(json.loads(row["raw_data"]), ensure_ascii=False, indent=2))
                dialog.exec()
        self.submit("domestic_source", lambda: read_transaction(raw_id), show)
