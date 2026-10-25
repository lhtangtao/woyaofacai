"""AI Assistant Page powered by local Codex CLI.

Provides interactive portfolio analysis, cost-basis diagnosis, and post-exit performance
review using real local investment ledger data with multi-session isolation per
(person, channel, stock) filter combination.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import threading
from PySide6.QtCore import QSize, QStandardPaths, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from portfolio.services.ai_service import CodexAIService
from portfolio.ui.filters import AccountFilter, TickerSearch
from portfolio.ui.icons import icon
from portfolio.ui.theme import numeric_font, tokens


@dataclass
class ChatMessage:
    role: str  # "user" or "assistant"
    content: str
    time_str: str = ""
    scope_tag: str = ""


class AskCodexWorker(QThread):
    """异步执行 Codex 提问的后台工作线程"""
    finished_signal = Signal(str, str)  # (reply, session_key)
    error_signal = Signal(str, str)     # (error_message, session_key)

    def __init__(
        self,
        prompt: str,
        context: str,
        session_key: str,
        model: str | None = None,
        analytics_service=None,
        person_name: str | None = None,
        channel_names: tuple[str, ...] | None = None,
        symbol: str | None = None,
        include_context: bool = True,
    ):
        super().__init__()
        self.prompt = prompt
        self.context = context
        self.session_key = session_key
        self.model = model
        self.analytics_service = analytics_service
        self.person_name = person_name
        self.channel_names = channel_names
        self.symbol = symbol
        self.include_context = include_context
        self.cancel_event = threading.Event()

    def run(self):
        try:
            # 若启用上下文且有 service，在后台线程中极速计算全量人员账本，穿透真实流水与持仓
            if self.include_context and self.analytics_service:
                try:
                    from datetime import date
                    from portfolio.domain.models import Ledger, QueryScope
                    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
                    fresh_analytics = self.analytics_service.calculate(scope)
                    self.context = CodexAIService.build_portfolio_context(
                        fresh_analytics,
                        person_name=self.person_name,
                        channel_names=self.channel_names,
                        symbol=self.symbol,
                    )
                except Exception:
                    pass

            result = CodexAIService.ask(
                self.prompt,
                context=self.context,
                model=self.model,
                cancel_event=self.cancel_event,
            )
            self.finished_signal.emit(result, self.session_key)
        except Exception as e:
            self.error_signal.emit(str(e), self.session_key)

    def cancel(self):
        self.cancel_event.set()


class AutoResizingTextBrowser(QTextBrowser):
    """自适应内容高度的 Markdown 文本浏览器，根据实际视口宽度精准计算高度，杜绝多余留白"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; font-size: 13px; color: #1E293B; line-height: 1.5; padding: 0px; margin: 0px; }"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adjust_content_height()

    def adjust_content_height(self):
        w = self.viewport().width() or self.width()
        if w > 0:
            self.document().setTextWidth(w)
            doc_h = int(self.document().size().height())
            if doc_h > 0:
                self.setFixedHeight(doc_h + 4)


class ChatMessageWidget(QFrame):
    """单条消息气泡组件（支持用户输入与 AI 富文本回复，带筛选范围徽标）"""

    def __init__(self, role: str, content: str, time_str: str = "", scope_tag: str = "", parent=None):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.body_view = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        t = tokens(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        # 头部标题行
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        if role == "user":
            self.setStyleSheet(
                "ChatMessageWidget { background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 12px; }"
            )
            title_label = QLabel("👤 我的提问")
            title_label.setStyleSheet("font-weight: 600; color: #1D4ED8; font-size: 13px;")
        else:
            self.setStyleSheet(
                "ChatMessageWidget { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px; }"
            )
            title_label = QLabel("🤖 Codex 投资顾问")
            title_label.setStyleSheet("font-weight: 600; color: #16A34A; font-size: 13px;")

        header_layout.addWidget(title_label)

        if scope_tag and role == "user":
            scope_badge = QLabel(scope_tag)
            scope_badge.setStyleSheet(
                "background: #DBEAFE; color: #1E40AF; font-size: 11px; padding: 1px 8px; border-radius: 6px; font-weight: 500;"
            )
            header_layout.addWidget(scope_badge)

        time_lbl = QLabel(time_str or datetime.now().strftime("%H:%M:%S"))
        time_lbl.setStyleSheet("color: #94A3B8; font-size: 11px;")
        header_layout.addWidget(time_lbl)
        header_layout.addStretch()

        if role == "assistant":
            copy_btn = QPushButton("复制回答")
            copy_btn.setObjectName("quiet")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet("font-size: 11px; padding: 2px 8px; color: #64748B;")
            copy_btn.clicked.connect(self.copy_content)
            header_layout.addWidget(copy_btn)

        layout.addLayout(header_layout)

        # 内容正文展示
        if role == "assistant":
            self.body_view = AutoResizingTextBrowser(self)
            self.body_view.setMarkdown(content)
            self.body_view.adjust_content_height()
            layout.addWidget(self.body_view)
        else:
            body_lbl = QLabel(content)
            body_lbl.setWordWrap(True)
            body_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body_lbl.setStyleSheet("font-size: 13px; color: #1E293B; line-height: 1.4;")
            layout.addWidget(body_lbl)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.body_view:
            self.body_view.adjust_content_height()

    def copy_content(self):
        QApplication.clipboard().setText(self.content)


class AIAssistantPage(QWidget):
    """
    AI 投资问答与深度分析主页面
    【核心特性】：
    1. 人员、渠道、股票三联筛选联动；
    2. 多会话隔离：不同的（人员、渠道、股票）组合存放在各自独立的对话线程中，互不串扰；
    3. 切换筛选条件自动无缝切换对应会话的历史记录；
    4. 携带真实持仓、成本及清仓表现数据。
    """

    def __init__(self, window=None):
        super().__init__(window)
        self.window = window
        self.worker = None

        # 思考动态耗时计时器与心跳指示
        self._thinking_timer = QTimer(self)
        self._thinking_timer.timeout.connect(self._update_thinking_progress)
        self._thinking_seconds = 0
        self._thinking_dots = 0
        self._need_adjust_views = False

        # 会话存储机制: session_key -> list[ChatMessage]
        self.chat_sessions: dict[str, list[ChatMessage]] = {}
        self.active_session_key: str = ""
        self.load_sessions()

        self._init_ui()
        self.refresh_codex_status(force=False)

    def _history_file_path(self) -> Path:
        """获取本地持久化存储对话历史的 JSON 文件路径"""
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
        base_dir = (Path(app_data) / "MoneyPortfolio") if app_data else Path.home() / ".money_portfolio"
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / "ai_chat_sessions.json"

    def load_sessions(self):
        """从本地磁盘读取持久化历史对话"""
        path = self._history_file_path()
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key, msg_list in data.items():
                    if isinstance(msg_list, list):
                        self.chat_sessions[key] = [
                            ChatMessage(
                                role=m.get("role", "user"),
                                content=m.get("content", ""),
                                time_str=m.get("time_str", ""),
                                scope_tag=m.get("scope_tag", ""),
                            )
                            for m in msg_list
                            if isinstance(m, dict) and "role" in m and "content" in m
                        ]
        except Exception:
            pass

    def save_sessions(self):
        """将当前内存中的所有会话历史持久化保存到本地磁盘"""
        path = self._history_file_path()
        try:
            data = {}
            for key, msg_list in self.chat_sessions.items():
                data[key] = [asdict(m) for m in msg_list[-100:]]  # 每个频道持久化最多最近 100 条
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # 1. 顶部标题与状态卡片
        header_card = QFrame()
        header_card.setObjectName("headerCard")
        header_card.setStyleSheet("QFrame#headerCard { background: #FFFFFF; border: 1px solid #E5EAF1; border-radius: 12px; }")
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(18, 14, 18, 14)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(6)
        title_box = QHBoxLayout()
        title_lbl = QLabel("AI 投资顾问 · 智能问答")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: 700; color: #17253D;")
        title_box.addWidget(title_lbl)

        self.status_badge = QLabel("正在检测 Codex 状态...")
        self.status_badge.setStyleSheet("font-size: 12px; padding: 2px 10px; border-radius: 10px; background: #F1F5F9; color: #64748B;")
        title_box.addWidget(self.status_badge)
        title_box.addStretch()
        info_layout.addLayout(title_box)

        desc_lbl = QLabel("基于本地 Codex CLI 官方驱动 · 免 API 密钥 · 各股票/人员/渠道独立多会话隔离")
        desc_lbl.setStyleSheet("font-size: 12px; color: #64748B;")
        info_layout.addWidget(desc_lbl)

        # --- 用户指定新增的三联选择框 (人员 / 渠道 / 股票) ---
        filter_bar = QHBoxLayout()
        filter_bar.setContentsMargins(0, 6, 0, 0)
        filter_bar.setSpacing(8)

        # (1) 人员筛选下拉框 (默认选中「个人」)
        self.person_combo = QComboBox()
        self.person_combo.setMinimumWidth(115)
        self.person_combo.setToolTip("选择归属人员（个人 / 他人 / 全部人员）")
        self.person_combo.addItem("全部启用人员", None)
        self.person_combo.addItem("个人", 1)
        self.person_combo.addItem("他人", "OTHERS")
        self.person_combo.setCurrentIndex(1)  # 默认选中「个人」
        self.person_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.person_combo)

        # (2) 渠道多选下拉按钮 (默认显示「全部渠道」)
        self.channel_filter = AccountFilter()
        self.channel_filter.setMinimumWidth(125)
        self.channel_filter.setToolTip("选择交易渠道（长桥 / 嘉信 / BBAE / 第一证券 或 全部渠道）")
        self.channel_filter.changed.connect(self._on_filter_changed)
        filter_bar.addWidget(self.channel_filter)

        # (3) 股票搜索与自动补全输入框 (Placeholder:「输入 Ticker」)
        self.stock_search = TickerSearch()
        self.stock_search.setMinimumWidth(148)
        self.stock_search.addItem("全部股票", None)
        self.stock_search.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.stock_search)

        # (4) 重置按钮
        self.reset_filter_btn = QPushButton("重置")
        self.reset_filter_btn.setObjectName("quiet")
        self.reset_filter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_filter_btn.setStyleSheet("font-size: 11px; padding: 4px 8px; color: #64748B;")
        self.reset_filter_btn.setToolTip("重置为个人·全部渠道·全部股票")
        self.reset_filter_btn.clicked.connect(self.reset_filters)
        filter_bar.addWidget(self.reset_filter_btn)

        # (5) 动态焦点与独立会话提示
        self.filter_hint = QLabel("当前会话频道: 个人 · 全部渠道 · 全部股票")
        self.filter_hint.setStyleSheet("font-size: 11px; color: #64748B; margin-left: 6px;")
        filter_bar.addWidget(self.filter_hint)
        filter_bar.addStretch()

        info_layout.addLayout(filter_bar)
        h_layout.addLayout(info_layout, 1)

        # 状态刷新与清空当前会话按钮
        btn_box = QVBoxLayout()
        btn_box.setSpacing(6)
        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(lambda: self.refresh_codex_status(force=True))
        btn_box.addWidget(self.refresh_btn)

        self.clear_btn = QPushButton("清空此会话")
        self.clear_btn.setObjectName("quiet")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setToolTip("仅清空当前这个组合的对话记录，不影响其他股票或全局会话")
        self.clear_btn.clicked.connect(self.clear_current_chat)
        btn_box.addWidget(self.clear_btn)
        h_layout.addLayout(btn_box)

        root.addWidget(header_card)

        # 2. 快捷提问预设胶囊区 (Prompt Chips)
        chips_frame = QFrame()
        chips_frame.setStyleSheet("background: transparent; border: none;")
        chips_layout = QHBoxLayout(chips_frame)
        chips_layout.setContentsMargins(0, 0, 0, 0)
        chips_layout.setSpacing(8)

        chips_layout.addWidget(QLabel("快捷洞察:"))

        presets = [
            ("📊 诊断持仓成本与做T空间", "请分析我当前选定范围内的持仓成本与浮动盈亏，重点评估该标的的成本安全垫以及后续补仓/做T摊薄的空间。"),
            ("🚀 清仓卖飞与逃顶复盘", "请结合我的已清仓标的清仓后表现数据，详细分析哪些股票成功逃顶、哪些股票过早卖飞，并总结清仓时机经验。"),
            ("📝 生成近期投资总结", "请综合我的资产总体表现、净投入与持仓盈亏，为我生成一份条理分明的近期投资复盘与表现总结报告。"),
            ("🛡️ 杠杆与大盘风险评估", "请评估我投资组合中大盘 ETF 与杠杆标的的仓位暴露比例与风险集中度。"),
        ]

        for label_text, prompt_text in presets:
            btn = QPushButton(label_text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 14px; padding: 5px 12px; font-size: 12px; color: #334155; }"
                "QPushButton:hover { background: #F8FAFC; border-color: #2563EB; color: #2563EB; }"
            )
            btn.clicked.connect(lambda _, p=prompt_text: self.send_preset_prompt(p))
            chips_layout.addWidget(btn)

        chips_layout.addStretch()
        root.addWidget(chips_frame)

        # 3. 对话展示滚动区域
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 4, 0, 8)
        self.chat_layout.setSpacing(14)
        self.chat_layout.addStretch()

        self.scroll_area.setWidget(self.chat_container)
        root.addWidget(self.scroll_area, 1)

        # 4. 底部输入与控制区
        bottom_card = QFrame()
        bottom_card.setObjectName("bottomCard")
        bottom_card.setStyleSheet("QFrame#bottomCard { background: #FFFFFF; border: 1px solid #E5EAF1; border-radius: 12px; }")
        bottom_layout = QVBoxLayout(bottom_card)
        bottom_layout.setContentsMargins(16, 12, 16, 12)
        bottom_layout.setSpacing(10)

        # 上下文携带开关
        ctrl_bar = QHBoxLayout()
        self.context_checkbox = QCheckBox("携带当前组合真实背景（向 AI 注入当前人员/渠道/标的的真实成本、持仓与流水）")
        self.context_checkbox.setChecked(True)
        self.context_checkbox.setStyleSheet("font-size: 12px; color: #475569;")
        ctrl_bar.addWidget(self.context_checkbox)

        ctrl_bar.addStretch()
        self.status_hint = QLabel("")
        self.status_hint.setStyleSheet("font-size: 12px; color: #64748B;")
        ctrl_bar.addWidget(self.status_hint)
        bottom_layout.addLayout(ctrl_bar)

        # 输入框与发送按钮栏
        input_bar = QHBoxLayout()
        input_bar.setSpacing(10)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText("在此输入您的投资疑问、成本分析或策略需求...（Enter 发送，Shift + Enter 换行）")
        self.input_edit.setFixedHeight(72)
        self.input_edit.setStyleSheet(
            "QPlainTextEdit { border: 1px solid #CBD5E1; border-radius: 8px; padding: 8px 10px; font-size: 13px; background: #FFFFFF; }"
            "QPlainTextEdit:focus { border: 1px solid #2563EB; }"
        )
        self.input_edit.keyPressEvent = self._on_key_press
        input_bar.addWidget(self.input_edit, 1)

        btn_vbox = QVBoxLayout()
        self.send_btn = QPushButton("发 送")
        self.send_btn.setObjectName("primary")
        self.send_btn.setFixedHeight(40)
        self.send_btn.setFixedWidth(88)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(
            "QPushButton#primary { background: #2563EB; color: #FFFFFF; font-weight: 600; border-radius: 8px; font-size: 14px; }"
            "QPushButton#primary:hover { background: #1D4ED8; }"
            "QPushButton#primary:disabled { background: #94A3B8; }"
        )
        self.send_btn.clicked.connect(self.handle_send)
        btn_vbox.addWidget(self.send_btn)

        self.cancel_btn = QPushButton("中 止")
        self.cancel_btn.setObjectName("quiet")
        self.cancel_btn.setFixedHeight(26)
        self.cancel_btn.setFixedWidth(88)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.handle_cancel)
        btn_vbox.addWidget(self.cancel_btn)

        input_bar.addLayout(btn_vbox)
        bottom_layout.addLayout(input_bar)

        root.addWidget(bottom_card)

        # 初始化会话 Key 并展示默认会话
        self.active_session_key = self.current_session_key()
        self.switch_to_session(self.active_session_key)

    def showEvent(self, event):
        super().showEvent(event)
        # 如果后台 AI 正在运算中，切回时保持锁死与平滑动态指示，严禁重置会话或触发阻塞检测
        if self.worker and self.worker.isRunning():
            self.send_btn.setEnabled(False)
            self.input_edit.setEnabled(False)
            self.cancel_btn.setVisible(True)
            if not self._thinking_timer.isActive():
                self._thinking_timer.start(1000)
            return

        # 检查是否需要补齐筛选器
        if self.stock_search.count() <= 1 and self.window and getattr(self.window, "info", None):
            self.populate_filters(self.window.info)

        # 刷新所有气泡的高度（如果在后台期间接收到了新回复）
        if self._need_adjust_views:
            self._need_adjust_views = False
            self._adjust_all_message_views()

        # 使用缓存刷新状态，绝不阻塞主线程
        self.refresh_codex_status(force=False)

    def _adjust_all_message_views(self):
        """当页面从隐藏状态切回时，对所有 Markdown 气泡重新计算视口高度，贴合底部"""
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, ChatMessageWidget) and w.body_view:
                w.body_view.adjust_content_height()
        self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())
        QTimer.singleShot(60, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))

    # --- 会话 Key 与多会话独立隔离机制 ---

    def current_session_key(self) -> str:
        """根据当前选中的人员、渠道与股票计算唯一的会话频道标识"""
        person_txt = self.person_combo.currentText().strip() or "全部人员"
        channel_txt = self.channel_filter.text().strip() or "全部渠道"
        stock_txt = self.stock_search.currentText().strip() or "全部股票"
        sym = stock_txt.split(" · ")[0].strip().upper().removesuffix(".US") if stock_txt != "全部股票" else "全部股票"
        return f"{person_txt}___{channel_txt}___{sym}"

    def switch_to_session(self, new_session_key: str):
        """
        无缝切换到新会话频道：
        1. 清理当前视图中的消息气泡；
        2. 载入对应会话频道的历史消息；
        3. 如果新会话首次进入，生成该频道的专属欢迎卡片。
        """
        self.active_session_key = new_session_key

        # 清除现有气泡组件（保留最下方的 stretch）
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 检查该会话是否有历史消息
        history = self.chat_sessions.get(new_session_key)
        if not history:
            # 首次进入此组合频道，生成专属引导卡片
            welcome_msg = self._generate_channel_welcome_message(new_session_key)
            first_chat = ChatMessage(role="assistant", content=welcome_msg, time_str=datetime.now().strftime("%H:%M:%S"))
            self.chat_sessions[new_session_key] = [first_chat]
            self._render_message_widget(first_chat)
        else:
            # 还原渲染该频道的全部历史消息
            for msg in history:
                self._render_message_widget(msg)

        QApplication.processEvents()
        self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())

    def _generate_channel_welcome_message(self, session_key: str) -> str:
        parts = session_key.split("___")
        person_txt = parts[0] if len(parts) > 0 else "全部人员"
        channel_txt = parts[1] if len(parts) > 1 else "全部渠道"
        sym_txt = parts[2] if len(parts) > 2 else "全部股票"

        if sym_txt != "全部股票":
            return (
                f"您好！已为您切换至 **{sym_txt}** 的专属独立对话频道。\n\n"
                f"• **当前范围**：归属人员【{person_txt}】· 交易渠道【{channel_txt}】· 锁定标的【**{sym_txt}**】\n"
                f"• **独立隔离**：在此提出的所有问题（持仓成本诊断、做T流水复盘、买卖策略分析等）均**完全独立保存**，不会与其它股票或全量账本对话串扰。\n\n"
                f"您可以直接针对 **{sym_txt}** 提出任何疑问，AI 将自动穿透分析该股票在该组合下的成本均价与近期流水。"
            )
        else:
            return (
                f"您好！已为您切换至 **全局资产与组合** 宏观对话频道。\n\n"
                f"• **当前范围**：归属人员【{person_txt}】· 交易渠道【{channel_txt}】· 全部股票\n"
                f"• **独立隔离**：在此处提出的问题聚焦于投资组合总资产、大盘与杠杆分布比例及整体表现，与单只股票的细分对话相互隔离。\n\n"
                f"如需针对某只具体标的（如 TSLL、NVDA）深入诊断，只需在上方「输入 Ticker」中选择该股票即可无缝切换独立频道。"
            )

    def _on_filter_changed(self, *_):
        self._update_filter_hint()
        new_key = self.current_session_key()
        if new_key != self.active_session_key:
            self.switch_to_session(new_key)

    def _update_filter_hint(self):
        person_txt = self.person_combo.currentText()
        channel_txt = self.channel_filter.text()
        stock_txt = self.stock_search.currentText().strip()
        if stock_txt and stock_txt != "全部股票":
            sym = stock_txt.split(" · ")[0].strip().upper().removesuffix(".US")
            self.filter_hint.setText(f"🎯 独立会话频道: 【{sym}】 ({person_txt} · {channel_txt}) · 对话完全隔离")
            self.filter_hint.setStyleSheet("font-size: 11px; color: #2563EB; font-weight: 600; margin-left: 6px;")
            self.clear_btn.setText(f"清空【{sym}】对话")
        else:
            self.filter_hint.setText(f"当前会话频道: {person_txt} · {channel_txt} · 全部股票 (宏观全局)")
            self.filter_hint.setStyleSheet("font-size: 11px; color: #64748B; margin-left: 6px;")
            self.clear_btn.setText("清空此会话")

    def populate_filters(self, info):
        """同步数据库中的人员、渠道及股票列表（仅展示美股且不带 .us 后缀）"""
        if not info:
            return

        # 1. 人员列表
        cur_person = self.person_combo.currentData()
        self.person_combo.blockSignals(True)
        self.person_combo.clear()
        self.person_combo.addItem("全部启用人员", None)
        self.person_combo.addItem("个人", 1)
        self.person_combo.addItem("他人", "OTHERS")
        for p in getattr(info, "persons", ()):
            if p.get("name") not in ("个人", "他人"):
                self.person_combo.addItem(p["name"], p["id"])
        idx = self.person_combo.findData(cur_person if cur_person is not None else 1)
        self.person_combo.setCurrentIndex(max(1, idx))
        self.person_combo.blockSignals(False)

        # 2. 渠道列表
        if hasattr(info, "accounts"):
            self.channel_filter.populate(info.accounts)

        # 3. 股票列表（仅筛选美股，去除 .us 后缀，保持整洁）
        self.stock_search.blockSignals(True)
        self.stock_search.clear()
        self.stock_search.addItem("全部股票", None)
        seen_symbols = set()
        us_instruments = []
        for inst in getattr(info, "instruments", ()):
            if inst.get("market") != "US":
                continue
            raw_sym = inst.get("symbol", "").strip()
            clean_sym = raw_sym.upper().removesuffix(".US")
            if not clean_sym or clean_sym in seen_symbols:
                continue
            seen_symbols.add(clean_sym)
            us_instruments.append((clean_sym, inst["id"]))

        us_instruments.sort(key=lambda x: x[0])
        for clean_sym, inst_id in us_instruments:
            self.stock_search.addItem(clean_sym, inst_id)
        self.stock_search.blockSignals(False)

        self._update_filter_hint()
        new_key = self.current_session_key()
        if new_key != self.active_session_key:
            self.switch_to_session(new_key)

    def reset_filters(self):
        self.person_combo.setCurrentIndex(1)  # 恢复个人
        self.channel_filter.reset()
        self.stock_search.setCurrentIndex(0)
        self._update_filter_hint()
        self.switch_to_session(self.current_session_key())

    def clear_current_chat(self):
        """仅清空当前这个组合频道的对话历史，绝不影响其它股票或全局会话"""
        if self.active_session_key in self.chat_sessions:
            self.chat_sessions[self.active_session_key] = []
            self.save_sessions()
        self.switch_to_session(self.active_session_key)

    def refresh_codex_status(self, force: bool = False):
        status = CodexAIService.check_login_status(force=force)
        is_running = bool(self.worker and self.worker.isRunning())
        if status["logged_in"]:
            account = status["account_type"] or "ChatGPT"
            self.status_badge.setText(f"🟢 已连接 Codex ({account} 订阅)")
            self.status_badge.setStyleSheet("font-size: 12px; padding: 2px 10px; border-radius: 10px; background: #DCFCE7; color: #166534; font-weight: 600;")
            self.send_btn.setEnabled(not is_running)
        elif status["installed"]:
            self.status_badge.setText("🟡 未登录 (请在终端执行 codex login)")
            self.status_badge.setStyleSheet("font-size: 12px; padding: 2px 10px; border-radius: 10px; background: #FEF9C3; color: #854D0E;")
            self.send_btn.setEnabled(False)
        else:
            self.status_badge.setText("🔴 未安装 Codex CLI")
            self.status_badge.setStyleSheet("font-size: 12px; padding: 2px 10px; border-radius: 10px; background: #FEE2E2; color: #991B1B;")
            self.send_btn.setEnabled(False)

    def _update_thinking_progress(self):
        """动态心跳计时器槽函数：秒级递增并刷新动态动画点，杜绝卡死感"""
        if not (self.worker and self.worker.isRunning()):
            self._thinking_timer.stop()
            return
        self._thinking_seconds += 1
        self._thinking_dots = (self._thinking_dots + 1) % 4
        dots = "." * self._thinking_dots
        self.status_hint.setText(f"🤖 Codex 正在思考并分析数据中... (已耗时 {self._thinking_seconds} 秒 · 运算中{dots})")
        self.status_hint.repaint()

    def send_preset_prompt(self, prompt: str):
        if self.worker and self.worker.isRunning():
            return
        self.input_edit.setPlainText(prompt)
        self.handle_send()

    def _on_key_press(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            if self.worker and self.worker.isRunning():
                event.accept()
                return
            self.handle_send()
            event.accept()
        else:
            QPlainTextEdit.keyPressEvent(self.input_edit, event)

    def handle_send(self):
        # 思考中严格拦截，绝不允许再次发送或并发触发
        if self.worker and self.worker.isRunning():
            return

        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        try:
            person_txt = self.person_combo.currentText()
            channel_txt = self.channel_filter.text()
            stock_txt = self.stock_search.currentText().strip()
            selected_channels = tuple(action.text() for action in self.channel_filter.actions_by_id.values() if action.isChecked()) or None
            target_symbol = stock_txt.split(" · ")[0].strip().upper().removesuffix(".US") if stock_txt and stock_txt != "全部股票" else None

            current_key = self.active_session_key
            scope_tag = f"{person_txt} · {channel_txt}" + (f" · {target_symbol}" if target_symbol else "")
            user_msg = ChatMessage(role="user", content=text, time_str=datetime.now().strftime("%H:%M:%S"), scope_tag=scope_tag)

            # 存入当前独立会话历史
            self.chat_sessions.setdefault(current_key, []).append(user_msg)
            self._render_message_widget(user_msg)
            self.save_sessions()
            self.input_edit.clear()

            # 构建上下文数据（传入用户选中的人员、渠道与股票）
            analytics_svc = getattr(self.window, "analytics_service", None) if self.window else None
            include_ctx = self.context_checkbox.isChecked()
            static_context = ""
            if include_ctx and not analytics_svc:
                analytics = getattr(self.window, "analytics", None) if self.window else None
                static_context = CodexAIService.build_portfolio_context(
                    analytics,
                    person_name=person_txt,
                    channel_names=selected_channels,
                    symbol=target_symbol,
                )

            # 思考中双重锁死：发送按钮置灰，输入框禁用，显示中止按钮
            self.send_btn.setEnabled(False)
            self.input_edit.setEnabled(False)
            self.cancel_btn.setVisible(True)

            # 启动秒级心跳耗时指示
            self._thinking_seconds = 0
            self._thinking_dots = 0
            self.status_hint.setText("🤖 Codex 正在思考并分析数据中... (已耗时 0 秒 · 运算中)")
            self._thinking_timer.start(1000)

            # 启动后台任务，带入 session_key 并支持后台穿透计算
            self.worker = AskCodexWorker(
                text,
                context=static_context,
                session_key=current_key,
                analytics_service=analytics_svc,
                person_name=person_txt,
                channel_names=selected_channels,
                symbol=target_symbol,
                include_context=include_ctx,
            )
            self.worker.finished_signal.connect(self._on_worker_finished)
            self.worker.error_signal.connect(self._on_worker_error)
            self.worker.start()
        except Exception as e:
            if self._thinking_timer.isActive():
                self._thinking_timer.stop()
            self.status_hint.setText(f"❌ 处理异常: {e}")
            err_card = ChatMessage(
                role="assistant",
                content=f"⚠️ **发送请求时发生内部错误**：\n\n```\n{e}\n```\n请检查输入或相关配置。",
                time_str=datetime.now().strftime("%H:%M:%S"),
            )
            self.chat_sessions.setdefault(self.active_session_key, []).append(err_card)
            self._render_message_widget(err_card)
            self._reset_input_state()

    def handle_cancel(self):
        if self._thinking_timer.isActive():
            self._thinking_timer.stop()
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            cancel_card = ChatMessage(
                role="assistant",
                content="⏸️ *您已主动中止了本次分析请求。可调整提问或筛选条件后重新发送。*",
                time_str=datetime.now().strftime("%H:%M:%S"),
            )
            self.chat_sessions.setdefault(self.active_session_key, []).append(cancel_card)
            self._render_message_widget(cancel_card)
            self.save_sessions()
            self._reset_input_state()

    def _on_worker_finished(self, reply: str, session_key: str):
        elapsed = self._thinking_seconds
        if self._thinking_timer.isActive():
            self._thinking_timer.stop()

        asst_msg = ChatMessage(role="assistant", content=reply, time_str=datetime.now().strftime("%H:%M:%S"))
        # 存入发起该请求时的专属会话历史
        self.chat_sessions.setdefault(session_key, []).append(asst_msg)
        self.save_sessions()
        # 只有当用户当前依然停留在该会话频道时才直接渲染在界面上，防止串屏
        if session_key == self.active_session_key:
            self._render_message_widget(asst_msg)
        self._reset_input_state()
        self.status_hint.setText(f"✅ 分析完成 (总耗时 {elapsed} 秒)")
        QTimer.singleShot(4000, lambda: self.status_hint.setText("") if not (self.worker and self.worker.isRunning()) else None)

    def _on_worker_error(self, err_msg: str, session_key: str):
        if self._thinking_timer.isActive():
            self._thinking_timer.stop()
        error_reply = f"⚠️ **调用 Codex 遇到问题**：\n\n```\n{err_msg}\n```\n请检查本地 Codex 登录状态或网络连接。"
        asst_msg = ChatMessage(role="assistant", content=error_reply, time_str=datetime.now().strftime("%H:%M:%S"))
        self.chat_sessions.setdefault(session_key, []).append(asst_msg)
        self.save_sessions()
        if session_key == self.active_session_key:
            self._render_message_widget(asst_msg)
        self._reset_input_state()
        self.status_hint.setText("⚠️ 分析中断或出错")
        QTimer.singleShot(4000, lambda: self.status_hint.setText("") if not (self.worker and self.worker.isRunning()) else None)

    def _reset_input_state(self):
        self.send_btn.setEnabled(True)
        self.input_edit.setEnabled(True)
        self.input_edit.setFocus()
        self.cancel_btn.setVisible(False)
        self.worker = None

    def _render_message_widget(self, msg: ChatMessage):
        """在当前展示流中追加渲染单个气泡"""
        msg_widget = ChatMessageWidget(
            role=msg.role,
            content=msg.content,
            time_str=msg.time_str,
            scope_tag=msg.scope_tag,
            parent=self.chat_container,
        )
        insert_idx = max(0, self.chat_layout.count() - 1)
        self.chat_layout.insertWidget(insert_idx, msg_widget)
        if not self.isVisible():
            self._need_adjust_views = True
        else:
            QApplication.processEvents()
            self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())
            QTimer.singleShot(50, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))
