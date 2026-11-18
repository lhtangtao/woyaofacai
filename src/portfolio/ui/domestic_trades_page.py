"""国内资产（支付宝 / 天天基金 / 同花顺）交易明细页面：与美股交易明细在视觉风格、KPI卡片与回放列信息上完全对齐。"""
from __future__ import annotations

import csv
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from portfolio.domestic.trades_query import query_domestic_trades
from portfolio.ui.theme import numeric_font, tokens
from portfolio.ui.trade_center_style import STYLE as TRADE_CENTER_STYLE, StatCard


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


class _DomesticDirectionDelegate(QStyledItemDelegate):
    """与美股交易明细一致的圆角彩色胶囊方向标签绘制器。"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        val = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not val or val == "—":
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor("#EAF2FF"))
        if val == "买入":
            bg, fg = "#ECFDF3", "#15803D"
        elif val == "卖出":
            bg, fg = "#FFF1F2", "#B91C1C"
        else:
            bg, fg = "#EFF6FF", "#2563EB"
        fm = option.fontMetrics
        w = max(44, min(option.rect.width() - 8, fm.horizontalAdvance(val) + 18))
        h = 22
        r = QRectF(
            option.rect.center().x() - w / 2.0,
            option.rect.center().y() - h / 2.0,
            w,
            h,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(r, 6, 6)
        f = QFont(option.font)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(fg))
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, val)
        painter.restore()


class DomesticTradesModel(QAbstractTableModel):
    COLUMNS = (
        ("transaction_time", "交易时间", "time"),
        ("platform", "券商渠道", "center"),
        ("fund_name", "名称", "text"),
        ("fund_code", "代码", "code"),
        ("fund_type", "分类", "text"),
        ("direction", "交易方向", "dir"),
        ("transaction_type_raw", "业务类型", "text"),
        ("current_price", "当前价", "nav"),
        ("confirmed_nav", "成交价", "nav"),
        ("confirmed_shares", "成交数量", "shares"),
        ("confirmed_amount", "成交金额", "money"),
        ("fee", "手续费", "money"),
        ("post_shares", "成交后持仓", "shares"),
        ("post_cost", "成交后成本价", "nav"),
        ("post_net", "成交后净投入", "money"),
        ("cost_change", "成本变化", "cost_change"),
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
            sort_key = "cost_diff" if key == "cost_change" else key
            is_num = is_num or key == "cost_change"
            self._all_rows.sort(
                key=lambda r: (
                    (0, float(r[sort_key])) if (is_num and r.get(sort_key) is not None)
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
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.COLUMNS[section][1]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                _, _, fmt = self.COLUMNS[section]
                if fmt in ("money", "shares", "nav", "cost_change"):
                    return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if fmt in ("dir", "center", "code"):
                    return int(Qt.AlignmentFlag.AlignCenter)
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
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
            return str(val if val not in (None, "") else "—")
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if fmt in ("money", "shares", "nav", "cost_change"):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if fmt in ("dir", "center", "code"):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            if fmt == "cost_change":
                diff = row.get("cost_diff")
                if diff is not None and abs(diff) >= 0.00005:
                    t = tokens(self.parent())
                    # 与美股一致：成本降低（diff < 0）为绿色利好，成本抬升（diff > 0）为红色
                    return QColor(t.profit if diff < 0 else t.loss)
                return QColor("#475569")
            if val is None or val == "":
                return QColor("#94A3B8")
        if role == Qt.ItemDataRole.FontRole:
            if fmt in ("money", "shares", "nav", "cost_change"):
                return numeric_font()
            if fmt == "code":
                return _ui_font(12, bold=True)
        return None


class DomesticTradesPage(QWidget):
    """国内资产交易明细主页（与美股 TradeCenterPage 风格与列信息一致）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_data: dict[str, Any] | None = None
        self._filtered_rows: list[dict[str, Any]] = []
        self._loading = False
        self._worker_thread: QThread | None = None
        self._quick_filter: str = ""  # "", "buy", "sell", "review"
        self._build_ui()

    def _build_ui(self):
        self.setObjectName("domesticTradesPage")
        self.setStyleSheet(TRADE_CENTER_STYLE.replace("#tradeCenter", "#domesticTradesPage"))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # 1. 筛选栏（与美股一致置于最上方，无多余重复标题栏）
        filter_card = QWidget(objectName="filterCard")
        fl_outer = QVBoxLayout(filter_card)
        fl_outer.setContentsMargins(14, 10, 14, 10)
        fl_outer.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.addWidget(QLabel("券商渠道", objectName="fieldLabel"))
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("全部渠道", "")
        self.platform_combo.setMinimumWidth(120)
        row1.addWidget(self.platform_combo)

        row1.addWidget(QLabel("标的搜索", objectName="fieldLabel"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("全部基金/股票 · 输入代码 / 名称 / 订单号…")
        self.search_edit.returnPressed.connect(self._apply_filters)
        self.search_edit.textChanged.connect(lambda _t: self._apply_filters())
        row1.addWidget(self.search_edit, 1)

        self.reset_btn = QPushButton("重置筛选")
        self.reset_btn.clicked.connect(self._reset_filters)
        row1.addWidget(self.reset_btn)

        self.adv_btn = QPushButton("高级筛选 ▾")
        self.adv_btn.setCheckable(True)
        self.adv_btn.toggled.connect(self._toggle_advanced)
        row1.addWidget(self.adv_btn)

        self.export_btn = QPushButton("导出", objectName="primary")
        self.export_btn.clicked.connect(self.export_csv)
        row1.addWidget(self.export_btn)
        fl_outer.addLayout(row1)

        # 折叠式高级筛选面板
        self.adv_panel = QWidget()
        adv_layout = QHBoxLayout(self.adv_panel)
        adv_layout.setContentsMargins(0, 4, 0, 0)
        adv_layout.setSpacing(10)

        adv_layout.addWidget(QLabel("交易方向", objectName="fieldLabel"))
        self.dir_combo = QComboBox()
        self.dir_combo.addItem("全部方向", "")
        adv_layout.addWidget(self.dir_combo)

        adv_layout.addWidget(QLabel("分类", objectName="fieldLabel"))
        self.fund_type_combo = QComboBox()
        self.fund_type_combo.addItem("全部分类", "")
        adv_layout.addWidget(self.fund_type_combo)

        adv_layout.addWidget(QLabel("业务类型", objectName="fieldLabel"))
        self.raw_type_combo = QComboBox()
        self.raw_type_combo.addItem("全部业务类型", "")
        adv_layout.addWidget(self.raw_type_combo)

        adv_layout.addWidget(QLabel("起止日期", objectName="fieldLabel"))
        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("开始 YYYY-MM-DD")
        self.start_edit.setFixedWidth(125)
        self.start_edit.editingFinished.connect(self._apply_filters)
        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText("结束 YYYY-MM-DD")
        self.end_edit.setFixedWidth(125)
        self.end_edit.editingFinished.connect(self._apply_filters)
        adv_layout.addWidget(self.start_edit)
        adv_layout.addWidget(QLabel("至", objectName="fieldLabel"))
        adv_layout.addWidget(self.end_edit)
        adv_layout.addStretch(1)
        self.adv_panel.hide()
        fl_outer.addWidget(self.adv_panel)

        for w in (self.platform_combo, self.dir_combo, self.fund_type_combo, self.raw_type_combo):
            w.currentIndexChanged.connect(lambda _i: self._apply_filters())

        root.addWidget(filter_card)

        # 2. 5 张 KPI 汇总卡片（与美股标题、图标、交互一致）
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(12)
        self._kpi_labels: dict[str, tuple[QLabel, QLabel]] = {}
        self._kpi_cards: dict[str, StatCard] = {}
        for key, title, glyph, color, interactive in (
            ("count", "匹配交易总数", "layers", "#2563EB", False),
            ("buy", "买入记录", "buy", "#16A34A", True),
            ("sell", "卖出记录", "sell", "#DC2626", True),
            ("fee", "手续费合计", "fees", "#2563EB", False),
            ("review", "待核查记录", "review", "#2563EB", True),
        ):
            card = StatCard(title, glyph, color, interactive=interactive)
            card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            if interactive:
                card.clicked.connect(lambda _checked=False, k=key: self._on_quick_card_clicked(k))
            v_lbl, s_lbl = card.value, card.hint
            kpi_row.addWidget(card, 1)
            self._kpi_labels[key] = (v_lbl, s_lbl)
            self._kpi_cards[key] = card
        root.addLayout(kpi_row)

        # 3. 交易明细表格卡片（含「交易记录 X 条匹配记录」+「显示列」菜单 + 表格 + 底部同款分页）
        table_card = QFrame(objectName="surface")
        tl = QVBoxLayout(table_card)
        tl.setContentsMargins(14, 12, 14, 10)
        tl.setSpacing(8)

        card_header = QHBoxLayout()
        card_header.setSpacing(8)
        self.section_title = QLabel("交易记录", objectName="sectionTitle")
        self.summary_pill = QLabel("加载中…", objectName="fieldLabel")
        card_header.addWidget(self.section_title)
        card_header.addWidget(self.summary_pill)
        card_header.addStretch(1)
        self.columns_btn = QPushButton("显示列")
        self.columns_btn.clicked.connect(self._open_columns_menu)
        card_header.addWidget(self.columns_btn)
        tl.addLayout(card_header)

        self.model = DomesticTradesModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setFixedHeight(40)
        self.table.doubleClicked.connect(self._show_detail)

        dir_col = next(i for i, c in enumerate(DomesticTradesModel.COLUMNS) if c[0] == "direction")
        self._dir_delegate = _DomesticDirectionDelegate(self.table)
        self.table.setItemDelegateForColumn(dir_col, self._dir_delegate)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        tl.addWidget(self.table, 1)

        pager = QHBoxLayout()
        self.page_info = QLabel("共 0 条 · 第 1 / 1 页", objectName="fieldLabel")
        self.page_size_combo = QComboBox()
        for n in (50, 100, 200, 500):
            self.page_size_combo.addItem(f"每页 {n} 条", n)
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

    def _toggle_advanced(self, checked: bool):
        self.adv_panel.setVisible(checked)
        self.adv_btn.setText("高级筛选 ▴" if checked else "高级筛选 ▾")

    def _open_columns_menu(self):
        menu = QMenu(self)
        for idx, (_key, title, _fmt) in enumerate(DomesticTradesModel.COLUMNS):
            act = menu.addAction(title)
            act.setCheckable(True)
            act.setChecked(not self.table.isColumnHidden(idx))
            act.toggled.connect(lambda checked, c=idx: (self.table.setColumnHidden(c, not checked), self._fit_columns()))
        menu.exec(self.columns_btn.mapToGlobal(self.columns_btn.rect().bottomLeft()))

    def _fit_columns(self):
        widths = {
            "transaction_time": 146,
            "platform": 80,
            "fund_name": 180,
            "fund_code": 74,
            "fund_type": 116,
            "direction": 78,
            "transaction_type_raw": 110,
            "current_price": 78,
            "confirmed_nav": 78,
            "confirmed_shares": 90,
            "confirmed_amount": 98,
            "fee": 72,
            "post_shares": 96,
            "post_cost": 96,
            "post_net": 106,
            "cost_change": 106,
        }
        visible = [
            (i, widths.get(col[0], 90))
            for i, col in enumerate(DomesticTradesModel.COLUMNS)
            if not self.table.isColumnHidden(i)
        ]
        if not visible:
            return
        vp_w = max(0, self.table.viewport().width())
        base_sum = sum(w for _, w in visible)
        extra = max(0, vp_w - base_sum)
        for pos, (col_idx, w) in enumerate(visible):
            self.table.setColumnWidth(col_idx, w + extra // len(visible) + (1 if pos < extra % len(visible) else 0))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_columns()

    def _on_quick_card_clicked(self, key: str):
        if self._quick_filter == key:
            self._quick_filter = ""
        else:
            self._quick_filter = key
        for k, card in self._kpi_cards.items():
            if card.isCheckable():
                card.blockSignals(True)
                card.setChecked(self._quick_filter == k)
                card.blockSignals(False)
        self._apply_filters()

    def reload(self, *, force: bool = False):
        if self._loading:
            return
        if self._raw_data is not None and not force:
            return
        self._loading = True
        self.summary_pill.setText("正在加载国内资产交易流水…")

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
        if error or data is None:
            self.summary_pill.setText(f"加载失败：{error or '未知错误'}")
            return
        self._raw_data = data
        for combo, label, key in (
            (self.platform_combo, "全部渠道", "platforms"),
            (self.dir_combo, "全部方向", "directions"),
            (self.fund_type_combo, "全部分类", "fund_types"),
            (self.raw_type_combo, "全部业务类型", "raw_types"),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, "")
            for val in data.get(key, ()):
                combo.addItem(val, val)
            combo.blockSignals(False)
        self._apply_filters()
        self._fit_columns()

    def _reset_filters(self):
        self._quick_filter = ""
        for card in self._kpi_cards.values():
            if card.isCheckable():
                card.blockSignals(True)
                card.setChecked(False)
                card.blockSignals(False)
        for combo in (self.platform_combo, self.dir_combo, self.fund_type_combo, self.raw_type_combo):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.start_edit.clear()
        self.end_edit.clear()
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
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
            if self._quick_filter == "buy" and r["direction"] != "买入":
                continue
            if self._quick_filter == "sell" and r["direction"] != "卖出":
                continue
            if self._quick_filter == "review" and not r.get("review_required"):
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
        self.summary_pill.setText(f"{len(filtered):,} 条匹配记录")
        self._fit_columns()

    def _update_kpis(self, rows: list[dict[str, Any]]):
        buy_sum = 0.0
        sell_sum = 0.0
        buy_cnt = 0
        sell_cnt = 0
        other_cnt = 0
        fee_sum = 0.0
        review_cnt = 0
        for r in rows:
            amt = r.get("confirmed_amount") or 0.0
            if r["direction"] == "买入":
                buy_sum += amt
                buy_cnt += 1
            elif r["direction"] == "卖出":
                sell_sum += amt
                sell_cnt += 1
            else:
                other_cnt += 1
            fee_sum += r.get("fee") or 0.0
            if r.get("review_required"):
                review_cnt += 1

        self._kpi_labels["count"][0].setText(f"{len(rows):,}")
        self._kpi_labels["count"][1].setText(
            f"买入 {buy_cnt:,} · 卖出 {sell_cnt:,} · 其他 {other_cnt:,}" if other_cnt else f"买入 {buy_cnt:,} · 卖出 {sell_cnt:,} · 其他 —"
        )
        self._kpi_labels["buy"][0].setText(f"{buy_cnt:,}")
        self._kpi_labels["buy"][1].setText(f"买入总额 ¥{buy_sum:,.2f}")
        self._kpi_labels["sell"][0].setText(f"{sell_cnt:,}")
        self._kpi_labels["sell"][1].setText(f"卖出总额 ¥{sell_sum:,.2f}")
        self._kpi_labels["fee"][0].setText(f"{fee_sum:,.2f}")
        self._kpi_labels["fee"][1].setText("已记录确认手续费 (CNY)")
        self._kpi_labels["review"][0].setText(f"{review_cnt:,}")
        self._kpi_labels["review"][1].setText("关联 / 解析与导入异常")

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
        self.page_info.setText(f"共 {len(self._filtered_rows):,} 条 · 第 {self.model.page + 1} / {self.model.total_pages} 页")
        self.prev_btn.setEnabled(self.model.page > 0)
        self.next_btn.setEnabled(self.model.page + 1 < self.model.total_pages)

    def _show_detail(self, index: QModelIndex):
        row = self.model.row_at(index.row())
        if not row:
            return
        lines = [
            f"标的：{row['fund_name']} ({row['fund_code']})",
            f"券商渠道：{row['platform']}  |  分类：{row['fund_type']}",
            f"交易时间：{row['transaction_time'] or '—'}  |  确认日期：{row['confirmation_date'] or '—'}",
            f"交易方向：{row['direction']}  |  业务类型：{row['transaction_type_raw']}",
            f"当前价：{_fmt_num(row.get('current_price'), 4)}  |  成交价：{_fmt_num(row.get('confirmed_nav'), 4)}",
            f"成交数量：{_fmt_num(row.get('confirmed_shares'))}  |  成交金额：{_fmt_num(row.get('confirmed_amount'))} CNY",
            f"手续费：{_fmt_num(row.get('fee'))} CNY  |  状态：{row['status']}",
            f"成交后持仓：{_fmt_num(row.get('post_shares'))}  |  成交后成本价：{_fmt_num(row.get('post_cost'), 4)}",
            f"成交后净投入：{_fmt_num(row.get('post_net'))} CNY  |  成本变化：{row.get('cost_change') or '—'}",
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
