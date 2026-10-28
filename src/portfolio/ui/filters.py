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


class PersonFilter(QToolButton):
    """Multi-select person filter styled like AccountFilter with QComboBox-compatible API."""
    changed = Signal()
    currentIndexChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setMinimumWidth(136)
        self._items = []
        self.actions_by_id = {}
        self._others_action = None
        self._others_mode = False
        self._all_text = "全部启用人员"
        self._multi_target = None
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self.setText(self._all_text)

    def clear(self):
        self._items = []
        self.actions_by_id = {}
        self._others_action = None
        self._others_mode = False
        self._multi_target = None
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._sync_text()

    def addItem(self, text, data=None):
        self._items.append((text, data))
        if data is None:
            self._all_text = text
            act = self._menu.addAction(text)
            act.triggered.connect(self.reset)
        elif data == "OTHERS":
            act = QAction(text, self)
            act.setCheckable(True)
            act.toggled.connect(self._on_others_toggled)
            self._menu.addAction(act)
            self._menu.addSeparator()
            self._others_action = act
        else:
            act = QAction(text, self)
            act.setCheckable(True)
            act.toggled.connect(self._on_person_toggled)
            self._menu.addAction(act)
            self.actions_by_id[data] = act
        self._sync_text()

    def _uncheck_all_quietly(self):
        self._others_mode = False
        if self._others_action:
            self._others_action.blockSignals(True)
            self._others_action.setChecked(False)
            self._others_action.blockSignals(False)
        for act in self.actions_by_id.values():
            act.blockSignals(True)
            act.setChecked(False)
            act.blockSignals(False)

    def reset(self):
        self._uncheck_all_quietly()
        self._sync_text()
        if not self.signalsBlocked():
            self.changed.emit()
            self.currentIndexChanged.emit(0)

    def _on_others_toggled(self, checked):
        if checked:
            for act in self.actions_by_id.values():
                act.blockSignals(True)
                act.setChecked(False)
                act.blockSignals(False)
            self._others_mode = True
        else:
            self._others_mode = False
        self._sync_text()
        if not self.signalsBlocked():
            self.changed.emit()
            self.currentIndexChanged.emit(self.currentIndex())

    def _on_person_toggled(self, _checked):
        if self._others_action and self._others_action.isChecked():
            self._others_action.blockSignals(True)
            self._others_action.setChecked(False)
            self._others_action.blockSignals(False)
        self._others_mode = False
        self._sync_text()
        if not self.signalsBlocked():
            self.changed.emit()
            self.currentIndexChanged.emit(self.currentIndex())

    def selected_ids(self):
        return tuple(pid for pid, act in self.actions_by_id.items() if act.isChecked()) or None

    def selected_person_items(self):
        return [(pid, act.text().replace("（停用）", "")) for pid, act in self.actions_by_id.items() if act.isChecked()]

    def _sync_text(self):
        if self._others_mode:
            self.setText("他人")
            return
        items = self.selected_person_items()
        if not items:
            self.setText(self._all_text)
        elif len(items) == 1:
            self.setText(self.actions_by_id[items[0][0]].text())
        elif len(items) == 2:
            self.setText(f"{items[0][1]}、{items[1][1]}")
        else:
            self.setText(f"{items[0][1]}等 {len(items)} 人")

    def currentData(self):
        if self._others_mode:
            return "OTHERS"
        ids = self.selected_ids()
        if not ids:
            return None
        if len(ids) == 1:
            return ids[0]
        return ids

    def currentText(self):
        return self.text()

    def count(self):
        return len(self._items)

    def itemText(self, index):
        return self._items[index][0] if 0 <= index < len(self._items) else ""

    def itemData(self, index):
        return self._items[index][1] if 0 <= index < len(self._items) else None

    def findText(self, text):
        return next((i for i, (t, _) in enumerate(self._items) if t == text), -1)

    def findData(self, data):
        if isinstance(data, (tuple, list)):
            valid_ids = tuple(x for x in data if x in self.actions_by_id)
            if valid_ids:
                self._multi_target = valid_ids
                return 9999
            return -1
        return next((i for i, (_, d) in enumerate(self._items) if d == data), -1)

    def currentIndex(self):
        if self._others_mode:
            return next((i for i, (_, d) in enumerate(self._items) if d == "OTHERS"), 1)
        ids = self.selected_ids()
        if not ids:
            return 0
        if len(ids) == 1:
            return next((i for i, (_, d) in enumerate(self._items) if d == ids[0]), 1)
        return 9999

    def setCurrentIndex(self, index):
        self._uncheck_all_quietly()
        if index == 9999 and self._multi_target:
            for pid in self._multi_target:
                if pid in self.actions_by_id:
                    self.actions_by_id[pid].blockSignals(True)
                    self.actions_by_id[pid].setChecked(True)
                    self.actions_by_id[pid].blockSignals(False)
        elif 0 < index < len(self._items):
            _, data = self._items[index]
            if data == "OTHERS" and self._others_action:
                self._others_mode = True
                self._others_action.blockSignals(True)
                self._others_action.setChecked(True)
                self._others_action.blockSignals(False)
            elif data in self.actions_by_id:
                self.actions_by_id[data].blockSignals(True)
                self.actions_by_id[data].setChecked(True)
                self.actions_by_id[data].blockSignals(False)
        self._sync_text()
        if not self.signalsBlocked():
            self.changed.emit()
            self.currentIndexChanged.emit(self.currentIndex())
