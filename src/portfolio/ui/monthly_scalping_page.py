# -*- coding: utf-8 -*-
"""
Monthly Scalping Performance Page V3.0 (每月微操收益页面 V3.0)
专业主动交易微操（同自然月内买卖配对）分析页面：
1. 明确微操统计口径：仅统计同一自然月内完成买卖配对的交易，跨月交易不计入微操收益；
2. 取消全周期微操收益汇总；
3. 顶部增加浅蓝色微操统计口径说明卡片；
4. 优化月度微操收益趋势柱状图（盈利柱向上标注金额，亏损柱向下标注金额，无配对灰线标识，选中月份高亮）；
5. 趋势图右侧增加「选中月份微操数据摘要」卡片（月内微操收益 / 微操次数 / 微操数量）；
6. 两个月度股票 TOP 5 排行榜（月内微操收益 TOP 5 & 月内微操亏损 TOP 5，仅看已选月份）；
7. 增加「当前浮动盈亏」独立展示列（仅供辅助参考，不参与微操收益计算与排序）；
8. 16 列微操明细表格（含当前浮动盈亏与说明列），支持全列排序、分页、行点击抽屉详情与双击交易回放。
"""

import csv
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableView,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


def numeric_font(size: int = 12, bold: bool = False) -> QFont:
    """金融数字等宽字体"""
    font = QFont(".AppleSystemUIFont", size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    if bold:
        font.setWeight(QFont.Weight.Bold)
    return font


def fmt_money(val: Any, digits: int = 2) -> str:
    if val is None or val == "":
        return "—"
    try:
        f = float(val)
        return f"{f:,.{digits}f}"
    except (ValueError, TypeError):
        return str(val)


def fmt_signed_money(val: Any, digits: int = 2) -> str:
    if val is None or val == "":
        return "—"
    try:
        f = float(val)
        if abs(f) < 1e-9:
            return f"$0.{'0' * digits}"
        sign = "+" if f > 0 else "-"
        return f"{sign}${abs(f):,.{digits}f}"
    except (ValueError, TypeError):
        return str(val)


def fmt_broker_cn(name: Any) -> str:
    if not name:
        return "—"
    s = str(name).strip()
    upper = s.upper()
    if upper in ("LONGBRIDGE", "LONG BRIDGE", "长桥证券"):
        return "长桥"
    if upper in ("FUTU", "富途证券", "富途"):
        return "富途"
    if upper in ("TIGER", "老虎证券", "老虎"):
        return "老虎"
    if upper in ("FIRSTTRADE", "FIRST TRADE", "第一证券", "第一"):
        return "第一证券"
    if upper in ("IBKR", "IB", "INTERACTIVE BROKERS", "盈透证券", "盈透"):
        return "盈透"
    if upper in ("SCHWAB", "CHARLES SCHWAB", "嘉信理财", "嘉信"):
        return "嘉信"
    if upper in ("BBAE",):
        return "BBAE"
    return s


def calc_weighted_spread(rows: List[Tuple]) -> Tuple[Optional[float], str]:
    """
    加权平均每股价差 = Σ(每股价差 × 微操数量) / Σ(微操数量)
    仅对有效且微操数量 > 0 的记录进行加权
    """
    total_spread_qty_product = 0.0
    total_qty = 0.0
    for r in rows:
        try:
            qty = float(r[5] or 0)
            spread = float(r[8] or 0)
            if qty > 0:
                total_spread_qty_product += spread * qty
                total_qty += qty
        except (ValueError, TypeError):
            continue

    if total_qty > 0:
        val = total_spread_qty_product / total_qty
        sign = "+" if val > 0 else "-"
        return val, f"{sign}${abs(val):,.2f}"
    return None, "—"


# -------------------------------------------------------------------------
# 1. 选中月份微操数据摘要卡片 (MonthSummaryCard)
# -------------------------------------------------------------------------
class MonthSummaryCard(QFrame):
    """
    展示当前选中月份的微操关键指标与质量诊断：
    1. 微操净收益（正绿负红，无配对显示 —）、微操次数、微操数量
    2. 正T（先买后卖）与反T（先卖后买）收益拆分
    3. 胜率、盈亏比、平均持仓交易日
    4. 超时未配对纠偏（正T转长持 / 反T未接回）
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("monthSummaryCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("""
            QFrame#monthSummaryCard {
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
            }
        """)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(16, 12, 16, 12)
        vbox.setSpacing(8)

        # 顶部标题：📅 已选月份：YYYY-MM
        top_h = QHBoxLayout()
        top_h.setContentsMargins(0, 0, 0, 0)
        top_h.setSpacing(6)

        icon_lbl = QLabel("📅")
        icon_lbl.setStyleSheet("font-size: 13px;")
        top_h.addWidget(icon_lbl)

        self.title_lbl = QLabel("已选月份：—")
        self.title_lbl.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet("color: #0F172A;")
        top_h.addWidget(self.title_lbl)
        top_h.addStretch()

        self.mode_badge = QLabel("≤5交易日")
        self.mode_badge.setStyleSheet(
            "background: #EFF6FF; color: #1D4ED8; font-size: 10.5px; font-weight: 600; "
            "padding: 2px 7px; border-radius: 4px;"
        )
        top_h.addWidget(self.mode_badge)
        vbox.addLayout(top_h)

        # 3 个核心指标横向并排
        grid = QHBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)

        col1 = QVBoxLayout()
        col1.setSpacing(2)
        self.lbl_pnl_title = QLabel("微操净收益")
        self.lbl_pnl_title.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
        col1.addWidget(self.lbl_pnl_title)

        self.val_pnl = QLabel("—")
        self.val_pnl.setFont(numeric_font(18, bold=True))
        self.val_pnl.setStyleSheet("color: #0F172A;")
        col1.addWidget(self.val_pnl)
        grid.addLayout(col1, 1)

        col2 = QVBoxLayout()
        col2.setSpacing(2)
        lbl_cnt_title = QLabel("微操次数")
        lbl_cnt_title.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
        col2.addWidget(lbl_cnt_title)

        self.val_trades = QLabel("0")
        self.val_trades.setFont(numeric_font(18, bold=True))
        self.val_trades.setStyleSheet("color: #0F172A;")
        col2.addWidget(self.val_trades)
        grid.addLayout(col2, 1)

        col3 = QVBoxLayout()
        col3.setSpacing(2)
        lbl_qty_title = QLabel("微操数量")
        lbl_qty_title.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
        col3.addWidget(lbl_qty_title)

        self.val_qty = QLabel("0.00")
        self.val_qty.setFont(numeric_font(18, bold=True))
        self.val_qty.setStyleSheet("color: #0F172A;")
        col3.addWidget(self.val_qty)
        grid.addLayout(col3, 1)

        vbox.addLayout(grid)

        # 分隔线
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("background-color: #E2E8F0; max-height: 1px; border: none;")
        vbox.addWidget(div)

        # 实战质量诊断区（正T/反T、胜率/盈亏比/持仓天数、超时未配对纠偏）
        self.diag_widget = QWidget()
        diag_lay = QVBoxLayout(self.diag_widget)
        diag_lay.setContentsMargins(0, 0, 0, 0)
        diag_lay.setSpacing(6)

        split_row = QHBoxLayout()
        split_row.setSpacing(8)
        self.pos_t_lbl = QLabel("正T(先买后卖): —")
        self.pos_t_lbl.setStyleSheet("color: #334155; font-size: 11.5px; font-weight: 500;")
        split_row.addWidget(self.pos_t_lbl, 1)

        self.rev_t_lbl = QLabel("反T(先卖后买): —")
        self.rev_t_lbl.setStyleSheet("color: #334155; font-size: 11.5px; font-weight: 500;")
        split_row.addWidget(self.rev_t_lbl, 1)
        diag_lay.addLayout(split_row)

        self.quality_lbl = QLabel("胜率: —  ·  盈亏比: —  ·  均持: —")
        self.quality_lbl.setStyleSheet("color: #475569; font-size: 11.5px;")
        diag_lay.addWidget(self.quality_lbl)

        self.unpaired_lbl = QLabel("防幸存者偏差：本月无超时未配对记录")
        self.unpaired_lbl.setStyleSheet(
            "background: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; "
            "border-radius: 4px; padding: 4px 8px; font-size: 11px;"
        )
        self.unpaired_lbl.setWordWrap(True)
        diag_lay.addWidget(self.unpaired_lbl)

        vbox.addWidget(self.diag_widget)
        vbox.addStretch()

    def set_month_data(
        self,
        month: str,
        pnl: Optional[float],
        trades: int,
        qty: float,
        has_pair: bool,
        *,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.title_lbl.setText(f"已选月份：{month}" if month else "已选月份：全周期汇总")
        extra = extra or {}
        mode_label = extra.get("mode_label", "")
        if mode_label:
            self.mode_badge.setText(mode_label)
            self.mode_badge.show()
        else:
            self.mode_badge.hide()

        is_compat = extra.get("is_compat", False)
        self.lbl_pnl_title.setText("月内微操收益" if is_compat else "微操净收益")

        if not has_pair or pnl is None:
            self.val_pnl.setText("—")
            self.val_pnl.setStyleSheet("color: #94A3B8;")
            self.val_trades.setText("0")
            self.val_qty.setText("0.00")
        else:
            self.val_pnl.setText(fmt_signed_money(pnl, 2))
            if pnl > 0:
                self.val_pnl.setStyleSheet("color: #10B981;")
            elif pnl < 0:
                self.val_pnl.setStyleSheet("color: #EF4444;")
            else:
                self.val_pnl.setStyleSheet("color: #0F172A;")
            self.val_trades.setText(str(trades))
            self.val_qty.setText(fmt_money(qty, 2))

        if is_compat:
            self.pos_t_lbl.setText("口径：同自然月 LIFO/FIFO 兼容")
            self.rev_t_lbl.setText("跨月交易不计入")
            self.quality_lbl.setText("可切回「超短线 ≤5天」查看跨月做T、正反T拆分与胜率诊断")
            self.unpaired_lbl.hide()
            return

        pos_cnt = int(extra.get("pos_t_count", 0))
        pos_pnl = float(extra.get("pos_t_pnl", 0.0))
        rev_cnt = int(extra.get("rev_t_count", 0))
        rev_pnl = float(extra.get("rev_t_pnl", 0.0))
        pos_color = "#10B981" if pos_pnl > 0 else "#EF4444" if pos_pnl < 0 else "#475569"
        rev_color = "#10B981" if rev_pnl > 0 else "#EF4444" if rev_pnl < 0 else "#475569"
        pos_txt = f"{fmt_signed_money(pos_pnl, 2)} ({pos_cnt}笔)" if pos_cnt else "0笔"
        rev_txt = f"{fmt_signed_money(rev_pnl, 2)} ({rev_cnt}笔)" if rev_cnt else "0笔"
        self.pos_t_lbl.setText(f"正T: <b style='color:{pos_color};'>{pos_txt}</b>")
        self.rev_t_lbl.setText(f"反T: <b style='color:{rev_color};'>{rev_txt}</b>")

        wins = int(extra.get("win_count", 0))
        losses = int(extra.get("loss_count", 0))
        total_p = int(extra.get("pair_count", 0))
        win_rate = f"{(wins / total_p * 100):.1f}% ({wins}胜/{losses}负)" if total_p > 0 else "—"
        pl_ratio_val = extra.get("pl_ratio")
        pl_ratio_str = f"{pl_ratio_val:.2f}" if pl_ratio_val is not None else "—"
        avg_days = extra.get("avg_hold_days")
        avg_days_str = f"{avg_days:.1f}天" if avg_days is not None and total_p > 0 else "—"
        cross_cnt = int(extra.get("cross_month_count", 0))
        cross_hint = f" · 跨月{cross_cnt}笔" if cross_cnt else ""
        self.quality_lbl.setText(f"胜率 {win_rate} · 盈亏比 {pl_ratio_str} · 均持 {avg_days_str}{cross_hint}")

        exp_b_cnt = int(extra.get("expired_buy_count", 0))
        exp_b_amt = float(extra.get("expired_buy_amount", 0.0))
        exp_s_cnt = int(extra.get("expired_sell_count", 0))
        exp_s_amt = float(extra.get("expired_sell_amount", 0.0))
        self.unpaired_lbl.show()
        if exp_b_cnt or exp_s_cnt:
            self.unpaired_lbl.setText(
                f"未配对纠偏：正T转长持 {exp_b_cnt} 笔 (${fmt_money(exp_b_amt, 0)}) · "
                f"反T未接回 {exp_s_cnt} 笔 (${fmt_money(exp_s_amt, 0)})"
            )
            self.unpaired_lbl.setStyleSheet(
                "background: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; "
                "border-radius: 4px; padding: 4px 8px; font-size: 11px;"
            )
        else:
            self.unpaired_lbl.setText("未配对纠偏：本月无超时转长持或未接回记录")
            self.unpaired_lbl.setStyleSheet(
                "background: #F8FAFC; color: #64748B; border: 1px solid #E2E8F0; "
                "border-radius: 4px; padding: 4px 8px; font-size: 11px;"
            )


# -------------------------------------------------------------------------
# 2. 月度微操收益趋势图画布 (MonthlyScalpingTrendCanvas V3.0)
# -------------------------------------------------------------------------
class MonthlyScalpingTrendCanvas(QWidget):
    """
    月度微操收益趋势柱状图：
    - 横坐标：月份（如 2026-01 ~ 2026-11）
    - 纵坐标：月内微操收益金额
    - 盈利柱向上，柱顶标出金额（如 +$1,280.50）；
    - 亏损柱向下，柱底标出金额（如 -$1,536.20）；
    - 无配对月份显示浅灰状态标识，Tooltip 提示「本月无完成的月内买卖配对」；
    - 点击柱体选中月份，浅蓝半透明高亮柱，并联动右侧摘要、排行榜与明细。
    """
    month_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # points: list of dict:
        # {"month": str, "pnl": float, "trades": int, "qty": float, "profit_cnt": int, "loss_cnt": int, "has_pair": bool}
        self.points_info: List[Dict[str, Any]] = []
        self.selected_month = ""
        self.hovered_index = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    @property
    def data_points(self):
        """向后兼容属性"""
        return [(p["month"], p["pnl"], 0.0) for p in self.points_info]

    def set_data(self, points: List[Dict[str, Any]], selected_month: str = ""):
        self.points_info = points
        self.selected_month = selected_month
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor("#FFFFFF"))

        if not self.points_info:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无月度微操收益趋势数据")
            return

        margin_left = 65
        margin_right = 24
        margin_top = 34
        margin_bottom = 32

        plot_w = w - margin_left - margin_right
        plot_h = h - margin_top - margin_bottom

        if plot_w <= 10 or plot_h <= 10:
            return

        valid_pnls = [p["pnl"] for p in self.points_info if p["has_pair"]]
        if not valid_pnls:
            valid_pnls = [0.0]

        raw_min = min(valid_pnls)
        raw_max = max(valid_pnls)

        # 对称或留足柱顶金额文本空间的极值
        max_abs = max(abs(raw_max), abs(raw_min), 50.0) * 1.35
        max_val = max_abs
        min_val = -max_abs
        val_range = max(1e-5, max_val - min_val)

        def to_y(v):
            ratio = (v - min_val) / val_range
            return margin_top + plot_h * (1.0 - ratio)

        zero_y = to_y(0.0)
        zero_y = max(margin_top, min(margin_top + plot_h, zero_y))

        # 1. 绘制网格虚线与 Y 轴刻度
        painter.setPen(QPen(QColor("#F1F5F9"), 1, Qt.PenStyle.DashLine))
        y_ticks = 4
        for i in range(y_ticks + 1):
            cur_v = min_val + (val_range / y_ticks) * i
            cur_y = to_y(cur_v)
            painter.drawLine(margin_left, int(cur_y), margin_left + plot_w, int(cur_y))

            painter.setPen(QColor("#94A3B8"))
            painter.setFont(numeric_font(9))
            lbl_str = f"${cur_v:,.0f}" if cur_v >= 0 else f"-${abs(cur_v):,.0f}"
            painter.drawText(QRectF(0, cur_y - 8, margin_left - 8, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, lbl_str)
            painter.setPen(QPen(QColor("#F1F5F9"), 1, Qt.PenStyle.DashLine))

        # 2. 绘制 0 水平基准线
        painter.setPen(QPen(QColor("#CBD5E1"), 1.2, Qt.PenStyle.SolidLine))
        painter.drawLine(margin_left, int(zero_y), margin_left + plot_w, int(zero_y))

        # 3. 绘制柱体、数值标签与选中高亮
        n = len(self.points_info)
        slot_w = plot_w / n
        bar_w = max(4.0, min(24.0, slot_w * 0.55))

        for i, info in enumerate(self.points_info):
            m = info["month"]
            pnl = info["pnl"]
            has_pair = info["has_pair"]
            cx = margin_left + slot_w * (i + 0.5)
            rx = cx - bar_w / 2.0

            is_sel = (m == self.selected_month)
            is_hover = (i == self.hovered_index)

            # 选中状态：整列绘制浅蓝色半透明高亮背景
            if is_sel:
                painter.fillRect(QRectF(cx - slot_w * 0.48, margin_top, slot_w * 0.96, plot_h),
                                 QColor(239, 246, 255, 210))
                painter.setPen(QPen(QColor("#BFDBFE"), 1.0, Qt.PenStyle.DashLine))
                painter.drawRect(QRectF(cx - slot_w * 0.48, margin_top, slot_w * 0.96, plot_h))

            if has_pair:
                vy = to_y(pnl)
                top_y = min(vy, zero_y)
                bot_y = max(vy, zero_y)
                bar_h = max(2.5, bot_y - top_y)

                if pnl >= 0:
                    fill_col = QColor("#10B981") if not is_hover else QColor("#059669")
                else:
                    fill_col = QColor("#EF4444") if not is_hover else QColor("#DC2626")

                painter.fillRect(QRectF(rx, top_y, bar_w, bar_h), fill_col)

                if is_sel:
                    painter.setPen(QPen(QColor("#2563EB"), 2.0))
                    painter.drawRect(QRectF(rx - 1, top_y - 1, bar_w + 2, bar_h + 2))

                # 柱顶/柱底金额标签
                val_txt = fmt_signed_money(pnl, 2)
                painter.setFont(numeric_font(9, bold=True))
                if pnl >= 0:
                    painter.setPen(QColor("#10B981" if not is_sel else "#047857"))
                    txt_rect = QRectF(cx - 50, top_y - 18, 100, 16)
                    painter.drawText(txt_rect, Qt.AlignmentFlag.AlignCenter, val_txt)
                else:
                    painter.setPen(QColor("#EF4444" if not is_sel else "#B91C1C"))
                    txt_rect = QRectF(cx - 50, bot_y + 3, 100, 16)
                    painter.drawText(txt_rect, Qt.AlignmentFlag.AlignCenter, val_txt)
            else:
                # 无月内配对：在 0 轴上绘制浅灰色短横指示条
                painter.setPen(QPen(QColor("#CBD5E1"), 2.5, Qt.PenStyle.SolidLine))
                painter.drawLine(int(rx), int(zero_y), int(rx + bar_w), int(zero_y))

            # X 轴月份标签
            step = max(1, n // 14)
            if i % step == 0 or i == n - 1 or is_sel:
                painter.setPen(QColor("#2563EB" if is_sel else "#64748B"))
                painter.setFont(numeric_font(9, bold=is_sel))
                lbl_m = m if len(m) <= 7 else m[2:]
                painter.drawText(QRectF(cx - slot_w / 2.0, margin_top + plot_h + 4, slot_w, 20),
                                 Qt.AlignmentFlag.AlignCenter, lbl_m)

    def mouseMoveEvent(self, event):
        w = self.width()
        margin_left = 65
        margin_right = 24
        plot_w = w - margin_left - margin_right
        n = len(self.points_info)

        if n > 0 and margin_left <= event.position().x() <= margin_left + plot_w:
            slot_w = plot_w / n
            idx = int((event.position().x() - margin_left) / slot_w)
            idx = max(0, min(n - 1, idx))
            if idx != self.hovered_index:
                self.hovered_index = idx
                self.update()

            info = self.points_info[idx]
            m = info["month"]
            if info["has_pair"]:
                tip = (
                    f"月份: {m}\n"
                    f"月内微操收益: {fmt_signed_money(info['pnl'], 2)}\n"
                    f"微操次数: {info['trades']} 次\n"
                    f"微操数量: {fmt_money(info['qty'], 2)} 股\n"
                    f"盈利股票: {info['profit_cnt']} 只   亏损股票: {info['loss_cnt']} 只\n"
                    f"配对状态: 已完成配对\n"
                    f"(点击柱体筛选该月份明细与排行榜)"
                )
            else:
                tip = (
                    f"月份: {m}\n"
                    f"状态: 本月无完成的月内买卖配对\n"
                    f"说明: 仅有单边买入或卖出，不计入微操收益"
                )
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            if self.hovered_index != -1:
                self.hovered_index = -1
                self.update()
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hovered_index = -1
        self.update()
        QToolTip.hideText()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.points_info:
            w = self.width()
            margin_left = 65
            margin_right = 24
            plot_w = w - margin_left - margin_right
            n = len(self.points_info)
            if margin_left <= event.position().x() <= margin_left + plot_w:
                slot_w = plot_w / n
                idx = int((event.position().x() - margin_left) / slot_w)
                idx = max(0, min(n - 1, idx))
                clicked_m = self.points_info[idx]["month"]
                if self.selected_month != clicked_m:
                    self.selected_month = clicked_m
                    self.month_selected.emit(clicked_m)
                    self.update()
                # 再次点击同一月份时，相当于没有点击，保持单月选中状态不变
        super().mousePressEvent(event)


# -------------------------------------------------------------------------
# 3. 现代微操明细表格模型 (ModernScalpingTableModel V3.0 - 16列)
# -------------------------------------------------------------------------
class ModernScalpingTableModel(QAbstractTableModel):
    """
    16 列微操明细表格模型：
    0: 月份
    1: 渠道
    2: 分类
    3: Ticker
    4: 微操次数
    5: 微操数量
    6: 微操均买价($)
    7: 微操均卖价($)
    8: 每股价差($)
    9: 买入金额($)
    10: 卖出金额($)
    11: 月内微操收益($)
    12: 首笔微操时间
    13: 最近微操时间
    14: 当前浮动盈亏($)
    15: 说明
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns: Tuple[str, ...] = (
            "月份", "渠道", "分类", "Ticker", "微操次数", "微操数量",
            "微操均买价($)", "微操均卖价($)", "每股价差($)", "买入金额($)", "卖出金额($)", "月内微操收益($)",
            "首笔微操时间", "最近微操时间", "当前浮动盈亏($)", "说明"
        )
        self.all_rows: List[List[Any]] = []
        self.rows: List[List[Any]] = []
        self.page = 1
        self.page_size = 50
        self.sort_column: int = 0
        self.sort_order: Qt.SortOrder = Qt.SortOrder.DescendingOrder
        self.highlight_ticker: str = ""
        self.highlight_month: str = ""

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if 0 <= section < len(self.columns):
                col_name = self.columns[section]
                if self.sort_column == section:
                    arrow = " ▲" if self.sort_order == Qt.SortOrder.AscendingOrder else " ▼"
                    return f"{col_name}{arrow}"
                return col_name
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return str((self.page - 1) * self.page_size + section + 1)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if r >= len(self.rows) or c >= len(self.columns):
            return None

        val = self.rows[r][c]
        val_str = str(val) if val is not None else "—"
        col_name = self.columns[c]

        if role == Qt.ItemDataRole.DisplayRole:
            return val_str

        if role == Qt.ItemDataRole.BackgroundRole:
            row_data = self.rows[r]
            is_hl_m = self.highlight_month and len(row_data) > 0 and row_data[0] == self.highlight_month
            is_hl_t = self.highlight_ticker and len(row_data) > 3 and row_data[3] == self.highlight_ticker
            if is_hl_m or is_hl_t:
                return QColor("#EFF6FF")
            return None

        # 对齐规则：文本靠左，代码/时间/说明居中，金额与数量靠右
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col_name in ("月份", "渠道", "分类"):
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if col_name in ("Ticker", "首笔微操时间", "最近微操时间", "说明"):
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 颜色渲染：盈绿亏红
        if role == Qt.ItemDataRole.ForegroundRole:
            if col_name == "Ticker":
                return QColor("#2563EB")
            if col_name in ("每股价差($)", "月内微操收益($)", "微操净收益($)", "当前浮动盈亏($)"):
                if val_str.startswith("+"):
                    return QColor("#10B981")
                if val_str.startswith("-") or val_str.startswith("−"):
                    return QColor("#EF4444")
            if col_name == "说明":
                if "⚠" in val_str or "转长持" in val_str:
                    return QColor("#B45309")
                return QColor("#059669")
            if val_str in ("$0.00", "0.00", "0", "—"):
                return QColor("#64748B")
            return QColor("#1E293B")

        if role == Qt.ItemDataRole.FontRole:
            if col_name == "Ticker":
                return numeric_font(12, bold=True)
            if col_name in ("微操数量", "微操均买价($)", "微操均卖价($)", "每股价差($)", "买入金额($)", "卖出金额($)", "月内微操收益($)", "微操净收益($)", "当前浮动盈亏($)"):
                bold = col_name in ("月内微操收益($)", "微操净收益($)", "当前浮动盈亏($)")
                return numeric_font(12, bold=bold)
            return QFont(".AppleSystemUIFont", 12)

        return None

    def _parse_sort_key(self, row: List[Any], col: int):
        if col >= len(row):
            return ""
        val = row[col]
        if val is None or val == "—":
            return -float("inf")
        s = str(val).strip()
        clean = s.replace("$", "").replace(",", "").replace("%", "").replace("+", "").replace("−", "-")
        try:
            return float(clean)
        except ValueError:
            return s

    def apply_sort_and_page(self):
        self.beginResetModel()
        if self.sort_column == 0 and self.sort_order == Qt.SortOrder.DescendingOrder:
            # 严格三级稳定排序：月份降序 -> 收益降序 -> Ticker 升序
            self.all_rows.sort(key=lambda r: str(r[3] or ""))
            self.all_rows.sort(key=lambda r: self._parse_sort_key(r, 11), reverse=True)
            self.all_rows.sort(key=lambda r: str(r[0] or ""), reverse=True)
        elif self.sort_column >= 0:
            rev = (self.sort_order == Qt.SortOrder.DescendingOrder)
            self.all_rows.sort(key=lambda r: self._parse_sort_key(r, self.sort_column), reverse=rev)

        start = (self.page - 1) * self.page_size
        end = start + self.page_size
        self.rows = self.all_rows[start:end]
        self.endResetModel()

    def set_columns_and_rows(self, cols: Tuple[str, ...], rows: List[List[Any]]):
        self.columns = cols
        self.all_rows = list(rows)
        self.apply_sort_and_page()

    def toggle_sort(self, column: int):
        if column < 0 or column >= len(self.columns):
            return
        if self.sort_column == column:
            self.sort_order = Qt.SortOrder.DescendingOrder if self.sort_order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        else:
            self.sort_column = column
            self.sort_order = Qt.SortOrder.DescendingOrder if column in (0, 4, 5, 6, 7, 8, 9, 10, 11, 14) else Qt.SortOrder.AscendingOrder
        self.page = 1
        self.apply_sort_and_page()

    def set_page(self, page: int):
        total_pages = max(1, (len(self.all_rows) + self.page_size - 1) // self.page_size)
        new_p = max(1, min(page, total_pages))
        if new_p != self.page:
            self.page = new_p
            self.apply_sort_and_page()


# -------------------------------------------------------------------------
# 4. 主页面 MonthlyScalpingPage (V3.0 专业重构)
# -------------------------------------------------------------------------
class MonthlyScalpingPage(QWidget):
    drill = Signal(str, str, object)  # 信号：下钻或历史回放

    MODE_SPECS = (
        ("SHORT_5", "超短线 ≤5天", "实战短线口径（≤5 交易日 · 支持跨月配对）：同一「人员·渠道·标的」按连续时间轴配对，收益归属平仓/接回日所在月份；严格区分正T（先买后卖）与反T（先卖后买），追踪超时转长持/未接回，并优先采用已核实真实手续费。"),
        ("SHORT_0", "日内 T+0", "日内 T+0 口径（同交易日内买卖配对）：仅统计同一交易日完成高抛低吸或低吸高抛的交易；严格拆分正T与反T，隔夜未平仓计入超时转长持/未接回，手续费优先采用已核实真实费用。"),
        ("SHORT_20", "短波段 ≤20天", "短波段口径（≤20 交易日 · 支持跨月配对）：适合复盘约一个月交易周期内的快进快出操作；归属平仓/接回日所在月份，拆出正T/反T收益与胜率，并追踪超过 20 个交易日未平仓/未接回记录。"),
        ("EXCEL_COMPAT", "月内兼容口径", "月内兼容口径（Excel 对账）：保留原自然月一刀切规则，仅统计同一自然月内完成买卖配对的交易；跨月持仓盈亏不计入微操收益，用于历史账本核对。"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.scalping_mode: str = "SHORT_5"
        self.active_bundle: Dict[str, Any] = {}
        self.raw_rows: List[Tuple] = []
        self.row_meta_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        self.unrealized_pnl_map: Dict[str, float] = {}  # ticker -> 当前持仓浮动盈亏
        self._unpaired_expanded: bool = False

        # 待查询与已生效筛选分离
        self.applied_person: Any = None
        self.applied_broker: str = ""
        self.applied_category: str = ""
        self.applied_ticker: str = ""
        self.applied_start_month: str = ""
        self.applied_end_month: str = ""

        # 选中的月份与股票
        self.active_chart_month: str = ""
        self.active_ranking_ticker: str = ""

        # UI 构建
        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: #F8FAFC; border: none; }")

        content_widget = QWidget()
        content_widget.setStyleSheet("background: #F8FAFC;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(18, 14, 18, 20)
        content_layout.setSpacing(12)

        # 1. 顶部 Header 控件（保留对象供外部调用或测试使用）
        self.title_lbl = QLabel("每月微操收益")
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.recalculate_and_refresh)

        # 2. 口径切换与说明卡片 (浅蓝色背景置顶展示)
        desc_card = QFrame()
        desc_card.setStyleSheet("""
            QFrame {
                background-color: #EFF6FF;
                border: 1px solid #BFDBFE;
                border-radius: 6px;
            }
        """)
        desc_vbox = QVBoxLayout(desc_card)
        desc_vbox.setContentsMargins(14, 8, 14, 8)
        desc_vbox.setSpacing(6)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_lbl = QLabel("微操统计口径")
        mode_lbl.setStyleSheet("color: #1E3A8A; font-size: 12px; font-weight: 700; border: none; background: transparent;")
        mode_row.addWidget(mode_lbl)

        self.mode_buttons: Dict[str, QPushButton] = {}
        for mode_key, label_text, _ in self.MODE_SPECS:
            btn = QPushButton(label_text)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked=False, mk=mode_key: self.set_scalping_mode(mk))
            self.mode_buttons[mode_key] = btn
            mode_row.addWidget(btn)
        mode_row.addStretch()
        desc_vbox.addLayout(mode_row)

        desc_layout = QHBoxLayout()
        desc_layout.setContentsMargins(0, 0, 0, 0)
        desc_layout.setSpacing(6)

        info_icon = QLabel("ℹ")
        info_icon.setStyleSheet("color: #2563EB; font-size: 13px; font-weight: bold; border: none; background: transparent;")
        desc_layout.addWidget(info_icon)

        self.desc_text = QLabel(self.MODE_SPECS[0][2])
        self.desc_text.setWordWrap(True)
        self.desc_text.setStyleSheet("color: #1E40AF; font-size: 11.5px; border: none; background: transparent;")
        desc_layout.addWidget(self.desc_text, 1)
        desc_vbox.addLayout(desc_layout)
        self._sync_mode_buttons()

        content_layout.addWidget(desc_card)

        # 3. 全局筛选卡片 (Filter Bar)
        filter_card = QFrame()
        filter_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        filter_layout = QHBoxLayout(filter_card)
        filter_layout.setContentsMargins(12, 8, 12, 8)
        filter_layout.setSpacing(8)

        # 归属人员
        lbl_p = QLabel("人员")
        lbl_p.setStyleSheet("color: #64748B; font-size: 12px; font-weight: 500;")
        filter_layout.addWidget(lbl_p)

        self.person_combo = QComboBox()
        self.person_combo.addItem("全部人员", None)
        self.person_combo.setFixedHeight(32)
        filter_layout.addWidget(self.person_combo)

        # 券商渠道
        lbl_b = QLabel("渠道")
        lbl_b.setStyleSheet("color: #64748B; font-size: 12px; font-weight: 500; margin-left: 4px;")
        filter_layout.addWidget(lbl_b)

        self.broker_combo = QComboBox()
        self.broker_combo.addItem("全部渠道", "")
        self.broker_combo.setFixedHeight(32)
        filter_layout.addWidget(self.broker_combo)

        # 股票分类
        lbl_c = QLabel("分类")
        lbl_c.setStyleSheet("color: #64748B; font-size: 12px; font-weight: 500; margin-left: 4px;")
        filter_layout.addWidget(lbl_c)

        self.category_combo = QComboBox()
        self.category_combo.addItem("全部分类", "")
        self.category_combo.setFixedHeight(32)
        filter_layout.addWidget(self.category_combo)

        # Ticker
        lbl_t = QLabel("Ticker")
        lbl_t.setStyleSheet("color: #64748B; font-size: 12px; font-weight: 500; margin-left: 4px;")
        filter_layout.addWidget(lbl_t)

        self.ticker_edit = QLineEdit()
        self.ticker_edit.setPlaceholderText("输入 Ticker")
        self.ticker_edit.setFixedHeight(32)
        self.ticker_edit.setFixedWidth(110)
        self.ticker_edit.setStyleSheet("QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 0 8px; font-size: 12px; }")
        self.ticker_edit.returnPressed.connect(self.on_query_clicked)
        filter_layout.addWidget(self.ticker_edit)

        # 开始月份
        lbl_sm = QLabel("开始月份")
        lbl_sm.setStyleSheet("color: #64748B; font-size: 12px; font-weight: 500; margin-left: 4px;")
        filter_layout.addWidget(lbl_sm)

        self.start_month_combo = QComboBox()
        self.start_month_combo.setFixedHeight(32)
        self.start_month_combo.setMinimumWidth(100)
        filter_layout.addWidget(self.start_month_combo)

        lbl_tilde = QLabel("至")
        lbl_tilde.setStyleSheet("color: #64748B; font-size: 12px;")
        filter_layout.addWidget(lbl_tilde)

        # 结束月份
        lbl_em = QLabel("结束月份")
        lbl_em.setStyleSheet("color: #64748B; font-size: 12px; font-weight: 500; margin-left: 4px;")
        filter_layout.addWidget(lbl_em)

        self.end_month_combo = QComboBox()
        self.end_month_combo.setFixedHeight(32)
        self.end_month_combo.setMinimumWidth(100)
        filter_layout.addWidget(self.end_month_combo)

        filter_layout.addStretch()

        # 重置与查询按钮
        self.reset_btn = QPushButton("重置")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setFixedHeight(32)
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background: #F1F5F9; color: #475569; font-weight: 600; font-size: 12px;
                border: 1px solid #CBD5E1; border-radius: 6px; padding: 0 14px;
            }
            QPushButton:hover { background: #E2E8F0; color: #0F172A; }
        """)
        self.reset_btn.clicked.connect(self.on_reset_clicked)
        filter_layout.addWidget(self.reset_btn)

        self.search_btn = QPushButton("查询")
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.setFixedHeight(32)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB; color: #FFFFFF; font-weight: 600; font-size: 12px;
                border: none; border-radius: 6px; padding: 0 18px;
            }
            QPushButton:hover { background: #1D4ED8; }
        """)
        self.search_btn.clicked.connect(self.on_query_clicked)
        filter_layout.addWidget(self.search_btn)

        content_layout.addWidget(filter_card)

        # 4. 生效条件 Chips 栏
        self.chips_bar = QFrame()
        self.chips_bar.setStyleSheet("background: transparent;")
        chips_layout = QHBoxLayout(self.chips_bar)
        chips_layout.setContentsMargins(4, 0, 4, 0)
        chips_layout.setSpacing(8)

        # 人员 Chip
        self.chip_person = self._create_chip("全部人员", self._clear_person_filter)
        chips_layout.addWidget(self.chip_person)

        # 渠道 Chip
        self.chip_broker = self._create_chip("全部渠道", self._clear_broker_filter)
        chips_layout.addWidget(self.chip_broker)

        # 分类 Chip
        self.chip_category = self._create_chip("全部分类", self._clear_category_filter)
        chips_layout.addWidget(self.chip_category)

        # 日期范围 Chip
        self.chip_date = self._create_chip("全周期", None)
        chips_layout.addWidget(self.chip_date)

        # 选中月份下钻 Chip
        self.chip_month_drill = self._create_chip("月份: —", lambda: self.on_trend_month_clicked(""))
        chips_layout.addWidget(self.chip_month_drill)
        self.chip_month_drill.hide()

        # 选中股票下钻 Chip
        self.chip_ticker_drill = self._create_chip("股票: —", lambda: self.filter_by_ticker(""))
        chips_layout.addWidget(self.chip_ticker_drill)
        self.chip_ticker_drill.hide()

        chips_layout.addStretch()
        # 根据用户明确要求，彻底隐藏不展示第二行 Chips 生效标签栏
        self.chips_bar.hide()

        # 5. 中部图表与选中月份摘要 (Splitter: 68% : 32%)
        self.mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.mid_splitter.setStyleSheet("QSplitter::handle { background: transparent; }")

        # 左侧：月度微操收益趋势卡片
        trend_card = QFrame()
        trend_card.setFixedHeight(260)
        trend_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        trend_vbox = QVBoxLayout(trend_card)
        trend_vbox.setContentsMargins(14, 10, 14, 10)
        trend_vbox.setSpacing(4)

        trend_top = QHBoxLayout()
        trend_title = QLabel("月度微操收益趋势")
        trend_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        trend_title.setStyleSheet("color: #0F172A;")
        trend_top.addWidget(trend_title)

        trend_top.addSpacing(16)

        # 图例
        leg_lay = QHBoxLayout()
        leg_lay.setSpacing(6)

        p_box = QLabel("■")
        p_box.setStyleSheet("color: #10B981; font-size: 10px;")
        leg_lay.addWidget(p_box)
        p_txt = QLabel("月内微操盈利")
        p_txt.setStyleSheet("color: #64748B; font-size: 11px;")
        leg_lay.addWidget(p_txt)

        l_box = QLabel("■")
        l_box.setStyleSheet("color: #EF4444; font-size: 10px; margin-left: 8px;")
        leg_lay.addWidget(l_box)
        l_txt = QLabel("月内微操亏损")
        l_txt.setStyleSheet("color: #64748B; font-size: 11px;")
        leg_lay.addWidget(l_txt)

        n_box = QLabel("■")
        n_box.setStyleSheet("color: #CBD5E1; font-size: 10px; margin-left: 8px;")
        leg_lay.addWidget(n_box)
        n_txt = QLabel("无月内配对")
        n_txt.setStyleSheet("color: #64748B; font-size: 11px;")
        leg_lay.addWidget(n_txt)

        trend_top.addLayout(leg_lay)
        trend_top.addStretch()

        trend_vbox.addLayout(trend_top)

        self.trend_canvas = MonthlyScalpingTrendCanvas(self)
        self.trend_canvas.month_selected.connect(self.on_trend_month_clicked)
        trend_vbox.addWidget(self.trend_canvas, 1)

        # 底部说明文本
        self.trend_note = QLabel("ⓘ 说明：短线口径按平仓/接回日归属月份并支持跨月配对；若某月无完成配对则不计入微操收益。")
        self.trend_note.setStyleSheet("color: #94A3B8; font-size: 11px;")
        trend_vbox.addWidget(self.trend_note)

        self.mid_splitter.addWidget(trend_card)

        # 右侧：选中月份摘要卡片
        self.month_summary_card = MonthSummaryCard(self)
        self.month_summary_card.setFixedHeight(260)
        self.mid_splitter.addWidget(self.month_summary_card)

        self.mid_splitter.setSizes([850, 420])
        content_layout.addWidget(self.mid_splitter)

        # 6. 股票微操收益排行榜 (月内微操收益 TOP 5 & 月内微操亏损 TOP 5)
        rank_row = QHBoxLayout()
        rank_row.setSpacing(12)

        # 左侧：月内微操收益 TOP 5
        profit_card = QFrame()
        profit_card.setFixedHeight(205)
        profit_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        profit_vbox = QVBoxLayout(profit_card)
        profit_vbox.setContentsMargins(14, 10, 14, 10)
        profit_vbox.setSpacing(4)

        p_hdr = QHBoxLayout()
        self.profit_top_title = QLabel("短线微操收益 TOP 5")
        self.profit_top_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        self.profit_top_title.setStyleSheet("color: #0F172A;")
        p_hdr.addWidget(self.profit_top_title)

        p_tag = QLabel("仅看已选月份")
        p_tag.setStyleSheet("background: #ECFDF5; color: #10B981; font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 4px;")
        p_hdr.addWidget(p_tag)
        p_hdr.addStretch()
        profit_vbox.addLayout(p_hdr)

        p_th = QHBoxLayout()
        p_th.setContentsMargins(4, 2, 4, 2)
        p_th.addWidget(self._make_th_label("#", 24))
        p_th.addWidget(self._make_th_label("Ticker", 70))
        p_th.addWidget(self._make_th_label("微操净收益", 90, align_right=True))
        p_th.addWidget(self._make_th_label("微操次数", 65, align_right=True))
        p_th.addWidget(self._make_th_label("微操数量", 80, align_right=True))
        p_th.addWidget(self._make_th_label("当前浮动盈亏", 95, align_right=True))
        profit_vbox.addLayout(p_th)

        self.profit_top_vbox = QVBoxLayout()
        self.profit_top_vbox.setSpacing(3)
        profit_vbox.addLayout(self.profit_top_vbox)
        profit_vbox.addStretch()
        rank_row.addWidget(profit_card, 1)

        # 右侧：短线微操亏损 TOP 5
        loss_card = QFrame()
        loss_card.setFixedHeight(205)
        loss_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        loss_vbox = QVBoxLayout(loss_card)
        loss_vbox.setContentsMargins(14, 10, 14, 10)
        loss_vbox.setSpacing(4)

        l_hdr = QHBoxLayout()
        self.loss_top_title = QLabel("短线微操亏损 TOP 5")
        self.loss_top_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        self.loss_top_title.setStyleSheet("color: #0F172A;")
        l_hdr.addWidget(self.loss_top_title)

        l_tag = QLabel("仅看已选月份")
        l_tag.setStyleSheet("background: #FEF2F2; color: #EF4444; font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 4px;")
        l_hdr.addWidget(l_tag)
        l_hdr.addStretch()
        loss_vbox.addLayout(l_hdr)

        l_th = QHBoxLayout()
        l_th.setContentsMargins(4, 2, 4, 2)
        l_th.addWidget(self._make_th_label("#", 24))
        l_th.addWidget(self._make_th_label("Ticker", 70))
        l_th.addWidget(self._make_th_label("微操净亏损", 90, align_right=True))
        l_th.addWidget(self._make_th_label("微操次数", 65, align_right=True))
        l_th.addWidget(self._make_th_label("微操数量", 80, align_right=True))
        l_th.addWidget(self._make_th_label("当前浮动盈亏", 95, align_right=True))
        loss_vbox.addLayout(l_th)

        self.loss_top_vbox = QVBoxLayout()
        self.loss_top_vbox.setSpacing(3)
        loss_vbox.addLayout(self.loss_top_vbox)
        loss_vbox.addStretch()
        rank_row.addWidget(loss_card, 1)

        content_layout.addLayout(rank_row)

        # 6.5 超时未配对短线尝试纠偏卡片（防幸存者偏差，仅实战短线口径展示）
        self.unpaired_card = QFrame()
        self.unpaired_card.setStyleSheet(
            "QFrame { background-color: #FFFBEB; border: 1px solid #FDE68A; border-radius: 6px; }"
        )
        unpaired_vbox = QVBoxLayout(self.unpaired_card)
        unpaired_vbox.setContentsMargins(14, 8, 14, 8)
        unpaired_vbox.setSpacing(6)

        up_hdr = QHBoxLayout()
        up_hdr.setSpacing(8)
        up_title = QLabel("未配对短线尝试（防幸存者偏差）")
        up_title.setFont(QFont(".AppleSystemUIFont", 12, QFont.Weight.Bold))
        up_title.setStyleSheet("color: #92400E; border: none; background: transparent;")
        up_hdr.addWidget(up_title)

        self.unpaired_summary_lbl = QLabel("—")
        self.unpaired_summary_lbl.setStyleSheet("color: #78350F; font-size: 11.5px; border: none; background: transparent;")
        up_hdr.addWidget(self.unpaired_summary_lbl, 1)

        self.unpaired_toggle_btn = QPushButton("展开明细 ▾")
        self.unpaired_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unpaired_toggle_btn.setStyleSheet(
            "QPushButton { background: #FFFFFF; color: #92400E; border: 1px solid #FCD34D; "
            "border-radius: 5px; padding: 2px 10px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: #FEF3C7; }"
        )
        self.unpaired_toggle_btn.clicked.connect(self._toggle_unpaired_list)
        up_hdr.addWidget(self.unpaired_toggle_btn)
        unpaired_vbox.addLayout(up_hdr)

        self.unpaired_list_host = QWidget()
        self.unpaired_list_host.setStyleSheet("background: transparent; border: none;")
        self.unpaired_list_layout = QVBoxLayout(self.unpaired_list_host)
        self.unpaired_list_layout.setContentsMargins(0, 4, 0, 2)
        self.unpaired_list_layout.setSpacing(3)
        self.unpaired_list_host.hide()
        unpaired_vbox.addWidget(self.unpaired_list_host)

        content_layout.addWidget(self.unpaired_card)

        # 7. 微操收益明细表格卡片 (底部全宽)
        table_card = QFrame()
        table_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        table_vbox = QVBoxLayout(table_card)
        table_vbox.setContentsMargins(14, 10, 14, 10)
        table_vbox.setSpacing(8)

        table_top_h = QHBoxLayout()
        tbl_title = QLabel("微操收益明细")
        tbl_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        tbl_title.setStyleSheet("color: #0F172A;")
        table_top_h.addWidget(tbl_title)

        self.table_sub_lbl = QLabel("共 0 条记录 · 点击行查看详情，双击行进入交易回放")
        self.table_sub_lbl.setStyleSheet("font-size: 11px; color: #64748B; margin-left: 8px;")
        table_top_h.addWidget(self.table_sub_lbl)
        table_top_h.addStretch()

        # 导出 CSV 按钮（放置在表格右上角）
        self.export_btn = QPushButton("导出 CSV")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB; border: 1px solid #1D4ED8; border-radius: 6px;
                padding: 4px 14px; font-size: 12px; font-weight: 600; color: #FFFFFF;
            }
            QPushButton:hover { background: #1D4ED8; }
        """)
        self.export_btn.clicked.connect(self.export_csv)
        table_top_h.addWidget(self.export_btn)

        # 保持属性兼容对象
        self.page_size_lbl = QLabel()
        self.prev_page_btn = QPushButton()
        self.next_page_btn = QPushButton()
        self.page_num_lbl = QLabel()

        table_vbox.addLayout(table_top_h)

        self.table_model = ModernScalpingTableModel(self)
        self.table_view = QTableView(self)
        self.table_view.setModel(self.table_model)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setShowGrid(False)
        self.table_view.setWordWrap(False)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table_view.verticalHeader().hide()
        self.table_view.verticalHeader().setDefaultSectionSize(34)
        self.table_view.horizontalHeader().setFixedHeight(34)
        self.table_view.horizontalHeader().setHighlightSections(False)
        self.table_view.horizontalHeader().setSectionsClickable(True)
        self.table_view.horizontalHeader().sectionClicked.connect(self.on_header_section_clicked)
        # 彻底去除滚动条，高度由内容自适应撑开直接全部展示
        self.table_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table_view.setStyleSheet("""
            QTableView {
                background-color: #FFFFFF;
                alternate-background-color: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 4px;
                gridline-color: transparent;
                selection-background-color: #EFF6FF;
                selection-color: #1E293B;
            }
            QHeaderView::section {
                background-color: #F8FAFC;
                color: #475569;
                font-size: 11.5px;
                font-weight: 600;
                border: none;
                border-bottom: 1px solid #E2E8F0;
                padding: 4px 8px;
            }
        """)
        self.table_view.clicked.connect(self.on_table_row_clicked)
        self.table_view.doubleClicked.connect(self.on_table_row_double_clicked)
        table_vbox.addWidget(self.table_view)

        content_layout.addWidget(table_card)

        scroll.setWidget(content_widget)
        root_layout.addWidget(scroll)

        # 8. 右侧详情抽屉 (Drawer)
        self.drawer_overlay = QWidget(self)
        self.drawer_overlay.setStyleSheet("background-color: rgba(15, 23, 42, 0.20);")
        self.drawer_overlay.setCursor(Qt.CursorShape.PointingHandCursor)
        self.drawer_overlay.hide()
        self.drawer_overlay.mousePressEvent = lambda e: self.close_drawer()

        self.drawer_frame = QFrame(self)
        self.drawer_frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-left: 1px solid #CBD5E1;
            }
        """)
        self.drawer_frame.hide()

        drawer_vbox = QVBoxLayout(self.drawer_frame)
        drawer_vbox.setContentsMargins(18, 16, 18, 16)
        drawer_vbox.setSpacing(14)

        d_top = QHBoxLayout()
        self.drawer_title = QLabel("微操详情")
        self.drawer_title.setFont(QFont(".AppleSystemUIFont", 15, QFont.Weight.Bold))
        self.drawer_title.setStyleSheet("color: #0F172A;")
        d_top.addWidget(self.drawer_title)
        d_top.addStretch()

        d_close = QPushButton("✕")
        d_close.setFixedSize(26, 26)
        d_close.setCursor(Qt.CursorShape.PointingHandCursor)
        d_close.setStyleSheet("""
            QPushButton {
                border: none; background: #F1F5F9; border-radius: 13px;
                color: #64748B; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background: #E2E8F0; color: #0F172A; }
        """)
        d_close.clicked.connect(self.close_drawer)
        d_top.addWidget(d_close)
        drawer_vbox.addLayout(d_top)

        self.drawer_subtitle = QLabel("2026-11 · 长桥 · 杠杆")
        self.drawer_subtitle.setStyleSheet("color: #64748B; font-size: 12px;")
        drawer_vbox.addWidget(self.drawer_subtitle)

        drawer_scroll = QScrollArea()
        drawer_scroll.setWidgetResizable(True)
        drawer_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        drawer_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        drawer_body = QWidget()
        drawer_body.setStyleSheet("background: transparent;")
        self.drawer_list_layout = QVBoxLayout(drawer_body)
        self.drawer_list_layout.setContentsMargins(0, 0, 4, 0)
        self.drawer_list_layout.setSpacing(8)
        drawer_scroll.setWidget(drawer_body)
        drawer_vbox.addWidget(drawer_scroll, 1)

    def _create_chip(self, text: str, on_close=None) -> QFrame:
        chip = QFrame()
        chip.setStyleSheet("""
            QFrame {
                background-color: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 12px;
            }
        """)
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(4)

        lbl = QLabel(text)
        lbl.setStyleSheet("color: #1D4ED8; font-size: 11.5px; font-weight: 500;")
        lay.addWidget(lbl)
        chip._label = lbl

        if on_close:
            btn = QPushButton("✕")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("border: none; background: transparent; color: #2563EB; font-weight: bold; font-size: 11px;")
            btn.clicked.connect(on_close)
            lay.addWidget(btn)
        return chip

    def _clear_person_filter(self):
        self.person_combo.setCurrentIndex(0)
        self.on_query_clicked()

    def _clear_broker_filter(self):
        self.broker_combo.setCurrentIndex(0)
        self.on_query_clicked()

    def _clear_category_filter(self):
        self.category_combo.setCurrentIndex(0)
        self.on_query_clicked()

    def _make_th_label(self, text: str, width: int, align_right: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(width)
        lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #94A3B8;")
        if align_right:
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def apply_proportional_column_widths(self):
        """按 16 列基准宽度平摊视口剩余空间"""
        if not hasattr(self, "table_view") or not self.table_view:
            return
        header = self.table_view.horizontalHeader()
        header.setStretchLastSection(False)

        base_widths = [80, 80, 70, 75, 65, 80, 90, 90, 85, 100, 100, 105, 125, 125, 105, 80]
        n = len(base_widths)
        if self.table_model.columnCount() != n:
            return

        viewport_w = self.table_view.viewport().width()
        if viewport_w <= 20:
            viewport_w = self.table_view.width() - 4
        if viewport_w <= 20:
            viewport_w = 1400

        base_sum = sum(base_widths)
        extra = max(0, viewport_w - base_sum)
        per_col = extra // n
        rem = extra % n

        for i, w in enumerate(base_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            extra_px = per_col + (1 if i < rem else 0)
            self.table_view.setColumnWidth(i, w + extra_px)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "mid_splitter") and self.mid_splitter:
            w = self.mid_splitter.width()
            if w > 100:
                self.mid_splitter.setSizes([int(w * 0.68), int(w * 0.32)])
        if self.drawer_frame.isVisible():
            w = 420
            self.drawer_overlay.setGeometry(self.rect())
            self.drawer_frame.setGeometry(self.width() - w, 0, w, self.height())
            self.drawer_overlay.raise_()
            self.drawer_frame.raise_()
        self.apply_proportional_column_widths()

    # -------------------------------------------------------------------------
    # 数据加载与处理
    # -------------------------------------------------------------------------
    def activate(self):
        if self.person_combo.count() <= 1:
            self._populate_persons()
        if not self.raw_rows and self.analytics:
            self.update_data(self.analytics)
        self.apply_proportional_column_widths()

    def sync_person_from_main(self, person_id: Any):
        if getattr(self, "_syncing_person", False):
            return
        self._syncing_person = True
        try:
            self.applied_person = person_id
            if self.person_combo.count() <= 1:
                self._populate_persons()
            self.person_combo.blockSignals(True)
            idx = self.person_combo.findData(person_id)
            self.person_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.person_combo.blockSignals(False)
            self.recalculate_and_refresh()
        finally:
            self._syncing_person = False

    def _populate_persons(self):
        main_win = getattr(self, "main_window", None) or self.parent() or self.window()
        persons_list = []
        if hasattr(main_win, "person_selector"):
            combo = main_win.person_selector
            for i in range(combo.count()):
                persons_list.append((combo.itemText(i), combo.itemData(i)))
        elif hasattr(main_win, "persons"):
            persons_list = [("全部人员", None)] + [(p.name, p.id) for p in main_win.persons]

        if persons_list:
            cur_data = self.applied_person if self.applied_person is not None else self.person_combo.currentData()
            self.person_combo.blockSignals(True)
            self.person_combo.clear()
            for text, data in persons_list:
                self.person_combo.addItem(text, data)
            idx = self.person_combo.findData(cur_data)
            self.person_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.person_combo.blockSignals(False)

    def _sync_mode_buttons(self):
        active_desc = ""
        for mk, _, desc in self.MODE_SPECS:
            btn = self.mode_buttons.get(mk)
            if not btn:
                continue
            active = (mk == self.scalping_mode)
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            if active:
                active_desc = desc
                btn.setStyleSheet(
                    "QPushButton { background: #2563EB; color: #FFFFFF; border: 1px solid #1D4ED8; "
                    "border-radius: 5px; padding: 0 11px; font-size: 11.5px; font-weight: 600; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #FFFFFF; color: #1E40AF; border: 1px solid #BFDBFE; "
                    "border-radius: 5px; padding: 0 11px; font-size: 11.5px; font-weight: 500; }"
                    "QPushButton:hover { background: #DBEAFE; }"
                )
        if hasattr(self, "desc_text") and active_desc:
            self.desc_text.setText(active_desc)
        is_compat = (self.scalping_mode == "EXCEL_COMPAT")
        if hasattr(self, "profit_top_title"):
            self.profit_top_title.setText("月内微操收益 TOP 5" if is_compat else "短线微操收益 TOP 5")
        if hasattr(self, "loss_top_title"):
            self.loss_top_title.setText("月内微操亏损 TOP 5" if is_compat else "短线微操亏损 TOP 5")
        if hasattr(self, "trend_note"):
            self.trend_note.setText(
                "ⓘ 说明：若某月只有买入或只有卖出，未形成月内配对，则该月不计入微操收益。"
                if is_compat
                else "ⓘ 说明：短线口径按平仓/接回日归属月份，支持跨月配对；超时未平仓/未接回单列在「未配对短线尝试」供复盘。"
            )
        if hasattr(self, "unpaired_card"):
            self.unpaired_card.setVisible(not is_compat)

    def set_scalping_mode(self, mode_key: str):
        if mode_key == self.scalping_mode:
            self._sync_mode_buttons()
            return
        self.scalping_mode = mode_key
        self._unpaired_expanded = False
        if hasattr(self, "unpaired_list_host"):
            self.unpaired_list_host.hide()
            self.unpaired_toggle_btn.setText("展开明细 ▾")
        self._sync_mode_buttons()
        if self.analytics:
            self._load_active_mode_data()
            self.recalculate_and_refresh()

    def _toggle_unpaired_list(self):
        self._unpaired_expanded = not self._unpaired_expanded
        self.unpaired_list_host.setVisible(self._unpaired_expanded)
        self.unpaired_toggle_btn.setText("收起明细 ▴" if self._unpaired_expanded else "展开明细 ▾")

    def _load_active_mode_data(self):
        analytics = self.analytics
        if not analytics or not hasattr(analytics, "reports"):
            return
        modes = getattr(analytics, "scalping_modes", None) or {}
        bundle = modes.get(self.scalping_mode)
        if not bundle:
            fallback_rep = analytics.reports.get("每月微操收益")
            if not fallback_rep:
                return
            bundle = {
                "mode": "EXCEL_COMPAT",
                "max_days": None,
                "report": fallback_rep,
                "pairs_report": analytics.reports.get("微操配对证据"),
                "pairs": (),
                "expired_buys": (),
                "expired_sells": (),
                "pending_buys": (),
                "pending_sells": (),
            }
        self.active_bundle = bundle
        report = bundle["report"]
        self.raw_rows = [tuple(r.values) for r in report.rows]
        self.row_meta_map = {}
        for r in report.rows:
            if len(r.values) >= 4:
                key = (str(r.values[0]), fmt_broker_cn(r.values[1]), str(r.values[3]).upper())
                self.row_meta_map[key] = dict(r.metadata or {})

        # 填充券商渠道下拉框（尽量保留已选渠道）
        prev_broker = self.applied_broker
        brokers = sorted({fmt_broker_cn(r[1]) for r in self.raw_rows if len(r) > 1 and r[1]})
        self.broker_combo.blockSignals(True)
        self.broker_combo.clear()
        self.broker_combo.addItem("全部渠道", "")
        for b in brokers:
            self.broker_combo.addItem(b, b)
        b_idx = self.broker_combo.findData(prev_broker)
        self.broker_combo.setCurrentIndex(b_idx if b_idx >= 0 else 0)
        self.applied_broker = self.broker_combo.currentData() or ""
        self.broker_combo.blockSignals(False)

        # 填充分类下拉框（尽量保留已选分类）
        prev_cat = self.applied_category
        categories = sorted({str(r[2]) for r in self.raw_rows if len(r) > 2 and r[2]})
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部分类", "")
        for c in categories:
            self.category_combo.addItem(c, c)
        c_idx = self.category_combo.findData(prev_cat)
        self.category_combo.setCurrentIndex(c_idx if c_idx >= 0 else 0)
        self.applied_category = self.category_combo.currentData() or ""
        self.category_combo.blockSignals(False)

        # 填充月份下拉框（包含配对月份与超时未配对月份）
        months_set = {str(r[0]) for r in self.raw_rows if len(r) > 0 and r[0]}
        for item in (*bundle.get("expired_buys", ()), *bundle.get("expired_sells", ())):
            if item.get("month"):
                months_set.add(str(item["month"]))
        months = sorted(months_set)
        self.start_month_combo.blockSignals(True)
        self.end_month_combo.blockSignals(True)
        self.start_month_combo.clear()
        self.end_month_combo.clear()
        for m in months:
            self.start_month_combo.addItem(m, m)
            self.end_month_combo.addItem(m, m)
        if months:
            self.start_month_combo.setCurrentIndex(0)
            self.end_month_combo.setCurrentIndex(len(months) - 1)
            self.applied_start_month = months[0]
            self.applied_end_month = months[-1]
            if not self.active_chart_month or self.active_chart_month not in months:
                self.active_chart_month = months[-1]  # 默认选中最新月份
        else:
            self.applied_start_month = ""
            self.applied_end_month = ""
            self.active_chart_month = ""
        self.start_month_combo.blockSignals(False)
        self.end_month_combo.blockSignals(False)

    def update_data(self, analytics):
        self.analytics = analytics
        if not analytics or not hasattr(analytics, "reports"):
            return

        # 提取当前持仓浮动盈亏映射 (Ticker -> unrealized PnL)
        self.unrealized_pnl_map = {}
        holdings_rep = analytics.reports.get("当前持仓")
        if holdings_rep:
            # HOLDINGS_COLUMNS: 渠道, 分类, Ticker, 持仓数量, 当前价($), 持仓市值($), 持仓成本价($), 累计买入金额($), 累计卖出金额($), 累计净投入($), 持仓盈亏金额($)...
            for r in holdings_rep.rows:
                if len(r.values) > 10 and r.values[2] is not None:
                    sym = str(r.values[2]).upper()
                    val = r.values[10]
                    if val is not None:
                        self.unrealized_pnl_map[sym] = self.unrealized_pnl_map.get(sym, 0.0) + float(val)

        self._sync_mode_buttons()
        self._load_active_mode_data()
        self._populate_persons()
        self.recalculate_and_refresh()

    def on_query_clicked(self):
        """点击查询按钮，正式应用筛选条件"""
        p_id = self.person_combo.currentData()
        person_changed = (p_id != self.applied_person)
        self.applied_person = p_id

        # 同步给主窗口人员筛选
        main_win = getattr(self, "main_window", None) or self.parent() or self.window()
        if person_changed and hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(p_id)
            finally:
                self._syncing_person = False

        self.applied_broker = self.broker_combo.currentData() or ""
        self.applied_category = self.category_combo.currentData() or ""
        self.applied_ticker = self.ticker_edit.text().strip().upper()

        s_m = self.start_month_combo.currentData() or ""
        e_m = self.end_month_combo.currentData() or ""
        if s_m and e_m and s_m > e_m:
            s_m, e_m = e_m, s_m
            self.start_month_combo.setCurrentText(s_m)
            self.end_month_combo.setCurrentText(e_m)

        self.applied_start_month = s_m
        self.applied_end_month = e_m

        self.active_ranking_ticker = ""
        self.table_model.page = 1
        self.recalculate_and_refresh()

    def on_reset_clicked(self):
        """点击重置按钮，恢复全部默认条件"""
        self.person_combo.setCurrentIndex(0)
        self.broker_combo.setCurrentIndex(0)
        self.category_combo.setCurrentIndex(0)
        self.ticker_edit.clear()

        months = sorted({str(r[0]) for r in self.raw_rows if len(r) > 0 and r[0]})
        if months:
            self.start_month_combo.setCurrentIndex(0)
            self.end_month_combo.setCurrentIndex(len(months) - 1)
            self.applied_start_month = months[0]
            self.applied_end_month = months[-1]
            self.active_chart_month = months[-1]
        else:
            self.applied_start_month = ""
            self.applied_end_month = ""
            self.active_chart_month = ""

        self.applied_person = None
        self.applied_broker = ""
        self.applied_category = ""
        self.applied_ticker = ""
        self.active_ranking_ticker = ""

        main_win = getattr(self, "main_window", None) or self.parent() or self.window()
        if hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(None)
            finally:
                self._syncing_person = False

        self.table_model.page = 1
        self.recalculate_and_refresh()

    def recalculate_and_refresh(self):
        if not self.raw_rows:
            return

        # 1. 计算全局范围过滤记录集 (global_scope_rows)
        global_scope_rows: List[Tuple] = []
        for r in self.raw_rows:
            m = str(r[0]) if len(r) > 0 else ""
            b_cn = fmt_broker_cn(r[1]) if len(r) > 1 else ""
            cat = str(r[2]) if len(r) > 2 else ""
            sym = str(r[3]).upper() if len(r) > 3 else ""

            if self.applied_broker and b_cn != self.applied_broker:
                continue
            if self.applied_category and cat != self.applied_category:
                continue
            if self.applied_ticker and self.applied_ticker not in sym:
                continue
            if self.applied_start_month and m < self.applied_start_month:
                continue
            if self.applied_end_month and m > self.applied_end_month:
                continue

            global_scope_rows.append(r)

        # 2. 统计月度微操趋势数据 (各月份独立统计，无配对月份明确区分)
        month_records: Dict[str, List[Tuple]] = {}
        for r in global_scope_rows:
            m = str(r[0])
            if m not in month_records:
                month_records[m] = []
            month_records[m].append(r)

        # 提取筛选区间内所有的月份
        all_months_in_scope: List[str] = []
        if self.applied_start_month and self.applied_end_month:
            # 顺序展开
            sm_y, sm_m = int(self.applied_start_month[:4]), int(self.applied_start_month[5:7])
            em_y, em_m = int(self.applied_end_month[:4]), int(self.applied_end_month[5:7])
            cur_y, cur_m = sm_y, sm_m
            while (cur_y < em_y) or (cur_y == em_y and cur_m <= em_m):
                all_months_in_scope.append(f"{cur_y:04d}-{cur_m:02d}")
                cur_m += 1
                if cur_m > 12:
                    cur_y += 1
                    cur_m = 1
        else:
            all_months_in_scope = sorted(month_records.keys())

        trend_points: List[Dict[str, Any]] = []
        for m in all_months_in_scope:
            m_rows = month_records.get(m, [])
            if m_rows:
                m_pnl = sum(float(r[11] or 0) for r in m_rows)
                m_trades = sum(int(r[4] or 0) for r in m_rows)
                m_qty = sum(float(r[5] or 0) for r in m_rows)
                profit_cnt = sum(1 for r in m_rows if float(r[11] or 0) > 0)
                loss_cnt = sum(1 for r in m_rows if float(r[11] or 0) < 0)
                has_pair = True
            else:
                m_pnl = 0.0
                m_trades = 0
                m_qty = 0.0
                profit_cnt = 0
                loss_cnt = 0
                has_pair = False

            trend_points.append({
                "month": m,
                "pnl": m_pnl,
                "trades": m_trades,
                "qty": m_qty,
                "profit_cnt": profit_cnt,
                "loss_cnt": loss_cnt,
                "has_pair": has_pair,
            })

        # 严格锁定单月：若未选或不在区间内，强制默认选中区间内最后一个月（最新月），绝不汇总全周期
        if not self.active_chart_month or self.active_chart_month not in all_months_in_scope:
            self.active_chart_month = all_months_in_scope[-1] if all_months_in_scope else ""

        self.trend_canvas.set_data(trend_points, self.active_chart_month)

        # 3. 统计选中月份摘要 (右侧卡片，含正T/反T、胜率、盈亏比、持仓天数与未配对纠偏)
        is_compat = (self.scalping_mode == "EXCEL_COMPAT")
        mode_short_label = next((lbl for mk, lbl, _ in self.MODE_SPECS if mk == self.scalping_mode), "")

        def _match_unpaired(item: Dict[str, Any]) -> bool:
            m = str(item.get("month", ""))
            b_cn = fmt_broker_cn(item.get("channel", ""))
            cat = str(item.get("category", ""))
            sym = str(item.get("ticker", "")).upper()
            if self.active_chart_month and m != self.active_chart_month:
                return False
            if self.applied_broker and b_cn != self.applied_broker:
                return False
            if self.applied_category and cat != self.applied_category:
                return False
            if self.applied_ticker and self.applied_ticker not in sym:
                return False
            if self.active_ranking_ticker and sym != self.active_ranking_ticker:
                return False
            return True

        month_exp_buys = [x for x in self.active_bundle.get("expired_buys", ()) if _match_unpaired(x)] if not is_compat else []
        month_exp_sells = [x for x in self.active_bundle.get("expired_sells", ()) if _match_unpaired(x)] if not is_compat else []

        month_scope_rows = [r for r in global_scope_rows if str(r[0]) == self.active_chart_month] if self.active_chart_month else global_scope_rows
        month_pairs: List[Dict[str, Any]] = []
        for r in month_scope_rows:
            k = (str(r[0]), fmt_broker_cn(r[1]), str(r[3]).upper())
            meta = self.row_meta_map.get(k) or {}
            month_pairs.extend(meta.get("pairs", ()))

        pos_pairs = [p for p in month_pairs if p.get("mode") == "POS_T"]
        rev_pairs = [p for p in month_pairs if p.get("mode") == "REV_T"]
        win_pairs = [p for p in month_pairs if float(p.get("pnl") or 0) > 0]
        loss_pairs = [p for p in month_pairs if float(p.get("pnl") or 0) < 0]
        avg_win = (sum(float(p["pnl"]) for p in win_pairs) / len(win_pairs)) if win_pairs else None
        avg_loss = (sum(abs(float(p["pnl"])) for p in loss_pairs) / len(loss_pairs)) if loss_pairs else None
        pl_ratio = (avg_win / avg_loss) if (avg_win is not None and avg_loss and avg_loss > 0) else None
        avg_hold_days = (sum(float(p.get("hold_days") or 0) for p in month_pairs) / len(month_pairs)) if month_pairs else None

        summary_extra = {
            "is_compat": is_compat,
            "mode_label": mode_short_label,
            "pair_count": len(month_pairs),
            "pos_t_count": len(pos_pairs),
            "pos_t_pnl": sum(float(p.get("pnl") or 0) for p in pos_pairs),
            "rev_t_count": len(rev_pairs),
            "rev_t_pnl": sum(float(p.get("pnl") or 0) for p in rev_pairs),
            "win_count": len(win_pairs),
            "loss_count": len(loss_pairs),
            "pl_ratio": pl_ratio,
            "avg_hold_days": avg_hold_days,
            "cross_month_count": sum(1 for p in month_pairs if p.get("cross_month")),
            "expired_buy_count": len(month_exp_buys),
            "expired_buy_amount": sum(float(x.get("amount") or 0) for x in month_exp_buys),
            "expired_sell_count": len(month_exp_sells),
            "expired_sell_amount": sum(float(x.get("amount") or 0) for x in month_exp_sells),
        }

        sel_month_info = next((p for p in trend_points if p["month"] == self.active_chart_month), None)
        if sel_month_info:
            self.month_summary_card.set_month_data(
                self.active_chart_month,
                sel_month_info["pnl"] if sel_month_info["has_pair"] else None,
                sel_month_info["trades"],
                sel_month_info["qty"],
                sel_month_info["has_pair"],
                extra=summary_extra,
            )
        else:
            self.month_summary_card.set_month_data(
                self.active_chart_month, None, 0, 0.0, False, extra=summary_extra
            )

        # 3.5 更新未配对短线尝试卡片（防幸存者偏差）
        self._update_unpaired_panel(month_exp_buys, month_exp_sells, summary_extra["cross_month_count"])

        # 4. 统计月度股票 TOP 5 (严格取当前选中月份)
        stock_map: Dict[str, Dict[str, Any]] = {}
        for r in month_scope_rows:
            sym = str(r[3]).upper()
            if sym not in stock_map:
                stock_map[sym] = {"pnl": 0.0, "trades": 0, "qty": 0.0}
            stock_map[sym]["pnl"] += float(r[11] or 0)
            stock_map[sym]["trades"] += int(r[4] or 0)
            stock_map[sym]["qty"] += float(r[5] or 0)
        self.update_stock_top5(stock_map)

        # 5. 更新 Chips 生效标签
        p_text = self.person_combo.currentText() or "全部人员"
        b_text = self.applied_broker or "全部渠道"
        c_text = self.applied_category or "全部分类"
        d_range = f"{self.applied_start_month} 至 {self.applied_end_month}" if self.applied_start_month else "全周期"

        self.chip_person._label.setText(p_text)
        self.chip_broker._label.setText(b_text)
        self.chip_category._label.setText(c_text)
        self.chip_date._label.setText(d_range)

        if self.active_chart_month:
            self.chip_month_drill._label.setText(f"月份: {self.active_chart_month}")
            self.chip_month_drill.show()
        else:
            self.chip_month_drill.hide()

        if self.active_ranking_ticker:
            self.chip_ticker_drill._label.setText(f"股票: {self.active_ranking_ticker}")
            self.chip_ticker_drill.show()
        else:
            self.chip_ticker_drill.hide()

        # 6. 计算用于明细表格展示的数据集 (display_rows)
        # 受 active_chart_month 与 active_ranking_ticker 联动筛选
        display_rows: List[Tuple] = []
        for r in global_scope_rows:
            m = str(r[0]) if len(r) > 0 else ""
            sym = str(r[3]).upper() if len(r) > 3 else ""

            if self.active_chart_month and m != self.active_chart_month:
                continue
            if self.active_ranking_ticker and sym != self.active_ranking_ticker:
                continue

            display_rows.append(r)

        # 7. 更新明细表格数据 (16列)
        cols = (
            "月份", "渠道", "分类", "Ticker", "微操次数", "微操数量",
            "微操均买价($)", "微操均卖价($)", "每股价差($)", "买入金额($)", "卖出金额($)",
            "月内微操收益($)" if is_compat else "微操净收益($)",
            "首笔微操时间", "最近微操时间", "当前浮动盈亏($)", "说明",
        )
        table_rows: List[List[Any]] = []
        for r in display_rows:
            b_cn = fmt_broker_cn(r[1])
            sym = str(r[3]).upper() if len(r) > 3 else ""
            unrealized_pnl = self.unrealized_pnl_map.get(sym)
            unrealized_str = fmt_signed_money(unrealized_pnl, 2) if unrealized_pnl is not None else "—"

            if is_compat:
                note_str = "仅月内配对"
            else:
                meta = self.row_meta_map.get((str(r[0]), b_cn, sym)) or {}
                parts = []
                if meta.get("pos_t_count"):
                    parts.append(f"正T{meta['pos_t_count']}")
                if meta.get("rev_t_count"):
                    parts.append(f"反T{meta['rev_t_count']}")
                if meta.get("pair_count"):
                    wr = float(meta.get("win_rate") or 0) * 100
                    parts.append(f"胜{wr:.0f}%")
                if meta.get("cross_month_count"):
                    parts.append("跨月")
                if meta.get("expired_buy_count"):
                    parts.append(f"转长持{meta['expired_buy_count']}")
                if meta.get("fee_unverified_count"):
                    parts.append("⚠含未核实费")
                note_str = " · ".join(parts) if parts else mode_short_label

            table_rows.append([
                str(r[0]),                                  # 0: 月份
                b_cn,                                       # 1: 渠道
                str(r[2]),                                  # 2: 分类
                str(r[3]),                                  # 3: Ticker
                str(r[4]),                                  # 4: 微操次数
                fmt_money(r[5], 2),                         # 5: 微操数量
                f"${fmt_money(r[6], 2)}",                   # 6: 微操均买价($)
                f"${fmt_money(r[7], 2)}",                   # 7: 微操均卖价($)
                fmt_signed_money(r[8], 2),                  # 8: 每股价差($)
                f"${fmt_money(r[9], 2)}",                   # 9: 买入金额($)
                f"${fmt_money(r[10], 2)}",                  # 10: 卖出金额($)
                fmt_signed_money(r[11], 2),                 # 11: 微操净收益($) / 月内微操收益($)
                str(r[12]) if len(r) > 12 else "—",          # 12: 首笔微操时间
                str(r[13]) if len(r) > 13 else "—",          # 13: 最近微操时间
                unrealized_str,                             # 14: 当前浮动盈亏($)
                note_str,                                   # 15: 说明
            ])

        self.table_model.highlight_month = self.active_chart_month
        self.table_model.highlight_ticker = self.active_ranking_ticker
        self.table_model.page_size = max(1000, len(table_rows))
        self.table_model.page = 1
        self.table_model.set_columns_and_rows(cols, table_rows)

        # 更新表格副标题，直接全部展示，根据行数自适应高度，绝不出现内部垂直滚动条
        total_rows = len(table_rows)
        active_m_hint = f"（已选月份 {self.active_chart_month}）" if self.active_chart_month else ""
        self.table_sub_lbl.setText(f"共 {total_rows} 条记录 {active_m_hint}· 点击行查看详情，双击行进入交易回放")

        calc_height = 34 + max(1, total_rows) * 34 + 4
        self.table_view.setFixedHeight(calc_height)
        self.table_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.apply_proportional_column_widths()

    # -------------------------------------------------------------------------
    # 股票 TOP 5 排行榜更新
    # -------------------------------------------------------------------------
    def update_stock_top5(self, stock_map: Dict[str, Dict[str, Any]]):
        # 清空现有子项
        while self.profit_top_vbox.count():
            item = self.profit_top_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self.loss_top_vbox.count():
            item = self.loss_top_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 盈利 TOP 5：按收益从高到低排序，收益 >= 0
        profit_stocks = sorted(
            [item for item in stock_map.items() if item[1]["pnl"] >= 0],
            key=lambda x: x[1]["pnl"],
            reverse=True,
        )[:5]

        # 亏损 TOP 5：按收益从低到高排序，收益 < 0
        loss_stocks = sorted(
            [item for item in stock_map.items() if item[1]["pnl"] < 0],
            key=lambda x: x[1]["pnl"],
        )[:5]

        if not profit_stocks:
            empty_lbl = QLabel("当前月份暂无月内微操盈利股票")
            empty_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; padding: 10px 0;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.profit_top_vbox.addWidget(empty_lbl)
        else:
            for rank, (sym, data) in enumerate(profit_stocks, 1):
                row_w = self._create_stock_rank_row(rank, sym, data["pnl"], data["trades"], data["qty"], is_profit=True)
                self.profit_top_vbox.addWidget(row_w)

        if not loss_stocks:
            empty_lbl = QLabel("当前月份暂无月内微操亏损股票")
            empty_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; padding: 10px 0;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.loss_top_vbox.addWidget(empty_lbl)
        else:
            for rank, (sym, data) in enumerate(loss_stocks, 1):
                row_w = self._create_stock_rank_row(rank, sym, data["pnl"], data["trades"], data["qty"], is_profit=False)
                self.loss_top_vbox.addWidget(row_w)

    def _create_stock_rank_row(self, rank: int, ticker: str, pnl: float, trades: int, qty: float, is_profit: bool) -> QWidget:
        w = QWidget()
        w.setCursor(Qt.CursorShape.PointingHandCursor)
        is_active = (ticker == self.active_ranking_ticker)
        bg = "#EFF6FF" if is_active else "transparent"
        w.setStyleSheet(f"background: {bg}; border-radius: 4px;")

        h = QHBoxLayout(w)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(6)

        # 1. 排名徽章
        badge = QLabel(str(rank))
        badge.setFixedSize(18, 18)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if is_profit:
            badge.setStyleSheet("background: #10B981; color: #FFFFFF; font-size: 10px; font-weight: bold; border-radius: 9px;")
        else:
            badge.setStyleSheet("background: #EF4444; color: #FFFFFF; font-size: 10px; font-weight: bold; border-radius: 9px;")
        h.addWidget(badge)

        # 2. Ticker
        t_lbl = QLabel(ticker)
        t_lbl.setFixedWidth(70)
        t_lbl.setFont(numeric_font(12, bold=True))
        t_lbl.setStyleSheet("color: #2563EB;")
        h.addWidget(t_lbl)

        # 3. 月内微操收益/亏损
        p_lbl = QLabel(fmt_signed_money(pnl, 2))
        p_lbl.setFixedWidth(90)
        p_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        p_lbl.setFont(numeric_font(11.5, bold=True))
        p_lbl.setStyleSheet("color: #10B981;" if is_profit else "color: #EF4444;")
        h.addWidget(p_lbl)

        # 4. 微操次数
        cnt_lbl = QLabel(str(trades))
        cnt_lbl.setFixedWidth(65)
        cnt_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        cnt_lbl.setFont(numeric_font(11))
        cnt_lbl.setStyleSheet("color: #475569;")
        h.addWidget(cnt_lbl)

        # 5. 微操数量
        qty_lbl = QLabel(fmt_money(qty, 2))
        qty_lbl.setFixedWidth(80)
        qty_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        qty_lbl.setFont(numeric_font(11))
        qty_lbl.setStyleSheet("color: #475569;")
        h.addWidget(qty_lbl)

        # 6. 当前浮动盈亏（辅助参考列）
        unrealized = self.unrealized_pnl_map.get(ticker.upper())
        u_str = fmt_signed_money(unrealized, 2) if unrealized is not None else "—"
        u_lbl = QLabel(u_str)
        u_lbl.setFixedWidth(95)
        u_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        u_lbl.setFont(numeric_font(11, bold=True))
        if unrealized is not None:
            if unrealized > 0:
                u_lbl.setStyleSheet("color: #10B981;")
            elif unrealized < 0:
                u_lbl.setStyleSheet("color: #EF4444;")
            else:
                u_lbl.setStyleSheet("color: #64748B;")
        else:
            u_lbl.setStyleSheet("color: #94A3B8;")
        h.addWidget(u_lbl)

        # 点击反选联动
        w.mousePressEvent = lambda e: self.filter_by_ticker(ticker)
        return w

    def filter_by_ticker(self, ticker: str):
        if self.active_ranking_ticker == ticker or not ticker:
            self.active_ranking_ticker = ""
        else:
            self.active_ranking_ticker = ticker
        self.table_model.page = 1
        self.recalculate_and_refresh()

    def on_trend_month_clicked(self, month: str):
        """点击趋势图月份柱体进行切换；若为空或与当前一致，相当于没有点击，保持单月选中不变"""
        if not month:
            return
        if self.active_chart_month != month:
            self.active_chart_month = month
            self.table_model.page = 1
            self.recalculate_and_refresh()

    def change_page(self, delta: int):
        self.table_model.set_page(self.table_model.page + delta)
        total_rows = len(self.table_model.all_rows)
        total_pages = max(1, (total_rows + self.table_model.page_size - 1) // self.table_model.page_size)
        self.page_num_lbl.setText(f"{self.table_model.page} / {total_pages}")
        self.prev_page_btn.setEnabled(self.table_model.page > 1)
        self.next_page_btn.setEnabled(self.table_model.page < total_pages)

    def on_header_section_clicked(self, section: int):
        self.table_model.toggle_sort(section)
        total_rows = len(self.table_model.all_rows)
        total_pages = max(1, (total_rows + self.table_model.page_size - 1) // self.table_model.page_size)
        self.page_num_lbl.setText(f"{self.table_model.page} / {total_pages}")
        self.prev_page_btn.setEnabled(self.table_model.page > 1)
        self.next_page_btn.setEnabled(self.table_model.page < total_pages)

    def on_table_row_clicked(self, index: QModelIndex):
        r = index.row()
        if 0 <= r < len(self.table_model.rows):
            row_data = self.table_model.rows[r]
            self.open_drawer(row_data)

    def on_table_row_double_clicked(self, index: QModelIndex):
        r = index.row()
        if 0 <= r < len(self.table_model.rows):
            row_data = self.table_model.rows[r]
            # 双击进入交易回放
            sym = str(row_data[3])
            m = str(row_data[0])
            broker = str(row_data[1]) if len(row_data) > 1 else ""
            self.open_stock_replay(sym, month=m, broker=broker)

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                MonthlyScalpingPage._clear_layout(item.layout())

    def _update_unpaired_panel(self, exp_buys: List[Dict[str, Any]], exp_sells: List[Dict[str, Any]], cross_cnt: int):
        if self.scalping_mode == "EXCEL_COMPAT":
            self.unpaired_card.hide()
            return
        self.unpaired_card.show()
        self._clear_layout(self.unpaired_list_layout)
        buy_amt = sum(float(x.get("amount") or 0) for x in exp_buys)
        sell_amt = sum(float(x.get("amount") or 0) for x in exp_sells)
        win_days = self.active_bundle.get("max_days", 5)
        cross_str = f" · 本月含跨月配对 {cross_cnt} 笔" if cross_cnt else ""
        if exp_buys or exp_sells:
            self.unpaired_summary_lbl.setText(
                f"超过 {win_days} 交易日未配对：正T转长持 {len(exp_buys)} 笔（合计 ${fmt_money(buy_amt, 2)}） · "
                f"反T未接回 {len(exp_sells)} 笔（合计 ${fmt_money(sell_amt, 2)}）{cross_str}"
            )
            self.unpaired_toggle_btn.show()
            combined = [
                ("正T转长持", x) for x in exp_buys
            ] + [
                ("反T未接回", x) for x in exp_sells
            ]
            combined.sort(key=lambda pair: (str(pair[1].get("open_day", "")), str(pair[1].get("ticker", ""))), reverse=True)
            for tag, x in combined[:40]:
                row_w = QWidget()
                row_lay = QHBoxLayout(row_w)
                row_lay.setContentsMargins(6, 2, 6, 2)
                row_lay.setSpacing(10)
                badge = QLabel(tag)
                if tag == "正T转长持":
                    badge.setStyleSheet("background: #FEE2E2; color: #B91C1C; font-size: 10.5px; font-weight: 600; padding: 1px 6px; border-radius: 4px;")
                else:
                    badge.setStyleSheet("background: #E0F2FE; color: #0369A1; font-size: 10.5px; font-weight: 600; padding: 1px 6px; border-radius: 4px;")
                row_lay.addWidget(badge)
                info = QLabel(
                    f"{x.get('open_day', '—')}  ·  {fmt_broker_cn(x.get('channel'))}  ·  "
                    f"<b>{x.get('ticker', '—')}</b>  ·  {fmt_money(x.get('qty'), 2)} 股"
                )
                info.setStyleSheet("color: #334155; font-size: 11.5px;")
                row_lay.addWidget(info, 1)
                amt_lbl = QLabel(f"${fmt_money(x.get('amount'), 2)}")
                amt_lbl.setFont(numeric_font(11.5, bold=True))
                amt_lbl.setStyleSheet("color: #78350F;")
                row_lay.addWidget(amt_lbl)
                self.unpaired_list_layout.addWidget(row_w)
            if len(combined) > 40:
                more = QLabel(f"… 另有 {len(combined) - 40} 笔超时记录（可筛选特定标的查看）")
                more.setStyleSheet("color: #92400E; font-size: 11px; padding-left: 6px;")
                self.unpaired_list_layout.addWidget(more)
        else:
            self.unpaired_summary_lbl.setText(f"当前筛选月份无超过 {win_days} 交易日未配对的买入或卖出{cross_str}")
            self.unpaired_toggle_btn.hide()
            self.unpaired_list_host.hide()
            self._unpaired_expanded = False

    def open_drawer(self, row: List[Any]):
        self.drawer_title.setText(f"{row[3]} 微操详情")
        self.drawer_subtitle.setText(f"{row[0]} · {row[1]} · {row[2]}")

        self._clear_layout(self.drawer_list_layout)
        is_compat = (self.scalping_mode == "EXCEL_COMPAT")
        meta = self.row_meta_map.get((str(row[0]), str(row[1]), str(row[3]).upper())) or {}

        pnl_label = "月内微操收益" if is_compat else "微操净收益"
        items = [
            ("月份", row[0]),
            ("渠道", row[1]),
            ("分类", row[2]),
            ("股票代码", row[3]),
            ("微操次数", f"{row[4]} 次"),
            ("微操数量", f"{row[5]} 股"),
            ("微操均买价", row[6]),
            ("微操均卖价", row[7]),
            ("每股价差", row[8]),
            ("买入金额", row[9]),
            ("卖出金额", row[10]),
            (pnl_label, row[11]),
            ("首笔微操时间", row[12]),
            ("最近微操时间", row[13]),
            ("当前浮动盈亏", row[14]),
        ]
        if not is_compat and meta:
            pos_cnt = meta.get("pos_t_count", 0)
            rev_cnt = meta.get("rev_t_count", 0)
            pos_pnl = fmt_signed_money(meta.get("pos_t_pnl", 0), 2) if pos_cnt else "0 笔"
            rev_pnl = fmt_signed_money(meta.get("rev_t_pnl", 0), 2) if rev_cnt else "0 笔"
            wr = f"{float(meta.get('win_rate') or 0) * 100:.1f}% ({meta.get('win_count', 0)}胜/{meta.get('loss_count', 0)}负)"
            plr = f"{float(meta['profit_loss_ratio']):.2f}" if meta.get("profit_loss_ratio") is not None else "—"
            avg_d = f"{float(meta.get('avg_hold_days') or 0):.1f} 个交易日"
            rep_fee = meta.get("reported_fee")
            fee_txt = f"${fmt_money(rep_fee, 2)}" if rep_fee is not None else "含未核实费用"
            if meta.get("fee_unverified_count"):
                fee_txt += f"（{meta['fee_unverified_count']}笔未核实）"
            items.extend([
                ("正T（先买后卖）", f"{pos_pnl}（{pos_cnt} 笔）" if pos_cnt else "0 笔"),
                ("反T（先卖后买）", f"{rev_pnl}（{rev_cnt} 笔）" if rev_cnt else "0 笔"),
                ("胜率 / 盈亏比", f"{wr} · {plr}"),
                ("平均持仓时长", avg_d),
                ("跨月配对笔数", f"{meta.get('cross_month_count', 0)} 笔"),
                ("已核实手续费", fee_txt),
                ("当月超时转长持", f"{meta.get('expired_buy_count', 0)} 笔 (${fmt_money(meta.get('expired_buy_amount', 0), 2)})"),
                ("当月超时未接回", f"{meta.get('expired_sell_count', 0)} 笔 (${fmt_money(meta.get('expired_sell_amount', 0), 2)})"),
            ])
        items.append(("统计说明", row[15]))

        for label, val in items:
            row_box = QHBoxLayout()
            lbl_key = QLabel(label)
            lbl_key.setStyleSheet("color: #64748B; font-size: 12px;")
            lbl_val = QLabel(str(val))
            is_pnl_row = label in (pnl_label, "当前浮动盈亏", "正T（先买后卖）", "反T（先卖后买）")
            lbl_val.setFont(numeric_font(12, bold=(label in (pnl_label, "当前浮动盈亏"))))
            if is_pnl_row:
                if str(val).startswith("+"):
                    lbl_val.setStyleSheet("color: #10B981; font-weight: bold;")
                elif str(val).startswith("-") or str(val).startswith("−"):
                    lbl_val.setStyleSheet("color: #EF4444; font-weight: bold;")
                else:
                    lbl_val.setStyleSheet("color: #0F172A;")
            else:
                lbl_val.setStyleSheet("color: #0F172A;")
            lbl_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row_box.addWidget(lbl_key)
            row_box.addStretch()
            row_box.addWidget(lbl_val)
            self.drawer_list_layout.addLayout(row_box)

        # 逐笔配对证据列表（实战短线模式）
        pairs_list = meta.get("pairs") or ()
        if pairs_list:
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("background: #E2E8F0; max-height: 1px; border: none; margin: 4px 0;")
            self.drawer_list_layout.addWidget(sep)

            p_hdr = QLabel(f"逐笔配对证据（共 {len(pairs_list)} 笔）")
            p_hdr.setFont(QFont(".AppleSystemUIFont", 12, QFont.Weight.Bold))
            p_hdr.setStyleSheet("color: #0F172A;")
            self.drawer_list_layout.addWidget(p_hdr)

            for idx, p in enumerate(pairs_list, 1):
                p_card = QFrame()
                p_card.setStyleSheet(
                    "QFrame { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; }"
                )
                pv = QVBoxLayout(p_card)
                pv.setContentsMargins(10, 6, 10, 6)
                pv.setSpacing(2)

                top_r = QHBoxLayout()
                cross_badge = " · 跨月" if p.get("cross_month") else ""
                tag_lbl = QLabel(f"#{idx} {p.get('mode_label', '')} · 持仓{p.get('hold_days', 0)}天{cross_badge}")
                tag_lbl.setStyleSheet("color: #1E40AF; font-size: 11px; font-weight: 600; border: none; background: transparent;")
                top_r.addWidget(tag_lbl)
                top_r.addStretch()
                p_pnl = float(p.get("pnl") or 0)
                pnl_c = "#10B981" if p_pnl > 0 else "#EF4444" if p_pnl < 0 else "#475569"
                p_pnl_lbl = QLabel(fmt_signed_money(p_pnl, 2))
                p_pnl_lbl.setFont(numeric_font(11.5, bold=True))
                p_pnl_lbl.setStyleSheet(f"color: {pnl_c}; border: none; background: transparent;")
                top_r.addWidget(p_pnl_lbl)
                pv.addLayout(top_r)

                fee_note = ""
                if not p.get("fee_verified"):
                    fee_note = " · ⚠未核实费"
                elif p.get("reported_fee") is not None and float(p.get("reported_fee") or 0) > 0:
                    fee_note = f" · 费${fmt_money(p['reported_fee'], 2)}"
                sub_lbl = QLabel(
                    f"数量 {fmt_money(p.get('qty'), 2)} 股 · 买 ${fmt_money(p.get('buy_amount'), 2)} → "
                    f"卖 ${fmt_money(p.get('sell_amount'), 2)}{fee_note}"
                )
                sub_lbl.setStyleSheet("color: #475569; font-size: 11px; border: none; background: transparent;")
                pv.addWidget(sub_lbl)

                time_lbl = QLabel(f"买 {str(p.get('buy_time', ''))[:16]}  |  卖 {str(p.get('sell_time', ''))[:16]}")
                time_lbl.setStyleSheet("color: #94A3B8; font-size: 10.5px; border: none; background: transparent;")
                pv.addWidget(time_lbl)
                self.drawer_list_layout.addWidget(p_card)

        btn_replay = QPushButton("查看该标的历史交易回放 →")
        btn_replay.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_replay.setStyleSheet("""
            QPushButton {
                background: #2563EB; color: #FFFFFF; font-weight: 600; font-size: 12px;
                border: none; border-radius: 6px; padding: 8px 12px; margin-top: 10px;
            }
            QPushButton:hover { background: #1D4ED8; }
        """)
        sym = str(row[3])
        m = str(row[0])
        broker = str(row[1]) if len(row) > 1 else ""
        btn_replay.clicked.connect(lambda: self.open_stock_replay(sym, month=m, broker=broker))
        self.drawer_list_layout.addWidget(btn_replay)
        self.drawer_list_layout.addStretch()

        w = 440
        self.drawer_overlay.setGeometry(self.rect())
        self.drawer_frame.setGeometry(self.width() - w, 0, w, self.height())
        self.drawer_overlay.show()
        self.drawer_frame.show()
        self.drawer_overlay.raise_()
        self.drawer_frame.raise_()

    def open_stock_replay(self, sym: str, month: str = "", broker: str = ""):
        """打开该标的的完整交易回放抽屉弹窗（含走势图、买卖点、11列交易记录与月度表现）"""
        from portfolio.services.holdings_management_service import HoldingsManagementService
        from portfolio.ui.holdings_page import StockDrawerWidget

        host = self.window()
        src_path = str(getattr(getattr(host, "service", None), "path", "")) or "数据库/money.sqlite3"
        analytics = self.analytics or getattr(host, "analytics", None)

        person_id = self.applied_person
        if person_id is None and self.person_combo.currentIndex() > 0:
            person_id = self.person_combo.currentData()
        if person_id is None and host and hasattr(host, "person"):
            person_id = host.person.currentData()

        broker_name = broker or (self.broker_combo.currentText() if self.broker_combo.currentIndex() > 0 else None)
        if broker_name in ("全部渠道", ""):
            broker_name = None

        def show_detail(data):
            if not data:
                return
            dialog = QDialog(self)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            title_text = f"交易回放 · {sym}" + (f" · {month}" if month else "")
            dialog.setWindowTitle(title_text)
            dialog.setStyleSheet("QDialog { background-color: #F8FAFC; }")
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(16, 14, 16, 16)
            layout.setSpacing(10)

            top_bar = QHBoxLayout()
            desc = f"标的代码：<b style='color:#1E293B;'>{sym}</b>"
            if month:
                desc += f"&nbsp;&nbsp;·&nbsp;&nbsp;观察月份：<span style='color:#2563EB; font-weight:600;'>{month}</span>"
            if broker_name:
                desc += f"&nbsp;&nbsp;·&nbsp;&nbsp;券商渠道：<span style='color:#475569;'>{broker_name}</span>"
            lbl_info = QLabel(desc)
            lbl_info.setStyleSheet("font-size: 13px; color: #334155;")
            top_bar.addWidget(lbl_info)
            top_bar.addStretch()

            btn_go_trades = QPushButton("在全量「交易明细」中查看 →")
            btn_go_trades.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_go_trades.setStyleSheet("""
                QPushButton {
                    background: #FFFFFF; color: #2563EB; font-weight: 600; font-size: 12px;
                    border: 1px solid #BFDBFE; border-radius: 6px; padding: 6px 14px;
                }
                QPushButton:hover { background: #EFF6FF; border-color: #93C5FD; }
            """)

            def go_trade_center():
                dialog.close()
                self.close_drawer()
                if host and hasattr(host, "drill_record"):
                    host.drill_record({"symbol": sym})
                    return
                if host and hasattr(host, "trade_center"):
                    host.trade_center.fields["symbol"].setText(sym)
                    host.trade_center.query()
                if host and hasattr(host, "navigation"):
                    host.navigation.setCurrentRow(0)

            btn_go_trades.clicked.connect(go_trade_center)
            top_bar.addWidget(btn_go_trades)
            layout.addLayout(top_bar)

            drawer = StockDrawerWidget(dialog)
            drawer.setMaximumWidth(16777215)
            drawer.set_data(data)
            drawer.closed.connect(dialog.close)
            layout.addWidget(drawer, 1)

            dialog.resize(980, 720)
            self._replay_dialog = dialog
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

        if host and hasattr(host, "_submit"):
            host._submit(
                f"scalping_stock_replay_{sym}",
                lambda cancel: HoldingsManagementService.get_stock_drawer_data(
                    src_path, analytics, sym, person_id=person_id if isinstance(person_id, int) else None,
                    broker=broker_name,
                ),
                show_detail
            )
        else:
            data = HoldingsManagementService.get_stock_drawer_data(
                src_path, analytics, sym, person_id=person_id if isinstance(person_id, int) else None,
                broker=broker_name,
            )
            show_detail(data)

    def close_drawer(self):
        self.drawer_overlay.hide()
        self.drawer_frame.hide()

    def export_csv(self):
        if not self.table_model.all_rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出微操收益明细", "每月微操收益明细.csv", "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(self.table_model.columns)
                for r in self.table_model.all_rows:
                    writer.writerow(r)
        except Exception:
            pass
