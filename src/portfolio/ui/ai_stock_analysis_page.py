"""AI Stock Analysis Page powered by local Codex CLI.

Dedicated pure stock fundamental, earnings, and business outlook analysis assistant.
Filtered solely by Ticker (without personal holdings, ledger data, or channel splits).
Provides multi-session conversation isolation per stock symbol and local disk persistence.
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
from portfolio.ui.filters import TickerSearch
from portfolio.ui.icons import icon
from portfolio.ui.theme import numeric_font, tokens


@dataclass
class StockChatMessage:
    role: str  # "user" or "assistant"
    content: str
    time_str: str = ""
    symbol_tag: str = ""


class AskStockWorker(QThread):
    """异步执行单股基本面与前景分析的后台工作线程"""
    finished_signal = Signal(str, str)  # (reply, session_key)
    error_signal = Signal(str, str)     # (error_message, session_key)

    def __init__(self, prompt: str, symbol: str, session_key: str, model: str | None = None):
        super().__init__()
        self.prompt = prompt
        self.symbol = symbol
        self.session_key = session_key
        self.model = model
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = CodexAIService.ask_stock(
                self.prompt,
                symbol=self.symbol,
                model=self.model,
                cancel_event=self.cancel_event,
            )
            self.finished_signal.emit(result, self.session_key)
        except Exception as e:
            self.error_signal.emit(str(e), self.session_key)

    def cancel(self):
        self.cancel_event.set()


class AutoResizingStockBrowser(QTextBrowser):
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


class StockChatMessageWidget(QFrame):
    """单条股票分析消息气泡组件（支持用户提问与 AI 专业研报富文本回复）"""

    def __init__(self, role: str, content: str, time_str: str = "", symbol_tag: str = "", parent=None):
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
                "StockChatMessageWidget { background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 12px; }"
            )
            title_label = QLabel("👤 我的分析提问")
            title_label.setStyleSheet("font-weight: 600; color: #15803D; font-size: 13px;")
        else:
            self.setStyleSheet(
                "StockChatMessageWidget { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 12px; }"
            )
            title_label = QLabel("📈 Codex 证券研究员")
            title_label.setStyleSheet("font-weight: 600; color: #0284C7; font-size: 13px;")

        header_layout.addWidget(title_label)

        if symbol_tag and role == "user":
            badge = QLabel(symbol_tag)
            badge.setStyleSheet(
                "background: #DCFCE7; color: #166534; font-size: 11px; padding: 1px 8px; border-radius: 6px; font-weight: 600;"
            )
            header_layout.addWidget(badge)

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

        # 正文展示
        if role == "assistant":
            self.body_view = AutoResizingStockBrowser(self)
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


class AIStockAnalysisPage(QWidget):
    """
    AI 股票分析主页面（纯股票基本面、财报与商业前景研究）
    【核心特性】：
    1. 仅提供股票（Ticker）筛选框，去除持有人与渠道等个人私有账本干扰；
    2. 围绕标的公司本身的基本面、商业模式、财报财务表现、竞争格局与前景进行深度研判；
    3. 按股票代码（Ticker）完全独立多会话隔离，切换股票秒级切台；
    4. 本地持久化存储，下次启动无缝还原各标的的历史分析。
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

        # 会话存储机制: stock_symbol -> list[StockChatMessage]
        self.chat_sessions: dict[str, list[StockChatMessage]] = {}
        self.active_session_key: str = ""
        self.load_sessions()

        self._init_ui()
        self.refresh_codex_status(force=False)

    def _history_file_path(self) -> Path:
        """获取纯股票分析本地持久化存储的 JSON 文件路径"""
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
        base_dir = (Path(app_data) / "MoneyPortfolio") if app_data else Path.home() / ".money_portfolio"
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / "ai_stock_analysis_sessions.json"

    def load_sessions(self):
        """从本地磁盘读取持久化股票分析历史"""
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
                            StockChatMessage(
                                role=m.get("role", "user"),
                                content=m.get("content", ""),
                                time_str=m.get("time_str", ""),
                                symbol_tag=m.get("symbol_tag", ""),
                            )
                            for m in msg_list
                            if isinstance(m, dict) and "role" in m and "content" in m
                        ]
        except Exception:
            pass

    def save_sessions(self):
        """将当前内存中的所有股票分析会话历史持久化写盘"""
        path = self._history_file_path()
        try:
            data = {}
            for key, msg_list in self.chat_sessions.items():
                data[key] = [asdict(m) for m in msg_list[-100:]]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # 1. 顶部标题与股票控制栏
        header_card = QFrame()
        header_card.setObjectName("headerCard")
        header_card.setStyleSheet("QFrame#headerCard { background: #FFFFFF; border: 1px solid #E5EAF1; border-radius: 12px; }")
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(18, 14, 18, 14)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(6)
        title_box = QHBoxLayout()
        title_lbl = QLabel("AI 股票分析 · 标的深度研报")
        title_lbl.setStyleSheet("font-size: 18px; font-weight: 700; color: #17253D;")
        title_box.addWidget(title_lbl)

        self.status_badge = QLabel("正在检测 Codex 状态...")
        self.status_badge.setStyleSheet("font-size: 12px; padding: 2px 10px; border-radius: 10px; background: #F1F5F9; color: #64748B;")
        title_box.addWidget(self.status_badge)
        title_box.addStretch()
        info_layout.addLayout(title_box)

        desc_lbl = QLabel("纯粹聚焦标的本身的基本面、财报指标、商业前景与竞争格局 · 不受个人账本数据干扰 · 标的独立对话隔离")
        desc_lbl.setStyleSheet("font-size: 12px; color: #64748B;")
        info_layout.addWidget(desc_lbl)

        # --- 仅保留股票（Ticker）筛选框 ---
        filter_bar = QHBoxLayout()
        filter_bar.setContentsMargins(0, 6, 0, 0)
        filter_bar.setSpacing(8)

        lbl_select = QLabel("锁定研究股票:")
        lbl_select.setStyleSheet("font-size: 13px; font-weight: 600; color: #334155;")
        filter_bar.addWidget(lbl_select)

        self.stock_search = TickerSearch()
        self.stock_search.setMinimumWidth(200)
        self.stock_search.setToolTip("输入或选择要深度分析的目标股票代码（如 INTC、NVDA、AAPL）")
        self.stock_search.currentIndexChanged.connect(self._on_stock_changed)
        filter_bar.addWidget(self.stock_search)

        reset_btn = QPushButton("重置为全部股票")
        reset_btn.setObjectName("quiet")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setStyleSheet("font-size: 12px; padding: 4px 10px; color: #64748B;")
        reset_btn.clicked.connect(self.reset_stock)
        filter_bar.addWidget(reset_btn)

        self.filter_hint = QLabel("🎯 当前标的: 全部股票 (宏观行业模式)")
        self.filter_hint.setStyleSheet("font-size: 11px; color: #64748B; margin-left: 6px;")
        filter_bar.addWidget(self.filter_hint)

        filter_bar.addStretch()

        self.clear_btn = QPushButton("清空此标的对话")
        self.clear_btn.setObjectName("quiet")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setStyleSheet("font-size: 12px; padding: 4px 12px; color: #EF4444; font-weight: 500;")
        self.clear_btn.clicked.connect(self.clear_current_chat)
        filter_bar.addWidget(self.clear_btn)

        info_layout.addLayout(filter_bar)
        h_layout.addLayout(info_layout, 1)

        refresh_btn = QPushButton("刷新状态")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.setStyleSheet("font-size: 12px; padding: 6px 14px; border: 1px solid #CBD5E1; border-radius: 8px; background: #FFFFFF; color: #334155;")
        refresh_btn.clicked.connect(lambda: self.refresh_codex_status(force=True))
        h_layout.addWidget(refresh_btn, 0, Qt.AlignmentFlag.AlignTop)

        root.addWidget(header_card)

        # 2. 快捷股票分析提问胶囊
        chips_frame = QFrame()
        chips_layout = QHBoxLayout(chips_frame)
        chips_layout.setContentsMargins(0, 0, 0, 0)
        chips_layout.setSpacing(8)

        chips_layout.addWidget(QLabel("股票研究洞察:"))

        presets = [
            ("📊 财报深度解读与核心指标", "请深入解读该公司的最新财报表现，重点分析营收增速、毛利率变动、自由现金流状况及关键盈利质量指标。"),
            ("🚀 核心商业模式与护城河", "请分析该公司的核心商业壁垒、护城河深度，以及相对于行业主要竞争对手的关键优势与劣势。"),
            ("🔮 未来 1~3 年增长催化剂", "请分析未来 1 到 3 年该公司可能迎来的核心增长驱动力、行业结构性机遇以及重要的业务催化剂事件。"),
            ("⚠️ 潜在下行风险与利空隐患", "请深入剖析当前该公司面临的核心下行风险、行业周期性压力、监管或客户集中度风险。"),
        ]

        for label_text, prompt_text in presets:
            btn = QPushButton(label_text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 14px; padding: 5px 12px; font-size: 12px; color: #334155; }"
                "QPushButton:hover { background: #F8FAFC; border-color: #0284C7; color: #0284C7; }"
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

        # 状态行
        status_row = QHBoxLayout()
        desc_hint = QLabel("💡 提示: 纯粹围绕选定股票本身的研究讨论，不加载您的持仓成本与成交流水。")
        desc_hint.setStyleSheet("font-size: 11px; color: #64748B;")
        status_row.addWidget(desc_hint)
        status_row.addStretch()

        self.status_hint = QLabel("")
        self.status_hint.setStyleSheet("font-size: 12px; color: #2563EB; font-weight: 500;")
        status_row.addWidget(self.status_hint)
        bottom_layout.addLayout(status_row)

        # 输入框与发送控制
        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText("在此输入您对该股票的分析疑问、财报讨论、行业竞争或商业前景需求…（Enter 发送，Shift + Enter 换行）")
        self.input_edit.setFixedHeight(72)
        self.input_edit.setStyleSheet(
            "QPlainTextEdit { border: 1px solid #CBD5E1; border-radius: 8px; padding: 10px 12px; font-size: 13px; color: #1E293B; background: #FFFFFF; }"
            "QPlainTextEdit:focus { border-color: #0284C7; }"
        )
        self.input_edit.keyPressEvent = self._on_key_press
        input_row.addWidget(self.input_edit, 1)

        btn_box = QVBoxLayout()
        btn_box.setSpacing(6)

        self.send_btn = QPushButton("发送分析")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(
            "QPushButton { background: #0284C7; color: #FFFFFF; font-weight: 600; font-size: 13px; border-radius: 8px; padding: 0px 22px; min-height: 40px; border: none; }"
            "QPushButton:hover { background: #0369A1; }"
            "QPushButton:disabled { background: #94A3B8; }"
        )
        self.send_btn.clicked.connect(self.handle_send)
        btn_box.addWidget(self.send_btn)

        self.cancel_btn = QPushButton("中止")
        self.cancel_btn.setObjectName("quiet")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet("font-size: 11px; color: #94A3B8; padding: 2px;")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.handle_cancel)
        btn_box.addWidget(self.cancel_btn)

        input_row.addLayout(btn_box)
        bottom_layout.addLayout(input_row)

        root.addWidget(bottom_card)

        # 初始化会话
        init_key = self.current_session_key()
        self.switch_to_session(init_key)

    def current_session_key(self) -> str:
        """根据当前选中的股票计算唯一的会话频道标识"""
        stock_txt = self.stock_search.currentText().strip() or "全部股票"
        sym = stock_txt.split(" · ")[0].strip().upper().removesuffix(".US") if stock_txt != "全部股票" else "全部股票"
        return sym

    def switch_to_session(self, new_session_key: str):
        """无缝切换至对应股票的专属分析频道（实时载入可能在手机端新增的咨询记录）"""
        self.load_sessions()
        self.active_session_key = new_session_key

        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        history = self.chat_sessions.get(new_session_key)
        if not history:
            welcome_msg = self._generate_stock_welcome_message(new_session_key)
            first_chat = StockChatMessage(role="assistant", content=welcome_msg, time_str=datetime.now().strftime("%H:%M:%S"))
            self.chat_sessions[new_session_key] = [first_chat]
            self._render_message_widget(first_chat)
            self.save_sessions()
        else:
            for msg in history:
                self._render_message_widget(msg)

        QApplication.processEvents()
        self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())

    def _generate_stock_welcome_message(self, symbol: str) -> str:
        if symbol != "全部股票":
            return (
                f"您好！已为您切换至 **{symbol}** 的纯股票深度研究频道。\n\n"
                f"• **研究标的**：**{symbol}**\n"
                f"• **纯粹独立**：本页面**不依赖您的个人数据库账本**，专注于深入剖析 **{symbol}** 这家公司本身的商业模式、财报财务健康度、技术壁垒、竞争护城河与未来成长前景。\n\n"
                f"您可以直接针对 **{symbol}** 的近期财报、核心业务亮点或未来催化剂提出任何疑问。"
            )
        else:
            return (
                f"您好！已为您进入 **全市场与行业宏观** 股票分析频道。\n\n"
                f"• **当前范围**：全部股票（宏观行业研究模式）\n"
                f"• **独立隔离**：在此处提出的问题聚焦于宏观市场、行业板块趋势或赛道对比。\n\n"
                f"如需针对某只具体标的（如 INTC、NVDA、AAPL）进行深入研报诊断，只需在上方「输入 Ticker」中选择即可。"
            )

    def _on_stock_changed(self, *_):
        self._update_filter_hint()
        new_key = self.current_session_key()
        if new_key != self.active_session_key:
            self.switch_to_session(new_key)

    def _update_filter_hint(self):
        stock_txt = self.stock_search.currentText().strip()
        if stock_txt and stock_txt != "全部股票":
            sym = stock_txt.split(" · ")[0].strip().upper().removesuffix(".US")
            self.filter_hint.setText(f"🎯 当前标的: 【{sym}】 · 专属独立分析会话")
            self.filter_hint.setStyleSheet("font-size: 11px; color: #0284C7; font-weight: 600; margin-left: 6px;")
            self.clear_btn.setText(f"清空【{sym}】分析")
        else:
            self.filter_hint.setText("🎯 当前标的: 全部股票 (宏观行业模式)")
            self.filter_hint.setStyleSheet("font-size: 11px; color: #64748B; margin-left: 6px;")
            self.clear_btn.setText("清空此会话")

    def populate_filters(self, info):
        """同步数据库中的证券标的列表供联想搜索（仅展示美股且不带 .us 后缀）"""
        if not info:
            return
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

    def reset_stock(self):
        self.stock_search.setCurrentIndex(0)
        self._update_filter_hint()
        self.switch_to_session(self.current_session_key())

    def select_stock(self, symbol: str):
        """外部快捷跳转接口：锁定并切换至指定的股票进行深度研报分析"""
        if not symbol:
            return
        clean_sym = symbol.strip().upper().removesuffix(".US")

        # 查找下拉框中是否已有对应证券
        matched_idx = -1
        for i in range(self.stock_search.count()):
            txt = self.stock_search.itemText(i)
            s = txt.split(" · ")[0].strip().upper().removesuffix(".US")
            if s == clean_sym:
                matched_idx = i
                break

        if matched_idx >= 0:
            self.stock_search.setCurrentIndex(matched_idx)
        else:
            # 若列表中未预置，动态插入并选中（仅展示代码本身，如 AAOI / AAOX）
            self.stock_search.addItem(clean_sym, clean_sym)
            self.stock_search.setCurrentIndex(self.stock_search.count() - 1)

        self._update_filter_hint()
        self.switch_to_session(clean_sym)

    def showEvent(self, event):
        super().showEvent(event)
        # 如果后台 AI 正在分析运算中，切回时保持锁死与平滑动态指示，严禁重置会话或触发阻塞检测
        if self.worker and self.worker.isRunning():
            self.send_btn.setEnabled(False)
            self.input_edit.setEnabled(False)
            self.cancel_btn.setVisible(True)
            if not self._thinking_timer.isActive():
                self._thinking_timer.start(1000)
            return

        # 检查是否需要补齐标的列表
        if self.stock_search.count() <= 1 and self.window and getattr(self.window, "info", None):
            self.populate_filters(self.window.info)
        else:
            # 实时从本地磁盘载入可能在手机端新增的咨询会话
            self.switch_to_session(self.current_session_key())

        # 刷新所有气泡的高度（如果在后台期间接收到了新研报）
        if self._need_adjust_views:
            self._need_adjust_views = False
            self._adjust_all_message_views()

        # 使用缓存刷新状态，绝不阻塞主线程
        self.refresh_codex_status(force=False)

    def _adjust_all_message_views(self):
        """当页面从隐藏状态切回时，对所有 Markdown 研报气泡重新计算视口高度，贴合底部"""
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, StockChatMessageWidget) and w.body_view:
                w.body_view.adjust_content_height()
        self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum())
        QTimer.singleShot(60, lambda: self.scroll_area.verticalScrollBar().setValue(self.scroll_area.verticalScrollBar().maximum()))

    def clear_current_chat(self):
        """仅清空当前选定股票的历史对话，绝不影响其他标的"""
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
        stock_txt = self.stock_search.currentText().strip()
        target_symbol = stock_txt.split(" · ")[0].strip().upper().removesuffix(".US") if stock_txt and stock_txt != "全部股票" else "该标的"
        self.status_hint.setText(f"📈 Codex 正在对 {target_symbol} 进行深度研报分析... (已耗时 {self._thinking_seconds} 秒 · 运算中{dots})")
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
        # 思考中严禁任何再次发送或回车穿透
        if self.worker and self.worker.isRunning():
            return

        text = self.input_edit.toPlainText().strip()
        if not text:
            return

        try:
            stock_txt = self.stock_search.currentText().strip()
            target_symbol = stock_txt.split(" · ")[0].strip().upper().removesuffix(".US") if stock_txt and stock_txt != "全部股票" else ""

            current_key = self.active_session_key
            symbol_tag = target_symbol if target_symbol else "宏观分析"
            user_msg = StockChatMessage(role="user", content=text, time_str=datetime.now().strftime("%H:%M:%S"), symbol_tag=symbol_tag)

            self.chat_sessions.setdefault(current_key, []).append(user_msg)
            self._render_message_widget(user_msg)
            self.save_sessions()
            self.input_edit.clear()

            # 双重锁死按钮与输入框
            self.send_btn.setEnabled(False)
            self.input_edit.setEnabled(False)
            self.cancel_btn.setVisible(True)

            # 启动秒级心跳指示
            self._thinking_seconds = 0
            self._thinking_dots = 0
            self.status_hint.setText(f"📈 Codex 正在对 {target_symbol or '该标的'} 进行基本面与商业前景分析... (已耗时 0 秒 · 运算中)")
            self._thinking_timer.start(1000)

            self.worker = AskStockWorker(text, symbol=target_symbol, session_key=current_key)
            self.worker.finished_signal.connect(self._on_worker_finished)
            self.worker.error_signal.connect(self._on_worker_error)
            self.worker.start()
        except Exception as e:
            if self._thinking_timer.isActive():
                self._thinking_timer.stop()
            self.status_hint.setText(f"❌ 发送失败: {e}")
            err_card = StockChatMessage(
                role="assistant",
                content=f"⚠️ **发生内部异常**：\n\n```\n{e}\n```\n请检查相关配置。",
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
            cancel_card = StockChatMessage(
                role="assistant",
                content="⏸️ *您已主动中止了本次股票分析请求。可调整提问后重新发送。*",
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

        asst_msg = StockChatMessage(role="assistant", content=reply, time_str=datetime.now().strftime("%H:%M:%S"))
        self.chat_sessions.setdefault(session_key, []).append(asst_msg)
        self.save_sessions()
        if session_key == self.active_session_key:
            self._render_message_widget(asst_msg)
        self._reset_input_state()
        self.status_hint.setText(f"✅ 研报分析完成 (总耗时 {elapsed} 秒)")
        QTimer.singleShot(4000, lambda: self.status_hint.setText("") if not (self.worker and self.worker.isRunning()) else None)

    def _on_worker_error(self, err_msg: str, session_key: str):
        if self._thinking_timer.isActive():
            self._thinking_timer.stop()
        error_reply = f"⚠️ **调用 Codex 失败**：\n\n```\n{err_msg}\n```\n请检查本地 Codex 登录状态或网络连接。"
        asst_msg = StockChatMessage(role="assistant", content=error_reply, time_str=datetime.now().strftime("%H:%M:%S"))
        self.chat_sessions.setdefault(session_key, []).append(asst_msg)
        self.save_sessions()
        if session_key == self.active_session_key:
            self._render_message_widget(asst_msg)
        self._reset_input_state()
        self.status_hint.setText("⚠️ 研报分析中断或出错")
        QTimer.singleShot(4000, lambda: self.status_hint.setText("") if not (self.worker and self.worker.isRunning()) else None)

    def _reset_input_state(self):
        self.send_btn.setEnabled(True)
        self.input_edit.setEnabled(True)
        self.input_edit.setFocus()
        self.cancel_btn.setVisible(False)
        self.worker = None

    def _render_message_widget(self, msg: StockChatMessage):
        """渲染单条消息气泡，平滑滚动贴合底部"""
        msg_widget = StockChatMessageWidget(
            role=msg.role,
            content=msg.content,
            time_str=msg.time_str,
            symbol_tag=msg.symbol_tag,
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
