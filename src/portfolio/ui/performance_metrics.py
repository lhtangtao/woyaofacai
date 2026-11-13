"""每日与每月持仓收益共享统一指标组件（7 项核心收益指标 + 可折叠 5 项资金与交易指标）。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def _fmt_money(val: float | None, decimals: int = 2) -> str:
    if val is None:
        return "—"
    return f"{val:,.{decimals}f}"


def _fmt_signed_money(val: float | None, decimals: int = 2) -> str:
    if val is None:
        return "—"
    if abs(val) < 1e-9:
        return f"{0.0:,.{decimals}f}"
    sign = "+" if val > 0 else "-"
    return f"{sign}{abs(val):,.{decimals}f}"


class PerformanceMetricCard(QFrame):
    """统一持仓收益指标卡片，支持盈利/亏损/中性三种状态及点击下钻高亮。"""

    clicked = Signal(str)

    def __init__(self, key: str, title: str, *, clickable: bool = True, compact: bool = False, parent=None):
        super().__init__(parent)
        self.key = key
        self.clickable = clickable
        self._selected = False
        self._state = "neutral"

        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(74 if compact else 84)
        self.setObjectName("perfMetricCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(3)

        self.title_lbl = QLabel(title, self)
        self.title_lbl.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 600; border: none; background: transparent;")
        layout.addWidget(self.title_lbl)

        self.val_lbl = QLabel("—", self)
        self.value_label = self.val_lbl
        font = QFont(".AppleSystemUIFont", 15 if compact else 16)
        font.setBold(True)
        self.val_lbl.setFont(font)
        self.val_lbl.setStyleSheet("color: #0F172A; border: none; background: transparent;")
        layout.addWidget(self.val_lbl)

        self.sub_lbl = QLabel("", self)
        self.sub_label = self.sub_lbl
        self.sub_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; border: none; background: transparent;")
        layout.addWidget(self.sub_lbl)

        self._apply_style()

    def set_data(self, value_text: str, sub_text: str = "", state: str = "neutral"):
        self._state = state or "neutral"
        self.val_lbl.setText(value_text if value_text else "—")
        self.sub_lbl.setText(sub_text or "")
        self.sub_lbl.setVisible(bool(sub_text))
        color = "#16A34A" if self._state == "profit" else "#DC2626" if self._state == "loss" else "#0F172A"
        self.val_lbl.setStyleSheet(f"color: {color}; border: none; background: transparent;")

    def set_selected(self, selected: bool):
        if not self.clickable:
            return
        self._selected = bool(selected)
        self._apply_style()

    def _apply_style(self):
        if self._selected:
            self.setStyleSheet("""
                QFrame#perfMetricCard {
                    background: #EFF6FF;
                    border: 1.5px solid #2563EB;
                    border-radius: 10px;
                }
            """)
        elif self.clickable:
            self.setStyleSheet("""
                QFrame#perfMetricCard {
                    background: #FFFFFF;
                    border: 1px solid #E2E8F0;
                    border-radius: 10px;
                }
                QFrame#perfMetricCard:hover {
                    border-color: #93C5FD;
                    background: #F8FAFC;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#perfMetricCard {
                    background: #FFFFFF;
                    border: 1px solid #E2E8F0;
                    border-radius: 10px;
                }
            """)

    def mousePressEvent(self, event):
        if self.clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


class UnifiedPerformanceMetrics(QWidget):
    """每日/每月持仓收益统一指标区域：7 项核心收益指标（常驻）+ 5 项资金与交易指标（默认折叠）。"""

    cardClicked = Signal(str)
    _shared_fund_expanded: bool = True

    def __init__(self, *, period_unit: str = "day", parent=None):
        super().__init__(parent)
        if period_unit not in ("day", "month"):
            raise ValueError(f"Unsupported period_unit: {period_unit}")
        self.period_unit = period_unit
        self._is_day = period_unit == "day"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # 第一层：7 个核心收益指标（默认展开）
        core_grid = QGridLayout()
        core_grid.setContentsMargins(0, 0, 0, 0)
        core_grid.setSpacing(8)

        period_key = "days" if self._is_day else "months"
        core_specs = (
            ("total", "期间总收益"),
            (period_key, "统计天数" if self._is_day else "统计月数"),
            ("avg", "日均收益" if self._is_day else "月均收益"),
            ("up", "盈利天数" if self._is_day else "盈利月数"),
            ("down", "亏损天数" if self._is_day else "亏损月数"),
            ("avg_profit", "平均盈利"),
            ("avg_loss", "平均亏损"),
        )
        self.kpi_cards: dict[str, PerformanceMetricCard] = {}
        for col, (key, title) in enumerate(core_specs):
            card = PerformanceMetricCard(key, title, clickable=True, compact=False, parent=self)
            card.clicked.connect(self.cardClicked.emit)
            self.kpi_cards[key] = card
            core_grid.addWidget(card, 0, col)
            core_grid.setColumnStretch(col, 1)
        root.addLayout(core_grid)

        # 第二层：5 项资金与交易指标（直接展开展示，不再占用折叠按钮行）
        self.fund_toggle_btn = QPushButton(self)
        self.fund_toggle_btn.hide()

        self.fund_container = QWidget(self)
        fund_grid = QGridLayout(self.fund_container)
        fund_grid.setContentsMargins(0, 0, 0, 0)
        fund_grid.setSpacing(8)
        fund_specs = (
            ("buy", "期间买入金额"),
            ("sell", "期间卖出金额"),
            ("net_flow", "期间净投入"),
            ("end_net", "期末累计净投入"),
            ("ops", "账本操作笔数"),
        )
        self.fund_cards: dict[str, PerformanceMetricCard] = {}
        for col, (key, title) in enumerate(fund_specs):
            card = PerformanceMetricCard(key, title, clickable=False, compact=True, parent=self.fund_container)
            self.fund_cards[key] = card
            fund_grid.addWidget(card, 0, col)
            fund_grid.setColumnStretch(col, 1)
        root.addWidget(self.fund_container)

        self._sync_fund_visibility()

    def _on_toggle_fund_section(self):
        UnifiedPerformanceMetrics._shared_fund_expanded = self.fund_toggle_btn.isChecked()
        self._sync_fund_visibility()

    def set_fund_expanded(self, expanded: bool):
        UnifiedPerformanceMetrics._shared_fund_expanded = bool(expanded)
        self.fund_toggle_btn.setChecked(bool(expanded))
        self._sync_fund_visibility()

    def _sync_fund_visibility(self):
        self.fund_toggle_btn.hide()
        self.fund_container.setVisible(True)

    def sync_shared_expansion(self):
        """切换页面时同步展开/折叠偏好。"""
        self._sync_fund_visibility()

    def update_metrics(
        self,
        *,
        total_pnl: float | None,
        period_count: int,
        first_period: Optional[str],
        last_period: Optional[str],
        up_count: int,
        down_count: int,
        up_pnl_sum: float,
        down_pnl_sum: float,
        buy_total: float | None,
        sell_total: float | None,
        net_inflow: float | None,
        end_cum_net: float | None,
        op_count: int | None,
    ):
        unit_lbl = "天" if self._is_day else "个月"
        unit_short = "天" if self._is_day else "月"
        period_key = "days" if self._is_day else "months"

        if period_count > 0 and total_pnl is not None:
            total_state = "profit" if total_pnl > 0 else "loss" if total_pnl < 0 else "neutral"
            self.kpi_cards["total"].set_data(_fmt_signed_money(total_pnl), "区间累计收益", total_state)
            self.kpi_cards[period_key].set_data(
                f"{period_count} {unit_lbl}",
                f"{first_period or '—'} 至 {last_period or '—'}",
                "neutral",
            )
            avg_pnl = total_pnl / period_count
            avg_state = "profit" if avg_pnl > 0 else "loss" if avg_pnl < 0 else "neutral"
            self.kpi_cards["avg"].set_data(
                _fmt_signed_money(avg_pnl),
                "日均净收益额" if self._is_day else "月均净收益额",
                avg_state,
            )
            up_pct = (up_count / period_count) * 100.0
            dn_pct = (down_count / period_count) * 100.0
            self.kpi_cards["up"].set_data(f"{up_count} {unit_lbl}", f"占比 {up_pct:.1f}%", "profit")
            self.kpi_cards["down"].set_data(f"{down_count} {unit_lbl}", f"占比 {dn_pct:.1f}%", "loss")

            if up_count > 0:
                avg_up = up_pnl_sum / up_count
                self.kpi_cards["avg_profit"].set_data(
                    _fmt_signed_money(avg_up),
                    f"盈利{unit_short}均值 ({up_count}{unit_short})",
                    "profit",
                )
            else:
                self.kpi_cards["avg_profit"].set_data("—", f"暂无盈利{unit_short}", "neutral")

            if down_count > 0:
                avg_dn = down_pnl_sum / down_count
                self.kpi_cards["avg_loss"].set_data(
                    _fmt_signed_money(avg_dn),
                    f"亏损{unit_short}均值 ({down_count}{unit_short})",
                    "loss",
                )
            else:
                self.kpi_cards["avg_loss"].set_data("—", f"暂无亏损{unit_short}", "neutral")
        else:
            self.kpi_cards["total"].set_data("—", "区间累计收益", "neutral")
            self.kpi_cards[period_key].set_data("—", "— 至 —", "neutral")
            self.kpi_cards["avg"].set_data("—", "日均净收益额" if self._is_day else "月均净收益额", "neutral")
            self.kpi_cards["up"].set_data("—", "占比 —", "neutral")
            self.kpi_cards["down"].set_data("—", "占比 —", "neutral")
            self.kpi_cards["avg_profit"].set_data("—", f"暂无盈利{unit_short}", "neutral")
            self.kpi_cards["avg_loss"].set_data("—", f"暂无亏损{unit_short}", "neutral")

        # 第二层：5 项资金与交易指标
        self.fund_cards["buy"].set_data(_fmt_money(buy_total, 2), "筛选期间买入总额", "neutral")
        self.fund_cards["sell"].set_data(_fmt_money(sell_total, 2), "筛选期间卖出总额", "neutral")
        if net_inflow is not None:
            nf_state = "profit" if net_inflow > 0 else "loss" if net_inflow < 0 else "neutral"
            self.fund_cards["net_flow"].set_data(_fmt_signed_money(net_inflow, 2), "期间买入减卖出", nf_state)
        else:
            self.fund_cards["net_flow"].set_data("—", "期间买入减卖出", "neutral")
        self.fund_cards["end_net"].set_data(_fmt_money(end_cum_net, 2), "筛选期末累计余额", "neutral")
        self.fund_cards["ops"].set_data(f"{int(op_count):,}" if op_count is not None else "—", "期间有效操作记录", "neutral")
