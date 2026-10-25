import json
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.models import exact_text, from_e8
from portfolio.ui.icons import icon


class TradeDrawer(QWidget):
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("detailDrawer")
        self.setMinimumWidth(360)
        self.setMaximumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        heading = QHBoxLayout()
        self.title = QLabel("交易详情")
        self.title.setObjectName("sectionTitle")
        heading.addWidget(self.title, 1)
        close = QPushButton()
        close.setIcon(icon("close", self))
        close.setToolTip("关闭交易详情")
        close.setFixedWidth(34)
        close.clicked.connect(self.dismiss)
        heading.addWidget(close)
        layout.addLayout(heading)
        self.subtitle = QLabel("选择交易查看详情")
        self.subtitle.setObjectName("secondary")
        layout.addWidget(self.subtitle)
        self.tabs = QTabWidget()
        self.forms = []
        for name in ("基本信息", "账务信息"):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            panel = QWidget()
            form = QFormLayout(panel)
            form.setContentsMargins(4, 20, 4, 16)
            form.setSpacing(18)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            scroll.setWidget(panel)
            self.tabs.addTab(scroll, name)
            self.forms.append(form)
        raw = QWidget()
        raw_layout = QVBoxLayout(raw)
        raw_layout.setContentsMargins(0, 12, 0, 0)
        self.sensitive = QCheckBox("显示敏感字段（仅本机）")
        raw_layout.addWidget(self.sensitive)
        self.raw_text = QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setPlaceholderText("读取原始来源中…")
        raw_layout.addWidget(self.raw_text)
        copy = QPushButton("复制当前原始数据")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.raw_text.toPlainText()))
        raw_layout.addWidget(copy)
        self.tabs.addTab(raw, "原始数据")
        layout.addWidget(self.tabs, 1)
        self.hide()

    def dismiss(self):
        self.hide()
        self.closed.emit()

    def clear(self):
        self.raw_text.clear()
        self.hide()

    def show_record(self, row):
        data = row.metadata
        self.title.setText(f"{data['symbol']} · {row.values[4]}")
        self.subtitle.setText(f"{data['broker_code']}  /  {data.get('person_name') or '券商真实账本'}")
        fees = from_e8(data.get("fees_e8"))
        source = {'SCHWAB_TRANSACTION_API': '嘉信API费用明细', 'LONGBRIDGE_ORDER_DETAIL_API': '长桥API订单费用',
                  'RULE:FIRSTRADE_ZERO': '第一证券免手续费', 'RULE:INTERNAL_ZERO': '内部划转免手续费',
                  'RULE:BBAE_2026-07-01': 'BBAE：7月1日起每笔1美元'}.get(data.get('fee_source'), '待获取')
        status = {'KNOWN': '已确认', 'UNKNOWN': '待获取', 'UNMATCHED_ORDER': '订单待匹配',
                  'ORDER_QUANTITY_MISMATCH': '订单数量待核对', 'ALLOCATION_MISMATCH': '归属数量待核对',
                  'NOT_COLLECTED': '尚未采集'}.get(data.get('fee_status'), '待获取')
        basic = (
            ("成交时间", data["book_time"]), ("证券代码", data["symbol"]),
            ("证券名称", data.get("instrument_name")),
            ("归属人员", data.get("person_name") or "券商真实账本"), ("交易方向", row.values[4]),
            ("成交数量", row.values[5]), ("操作后持仓（股）", row.values[9]), ("成交价格", row.values[6]), ("成交金额", row.values[7]),
            ("操作后成本（每股）", row.values[11]), ("操作后累计净投入", row.values[10]),
            ("币种", data["currency"]), ("手续费", fees),
            ("费用来源", source),
            ("费用状态", status), ("订单编号", data["order_id"]),
        )
        accounting = (
            ("账务日期", data["business_date"]), ("市场交易日", data.get("market_trade_date")),
            ("交易分类", data["category"]), ("金额口径", data["amount_basis"]),
            ("记录状态", data["record_state"]), ("账务角色", data["accounting_role"]),
            ("记录粒度", data["record_granularity"]),
            ("解析状态", data.get("parse_status")), ("来源文件", data.get("source_path")),
            ("工作表 / 行号", f"{data.get('source_sheet') or '—'} / {data.get('source_row') or '—'}"),
        )
        for form, fields in zip(self.forms, (basic, accounting)):
            while form.rowCount():
                form.removeRow(0)
            for name, value in fields:
                text = f"{value:,.2f}" if isinstance(value, Decimal) else exact_text(value) if value not in (None, "") else "—"
                label = QLabel(text)
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setMinimumWidth(0)
                label.setMaximumWidth(220)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                if name == "操作后持仓（股）":
                    label.setToolTip(data.get('post_quantity_note', ''))
                if name in ("操作后成本（每股）", "操作后累计净投入"):
                    label.setToolTip(data.get('post_cost_note', ''))
                if name == "手续费" and fees is None:
                    label.setToolTip("人员份额没有独立手续费字段，不能把券商整笔手续费重复分配到每个人。")
                caption = QLabel(name)
                caption.setObjectName("secondary")
                form.addRow(caption, label)
        self.tabs.setCurrentIndex(0)
        self.show()

    def set_provenance(self, metadata, provenance):
        # Never expose raw source paths/secrets through the basic info panel.
        self.raw_text.setPlainText(json.dumps({"记录信息": metadata, "原始来源": provenance},
                                             ensure_ascii=False, indent=2, default=exact_text))
