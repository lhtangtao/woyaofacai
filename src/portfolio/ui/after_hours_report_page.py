"""Mac 桌面端「盘后复盘日报」页面（与 Web 端共享本地存档与自动同步/生成引擎）。"""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDate, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from portfolio.paths import DATABASE_PATH
from portfolio.services.after_hours_report import (
    AfterHoursReportStore,
    generate_after_hours_report,
    resolve_default_trade_date,
)
from portfolio.services.ai_service import CodexAIService


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


class AfterHoursReportPage(QWidget):
    """美股盘后复盘日报桌面端视图（支持按交易日与人员范围查看/手动生成）。"""

    def __init__(self, parent=None, store: Optional[AfterHoursReportStore] = None, db_path: Path = DATABASE_PATH):
        super().__init__(parent)
        self.store = store or AfterHoursReportStore()
        self.db_path = db_path
        self._reports: list[dict] = []
        self._current_report: Optional[dict] = None
        self._worker: Optional[_AfterHoursWorker] = None
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
        top.setContentsMargins(12, 10, 12, 10)
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
        self.person_combo.setMinimumWidth(110)
        self.person_combo.addItem("个人", "个人")
        self.person_combo.addItem("全部人员", "ALL")
        top.addWidget(self.person_combo)

        self.sync_check = QCheckBox("生成前同步券商流水")
        self.sync_check.setChecked(True)
        top.addWidget(self.sync_check)

        top.addStretch()

        self.status_lbl = QLabel("自动调度：美股每个交易日盘后（美东 20:10）自动同步并生成「个人」日报")
        self.status_lbl.setStyleSheet("font-size: 11.5px; color: #64748B;")
        top.addWidget(self.status_lbl)

        self.refresh_btn = QPushButton("刷新列表")
        self.refresh_btn.clicked.connect(self.reload)
        top.addWidget(self.refresh_btn)

        self.generate_btn = QPushButton("生成 / 重算日报")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.clicked.connect(self._start_generate)
        top.addWidget(self.generate_btn)

        root.addWidget(top_card)

        # 主体左右分栏
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_card = QFrame()
        left_card.setObjectName("card")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(6)

        self.count_lbl = QLabel("历史盘后日报 (0)")
        self.count_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #334155;")
        left_layout.addWidget(self.count_lbl)

        self.report_list = QListWidget()
        self.report_list.currentRowChanged.connect(self._on_report_selected)
        left_layout.addWidget(self.report_list, 1)

        right_card = QFrame()
        right_card.setObjectName("card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)

        hdr_row = QHBoxLayout()
        self.detail_title = QLabel("请选择左侧日报或点击右上角立即生成")
        self.detail_title.setStyleSheet("font-size: 13.5px; font-weight: 700; color: #0F172A;")
        self.detail_title.setWordWrap(True)
        hdr_row.addWidget(self.detail_title, 1)

        self.copy_btn = QPushButton("复制 Markdown")
        self.copy_btn.clicked.connect(self._copy_markdown)
        hdr_row.addWidget(self.copy_btn)
        right_layout.addLayout(hdr_row)

        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(False)
        self.viewer.setStyleSheet(
            "QTextBrowser { border: 1px solid #E2E8F0; border-radius: 8px; padding: 12px; background: #FFFFFF; font-size: 13px; }"
        )
        right_layout.addWidget(self.viewer, 1)

        splitter.addWidget(left_card)
        splitter.addWidget(right_card)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        root.addWidget(splitter, 1)

    def populate_persons(self, persons: list[dict] | tuple[dict, ...]):
        """同步数据库中的全部启用人员到范围下拉框。"""
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

    def reload(self, prefer_id: Optional[str] = None):
        """从共享本地文件刷新历史盘后复盘日报列表。"""
        self._reports = self.store.list_reports()
        self.count_lbl.setText(f"历史盘后日报 ({len(self._reports)})")
        self.report_list.clear()
        select_row = 0
        for i, rep in enumerate(self._reports):
            pnl = rep.get("day_pnl")
            pnl_str = "—" if pnl is None else (f"+${pnl:,.2f}" if pnl > 0 else f"-${abs(pnl):,.2f}")
            trig = "自动" if rep.get("trigger") == "auto" else "手动"
            label = (
                f"{rep.get('trade_date')} · {rep.get('person_name')} [{trig}]\n"
                f"当日盈亏: {pnl_str} · 成交 {rep.get('trade_count', 0)} 笔"
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
            self.viewer.setHtml(
                "<p style='color:#64748B;'>暂无历史盘后复盘日报。点击右上角「生成 / 重算日报」可立即生成。</p>"
            )

    def _on_report_selected(self, row: int):
        if row < 0 or row >= len(self._reports):
            return
        rid = self._reports[row].get("id")
        full = self.store.get_report(str(rid))
        if not full:
            return
        self._current_report = full
        sync_info = full.get("sync_status") or {}
        sync_tag = (
            "已自动同步" if (sync_info.get("ran") and sync_info.get("ok"))
            else ("同步异常" if sync_info.get("ran") else "未执行同步")
        )
        self.detail_title.setText(
            f"{full.get('trade_date')} 美股盘后复盘日报 · {full.get('person_name')}  "
            f"（生成：{(full.get('generated_at') or '').replace('T', ' ')} · {sync_tag}）"
        )
        md = str(full.get("markdown") or "")
        # 利用 Qt 内置 Markdown 渲染器清晰展示报告正文
        self.viewer.setMarkdown(md)

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

    def _copy_markdown(self):
        if self._current_report and self._current_report.get("markdown"):
            QGuiApplication.clipboard().setText(str(self._current_report["markdown"]))
