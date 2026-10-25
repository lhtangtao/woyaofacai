"""Rule creation and editing dialog/drawer for market monitoring center."""

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.monitor_rules import (
    ChannelType,
    CheckMode,
    ConditionType,
    MonitorRule,
    RepeatMode,
    SessionScope,
    describe_rule,
)
from portfolio.domain.technical_indicators import PERIODS
from portfolio.ui.theme import tokens


class RuleDrawerDialog(QDialog):
    """
    Comprehensive rule editor dialog supporting all PRD v2.0 rule types,
    trading sessions, trigger modes, and notification options.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        rule: Optional[MonitorRule] = None,
        default_symbol: str = "",
        default_type: str = "PRICE",
        default_expr: Optional[Dict[str, Any]] = None,
        available_symbols: Optional[List[Dict[str, Any]]] = None,
        scope_key: str = "",
    ):
        super().__init__(parent)
        self.existing_rule = rule
        self.default_symbol = default_symbol or (rule.analysis_symbol if rule else "")
        self.scope_key = scope_key or (rule.scope_key if rule else "")
        self.available_symbols = available_symbols or []

        self.setWindowTitle("编辑监控规则" if rule else "新建监控规则")
        self.resize(640, 720)
        self.setMinimumWidth(560)
        self.setModal(True)

        self._init_ui()
        if rule:
            self._load_rule(rule)
        elif default_type:
            self.type_combo.setCurrentText(self._type_to_label(default_type))
            if default_expr:
                self._apply_default_expr(default_type, default_expr)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(16)

        # 1. Target Selector Section
        target_group = QGroupBox("1. 监控标的")
        target_layout = QFormLayout(target_group)
        target_layout.setSpacing(10)

        self.symbol_combo = QComboBox()
        self.symbol_combo.setEditable(True)
        for t in self.available_symbols:
            sym = t.get("analysis_symbol", "")
            sources = t.get("sources", [])
            clean_sym = sym.removesuffix(".US")
            desc = clean_sym
            if sources:
                held_clean = sources[0].get('held_symbol', '').removesuffix(".US")
                desc += f" (持仓: {held_clean})"
            self.symbol_combo.addItem(desc, sym)

        if self.default_symbol:
            idx = self.symbol_combo.findData(self.default_symbol)
            if idx >= 0:
                self.symbol_combo.setCurrentIndex(idx)
            else:
                self.symbol_combo.setEditText(self.default_symbol.removesuffix(".US"))
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_changed)
        target_layout.addRow("分析标的代码:", self.symbol_combo)

        # Underlying vs ETF selection
        self.target_type_combo = QComboBox()
        self.target_type_combo.addItem("正股 / 分析标的 (默认)", "UNDERLYING")
        self.target_type_combo.addItem("ETF 自身代码", "ETF")
        self.etf_code_input = QLineEdit()
        self.etf_code_input.setPlaceholderText("例如 AAOX (如需直接监控杠杆ETF)")
        self.etf_code_input.setVisible(False)
        self.target_type_combo.currentIndexChanged.connect(
            lambda idx: self.etf_code_input.setVisible(idx == 1)
        )
        target_layout.addRow("监控对象层级:", self.target_type_combo)
        target_layout.addRow("ETF 代码:", self.etf_code_input)

        layout.addWidget(target_group)

        # 2. Condition Type & Expression Section
        cond_group = QGroupBox("2. 监控触发条件")
        cond_layout = QVBoxLayout(cond_group)
        cond_layout.setSpacing(12)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("条件类别:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "固定价格突破 / 跌破",
            "股价与均线交叉",
            "均线金叉 / 死叉",
            "MACD 指标提醒",
            "RSI 强弱提醒",
            "成交量异常放大",
            "52 周新高 / 新低",
            "历史最高价回撤",
            "多条件组合监控 (AND / OR)",
        ])
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self.type_combo, 1)
        cond_layout.addLayout(type_row)

        # Stacked widgets for different condition parameters
        self.expr_stack = QStackedWidget()
        self._build_expr_pages()
        cond_layout.addWidget(self.expr_stack)

        layout.addWidget(cond_group)

        # 3. Strategy & Environment Section
        strat_group = QGroupBox("3. 触发策略与时段")
        strat_layout = QFormLayout(strat_group)
        strat_layout.setSpacing(10)

        # Check Mode
        self.check_mode_combo = QComboBox()
        self.check_mode_combo.addItem("盘中实时动态判断", "INTRADAY")
        self.check_mode_combo.addItem("日K收盘价确认 (收盘后或休市判定)", "DAILY_CLOSE")
        strat_layout.addRow("判定时机:", self.check_mode_combo)

        # Session Scope
        self.session_combo = QComboBox()
        self.session_combo.addItem("仅正常交易时段 (9:30-16:00 美东)", "REGULAR")
        self.session_combo.addItem("包含盘前盘后时段 (4:00-20:00 美东)", "EXTENDED")
        strat_layout.addRow("交易时段:", self.session_combo)

        # Repeat Mode
        self.repeat_combo = QComboBox()
        self.repeat_combo.addItem("重新穿越后再次提醒 (防止价格徘徊误报)", "REARM_CROSSING")
        self.repeat_combo.addItem("仅提醒单次 (触发后规则自动完成并停用)", "ONCE")
        self.repeat_combo.addItem("冷却时间间隔 (触发后限制短时间内重复通知)", "COOLDOWN")
        self.repeat_combo.currentIndexChanged.connect(self._on_repeat_changed)
        strat_layout.addRow("重复策略:", self.repeat_combo)

        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(60, 86400)
        self.cooldown_spin.setValue(1800)
        self.cooldown_spin.setSuffix(" 秒 (30分钟)")
        self.cooldown_spin.setVisible(False)
        self.cooldown_label = QLabel("冷却时长:")
        self.cooldown_label.setVisible(False)
        strat_layout.addRow(self.cooldown_label, self.cooldown_spin)

        layout.addWidget(strat_group)

        # 4. Notification Channels & Sound
        notify_group = QGroupBox("4. 提醒方式")
        notify_layout = QFormLayout(notify_group)
        notify_layout.setSpacing(10)

        ch_layout = QHBoxLayout()
        self.ch_in_app = QCheckBox("应用内提醒")
        self.ch_in_app.setChecked(True)
        ch_layout.addWidget(self.ch_in_app)

        self.ch_desktop = QCheckBox("macOS 桌面系统通知")
        self.ch_desktop.setChecked(True)
        ch_layout.addWidget(self.ch_desktop)

        self.ch_sound = QCheckBox("播放提示音")
        self.ch_sound.setChecked(True)
        ch_layout.addWidget(self.ch_sound)
        ch_layout.addStretch()
        notify_layout.addRow("通知渠道:", ch_layout)

        sound_row = QHBoxLayout()
        self.sound_combo = QComboBox()
        self.sound_combo.addItems(["Glass", "Ping", "Submarine", "Morse", "Hero", "Basso", "Bottle"])
        sound_row.addWidget(self.sound_combo)
        self.test_sound_btn = QPushButton("试听声音")
        self.test_sound_btn.clicked.connect(self._test_sound)
        sound_row.addWidget(self.test_sound_btn)
        sound_row.addStretch()
        notify_layout.addRow("提示声音:", sound_row)

        layout.addWidget(notify_group)

        # 5. Rule Name and Note
        name_group = QGroupBox("5. 规则备注")
        name_layout = QFormLayout(name_group)
        name_layout.setSpacing(10)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("留空将根据条件自动生成清晰名称")
        name_layout.addRow("规则名称:", self.name_input)

        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("选填：例如设定的技术依据、止损位置等")
        name_layout.addRow("备注说明:", self.note_input)

        layout.addWidget(name_group)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        btn_layout.addStretch()

        self.save_paused_btn = QPushButton("保存规则（默认暂停）")
        self.save_paused_btn.clicked.connect(lambda: self._save(enabled=False))
        btn_layout.addWidget(self.save_paused_btn)

        self.save_enabled_btn = QPushButton("保存并立即启用")
        self.save_enabled_btn.setStyleSheet("font-weight: bold; background-color: #2563EB; color: white;")
        self.save_enabled_btn.clicked.connect(lambda: self._save(enabled=True))
        btn_layout.addWidget(self.save_enabled_btn)

        root.addLayout(btn_layout)

    def _build_expr_pages(self):
        """Construct dedicated parameter panels for each of the 9 condition types."""
        # 0. PRICE
        p0 = QWidget()
        l0 = QFormLayout(p0)
        self.p_price_dir = QComboBox()
        self.p_price_dir.addItem("股价突破高于 (>= 阈值)", "CROSS_ABOVE")
        self.p_price_dir.addItem("股价跌破低于 (<= 阈值)", "CROSS_BELOW")
        l0.addRow("方向:", self.p_price_dir)
        self.p_price_val = QDoubleSpinBox()
        self.p_price_val.setRange(0.001, 999999.0)
        self.p_price_val.setDecimals(3)
        self.p_price_val.setValue(100.0)
        self.p_price_val.setPrefix("$ ")
        l0.addRow("目标价格:", self.p_price_val)
        self.expr_stack.addWidget(p0)

        # 1. PRICE_MA_CROSS
        p1 = QWidget()
        l1 = QFormLayout(p1)
        self.p_pma_dir = QComboBox()
        self.p_pma_dir.addItem("股价上穿均线 (突破)", "CROSS_ABOVE")
        self.p_pma_dir.addItem("股价下穿均线 (跌破)", "CROSS_BELOW")
        l1.addRow("交叉方向:", self.p_pma_dir)
        self.p_pma_period = QComboBox()
        for p in PERIODS:
            self.p_pma_period.addItem(f"MA{p} 日均线", p)
        self.p_pma_period.setCurrentIndex(6)  # MA200
        l1.addRow("目标均线:", self.p_pma_period)
        self.expr_stack.addWidget(p1)

        # 2. MA_CROSS
        p2 = QWidget()
        l2 = QFormLayout(p2)
        self.p_ma_dir = QComboBox()
        self.p_ma_dir.addItem("金叉 (快线上穿慢线)", "CROSS_ABOVE")
        self.p_ma_dir.addItem("死叉 (快线下穿慢线)", "CROSS_BELOW")
        l2.addRow("形态:", self.p_ma_dir)
        self.p_ma_fast = QComboBox()
        for p in (5, 10, 20, 50):
            self.p_ma_fast.addItem(f"MA{p}", p)
        self.p_ma_fast.setCurrentText("MA50")
        l2.addRow("短期均线 (快线):", self.p_ma_fast)
        self.p_ma_slow = QComboBox()
        for p in (20, 50, 60, 120, 200, 250):
            self.p_ma_slow.addItem(f"MA{p}", p)
        self.p_ma_slow.setCurrentText("MA200")
        l2.addRow("长期均线 (慢线):", self.p_ma_slow)
        self.expr_stack.addWidget(p2)

        # 3. MACD
        p3 = QWidget()
        l3 = QFormLayout(p3)
        self.p_macd_type = QComboBox()
        self.p_macd_type.addItem("DIF 上穿 DEA (金叉)", "GOLDEN_CROSS")
        self.p_macd_type.addItem("DIF 下穿 DEA (死叉)", "DEATH_CROSS")
        self.p_macd_type.addItem("柱状图由负转正 (红柱翻绿)", "HIST_TURN_POSITIVE")
        self.p_macd_type.addItem("柱状图由正转负 (绿柱翻红)", "HIST_TURN_NEGATIVE")
        l3.addRow("MACD 信号:", self.p_macd_type)
        self.expr_stack.addWidget(p3)

        # 4. RSI
        p4 = QWidget()
        l4 = QFormLayout(p4)
        self.p_rsi_dir = QComboBox()
        self.p_rsi_dir.addItem("RSI 上穿超卖线 (超卖反弹)", "CROSS_ABOVE")
        self.p_rsi_dir.addItem("RSI 下穿超买线 (超买回调)", "CROSS_BELOW")
        l4.addRow("方向:", self.p_rsi_dir)
        self.p_rsi_val = QDoubleSpinBox()
        self.p_rsi_val.setRange(1.0, 99.0)
        self.p_rsi_val.setDecimals(1)
        self.p_rsi_val.setValue(30.0)
        l4.addRow("RSI 阈值:", self.p_rsi_val)
        self.p_rsi_dir.currentIndexChanged.connect(
            lambda idx: self.p_rsi_val.setValue(70.0 if idx == 1 else 30.0)
        )
        self.expr_stack.addWidget(p4)

        # 5. VOLUME
        p5 = QWidget()
        l5 = QFormLayout(p5)
        self.p_vol_mult = QDoubleSpinBox()
        self.p_vol_mult.setRange(1.1, 20.0)
        self.p_vol_mult.setSingleStep(0.5)
        self.p_vol_mult.setValue(2.0)
        self.p_vol_mult.setSuffix(" 倍")
        l5.addRow("放量倍数:", self.p_vol_mult)
        self.p_vol_base = QComboBox()
        self.p_vol_base.addItem("5日均量 (VMA5)", 5)
        self.p_vol_base.addItem("20日均量 (VMA20)", 20)
        l5.addRow("对比基准:", self.p_vol_base)
        self.expr_stack.addWidget(p5)

        # 6. WEEK52
        p6 = QWidget()
        l6 = QFormLayout(p6)
        self.p_w52_type = QComboBox()
        self.p_w52_type.addItem("突破创 52 周新高", "NEW_HIGH")
        self.p_w52_type.addItem("跌破创 52 周新低", "NEW_LOW")
        l6.addRow("新高新低:", self.p_w52_type)
        self.expr_stack.addWidget(p6)

        # 7. ATH_DRAWDOWN
        p7 = QWidget()
        l7 = QFormLayout(p7)
        self.p_ath_type = QComboBox()
        self.p_ath_type.addItem("创历史新高 (突破历史峰值)", "NEW_HIGH")
        self.p_ath_type.addItem("较历史最高价回撤达到阈值", "DRAWDOWN_REACH")
        l7.addRow("模式:", self.p_ath_type)
        self.p_ath_dd = QDoubleSpinBox()
        self.p_ath_dd.setRange(1.0, 99.0)
        self.p_ath_dd.setValue(20.0)
        self.p_ath_dd.setSuffix(" %")
        l7.addRow("回撤幅度:", self.p_ath_dd)
        self.p_ath_type.currentIndexChanged.connect(
            lambda idx: self.p_ath_dd.setEnabled(idx == 1)
        )
        self.expr_stack.addWidget(p7)

        # 8. COMPOSITE
        p8 = QWidget()
        l8 = QVBoxLayout(p8)
        self.p_comp_logic = QComboBox()
        self.p_comp_logic.addItem("同时满足以下全部条件 (AND)", "ALL")
        self.p_comp_logic.addItem("满足以下任一条件即可 (OR)", "ANY")
        l8.addWidget(self.p_comp_logic)

        self.comp_hint = QLabel("组合条件：例如【股价跌破MA20】同时【RSI < 40】")
        self.comp_hint.setStyleSheet("color: #64748B; font-size: 12px;")
        l8.addWidget(self.comp_hint)

        # Pre-configured composite presets
        self.comp_preset = QComboBox()
        self.comp_preset.addItem("预设 1: 股价跌破 MA20 且 RSI < 40 (超卖下破)", "PRESET_MA20_RSI")
        self.comp_preset.addItem("预设 2: MACD金叉 且 股价突破 MA50 (强势启动)", "PRESET_MACD_MA50")
        self.comp_preset.addItem("预设 3: 52周新高 且 成交量放大2倍 (放量突破)", "PRESET_HIGH52_VOL")
        l8.addWidget(self.comp_preset)
        self.expr_stack.addWidget(p8)

    def _type_to_label(self, t: str) -> str:
        mapping = {
            "PRICE": "固定价格突破 / 跌破",
            "PRICE_MA_CROSS": "股价与均线交叉",
            "MA_CROSS": "均线金叉 / 死叉",
            "MACD": "MACD 指标提醒",
            "RSI": "RSI 强弱提醒",
            "VOLUME": "成交量异常放大",
            "WEEK52": "52 周新高 / 新低",
            "ATH_DRAWDOWN": "历史最高价回撤",
            "COMPOSITE": "多条件组合监控 (AND / OR)",
        }
        return mapping.get(t, "固定价格突破 / 跌破")

    def _label_to_type(self, lbl: str) -> str:
        mapping = {
            "固定价格突破 / 跌破": "PRICE",
            "股价与均线交叉": "PRICE_MA_CROSS",
            "均线金叉 / 死叉": "MA_CROSS",
            "MACD 指标提醒": "MACD",
            "RSI 强弱提醒": "RSI",
            "成交量异常放大": "VOLUME",
            "52 周新高 / 新低": "WEEK52",
            "历史最高价回撤": "ATH_DRAWDOWN",
            "多条件组合监控 (AND / OR)": "COMPOSITE",
        }
        return mapping.get(lbl, "PRICE")

    def _on_type_changed(self, idx: int):
        self.expr_stack.setCurrentIndex(idx)

    def _on_symbol_changed(self, text: str):
        pass

    def _on_repeat_changed(self, idx: int):
        is_cooldown = (idx == 2)
        self.cooldown_label.setVisible(is_cooldown)
        self.cooldown_spin.setVisible(is_cooldown)

    def _test_sound(self):
        from portfolio.services.notification_service import notification_service
        sound = self.sound_combo.currentText()
        notification_service.play_sound(sound)

    def _apply_default_expr(self, rule_type: str, expr: Dict[str, Any]):
        if rule_type == "PRICE":
            self.p_price_dir.setCurrentIndex(0 if expr.get("operator") == "CROSS_ABOVE" else 1)
            if "threshold" in expr:
                self.p_price_val.setValue(float(expr["threshold"]))
        elif rule_type == "PRICE_MA_CROSS":
            self.p_pma_dir.setCurrentIndex(0 if expr.get("operator") == "CROSS_ABOVE" else 1)
            p = expr.get("period", 200)
            idx = self.p_pma_period.findData(p)
            if idx >= 0:
                self.p_pma_period.setCurrentIndex(idx)
        elif rule_type == "RSI":
            self.p_rsi_dir.setCurrentIndex(0 if expr.get("operator") == "CROSS_ABOVE" else 1)
            if "threshold" in expr:
                self.p_rsi_val.setValue(float(expr["threshold"]))

    def _load_rule(self, rule: MonitorRule):
        # 1. Target
        idx = self.symbol_combo.findData(rule.analysis_symbol)
        if idx >= 0:
            self.symbol_combo.setCurrentIndex(idx)
        else:
            self.symbol_combo.setEditText(rule.analysis_symbol)

        if rule.monitored_symbol and rule.monitored_symbol != rule.analysis_symbol:
            self.target_type_combo.setCurrentIndex(1)
            self.etf_code_input.setText(rule.monitored_symbol)
            self.etf_code_input.setVisible(True)

        # 2. Type & Expr
        self.type_combo.setCurrentText(self._type_to_label(rule.rule_type))
        expr = rule.expression or {}
        t = rule.rule_type
        if t == "PRICE":
            self.p_price_dir.setCurrentIndex(0 if expr.get("operator") == "CROSS_ABOVE" else 1)
            self.p_price_val.setValue(float(expr.get("threshold", 100.0)))
        elif t == "PRICE_MA_CROSS":
            self.p_pma_dir.setCurrentIndex(0 if expr.get("operator") == "CROSS_ABOVE" else 1)
            p = expr.get("period", 200)
            idx = self.p_pma_period.findData(p)
            if idx >= 0:
                self.p_pma_period.setCurrentIndex(idx)
        elif t == "MA_CROSS":
            self.p_ma_dir.setCurrentIndex(0 if expr.get("operator") == "CROSS_ABOVE" else 1)
            self.p_ma_fast.setCurrentText(f"MA{expr.get('fast_period', 50)}")
            self.p_ma_slow.setCurrentText(f"MA{expr.get('slow_period', 200)}")
        elif t == "MACD":
            op = expr.get("operator", "GOLDEN_CROSS")
            map_op = {
                "GOLDEN_CROSS": 0, "DEATH_CROSS": 1,
                "HIST_TURN_POSITIVE": 2, "HIST_TURN_NEGATIVE": 3
            }
            self.p_macd_type.setCurrentIndex(map_op.get(op, 0))
        elif t == "RSI":
            self.p_rsi_dir.setCurrentIndex(0 if expr.get("operator") == "CROSS_ABOVE" else 1)
            self.p_rsi_val.setValue(float(expr.get("threshold", 30.0)))
        elif t == "VOLUME":
            self.p_vol_mult.setValue(float(expr.get("multiplier", 2.0)))
            idx = self.p_vol_base.findData(expr.get("base_period", 5))
            if idx >= 0:
                self.p_vol_base.setCurrentIndex(idx)
        elif t == "WEEK52":
            self.p_w52_type.setCurrentIndex(0 if expr.get("operator") == "NEW_HIGH" else 1)
        elif t == "ATH_DRAWDOWN":
            self.p_ath_type.setCurrentIndex(0 if expr.get("operator") == "NEW_HIGH" else 1)
            self.p_ath_dd.setValue(float(expr.get("drawdown_pct", 20.0)))

        # 3. Strat
        idx = self.check_mode_combo.findData(rule.check_mode)
        if idx >= 0:
            self.check_mode_combo.setCurrentIndex(idx)

        idx = self.session_combo.findData(rule.session_scope)
        if idx >= 0:
            self.session_combo.setCurrentIndex(idx)

        idx = self.repeat_combo.findData(rule.repeat_mode)
        if idx >= 0:
            self.repeat_combo.setCurrentIndex(idx)
            self._on_repeat_changed(idx)

        self.cooldown_spin.setValue(rule.cooldown_seconds)

        # 4. Notification
        channels = rule.notification_channels.split(",")
        self.ch_in_app.setChecked("in_app" in channels)
        self.ch_desktop.setChecked("desktop" in channels)
        self.ch_sound.setChecked("sound" in channels)
        self.sound_combo.setCurrentText(rule.sound_name or "Glass")

        # 5. Name & Note
        self.name_input.setText(rule.name)
        self.note_input.setText(rule.note)

    def _collect_expression(self, rule_type: str) -> Dict[str, Any]:
        expr = {}
        if rule_type == "PRICE":
            expr = {
                "operator": self.p_price_dir.currentData(),
                "threshold": str(Decimal(str(round(self.p_price_val.value(), 3)))),
            }
        elif rule_type == "PRICE_MA_CROSS":
            expr = {
                "operator": self.p_pma_dir.currentData(),
                "period": int(self.p_pma_period.currentData()),
            }
        elif rule_type == "MA_CROSS":
            fast = int(self.p_ma_fast.currentText().replace("MA", ""))
            slow = int(self.p_ma_slow.currentText().replace("MA", ""))
            if fast >= slow:
                raise ValueError("短期均线周期必须小于长期均线周期！")
            expr = {
                "operator": self.p_ma_dir.currentData(),
                "fast_period": fast,
                "slow_period": slow,
            }
        elif rule_type == "MACD":
            op_map = {
                0: "GOLDEN_CROSS", 1: "DEATH_CROSS",
                2: "HIST_TURN_POSITIVE", 3: "HIST_TURN_NEGATIVE"
            }
            expr = {"operator": op_map[self.p_macd_type.currentIndex()]}
        elif rule_type == "RSI":
            expr = {
                "operator": self.p_rsi_dir.currentData(),
                "threshold": str(Decimal(str(round(self.p_rsi_val.value(), 1)))),
            }
        elif rule_type == "VOLUME":
            expr = {
                "multiplier": str(Decimal(str(round(self.p_vol_mult.value(), 2)))),
                "base_period": int(self.p_vol_base.currentData()),
            }
        elif rule_type == "WEEK52":
            expr = {
                "operator": "NEW_HIGH" if self.p_w52_type.currentIndex() == 0 else "NEW_LOW"
            }
        elif rule_type == "ATH_DRAWDOWN":
            is_high = (self.p_ath_type.currentIndex() == 0)
            expr = {
                "operator": "NEW_HIGH" if is_high else "DRAWDOWN_REACH",
                "drawdown_pct": str(Decimal(str(round(self.p_ath_dd.value(), 1)))) if not is_high else "0",
            }
        elif rule_type == "COMPOSITE":
            preset = self.comp_preset.currentData()
            logic = self.p_comp_logic.currentData()
            if preset == "PRESET_MA20_RSI":
                sub_exprs = [
                    {"rule_type": "PRICE_MA_CROSS", "operator": "CROSS_BELOW", "period": 20},
                    {"rule_type": "RSI", "operator": "CROSS_BELOW", "threshold": "40.0"},
                ]
            elif preset == "PRESET_MACD_MA50":
                sub_exprs = [
                    {"rule_type": "MACD", "operator": "GOLDEN_CROSS"},
                    {"rule_type": "PRICE_MA_CROSS", "operator": "CROSS_ABOVE", "period": 50},
                ]
            else:
                sub_exprs = [
                    {"rule_type": "WEEK52", "operator": "NEW_HIGH"},
                    {"rule_type": "VOLUME", "multiplier": "2.0", "base_period": 5},
                ]
            expr = {
                "logic": logic,
                "conditions": sub_exprs,
            }
        return expr

    def _save(self, enabled: bool):
        sym_text = self.symbol_combo.currentText().strip()
        data_sym = self.symbol_combo.currentData()
        analysis_sym = data_sym or sym_text.split(" ")[0].upper()
        if not analysis_sym:
            QMessageBox.warning(self, "输入错误", "请选择或输入有效的标的代码！")
            return

        if "." not in analysis_sym:
            analysis_sym = f"{analysis_sym}.US"

        monitored_sym = analysis_sym
        if self.target_type_combo.currentIndex() == 1:
            etf_code = self.etf_code_input.text().strip().upper()
            if etf_code:
                if "." not in etf_code:
                    etf_code = f"{etf_code}.US"
                monitored_sym = etf_code

        rule_type = self._label_to_type(self.type_combo.currentText())

        try:
            expr = self._collect_expression(rule_type)
        except Exception as e:
            QMessageBox.warning(self, "参数错误", str(e))
            return

        # Notification channels
        chs = []
        if self.ch_in_app.isChecked():
            chs.append("in_app")
        if self.ch_desktop.isChecked():
            chs.append("desktop")
        if self.ch_sound.isChecked():
            chs.append("sound")
        if not chs:
            QMessageBox.warning(self, "提醒方式", "请至少勾选一种提醒方式！")
            return

        check_mode = self.check_mode_combo.currentData()
        session_scope = self.session_combo.currentData()
        repeat_mode = self.repeat_combo.currentData()
        cooldown_sec = self.cooldown_spin.value()
        sound_name = self.sound_combo.currentText()
        user_name = self.name_input.text().strip()
        note = self.note_input.text().strip()

        if self.existing_rule:
            self.result_rule = self.existing_rule
            self.result_rule.analysis_symbol = analysis_sym
            self.result_rule.monitored_symbol = monitored_sym
            self.result_rule.rule_type = rule_type
            self.result_rule.expression = expr
            self.result_rule.check_mode = check_mode
            self.result_rule.session_scope = session_scope
            self.result_rule.repeat_mode = repeat_mode
            self.result_rule.cooldown_seconds = cooldown_sec
            self.result_rule.notification_channels = ",".join(chs)
            self.result_rule.sound_name = sound_name
            self.result_rule.enabled = enabled
            self.result_rule.name = user_name or describe_rule(self.result_rule)
            self.result_rule.note = note
        else:
            self.result_rule = MonitorRule(
                rule_id="",
                scope_key=self.scope_key,
                analysis_symbol=analysis_sym,
                monitored_symbol=monitored_sym,
                rule_type=rule_type,
                expression=expr,
                check_mode=check_mode,
                session_scope=session_scope,
                repeat_mode=repeat_mode,
                cooldown_seconds=cooldown_sec,
                notification_channels=",".join(chs),
                sound_name=sound_name,
                enabled=enabled,  # Defaults to False unless user clicked 'Save and Enable'
                name=user_name,
                note=note,
            )
            if not self.result_rule.name:
                self.result_rule.name = describe_rule(self.result_rule)

        self.accept()
