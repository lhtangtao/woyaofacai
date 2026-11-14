"""国内资产（支付宝 / 天天基金）交易明细页面（参考美股交易明细结构）。"""
from __future__ import annotations

import csv
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from portfolio.domestic.trades_query import query_domestic_trades
from portfolio.ui.trade_center_style import StatCard, STYLE as TRADE_CENTER_STYLE


def _ui_font(size: int = 12, *, bold: bool = False) -> QFont:
    f = QFont(".AppleSystemUIFont", size)
    f.setBold(bold)
    return f


def _fmt_num(val: float | None, decimals: int = 2) -> str:
    if val is None:
        return "—"
    return f"{val:,.{decimals}f}"


class _TradesWorker(QObject):
    finished = Signal(object, str)

    def run(self):
        try:
            self.finished.emit(query_domestic_trades(), "")
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(None, str(exc))


class DomesticTradesModel(QAbstractTableModel):
    COLUMNS = (
        ("trade_date", "确认日期", "text"),
        ("transaction_time", "交易时间", "text"),
        ("platform", "平台", "text"),
        ("fund_name", "基金名称", "text"),
        ("fund_code", "代码", "text"),
        ("fund_type", "基金类型", "text"),
        ("direction", "方向", "dir"),
        ("transaction_type_raw", "业务类型", "text"),
        ("confirmed_amount", "确认金额(CNY)", "money"),
        ("confirmed_shares", "确认份额", "shares"),
        ("confirmed_nav", "确认净值", "nav"),
        ("fee", "手续费(CNY)", "money"),
        ("status", "状态", "text"),
        ("order_id", "订单号", "text"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_rows: list[dict[str, Any]] = []
        self._page_rows: list[dict[str, Any]] = []
        self._page = 0
        self._page_size = 50
        self._sort_col = 0
        self._sort_order = Qt.SortOrder.DescendingOrder

    def set_rows(self, rows: list[dict[str, Any]], *, page_size: int | None = None, reset_page: bool = True):
        self._all_rows = list(rows)
        if page_size is not None:
            self._page_size = page_size
        if reset_page:
            self._page = 0
        self._apply_sort_and_slice()

    def set_page(self, page: int, *, page_size: int | None = None):
        if page_size is not None:
            self._page_size = page_size
        total_pages = max(1, (len(self._all_rows) + self._page_size - 1) // self._page_size)
        self._page = max(0, min(page, total_pages - 1))
        self._slice_page()

    @property
    def page(self) -> int:
        return self._page

    @property
    def total_pages(self) -> int:
        return max(1, (len(self._all_rows) + self._page_size - 1) // self._page_size)

    def row_at(self, row_idx: int) -> dict[str, Any] | None:
        return self._page_rows[row_idx] if 0 <= row_idx < len(self._page_rows) else None

    def _apply_sort_and_slice(self):
        if 0 <= self._sort_col < len(self.COLUMNS):
            key, _, fmt = self.COLUMNS[self._sort_col]
            is_num = fmt in ("money", "shares", "nav")
            self._all_rows.sort(
                key=lambda r: (
                    (0, float(r[key])) if (is_num and r.get(key) is not None)
                    else (1, 0.0) if is_num
                    else str(r.get(key) or "")
                ),
                reverse=(self._sort_order == Qt.SortOrder.DescendingOrder),
            )
        self._slice_page()

    def _slice_page(self):
        self.beginResetModel()
        start = self._page * self._page_size
        self._page_rows = self._all_rows[start:start + self._page_size]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._page_rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section][1]
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.DescendingOrder):
        if not (0 <= column < len(self.COLUMNS)):
            return
        self._sort_col = column
        self._sort_order = order
        self._apply_sort_and_slice()

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._page_rows[index.row()]
        key, _, fmt = self.COLUMNS[index.column()]
        val = row.get(key)
        if role == Qt.ItemDataRole.DisplayRole:
            if fmt == "money":
                return _fmt_num(val, 2)
            if fmt == "shares":
                return _fmt_num(val, 2)
            if fmt == "nav":
                return _fmt_num(val, 4)
            return str(val or "—")
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if fmt in ("money", "shares", "nav"):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if fmt == "dir" or key in ("trade_date", "platform", "fund_code", "status"):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and fmt == "dir":
            return {
                "买入": QColor("#2563EB"),
                "卖出": QColor("#DC2626"),
                "分红": QColor("#16A34A"),
                "转换": QColor("#7C3AED"),
            }.get(str(val), QColor("#475569"))
        if role == Qt.ItemDataRole.FontRole:
            if fmt in ("money", "shares", "nav"):
                return _ui_font(12)
            if fmt == "dir":
                return _ui_font(12, bold=True)
        return None


class DomesticTradesPage(QWidget):
    """国内资产交易明细主页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_data: dict[str, Any] | None = None
        self._filtered_rows: list[dict[str, Any]] = []
        self._loading = False
        self._worker_thread: QThread | None = None
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("domesticTradesPage")
        self.setStyleSheet(TRADE_CENTER_STYLE.replace('#tradeCenter', '#domesticTradesPage') + """
            QWidget#domesticTradesPage { background: #F8FAFC; }
            QFrame#card { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; }
            QComboBox, QLineEdit {
                min-height: 28px; padding: 2px 8px; border: 1px solid #CBD5E1;
                border-radius: 6px; background: #FFFFFF; color: #0F172A; font-size: 12px;
            }
            QPushButton {
                min-height: 28px; padding: 4px 12px; border: 1px solid #CBD5E1;
                border-radius: 6px; background: #FFFFFF; color: #334155; font-size: 12px; font-weight: 600;
            }
            QPushButton:hover { background: #F1F5F9; }
            QPushButton#primaryBtn { background: #2563EB; border-color: #2563EB; color: #FFFFFF; }
            QPushButton#primaryBtn:hover { background: #1D4ED8; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # 1. 顶部操作栏
        top_bar = QHBoxLayout()
        self.summary_pill = QLabel("加载中…")
        self.summary_pill.setStyleSheet("color: #64748B; font-size: 12px; padding: 4px 10px; background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        top_bar.addWidget(self.summary_pill)
        top_bar.addStretch(1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(lambda: self.reload(force=True))
        self.export_btn = QPushButton("导出 CSV")
        self.export_btn.clicked.connect(self.export_csv)
        top_bar.addWidget(self.refresh_btn)
        top_bar.addWidget(self.export_btn)
        root.addLayout(top_bar)

        # 2. 筛选栏
        filter_card = QFrame(objectName="card")
        fl = QHBoxLayout(filter_card)
        fl.setContentsMargins(12, 10, 12, 10)
        fl.setSpacing(8)

        self.platform_combo = QComboBox()
        self.platform_combo.addItem("全部平台", "")
        self.dir_combo = QComboBox()
        self.dir_combo.addItem("全部方向", "")
        self.fund_type_combo = QComboBox()
        self.fund_type_combo.addItem("全部基金类型", "")
        self.raw_type_combo = QComboBox()
        self.raw_type_combo.addItem("全部业务类型", "")
        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("开始日期 YYYY-MM-DD")
        self.start_edit.setFixedWidth(130)
        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText("结束日期 YYYY-MM-DD")
        self.end_edit.setFixedWidth(130)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索基金代码 / 名称 / 订单号…")
        self.search_edit.returnPressed.connect(self._apply_filters)

        query_btn = QPushButton("查询", objectName="primaryBtn")
        query_btn.clicked.connect(self._apply_filters)
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self._reset_filters)

        for w in (self.platform_combo, self.dir_combo, self.fund_type_combo, self.raw_type_combo):
            w.currentIndexChanged.connect(lambda _i: self._apply_filters())

        fl.addWidget(self.platform_combo)
        fl.addWidget(self.dir_combo)
        fl.addWidget(self.fund_type_combo)
        fl.addWidget(self.raw_type_combo)
        fl.addWidget(self.start_edit)
        fl.addWidget(QLabel("至"))
        fl.addWidget(self.end_edit)
        fl.addWidget(self.search_edit, 1)
        fl.addWidget(query_btn)
        fl.addWidget(reset_btn)
        root.addWidget(filter_card)

        # 3. 5 项 KPI 汇总卡片
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self._kpi_labels: dict[str, tuple[QLabel, QLabel]] = {}
        self._kpi_cards = {}
        for key, title, glyph, color in (
            ("count", "筛选交易笔数", "layers", "#2563EB"),
            ("buy", "买入合计 (CNY)", "buy", "#16A34A"),
            ("sell", "卖出合计 (CNY)", "sell", "#DC2626"),
            ("net", "净投入金额 (CNY)", "layers", "#2563EB"),
            ("fee", "手续费合计 (CNY)", "fees", "#2563EB"),
        ):
            card = StatCard(title, glyph, color, interactive=False)
            v_lbl, s_lbl = card.value, card.hint
            kpi_row.addWidget(card, 1)
            self._kpi_labels[key] = (v_lbl, s_lbl)
            self._kpi_cards[key] = card
        root.addLayout(kpi_row)

        # 4. 交易明细表格 + 分页栏
        table_card = QFrame(objectName="card")
        tl = QVBoxLayout(table_card)
        tl.setContentsMargins(12, 10, 12, 10)
        tl.setSpacing(8)

        self.model = DomesticTradesModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.doubleClicked.connect(self._show_detail)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        tl.addWidget(self.table, 1)

        pager = QHBoxLayout()
        self.page_info = QLabel("第 1 / 1 页 · 共 0 条")
        self.page_info.setStyleSheet("color: #64748B; font-size: 12px;")
        self.page_size_combo = QComboBox()
        for n in (50, 100, 200, 500):
            self.page_size_combo.addItem(f"{n} 条/页", n)
        self.page_size_combo.currentIndexChanged.connect(lambda _i: self._on_page_size_changed())
        self.prev_btn = QPushButton("上一页")
        self.next_btn = QPushButton("下一页")
        self.prev_btn.clicked.connect(lambda: self._go_page(self.model.page - 1))
        self.next_btn.clicked.connect(lambda: self._go_page(self.model.page + 1))
        pager.addWidget(self.page_info, 1)
        pager.addWidget(self.page_size_combo)
        pager.addWidget(self.prev_btn)
        pager.addWidget(self.next_btn)
        tl.addLayout(pager)
        root.addWidget(table_card, 1)

    def reload(self, *, force: bool = False):
        if self._loading:
            return
        if self._raw_data is not None and not force:
            return
        self._loading = True
        self.summary_pill.setText("正在加载国内资产交易流水…")
        self.refresh_btn.setEnabled(False)

        self._worker_thread = QThread(self)
        self._worker = _TradesWorker()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_loaded)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        self._worker_thread.start()

    def _on_loaded(self, data: dict[str, Any] | None, error: str):
        self._loading = False
        self.refresh_btn.setEnabled(True)
        if error or data is None:
            self.summary_pill.setText(f"加载失败：{error or '未知错误'}")
            return
        self._raw_data = data
        for combo, label, key in (
            (self.platform_combo, "全部平台", "platforms"),
            (self.dir_combo, "全部方向", "directions"),
            (self.fund_type_combo, "全部基金类型", "fund_types"),
            (self.raw_type_combo, "全部业务类型", "raw_types"),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, "")
            for val in data.get(key, ()):
                combo.addItem(val, val)
            combo.blockSignals(False)
        self._apply_filters()

    def _reset_filters(self):
        for combo in (self.platform_combo, self.dir_combo, self.fund_type_combo, self.raw_type_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.start_edit.clear()
        self.end_edit.clear()
        self.search_edit.clear()
        self._apply_filters()

    def _apply_filters(self):
        if self._raw_data is None:
            return
        rows = self._raw_data.get("rows", [])
        plat = self.platform_combo.currentData() or ""
        direction = self.dir_combo.currentData() or ""
        ftype = self.fund_type_combo.currentData() or ""
        rtype = self.raw_type_combo.currentData() or ""
        s_date = self.start_edit.text().strip()
        e_date = self.end_edit.text().strip()
        kw = self.search_edit.text().strip().lower()

        filtered = []
        for r in rows:
            if plat and r["platform"] != plat:
                continue
            if direction and r["direction"] != direction:
                continue
            if ftype and r["fund_type"] != ftype:
                continue
            if rtype and r["transaction_type_raw"] != rtype:
                continue
            td = r["trade_date"]
            if s_date and td < s_date:
                continue
            if e_date and td > e_date:
                continue
            if kw and kw not in r["fund_code"].lower() and kw not in r["fund_name"].lower() and kw not in r["order_id"].lower():
                continue
            filtered.append(r)

        self._filtered_rows = filtered
        self._update_kpis(filtered)
        page_size = self.page_size_combo.currentData() or 50
        self.model.set_rows(filtered, page_size=page_size, reset_page=True)
        self._update_pager()

        dates = [r["trade_date"] for r in filtered if r["trade_date"]]
        span = f"{min(dates)} 至 {max(dates)}" if dates else "—"
        self.summary_pill.setText(f"共 {len(filtered):,} 条流水 · {span}")

    def _update_kpis(self, rows: list[dict[str, Any]]):
        buy_sum = 0.0
        sell_sum = 0.0
        buy_cnt = 0
        sell_cnt = 0
        fee_sum = 0.0
        tt_cnt = 0
        ap_cnt = 0
        for r in rows:
            if r["platform"] == "天天基金":
                tt_cnt += 1
            elif r["platform"] == "支付宝":
                ap_cnt += 1
            amt = r.get("confirmed_amount") or 0.0
            if r["direction"] == "买入":
                buy_sum += amt
                buy_cnt += 1
            elif r["direction"] == "卖出":
                sell_sum += amt
                sell_cnt += 1
            fee_sum += r.get("fee") or 0.0

        net = buy_sum - sell_sum
        self._kpi_labels["count"][0].setText(f"{len(rows):,}")
        self._kpi_labels["count"][1].setText(f"支付宝 {ap_cnt:,} · 天天基金 {tt_cnt:,}")
        self._kpi_labels["buy"][0].setText(f"¥{buy_sum:,.2f}")
        self._kpi_labels["buy"][1].setText(f"共 {buy_cnt:,} 笔")
        self._kpi_labels["sell"][0].setText(f"¥{sell_sum:,.2f}")
        self._kpi_labels["sell"][1].setText(f"共 {sell_cnt:,} 笔")
        net_lbl, net_sub = self._kpi_labels["net"]
        net_lbl.setText(f"{'+' if net > 0 else '-' if net < 0 else ''}¥{abs(net):,.2f}")
        net_lbl.setStyleSheet(f"color: {'#2563EB' if net >= 0 else '#DC2626'};")
        net_sub.setText("买入减卖出总额")
        self._kpi_labels["fee"][0].setText(f"¥{fee_sum:,.2f}")
        self._kpi_labels["fee"][1].setText("已记录确认手续费")
        for key, card in self._kpi_cards.items():
            value, hint = self._kpi_labels[key]
            card.display = [value.text(), hint.text()]
            card.fit_text()

    def _on_page_size_changed(self):
        size = self.page_size_combo.currentData() or 50
        self.model.set_page(0, page_size=size)
        self._update_pager()

    def _go_page(self, page: int):
        self.model.set_page(page)
        self._update_pager()

    def _update_pager(self):
        self.page_info.setText(f"第 {self.model.page + 1} / {self.model.total_pages} 页 · 共 {len(self._filtered_rows):,} 条")
        self.prev_btn.setEnabled(self.model.page > 0)
        self.next_btn.setEnabled(self.model.page + 1 < self.model.total_pages)

    def _show_detail(self, index: QModelIndex):
        row = self.model.row_at(index.row())
        if not row:
            return
        lines = [
            f"基金名称：{row['fund_name']} ({row['fund_code']})",
            f"交易平台：{row['platform']}  |  基金类型：{row['fund_type']}",
            f"确认日期：{row['confirmation_date'] or '—'}  |  交易时间：{row['transaction_time'] or '—'}",
            f"交易方向：{row['direction']}  |  业务类型：{row['transaction_type_raw']}",
            f"申请金额：{_fmt_num(row['application_amount'])}  |  确认金额：{_fmt_num(row['confirmed_amount'])} CNY",
            f"确认份额：{_fmt_num(row['confirmed_shares'])}  |  确认净值：{_fmt_num(row['confirmed_nav'], 4)}",
            f"手续费：{_fmt_num(row['fee'])} CNY  |  状态：{row['status']}",
            f"订单号：{row['order_id'] or '—'}",
        ]
        QMessageBox.information(self, "交易流水详情", "\n".join(lines))

    def export_csv(self):
        if not self._filtered_rows:
            QMessageBox.information(self, "导出提示", "当前没有可导出的交易明细。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出国内资产交易明细", "国内资产_交易明细.csv", "CSV (*.csv)")
        if not path:
            return
        headers = [col[1] for col in DomesticTradesModel.COLUMNS]
        keys = [col[0] for col in DomesticTradesModel.COLUMNS]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for r in self._filtered_rows:
                writer.writerow([r.get(k) if r.get(k) is not None else "" for k in keys])
        QMessageBox.information(self, "导出完成", f"已导出 {len(self._filtered_rows):,} 条交易流水到：\n{path}")
