"""High-frequency PnL trend across month (30-min sampling) and year (2-hour sampling)."""
import calendar
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path
from portfolio.paths import DATABASE_PATH
import sqlite3
from zoneinfo import ZoneInfo

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.live_quotes import NY, UTC
from portfolio.services.charts_service import Chart, Series
from portfolio.ui.charts_page import ChartCanvas, chart_font
from portfolio.ui.filters import PersonFilter
from portfolio.ui.theme import tokens


def to_bj_datetime(iso_str: str) -> datetime | None:
    """Safely convert ISO timestamp string to Asia/Shanghai datetime."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return None


def get_period_opening_pnl(analytics, period, is_year=False, channels=None, ticker=None) -> Decimal:
    """
    计算所选周期起点之前的历史累计总盈亏：
    月度时：从系统最开始到上个月底的累计盈亏
    年度时：从系统最开始到上年底的累计盈亏
    支持渠道与股票过滤。
    """
    if not analytics or "每月持仓收益" not in analytics.reports:
        return Decimal(0)

    report = analytics.reports["每月持仓收益"]
    cutoff = f"{period}-01" if is_year else period

    total = Decimal(0)
    for r in report.rows:
        v = r.values
        m_str = str(v[0])
        if m_str < cutoff:
            if channels and v[1] not in channels:
                continue
            if ticker and ticker.upper() not in str(v[3]).upper():
                continue
            pnl = v[20]  # 本月收益($)
            if pnl is not None:
                total += Decimal(str(pnl))
    return total


def calculate_realtime_metrics(
    series_points,
    *,
    is_year=False,
    period_str="",
    state=None,
    base_pnl: Decimal | None = None,
):
    """
    计算月度/年度走势详情的核心指标：
    1. 月初/年初累计盈亏: 从最开始到上个月底时的累计盈亏 (若传入 base_pnl 则使用 base_pnl)
    2. 当前最新累计盈亏: 从一开始到当前最新的累计盈亏数据 (基于 base_pnl 叠加当期变动)
    3. 本月/本年变化: 最新累计盈亏 - 月初累计盈亏 (delta_val)
    4. 月内/年内波动区间: range_val = max_val - min_val
    5. 最高点与最低点及发生时间
    6. 最大回撤 (时序算法: max_dd = trough - prior_peak)
    7. 数据覆盖状态
    """
    valid = []
    for stamp_str, v in series_points:
        if v is not None:
            dt_bj = to_bj_datetime(stamp_str)
            if dt_bj:
                valid.append((dt_bj, Decimal(str(v)), stamp_str))

    # 区间文字
    if is_year:
        year_str = period_str or str(date.today().year)
        period_range_text = f"{year_str}-01-01 00:00 ～ {year_str}-12-31 23:59"
    else:
        month_str = period_str or date.today().strftime("%Y-%m")
        try:
            y, m = map(int, month_str.split("-"))
            _, last_d = calendar.monthrange(y, m)
            period_range_text = f"{month_str}-01 00:00 ～ {month_str}-{last_d:02d} 23:59"
        except Exception:
            period_range_text = f"{month_str}-01 00:00 ～ {month_str}-31 23:59"

    if not valid:
        return {
            "valid_count": 0,
            "period_range_text": period_range_text,
            "start_val": base_pnl,
            "start_time_text": f"上{'年' if is_year else '月'}底累计 · {period_str}起点" if base_pnl is not None else "无有效历史数据",
            "latest_val": None,
            "latest_time_text": "无有效历史数据",
            "delta_val": None,
            "delta_sub_text": f"较{'年初' if is_year else '月初'}变化（最新－{'年初' if is_year else '月初'}）",
            "range_val": None,
            "range_sub_text": "—",
            "max_val": None,
            "max_time_text": "—",
            "min_val": None,
            "min_time_text": "—",
            "drawdown_val": None,
            "drawdown_sub_text": "—",
            "status_text": "无有效数据",
            "status_level": "neutral",
        }

    # 1. 月初/年初累计盈亏
    first_dt_bj, first_val, _ = valid[0]
    if base_pnl is not None:
        start_val = base_pnl
        valid_totals = [(dt_bj, base_pnl + v, s) for dt_bj, v, s in valid]
        start_time_text = f"上{'年' if is_year else '月'}底累计 · {period_str}起点"
    else:
        start_val = first_val
        valid_totals = valid
        start_time_text = f"{'年初' if is_year else '本月'}起点 · {first_dt_bj.strftime('%Y-%m-%d')}"

    # 2. 当前最新累计盈亏
    latest_dt_bj, latest_val, _ = valid_totals[-1]
    latest_time_text = f"最近有效点 · {latest_dt_bj.strftime('%m-%d %H:%M')} 北京时间"

    # 3. 本月/本年变化 = 当前最新 - 月初
    delta_val = latest_val - start_val
    delta_sub_text = f"较{'年初' if is_year else '月初'}变化（最新－{'年初' if is_year else '月初'}）"

    # 4. 极值与波动区间（基于全周期累计点位）
    max_tuple = max(valid_totals, key=lambda x: x[1])
    max_val = max_tuple[1]
    max_time_text = max_tuple[0].strftime("%m-%d %H:%M")

    min_tuple = min(valid_totals, key=lambda x: x[1])
    min_val = min_tuple[1]
    min_time_text = min_tuple[0].strftime("%m-%d %H:%M")

    range_val = max_val - min_val
    range_sub_text = f"{max_val:+,.2f} ～ {min_val:+,.2f}"

    # 5. 最大回撤（严格按时间顺序计算：trough - prior_peak）
    max_dd = Decimal(0)
    best_peak = valid_totals[0][1]
    best_trough = valid_totals[0][1]
    running_peak = valid_totals[0][1]

    for dt_bj, v, _ in valid_totals:
        if v > running_peak:
            running_peak = v
        else:
            dd = v - running_peak  # dd <= 0
            if dd < max_dd:
                max_dd = dd
                best_peak = running_peak
                best_trough = v

    if max_dd < 0:
        drawdown_val = max_dd
        drawdown_sub_text = f"(从 {best_peak:+,.2f} 至 {best_trough:+,.2f})"
    else:
        drawdown_val = Decimal(0)
        drawdown_sub_text = "(无回撤)"

    # 6. 数据覆盖状态
    st = state or {}
    if st.get("baseline_state") == "REPORT_PENDING":
        status_text = "待生成 / 计算中"
        status_level = "warning"
    elif (
        st.get("invalid_partitions")
        or st.get("not_generated_days")
        or st.get("baseline_break_days")
        or st.get("missing")
    ):
        status_text = "覆盖不完整 / 存在缺失"
        status_level = "warning"
    elif len(valid) >= 5:
        status_text = "正常 / 覆盖完整"
        status_level = "success"
    else:
        status_text = "样本较少"
        status_level = "neutral"

    return {
        "valid_count": len(valid),
        "period_range_text": period_range_text,
        "start_val": start_val,
        "start_time_text": start_time_text,
        "latest_val": latest_val,
        "latest_time_text": latest_time_text,
        "delta_val": delta_val,
        "delta_sub_text": delta_sub_text,
        "range_val": range_val,
        "range_sub_text": range_sub_text,
        "max_val": max_val,
        "max_time_text": max_time_text,
        "min_val": min_val,
        "min_time_text": min_time_text,
        "drawdown_val": drawdown_val,
        "drawdown_sub_text": drawdown_sub_text,
        "status_text": status_text,
        "status_level": status_level,
    }


class ChannelFilter(QToolButton):
    """Channel multi-select filter styled identically to AccountFilter."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setText("全部渠道")
        self.setMinimumWidth(110)
        self.setToolTip("筛选券商渠道（点击单选或多选；选择全部渠道恢复全量）")
        self.actions_by_channel = {}
        self._menu = None

    def populate(self, channels):
        curr_selected = self.selected_channels()
        self._menu = QMenu(self)
        reset = self._menu.addAction("全部渠道")
        reset.triggered.connect(self.reset)
        self._menu.addSeparator()
        self.actions_by_channel = {}

        display_names = {
            "FIRSTRADE": "第一证券",
            "第一证券": "第一证券",
            "BBAE": "BBAE",
            "LONGBRIDGE": "长桥",
            "长桥": "长桥",
            "SCHWAB": "嘉信",
            "嘉信": "嘉信",
        }
        order_map = {"第一证券": 0, "BBAE": 1, "长桥": 2, "嘉信": 3}
        sorted_channels = sorted(channels, key=lambda c: order_map.get(display_names.get(c, c), 99))
        for ch in sorted_channels:
            name = display_names.get(ch, ch)
            act = QAction(name, self)
            act.setCheckable(True)
            if curr_selected and ch in curr_selected:
                act.setChecked(True)
            act.toggled.connect(self._on_action_toggled)
            self._menu.addAction(act)
            self.actions_by_channel[ch] = act
        self.setMenu(self._menu)
        self._sync_text()

    def reset(self):
        for act in self.actions_by_channel.values():
            act.blockSignals(True)
            act.setChecked(False)
            act.blockSignals(False)
        self._sync_text()
        self.changed.emit()

    def _on_action_toggled(self):
        self._sync_text()
        self.changed.emit()

    def _sync_text(self):
        selected = [ch for ch, act in self.actions_by_channel.items() if act.isChecked()]
        if not selected:
            self.setText("全部渠道")
        elif len(selected) == 1:
            display_names = {
                "FIRSTRADE": "第一证券",
                "第一证券": "第一证券",
                "BBAE": "BBAE",
                "LONGBRIDGE": "长桥",
                "长桥": "长桥",
                "SCHWAB": "嘉信",
                "嘉信": "嘉信",
            }
            self.setText(display_names.get(selected[0], selected[0]))
        else:
            self.setText(f"已选 {len(selected)} 个渠道")

    def selected_channels(self):
        selected = {ch for ch, act in self.actions_by_channel.items() if act.isChecked()}
        return selected or None


class StockFilter(QComboBox):
    """Stock ticker filter with completer and clean dropdown selection."""
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(125)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().setPlaceholderText("全部股票")
        self.lineEdit().setClearButtonEnabled(True)
        self.setToolTip("输入代码自动联想，或直接从下拉列表选择特定股票；清空恢复全部股票。")

        self.completer = QCompleter(self.model(), self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.popup().setMinimumWidth(160)
        self.setCompleter(self.completer)

        self.currentIndexChanged.connect(self._on_index_changed)
        self.lineEdit().textEdited.connect(self._on_text_edited)
        self.lineEdit().editingFinished.connect(self._on_editing_finished)

    def populate(self, tickers):
        curr_ticker = self.selected_ticker()
        self.blockSignals(True)
        self.clear()
        self.addItem("全部股票", None)
        for t in sorted(tickers):
            self.addItem(t, t)
        if curr_ticker:
            idx = self.findData(curr_ticker)
            if idx >= 0:
                self.setCurrentIndex(idx)
            else:
                self.setCurrentIndex(0)
        else:
            self.setCurrentIndex(0)
        self.blockSignals(False)

    def selected_ticker(self):
        data = self.currentData()
        if data:
            return str(data).strip().upper()
        text = self.lineEdit().text().strip().upper()
        if text and text != "全部股票":
            for i in range(1, self.count()):
                if self.itemText(i).upper() == text:
                    return self.itemText(i)
            return text
        return None

    def _on_index_changed(self, index):
        self.changed.emit(self.selected_ticker() or "")

    def _on_text_edited(self, text):
        if not text.strip():
            self.blockSignals(True)
            self.setCurrentIndex(0)
            self.blockSignals(False)
            self.changed.emit("")

    def _on_editing_finished(self):
        ticker = self.selected_ticker()
        self.changed.emit(ticker or "")


class TrendKpiCard(QFrame):
    """Modern financial KPI card for monthly/annual realtime trends."""

    def __init__(self, title: str, icon_symbol: str, icon_color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("trendKpiCard")
        self.setStyleSheet("""
            QFrame#trendKpiCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
                padding: 12px 14px;
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Icon box
        self.icon_box = QLabel(icon_symbol)
        self.icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_box.setFixedSize(40, 40)
        self.icon_box.setStyleSheet(f"""
            QLabel {{
                background: {icon_color}18;
                color: {icon_color};
                font-size: 18px;
                border-radius: 8px;
                font-weight: bold;
            }}
        """)
        layout.addWidget(self.icon_box)

        # Text column
        v_layout = QVBoxLayout()
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(2)

        # Title
        self.title_lbl = QLabel(f"{title} ⓘ")
        self.title_lbl.setStyleSheet("font-size: 12px; color: #64748B; font-weight: 500;")
        v_layout.addWidget(self.title_lbl)

        # Value
        self.val_lbl = QLabel("—")
        self.val_lbl.setStyleSheet("font-size: 20px; font-weight: 700; color: #0F172A;")
        v_layout.addWidget(self.val_lbl)

        # Subtitle
        self.sub_lbl = QLabel("—")
        self.sub_lbl.setStyleSheet("font-size: 11px; color: #94A3B8;")
        v_layout.addWidget(self.sub_lbl)

        layout.addLayout(v_layout)

    def set_title(self, title: str):
        self.title_lbl.setText(f"{title} ⓘ")

    def update_data(self, val: Decimal | None, sub_text: str, is_gain: bool | None = None, is_neutral: bool = False):
        self.sub_lbl.setText(sub_text)
        if val is None:
            self.val_lbl.setText("—")
            self.val_lbl.setStyleSheet("font-size: 20px; font-weight: 700; color: #94A3B8;")
            return

        if is_neutral:
            color = "#0F172A"
            text = f"{val:,.2f} USD"
        else:
            if is_gain is None:
                is_gain = val > 0
            if val > 0:
                color = "#16A34A"  # 绿色
                text = f"+{val:,.2f} USD"
            elif val < 0:
                color = "#DC2626"  # 红色
                text = f"-{abs(val):,.2f} USD"
            else:
                color = "#64748B"
                text = f"$0.00 USD"

        self.val_lbl.setText(text)
        self.val_lbl.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {color};")


class RightAnalysisCard(QFrame):
    """Right-side statistics card: Range, Extremes, Max Drawdown, Data Coverage."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rightAnalysisCard")
        self.setStyleSheet("""
            QFrame#rightAnalysisCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
                padding: 16px;
            }
        """)
        self.setFixedWidth(285)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # 1. 周期起止
        self.range_title_lbl = QLabel("📅 本月区间")
        self.range_title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A;")
        layout.addWidget(self.range_title_lbl)

        self.range_val_lbl = QLabel("—")
        self.range_val_lbl.setStyleSheet("font-size: 11px; color: #64748B; margin-bottom: 2px;")
        layout.addWidget(self.range_val_lbl)

        line1 = QFrame()
        line1.setFrameShape(QFrame.Shape.HLine)
        line1.setStyleSheet("color: #F1F5F9;")
        layout.addWidget(line1)

        # 2. 最高点
        lbl_max_tag = QLabel("↗ 最高点")
        lbl_max_tag.setStyleSheet("font-size: 12px; color: #64748B; font-weight: 500;")
        layout.addWidget(lbl_max_tag)

        self.max_val_lbl = QLabel("—")
        self.max_val_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #16A34A;")
        layout.addWidget(self.max_val_lbl)

        self.max_time_lbl = QLabel("—")
        self.max_time_lbl.setStyleSheet("font-size: 11px; color: #94A3B8; margin-bottom: 2px;")
        layout.addWidget(self.max_time_lbl)

        # 3. 最低点
        lbl_min_tag = QLabel("↘ 最低点")
        lbl_min_tag.setStyleSheet("font-size: 12px; color: #64748B; font-weight: 500;")
        layout.addWidget(lbl_min_tag)

        self.min_val_lbl = QLabel("—")
        self.min_val_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #DC2626;")
        layout.addWidget(self.min_val_lbl)

        self.min_time_lbl = QLabel("—")
        self.min_time_lbl.setStyleSheet("font-size: 11px; color: #94A3B8; margin-bottom: 2px;")
        layout.addWidget(self.min_time_lbl)

        # 4. 最大回撤
        lbl_dd_tag = QLabel("⤓ 最大回撤")
        lbl_dd_tag.setStyleSheet("font-size: 12px; color: #64748B; font-weight: 500;")
        layout.addWidget(lbl_dd_tag)

        self.dd_val_lbl = QLabel("—")
        self.dd_val_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #0F172A;")
        layout.addWidget(self.dd_val_lbl)

        self.dd_sub_lbl = QLabel("—")
        self.dd_sub_lbl.setStyleSheet("font-size: 11px; color: #94A3B8; margin-bottom: 4px;")
        layout.addWidget(self.dd_sub_lbl)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setStyleSheet("color: #F1F5F9;")
        layout.addWidget(line2)

        # 5. 数据状态
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 4, 0, 0)
        lbl_status_tag = QLabel("数据状态")
        lbl_status_tag.setStyleSheet("font-size: 12px; color: #64748B; font-weight: 500;")
        status_row.addWidget(lbl_status_tag)
        status_row.addStretch()

        self.status_badge = QLabel("正常 / 覆盖完整")
        self.status_badge.setStyleSheet("""
            background: #ECFDF5;
            color: #059669;
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
        """)
        status_row.addWidget(self.status_badge)
        layout.addLayout(status_row)

        layout.addStretch()

    def set_is_year(self, is_year: bool):
        self.range_title_lbl.setText("📅 本年区间" if is_year else "📅 本月区间")

    def update_metrics(self, m: dict):
        self.range_val_lbl.setText(m.get("period_range_text", "—"))

        # 最高点
        max_v = m.get("max_val")
        if max_v is not None:
            c = "#16A34A" if max_v >= 0 else "#DC2626"
            self.max_val_lbl.setText(f"{max_v:+,.2f} USD")
            self.max_val_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {c};")
            self.max_time_lbl.setText(m.get("max_time_text", "—"))
        else:
            self.max_val_lbl.setText("—")
            self.max_time_lbl.setText("—")

        # 最低点
        min_v = m.get("min_val")
        if min_v is not None:
            c = "#16A34A" if min_v >= 0 else "#DC2626"
            self.min_val_lbl.setText(f"{min_v:+,.2f} USD")
            self.min_val_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {c};")
            self.min_time_lbl.setText(m.get("min_time_text", "—"))
        else:
            self.min_val_lbl.setText("—")
            self.min_time_lbl.setText("—")

        # 最大回撤
        dd_v = m.get("drawdown_val")
        if dd_v is not None:
            self.dd_val_lbl.setText(f"{dd_v:,.2f} USD" if dd_v == 0 else f"-{abs(dd_v):,.2f} USD")
            self.dd_sub_lbl.setText(m.get("drawdown_sub_text", "—"))
        else:
            self.dd_val_lbl.setText("—")
            self.dd_sub_lbl.setText("—")

        # 数据状态胶囊
        st_text = m.get("status_text", "—")
        st_level = m.get("status_level", "neutral")
        if st_level == "success":
            style = "background: #ECFDF5; color: #059669;"
        elif st_level == "warning":
            style = "background: #FFFBEB; color: #D97706;"
        else:
            style = "background: #F1F5F9; color: #64748B;"
        self.status_badge.setText(st_text)
        self.status_badge.setStyleSheet(f"""
            {style}
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
        """)


class RealtimeChartCanvas(ChartCanvas):
    """Enhanced ChartCanvas with vertical tracking crosshair, dot marker and floating Tooltip."""

    def __init__(self, parent=None, card_mode=False):
        super().__init__(parent, card_mode=card_mode)
        self.hide_legend = True
        self.hover_point = None  # (x, y, dt_str, val_str, delta_str, status_str)
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event):
        pos = event.position()
        if not self.chart or not self.chart.series:
            super().mouseMoveEvent(event)
            return

        # Find closest point
        best_pt = None
        min_dist = float("inf")
        # In ChartCanvas line rendering, self.hits contains (QRectF, key, value, name)
        if hasattr(self, "hits") and self.hits:
            for rect, key, val, name in self.hits:
                cx = rect.center().x()
                dist = abs(cx - pos.x())
                if dist < min_dist and val is not None:
                    min_dist = dist
                    best_pt = (rect, key, val, name)

        if best_pt and min_dist < 40:
            rect, key, val, name = best_pt
            dt_bj = to_bj_datetime(key)
            dt_str = dt_bj.strftime("%m-%d %H:%M") if dt_bj else key

            # Check change compared to previous valid
            delta_str = "—"
            pts = self.chart.series[0].points if self.chart.series else ()
            for i, (k, v) in enumerate(pts):
                if k == key and i > 0 and pts[i - 1][1] is not None and v is not None:
                    diff = Decimal(str(v)) - Decimal(str(pts[i - 1][1]))
                    delta_str = f"{diff:+,.2f} USD"
                    break

            val_str = f"{Decimal(str(val)):+,.2f} USD"
            # Calculate approx Y for the point
            cy = rect.center().y()
            # If hit has exact Y, cy is in rect
            self.hover_point = {
                "x": rect.center().x(),
                "y": cy,
                "time": dt_str,
                "val": val_str,
                "delta": delta_str,
                "val_num": Decimal(str(val)),
                "delta_num": Decimal(str(delta_str.replace(" USD", "").replace("+", ""))) if "USD" in delta_str else Decimal(0),
            }
            self.update()
        else:
            if self.hover_point is not None:
                self.hover_point = None
                self.update()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.hover_point is not None:
            self.hover_point = None
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.hover_point or not self.chart:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        hp = self.hover_point
        hx = hp["x"]

        # 1. 垂直标线
        pen_line = QPen(QColor("#94A3B8"), 1.0, Qt.PenStyle.DashLine)
        painter.setPen(pen_line)
        painter.drawLine(QPointF(hx, 30), QPointF(hx, self.height() - 60))

        # 2. 高亮实心圆点
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.setBrush(QColor("#2563EB"))
        # We can find accurate y from zero axis or hit rect
        hy = hp["y"]
        painter.drawEllipse(QPointF(hx, hy), 5.0, 5.0)

        # 3. 浮动白色卡片 Tooltip
        card_w, card_h = 175, 105
        card_x = hx + 12
        if card_x + card_w > self.width() - 10:
            card_x = hx - card_w - 12
        card_y = max(35, min(hy - card_h / 2, self.height() - card_h - 60))

        card_rect = QRectF(card_x, card_y, card_w, card_h)

        # 绘制卡片背景与柔和阴影
        painter.setPen(QPen(QColor("#CBD5E1"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(card_rect, 8, 8)

        # 绘制文本行
        painter.setFont(chart_font(11))

        # 行1：时间
        painter.setPen(QColor("#64748B"))
        painter.drawText(QRectF(card_x + 10, card_y + 8, 55, 18), Qt.AlignmentFlag.AlignLeft, "时间")
        painter.setPen(QColor("#0F172A"))
        painter.drawText(QRectF(card_x + 65, card_y + 8, card_w - 75, 18), Qt.AlignmentFlag.AlignRight, hp["time"])

        # 行2：累计盈亏
        painter.setPen(QColor("#64748B"))
        painter.drawText(QRectF(card_x + 10, card_y + 30, 55, 18), Qt.AlignmentFlag.AlignLeft, "累计盈亏")
        c_pnl = "#16A34A" if hp["val_num"] >= 0 else "#DC2626"
        painter.setPen(QColor(c_pnl))
        painter.drawText(QRectF(card_x + 65, card_y + 30, card_w - 75, 18), Qt.AlignmentFlag.AlignRight, hp["val"])

        # 行3：当日变化
        painter.setPen(QColor("#64748B"))
        painter.drawText(QRectF(card_x + 10, card_y + 52, 55, 18), Qt.AlignmentFlag.AlignLeft, "当日变化")
        c_delta = "#16A34A" if hp["delta_num"] >= 0 else "#DC2626"
        painter.setPen(QColor(c_delta))
        painter.drawText(QRectF(card_x + 65, card_y + 52, card_w - 75, 18), Qt.AlignmentFlag.AlignRight, hp["delta"])

        # 行4：状态
        painter.setPen(QColor("#64748B"))
        painter.drawText(QRectF(card_x + 10, card_y + 74, 55, 18), Qt.AlignmentFlag.AlignLeft, "状态")

        # 绿点
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#16A34A"))
        painter.drawEllipse(QPointF(card_x + card_w - 62, card_y + 83), 3.5, 3.5)

        painter.setPen(QColor("#0F172A"))
        painter.drawText(QRectF(card_x + card_w - 55, card_y + 74, 45, 18), Qt.AlignmentFlag.AlignRight, "有效采样")


class MonthlyRealtimePnlPage(QWidget):
    drill = Signal(str, str)
    error = Signal(str)
    read_progress = Signal(object)

    def __init__(self, parent=None, embedded=False):
        super().__init__(parent)
        self.read_progress.connect(self._show_read_progress, Qt.ConnectionType.QueuedConnection)
        self.window = parent
        self.analytics = None
        self.embedded = embedded

        # 月度高频数据缓存
        self._cached_month = ""
        self._cached_by_day = {}
        self._cached_all_trading_days = []
        self._cached_channels = []
        self._cached_tickers = []
        self._rebuild_pending = False
        self._rebuild_attempted = set()

        # 年度高频数据缓存
        self._cached_year = ""
        self._cached_year_by_day = {}
        self._cached_year_all_trading_days = []
        self._cached_year_channels = []
        self._cached_year_tickers = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        # 1. 顶部 Tab 栏：月度走势详情 vs 年度走势详情
        self.tab_bar = QTabBar(self)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.addTab("月度走势详情")
        self.tab_bar.addTab("年度走势详情")
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tab_bar)

        # 2. 顶部控制栏
        self.top_bar_widget = QWidget(self)
        top_bar = QHBoxLayout(self.top_bar_widget)
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(8)

        # 月份选择器
        self.lbl_month = QLabel("月份/年份：")
        top_bar.addWidget(self.lbl_month)
        self.month_combo = QComboBox()
        self.month_combo.setMinimumWidth(115)
        self.month_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.month_combo.view().setMinimumWidth(125)
        self.month_combo.currentIndexChanged.connect(self._on_month_changed)
        top_bar.addWidget(self.month_combo)

        # 年份选择器
        self.lbl_year = QLabel("年份：")
        self.lbl_year.setVisible(False)
        top_bar.addWidget(self.lbl_year)
        self.year_combo = QComboBox()
        self.year_combo.setMinimumWidth(95)
        self.year_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.year_combo.view().setMinimumWidth(105)
        self.year_combo.setVisible(False)
        self.year_combo.currentIndexChanged.connect(self._on_year_changed)
        top_bar.addWidget(self.year_combo)

        # 走势模式下拉框
        lbl_mode = QLabel("走势模式：")
        lbl_mode.setContentsMargins(6, 0, 0, 0)
        top_bar.addWidget(lbl_mode)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("当月累计盈亏走势", "cumulative")
        self.mode_combo.addItem("每日日内盈亏走势", "intraday")
        self.mode_combo.setMinimumWidth(140)
        self.mode_combo.setToolTip("选择查看累计盈亏曲线，或各交易日各自的日内波动走势")
        self.mode_combo.currentIndexChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.mode_combo)

        # 人员筛选
        lbl_person = QLabel("人员：")
        top_bar.addWidget(lbl_person)
        self.person_combo = PersonFilter(self)
        self.person_combo.setMinimumWidth(115)
        self.person_combo.setToolTip("归属人员筛选（点击可单选或多选具体持有者叠加对比走势）")
        self.person_combo.addItem("全部启用人员", None)
        self.person_combo.addItem("他人", "OTHERS")
        self.person_combo.addItem("个人", 1)
        self.person_combo.currentIndexChanged.connect(self._on_person_changed)
        top_bar.addWidget(self.person_combo)

        # 渠道筛选
        lbl_ch = QLabel("渠道：")
        top_bar.addWidget(lbl_ch)
        self.channel_filter = ChannelFilter(self)
        self.channel_filter.changed.connect(self._on_filter_changed)
        top_bar.addWidget(self.channel_filter)

        # 股票筛选
        lbl_stk = QLabel("股票：")
        top_bar.addWidget(lbl_stk)
        self.stock_combo = StockFilter(self)
        self.stock_combo.changed.connect(self._on_filter_changed)
        top_bar.addWidget(self.stock_combo)

        # 搜索输入框
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索代码、订单、备注等")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.search_edit)

        # 重置筛选
        self.reset_filter_btn = QPushButton("重置筛选")
        self.reset_filter_btn.clicked.connect(self.reset_filters)
        top_bar.addWidget(self.reset_filter_btn)

        # 导出 PNG
        export_btn = QPushButton("📥 导出 PNG")
        export_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB;
                color: #FFFFFF;
                font-weight: 600;
                border-radius: 6px;
                padding: 5px 12px;
            }
            QPushButton:hover {
                background: #1D4ED8;
            }
        """)
        export_btn.clicked.connect(self.export)
        top_bar.addWidget(export_btn)

        # 隐藏的简要摘要徽标（保留用于异步计算状态通知）
        self.summary_badge = QLabel("")
        self.summary_badge.setObjectName("muted")
        self.summary_badge.setVisible(False)
        top_bar.addWidget(self.summary_badge)

        layout.addWidget(self.top_bar_widget)

        # 3. 已选条件 Chips 栏（根据需求彻底隐藏，不展示）
        self.chips_bar = QWidget(self)
        self.chips_bar.setVisible(False)
        self.chip_period = QPushButton("月份: 2026-11")
        self.chip_person = QPushButton("人员: 全部启用人员")
        self.chip_channel = QPushButton("渠道: 全部渠道")
        self.chip_stock = QPushButton("股票: 全部股票")

        # 4. 核心指标卡片区域（4 张卡片水平等宽并排）
        self.kpi_container = QWidget(self)
        kpi_layout = QHBoxLayout(self.kpi_container)
        kpi_layout.setContentsMargins(0, 2, 0, 4)
        kpi_layout.setSpacing(12)

        self.kpi_start = TrendKpiCard("月初累计盈亏", "🏳", "#3B82F6", self)
        self.kpi_latest = TrendKpiCard("当前最新累计盈亏", "📅", "#0EA5E9", self)
        self.kpi_delta = TrendKpiCard("本月变化", "↘", "#EF4444", self)
        self.kpi_range = TrendKpiCard("月内波动区间", "📈", "#6366F1", self)

        kpi_layout.addWidget(self.kpi_start)
        kpi_layout.addWidget(self.kpi_latest)
        kpi_layout.addWidget(self.kpi_delta)
        kpi_layout.addWidget(self.kpi_range)
        layout.addWidget(self.kpi_container)

        # 5. 主图表与右侧面板容器
        self.mid_container = QWidget(self)
        mid_layout = QHBoxLayout(self.mid_container)
        mid_layout.setContentsMargins(0, 2, 0, 2)
        mid_layout.setSpacing(12)

        # 左侧图表主卡片
        self.chart_card = QFrame(self)
        self.chart_card.setObjectName("chartCard")
        self.chart_card.setStyleSheet("""
            QFrame#chartCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
        """)
        chart_card_layout = QVBoxLayout(self.chart_card)
        chart_card_layout.setContentsMargins(14, 12, 14, 12)
        chart_card_layout.setSpacing(6)

        # 图表卡片顶部工具栏（根据需求移除右上角缩放/重置/说明按钮）
        self.chart_title_lbl = QLabel("2026-11 累计盈亏走势")
        self.chart_title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #0F172A; padding: 2px 0 6px 0;")
        chart_card_layout.addWidget(self.chart_title_lbl)

        # 保留隐藏控件保持向后兼容
        self.zoom_combo = QComboBox()
        self.zoom_combo.setVisible(False)
        self.reset_view_btn = QPushButton("🔄 重置视图")
        self.reset_view_btn.setVisible(False)
        self.explain_btn = QPushButton("ℹ 查看说明")
        self.explain_btn.setVisible(False)

        # 图表画布
        self.canvas = RealtimeChartCanvas(card_mode=False)
        self.canvas.setMinimumHeight(440)
        self.canvas.selected.connect(self.drill)
        chart_card_layout.addWidget(self.canvas, 1)

        mid_layout.addWidget(self.chart_card, 78)

        # 右侧数据分析面板
        self.right_panel = RightAnalysisCard(self)
        mid_layout.addWidget(self.right_panel, 22)

        layout.addWidget(self.mid_container, 1)

        # 6. 底部精简状态栏
        self.bottom_bar = QFrame(self)
        self.bottom_bar.setObjectName("bottomBar")
        self.bottom_bar.setStyleSheet("""
            QFrame#bottomBar {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
                padding: 6px 14px;
            }
        """)
        bottom_bar_layout = QHBoxLayout(self.bottom_bar)
        bottom_bar_layout.setContentsMargins(8, 4, 8, 4)
        bottom_bar_layout.setSpacing(10)

        icon_layer = QLabel("📚")
        icon_layer.setStyleSheet("font-size: 14px;")
        bottom_bar_layout.addWidget(icon_layer)

        self.bottom_status_lbl = QLabel("正在加载高频行情数据…")
        self.bottom_status_lbl.setStyleSheet("font-size: 12px; color: #475569; font-weight: 500;")
        bottom_bar_layout.addWidget(self.bottom_status_lbl, 1)

        layout.addWidget(self.bottom_bar)

        # 兼容性属性：保留旧代码属性引用，避免外部访问报错
        self.status_label = self.bottom_status_lbl
        self.notes_label = QLabel("")
        self.notes_label.setVisible(False)

        # 定时器
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(60000)
        self.refresh_timer.timeout.connect(self._refresh_active_period)
        self.refresh_timer.start()

        self._derived_generation = None
        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.setInterval(2000)
        self.maintenance_timer.timeout.connect(self._poll_derived_progress)
        self.maintenance_timer.start()

    def reset_filters(self):
        """Reset person, channel, stock, search filters."""
        self.person_combo.blockSignals(True)
        self.person_combo.setCurrentIndex(0)
        self.person_combo.blockSignals(False)

        self.channel_filter.reset()

        self.stock_combo.blockSignals(True)
        self.stock_combo.setCurrentIndex(0)
        self.stock_combo.blockSignals(False)

        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)

        self.update_chart()

    def _sync_chips(self):
        """Sync chip labels with current filters."""
        is_year = (self.tab_bar.currentIndex() == 1)
        period = self.year_combo.currentText() if is_year else self.month_combo.currentText()
        self.chip_period.setText(f"{'年份' if is_year else '月份'}: {period}")

        person_txt = self.person_combo.currentText()
        self.chip_person.setText(f"人员: {person_txt} ×")

        ch_txt = self.channel_filter.text()
        self.chip_channel.setText(f"渠道: {ch_txt} ×")

        stk_txt = self.stock_combo.selected_ticker() or "全部"
        self.chip_stock.setText(f"股票: {stk_txt} ×")

    def _show_explanation_dialog(self):
        """Show non-intrusive lightweight dialog with data description."""
        note_text = (
            "【数据与采样说明】\n\n"
            "• 展示所选整月/整年全部美股交易日时间轴（夏令时北京时间每日夜盘 08:00 至次日 08:00 盘后）；\n"
            "• 月度走势采用 30 分钟连续采样，年度走势采用 2 小时高频采样；\n"
            "• 实盘曲线绘制截至当前时点，未来时段或缺少历史点的日期均留白，不臆测、不平滑、不补零；\n"
            "• 垂直虚线按每日夜盘（08:00）、盘前（16:00）、盘中（21:30）、盘后（04:00）精确划分各时段；\n"
            "• 最大回撤严格按时序先寻找峰值再寻找谷底计算；月内/年内波动区间为最高点与最低点之差。"
        )
        dlg = QMessageBox(self)
        dlg.setWindowTitle("说明 / 数据口径")
        dlg.setText(note_text)
        dlg.setIcon(QMessageBox.Icon.Information)
        dlg.exec()

    def _on_zoom_changed(self, index):
        """Zoom view to recent N days."""
        # Optional zoom logic: adjust category window if canvas supports it
        pass

    def _on_reset_view(self):
        """Reset view zoom."""
        self.zoom_combo.setCurrentIndex(0)
        self.update_chart()

    def _poll_derived_progress(self):
        if not self.isVisible():
            return
        source = self._resolve_source_path()
        if not source:
            return
        from portfolio.services.derived_maintenance import maintenance_status
        status = maintenance_status(source)
        progress = status["progress"]
        was_running = getattr(self, "_history_running", False)
        self._history_running = status["running"]
        if was_running and not status["running"]:
            self._derived_generation = None
        self._sync_loading(status)
        if status["running"] or not status["ready"]:
            text = progress.get("message", "正在后台计算全部历史人员。")
            if progress.get("total_units"):
                text += f" {progress.get('completed_units', 0)}/{progress['total_units']}（{progress.get('percent', 0)}%）"
            self.summary_badge.setText(text)
        if getattr(self, "_shared_busy", None):
            return
        if status["ready"] and status.get("generation") != self._derived_generation:
            self._derived_generation = status["generation"]
            if not self.month_combo.count():
                self._init_available_months()
            if not self.year_combo.count():
                self._init_available_years()
            if self.tab_bar.currentIndex() == 1:
                self._load_year_data(self.year_combo.currentText())
            else:
                self._load_month_data(self.month_combo.currentText())

    def _refresh_active_period(self):
        if not self.analytics or not self.isVisible():
            return
        from portfolio.domain.live_quotes import market_trade_day
        _, days, halves, _ = self.window.quote_store.load(datetime.now(UTC))
        today = market_trade_day(datetime.now(UTC), days, halves)
        if self.tab_bar.currentIndex() == 1:
            if self.year_combo.currentText() == str(today.year):
                self._load_year_data(self.year_combo.currentText())
        else:
            if self.month_combo.currentText() == today.strftime("%Y-%m"):
                self._load_month_data(self.month_combo.currentText())

    def _resolve_db_path(self):
        """Locate live_quotes.sqlite3 safely from window quote store or local application data."""
        if hasattr(self.window, "quote_store") and self.window.quote_store and self.window.quote_store.path.exists():
            return self.window.quote_store.path
        from portfolio.paths import quote_cache_path
        return quote_cache_path()

    def _resolve_source_path(self):
        """Locate the main money.sqlite3 database path."""
        if self.window and hasattr(self.window, "info") and self.window.info and self.window.info.path:
            return str(self.window.info.path)
        if self.window and hasattr(self.window, "service") and self.window.service and self.window.service.path:
            return str(self.window.service.path)
        candidates = [DATABASE_PATH]
        for p in candidates:
            if p.exists():
                return str(p)
        return ""

    def _get_connection(self):
        db_path = self._resolve_db_path()
        if not db_path.exists():
            return None
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)

    def clear(self):
        self.analytics = None
        self._shared_request = None
        if hasattr(self, "_shared_analytics"):
            del self._shared_analytics
        self._rendered_cache_key = None
        self._cached_month = ""
        self._cached_by_day.clear()
        self._cached_all_trading_days.clear()
        self._cached_year = ""
        self._cached_year_by_day.clear()
        self._cached_year_all_trading_days.clear()
        self._rebuild_attempted.clear()
        self.canvas.chart = None
        self.canvas.update()
        self.summary_badge.setText("")
        self.bottom_status_lbl.setText("未连接数据源或无可用行情记录。")

    def set_analytics(self, analytics, force=False):
        person_val = self.person_combo.currentData() if hasattr(self, "person_combo") else None
        cache_key = (
            getattr(analytics, "calculated_at", None),
            getattr(analytics, "source_identity", None),
            getattr(analytics, "scope", None),
            person_val,
        )
        if not force and getattr(self, "_rendered_cache_key", None) == cache_key:
            return
        self.analytics = analytics
        self._populate_persons()
        self._init_available_months()
        self._init_available_years()
        self._rendered_cache_key = (
            getattr(analytics, "calculated_at", None),
            getattr(analytics, "source_identity", None),
            getattr(analytics, "scope", None),
            self.person_combo.currentData() if hasattr(self, "person_combo") else None,
        )

    def _get_persons(self):
        if self.window and hasattr(self.window, "info") and self.window.info:
            persons = getattr(self.window.info, "persons", None)
            if persons:
                return persons
        source_path = self._resolve_source_path()
        if source_path and Path(source_path).exists():
            try:
                con = sqlite3.connect(source_path)
                cur = con.cursor()
                rows = cur.execute("SELECT id, name, enabled FROM persons ORDER BY id").fetchall()
                con.close()
                return [{"id": r[0], "name": r[1], "enabled": bool(r[2])} for r in rows]
            except Exception:
                pass
        return []

    def _populate_persons(self):
        persons = self._get_persons()
        if not persons:
            return
        preferred_val = self.person_combo.currentData()
        if self.window and hasattr(self.window, "person"):
            main_val = self.window.person.currentData()
            if self.window.person.count() > 0:
                preferred_val = main_val

        self.person_combo.blockSignals(True)
        self.person_combo.clear()
        self.person_combo.addItem("全部启用人员", None)
        self.person_combo.addItem("他人", "OTHERS")

        geren = next((p for p in persons if p.get("id") == 1 or p.get("name") == "个人"), None)
        if geren:
            self.person_combo.addItem(geren.get("name", "个人"), geren.get("id", 1))
        else:
            self.person_combo.addItem("个人", 1)

        for p in persons:
            if p.get("id") != 1 and p.get("name") != "个人" and p.get("enabled", True):
                self.person_combo.addItem(p["name"], p["id"])

        idx = self.person_combo.findData(preferred_val)
        if idx >= 0:
            self.person_combo.setCurrentIndex(idx)
        else:
            self.person_combo.setCurrentIndex(0)
        self.person_combo.blockSignals(False)

    def sync_person_from_main(self, person_val):
        if getattr(self, "_syncing_person", False):
            return
        curr_val = self.person_combo.currentData()
        if curr_val == person_val:
            return
        idx = self.person_combo.findData(person_val)
        if idx >= 0:
            self._syncing_person = True
            try:
                self.person_combo.blockSignals(True)
                self.person_combo.setCurrentIndex(idx)
                self.person_combo.blockSignals(False)
                if self.tab_bar.currentIndex() == 1:
                    self._load_year_data(self.year_combo.currentText())
                else:
                    self._load_month_data(self.month_combo.currentText())
            finally:
                self._syncing_person = False

    def _init_available_months(self):
        conn = self._get_connection()
        months = []
        if conn:
            try:
                months = [
                    r[0]
                    for r in conn.execute("SELECT DISTINCT substr(day,1,7) FROM us_calendar ORDER BY 1 DESC")
                    if r[0]
                ]
            finally:
                conn.close()
        if not months:
            today = date.today()
            months = [
                f"{year}-{month:02d}"
                for year in range(2026, today.year + 1)
                for month in range(1, (today.month if year == today.year else 12) + 1)
            ][::-1]
        preferred = date.today().strftime("%Y-%m")
        if self.window and hasattr(self.window, "quote_store") and self.window.quote_store:
            try:
                from portfolio.domain.live_quotes import market_trade_day
                _, calendar_days, halves, _ = self.window.quote_store.load(datetime.now(UTC))
                preferred = market_trade_day(datetime.now(UTC), calendar_days, halves).strftime("%Y-%m")
            except Exception:
                pass
        if preferred not in months:
            months.append(preferred)
            months.sort(reverse=True)
        current = self.month_combo.currentText() or preferred
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        self.month_combo.addItems(months)
        if current in months:
            self.month_combo.setCurrentText(current)
        self.month_combo.blockSignals(False)
        if self.tab_bar.currentIndex() == 0:
            self._load_month_data(self.month_combo.currentText())

    def _init_available_years(self):
        conn = self._get_connection()
        years = []
        if conn:
            try:
                years = [
                    r[0]
                    for r in conn.execute("SELECT DISTINCT substr(day,1,4) FROM us_calendar ORDER BY 1 DESC")
                    if r[0]
                ]
            finally:
                conn.close()
        if not years:
            years = [str(year) for year in range(date.today().year, 2025, -1)]
        preferred = str(date.today().year)
        if self.window and hasattr(self.window, "quote_store") and self.window.quote_store:
            try:
                from portfolio.domain.live_quotes import market_trade_day
                _, calendar_days, halves, _ = self.window.quote_store.load(datetime.now(UTC))
                preferred = str(market_trade_day(datetime.now(UTC), calendar_days, halves).year)
            except Exception:
                pass
        if preferred not in years:
            years.append(preferred)
            years.sort(reverse=True)
        current = self.year_combo.currentText() or preferred
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        self.year_combo.addItems(years)
        if current in years:
            self.year_combo.setCurrentText(current)
        self.year_combo.blockSignals(False)
        if self.tab_bar.currentIndex() == 1:
            self._load_year_data(self.year_combo.currentText())

    def _on_tab_changed(self, index):
        is_year = (index == 1)
        self.lbl_month.setVisible(not is_year)
        self.month_combo.setVisible(not is_year)
        self.lbl_year.setVisible(is_year)
        self.year_combo.setVisible(is_year)

        # 切换卡片标题（月初 vs 年初）
        self.kpi_start.set_title("年初累计盈亏" if is_year else "月初累计盈亏")
        self.kpi_delta.set_title("本年变化" if is_year else "本月变化")
        self.kpi_range.set_title("年内波动区间" if is_year else "月内波动区间")
        self.right_panel.set_is_year(is_year)

        mode_data = self.mode_combo.currentData() or "cumulative"
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        if is_year:
            self.mode_combo.addItem("当年累计盈亏走势", "cumulative")
            self.mode_combo.addItem("每日日内盈亏走势", "intraday")
        else:
            self.mode_combo.addItem("当月累计盈亏走势", "cumulative")
            self.mode_combo.addItem("每日日内盈亏走势", "intraday")
        idx = self.mode_combo.findData(mode_data)
        self.mode_combo.setCurrentIndex(max(0, idx))
        self.mode_combo.blockSignals(False)

        if is_year:
            if not self.year_combo.count():
                self._init_available_years()
            elif not self._cached_year_by_day or self._cached_year != self.year_combo.currentText():
                self._load_year_data(self.year_combo.currentText())
            else:
                self.channel_filter.populate(self._cached_year_channels)
                self.stock_combo.populate(self._cached_year_tickers)
                self.update_chart()
        else:
            if not self.month_combo.count():
                self._init_available_months()
            elif not self._cached_by_day or self._cached_month != self.month_combo.currentText():
                self._load_month_data(self.month_combo.currentText())
            else:
                self.channel_filter.populate(self._cached_channels)
                self.stock_combo.populate(self._cached_tickers)
                self.update_chart()

    def _on_month_changed(self):
        if self.tab_bar.currentIndex() == 0:
            self._load_month_data(self.month_combo.currentText())

    def _on_year_changed(self):
        if self.tab_bar.currentIndex() == 1:
            self._load_year_data(self.year_combo.currentText())

    def _on_person_changed(self):
        if getattr(self, "_syncing_person", False):
            return
        selected_val = self.person_combo.currentData()
        if self.tab_bar.currentIndex() == 1:
            self._load_year_data(self.year_combo.currentText())
        else:
            self._load_month_data(self.month_combo.currentText())

        if self.window and hasattr(self.window, "person"):
            curr_main_val = self.window.person.currentData()
            if curr_main_val != selected_val:
                idx = self.window.person.findData(selected_val)
                if idx >= 0:
                    self._syncing_person = True
                    try:
                        self.window.person.blockSignals(True)
                        self.window.person.setCurrentIndex(idx)
                        self.window.person.blockSignals(False)
                        self.window.filters_changed()
                    finally:
                        self._syncing_person = False

    def _shared_person_ids(self):
        selected = self.person_combo.currentData()
        if selected == "OTHERS":
            return tuple(
                p["id"]
                for p in self._get_persons()
                if p.get("name") != "个人" and p.get("enabled", True)
            )
        if isinstance(selected, (tuple, list)):
            return tuple(int(pid) for pid in selected) or None
        return (selected,) if selected is not None else None

    def _load_shared_period(self, period, year):
        if not self.analytics or not self.window:
            return
        inherited = getattr(self, "_parent_filters", None) is not None
        if inherited and getattr(self.analytics, "scope", None) and getattr(self.analytics.scope, "as_of", None):
            as_of = self.analytics.scope.as_of
            period = str(as_of.year) if year else as_of.strftime("%Y-%m")
        if not period:
            return
        from portfolio.services.derived_queries import load_history

        source = self._resolve_source_path()
        if not source:
            return
        if year:
            first, last = date(int(period), 1, 1), date(int(period), 12, 31)
        else:
            y, m = map(int, period.split("-"))
            first, last = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
        people = self.analytics.scope.person_ids if inherited else self._shared_person_ids()
        others_mode = (
            (getattr(self.window.person, "currentData", lambda: None)() == "OTHERS")
            if inherited
            else (self.person_combo.currentData() == "OTHERS")
        )
        scope = replace(self.analytics.scope, person_ids=people, start_date=None, as_of=min(last, date.today()))
        if getattr(self, "_parent_filters", None) is None:
            scope = replace(scope, account_ids=None, instrument_ids=None, category=None, search="")
        if scope.as_of < first:
            scope = replace(scope, as_of=last)
        inherited_filters = getattr(self, "_parent_filters", None)
        channels = (
            ([inherited_filters[0]] if inherited_filters[0] else None)
            if inherited_filters is not None
            else self.channel_filter.selected_channels()
        )
        ticker = (
            inherited_filters[1]
            if inherited_filters is not None
            else self.stock_combo.selected_ticker()
        )
        # 兼顾搜索框过滤
        search_txt = self.search_edit.text().strip().upper() if hasattr(self, "search_edit") else ""
        if search_txt and not ticker:
            ticker = search_txt

        mode = self.mode_combo.currentData() or "cumulative"
        request = (period, year, scope, others_mode, tuple(sorted(channels or ())), ticker, mode)
        if getattr(self, "_shared_busy", None) == request:
            return
        self._shared_request = request
        self._shared_busy = request
        self.summary_badge.setText("正在读取共享历史派生数据…")
        self.canvas.chart = None
        self.canvas.set_loading("正在读取并整理月度/年度历史曲线…")

        def task(cancel):
            from time import monotonic

            last_progress = [None, 0.0]

            def progress(stage, done, total):
                if cancel.is_set():
                    return
                now = monotonic()
                if stage != last_progress[0] or (total and done == total) or now - last_progress[1] >= 0.1:
                    last_progress[:] = [stage, now]
                    self.read_progress.emit((request, stage, done, total))

            from portfolio.services.derived_store import DerivedStore

            if not DerivedStore(source).readiness()["ready"]:
                rows, state = load_history(source, self.window.quote_store, scope, first, last)
                return None, rows, state, None
            from portfolio.services.derived_queries import historical_analytics, trend

            result = historical_analytics(source, scope, first, last, progress=progress)
            rows, state = load_history(
                source,
                self.window.quote_store,
                scope,
                first,
                last,
                aggregate=True,
                channels=channels,
                ticker=ticker,
                progress=progress,
            )
            chart, state = trend(
                source,
                self.window.quote_store,
                result,
                period,
                year=year,
                channels=channels,
                ticker=ticker,
                mode=mode,
                history_data=(rows, state),
                progress=progress,
            )
            if scope.person_ids and len(scope.person_ids) > 1 and not others_mode:
                persons_map = {p["id"]: p["name"] for p in self._get_persons()}
                person_series = []
                for pid in scope.person_ids:
                    pname = persons_map.get(pid, f"人员{pid}")
                    sub_scope = replace(scope, person_ids=(pid,))
                    person_progress = lambda stage, done, total: progress(pname + "：" + stage, done, total)
                    sub_res = historical_analytics(source, sub_scope, first, last, progress=person_progress)
                    sub_rows, sub_st = load_history(
                        source,
                        self.window.quote_store,
                        sub_scope,
                        first,
                        last,
                        aggregate=True,
                        channels=channels,
                        ticker=ticker,
                        progress=person_progress,
                    )
                    sub_chart, _ = trend(
                        source,
                        self.window.quote_store,
                        sub_res,
                        period,
                        year=year,
                        channels=channels,
                        ticker=ticker,
                        mode=mode,
                        history_data=(sub_rows, sub_st),
                        progress=person_progress,
                    )
                    if sub_chart.series:
                        person_series.append(Series(pname, sub_chart.series[0].points))
                if person_series:
                    chart = replace(chart, series=tuple(person_series))
            if state.get("baseline_state") == "REPORT_PENDING" or (
                not any(v is not None for series in chart.series for _, v in series.points)
                and (
                    state.get("invalid_partitions")
                    or state.get("not_generated_days")
                    or getattr(result, "incomplete_people", False)
                    or state.get("baseline_break_days")
                )
            ):
                from portfolio.services.derived_maintenance import schedule_changed

                schedule_changed(source)
            return result, rows, state, chart

        def ready(value):
            if self._shared_request != request:
                return
            self._shared_busy = None
            self.canvas.set_loading()
            self._shared_analytics, self._shared_rows, self._shared_state, chart = value
            self._shared_period, self._shared_year = period, year
            if year:
                self._cached_year = period
            else:
                self._cached_month = period
            if value[0] is None:
                self.canvas.chart = None
                self.canvas.update()
                self.bottom_status_lbl.setText(value[2].get("message", "尚无已发布历史数据。"))
                self._sync_loading()
                return
            channels = {r.values[1] for r in value[0].reports["每日持仓收益"].rows}
            tickers = {r.values[3] for r in value[0].reports["每日持仓收益"].rows}
            self.channel_filter.populate(sorted(channels))
            self.stock_combo.populate(sorted(tickers))
            self._display_shared_chart(chart, value[2], year)

        lane = f"shared-trend-{id(self)}"
        self.window._submit(lane, task, ready)

        def failed(message):
            if self._shared_request == request:
                self._shared_busy = None
                self.summary_badge.setText(message)
                self.canvas.set_loading()
                self.canvas.empty_message = "历史曲线查询失败：" + message
                self.canvas.update()

        self.window.jobs[lane].signals.error.connect(failed, Qt.ConnectionType.QueuedConnection)

    def _show_read_progress(self, value):
        request, stage, done, total = value
        if getattr(self, "_shared_busy", None) != request or getattr(self, "_shared_request", None) != request:
            return
        self.canvas.set_loading(stage + "…（当前阶段进度）", done, total)

    def set_parent_filters(self, channel, ticker):
        """
        Embedded Today charts share the parent channel/Ticker selection and hide redundant filter controls.
        【关键约束】：内嵌模式下完全隐藏顶部控制栏、Chips、4个指标卡片和右侧面板，100% 保持今日盈亏原有排版！
        """
        self.embedded = True
        if hasattr(self, "top_bar_widget"):
            self.top_bar_widget.hide()
        if hasattr(self, "chips_bar"):
            self.chips_bar.hide()
        if hasattr(self, "kpi_container"):
            self.kpi_container.hide()
        if hasattr(self, "right_panel"):
            self.right_panel.hide()
        if hasattr(self, "bottom_bar"):
            self.bottom_bar.hide()
        if self.layout():
            self.layout().setContentsMargins(0, 4, 0, 0)
        filters = (channel, (ticker or "").strip())
        if getattr(self, "_parent_filters", None) == filters:
            return
        self._parent_filters = filters
        for control in (self.person_combo, self.channel_filter, self.stock_combo):
            control.setEnabled(False)
            control.setToolTip("随今日盈亏主页面的人员、渠道和股票筛选同步更新。")
        self.update_chart()

    def _render_shared_chart(self, year):
        if (
            getattr(self, "_parent_filters", None) is not None
            and self.analytics
            and getattr(self.analytics, "scope", None)
            and getattr(self.analytics.scope, "as_of", None)
        ):
            as_of = self.analytics.scope.as_of
            period = str(as_of.year) if year else as_of.strftime("%Y-%m")
        else:
            period = self.year_combo.currentText() if year else self.month_combo.currentText()
        self._load_shared_period(period, year)

    def _sync_loading(self, status=None):
        if getattr(self, "_shared_busy", None):
            return
        from portfolio.services.derived_maintenance import maintenance_status

        source = self._resolve_source_path()
        if not source:
            return
        status = status or maintenance_status(source)
        progress = status["progress"]
        chart = self.canvas.chart
        valid = bool(chart and any(v is not None for series in chart.series for _, v in series.points))
        if status["running"]:
            text = "正在后台生成历史盈亏，完成后自动刷新。\n" + progress.get("message", "正在处理历史分区。")
            self.bottom_status_lbl.setText(text)
            if not valid:
                self.canvas.set_loading(text, progress.get("completed_units"), progress.get("total_units"))
        else:
            self.canvas.set_loading()
            if not valid:
                state = getattr(self, "_shared_state", {})
                message = (
                    progress.get("message", "")
                    if progress.get("state") in ("FAILED", "INTERRUPTED")
                    else state.get("message", "")
                )
                self.canvas.empty_message = "历史曲线尚不可用（当前没有后台生成任务）。\n" + (
                    message or "请检查行情覆盖、累计基准或任务失败原因。"
                )
                self.bottom_status_lbl.setText(self.canvas.empty_message)
                self.canvas.update()

    def _display_shared_chart(self, chart, state, year):
        # 1. 周期与模式
        period = (self.year_combo.currentText() if year else self.month_combo.currentText()) or (str(date.today().year) if year else date.today().strftime("%Y-%m"))
        mode_text = "每日日内盈亏走势" if (self.mode_combo.currentData() == "intraday") else "累计盈亏走势"
        mode = self.mode_combo.currentData() or "cumulative"
        self.chart_title_lbl.setText(f"{period} {mode_text}")

        # 2. 计算所选周期起点之前的历史累计总盈亏基准（从最开始到上个月底时的累计盈亏）
        channels = self.channel_filter.selected_channels()
        ticker = self.stock_combo.selected_ticker()
        base_pnl = get_period_opening_pnl(self.analytics, period, is_year=year, channels=channels, ticker=ticker)

        # 3. 规整化 X 轴标签：避免显示未解析的原始 ISO 字符串，统一为优雅日期
        clean_labels = dict(chart.x_labels) if chart.x_labels else {}
        for series in chart.series:
            for k, _ in series.points:
                if k not in clean_labels:
                    dt_bj = to_bj_datetime(k)
                    clean_labels[k] = dt_bj.strftime("%m-%d") if dt_bj else ""

        # 4. 图表曲线点位处理：若为累计走势，叠加基准盈亏展示真实累计走势；清除 series 名称彻底隐藏“累计盈亏”图例
        chart_series_shifted = []
        for s in chart.series:
            shifted_points = tuple(
                (k, (Decimal(str(v)) + base_pnl) if (v is not None and mode == "cumulative") else v)
                for k, v in s.points
            )
            # 用户明确要求：去掉“累计盈亏”字样，设置 name="" 结合 hide_legend 彻底移除
            chart_series_shifted.append(replace(s, name="", points=shifted_points))

        # 清除画布内部重复标题与被裁切的底层 note，由外层卡片与弹窗呈现
        chart_cleaned = replace(
            chart,
            title="",
            note="",
            series=tuple(chart_series_shifted),
            x_labels=clean_labels,
        )
        self.canvas.chart = chart_cleaned
        self.canvas.update()
        self.summary_badge.setText(state.get("message", ""))
        self.notes_label.setText(chart.note)

        # 5. 统计计算与指标更新
        all_pts = []
        if chart.series:
            if len(chart.series) == 1:
                all_pts = chart.series[0].points
            else:
                pts_map = defaultdict(lambda: Decimal(0))
                has_val = defaultdict(bool)
                for s in chart.series:
                    for k, v in s.points:
                        if v is not None:
                            pts_map[k] += Decimal(str(v))
                            has_val[k] = True
                all_pts = [(k, pts_map[k] if has_val[k] else None) for k, _ in chart.series[0].points]

        m = calculate_realtime_metrics(all_pts, is_year=year, period_str=period, state=state, base_pnl=base_pnl)

        # 更新 4 个核心 KPI 卡片
        self.kpi_start.update_data(m["start_val"], m["start_time_text"])
        self.kpi_latest.update_data(m["latest_val"], m["latest_time_text"])
        self.kpi_delta.update_data(m["delta_val"], m["delta_sub_text"])
        self.kpi_range.update_data(m["range_val"], m["range_sub_text"], is_neutral=True)

        # 更新右侧分析面板
        self.right_panel.update_metrics(m)

        # 6. 精简底部状态栏
        if m["valid_count"] > 0:
            latest_val_str = f"{m['latest_val']:+,.2f} USD" if m["latest_val"] is not None else "—"
            latest_time = m["latest_time_text"].replace("最近有效点 · ", "").replace(" 北京时间", "")
            status_text = f"有效采样 {m['valid_count']} · 最近有效点 {latest_time} 北京时间 · 最新累计盈亏 {latest_val_str}"
            self.bottom_status_lbl.setText(status_text)
        else:
            self.bottom_status_lbl.setText("当前范围没有有效历史采样；请检查初始化、价格覆盖与缓存状态。")

        self._sync_loading()

    def _load_month_data(self, month_str):
        return self._load_shared_period(month_str, False)

    def _load_year_data(self, year_str):
        return self._load_shared_period(year_str, True)

    def _on_filter_changed(self, *_):
        self.update_chart()

    def update_chart(self):
        if self.tab_bar.currentIndex() == 1:
            self._render_year_chart()
        else:
            self._render_month_chart()

    def _render_month_chart(self):
        return self._render_shared_chart(False)

    def _render_year_chart(self):
        return self._render_shared_chart(True)

    def export(self):
        """Export the realtime chart card to high-resolution PNG."""
        is_year = (self.tab_bar.currentIndex() == 1)
        period = self._cached_year or "当年" if is_year else (self._cached_month or "当月")
        mode = "累计走势" if self.mode_combo.currentData() == "cumulative" else "日内走势"
        ch = self.channel_filter.text().replace(" ", "_")
        ticker = self.stock_combo.selected_ticker() or "全股票"
        person = self.person_combo.currentText()
        sampling = "两小时采样" if is_year else "半小时采样"
        chart_type = "整年实时" if is_year else "整月实时"
        title = f"{period}_{chart_type}{mode}_{person}_{ch}_{ticker}_{sampling}"
        dlg_title = "导出整年实时走势图" if is_year else "导出当月实时走势图"
        path, _ = QFileDialog.getSaveFileName(self, dlg_title, f"{title}.png", "PNG (*.png)")
        if path:
            try:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                # 优先截取整个主图区域
                target_widget = self.mid_container if hasattr(self, "mid_container") else self
                if not target_widget.grab().save(buffer, "PNG"):
                    raise ValueError("PNG 编码失败")
                with Path(path).open("xb") as stream:
                    stream.write(bytes(buffer.data()))
            except Exception:
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")
