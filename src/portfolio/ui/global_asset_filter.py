"""资产管理统一全局筛选组件（Single Source of Truth）。"""
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from portfolio.ui.filters import AccountFilter, PersonFilter, TickerSearch


@dataclass(frozen=True)
class GlobalAssetFilterState:
    """资产管理全局筛选状态：人员、券商渠道、股票代码。"""

    person: Any = None
    account_ids: tuple[int, ...] | None = None
    instrument_id: int | None = None

    @classmethod
    def normalized(cls, person=None, account_ids=None, instrument_id=None) -> "GlobalAssetFilterState":
        if isinstance(person, list):
            person = tuple(person)
        if isinstance(person, tuple) and len(person) == 1:
            person = person[0]
        elif isinstance(person, tuple) and not person:
            person = None
        acc = tuple(sorted(set(account_ids))) if account_ids else None
        inst = int(instrument_id) if instrument_id is not None else None
        return cls(person=person, account_ids=acc, instrument_id=inst)


class GlobalAssetFilter(QFrame):
    """资产管理全局筛选栏：主窗口唯一实例，由资产总览、今日盈亏、持仓管理共用。"""

    filterChanged = Signal(object)
    resetRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("globalAssetFilter")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._syncing = False
        self._persons = ()

        self.setStyleSheet("""
            QFrame#globalAssetFilter {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
            QFrame#globalAssetFilter QLabel#globalFilterLabel {
                color: #475569;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
                border: none;
            }
            QFrame#globalAssetFilter QToolButton,
            QFrame#globalAssetFilter QComboBox {
                min-height: 30px;
                max-height: 30px;
                border: 1px solid #CBD5E1;
                border-radius: 7px;
                padding: 2px 10px;
                background: #F8FAFC;
                color: #0F172A;
                font-size: 12px;
                font-weight: 500;
            }
            QFrame#globalAssetFilter QToolButton:hover,
            QFrame#globalAssetFilter QComboBox:hover {
                border-color: #2563EB;
                background: #FFFFFF;
            }
            QFrame#globalAssetFilter QPushButton#globalResetBtn {
                min-height: 30px;
                max-height: 30px;
                padding: 0 14px;
                border: 1px solid #CBD5E1;
                border-radius: 7px;
                background: #F8FAFC;
                color: #334155;
                font-size: 12px;
                font-weight: 600;
            }
            QFrame#globalAssetFilter QPushButton#globalResetBtn:hover {
                border-color: #2563EB;
                color: #2563EB;
                background: #EFF6FF;
            }
            QFrame#globalAssetFilter QPushButton#globalTrailingBtn {
                min-height: 28px;
                padding: 0 12px;
                border: 1px solid #CBD5E1;
                border-radius: 7px;
                background: #FFFFFF;
                color: #334155;
                font-size: 12px;
                font-weight: 600;
            }
            QFrame#globalAssetFilter QPushButton#globalTrailingBtn:hover {
                border-color: #2563EB;
                color: #2563EB;
                background: #EFF6FF;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)

        lbl_person = QLabel("人员", self)
        lbl_person.setObjectName("globalFilterLabel")
        self.person_filter = PersonFilter(self)
        self.person_filter.addItem("全部启用人员", None)
        self.person_filter.setMinimumWidth(148)
        self.person_filter.setMaximumWidth(220)
        self.person_filter.setToolTip("归属人员（支持单选、多选或选择他人/全部启用人员）")
        layout.addWidget(lbl_person)
        layout.addWidget(self.person_filter)

        lbl_account = QLabel("券商渠道", self)
        lbl_account.setObjectName("globalFilterLabel")
        self.account_filter = AccountFilter()
        self.account_filter.setParent(self)
        self.account_filter.setMinimumWidth(140)
        self.account_filter.setMaximumWidth(220)
        self.account_filter.setToolTip("券商渠道（支持单选或多选，默认全部渠道）")
        layout.addWidget(lbl_account)
        layout.addWidget(self.account_filter)

        lbl_ticker = QLabel("股票代码", self)
        lbl_ticker.setObjectName("globalFilterLabel")
        self.ticker_search = TickerSearch()
        self.ticker_search.setParent(self)
        self.ticker_search.addItem("全部股票", None)
        self.ticker_search.setMinimumWidth(165)
        self.ticker_search.setMaximumWidth(240)
        self.ticker_search.lineEdit().setPlaceholderText("全部股票 / 输入 Ticker")
        layout.addWidget(lbl_ticker)
        layout.addWidget(self.ticker_search)

        self.inline_host = QWidget(self)
        self.inline_host.setStyleSheet("background: transparent; border: none;")
        self.inline_layout = QHBoxLayout(self.inline_host)
        self.inline_layout.setContentsMargins(8, 0, 0, 0)
        self.inline_layout.setSpacing(6)
        self.inline_host.hide()
        layout.addWidget(self.inline_host, 0)

        layout.addStretch(1)

        self.reset_button = QPushButton("重置筛选", self)
        self.reset_button.setObjectName("globalResetBtn")
        self.reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_button.setToolTip("重置人员、券商渠道、股票代码为默认筛选状态")
        layout.addWidget(self.reset_button)

        self.trailing_host = QWidget(self)
        self.trailing_host.setStyleSheet("background: transparent; border: none;")
        self.trailing_layout = QHBoxLayout(self.trailing_host)
        self.trailing_layout.setContentsMargins(0, 0, 0, 0)
        self.trailing_layout.setSpacing(6)
        self.trailing_host.hide()
        layout.addWidget(self.trailing_host, 0)

        self._state = GlobalAssetFilterState()

        self.person_filter.changed.connect(self._on_controls_changed)
        self.account_filter.changed.connect(self._on_controls_changed)
        self.ticker_search.currentIndexChanged.connect(self._on_controls_changed)
        self.reset_button.clicked.connect(self._on_reset_clicked)

    def state(self) -> GlobalAssetFilterState:
        return self._state

    def _read_controls(self) -> GlobalAssetFilterState:
        return GlobalAssetFilterState.normalized(
            person=self.person_filter.currentData(),
            account_ids=self.account_filter.selected_ids(),
            instrument_id=self.ticker_search.currentData(),
        )

    def _on_controls_changed(self, *_):
        if self._syncing:
            return
        new_state = self._read_controls()
        if new_state == self._state:
            return
        self._state = new_state
        self.filterChanged.emit(new_state)

    def _on_reset_clicked(self):
        self.resetRequested.emit()

    def populate_persons(self, persons, *, include_disabled=False):
        self._persons = tuple(persons or ())
        prev = self.person_filter.currentData()
        self._syncing = True
        try:
            self.person_filter.blockSignals(True)
            self.person_filter.clear()
            self.person_filter.addItem("全部历史人员" if include_disabled else "全部启用人员", None)
            self.person_filter.addItem("他人", "OTHERS")
            for person in self._persons:
                if person.get("enabled", True) or include_disabled:
                    suffix = "（停用）" if not person.get("enabled", True) else ""
                    self.person_filter.addItem(f"{person['name']}{suffix}", person["id"])
            if prev is not None:
                self.person_filter.set_person_id(prev)
            self.person_filter.blockSignals(False)
            self._state = self._read_controls()
        finally:
            self._syncing = False

    def populate(self, persons, accounts, instruments, *, include_disabled=False):
        self._syncing = True
        try:
            self.populate_persons(persons, include_disabled=include_disabled)
            self.account_filter.blockSignals(True)
            self.account_filter.populate(accounts or ())
            self.account_filter.blockSignals(False)
            self.ticker_search.blockSignals(True)
            self.ticker_search.clear()
            self.ticker_search.addItem("全部股票", None)
            for instrument in instruments or ():
                self.ticker_search.addItem(f"{instrument['symbol']} · {instrument['market']}", instrument["id"])
            self.ticker_search.setCurrentIndex(0)
            self.ticker_search.blockSignals(False)
            self._state = self._read_controls()
        finally:
            self._syncing = False

    def set_state(self, state: GlobalAssetFilterState, *, emit=False):
        target = GlobalAssetFilterState.normalized(state.person, state.account_ids, state.instrument_id)
        self._syncing = True
        try:
            self.person_filter.blockSignals(True)
            self.person_filter.set_person_id(target.person)
            self.person_filter.blockSignals(False)

            self.account_filter.blockSignals(True)
            selected_acc = set(target.account_ids or ())
            for aid, action in self.account_filter.actions_by_id.items():
                action.setChecked(aid in selected_acc)
            ids = self.account_filter.selected_ids()
            self.account_filter.setText(
                self.account_filter.actions_by_id[ids[0]].text()
                if ids and len(ids) == 1
                else f"已选 {len(ids)} 个渠道" if ids else "全部渠道"
            )
            self.account_filter.blockSignals(False)

            self.ticker_search.blockSignals(True)
            if target.instrument_id is None:
                self.ticker_search.setCurrentIndex(0)
            else:
                idx = self.ticker_search.findData(target.instrument_id)
                self.ticker_search.setCurrentIndex(max(0, idx))
            self.ticker_search.blockSignals(False)

            actual = self._read_controls()
            changed = actual != self._state
            self._state = actual
        finally:
            self._syncing = False
        if emit and changed:
            self.filterChanged.emit(self._state)

    def set_person(self, person_data, *, emit=True):
        self.set_state(
            GlobalAssetFilterState.normalized(person_data, self._state.account_ids, self._state.instrument_id),
            emit=emit,
        )

    def set_accounts(self, account_ids, *, emit=True):
        self.set_state(
            GlobalAssetFilterState.normalized(self._state.person, account_ids, self._state.instrument_id),
            emit=emit,
        )

    def set_broker_name(self, broker_name: str | None, *, emit=True):
        clean = (broker_name or "").strip()
        if not clean or clean == "全部渠道":
            self.set_accounts(None, emit=emit)
            return
        matched_ids = []
        for aid, action in self.account_filter.actions_by_id.items():
            name = action.text().replace("（停用）", "").strip()
            if name == clean or clean.upper() in name.upper() or name.upper() in clean.upper():
                matched_ids.append(aid)
        self.set_accounts(tuple(matched_ids) if matched_ids else None, emit=emit)

    def set_instrument(self, instrument_id: int | None, *, emit=True):
        self.set_state(
            GlobalAssetFilterState.normalized(self._state.person, self._state.account_ids, instrument_id),
            emit=emit,
        )

    def set_ticker_symbol(self, symbol: str | None, *, emit=True):
        clean = (symbol or "").strip().casefold()
        if not clean:
            self.set_instrument(None, emit=emit)
            return
        for i in range(1, self.ticker_search.count()):
            text = self.ticker_search.itemText(i).split(" · ")[0].strip().casefold()
            if text == clean:
                self.set_instrument(self.ticker_search.itemData(i), emit=emit)
                return
        self.set_instrument(None, emit=emit)

    def default_person_data(self):
        idx = self.person_filter.findText("个人")
        if idx >= 0:
            return self.person_filter.itemData(idx)
        return None

    def set_trailing_widget(self, widget: QWidget | None):
        """在右侧重置筛选按钮旁挂载或移除页面专属操作控件（如交易明细高级筛选）。"""
        while self.trailing_layout.count():
            item = self.trailing_layout.takeAt(0)
            old = item.widget()
            if old is not None and old is not widget:
                old.hide()
        if widget is None:
            self.trailing_host.hide()
            return
        self.trailing_layout.addWidget(widget)
        widget.show()
        self.trailing_host.show()

    def reset_to_default(self, *, emit=True):
        self.set_state(
            GlobalAssetFilterState.normalized(person=self.default_person_data(), account_ids=None, instrument_id=None),
            emit=emit,
        )
