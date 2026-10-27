from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from portfolio.ui.icons import icon

# Existing page indices retained as routing identities, independent of visual order.
GROUPS = (
    ("资产管理", (("资产总览", 2, "grid", None), ("今日盈亏", 3, "chart", "今日盈亏"), ("当前持仓", 3, "wallet", "当前持仓"),
              ("股票汇总", 3, "table", "股票汇总"), ("人员资产", 3, "people", "人员资产"), ("券商资产", 3, "bank", "渠道汇总"),
              ("股票归属", 3, "pie", "股票归属"), ("持仓财报日历", 14, "calendar", None))),
    ("交易与收益", (("交易明细", 0, "trade", None), ("每日持仓收益", 4, "calendar", "每日持仓收益"),
                ("每月持仓收益", 4, "chart", "每月持仓收益"), ("每月微操收益", 4, "trade", "每月微操收益"),
                ("清仓收益", 4, "wallet", "清仓收益"), ("月度净投入", 4, "chart", "月度净投入明细"))),
    ("AI 助手", (("AI 投资问答", 19, "robot", None),
                ("行情监控中心", 21, "chart", None),
                ("AI 股票分析", 20, "chart", None))),
    ("图表分析", (("资产走势图", 5, "chart", 17),
              ("月度走势详情", 18, "chart", None),
              ("持仓分布", 15, "grid", None),
              ("走势图", 16, "chart", None),
              ("Top日汇总", 17, "chart", "day"),
              ("Top月汇总", 17, "chart", "month"),
              ("收益排行榜", 5, "chart", 15),
              ("历史资产回放", 3, "calendar", "replay"),
              ("证券详情", 10, "chart", None))),
    ("系统管理", (("数据源检查", 1, "database", None), ("数据中心", 6, "database", None),
              ("Excel 对账", 8, "table", None), ("指标说明", 9, "table", None),
              ("数据同步 · 未开放", 11, "refresh", "sync"), ("开发进度", 12, "table", None), ("设置", 13, "grid", None))),
)


class Sidebar(QWidget):
    currentRowChanged = Signal(int)
    routeChanged = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("sidebar")
        self.setFixedWidth(212)
        self.collapsed = False
        self._page, self._route = -1, None
        self._selecting_page_only = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 24, 0, 16)
        layout.setSpacing(4)
        self.brand = QLabel("我要发财")
        self.brand.setObjectName("brand")
        self.brand.setContentsMargins(20, 0, 0, 0)
        self.subtitle = QLabel("投资资产管理")
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setContentsMargins(20, 0, 0, 12)
        layout.addWidget(self.brand)
        layout.addWidget(self.subtitle)
        self.items = QListWidget()
        self.items.setObjectName("navigation")
        self.items.setIconSize(QSize(18, 18))
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.groups, self.entries = [], []
        for group, entries in GROUPS:
            item = QListWidgetItem("", self.items)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(190, 30))
            label = QLabel(group)
            label.setObjectName("navGroup")
            self.items.setItemWidget(item, label)
            self.groups.append(item)
            for text, page, kind, target in entries:
                item = QListWidgetItem(icon(kind, self), text, self.items)
                item.setSizeHint(QSize(190, 36))
                item.setData(Qt.ItemDataRole.UserRole, (page, target))
                item.setToolTip(text)
                self.entries.append((item, text))
        self.items.currentItemChanged.connect(self._selected)
        layout.addWidget(self.items, 1)
        self.toggle_button = QPushButton("  收起导航")
        self.toggle_button.setIcon(icon("menu", self))
        self.toggle_button.setObjectName("navToggle")
        self.toggle_button.clicked.connect(self.toggle)
        layout.addWidget(self.toggle_button)
        self.footer = QLabel("本地工作区 · 源库只读")
        self.footer.setObjectName("secondary")
        self.footer.setContentsMargins(20, 4, 0, 0)
        layout.addWidget(self.footer)
        self.setCurrentRow(0)

    def _selected(self, item, previous):
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        for entry, _ in self.entries:
            font = QFont(self.font())
            font.setWeight(QFont.Weight.DemiBold if entry is item else QFont.Weight.Normal)
            entry.setFont(font)
        self._page, target = data
        if self._selecting_page_only:
            target = None
        self._route = (self._page, target)
        self.currentRowChanged.emit(self._page)
        self.routeChanged.emit(self._page, target)

    def currentRow(self):
        return self._page

    def setCurrentRow(self, page):
        self.select(page)

    def select(self, page, target=None):
        for item, _ in self.entries:
            p, t = item.data(Qt.ItemDataRole.UserRole)
            if p == page and (target is None or target == t):
                self._selecting_page_only = target is None
                if self.items.currentItem() is item:
                    self._selected(item, None)
                else:
                    self.items.setCurrentItem(item)
                self._selecting_page_only = False
                return

    def count(self):
        return len(self.entries)

    def toggle(self):
        self.collapsed = not self.collapsed
        self.setFixedWidth(64 if self.collapsed else 212)
        self.brand.setText("发" if self.collapsed else "我要发财")
        self.subtitle.setVisible(not self.collapsed)
        self.footer.setVisible(not self.collapsed)
        for item in self.groups:
            item.setHidden(self.collapsed)
        for item, text in self.entries:
            item.setText("" if self.collapsed else text)
            item.setSizeHint(QSize(40 if self.collapsed else 190, 38 if self.collapsed else 36))
        self.toggle_button.setText("" if self.collapsed else "  收起导航")
        self.toggle_button.setToolTip("展开导航" if self.collapsed else "收起导航")
