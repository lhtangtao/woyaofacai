from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QCompleter, QMenu, QToolButton


class TickerSearch(QComboBox):
    """Search existing securities; commit an instrument ID only after selection."""
    def __init__(self):
        super().__init__()
        self.setObjectName("tickerSearch")
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().setPlaceholderText("输入 Ticker")
        self.lineEdit().setClearButtonEnabled(True)
        self.setToolTip("输入 Ticker 自动匹配，选择后筛选；清空恢复全部股票。")
        completion = QCompleter(self.model(), self)
        completion.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completion.setFilterMode(Qt.MatchFlag.MatchContains)
        completion.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completion.setMaxVisibleItems(8)
        completion.popup().setMinimumWidth(220)
        self.setCompleter(completion)
        completion.activated[QModelIndex].connect(self._choose)
        self.currentIndexChanged.connect(self._sync_editor)
        self.lineEdit().textEdited.connect(self._edited)
        self.lineEdit().editingFinished.connect(self._finish_edit)

    def setCurrentIndex(self, index):
        super().setCurrentIndex(index)
        self._sync_editor()

    def _sync_editor(self, *_):
        text = self.itemText(self.currentIndex()) if self.currentData() is not None else ""
        self.setEditText(text)

    def _edited(self, text):
        clean = text.strip()
        if not clean:
            self.setCurrentIndex(0)
            self.completer().popup().hide()
        else:
            prefix = clean[:-3] if clean.lower().endswith(".us") else clean
            self.completer().setCompletionPrefix(prefix)
            self.completer().complete()

    def _choose(self, index):
        if index.isValid():
            match = self.findData(index.data(Qt.ItemDataRole.UserRole))
            if match >= 0:
                self.setCurrentIndex(match)

    def _finish_edit(self):
        text = self.lineEdit().text().strip().casefold()
        clean_text = text.removesuffix(".us")
        exact = [index for index in range(1, self.count())
                 if text in (self.itemText(index).casefold(), self.itemText(index).split(" · ")[0].casefold())
                 or clean_text in (self.itemText(index).casefold(), self.itemText(index).split(" · ")[0].casefold())]
        if len(exact) == 1:
            self.setCurrentIndex(exact[0])
        elif not self.completer().popup().isVisible():
            self._sync_editor()  # Unconfirmed text must not masquerade as an active filter.

    def showPopup(self):
        text = self.lineEdit().text().strip()
        if text:
            self.completer().setCompletionPrefix(text)
            self.completer().complete()
        else:
            self.lineEdit().setFocus()


class AccountFilter(QToolButton):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setText("全部渠道")
        self.actions_by_id = {}
        self.setMinimumWidth(165)

    def populate(self, accounts):
        menu = QMenu(self)
        reset = menu.addAction("全部渠道")
        reset.triggered.connect(self.reset)
        menu.addSeparator()
        self.actions_by_id = {}
        allowed_keys = {"FIRSTRADE", "BBAE", "LONGBRIDGE", "SCHWAB", "第一证券", "长桥", "嘉信"}
        has_allowed = any(str(acc.get('broker_code', '')).upper() in allowed_keys for acc in accounts)
        display_names = {
            "FIRSTRADE": "第一证券",
            "第一证券": "第一证券",
            "BBAE": "BBAE",
            "LONGBRIDGE": "长桥",
            "长桥": "长桥",
            "SCHWAB": "嘉信",
            "嘉信": "嘉信",
        }
        order_map = {"第一证券": 0, "BBAE": 1, "长桥": 2, "嘉信": 3}
        filtered_accounts = [acc for acc in accounts if str(acc.get('broker_code', '')).upper() in allowed_keys] if has_allowed else list(accounts)
        filtered_accounts.sort(key=lambda a: order_map.get(display_names.get(str(a.get('broker_code', '')).upper(), ""), 99))
        for account in filtered_accounts:
            code = str(account.get('broker_code', '')).upper()
            title = display_names.get(code, account.get('broker_code', ''))
            suffix = "（停用）" if not account.get('enabled', True) else ""
            action = QAction(title + suffix, self)
            action.setCheckable(True)
            action.toggled.connect(self._changed)
            menu.addAction(action)
            self.actions_by_id[account["id"]] = action
        self.setMenu(menu)
        self.setText("全部渠道")

    def selected_ids(self):
        return tuple(identifier for identifier, action in self.actions_by_id.items() if action.isChecked()) or None

    def reset(self):
        for action in self.actions_by_id.values():
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)
        self._changed()

    def _changed(self):
        ids = self.selected_ids()
        self.setText(self.actions_by_id[ids[0]].text() if ids and len(ids) == 1 else f"已选 {len(ids)} 个渠道" if ids else "全部渠道")
        self.changed.emit()
