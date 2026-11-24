"""Google API (Gemini) 配置与月度预算保护弹窗。"""

from decimal import Decimal
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from portfolio.services.ai_service import (
    CodexAIService,
    DEFAULT_GOOGLE_API_BUDGET_USD,
    DEFAULT_GOOGLE_API_MAX_OUTPUT_TOKENS,
    DEFAULT_GOOGLE_API_MODEL,
    GOOGLE_API_MODELS,
)


class GoogleAPIConfigDialog(QDialog):
    """配置 Google Gemini API Key、模型与月度防超额预算上限。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置 Google API (Gemini) 与预算保护")
        self.setMinimumWidth(480)
        self._init_ui()
        self._load_current()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        tip = QLabel(
            "直接通过 Google Gemini API 调用模型（不依赖外部 CLI）。\n"
            "内置本地 Token 计费估算与月度硬性预算熔断，防止超出 Pro 会员每月 $10 额度产生额外扣费。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("font-size: 12px; color: #475569; background: #F8FAFC; padding: 10px; border-radius: 8px;")
        layout.addWidget(tip)

        form = QFormLayout()
        form.setSpacing(10)

        # API Key 输入 + 显示切换
        key_row = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("粘贴 Google AI Studio API Key (AIza...)")
        key_row.addWidget(self.key_input, 1)

        self.toggle_key_btn = QPushButton("显示")
        self.toggle_key_btn.setCheckable(True)
        self.toggle_key_btn.setFixedWidth(56)
        self.toggle_key_btn.toggled.connect(self._on_toggle_key)
        key_row.addWidget(self.toggle_key_btn)
        form.addRow("API Key:", key_row)

        # 模型选择（可编辑）
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        for m in GOOGLE_API_MODELS:
            self.model_combo.addItem(m)
        self.model_combo.setToolTip(
            "日常问答推荐 gemini-2.5-flash（输入 $0.30 / 输出 $2.50 每百万 token）；\n"
            "深度研报可切换 gemini-2.5-pro（单价较高，约 $2.50/$15.00 每百万 token）。"
        )
        form.addRow("默认模型:", self.model_combo)

        # 月度安全预算上限
        self.budget_spin = QDoubleSpinBox()
        self.budget_spin.setRange(0.50, 10.00)
        self.budget_spin.setSingleStep(0.50)
        self.budget_spin.setDecimals(2)
        self.budget_spin.setPrefix("$")
        self.budget_spin.setSuffix(" / 月 (达到上限自动拦截)")
        form.addRow("月度安全上限:", self.budget_spin)

        # 单次最大输出 Token
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(512, 32768)
        self.max_tokens_spin.setSingleStep(512)
        self.max_tokens_spin.setSuffix(" tokens (防单次超长输出)")
        form.addRow("单次输出上限:", self.max_tokens_spin)

        layout.addLayout(form)

        # 本月用量统计与校准
        usage_row = QHBoxLayout()
        self.usage_label = QLabel("")
        self.usage_label.setWordWrap(True)
        self.usage_label.setStyleSheet("font-size: 12px; color: #1E293B; font-weight: 500;")
        usage_row.addWidget(self.usage_label, 1)

        self.calibrate_btn = QPushButton("校准本月已用")
        self.calibrate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.calibrate_btn.clicked.connect(self._on_calibrate_usage)
        usage_row.addWidget(self.calibrate_btn)
        layout.addLayout(usage_row)

        gcp_hint = QLabel(
            "💡 双保险建议：客户端熔断仅统计本软件发起的请求。若同一 API Key 也在其他工具使用，"
            "建议同时在 Google Cloud Console → Billing → Budgets & alerts 中设置 $10 预算提醒。"
        )
        gcp_hint.setWordWrap(True)
        gcp_hint.setStyleSheet("font-size: 11px; color: #64748B;")
        layout.addWidget(gcp_hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存配置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_toggle_key(self, checked: bool):
        self.key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.toggle_key_btn.setText("隐藏" if checked else "显示")

    def _load_current(self):
        cfg = CodexAIService.get_google_api_config()
        self.key_input.setText(cfg.get("api_key") or "")
        model = cfg.get("model") or DEFAULT_GOOGLE_API_MODEL
        idx = self.model_combo.findText(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.setEditText(model)

        try:
            budget_val = float(Decimal(str(cfg.get("monthly_budget_usd") or DEFAULT_GOOGLE_API_BUDGET_USD)))
        except Exception:
            budget_val = float(DEFAULT_GOOGLE_API_BUDGET_USD)
        self.budget_spin.setValue(max(0.50, min(10.00, budget_val)))
        self.max_tokens_spin.setValue(
            int(cfg.get("max_output_tokens") or DEFAULT_GOOGLE_API_MAX_OUTPUT_TOKENS)
        )
        self._refresh_usage_label()

    def _refresh_usage_label(self):
        usage = CodexAIService.get_google_api_monthly_usage()
        spent = Decimal(str(usage.get("estimated_cost_usd") or "0"))
        self.usage_label.setText(
            f"本月 ({usage.get('month')})：已请求 {usage.get('requests', 0)} 次 · "
            f"输入 {usage.get('prompt_tokens', 0):,} / 输出 {usage.get('output_tokens', 0):,} tokens · "
            f"预估已用 ${spent:.4f}"
        )

    def _on_calibrate_usage(self):
        usage = CodexAIService.get_google_api_monthly_usage()
        current_spent = float(Decimal(str(usage.get("estimated_cost_usd") or "0")))
        val, ok = QInputDialog.getDouble(
            self,
            "校准本月已用金额",
            "输入对照 Google Cloud 账单后的本月已用金额 (USD，输入 0 可清零重置):",
            current_spent,
            0.0,
            100.0,
            4,
        )
        if ok:
            CodexAIService.set_google_api_monthly_spent(f"{val:.4f}")
            self._refresh_usage_label()

    def _on_save(self):
        api_key = self.key_input.text().strip()
        model = self.model_combo.currentText().strip() or DEFAULT_GOOGLE_API_MODEL
        budget = Decimal(f"{self.budget_spin.value():.2f}")
        max_tokens = self.max_tokens_spin.value()
        if not api_key:
            QMessageBox.warning(self, "提示", "请输入有效的 Google API Key。")
            return
        CodexAIService.save_google_api_config(
            api_key=api_key,
            model=model,
            monthly_budget_usd=budget,
            max_output_tokens=max_tokens,
        )
        self.accept()
