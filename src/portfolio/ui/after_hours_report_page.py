"""Mac 桌面端「盘后复盘日报 2.0」页面（结构化驾驶舱 + 自动换行零横向溢出 + 清单持久化 + 追问）。"""
from __future__ import annotations

import html
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDate, QEasingCurve, QPropertyAnimation, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.us_market_calendar import (
    format_session_hint,
    get_us_market_session,
)
from portfolio.paths import DATABASE_PATH
from portfolio.services.after_hours_report import (
    DEFAULT_AFTER_HOURS_MODEL,
    AfterHoursReportStore,
    ask_after_hours_followup,
    current_ny_now,
    ensure_structured,
    generate_after_hours_report,
    resolve_default_trade_date,
)
from portfolio.ui.date_picker import DatePicker

logger = logging.getLogger(__name__)


def _fmt_money(v, signed: bool = False) -> str:
    if v is None:
        return "—"
    try:
        num = float(v)
    except Exception:
        return "—"
    prefix = ("+$" if num > 0 else "-$" if num < 0 else "$") if signed else ("-$" if num < 0 else "$")
    return f"{prefix}{abs(num):,.2f}"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        pct = float(v) * 100.0
    except Exception:
        return "—"
    return f"{'+' if pct > 0 else ''}{pct:.2f}%"


def _tone_color(v) -> str:
    try:
        num = float(v)
    except Exception:
        return "#64748B"
    if num > 0:
        return "#16A34A"
    if num < 0:
        return "#DC2626"
    return "#64748B"


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _make_wrap_label(text: str = "", style: str = "") -> QLabel:
    """创建严格按父容器宽度自动换行、绝不撑宽横向滚动条的标签。"""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setMinimumWidth(0)
    lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    if style:
        lbl.setStyleSheet(style)
    return lbl


def _format_news_title_html(title: str, url: str) -> str:
    """将带 http(s) 链接的新闻标题格式化为可点击跳转原文的超链接 HTML。"""
    esc_t = html.escape(title)
    clean_u = str(url or "").strip()
    if clean_u.startswith(("http://", "https://")):
        esc_u = html.escape(clean_u, quote=True)
        return f"<a href='{esc_u}' style='color:#2563EB; text-decoration:none;'>{esc_t} ↗</a>"
    return esc_t


def _pnl_prefix(v) -> str:
    """根据盈亏正负动态返回「浮亏」或「浮盈」。"""
    try:
        return "浮亏" if float(v) < 0 else "浮盈"
    except Exception:
        return "浮盈"


def _tone_pill_colors(v) -> tuple[str, str]:
    """根据数值正负返回胶囊背景色与前景色。"""
    try:
        num = float(v)
    except Exception:
        return "#F1F5F9", "#64748B"
    if num > 0:
        return "#DCFCE7", "#15803D"
    if num < 0:
        return "#FEE2E2", "#B91C1C"
    return "#F1F5F9", "#64748B"


def _make_pill_label(text: str, bg: str, fg: str, border: str = "") -> QLabel:
    """创建紧凑圆角胶囊标签。"""
    lbl = QLabel(text)
    lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    b_css = f"border: 1px solid {border};" if border else "border: none;"
    lbl.setStyleSheet(
        f"font-size: 10.5px; font-weight: 600; padding: 2px 7px; border-radius: 9px; "
        f"background: {bg}; color: {fg}; {b_css}"
    )
    return lbl


class _FitWidthScrollArea(QScrollArea):
    """锁定内容最大宽度等于视口宽度，禁止横向拖动并确保内部文本自然折行。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def _sync_widget_width(self):
        w = self.widget()
        vw = self.viewport().width()
        if w is not None and vw > 0:
            w.setMaximumWidth(vw)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_widget_width()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_widget_width()


class _AfterHoursWorker(QThread):
    finished_report = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        trade_date: date,
        person_key: str,
        run_sync: bool,
        model: Optional[str],
        db_path: Path,
        store: AfterHoursReportStore,
    ):
        super().__init__()
        self.trade_date = trade_date
        self.person_key = person_key
        self.run_sync = run_sync
        self.model = model
        self.db_path = db_path
        self.store = store

    def run(self):
        try:
            rep = generate_after_hours_report(
                trade_date=self.trade_date,
                person_key=self.person_key,
                run_sync=self.run_sync,
                provider="gemini",
                model=self.model,
                trigger="manual",
                db_path=self.db_path,
                store=self.store,
            )
            self.finished_report.emit(rep)
        except Exception as exc:
            self.failed.emit(str(exc))


class _FollowupWorker(QThread):
    finished_reply = Signal(dict)
    failed = Signal(str)

    def __init__(self, report: dict, question: str, model: Optional[str], store: AfterHoursReportStore):
        super().__init__()
        self.report = report
        self.question = question
        self.model = model
        self.store = store

    def run(self):
        try:
            res = ask_after_hours_followup(
                report=self.report,
                question=self.question,
                provider="gemini",
                model=self.model,
                store=self.store,
            )
            self.finished_reply.emit(res)
        except Exception as exc:
            self.failed.emit(str(exc))


class AfterHoursReportPage(QWidget):
    """美股盘后复盘日报 2.0 桌面端交互视图（全自动折行、无需左右拖动）。"""

    personSelectionChanged = Signal(object)

    def __init__(self, parent=None, store: Optional[AfterHoursReportStore] = None, db_path: Path = DATABASE_PATH):
        super().__init__(parent)
        self.store = store or AfterHoursReportStore()
        self.db_path = db_path
        self._reports: list[dict] = []
        self._current_report: Optional[dict] = None
        self._person_filter: Optional[str] = None
        self._person_id_by_key: dict[str, Optional[int]] = {"个人": 1, "ALL": None}
        self._syncing_person: bool = False
        self._mover_filter: str = "all"
        self._mover_sort: str = "day_pnl"
        self._expand_all_movers: bool = False
        self._expanded_syms: set[str] = set()
        self._sidebar_collapsed: bool = False
        self._prev_review_expanded: bool = True
        self._scroll_anim: Optional[QPropertyAnimation] = None
        self._worker: Optional[_AfterHoursWorker] = None
        self._followup_worker: Optional[_FollowupWorker] = None
        self._build_ui()
        self.reload()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # 顶部控制栏
        top_card = QFrame()
        top_card.setObjectName("card")
        top = QHBoxLayout(top_card)
        top.setContentsMargins(12, 9, 12, 9)
        top.setSpacing(8)

        self.expand_sidebar_btn = QPushButton("» 存档")
        self.expand_sidebar_btn.setStyleSheet(
            "font-size: 11.5px; padding: 4px 8px; color: #2563EB; border: 1px solid #BFDBFE; background: #EFF6FF;"
        )
        self.expand_sidebar_btn.setToolTip("展开左侧历史复盘存档栏")
        self.expand_sidebar_btn.clicked.connect(lambda: self._set_sidebar_collapsed(False))
        self.expand_sidebar_btn.hide()
        top.addWidget(self.expand_sidebar_btn)

        top.addWidget(QLabel("交易日："))
        default_d = resolve_default_trade_date()
        self.date_edit = DatePicker(QDate(default_d.year, default_d.month, default_d.day))
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDateRange(QDate(2020, 1, 1), QDate.currentDate())
        self.date_edit.track_today_max = True
        self.date_edit.setMinimumWidth(148)
        top.addWidget(self.date_edit)

        top.addWidget(QLabel("范围："))
        self.person_combo = QComboBox()
        self.person_combo.setMinimumWidth(100)
        self.person_combo.addItem("个人", "个人")
        self.person_combo.addItem("全部人员", "ALL")
        self.person_combo.currentIndexChanged.connect(self._on_person_combo_changed)
        top.addWidget(self.person_combo)

        self.sync_check = QCheckBox("生成前同步流水")
        self.sync_check.setChecked(True)
        top.addWidget(self.sync_check)

        top.addStretch()

        today_session = get_us_market_session(current_ny_now().date())
        self.status_lbl = QLabel(format_session_hint(today_session))
        self.status_lbl.setStyleSheet(
            "font-size: 11.5px; color: #B45309; font-weight: 600;"
            if not today_session.is_trading_day
            else ("font-size: 11.5px; color: #1D4ED8;" if today_session.is_early_close else "font-size: 11.5px; color: #64748B;")
        )
        top.addWidget(self.status_lbl)

        self.generate_btn = QPushButton("生成 / 重算日报")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.clicked.connect(self._start_generate)
        top.addWidget(self.generate_btn)

        root.addWidget(top_card)

        # 主体左右分栏
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        # 左侧：历史存档时间轴（定宽区间，可折叠以释放右侧复盘空间）
        self.left_card = QFrame()
        self.left_card.setObjectName("card")
        self.left_card.setMinimumWidth(205)
        self.left_card.setMaximumWidth(248)
        left_layout = QVBoxLayout(self.left_card)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        hdr_l = QHBoxLayout()
        hdr_l.setSpacing(4)
        title_l = QLabel("历史复盘存档")
        title_l.setStyleSheet("font-size: 12.5px; font-weight: 700; color: #0F172A;")
        hdr_l.addWidget(title_l)
        hdr_l.addStretch()
        self.count_lbl = QLabel("0 份")
        self.count_lbl.setStyleSheet("font-size: 11.5px; color: #64748B;")
        hdr_l.addWidget(self.count_lbl)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setFixedSize(24, 24)
        self.refresh_btn.setToolTip("刷新历史复盘存档列表")
        self.refresh_btn.setStyleSheet(
            "QPushButton { font-size: 13px; padding: 0; border-radius: 6px; border: 1px solid #E2E8F0; background: #F8FAFC; color: #475569; }"
            "QPushButton:hover { border-color: #2563EB; color: #2563EB; background: #EFF6FF; }"
        )
        self.refresh_btn.clicked.connect(lambda: self.reload())
        hdr_l.addWidget(self.refresh_btn)

        self.collapse_sidebar_btn = QPushButton("«")
        self.collapse_sidebar_btn.setFixedSize(24, 24)
        self.collapse_sidebar_btn.setToolTip("收起历史复盘存档栏")
        self.collapse_sidebar_btn.setStyleSheet(
            "QPushButton { font-size: 13px; padding: 0; border-radius: 6px; border: 1px solid #E2E8F0; background: #F8FAFC; color: #475569; }"
            "QPushButton:hover { border-color: #2563EB; color: #2563EB; background: #EFF6FF; }"
        )
        self.collapse_sidebar_btn.clicked.connect(lambda: self._set_sidebar_collapsed(True))
        hdr_l.addWidget(self.collapse_sidebar_btn)
        left_layout.addLayout(hdr_l)

        pill_row = QHBoxLayout()
        pill_row.setSpacing(4)
        self._pill_group = QButtonGroup(self)
        for idx, (lbl, pkey) in enumerate((("全部", None), ("个人", "个人"), ("全部人员", "ALL"))):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setChecked(idx == 0)
            btn.setMinimumWidth(0)
            btn.setStyleSheet(
                "QPushButton { font-size: 11px; padding: 3px 8px; border-radius: 10px; border: 1px solid #CBD5E1; background: #F8FAFC; color: #475569; }"
                "QPushButton:checked { border-color: #2563EB; background: #EFF6FF; color: #2563EB; font-weight: 600; }"
            )
            btn.clicked.connect(lambda _=False, k=pkey: self._set_person_filter(k))
            self._pill_group.addButton(btn)
            pill_row.addWidget(btn)
        pill_row.addStretch()
        left_layout.addLayout(pill_row)

        self.report_list = QListWidget()
        self.report_list.setSpacing(4)
        self.report_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.report_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; }"
            "QListWidget::item { border: 1px solid #E2E8F0; border-radius: 8px; padding: 6px; background: #F8FAFC; }"
            "QListWidget::item:selected { border: 1px solid #2563EB; background: #EFF6FF; color: #0F172A; }"
        )
        self.report_list.currentRowChanged.connect(self._on_report_selected)
        left_layout.addWidget(self.report_list, 1)

        # 右侧：锁宽自动折行滚动工作台
        self.right_scroll = _FitWidthScrollArea()
        self.right_container = QWidget()
        self.right_container.setMinimumWidth(0)
        right_layout = QVBoxLayout(self.right_container)
        right_layout.setContentsMargins(2, 0, 4, 4)
        right_layout.setSpacing(10)

        # 1. Hero 驾驶舱卡片（两行分层：标题+按钮 / 状态胶囊+元信息）
        self.hero_card = QFrame()
        self.hero_card.setObjectName("card")
        self.hero_card.setMinimumWidth(0)
        hero_l = QVBoxLayout(self.hero_card)
        hero_l.setContentsMargins(14, 14, 14, 14)
        hero_l.setSpacing(10)

        hero_hdr = QVBoxLayout()
        hero_hdr.setSpacing(5)

        hero_row1 = QHBoxLayout()
        hero_row1.setSpacing(8)
        self.detail_title = _make_wrap_label(
            "请选择左侧日报或点击右上角立即生成",
            "font-size: 15px; font-weight: 700; color: #0F172A;",
        )
        hero_row1.addWidget(self.detail_title, 1)

        hero_actions = QHBoxLayout()
        hero_actions.setSpacing(6)
        self.jump_followup_btn = QPushButton("追问 Gemini")
        self.jump_followup_btn.setStyleSheet(
            "color: #2563EB; border: 1px solid #BFDBFE; background: #EFF6FF; font-weight: 600;"
        )
        self.jump_followup_btn.setToolTip("平滑滚动到底部并聚焦追问输入框")
        self.jump_followup_btn.clicked.connect(self._scroll_to_followup)
        self.jump_followup_btn.setEnabled(False)
        hero_actions.addWidget(self.jump_followup_btn)

        self.copy_btn = QPushButton("复制 Markdown")
        self.copy_btn.clicked.connect(self._copy_markdown)
        self.copy_btn.setEnabled(False)
        hero_actions.addWidget(self.copy_btn)

        self.delete_btn = QPushButton("删除存档")
        self.delete_btn.setStyleSheet("color: #DC2626; border: 1px solid #FCA5A5;")
        self.delete_btn.clicked.connect(self._delete_current_report)
        self.delete_btn.setEnabled(False)
        hero_actions.addWidget(self.delete_btn)
        hero_actions.setAlignment(Qt.AlignmentFlag.AlignTop)
        hero_row1.addLayout(hero_actions)
        hero_hdr.addLayout(hero_row1)

        hero_row2 = QHBoxLayout()
        hero_row2.setSpacing(6)
        self.regime_badge = QLabel("")
        self.regime_badge.hide()
        hero_row2.addWidget(self.regime_badge)
        self.session_badge = QLabel("")
        self.session_badge.hide()
        hero_row2.addWidget(self.session_badge)
        self.sync_badge = QLabel("")
        self.sync_badge.hide()
        hero_row2.addWidget(self.sync_badge)
        self.detail_meta = _make_wrap_label("", "font-size: 11.5px; color: #64748B;")
        hero_row2.addWidget(self.detail_meta, 1)
        hero_hdr.addLayout(hero_row2)

        hero_l.addLayout(hero_hdr)

        # TL;DR 横幅
        self.tldr_frame = QFrame()
        self.tldr_frame.setMinimumWidth(0)
        self.tldr_frame.setStyleSheet("QFrame { border: 1px solid #BFDBFE; border-radius: 8px; background: #EFF6FF; }")
        tldr_row = QHBoxLayout(self.tldr_frame)
        tldr_row.setContentsMargins(10, 8, 10, 8)
        tldr_row.setSpacing(8)
        tldr_tag = QLabel("核心定性")
        tldr_tag.setStyleSheet("font-size: 11px; font-weight: 700; color: white; background: #2563EB; border-radius: 4px; padding: 2px 6px;")
        tldr_row.addWidget(tldr_tag, 0, Qt.AlignmentFlag.AlignTop)
        self.tldr_lbl = _make_wrap_label("", "font-size: 12.5px; color: #0F172A; border: none; background: transparent;")
        tldr_row.addWidget(self.tldr_lbl, 1)
        self.tldr_frame.hide()
        hero_l.addWidget(self.tldr_frame)

        # 4 张 KPI 卡片（等宽拉伸 + 零最小宽限制，确保全部同屏展示）
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(8)
        self.kpi_pnl_val, self.kpi_pnl_sub, self.kpi_alpha_lbl, self.kpi_card_1 = self._make_kpi_card("当日真实盈亏")
        self.kpi_trend_sub = _make_wrap_label("", "font-size: 10.5px; color: #64748B; border: none; padding-top: 2px;")
        self.kpi_trend_sub.setTextFormat(Qt.TextFormat.RichText)
        self.kpi_trend_sub.hide()
        self.kpi_card_1.layout().addWidget(self.kpi_trend_sub)

        self.kpi_mv_val, self.kpi_un_sub, _, self.kpi_card_2 = self._make_kpi_card("总资产（净值）/ 持仓")
        self.kpi_cash_sub = _make_wrap_label("", "font-size: 11px; color: #64748B; border: none;")
        self.kpi_card_2.layout().addWidget(self.kpi_cash_sub)

        self.kpi_bench_val, self.kpi_bench_sub, _, self.kpi_card_3 = self._make_kpi_card("大盘基准")
        self.kpi_bench_val.hide()
        self.kpi_bench_sub.setTextFormat(Qt.TextFormat.RichText)
        self.kpi_macro_sub = _make_wrap_label("", "font-size: 10.5px; color: #64748B; border: none; padding-top: 2px;")
        self.kpi_macro_sub.setTextFormat(Qt.TextFormat.RichText)
        self.kpi_macro_sub.hide()
        self.kpi_card_3.layout().addWidget(self.kpi_macro_sub)

        self.kpi_lev_val, self.kpi_lev_sub, self.kpi_eff_lev_badge, self.kpi_card_4 = self._make_kpi_card("杠杆仓位暴露")
        self.kpi_trades_lbl = self.kpi_eff_lev_badge
        self.kpi_eff_lev_badge.hide()
        self.lev_bar = QProgressBar()
        self.lev_bar.setRange(0, 100)
        self.lev_bar.setTextVisible(False)
        self.lev_bar.setFixedHeight(6)
        self.kpi_card_4.layout().addWidget(self.lev_bar)

        for col, card in enumerate((self.kpi_card_1, self.kpi_card_2, self.kpi_card_3, self.kpi_card_4)):
            kpi_grid.addWidget(card, 0, col)
            kpi_grid.setColumnStretch(col, 1)
        hero_l.addLayout(kpi_grid)

        # 未来 7 天关键事件预警条（仅当存在持仓财报或宏观大事件时显示，无事件零占位）
        self.events_frame = QFrame()
        self.events_frame.setMinimumWidth(0)
        self.events_frame.setStyleSheet("QFrame { border: 1px solid #FDE68A; border-radius: 8px; background: #FFFBEB; }")
        ev_row = QHBoxLayout(self.events_frame)
        ev_row.setContentsMargins(10, 6, 10, 6)
        ev_row.setSpacing(8)
        ev_tag = QLabel("未来7天预警")
        ev_tag.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        ev_tag.setStyleSheet("font-size: 10.5px; font-weight: 700; color: #92400E; background: #FEF3C7; border-radius: 4px; padding: 2px 6px; border: none;")
        ev_row.addWidget(ev_tag, 0, Qt.AlignmentFlag.AlignTop)
        self.events_lbl = _make_wrap_label("", "font-size: 11.5px; color: #78350F; border: none; background: transparent;")
        self.events_lbl.setTextFormat(Qt.TextFormat.RichText)
        ev_row.addWidget(self.events_lbl, 1)
        self.events_frame.hide()
        hero_l.addWidget(self.events_frame)

        right_layout.addWidget(self.hero_card)

        # 2. 多视角 Tabs
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMinimumWidth(0)

        # Tab 1: 结构化复盘总览（首屏先展示风控雷达 + 次日清单，下方为重点持仓异动）
        tab_overview = QWidget()
        tab_overview.setMinimumWidth(0)
        ov_l = QVBoxLayout(tab_overview)
        ov_l.setContentsMargins(8, 10, 8, 10)
        ov_l.setSpacing(12)

        # 1) 风险雷达 & 可勾选清单双栏（首屏置顶）
        self.rc_container = QWidget()
        self.rc_container.setMinimumWidth(0)
        rc_row = QHBoxLayout(self.rc_container)
        rc_row.setContentsMargins(0, 0, 0, 0)
        rc_row.setSpacing(10)

        risk_card = QFrame()
        risk_card.setObjectName("card")
        risk_card.setMinimumWidth(0)
        risk_outer = QVBoxLayout(risk_card)
        risk_outer.setContentsMargins(12, 10, 12, 10)
        risk_outer.setSpacing(8)
        r_hdr = QHBoxLayout()
        r_lbl = QLabel("仓位结构与风险雷达")
        r_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A;")
        r_hdr.addWidget(r_lbl)
        r_hdr.addStretch()
        self.risk_tag = QLabel("")
        r_hdr.addWidget(self.risk_tag)
        risk_outer.addLayout(r_hdr)

        # 底层资产与板块穿透敞口 Top 3 胶囊行（无数据时自动隐藏）
        self.exposure_frame = QFrame()
        self.exposure_frame.setMinimumWidth(0)
        self.exposure_frame.setStyleSheet("QFrame { border: 1px solid #E2E8F0; border-radius: 6px; background: #F8FAFC; }")
        exp_l = QHBoxLayout(self.exposure_frame)
        exp_l.setContentsMargins(8, 5, 8, 5)
        exp_l.setSpacing(6)
        self.exposure_lbl = _make_wrap_label("", "font-size: 11.5px; color: #334155; border: none; background: transparent;")
        self.exposure_lbl.setTextFormat(Qt.TextFormat.RichText)
        exp_l.addWidget(self.exposure_lbl, 1)
        self.exposure_frame.hide()
        risk_outer.addWidget(self.exposure_frame)

        self.risks_box = QVBoxLayout()
        self.risks_box.setSpacing(6)
        risk_outer.addLayout(self.risks_box)
        risk_outer.addStretch()

        cl_card = QFrame()
        cl_card.setObjectName("card")
        cl_card.setMinimumWidth(0)
        cl_outer = QVBoxLayout(cl_card)
        cl_outer.setContentsMargins(12, 10, 12, 10)
        cl_outer.setSpacing(8)
        c_hdr = QHBoxLayout()
        c_lbl = QLabel("次日盘前关注清单")
        c_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A;")
        c_hdr.addWidget(c_lbl)
        c_hdr.addStretch()
        self.cl_progress_lbl = QLabel("0 / 0 已核查")
        self.cl_progress_lbl.setStyleSheet("font-size: 11.5px; font-weight: 600; color: #2563EB;")
        c_hdr.addWidget(self.cl_progress_lbl)
        cl_outer.addLayout(c_hdr)

        # 昨日计划复盘可折叠浅色提示块（无昨日复盘数据时自动隐藏）
        self.prev_review_frame = QFrame()
        self.prev_review_frame.setMinimumWidth(0)
        self.prev_review_frame.setStyleSheet("QFrame { border: 1px solid #BFDBFE; border-radius: 6px; background: #EFF6FF; }")
        pr_outer = QVBoxLayout(self.prev_review_frame)
        pr_outer.setContentsMargins(8, 6, 8, 6)
        pr_outer.setSpacing(4)
        self.prev_review_toggle_btn = QPushButton("▾ 昨日计划复盘")
        self.prev_review_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_review_toggle_btn.setStyleSheet(
            "QPushButton { text-align: left; font-size: 11.5px; font-weight: 700; color: #1D4ED8; "
            "border: none; background: transparent; padding: 0; min-height: 16px; }"
        )
        self.prev_review_toggle_btn.clicked.connect(self._toggle_prev_review)
        pr_outer.addWidget(self.prev_review_toggle_btn)
        self.prev_review_lbl = _make_wrap_label("", "font-size: 11.5px; color: #1E293B; border: none; background: transparent;")
        self.prev_review_lbl.setTextFormat(Qt.TextFormat.RichText)
        pr_outer.addWidget(self.prev_review_lbl)
        self.prev_review_frame.hide()
        cl_outer.addWidget(self.prev_review_frame)

        self.checklist_box = QVBoxLayout()
        self.checklist_box.setSpacing(6)
        cl_outer.addLayout(self.checklist_box)
        cl_outer.addStretch()

        rc_row.addWidget(risk_card, 1)
        rc_row.addWidget(cl_card, 1)
        ov_l.addWidget(self.rc_container)

        # 2) 重点持仓异动与归因（支持排序、筛选、分级默认折叠）
        self.movers_section = QWidget()
        self.movers_section.setMinimumWidth(0)
        mv_outer = QVBoxLayout(self.movers_section)
        mv_outer.setContentsMargins(0, 0, 0, 0)
        mv_outer.setSpacing(8)

        mf_hdr = QHBoxLayout()
        mf_hdr.setSpacing(6)
        mf_title = QLabel("重点持仓异动与归因")
        mf_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A;")
        mf_hdr.addWidget(mf_title)
        mf_hdr.addStretch()

        sort_lbl = QLabel("排序：")
        sort_lbl.setStyleSheet("font-size: 11.5px; color: #64748B;")
        mf_hdr.addWidget(sort_lbl)
        self.mover_sort_combo = QComboBox()
        self.mover_sort_combo.setMinimumWidth(132)
        self.mover_sort_combo.addItem("按当日盈亏贡献", "day_pnl")
        self.mover_sort_combo.addItem("按仓位权重", "weight")
        self.mover_sort_combo.addItem("按涨跌幅", "change")
        self.mover_sort_combo.currentIndexChanged.connect(self._on_mover_sort_changed)
        mf_hdr.addWidget(self.mover_sort_combo)

        self.toggle_expand_all_btn = QPushButton("全部展开")
        self.toggle_expand_all_btn.setStyleSheet(
            "font-size: 11.5px; padding: 4px 9px; border-radius: 6px; border: 1px solid #CBD5E1; background: #F8FAFC; color: #334155;"
        )
        self.toggle_expand_all_btn.clicked.connect(self._toggle_expand_all_movers)
        mf_hdr.addWidget(self.toggle_expand_all_btn)
        mv_outer.addLayout(mf_hdr)

        mf_row = QHBoxLayout()
        mf_row.setSpacing(6)
        self._mf_group = QButtonGroup(self)
        for idx, (lbl, mkey) in enumerate((
            ("全部", "all"),
            ("领涨", "up"),
            ("领跌", "down"),
            ("盘后波动", "after"),
            ("有突发新闻", "news"),
        )):
            b = QPushButton(lbl)
            b.setCheckable(True)
            b.setChecked(idx == 0)
            b.setMinimumWidth(0)
            b.setStyleSheet(
                "QPushButton { font-size: 11px; padding: 3px 8px; border-radius: 6px; border: 1px solid #CBD5E1; background: #F8FAFC; color: #475569; }"
                "QPushButton:checked { border-color: #2563EB; background: #EFF6FF; color: #2563EB; font-weight: 600; }"
            )
            b.clicked.connect(lambda _=False, k=mkey: self._set_mover_filter(k))
            self._mf_group.addButton(b)
            mf_row.addWidget(b)
        mf_row.addStretch()
        mv_outer.addLayout(mf_row)

        self.movers_box = QVBoxLayout()
        self.movers_box.setSpacing(8)
        mv_outer.addLayout(self.movers_box)
        ov_l.addWidget(self.movers_section)

        # Tab 2: 今日操作复盘
        tab_trades = QWidget()
        tab_trades.setMinimumWidth(0)
        tr_l = QVBoxLayout(tab_trades)
        tr_l.setContentsMargins(8, 10, 8, 10)
        tr_l.setSpacing(8)
        self.trades_summary_lbl = _make_wrap_label(
            "",
            "padding: 8px 10px; border-radius: 8px; background: #F8FAFC; border: 1px solid #E2E8F0; font-size: 12.5px;",
        )
        tr_l.addWidget(self.trades_summary_lbl)
        self.trades_box = QVBoxLayout()
        self.trades_box.setSpacing(8)
        tr_l.addLayout(self.trades_box)
        tr_l.addStretch()

        # Tab 3: 突发新闻情报
        tab_news = QWidget()
        tab_news.setMinimumWidth(0)
        nw_l = QVBoxLayout(tab_news)
        nw_l.setContentsMargins(8, 10, 8, 10)
        nw_l.setSpacing(8)
        self.news_box = QVBoxLayout()
        self.news_box.setSpacing(8)
        nw_l.addLayout(self.news_box)
        nw_l.addStretch()

        # Tab 4: 完整 Markdown 长文
        tab_md = QWidget()
        tab_md.setMinimumWidth(0)
        md_l = QVBoxLayout(tab_md)
        md_l.setContentsMargins(8, 10, 8, 10)
        self.viewer = QTextBrowser()
        self.viewer.setMinimumHeight(420)
        self.viewer.setOpenExternalLinks(False)
        self.viewer.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewer.setStyleSheet("QTextBrowser { border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px; background: #FFFFFF; font-size: 13px; }")
        md_l.addWidget(self.viewer)

        self.tabs.addTab(tab_overview, "结构化复盘总览")
        self.tabs.addTab(tab_trades, "今日操作复盘 (0)")
        self.tabs.addTab(tab_news, "突发新闻情报 (0)")
        self.tabs.addTab(tab_md, "完整长文 (Markdown)")
        right_layout.addWidget(self.tabs)

        # 3. 底部带上下文追问条（快捷问题竖排，绝不横向撑爆页面）
        fq_card = QFrame()
        fq_card.setObjectName("card")
        fq_card.setMinimumWidth(0)
        fq_l = QVBoxLayout(fq_card)
        fq_l.setContentsMargins(12, 10, 12, 10)
        fq_l.setSpacing(8)

        fq_hdr = QLabel("基于这份盘后日报继续追问 Gemini（点击快捷问题可直接发送）")
        fq_hdr.setStyleSheet("font-size: 12px; font-weight: 700; color: #475569;")
        fq_l.addWidget(fq_hdr)

        self.fq_chips_box = QVBoxLayout()
        self.fq_chips_box.setSpacing(5)
        fq_l.addLayout(self.fq_chips_box)

        fq_input_row = QHBoxLayout()
        self.followup_input = QLineEdit()
        self.followup_input.setMinimumWidth(0)
        self.followup_input.setPlaceholderText("输入想基于当前日报继续追问的问题（回车发送）...")
        self.followup_input.returnPressed.connect(lambda: self._start_followup())
        fq_input_row.addWidget(self.followup_input, 1)

        self.followup_btn = QPushButton("追问 Gemini")
        self.followup_btn.setObjectName("primary")
        self.followup_btn.clicked.connect(lambda: self._start_followup())
        fq_input_row.addWidget(self.followup_btn)
        fq_l.addLayout(fq_input_row)

        self.fq_history_box = QVBoxLayout()
        self.fq_history_box.setSpacing(6)
        fq_l.addLayout(self.fq_history_box)

        right_layout.addWidget(fq_card)
        right_layout.addStretch()

        self.right_scroll.setWidget(self.right_container)

        self.splitter.addWidget(self.left_card)
        self.splitter.addWidget(self.right_scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter, 1)

    @staticmethod
    def _make_kpi_card(title: str):
        card = QFrame()
        card.setMinimumWidth(0)
        card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        card.setStyleSheet("QFrame { border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; }")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(4)
        t_lbl = _make_wrap_label(title, "font-size: 11px; color: #64748B; border: none;")
        top.addWidget(t_lbl, 1)
        right_lbl = QLabel("")
        right_lbl.setMinimumWidth(0)
        right_lbl.setStyleSheet("font-size: 10.5px; font-weight: 600; border: none;")
        top.addWidget(right_lbl)
        lay.addLayout(top)
        val_lbl = _make_wrap_label("—", "font-size: 15.5px; font-weight: 700; color: #0F172A; border: none;")
        lay.addWidget(val_lbl)
        sub_lbl = _make_wrap_label("", "font-size: 11px; color: #64748B; border: none;")
        lay.addWidget(sub_lbl)
        return val_lbl, sub_lbl, right_lbl, card

    def populate_persons(self, persons: list[dict] | tuple[dict, ...]):
        current = self.person_combo.currentData() or "个人"
        self._person_id_by_key = {"ALL": None, "个人": 1}
        self.person_combo.blockSignals(True)
        self.person_combo.clear()
        self.person_combo.addItem("个人", "个人")
        self.person_combo.addItem("全部人员", "ALL")
        for p in persons or ():
            name = str(p.get("name") or "").strip()
            pid = p.get("id")
            if name == "个人" and pid is not None:
                self._person_id_by_key["个人"] = pid
            elif name and name != "个人":
                self._person_id_by_key[name] = pid
                self.person_combo.addItem(name, name)
        idx = self.person_combo.findData(current)
        self.person_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.person_combo.blockSignals(False)

    def _focus_latest_report_for_key(self, person_key: str):
        """在当前存档列表中优先聚焦到匹配人员的最新一份复盘。"""
        if not self._reports:
            return
        for i, rep in enumerate(self._reports):
            if str(rep.get("person_key") or "") == person_key:
                self.report_list.setCurrentRow(i)
                return

    def sync_person_from_main(self, person_id, persons: list[dict] | tuple[dict, ...] | None = None):
        """跟随主窗口全局人员筛选框同步当前范围，并聚焦该人员最新复盘。"""
        if persons and self.person_combo.count() <= 2:
            self.populate_persons(persons)
        if person_id is None:
            target_key = "ALL"
        else:
            matched_key = next((k for k, v in self._person_id_by_key.items() if v == person_id), None)
            target_key = matched_key or "个人"
        idx = self.person_combo.findData(target_key)
        if idx >= 0 and self.person_combo.currentIndex() != idx:
            self._syncing_person = True
            self.person_combo.blockSignals(True)
            self.person_combo.setCurrentIndex(idx)
            self.person_combo.blockSignals(False)
            self._syncing_person = False
        self._focus_latest_report_for_key(target_key)

    def _on_person_combo_changed(self):
        if self._syncing_person:
            return
        key = str(self.person_combo.currentData() or "个人")
        self._focus_latest_report_for_key(key)
        pid = self._person_id_by_key.get(key)
        if key == "ALL":
            pid = None
        self.personSelectionChanged.emit(pid)

    def _set_person_filter(self, pkey: Optional[str]):
        self._person_filter = pkey
        self.reload()

    def _set_mover_filter(self, mkey: str):
        self._mover_filter = mkey
        if self._current_report:
            self._render_movers(self._current_report)

    def reload(self, prefer_id: Optional[str] = None):
        self._reports = self.store.list_reports(person_key=self._person_filter)
        self.count_lbl.setText(f"{len(self._reports)} 份")
        self.report_list.clear()
        select_row = 0
        for i, rep in enumerate(self._reports):
            pnl_str = _fmt_money(rep.get("day_pnl"), signed=True)
            pct_str = _fmt_pct(rep.get("day_pnl_pct"))
            trig = "自动" if rep.get("trigger") == "auto" else "手动"
            lev = rep.get("leverage_pct")
            lev_str = f" · 杠杆 {float(lev) * 100:.0f}%" if lev is not None else ""
            label = (
                f"{rep.get('trade_date')} · {rep.get('person_name')} [{trig}]\n"
                f"当日盈亏: {pnl_str} ({pct_str})\n"
                f"{rep.get('trade_count', 0)} 笔成交{lev_str}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, rep.get("id"))
            self.report_list.addItem(item)
            if prefer_id and rep.get("id") == prefer_id:
                select_row = i

        if self._reports:
            self.report_list.setCurrentRow(select_row)
        else:
            self._current_report = None
            self.jump_followup_btn.setEnabled(False)
            self.copy_btn.setEnabled(False)
            self.delete_btn.setEnabled(False)
            self.session_badge.hide()
            self.regime_badge.hide()
            self.sync_badge.hide()
            self.kpi_trend_sub.hide()
            self.kpi_macro_sub.hide()
            self.events_frame.hide()
            self.exposure_frame.hide()
            self.prev_review_frame.hide()
            self.detail_title.setText("暂无盘后复盘日报")
            self.detail_meta.setText("点击右上角「生成 / 重算日报」可立即生成")
            self.tldr_frame.hide()
            self.viewer.setHtml("<p style='color:#64748B;'>暂无历史盘后复盘日报。</p>")

    def _default_expanded_symbols(self, holdings: list[dict]) -> set[str]:
        """默认仅展开仓位权重 >= 5% 的前 3~5 只核心仓位，其余尾部小仓位折叠为摘要条。"""
        ranked = sorted(
            [h for h in holdings if h.get("symbol")],
            key=lambda x: float(x.get("weight_pct") or 0.0),
            reverse=True,
        )
        if not ranked:
            return set()
        core = [h for h in ranked if float(h.get("weight_pct") or 0.0) >= 0.05]
        if len(core) < 3:
            core = ranked[:3]
        elif len(core) > 5:
            core = core[:5]
        return {str(h.get("symbol")) for h in core if h.get("symbol")}

    def _set_sidebar_collapsed(self, collapsed: bool):
        self._sidebar_collapsed = bool(collapsed)
        self.left_card.setVisible(not self._sidebar_collapsed)
        self.expand_sidebar_btn.setVisible(self._sidebar_collapsed)
        self.right_scroll._sync_widget_width()
        QTimer.singleShot(0, self.right_scroll._sync_widget_width)

    def _scroll_to_followup(self):
        if self._scroll_anim is not None and self._scroll_anim.state() == QPropertyAnimation.State.Running:
            self._scroll_anim.stop()
        sb = self.right_scroll.verticalScrollBar()
        if sb is None or sb.value() == sb.maximum():
            self.followup_input.setFocus()
            return
        anim = QPropertyAnimation(sb, b"value", self)
        anim.setDuration(180)
        anim.setStartValue(sb.value())
        anim.setEndValue(sb.maximum())
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self.followup_input.setFocus)
        self._scroll_anim = anim
        anim.start()

    def _on_mover_sort_changed(self):
        self._mover_sort = str(self.mover_sort_combo.currentData() or "day_pnl")
        if self._current_report:
            self._render_movers(self._current_report)

    def _get_filtered_holdings(self, rep: dict) -> list[dict]:
        snap = rep.get("snapshot") or {}
        holdings = snap.get("holdings") or []
        structured = rep.get("structured") or {}
        filtered = []
        for h in holdings:
            if self._mover_filter == "up" and (h.get("regular_change_pct") or 0) <= 0:
                continue
            if self._mover_filter == "down" and (h.get("regular_change_pct") or 0) >= 0:
                continue
            if self._mover_filter == "after" and (
                h.get("after_hours_change_pct") is None or abs(float(h.get("after_hours_change_pct"))) < 0.005
            ):
                continue
            if self._mover_filter == "news" and not self._get_holding_news(snap, h, structured):
                continue
            filtered.append(h)
        return filtered

    def _toggle_expand_all_movers(self):
        if not self._current_report:
            return
        holdings = (self._current_report.get("snapshot") or {}).get("holdings") or []
        filtered = self._get_filtered_holdings(self._current_report)
        filtered_syms = {str(h.get("symbol")) for h in filtered if h.get("symbol")}
        if not filtered_syms:
            return
        if filtered_syms.issubset(self._expanded_syms):
            default_core = self._default_expanded_symbols(holdings)
            small_in_filtered = filtered_syms - default_core
            if small_in_filtered:
                self._expanded_syms -= small_in_filtered
            else:
                self._expanded_syms -= filtered_syms
            self._expand_all_movers = False
        else:
            self._expanded_syms |= filtered_syms
            self._expand_all_movers = True
        self._render_movers(self._current_report)

    def _on_report_selected(self, row: int):
        if row < 0 or row >= len(self._reports):
            return
        rid = self._reports[row].get("id")
        full = self.store.get_report(str(rid))
        if not full:
            return
        full = ensure_structured(full)
        self._current_report = full
        holdings = (full.get("snapshot") or {}).get("holdings") or []
        self._expand_all_movers = False
        self._expanded_syms = self._default_expanded_symbols(holdings)
        self._render_report(full)

    def _render_report(self, rep: dict):
        self.jump_followup_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
        snap = rep.get("snapshot") or {}
        summary = snap.get("summary") or {}
        benchmarks = snap.get("market_benchmarks") or {}
        structured = rep.get("structured") or {}
        trades = snap.get("trades") or []

        sync_info = rep.get("sync_status") or {}
        sync_tag = (
            "✓ 已自动同步" if (sync_info.get("ran") and sync_info.get("ok"))
            else ("⚠ 同步异常" if sync_info.get("ran") else "未执行同步")
        )
        self.detail_title.setText(f"{rep.get('trade_date')} 美股盘后复盘 · {rep.get('person_name')}")
        self.detail_meta.setText(
            f"生成：{(rep.get('generated_at') or '').replace('T', ' ')} · 引擎：{rep.get('provider')} "
            f"{'(' + rep.get('model') + ')' if rep.get('model') else ''}"
        )

        regime = structured.get("regime") or {}
        r_label = str(regime.get("label") or "")
        r_tone = str(regime.get("tone") or "warning")
        if r_label:
            r_bg = "#DCFCE7" if r_tone == "success" else ("#FEE2E2" if r_tone == "error" else "#FEF3C7")
            r_fg = "#166534" if r_tone == "success" else ("#991B1B" if r_tone == "error" else "#92400E")
            self.regime_badge.setText(r_label)
            self.regime_badge.setStyleSheet(f"font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; background: {r_bg}; color: {r_fg};")
            self.regime_badge.show()
        else:
            self.regime_badge.hide()

        ms = snap.get("market_session") or {}
        if ms.get("is_early_close"):
            self.session_badge.setText("半日市 · 13:00 ET 收盘")
            self.session_badge.setStyleSheet("font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; background: #DBEAFE; color: #1D4ED8;")
            self.session_badge.show()
        else:
            self.session_badge.hide()

        s_bg = "#FEE2E2" if sync_info.get("ok") is False else "#DCFCE7"
        s_fg = "#991B1B" if sync_info.get("ok") is False else "#166534"
        self.sync_badge.setText(sync_tag)
        self.sync_badge.setStyleSheet(f"font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; background: {s_bg}; color: {s_fg};")
        self.sync_badge.show()

        tldr = str(structured.get("tldr") or "")
        if tldr:
            self.tldr_lbl.setText(tldr)
            self.tldr_frame.show()
        else:
            self.tldr_frame.hide()

        # KPI 1-4
        self.kpi_pnl_val.setText(_fmt_money(summary.get("day_pnl"), signed=True))
        self.kpi_pnl_val.setStyleSheet(f"font-size: 15.5px; font-weight: 700; border: none; color: {_tone_color(summary.get('day_pnl'))};")
        pnl_sub_txt = f"持仓日收益 {_fmt_pct(summary.get('day_pnl_pct'))}"
        if summary.get("day_pnl_pct_total_assets") is not None:
            pnl_sub_txt += f" (总资产 {_fmt_pct(summary.get('day_pnl_pct_total_assets'))})"
        if summary.get("after_hours_day_pnl") is not None:
            pnl_sub_txt += (
                f"\n常规 {_fmt_money(summary.get('regular_day_pnl'), signed=True)} · "
                f"盘后 {_fmt_money(summary.get('after_hours_day_pnl'), signed=True)}"
            )
        self.kpi_pnl_sub.setText(pnl_sub_txt)
        denom_val = summary.get("day_pnl_denominator")
        if denom_val is not None:
            denom_lbl = summary.get("day_pnl_denominator_label") or "前日持仓市值"
            tip = f"持仓日收益率分母：{denom_lbl} {_fmt_money(denom_val)}"
            if summary.get("cash_balance") is not None:
                tip += f" | 总资产分母：{_fmt_money(float(denom_val) + float(summary['cash_balance']))}"
            self.kpi_pnl_sub.setToolTip(tip)
        else:
            self.kpi_pnl_sub.setToolTip("日收益率分母：前日持仓市值")

        # 近期趋势与回撤坐标（旧存档无 trend 字段时自动隐藏）
        trend = summary.get("trend") or {}
        t_parts = []
        if trend.get("pnl_5d") is not None:
            p5 = trend.get("pnl_5d")
            t_parts.append(f"近5日 <span style='color:{_tone_color(p5)}; font-weight:600;'>{_fmt_money(p5, signed=True)}</span>")
        if trend.get("pct_mtd") is not None:
            pm = trend.get("pct_mtd")
            t_parts.append(f"本月 <span style='color:{_tone_color(pm)}; font-weight:600;'>{_fmt_pct(pm)}</span>")
        if trend.get("drawdown_30d_pct") is not None:
            dd = float(trend.get("drawdown_30d_pct"))
            dd_col = "#DC2626" if dd < 0 else "#16A34A"
            t_parts.append(f"距30日高点 <span style='color:{dd_col}; font-weight:600;'>{dd * 100:.1f}%</span>")
        if trend.get("streak_days"):
            sd = int(trend["streak_days"])
            s_col = "#16A34A" if sd > 0 else "#DC2626"
            t_parts.append(f"<span style='color:{s_col}; font-weight:600;'>{'连盈' if sd > 0 else '连跌'}{abs(sd)}日</span>")
        if t_parts:
            self.kpi_trend_sub.setText(" · ".join(t_parts))
            wtd_tip = (
                f"本周 WTD：{_fmt_money(trend.get('pnl_wtd'), signed=True)} ({_fmt_pct(trend.get('pct_wtd'))})"
                if trend.get("pnl_wtd") is not None
                else ""
            )
            self.kpi_trend_sub.setToolTip(wtd_tip)
            self.kpi_trend_sub.show()
        else:
            self.kpi_trend_sub.setText("")
            self.kpi_trend_sub.hide()

        alpha = summary.get("alpha_vs_qqq")
        if alpha is not None:
            self.kpi_alpha_lbl.setText(f"vs QQQ {_fmt_pct(alpha)}")
            self.kpi_alpha_lbl.setStyleSheet(f"font-size: 10.5px; font-weight: 600; border: none; color: {_tone_color(alpha)};")
        else:
            self.kpi_alpha_lbl.setText("")

        cash_bal = summary.get("cash_balance")
        total_assets = summary.get("total_assets")
        if total_assets is None and cash_bal is not None and summary.get("total_market_value") is not None:
            total_assets = float(summary["total_market_value"]) + float(cash_bal)
        if total_assets is not None:
            self.kpi_mv_val.setText(_fmt_money(total_assets))
            cash_pct = summary.get("cash_pct")
            pct_str = f" ({float(cash_pct) * 100:.1f}%)" if cash_pct is not None else ""
            self.kpi_cash_sub.setText(f"持仓 {_fmt_money(summary.get('total_market_value'))} · 现金 {_fmt_money(cash_bal)}{pct_str}")
        else:
            self.kpi_mv_val.setText(_fmt_money(summary.get("total_market_value")))
            self.kpi_cash_sub.setText("可用现金 —（无独立现金账本）")

        anom_syms = summary.get("cost_anomaly_symbols") or []
        un_val = summary.get("total_unrealized_pnl")
        un_txt = f"{_pnl_prefix(un_val)} {_fmt_money(un_val, signed=True)} ({_fmt_pct(summary.get('unrealized_pct'))})"
        if anom_syms:
            un_txt += f" ⚠异常({','.join(anom_syms)})"
            if summary.get("unrealized_pnl_ex_anomaly") is not None:
                self.kpi_un_sub.setToolTip(
                    f"含成本异常标的 ({', '.join(anom_syms)})；剔除异常标的后参考累计盈亏：{_fmt_money(summary.get('unrealized_pnl_ex_anomaly'), signed=True)}"
                )
        else:
            self.kpi_un_sub.setToolTip("")
        self.kpi_un_sub.setText(un_txt)
        self.kpi_un_sub.setStyleSheet(f"font-size: 11px; border: none; color: {_tone_color(un_val)};")

        b_rows = []
        for k in ("SPY", "QQQ"):
            b = benchmarks.get(k)
            if b:
                reg_v = b.get("regular_change_pct")
                ah_v = b.get("after_hours_change_pct")
                b_rows.append(
                    f"<tr>"
                    f"<td style='padding:1px 6px 1px 0; font-weight:700; color:#0F172A;'>{k}</td>"
                    f"<td style='padding:1px 6px 1px 0; color:#64748B;'>收 <span style='color:{_tone_color(reg_v)}; font-weight:600;'>{_fmt_pct(reg_v)}</span></td>"
                    f"<td style='padding:1px 0; color:#64748B;'>盘后 <span style='color:{_tone_color(ah_v)}; font-weight:600;'>{_fmt_pct(ah_v)}</span></td>"
                    f"</tr>"
                )
        if b_rows:
            self.kpi_bench_sub.setText(f"<table cellspacing='0' cellpadding='0' style='font-size:11.5px;'>{''.join(b_rows)}</table>")
        else:
            self.kpi_bench_sub.setText("<span style='color:#64748B;'>暂无大盘基准行情</span>")

        # 市场风向标行：SMH / VIX / US10Y（缺项自动略过，全无则隐藏）
        m_parts = []
        smh_b = benchmarks.get("SMH")
        if smh_b and smh_b.get("regular_change_pct") is not None:
            smh_chg = smh_b.get("regular_change_pct")
            m_parts.append(f"<b>SMH</b> <span style='color:{_tone_color(smh_chg)}; font-weight:600;'>{_fmt_pct(smh_chg)}</span>")
        vix_b = benchmarks.get("^VIX")
        if vix_b and vix_b.get("regular_close") is not None:
            vix_p = float(vix_b["regular_close"])
            vix_chg = vix_b.get("regular_change_pct")
            vix_col = "#DC2626" if (vix_chg is not None and float(vix_chg) > 0) else ("#16A34A" if (vix_chg is not None and float(vix_chg) < 0) else "#64748B")
            chg_txt = f" (<span style='color:{vix_col};'>{_fmt_pct(vix_chg)}</span>)" if vix_chg is not None else ""
            m_parts.append(f"<b>VIX</b> {vix_p:.1f}{chg_txt}")
        tnx_b = benchmarks.get("^TNX")
        if tnx_b and tnx_b.get("regular_close") is not None:
            tnx_p = float(tnx_b["regular_close"])
            if tnx_p > 20:
                tnx_p = tnx_p / 10.0
            m_parts.append(f"<b>US10Y</b> {tnx_p:.2f}%")
        if m_parts:
            self.kpi_macro_sub.setText(" · ".join(m_parts))
            self.kpi_macro_sub.show()
        else:
            self.kpi_macro_sub.setText("")
            self.kpi_macro_sub.hide()

        # 未来 7 天事件预警条（仅当存在持仓财报或宏观大事件时显示）
        up_ev = snap.get("upcoming_events") or {}
        ev_items = []
        for ev in (up_ev.get("earnings") or [])[:4]:
            ev_items.append(
                f"<span style='background:#FEF3C7; color:#92400E; font-weight:600;'>[财报 T+{ev.get('days_away')}天]</span> "
                f"<b>{html.escape(str(ev.get('title') or ev.get('symbol') or ''))}</b> ({html.escape(str(ev.get('date') or ''))})"
            )
        for ev in (up_ev.get("macro") or [])[:4]:
            ev_items.append(
                f"<span style='background:#DBEAFE; color:#1D4ED8; font-weight:600;'>[宏观 T+{ev.get('days_away')}天]</span> "
                f"{html.escape(str(ev.get('title') or ''))} ({html.escape(str(ev.get('date') or ''))})"
            )
        if ev_items:
            self.events_lbl.setText(" &nbsp;·&nbsp; ".join(ev_items))
            self.events_frame.show()
        else:
            self.events_lbl.setText("")
            self.events_frame.hide()

        lev_pct = float(summary.get("leverage_pct") or 0) * 100.0
        eff_lev = summary.get("effective_leverage")
        self.kpi_lev_val.setText(f"{lev_pct:.1f}%")
        lev_hint = "高杠杆警戒" if lev_pct >= 50 else ("中高暴露" if lev_pct >= 35 else "稳健可控")
        self.kpi_lev_sub.setText(lev_hint)
        if eff_lev is not None:
            self.kpi_eff_lev_badge.setText(f"等效 {float(eff_lev):.2f}x")
            self.kpi_eff_lev_badge.setStyleSheet(
                "font-size: 10.5px; font-weight: 600; padding: 1px 7px; border-radius: 9px; "
                "background: #F1F5F9; color: #334155; border: 1px solid #CBD5E1;"
            )
            self.kpi_eff_lev_badge.show()
        else:
            self.kpi_eff_lev_badge.setText("")
            self.kpi_eff_lev_badge.hide()
        if summary.get("effective_leverage_total_assets") is not None and eff_lev is not None:
            eff_tip = f"持仓净名义敞口 {float(eff_lev):.2f}x | 含现金总资产净敞口 {float(summary['effective_leverage_total_assets']):.2f}x"
            self.kpi_lev_sub.setToolTip(eff_tip)
            self.kpi_eff_lev_badge.setToolTip(eff_tip)
        else:
            self.kpi_lev_sub.setToolTip("")
            self.kpi_eff_lev_badge.setToolTip("")
        bar_col = "#DC2626" if lev_pct >= 50 else ("#D97706" if lev_pct >= 35 else "#16A34A")
        self.kpi_lev_sub.setStyleSheet(f"font-size: 11px; font-weight: 600; border: none; color: {bar_col};")
        self.lev_bar.setValue(min(100, int(round(lev_pct))))
        self.lev_bar.setStyleSheet(f"QProgressBar {{ background: #E2E8F0; border-radius: 3px; }} QProgressBar::chunk {{ background: {bar_col}; border-radius: 3px; }}")

        # 渲染 4 个 Tab 内容
        self._render_movers(rep)
        self._render_risks_and_checklist(rep)
        self._render_trades(rep)
        news_cnt = self._render_news(rep)
        self.tabs.setTabText(1, f"今日操作复盘 ({len(trades)})")
        self.tabs.setTabText(2, f"突发新闻情报 ({news_cnt})")
        self.viewer.setMarkdown(str(rep.get("markdown") or ""))
        self._render_followups(rep)
        self.right_scroll._sync_widget_width()

    def _get_holding_news(self, snap: dict, h: dict, structured: Optional[dict] = None) -> list[dict]:
        st = structured or {}
        news_digest = st.get("news_digest") or []
        news_zh_map = st.get("news_zh_map") or {}
        syms = {str(h.get("symbol") or "").upper()}
        if h.get("underlying"):
            syms.add(str(h.get("underlying") or "").upper())

        if news_digest:
            matched = [d for d in news_digest if str(d.get("symbol") or "").upper() in syms]
            if matched:
                return [
                    {
                        "title": d.get("original_title") or d.get("zh_title") or "",
                        "zh_title": d.get("zh_title") or d.get("original_title") or "",
                        "summary": d.get("summary") or "",
                        "sentiment": d.get("sentiment") or "中性",
                        "publisher": d.get("publisher") or "",
                        "published_bj": d.get("published_bj") or "",
                        "url": d.get("url") or d.get("link") or "",
                    }
                    for d in matched
                ]

        news_map = snap.get("news") or {}
        own = news_map.get(h.get("symbol") or "") or []
        under = news_map.get(h.get("underlying") or "") or [] if h.get("underlying") else []
        seen = set()
        merged = []
        for n in [*own, *under]:
            t = str(n.get("title") or "")
            if t and t not in seen:
                seen.add(t)
                zh = news_zh_map.get(t) or {}
                merged.append({
                    **n,
                    "zh_title": zh.get("zh_title") or t,
                    "summary": zh.get("summary") or "",
                    "sentiment": zh.get("sentiment") or "",
                    "url": n.get("link") or n.get("url") or "",
                })
        return merged

    def _render_movers(self, rep: dict):
        _clear_layout(self.movers_box)
        snap = rep.get("snapshot") or {}
        structured = rep.get("structured") or {}
        reasons = structured.get("mover_reasons") or {}
        sentiments = structured.get("news_sentiments") or {}

        filtered = self._get_filtered_holdings(rep)

        if not filtered:
            self.toggle_expand_all_btn.setText("全部展开")
            empty = _make_wrap_label("当前筛选条件下无匹配持仓标的", "padding: 14px; color: #64748B; font-size: 12px;")
            self.movers_box.addWidget(empty)
            return

        if self._mover_sort == "weight":
            filtered.sort(key=lambda x: float(x.get("weight_pct") or 0.0), reverse=True)
        elif self._mover_sort == "change":
            filtered.sort(key=lambda x: abs(float(x.get("regular_change_pct") or 0.0)), reverse=True)
        else:
            filtered.sort(key=lambda x: abs(float(x.get("day_pnl") or 0.0)), reverse=True)

        filtered_syms = {str(h.get("symbol") or "") for h in filtered if h.get("symbol")}
        all_expanded = bool(filtered_syms) and filtered_syms.issubset(self._expanded_syms)
        self.toggle_expand_all_btn.setText("收起小仓位" if all_expanded else "全部展开")

        for h in filtered:
            sym = str(h.get("symbol") or "")
            h_news = self._get_holding_news(snap, h, structured)
            is_expanded = sym in self._expanded_syms
            w_pct = f"{float(h['weight_pct']) * 100:.1f}%" if h.get("weight_pct") is not None else "—"

            card = QFrame()
            card.setMinimumWidth(0)
            if h.get("cost_anomaly"):
                card.setStyleSheet("QFrame { border: 1px solid #F59E0B; border-radius: 8px; background: #FFFBEB; }")
            else:
                card.setStyleSheet("QFrame { border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; }")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 7 if not is_expanded else 9, 10, 7 if not is_expanded else 9)
            cl.setSpacing(6)

            def _add_holding_pills(row_layout):
                if h.get("underlying"):
                    row_layout.addWidget(_make_pill_label(f"→ {h['underlying']}", "#EFF6FF", "#2563EB", "#BFDBFE"))
                if h.get("is_leveraged"):
                    lf = h.get("leverage_factor")
                    lev_txt = f"{lf:g}x 杠杆" if lf is not None else "杠杆"
                    row_layout.addWidget(_make_pill_label(lev_txt, "#FEF3C7", "#B45309", "#FDE68A"))
                if h.get("cost_anomaly"):
                    anom_pill = _make_pill_label("⚠ 成本偏离核对", "#FEF3C7", "#B45309", "#F59E0B")
                    if h.get("cost_anomaly_reason"):
                        anom_pill.setToolTip(str(h["cost_anomaly_reason"]))
                    row_layout.addWidget(anom_pill)
                if h_news:
                    row_layout.addWidget(_make_pill_label(f"新闻 {len(h_news)}", "#FFFFFF", "#475569", "#CBD5E1"))

            reg_chg = h.get("regular_change_pct")
            ah_chg = h.get("after_hours_change_pct")
            reg_bg, reg_fg = _tone_pill_colors(reg_chg)
            ah_bg, ah_fg = _tone_pill_colors(ah_chg)

            if not is_expanded:
                # 折叠态：紧凑单行摘要条，点击整行可展开
                card.setCursor(Qt.CursorShape.PointingHandCursor)
                card.mousePressEvent = lambda _e, s=sym: self._toggle_symbol_expand(s)
                compact = QHBoxLayout()
                compact.setSpacing(6)
                arrow = QLabel("▸")
                arrow.setStyleSheet("font-size: 11px; color: #64748B; border: none;")
                compact.addWidget(arrow)
                sym_lbl = QLabel(sym)
                sym_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A; border: none;")
                compact.addWidget(sym_lbl)
                _add_holding_pills(compact)
                wt_lbl = QLabel(f"仓位 {w_pct}")
                wt_lbl.setStyleSheet("font-size: 11.5px; color: #64748B; border: none;")
                compact.addWidget(wt_lbl)
                compact.addStretch()
                compact.addWidget(_make_pill_label(f"收 {_fmt_pct(reg_chg)}", reg_bg, reg_fg))
                if ah_chg is not None:
                    compact.addWidget(_make_pill_label(f"后 {_fmt_pct(ah_chg)}", ah_bg, ah_fg))
                pnl_lbl = QLabel(_fmt_money(h.get("day_pnl"), signed=True))
                pnl_lbl.setMinimumWidth(78)
                pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                pnl_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {_tone_color(h.get('day_pnl'))}; border: none;")
                compact.addWidget(pnl_lbl)
                cl.addLayout(compact)
                self.movers_box.addWidget(card)
                continue

            # 展开态：左右分层头部（点击头部可折叠）+ 中性灰元数据 + 右侧放大盈亏与独立涨跌胶囊
            hdr_widget = QWidget()
            hdr_widget.setMinimumWidth(0)
            hdr_widget.setStyleSheet("background: transparent; border: none;")
            hdr_widget.setCursor(Qt.CursorShape.PointingHandCursor)
            hdr_widget.mousePressEvent = lambda _e, s=sym: self._toggle_symbol_expand(s)
            hdr_row = QHBoxLayout(hdr_widget)
            hdr_row.setContentsMargins(0, 0, 0, 0)
            hdr_row.setSpacing(10)

            left_col = QVBoxLayout()
            left_col.setSpacing(3)
            title_row = QHBoxLayout()
            title_row.setSpacing(6)
            arrow_lbl = QLabel("▾")
            arrow_lbl.setStyleSheet("font-size: 11px; color: #64748B; border: none;")
            title_row.addWidget(arrow_lbl)
            sym_lbl = QLabel(sym)
            sym_lbl.setStyleSheet("font-size: 13.5px; font-weight: 700; color: #0F172A; border: none;")
            title_row.addWidget(sym_lbl)
            _add_holding_pills(title_row)
            title_row.addStretch()
            left_col.addLayout(title_row)

            avg_val = h.get("avg_cost")
            is_zero = bool(h.get("is_zero_cost")) or (avg_val is not None and float(avg_val) <= 0)
            avg_txt = f"{_fmt_money(avg_val)}（已零成本/利润仓）" if is_zero else _fmt_money(avg_val)
            be_txt = (
                f" · 回本需 +{float(h['need_breakeven_pct']) * 100:.1f}%{' ⚠' if h.get('cost_anomaly') else ''}"
                if (h.get("need_breakeven_pct") is not None and not is_zero)
                else ""
            )
            ah_price_txt = (
                f" · 盘后 {_fmt_money(h.get('after_hours_price'))}"
                if h.get("after_hours_price") is not None
                else ""
            )
            meta_lbl = _make_wrap_label(
                f"{' / '.join(h.get('channels') or [])} · 仓位 {w_pct} · 摊薄均价 {avg_txt}{be_txt} · 常规收 {_fmt_money(h.get('regular_close'))}{ah_price_txt}",
                "font-size: 11.5px; color: #64748B; border: none;",
            )
            left_col.addWidget(meta_lbl)
            hdr_row.addLayout(left_col, 1)

            right_col = QVBoxLayout()
            right_col.setSpacing(4)
            right_col.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            day_pnl_lbl = QLabel(_fmt_money(h.get("day_pnl"), signed=True))
            day_pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            day_pnl_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {_tone_color(h.get('day_pnl'))}; border: none;")
            right_col.addWidget(day_pnl_lbl)

            pills_r = QHBoxLayout()
            pills_r.setSpacing(4)
            pills_r.addStretch()
            pills_r.addWidget(_make_pill_label(f"常规 {_fmt_pct(reg_chg)}", reg_bg, reg_fg))
            if ah_chg is not None:
                pills_r.addWidget(_make_pill_label(f"盘后 {_fmt_pct(ah_chg)}", ah_bg, ah_fg))
            right_col.addLayout(pills_r)
            hdr_row.addLayout(right_col)

            cl.addWidget(hdr_widget)

            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet("background: #E2E8F0; border: none;")
            cl.addWidget(sep)

            un_h = h.get("unrealized_pnl")
            reason = reasons.get(sym) or f"当前持仓 {h.get('quantity')} 股，{_pnl_prefix(un_h)} {_fmt_money(un_h, signed=True)}。"
            r_lbl = _make_wrap_label(
                f"<b>AI 异动归因：</b>{html.escape(str(reason))}",
                "font-size: 12px; color: #1E293B; border: none;",
            )
            cl.addWidget(r_lbl)
            for n in h_news:
                sent = n.get("sentiment") or sentiments.get(n.get("title"), "中性")
                s_col = "#15803D" if sent == "利好" else ("#B91C1C" if sent == "利空" else "#64748B")
                disp_title = str(n.get("zh_title") or n.get("title") or "")
                news_url = str(n.get("url") or n.get("link") or "")
                summary_txt = str(n.get("summary") or "").strip()
                n_lbl = _make_wrap_label(
                    f"• <b style='color:{s_col};'>[{html.escape(str(sent))}]</b> {_format_news_title_html(disp_title, news_url)} "
                    f"<span style='color:#64748B; font-size:11px;'>({html.escape(str(n.get('publisher') or ''))} · {html.escape(str(n.get('published_bj') or ''))})</span>",
                    "font-size: 11.5px; color: #334155; border: none; padding-left: 4px;",
                )
                if summary_txt:
                    n_lbl.setToolTip(f"摘要：{summary_txt}")
                n_lbl.setOpenExternalLinks(True)
                n_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                cl.addWidget(n_lbl)

            self.movers_box.addWidget(card)

    def _toggle_symbol_expand(self, sym: str):
        if sym in self._expanded_syms:
            self._expanded_syms.remove(sym)
        else:
            self._expanded_syms.add(sym)
        if self._current_report:
            self._render_movers(self._current_report)

    def _toggle_prev_review(self):
        self._prev_review_expanded = not self._prev_review_expanded
        self.prev_review_lbl.setVisible(self._prev_review_expanded)
        cur_txt = self.prev_review_toggle_btn.text()
        if cur_txt.startswith(("▾ ", "▸ ")):
            self.prev_review_toggle_btn.setText(("▾ " if self._prev_review_expanded else "▸ ") + cur_txt[2:])

    def _render_risks_and_checklist(self, rep: dict):
        _clear_layout(self.risks_box)
        _clear_layout(self.checklist_box)

        snap = rep.get("snapshot") or {}
        structured = rep.get("structured") or {}
        risks = structured.get("risks") or []
        checklist = structured.get("checklist") or []
        checked = set(rep.get("checked_ids") or [])

        # 底层资产穿透敞口 Top 3（旧存档无 exposure_breakdown 时自动隐藏）
        exp_bd = snap.get("exposure_breakdown") or {}
        under_top = exp_bd.get("by_underlying") or []
        sec_top = exp_bd.get("by_sector") or []
        if under_top:
            chips = []
            for it in under_top[:3]:
                pct_v = it.get("pct_of_mv")
                pct_s = f"{float(pct_v) * 100:.0f}%" if pct_v is not None else "—"
                chips.append(
                    f"<span style='background:#EFF6FF; color:#1D4ED8; font-weight:600;'>"
                    f"{html.escape(str(it.get('label') or it.get('key') or ''))} {pct_s}</span>"
                )
            self.exposure_lbl.setText("<b style='color:#475569;'>穿透敞口 Top 3：</b>" + " · ".join(chips))
            tip_lines = [
                "底层资产 Top 5：" + "，".join(
                    f"{x.get('label')} {float(x.get('pct_of_mv') or 0) * 100:.1f}%"
                    for x in under_top[:5]
                )
            ]
            if sec_top:
                tip_lines.append(
                    "主题板块 Top 4：" + "，".join(
                        f"{x.get('label')} {float(x.get('pct_of_mv') or 0) * 100:.1f}%"
                        for x in sec_top[:4]
                    )
                )
            self.exposure_frame.setToolTip("\n".join(tip_lines))
            self.exposure_frame.show()
        else:
            self.exposure_lbl.setText("")
            self.exposure_frame.hide()

        # 昨日计划复盘（旧存档或无昨日复盘时自动隐藏）
        prev_rev = structured.get("prev_checklist_review")
        if isinstance(prev_rev, dict) and (prev_rev.get("summary") or prev_rev.get("items")):
            p_date = str(prev_rev.get("prev_date") or "").strip()
            prefix = "▾ " if self._prev_review_expanded else "▸ "
            self.prev_review_toggle_btn.setText(
                f"{prefix}昨日计划复盘" + (f" ({p_date})" if p_date else "")
            )
            lines = []
            if prev_rev.get("summary"):
                lines.append(html.escape(str(prev_rev["summary"])))
            for it in (prev_rev.get("items") or [])[:5]:
                st_val = str(it.get("status") or "未触发")
                st_col = "#15803D" if st_val == "已执行" else ("#B45309" if st_val == "已触发" else ("#DC2626" if st_val == "未执行" else "#64748B"))
                lines.append(
                    f"• <b>{html.escape(str(it.get('ticker') or ''))}</b> "
                    f"<span style='color:{st_col}; font-weight:600;'>[{html.escape(st_val)}]</span> "
                    f"{html.escape(str(it.get('note') or ''))}"
                )
            self.prev_review_lbl.setText("<br>".join(lines))
            self.prev_review_lbl.setVisible(self._prev_review_expanded)
            self.prev_review_frame.show()
        else:
            self.prev_review_lbl.setText("")
            self.prev_review_frame.hide()

        high_cnt = sum(1 for r in risks if r.get("level") == "高危")
        self.risk_tag.setText(f"{high_cnt} 项高危" if high_cnt > 0 else "风险可控")
        self.risk_tag.setStyleSheet(
            f"font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; "
            f"background: {'#FEE2E2' if high_cnt > 0 else '#DCFCE7'}; color: {'#991B1B' if high_cnt > 0 else '#166534'};"
        )

        for r in risks:
            lv = r.get("level") or "提示"
            col = "#DC2626" if lv == "高危" else ("#D97706" if lv == "中危" else "#2563EB")
            lbl = _make_wrap_label(
                f"<b style='color:{col};'>[{html.escape(lv)}] {html.escape(str(r.get('title') or ''))}</b><br>"
                f"{html.escape(str(r.get('detail') or ''))}",
                "padding: 8px; border: 1px solid #E2E8F0; border-radius: 6px; background: #F8FAFC; font-size: 12px;",
            )
            self.risks_box.addWidget(lbl)
        if not risks:
            self.risks_box.addWidget(_make_wrap_label("暂无风险提示。"))

        done_cnt = sum(1 for c in checklist if c.get("id") in checked)
        self.cl_progress_lbl.setText(f"{done_cnt} / {len(checklist)} 已核查")

        for c in checklist:
            cid = str(c.get("id") or "")
            is_checked = cid in checked
            row_card = QFrame()
            row_card.setMinimumWidth(0)
            row_card.setStyleSheet("QFrame { border: 1px solid #E2E8F0; border-radius: 6px; background: #F8FAFC; }")
            hl = QHBoxLayout(row_card)
            hl.setContentsMargins(8, 6, 8, 6)
            hl.setSpacing(8)

            cb = QCheckBox("")
            cb.setChecked(is_checked)
            cb.setFixedWidth(20)
            cb.stateChanged.connect(lambda _state, item_id=cid: self._on_checklist_toggled(item_id))
            hl.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)

            text_html = (
                f"<b style='color:#2563EB;'>[{html.escape(str(c.get('ticker') or ''))} · "
                f"{html.escape(str(c.get('level') or ''))}]</b> "
                f"{html.escape(str(c.get('text') or ''))}"
            )
            lbl = _make_wrap_label(
                text_html,
                f"font-size: 12px; border: none; background: transparent; color: {'#64748B' if is_checked else '#0F172A'};",
            )
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.mousePressEvent = lambda _e, box=cb: box.toggle()
            hl.addWidget(lbl, 1)

            self.checklist_box.addWidget(row_card)
        if not checklist:
            self.checklist_box.addWidget(_make_wrap_label("暂无次日关注事项。"))

    def _on_checklist_toggled(self, item_id: str):
        if not self._current_report:
            return
        cur = list(self._current_report.get("checked_ids") or [])
        if item_id in cur:
            cur = [x for x in cur if x != item_id]
        else:
            cur.append(item_id)
        self._current_report["checked_ids"] = cur
        self.store.update_checklist_state(str(self._current_report["id"]), cur)
        checklist = (self._current_report.get("structured") or {}).get("checklist") or []
        done_cnt = sum(1 for c in checklist if c.get("id") in cur)
        self.cl_progress_lbl.setText(f"{done_cnt} / {len(checklist)} 已核查")

    def _render_trades(self, rep: dict):
        _clear_layout(self.trades_box)
        snap = rep.get("snapshot") or {}
        trades = snap.get("trades") or []
        structured = rep.get("structured") or {}
        self.trades_summary_lbl.setText(f"今日操作总评：{structured.get('trades_summary') or '暂无操作点评。'}")

        if not trades:
            lbl = _make_wrap_label("该交易日无真实成交记录。", "padding: 16px; color: #64748B; font-size: 12px;")
            self.trades_box.addWidget(lbl)
            return

        reviews = structured.get("trade_reviews") or []
        for t in trades:
            side = str(t.get("side") or "")
            is_buy = side.upper() == "BUY" or side == "买入"
            rev = next((r for r in reviews if r.get("symbol") == t.get("symbol")), None)
            vs_close = _fmt_pct(t.get("vs_close_pct"))
            comment_html = f"<br><span style='color:#475569;'>AI 点评：{html.escape(str(rev.get('comment') or ''))}</span>" if rev and rev.get("comment") else ""
            lbl = _make_wrap_label(
                f"<b>[{'买入' if is_buy else '卖出'}] {html.escape(str(t.get('symbol')))}</b> · {html.escape(str(t.get('channel') or ''))} · "
                f"{t.get('quantity')} 股 @ {_fmt_money(t.get('price'))} （收盘 {_fmt_money(t.get('close_price'))}，价差 {vs_close}）"
                f"{comment_html}",
                "padding: 9px 11px; border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; font-size: 12px;",
            )
            self.trades_box.addWidget(lbl)

    def _render_news(self, rep: dict) -> int:
        _clear_layout(self.news_box)
        snap = rep.get("snapshot") or {}
        news_map = snap.get("news") or {}
        structured = rep.get("structured") or {}
        sentiments = structured.get("news_sentiments") or {}
        news_digest = structured.get("news_digest") or []
        news_zh_map = structured.get("news_zh_map") or {}
        count = 0

        if news_digest:
            for d in news_digest:
                count += 1
                sym = str(d.get("symbol") or "组合")
                sent = str(d.get("sentiment") or "中性")
                col = "#16A34A" if sent == "利好" else ("#DC2626" if sent == "利空" else "#64748B")
                zh_title = str(d.get("zh_title") or d.get("original_title") or "")
                news_url = str(d.get("url") or d.get("link") or "")
                sum_txt = str(d.get("summary") or "")
                sum_html = (
                    f"<br><span style='color:#475569; font-size:11.5px;'>核心影响：{html.escape(sum_txt)}</span>"
                    if sum_txt
                    else ""
                )
                lbl = _make_wrap_label(
                    f"<b>[{html.escape(sym)}]</b> <span style='color:{col}; font-weight:600;'>[{html.escape(sent)}]</span> "
                    f"<b>{_format_news_title_html(zh_title, news_url)}</b>{sum_html}<br>"
                    f"<span style='color:#64748B; font-size:11px;'>{html.escape(str(d.get('publisher') or ''))} · {html.escape(str(d.get('published_bj') or ''))}</span>",
                    "padding: 9px 11px; border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; font-size: 12px;",
                )
                lbl.setOpenExternalLinks(True)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                self.news_box.addWidget(lbl)
        else:
            for sym, items in news_map.items():
                for n in items or []:
                    count += 1
                    raw_t = str(n.get("title") or "")
                    zh = news_zh_map.get(raw_t) or {}
                    sent = zh.get("sentiment") or sentiments.get(raw_t, "中性")
                    disp_t = str(zh.get("zh_title") or raw_t)
                    news_url = str(n.get("link") or n.get("url") or "")
                    sum_txt = zh.get("summary") or ""
                    sum_html = (
                        f"<br><span style='color:#475569; font-size:11.5px;'>核心影响：{html.escape(str(sum_txt))}</span>"
                        if sum_txt
                        else ""
                    )
                    col = "#16A34A" if sent == "利好" else ("#DC2626" if sent == "利空" else "#64748B")
                    lbl = _make_wrap_label(
                        f"<b>[{html.escape(str(sym))}]</b> <span style='color:{col}; font-weight:600;'>[{html.escape(sent)}]</span> "
                        f"{_format_news_title_html(disp_t, news_url)}{sum_html}<br>"
                        f"<span style='color:#64748B; font-size:11px;'>{html.escape(str(n.get('publisher') or ''))} · {html.escape(str(n.get('published_bj') or ''))}</span>",
                        "padding: 9px 11px; border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; font-size: 12px;",
                    )
                    lbl.setOpenExternalLinks(True)
                    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                    self.news_box.addWidget(lbl)
        if count == 0:
            empty = _make_wrap_label("近 36 小时未抓取到持仓/交易标的的显著突发新闻。", "padding: 16px; color: #64748B; font-size: 12px;")
            self.news_box.addWidget(empty)
        return count

    def _render_followups(self, rep: dict):
        _clear_layout(self.fq_chips_box)
        _clear_layout(self.fq_history_box)

        followups = (rep.get("structured") or {}).get("followups") or []
        for q in followups[:3]:
            btn = QPushButton(f"▸ {str(q)}")
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(
                "QPushButton { text-align: left; font-size: 11.5px; padding: 5px 10px; border-radius: 8px; "
                "border: 1px solid #CBD5E1; background: #F8FAFC; color: #1E293B; }"
                "QPushButton:hover { border-color: #2563EB; background: #EFF6FF; color: #2563EB; }"
            )
            btn.clicked.connect(lambda _=False, text=str(q): self._start_followup(text))
            self.fq_chips_box.addWidget(btn)

        for item in rep.get("followups_history") or []:
            box = _make_wrap_label(
                f"<b style='color:#2563EB;'>问：{html.escape(str(item.get('question') or ''))}</b> "
                f"<span style='color:#64748B; font-size:11px;'>({html.escape(str(item.get('time_str') or ''))})</span><br>"
                f"{html.escape(str(item.get('answer') or '')).replace(chr(10), '<br>')}",
                "padding: 8px 10px; border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; font-size: 12px;",
            )
            self.fq_history_box.addWidget(box)

    def _start_generate(self):
        if self._worker is not None and self._worker.isRunning():
            return
        qd = self.date_edit.date()
        target_date = date(qd.year(), qd.month(), qd.day())
        session = get_us_market_session(target_date)
        if not session.is_trading_day:
            self.status_lbl.setText(f"⚠️ {target_date.isoformat()} 美股休市（{session.holiday_name}），已跳过生成")
            return
        person_key = str(self.person_combo.currentData() or "个人")
        run_sync = self.sync_check.isChecked()
        model = DEFAULT_AFTER_HOURS_MODEL

        self.generate_btn.setEnabled(False)
        self.generate_btn.setText("正在同步并生成...")
        self.status_lbl.setText(f"正在生成 {target_date.isoformat()}（{person_key}）盘后复盘日报...")

        self._worker = _AfterHoursWorker(
            trade_date=target_date,
            person_key=person_key,
            run_sync=run_sync,
            model=model,
            db_path=self.db_path,
            store=self.store,
        )
        self._worker.finished_report.connect(self._on_generate_ok)
        self._worker.failed.connect(self._on_generate_fail)
        self._worker.start()

    def _on_generate_ok(self, report: dict):
        self.generate_btn.setEnabled(True)
        self.generate_btn.setText("生成 / 重算日报")
        self.status_lbl.setText(f"✅ 已完成 {report.get('trade_date')}（{report.get('person_name')}）盘后日报")
        self.reload(prefer_id=report.get("id"))

    def _on_generate_fail(self, err: str):
        logger.error("桌面端生成盘后复盘日报失败: %s", err)
        self.generate_btn.setEnabled(True)
        self.generate_btn.setText("生成 / 重算日报")
        brief = " ".join(str(err or "").splitlines()).strip()
        if len(brief) > 72:
            brief = brief[:72] + "…"
        self.status_lbl.setText(f"⚠️ 生成失败：{brief}" if brief else "⚠️ 生成失败")
        self.viewer.setHtml(
            f"<div style='color:#B91C1C; padding:12px;'><b>盘后复盘日报生成失败：</b><br>{html.escape(err)}</div>"
        )
        self.tabs.setCurrentIndex(3)

    def _start_followup(self, custom_q: Optional[str] = None):
        if not self._current_report:
            return
        if self._followup_worker is not None and self._followup_worker.isRunning():
            return
        q = (custom_q if custom_q is not None else self.followup_input.text()).strip()
        if not q:
            return
        if custom_q is None:
            self.followup_input.clear()

        self.followup_btn.setEnabled(False)
        self.followup_btn.setText("思考中...")
        self._followup_worker = _FollowupWorker(
            report=self._current_report,
            question=q,
            model=DEFAULT_AFTER_HOURS_MODEL,
            store=self.store,
        )
        self._followup_worker.finished_reply.connect(self._on_followup_ok)
        self._followup_worker.failed.connect(self._on_followup_fail)
        self._followup_worker.start()

    def _on_followup_ok(self, res: dict):
        self.followup_btn.setEnabled(True)
        self.followup_btn.setText("追问 Gemini")
        if self._current_report is not None:
            self._current_report["followups_history"] = res.get("followups_history") or []
            self._render_followups(self._current_report)

    def _on_followup_fail(self, err: str):
        self.followup_btn.setEnabled(True)
        self.followup_btn.setText("追问 Gemini")
        self.status_lbl.setText(f"⚠️ 追问失败：{err[:60]}")

    def _copy_markdown(self):
        if self._current_report and self._current_report.get("markdown"):
            QGuiApplication.clipboard().setText(str(self._current_report["markdown"]))

    def _delete_current_report(self, _checked: bool = False, *, confirm: bool = True):
        if not self._current_report:
            return
        rid = str(self._current_report.get("id") or "")
        if not rid:
            return
        if confirm:
            ans = QMessageBox.question(
                self,
                "删除盘后复盘日报",
                f"确认删除 {self._current_report.get('trade_date')}（{self._current_report.get('person_name')}）的盘后复盘日报存档吗？此操作不可撤销。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        if self.store.delete_report(rid):
            self.status_lbl.setText(f"已删除存档：{rid}")
            self.reload()
