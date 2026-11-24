"""Google API (Gemini) 配置与月度预算保护弹窗。"""

from decimal import Decimal
from PySide6.QtCore import Qt, QThread, Signal
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
        self.setMinimumWidth(560)
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

        # 模型选择（标准下拉框 + 实时拉取并自动展开账号最新可用模型）
        model_box = QVBoxLayout()
        model_box.setSpacing(4)
        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)
        self.model_combo.setMinimumWidth(260)
        self.model_combo.setMaxVisibleItems(16)
        for m in GOOGLE_API_MODELS:
            self.model_combo.addItem(m)
        self.model_combo.setToolTip(
            "推荐：gemini-3.8-flash（高性价比新一代 Flash）；\n"
            "深度研报可选 gemini-4-neon / gemini-4-argon（单价较高，注意月度预算）；\n"
            "点击右侧「拉取最新模型」可实时获取并展开您账号下所有可用 Gemini 模型。"
        )
        model_row.addWidget(self.model_combo, 1)

        self.fetch_models_btn = QPushButton("拉取最新模型")
        self.fetch_models_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fetch_models_btn.clicked.connect(self._on_fetch_models)
        model_row.addWidget(self.fetch_models_btn)
        model_box.addLayout(model_row)

        self.model_fetch_hint = QLabel("")
        self.model_fetch_hint.setStyleSheet("font-size: 11px; color: #15803D;")
        self.model_fetch_hint.setVisible(False)
        model_box.addWidget(self.model_fetch_hint)
        form.addRow("默认模型:", model_box)

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

    def _on_fetch_models(self):
        api_key = self.key_input.text().strip()
        if not api_key:
            QMessageBox.warning(self, "提示", "请先在上方输入 Google API Key。")
            return
        self.fetch_models_btn.setEnabled(False)
        self.fetch_models_btn.setText("拉取中...")
        try:
            models = CodexAIService.list_google_api_models(api_key=api_key)
            if not models:
                QMessageBox.information(self, "提示", "未查询到支持 generateContent 的 Gemini 模型。")
                return
            current = self.model_combo.currentText().strip()
            self.model_combo.clear()
            for m in models:
                self.model_combo.addItem(m)
            idx = self.model_combo.findText(current)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            self.model_fetch_hint.setText(f"✅ 已拉取 {len(models)} 个可用模型，请在上方下拉列表选择")
            self.model_fetch_hint.setVisible(True)
            self.model_combo.setFocus()
            self.model_combo.showPopup()
        except Exception as exc:
            QMessageBox.warning(self, "拉取失败", str(exc))
        finally:
            self.fetch_models_btn.setEnabled(True)
            self.fetch_models_btn.setText("拉取最新模型")

    def _load_current(self):
        cfg = CodexAIService.get_google_api_config()
        self.key_input.setText(cfg.get("api_key") or "")
        model = cfg.get("model") or DEFAULT_GOOGLE_API_MODEL
        idx = self.model_combo.findText(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.insertItem(0, model)
            self.model_combo.setCurrentIndex(0)

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


class AgyModelFetchWorker(QThread):
    """后台异步拉取本机 Antigravity CLI (agy) 可用模型列表，避免阻塞 UI。"""

    models_ready = Signal(list)

    def __init__(self, force: bool = False, parent=None):
        super().__init__(parent)
        self.force = force

    def run(self):
        models = CodexAIService.list_agy_models(force=self.force)
        self.models_ready.emit(models)


def populate_agy_model_combo(combo: QComboBox, models: list[tuple[str, str]], selected_model: str):
    """填充 agy 模型下拉框并保留用户当前选中的模型（不触发多余信号）。"""
    combo.blockSignals(True)
    try:
        combo.clear()
        found_idx = -1
        for idx, (m_id, m_label) in enumerate(models):
            combo.addItem(m_label, m_id)
            if m_id == selected_model:
                found_idx = idx
        if found_idx < 0 and selected_model:
            combo.addItem(selected_model, selected_model)
            found_idx = combo.count() - 1
        if found_idx >= 0:
            combo.setCurrentIndex(found_idx)
    finally:
        combo.blockSignals(False)

