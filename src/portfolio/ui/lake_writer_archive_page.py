"""Mac 桌面端「拉科写作记录」只读查看页面（支持查看活跃、已关闭标签及已清空归档）。"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from portfolio.services.lake_writer_store import LakeWriterStore


class LakeWriterArchivePage(QWidget):
    """桌面客户端专用的拉科写作记录与清空归档浏览器（只读，不修改原记录）。"""

    def __init__(self, parent=None, store: LakeWriterStore | None = None):
        super().__init__(parent)
        self.store = store or LakeWriterStore()
        self._records: list[dict] = []
        self._build_ui()
        self.reload()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # 顶部筛选与操作栏
        top_card = QFrame()
        top_card.setObjectName("card")
        top_layout = QHBoxLayout(top_card)
        top_layout.setContentsMargins(12, 10, 12, 10)
        top_layout.setSpacing(8)

        title_lbl = QLabel("拉科写作存档（含已清空对话与已关闭标签）")
        title_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #0F172A;")
        top_layout.addWidget(title_lbl)
        top_layout.addStretch()

        self.owner_combo = QComboBox()
        self.owner_combo.setMinimumWidth(120)
        self.owner_combo.addItem("全部账号", "")
        self.owner_combo.currentIndexChanged.connect(self.reload)
        top_layout.addWidget(QLabel("账号："))
        top_layout.addWidget(self.owner_combo)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索标题或正文关键词...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(220)
        self.search_input.returnPressed.connect(self.reload)
        top_layout.addWidget(self.search_input)

        self.refresh_btn = QPushButton("刷新记录")
        self.refresh_btn.clicked.connect(self.reload)
        top_layout.addWidget(self.refresh_btn)

        self.copy_btn = QPushButton("复制当前文本")
        self.copy_btn.clicked.connect(self._copy_current_text)
        top_layout.addWidget(self.copy_btn)

        root.addWidget(top_card)

        # 主体左右分栏：左侧标签存档列表，右侧完整对话与清空归档内容
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_wrap = QFrame()
        left_wrap.setObjectName("card")
        left_layout = QVBoxLayout(left_wrap)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(6)

        self.count_lbl = QLabel("共 0 个对话标签")
        self.count_lbl.setStyleSheet("font-size: 12px; color: #64748B;")
        left_layout.addWidget(self.count_lbl)

        self.tab_list = QListWidget()
        self.tab_list.currentRowChanged.connect(self._on_tab_selected)
        left_layout.addWidget(self.tab_list, 1)

        right_wrap = QFrame()
        right_wrap.setObjectName("card")
        right_layout = QVBoxLayout(right_wrap)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)

        self.detail_header = QLabel("请在左侧选择对话标签查看详情")
        self.detail_header.setStyleSheet("font-size: 13px; font-weight: 600; color: #1E293B;")
        self.detail_header.setWordWrap(True)
        right_layout.addWidget(self.detail_header)

        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(False)
        self.viewer.setStyleSheet(
            "QTextBrowser { border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px; background: #F8FAFC; font-size: 13px; }"
        )
        right_layout.addWidget(self.viewer, 1)

        splitter.addWidget(left_wrap)
        splitter.addWidget(right_wrap)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        root.addWidget(splitter, 1)

    def reload(self):
        """重新从共享持久化存储读取全部写作标签与清空归档记录。"""
        current_owner = self.owner_combo.currentData() or ""
        keyword = self.search_input.text().strip()
        payload = self.store.list_all_archives(
            owner_filter=current_owner or None,
            keyword=keyword or None,
        )

        # 更新账号下拉框，保留当前选中项
        self.owner_combo.blockSignals(True)
        self.owner_combo.clear()
        self.owner_combo.addItem("全部账号", "")
        for owner in payload.get("owners") or []:
            self.owner_combo.addItem(owner, owner)
        idx = self.owner_combo.findData(current_owner)
        self.owner_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.owner_combo.blockSignals(False)

        self._records = list(payload.get("records") or [])
        self.count_lbl.setText(f"共 {len(self._records)} 个对话标签")

        self.tab_list.clear()
        for rec in self._records:
            status_tag = "已关闭" if rec.get("status") == "deleted" else "活跃中"
            batches = rec.get("archived_batch_count") or 0
            arch_suffix = f" · 含 {batches} 次清空归档" if batches > 0 else ""
            text = (
                f"[{status_tag}] {rec.get('title') or '未命名文章'}\n"
                f"账号: {rec.get('owner')} · 共 {rec.get('total_message_count', 0)} 条{arch_suffix}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, rec.get("id"))
            self.tab_list.addItem(item)

        if self._records:
            self.tab_list.setCurrentRow(0)
        else:
            self.detail_header.setText("暂无匹配的拉科写作记录")
            self.viewer.setHtml("<p style='color:#64748B;'>暂无对话记录或归档。</p>")

    def _on_tab_selected(self, row: int):
        if row < 0 or row >= len(self._records):
            return
        rec = self._records[row]
        status_desc = (
            f"已关闭 ({(rec.get('deleted_at') or '').replace('T', ' ')})"
            if rec.get("status") == "deleted"
            else "活跃中"
        )
        self.detail_header.setText(
            f"{rec.get('title')}  |  账号：{rec.get('owner')}  |  状态：{status_desc}  |  "
            f"活跃 {rec.get('active_message_count', 0)} 条，清空归档 {rec.get('archived_batch_count', 0)} 批次（{rec.get('archived_message_count', 0)} 条）"
        )
        self.viewer.setHtml(self._render_record_html(rec))

    @staticmethod
    def _render_messages_html(messages: list[dict]) -> str:
        if not messages:
            return "<div style='color:#64748B; padding:6px 0;'>（当前无对话内容）</div>"
        blocks: list[str] = []
        for m in messages:
            is_user = m.get("role") == "user"
            role_lbl = "用户" if is_user else "Gemini"
            bg = "#EFF6FF" if is_user else "#FFFFFF"
            border = "#BFDBFE" if is_user else "#E2E8F0"
            role_color = "#1D4ED8" if is_user else "#0F172A"
            time_str = html.escape(str(m.get("time_str") or m.get("time") or ""))
            model_str = html.escape(str(m.get("model") or ""))
            meta = f"{role_lbl} · {time_str}" + (f" · {model_str}" if (not is_user and model_str) else "")
            content_html = html.escape(str(m.get("content") or "")).replace("\n", "<br>")
            blocks.append(
                f"<div style='background:{bg}; border:1px solid {border}; border-radius:8px; padding:10px 12px; margin:8px 0;'>"
                f"<div style='font-size:11px; font-weight:600; color:{role_color}; margin-bottom:4px;'>{meta}</div>"
                f"<div style='line-height:1.6; color:#0F172A;'>{content_html}</div>"
                f"</div>"
            )
        return "".join(blocks)

    def _render_record_html(self, rec: dict) -> str:
        parts: list[str] = [
            "<h3 style='margin:4px 0 8px; color:#1D4ED8;'>当前标签内对话</h3>",
            self._render_messages_html(rec.get("messages") or []),
        ]
        batches = rec.get("archived_batches") or []
        if batches:
            total_batches = len(batches)
            for idx, batch in enumerate(reversed(batches)):
                seq = total_batches - idx
                cleared_at = html.escape(str(batch.get("cleared_at") or "").replace("T", " "))
                msgs = batch.get("messages") or []
                parts.append(
                    f"<h3 style='margin:16px 0 8px; color:#B45309; border-top:1px dashed #CBD5E1; padding-top:10px;'>"
                    f"已清空存档 #{seq}（清空时间：{cleared_at} · 共 {len(msgs)} 条）</h3>"
                )
                parts.append(self._render_messages_html(msgs))
        return "".join(parts)

    def _copy_current_text(self):
        text = self.viewer.toPlainText().strip()
        if text:
            QGuiApplication.clipboard().setText(text)
