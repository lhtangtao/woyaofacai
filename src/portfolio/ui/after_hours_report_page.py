"""Mac 桌面端「盘后复盘日报 2.0」页面（结构化驾驶舱 + 持仓异动卡片 + 风险雷达 + 可勾选清单 + 追问）。"""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDate, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from portfolio.paths import DATABASE_PATH
from portfolio.services.after_hours_report import (
    AfterHoursReportStore,
    ask_after_hours_followup,
    ensure_structured,
    generate_after_hours_report,
    resolve_default_trade_date,
)
from portfolio.services.ai_service import CodexAIService


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
    """美股盘后复盘日报 2.0 桌面端交互视图。"""

    def __init__(self, parent=None, store: Optional[AfterHoursReportStore] = None, db_path: Path = DATABASE_PATH):
        super().__init__(parent)
        self.store = store or AfterHoursReportStore()
        self.db_path = db_path
        self._reports: list[dict] = []
        self._current_report: Optional[dict] = None
        self._person_filter: Optional[str] = None
        self._mover_filter: str = "all"
        self._expanded_syms: set[str] = set()
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

        top.addWidget(QLabel("交易日："))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        default_d = resolve_default_trade_date()
        self.date_edit.setDate(QDate(default_d.year, default_d.month, default_d.day))
        top.addWidget(self.date_edit)

        top.addWidget(QLabel("范围："))
        self.person_combo = QComboBox()
        self.person_combo.setMinimumWidth(105)
        self.person_combo.addItem("个人", "个人")
        self.person_combo.addItem("全部人员", "ALL")
        top.addWidget(self.person_combo)

        self.sync_check = QCheckBox("生成前同步流水")
        self.sync_check.setChecked(True)
        top.addWidget(self.sync_check)

        top.addStretch()

        self.status_lbl = QLabel("美东每个交易日 20:10 自动同步并生成「个人」盘后日报")
        self.status_lbl.setStyleSheet("font-size: 11.5px; color: #64748B;")
        top.addWidget(self.status_lbl)

        self.refresh_btn = QPushButton("刷新列表")
        self.refresh_btn.clicked.connect(lambda: self.reload())
        top.addWidget(self.refresh_btn)

        self.generate_btn = QPushButton("生成 / 重算日报")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.clicked.connect(self._start_generate)
        top.addWidget(self.generate_btn)

        root.addWidget(top_card)

        # 主体左右分栏
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：历史存档时间轴
        left_card = QFrame()
        left_card.setObjectName("card")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        hdr_l = QHBoxLayout()
        title_l = QLabel("历史复盘存档")
        title_l.setStyleSheet("font-size: 12.5px; font-weight: 700; color: #0F172A;")
        hdr_l.addWidget(title_l)
        hdr_l.addStretch()
        self.count_lbl = QLabel("0 份")
        self.count_lbl.setStyleSheet("font-size: 11.5px; color: #64748B;")
        hdr_l.addWidget(self.count_lbl)
        left_layout.addLayout(hdr_l)

        # 左侧人员筛选药丸
        pill_row = QHBoxLayout()
        pill_row.setSpacing(5)
        self._pill_group = QButtonGroup(self)
        for idx, (lbl, pkey) in enumerate((("全部", None), ("个人", "个人"), ("全部人员", "ALL"))):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setChecked(idx == 0)
            btn.setProperty("person_key", pkey)
            btn.setStyleSheet(
                "QPushButton { font-size: 11.5px; padding: 3px 9px; border-radius: 10px; border: 1px solid #CBD5E1; background: #F8FAFC; color: #475569; }"
                "QPushButton:checked { border-color: #2563EB; background: #EFF6FF; color: #2563EB; font-weight: 600; }"
            )
            btn.clicked.connect(lambda _=False, k=pkey: self._set_person_filter(k))
            self._pill_group.addButton(btn)
            pill_row.addWidget(btn)
        pill_row.addStretch()
        left_layout.addLayout(pill_row)

        self.report_list = QListWidget()
        self.report_list.setSpacing(4)
        self.report_list.setStyleSheet(
            "QListWidget { border: none; background: transparent; }"
            "QListWidget::item { border: 1px solid #E2E8F0; border-radius: 8px; padding: 6px; background: #F8FAFC; }"
            "QListWidget::item:selected { border: 1px solid #2563EB; background: #EFF6FF; color: #0F172A; }"
        )
        self.report_list.currentRowChanged.connect(self._on_report_selected)
        left_layout.addWidget(self.report_list, 1)

        # 右侧：可滚动工作台
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)

        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(2, 0, 4, 4)
        right_layout.setSpacing(10)

        # 1. Hero 驾驶舱卡片
        self.hero_card = QFrame()
        self.hero_card.setObjectName("card")
        hero_l = QVBoxLayout(self.hero_card)
        hero_l.setContentsMargins(14, 14, 14, 14)
        hero_l.setSpacing(10)

        hero_top = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(3)
        title_badges = QHBoxLayout()
        title_badges.setSpacing(8)
        self.detail_title = QLabel("请选择左侧日报或点击右上角立即生成")
        self.detail_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #0F172A;")
        title_badges.addWidget(self.detail_title)
        self.regime_badge = QLabel("")
        self.regime_badge.hide()
        title_badges.addWidget(self.regime_badge)
        self.sync_badge = QLabel("")
        self.sync_badge.hide()
        title_badges.addWidget(self.sync_badge)
        title_badges.addStretch()
        title_col.addLayout(title_badges)

        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet("font-size: 11.5px; color: #64748B;")
        title_col.addWidget(self.detail_meta)
        hero_top.addLayout(title_col, 1)

        self.copy_btn = QPushButton("复制 Markdown")
        self.copy_btn.clicked.connect(self._copy_markdown)
        hero_top.addWidget(self.copy_btn)
        hero_l.addLayout(hero_top)

        # TL;DR 横幅
        self.tldr_frame = QFrame()
        self.tldr_frame.setStyleSheet("QFrame { border: 1px solid #BFDBFE; border-radius: 8px; background: #EFF6FF; }")
        tldr_row = QHBoxLayout(self.tldr_frame)
        tldr_row.setContentsMargins(10, 8, 10, 8)
        tldr_row.setSpacing(8)
        tldr_tag = QLabel("核心定性")
        tldr_tag.setStyleSheet("font-size: 11px; font-weight: 700; color: white; background: #2563EB; border-radius: 4px; padding: 2px 6px;")
        tldr_tag.setAlignment(Qt.AlignmentFlag.AlignTop)
        tldr_row.addWidget(tldr_tag, 0, Qt.AlignmentFlag.AlignTop)
        self.tldr_lbl = QLabel("")
        self.tldr_lbl.setWordWrap(True)
        self.tldr_lbl.setStyleSheet("font-size: 12.5px; color: #0F172A; border: none; background: transparent;")
        tldr_row.addWidget(self.tldr_lbl, 1)
        self.tldr_frame.hide()
        hero_l.addWidget(self.tldr_frame)

        # 4 张 KPI 卡片
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(8)
        self.kpi_pnl_val, self.kpi_pnl_sub, self.kpi_alpha_lbl, kpi_card_1 = self._make_kpi_card("当日预估盈亏")
        self.kpi_mv_val, self.kpi_un_sub, _, kpi_card_2 = self._make_kpi_card("总持仓市值 / 累计浮盈")
        self.kpi_bench_val, self.kpi_bench_sub, _, kpi_card_3 = self._make_kpi_card("大盘基准 (常规/盘后)")
        self.kpi_lev_val, self.kpi_lev_sub, self.kpi_trades_lbl, kpi_card_4 = self._make_kpi_card("杠杆仓位暴露")
        self.lev_bar = QProgressBar()
        self.lev_bar.setRange(0, 100)
        self.lev_bar.setTextVisible(False)
        self.lev_bar.setFixedHeight(6)
        kpi_card_4.layout().addWidget(self.lev_bar)

        kpi_grid.addWidget(kpi_card_1, 0, 0)
        kpi_grid.addWidget(kpi_card_2, 0, 1)
        kpi_grid.addWidget(kpi_card_3, 0, 2)
        kpi_grid.addWidget(kpi_card_4, 0, 3)
        hero_l.addLayout(kpi_grid)

        right_layout.addWidget(self.hero_card)

        # 2. 多视角 Tabs
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # Tab 1: 结构化复盘总览
        tab_overview = QWidget()
        ov_l = QVBoxLayout(tab_overview)
        ov_l.setContentsMargins(8, 10, 8, 10)
        ov_l.setSpacing(12)

        # 持仓异动过滤条
        mf_row = QHBoxLayout()
        mf_title = QLabel("重点持仓异动与归因")
        mf_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A;")
        mf_row.addWidget(mf_title)
        mf_row.addStretch()
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
            b.setStyleSheet(
                "QPushButton { font-size: 11.5px; padding: 3px 9px; border-radius: 6px; border: 1px solid #CBD5E1; background: #F8FAFC; color: #475569; }"
                "QPushButton:checked { border-color: #2563EB; background: #EFF6FF; color: #2563EB; font-weight: 600; }"
            )
            b.clicked.connect(lambda _=False, k=mkey: self._set_mover_filter(k))
            self._mf_group.addButton(b)
            mf_row.addWidget(b)
        ov_l.addLayout(mf_row)

        self.movers_box = QVBoxLayout()
        self.movers_box.setSpacing(8)
        ov_l.addLayout(self.movers_box)

        # 风险雷达 & 可勾选清单双栏
        rc_row = QHBoxLayout()
        rc_row.setSpacing(10)

        risk_card = QFrame()
        risk_card.setObjectName("card")
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
        self.risks_box = QVBoxLayout()
        self.risks_box.setSpacing(6)
        risk_outer.addLayout(self.risks_box)
        risk_outer.addStretch()

        cl_card = QFrame()
        cl_card.setObjectName("card")
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
        self.checklist_box = QVBoxLayout()
        self.checklist_box.setSpacing(6)
        cl_outer.addLayout(self.checklist_box)
        cl_outer.addStretch()

        rc_row.addWidget(risk_card, 1)
        rc_row.addWidget(cl_card, 1)
        ov_l.addLayout(rc_row)

        # Tab 2: 今日操作复盘
        tab_trades = QWidget()
        tr_l = QVBoxLayout(tab_trades)
        tr_l.setContentsMargins(8, 10, 8, 10)
        tr_l.setSpacing(8)
        self.trades_summary_lbl = QLabel("")
        self.trades_summary_lbl.setWordWrap(True)
        self.trades_summary_lbl.setStyleSheet("padding: 8px 10px; border-radius: 8px; background: #F8FAFC; border: 1px solid #E2E8F0; font-size: 12.5px;")
        tr_l.addWidget(self.trades_summary_lbl)
        self.trades_box = QVBoxLayout()
        self.trades_box.setSpacing(8)
        tr_l.addLayout(self.trades_box)
        tr_l.addStretch()

        # Tab 3: 突发新闻情报
        tab_news = QWidget()
        nw_l = QVBoxLayout(tab_news)
        nw_l.setContentsMargins(8, 10, 8, 10)
        nw_l.setSpacing(8)
        self.news_box = QVBoxLayout()
        self.news_box.setSpacing(8)
        nw_l.addLayout(self.news_box)
        nw_l.addStretch()

        # Tab 4: 完整 Markdown 长文
        tab_md = QWidget()
        md_l = QVBoxLayout(tab_md)
        md_l.setContentsMargins(8, 10, 8, 10)
        self.viewer = QTextBrowser()
        self.viewer.setMinimumHeight(420)
        self.viewer.setOpenExternalLinks(False)
        self.viewer.setStyleSheet("QTextBrowser { border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px; background: #FFFFFF; font-size: 13px; }")
        md_l.addWidget(self.viewer)

        self.tabs.addTab(tab_overview, "结构化复盘总览")
        self.tabs.addTab(tab_trades, "今日操作复盘 (0)")
        self.tabs.addTab(tab_news, "突发新闻情报 (0)")
        self.tabs.addTab(tab_md, "完整长文 (Markdown)")
        right_layout.addWidget(self.tabs)

        # 3. 底部带上下文追问条
        fq_card = QFrame()
        fq_card.setObjectName("card")
        fq_l = QVBoxLayout(fq_card)
        fq_l.setContentsMargins(12, 10, 12, 10)
        fq_l.setSpacing(8)

        fq_hdr = QLabel("基于这份盘后日报继续追问 Gemini")
        fq_hdr.setStyleSheet("font-size: 12px; font-weight: 700; color: #475569;")
        fq_l.addWidget(fq_hdr)

        self.fq_chips_box = QHBoxLayout()
        self.fq_chips_box.setSpacing(6)
        fq_l.addLayout(self.fq_chips_box)

        fq_input_row = QHBoxLayout()
        self.followup_input = QLineEdit()
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

        right_scroll.setWidget(right_container)

        splitter.addWidget(left_card)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 6)
        root.addWidget(splitter, 1)

    @staticmethod
    def _make_kpi_card(title: str):
        card = QFrame()
        card.setStyleSheet("QFrame { border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; }")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)
        top = QHBoxLayout()
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 11px; color: #64748B; border: none;")
        top.addWidget(t_lbl)
        top.addStretch()
        right_lbl = QLabel("")
        right_lbl.setStyleSheet("font-size: 11px; font-weight: 600; border: none;")
        top.addWidget(right_lbl)
        lay.addLayout(top)
        val_lbl = QLabel("—")
        val_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #0F172A; border: none;")
        lay.addWidget(val_lbl)
        sub_lbl = QLabel("")
        sub_lbl.setStyleSheet("font-size: 11px; color: #64748B; border: none;")
        sub_lbl.setWordWrap(True)
        lay.addWidget(sub_lbl)
        return val_lbl, sub_lbl, right_lbl, card

    def populate_persons(self, persons: list[dict] | tuple[dict, ...]):
        current = self.person_combo.currentData() or "个人"
        self.person_combo.blockSignals(True)
        self.person_combo.clear()
        self.person_combo.addItem("个人", "个人")
        self.person_combo.addItem("全部人员", "ALL")
        for p in persons or ():
            name = str(p.get("name") or "").strip()
            if name and name != "个人":
                self.person_combo.addItem(name, name)
        idx = self.person_combo.findData(current)
        self.person_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.person_combo.blockSignals(False)

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
            self.detail_title.setText("暂无盘后复盘日报")
            self.detail_meta.setText("点击右上角「生成 / 重算日报」可立即生成")
            self.tldr_frame.hide()
            self.viewer.setHtml("<p style='color:#64748B;'>暂无历史盘后复盘日报。</p>")

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
        self._expanded_syms = {h.get("symbol") for h in holdings[:2] if h.get("symbol")}
        self._render_report(full)

    def _render_report(self, rep: dict):
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
        self.kpi_pnl_val.setStyleSheet(f"font-size: 16px; font-weight: 700; border: none; color: {_tone_color(summary.get('day_pnl'))};")
        self.kpi_pnl_sub.setText(f"日收益率 {_fmt_pct(summary.get('day_pnl_pct'))}")
        alpha = summary.get("alpha_vs_qqq")
        if alpha is not None:
            self.kpi_alpha_lbl.setText(f"vs QQQ {_fmt_pct(alpha)}")
            self.kpi_alpha_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; border: none; color: {_tone_color(alpha)};")
        else:
            self.kpi_alpha_lbl.setText("")

        self.kpi_mv_val.setText(_fmt_money(summary.get("total_market_value")))
        self.kpi_un_sub.setText(
            f"浮盈 {_fmt_money(summary.get('total_unrealized_pnl'), signed=True)} ({_fmt_pct(summary.get('unrealized_pct'))})"
        )
        self.kpi_un_sub.setStyleSheet(f"font-size: 11px; border: none; color: {_tone_color(summary.get('total_unrealized_pnl'))};")

        b_lines = []
        for k in ("SPY", "QQQ"):
            b = benchmarks.get(k)
            if b:
                b_lines.append(f"{k}: 收 {_fmt_pct(b.get('regular_change_pct'))} | 盘后 {_fmt_pct(b.get('after_hours_change_pct'))}")
        self.kpi_bench_val.setText(" / ".join(k for k in ("SPY", "QQQ") if k in benchmarks) or "—")
        self.kpi_bench_val.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A; border: none;")
        self.kpi_bench_sub.setText("\n".join(b_lines) if b_lines else "暂无大盘基准行情")

        lev_pct = float(summary.get("leverage_pct") or 0) * 100.0
        self.kpi_lev_val.setText(f"{lev_pct:.1f}%")
        self.kpi_lev_sub.setText("高杠杆警戒" if lev_pct >= 50 else ("中高暴露" if lev_pct >= 35 else "稳健可控"))
        bar_col = "#DC2626" if lev_pct >= 50 else ("#D97706" if lev_pct >= 35 else "#16A34A")
        self.kpi_lev_sub.setStyleSheet(f"font-size: 11px; font-weight: 600; border: none; color: {bar_col};")
        self.kpi_trades_lbl.setText(f"{summary.get('trade_count', 0)} 笔成交")
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

    def _get_holding_news(self, snap: dict, h: dict) -> list[dict]:
        news_map = snap.get("news") or {}
        own = news_map.get(h.get("symbol") or "") or []
        under = news_map.get(h.get("underlying") or "") or [] if h.get("underlying") else []
        seen = set()
        merged = []
        for n in [*own, *under]:
            t = str(n.get("title") or "")
            if t and t not in seen:
                seen.add(t)
                merged.append(n)
        return merged

    def _render_movers(self, rep: dict):
        _clear_layout(self.movers_box)
        snap = rep.get("snapshot") or {}
        holdings = snap.get("holdings") or []
        structured = rep.get("structured") or {}
        reasons = structured.get("mover_reasons") or {}
        sentiments = structured.get("news_sentiments") or {}

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
            if self._mover_filter == "news" and not self._get_holding_news(snap, h):
                continue
            filtered.append(h)

        if not filtered:
            empty = QLabel("当前筛选条件下无匹配持仓标的")
            empty.setStyleSheet("padding: 14px; color: #64748B; font-size: 12px;")
            self.movers_box.addWidget(empty)
            return

        for h in filtered:
            sym = str(h.get("symbol") or "")
            h_news = self._get_holding_news(snap, h)
            expanded = sym in self._expanded_syms

            card = QFrame()
            card.setStyleSheet("QFrame { border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; }")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(6)

            hdr = QHBoxLayout()
            left_info = QVBoxLayout()
            left_info.setSpacing(2)
            tags = f"  [→ {h['underlying']}]" if h.get("underlying") else ""
            lev_tag = "  [杠杆]" if h.get("is_leveraged") else ""
            news_tag = f"  [新闻 {len(h_news)}]" if h_news else ""
            top_lbl = QLabel(f"{sym}{tags}{lev_tag}{news_tag}")
            top_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A; border: none;")
            left_info.addWidget(top_lbl)

            w_pct = f"{float(h['weight_pct']) * 100:.1f}%" if h.get("weight_pct") is not None else "—"
            be_txt = f" · 回本需 +{float(h['need_breakeven_pct']) * 100:.1f}%" if h.get("need_breakeven_pct") is not None else ""
            sub_lbl = QLabel(f"{' / '.join(h.get('channels') or [])} · 仓位 {w_pct} · 均价 {_fmt_money(h.get('avg_cost'))}{be_txt}")
            sub_lbl.setStyleSheet("font-size: 11px; color: #64748B; border: none;")
            left_info.addWidget(sub_lbl)
            hdr.addLayout(left_info, 1)

            q_lbl = QLabel(
                f"收 {_fmt_money(h.get('regular_close'))} ({_fmt_pct(h.get('regular_change_pct'))})  |  "
                f"盘后 {_fmt_money(h.get('after_hours_price'))} ({_fmt_pct(h.get('after_hours_change_pct'))})  |  "
                f"当日 {_fmt_money(h.get('day_pnl'), signed=True)}"
            )
            q_lbl.setStyleSheet(f"font-size: 11.5px; font-weight: 600; color: {_tone_color(h.get('day_pnl'))}; border: none;")
            hdr.addWidget(q_lbl)

            toggle_btn = QPushButton("收起" if expanded else "详情")
            toggle_btn.setStyleSheet("font-size: 11px; padding: 2px 8px;")
            toggle_btn.clicked.connect(lambda _=False, s=sym: self._toggle_symbol_expand(s))
            hdr.addWidget(toggle_btn)
            cl.addLayout(hdr)

            if expanded:
                reason = reasons.get(sym) or f"当前持仓 {h.get('quantity')} 股，浮盈 {_fmt_money(h.get('unrealized_pnl'), signed=True)}。"
                r_lbl = QLabel(f"AI 异动归因：{reason}")
                r_lbl.setWordWrap(True)
                r_lbl.setStyleSheet("font-size: 12px; color: #1E293B; border: none; padding-top: 4px;")
                cl.addWidget(r_lbl)
                for n in h_news:
                    sent = sentiments.get(n.get("title"), "中性")
                    n_lbl = QLabel(f"• [{sent}] {n.get('title')} ({n.get('publisher')} · {n.get('published_bj')})")
                    n_lbl.setWordWrap(True)
                    n_lbl.setStyleSheet("font-size: 11.5px; color: #475569; border: none; padding-left: 6px;")
                    cl.addWidget(n_lbl)

            self.movers_box.addWidget(card)

    def _toggle_symbol_expand(self, sym: str):
        if sym in self._expanded_syms:
            self._expanded_syms.remove(sym)
        else:
            self._expanded_syms.add(sym)
        if self._current_report:
            self._render_movers(self._current_report)

    def _render_risks_and_checklist(self, rep: dict):
        _clear_layout(self.risks_box)
        _clear_layout(self.checklist_box)

        structured = rep.get("structured") or {}
        risks = structured.get("risks") or []
        checklist = structured.get("checklist") or []
        checked = set(rep.get("checked_ids") or [])

        high_cnt = sum(1 for r in risks if r.get("level") == "高危")
        self.risk_tag.setText(f"{high_cnt} 项高危" if high_cnt > 0 else "风险可控")
        self.risk_tag.setStyleSheet(
            f"font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; "
            f"background: {'#FEE2E2' if high_cnt > 0 else '#DCFCE7'}; color: {'#991B1B' if high_cnt > 0 else '#166534'};"
        )

        for r in risks:
            lv = r.get("level") or "提示"
            col = "#DC2626" if lv == "高危" else ("#D97706" if lv == "中危" else "#2563EB")
            lbl = QLabel(f"<b style='color:{col};'>[{html.escape(lv)}] {html.escape(str(r.get('title') or ''))}</b><br>{html.escape(str(r.get('detail') or ''))}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("padding: 8px; border: 1px solid #E2E8F0; border-radius: 6px; background: #F8FAFC; font-size: 12px;")
            self.risks_box.addWidget(lbl)
        if not risks:
            self.risks_box.addWidget(QLabel("暂无风险提示。"))

        done_cnt = sum(1 for c in checklist if c.get("id") in checked)
        self.cl_progress_lbl.setText(f"{done_cnt} / {len(checklist)} 已核查")

        for c in checklist:
            cid = str(c.get("id") or "")
            cb = QCheckBox(f"[{c.get('ticker')} · {c.get('level')}] {c.get('text')}")
            cb.setChecked(cid in checked)
            cb.setStyleSheet("QCheckBox { font-size: 12px; padding: 4px; }")
            cb.stateChanged.connect(lambda _state, item_id=cid: self._on_checklist_toggled(item_id))
            self.checklist_box.addWidget(cb)
        if not checklist:
            self.checklist_box.addWidget(QLabel("暂无次日关注事项。"))

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
            lbl = QLabel("该交易日无真实成交记录。")
            lbl.setStyleSheet("padding: 16px; color: #64748B; font-size: 12px;")
            self.trades_box.addWidget(lbl)
            return

        reviews = structured.get("trade_reviews") or []
        for t in trades:
            side = str(t.get("side") or "")
            is_buy = side.upper() == "BUY" or side == "买入"
            rev = next((r for r in reviews if r.get("symbol") == t.get("symbol")), None)
            vs_close = _fmt_pct(t.get("vs_close_pct"))
            comment_html = f"<br><span style='color:#475569;'>AI 点评：{html.escape(str(rev.get('comment') or ''))}</span>" if rev and rev.get("comment") else ""
            lbl = QLabel(
                f"<b>[{'买入' if is_buy else '卖出'}] {html.escape(str(t.get('symbol')))}</b> · {html.escape(str(t.get('channel') or ''))} · "
                f"{t.get('quantity')} 股 @ {_fmt_money(t.get('price'))} （收盘 {_fmt_money(t.get('close_price'))}，价差 {vs_close}）"
                f"{comment_html}"
            )
            lbl.setWordWrap(True)
            lbl.setStyleSheet("padding: 9px 11px; border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; font-size: 12px;")
            self.trades_box.addWidget(lbl)

    def _render_news(self, rep: dict) -> int:
        _clear_layout(self.news_box)
        snap = rep.get("snapshot") or {}
        news_map = snap.get("news") or {}
        sentiments = (rep.get("structured") or {}).get("news_sentiments") or {}
        count = 0
        for sym, items in news_map.items():
            for n in items or []:
                count += 1
                sent = sentiments.get(n.get("title"), "中性")
                col = "#16A34A" if sent == "利好" else ("#DC2626" if sent == "利空" else "#64748B")
                lbl = QLabel(
                    f"<b>[{html.escape(str(sym))}]</b> <span style='color:{col}; font-weight:600;'>[{html.escape(sent)}]</span> "
                    f"{html.escape(str(n.get('title') or ''))}<br>"
                    f"<span style='color:#64748B; font-size:11px;'>{html.escape(str(n.get('publisher') or ''))} · {html.escape(str(n.get('published_bj') or ''))}</span>"
                )
                lbl.setWordWrap(True)
                lbl.setStyleSheet("padding: 9px 11px; border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; font-size: 12px;")
                self.news_box.addWidget(lbl)
        if count == 0:
            empty = QLabel("近 36 小时未抓取到持仓/交易标的的显著突发新闻。")
            empty.setStyleSheet("padding: 16px; color: #64748B; font-size: 12px;")
            self.news_box.addWidget(empty)
        return count

    def _render_followups(self, rep: dict):
        _clear_layout(self.fq_chips_box)
        _clear_layout(self.fq_history_box)

        followups = (rep.get("structured") or {}).get("followups") or []
        for q in followups[:3]:
            btn = QPushButton(str(q))
            btn.setStyleSheet("QPushButton { font-size: 11px; padding: 3px 9px; border-radius: 10px; border: 1px solid #CBD5E1; background: #F8FAFC; color: #1E293B; }")
            btn.clicked.connect(lambda _=False, text=str(q): self._start_followup(text))
            self.fq_chips_box.addWidget(btn)
        self.fq_chips_box.addStretch()

        for item in rep.get("followups_history") or []:
            box = QLabel(
                f"<b style='color:#2563EB;'>问：{html.escape(str(item.get('question') or ''))}</b> "
                f"<span style='color:#64748B; font-size:11px;'>({html.escape(str(item.get('time_str') or ''))})</span><br>"
                f"{html.escape(str(item.get('answer') or '')).replace(chr(10), '<br>')}"
            )
            box.setWordWrap(True)
            box.setStyleSheet("padding: 8px 10px; border: 1px solid #E2E8F0; border-radius: 8px; background: #F8FAFC; font-size: 12px;")
            self.fq_history_box.addWidget(box)

    def _start_generate(self):
        if self._worker is not None and self._worker.isRunning():
            return
        qd = self.date_edit.date()
        target_date = date(qd.year(), qd.month(), qd.day())
        person_key = str(self.person_combo.currentData() or "个人")
        run_sync = self.sync_check.isChecked()
        model = CodexAIService.get_agy_model()

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
        self.generate_btn.setEnabled(True)
        self.generate_btn.setText("生成 / 重算日报")
        self.status_lbl.setText("⚠️ 生成失败")
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
            model=CodexAIService.get_agy_model(),
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
