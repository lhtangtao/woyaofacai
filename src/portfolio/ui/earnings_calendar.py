"""Holdings-only monthly earnings calendar, queried off the UI thread."""
import calendar
import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, QEvent, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from portfolio.services.earnings_links import (
    INDEX_FUNDS,
    SINGLE_STOCK_LINKS,
    link_description,
    migrate_legacy_links,
)
from portfolio.services.earnings_service import (
    EarningsService,
    holding_targets,
    reconciled_events,
    release_status,
    session_refresh_due,
)
from portfolio.ui.date_picker import DatePicker
from portfolio.ui.theme import tokens


class EarningsCalendarPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.service = EarningsService(store=getattr(window, 'quote_store', None))
        self.signature = self.analytics = self.result = self.request = None
        self.fetching = False
        self.targets, self.overrides = {}, {}
        if window.settings:
            try:
                self.overrides = json.loads(str(window.settings.value('earnings_company_links', '{}')))
                if not isinstance(self.overrides, dict):
                    self.overrides = {}
            except (ValueError, TypeError):
                self.overrides = {}
        if window.settings and int(window.settings.value('earnings_company_links_version', 0)) < 1:
            self.overrides = migrate_legacy_links(self.overrides)
            window.settings.setValue('earnings_company_links', json.dumps(self.overrides))
            window.settings.setValue('earnings_company_links_version', 1)
        outer = QVBoxLayout(self)
        controls = QHBoxLayout()
        previous, following, today = QPushButton('上个月'), QPushButton('下个月'), QPushButton('本月')
        self.month = DatePicker(QDate.currentDate())
        self.month.set_month_mode(True)
        refresh, mapping = QPushButton('刷新财报'), QPushButton('关联公司…')
        for widget in (previous, self.month, following, today):
            controls.addWidget(widget)
        controls.addStretch()
        controls.addWidget(mapping)
        controls.addWidget(refresh)
        outer.addLayout(controls)
        self.status = QLabel('连接数据库后，查询当前持仓公司的财报日程。')
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        note = QLabel('日期按上市市场当地日期展示；已核实公司公告优先于长桥日历，其他日历日期待公告核实。北京时间单独标明，盘前/盘后不冒充精确时刻。已发布须有公告或接口实际披露确认，不能只按日期判断。')
        note.setWordWrap(True)
        outer.addWidget(note)
        self.coverage_status = QLabel('等待持仓关联公司核查…')
        self.coverage_status.setWordWrap(True)
        outer.addWidget(self.coverage_status)
        scroll = self.scroll = QScrollArea()
        self._center_today = True
        self.center_timer = QTimer(self)
        self.center_timer.setSingleShot(True)
        self.center_timer.timeout.connect(self.center_today)
        scroll.viewport().installEventFilter(self)
        scroll.verticalScrollBar().sliderPressed.connect(self.stop_centering)
        scroll.verticalScrollBar().actionTriggered.connect(self.stop_centering)
        scroll.verticalScrollBar().rangeChanged.connect(self.schedule_center_today)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        self.calendar = QTableWidget(0, 7)
        self.calendar.setHorizontalHeaderLabels(('周一', '周二', '周三', '周四', '周五', '周六', '周日'))
        self.calendar.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.calendar.verticalHeader().hide()
        self.calendar.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.calendar.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.calendar.viewport().installEventFilter(self)
        layout.addWidget(self.calendar)
        layout.addWidget(QLabel('持仓关联公司逐项核查（所选月起3个月，日期按市场当地时间）'))
        self.coverage = QTableWidget(0, 8)
        self.coverage.setHorizontalHeaderLabels(('关联公司', '对应持仓', '财报日期', '发布时段', '北京时间', '发布状态', '时段涨跌幅', '日期来源'))
        self.coverage.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.coverage.verticalHeader().hide()
        self.coverage.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.coverage.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.coverage.viewport().installEventFilter(self)
        layout.addWidget(self.coverage)
        self.day_label = QLabel('当月财报明细（点击日历日期筛选，再点同日恢复全部）')
        layout.addWidget(self.day_label)
        self.details = QTableWidget(0, 9)
        self.details.setHorizontalHeaderLabels(('日期（当地）', '公司/宏观事件', '代码/盯盘标的', '发布时段（当地）', '北京时间', '持仓/关注', '发布状态', '时段涨跌幅', '市场预期与核心看点'))
        self.details.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.details.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        self.details.setColumnWidth(0, 85)
        self.details.setColumnWidth(1, 135)
        self.details.setColumnWidth(2, 125)
        self.details.setColumnWidth(3, 110)
        self.details.setColumnWidth(4, 110)
        self.details.setColumnWidth(5, 85)
        self.details.setColumnWidth(6, 100)
        self.details.setColumnWidth(7, 120)
        self.details.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.details.verticalHeader().hide()
        self.details.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.details.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.details.viewport().installEventFilter(self)
        layout.addWidget(self.details)
        self.unmatched = QLabel('')
        self.unmatched.setWordWrap(True)
        layout.addWidget(self.unmatched)
        outer.addWidget(scroll, 1)
        self.selected_day = None
        self.month_timer = QTimer(self)
        self.month_timer.setSingleShot(True)
        self.month_timer.setInterval(150)
        self.month_timer.timeout.connect(self.fetch)
        self.month.dateChanged.connect(self.change_month)
        previous.clicked.connect(lambda: self.month.setDate(self.month.date().addMonths(-1)))
        following.clicked.connect(lambda: self.month.setDate(self.month.date().addMonths(1)))
        today.clicked.connect(self.focus_today)
        refresh.clicked.connect(lambda: self.fetch(force=True))
        mapping.clicked.connect(self.edit_links)
        self.calendar.cellClicked.connect(self.select_day)
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(lambda: self.activate() if window.pages.currentWidget() is self else None)
        self.timer.start()
        self.draw()

    def showEvent(self, event):
        super().showEvent(event)
        self.focus_today()

    def focus_today(self, *_):
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
        self._center_today = True
        self.month.setDate(QDate(today.year, today.month, today.day))
        self.schedule_center_today()

    def stop_centering(self, *_):
        # A manual scroll keeps its position through subsequent background refreshes.
        self._center_today = False

    def schedule_center_today(self, *_):
        if self._center_today and self.isVisible():
            self.center_timer.start(0)

    def center_today(self):
        if not self._center_today or not self.isVisible():
            return
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
        month = self.month.date()
        if (month.year(), month.month()) != (today.year, today.month):
            return
        weeks = calendar.Calendar().monthdayscalendar(today.year, today.month)
        row = next(r for r, week in enumerate(weeks) if today.day in week)
        cell = self.calendar.cellWidget(row, weeks[row].index(today.day))
        if cell is not None:
            center_y = cell.mapTo(self.scroll.widget(), cell.rect().center()).y()
            self.scroll.verticalScrollBar().setValue(center_y - self.scroll.viewport().height() // 2)

    def clear(self):
        self.month_timer.stop()
        self.signature = self.analytics = self.result = self.request = None
        self.fetching = False
        self.targets = {}
        self.status.setText('等待当前持仓计算…')
        self.draw()

    def activate(self):
        window = self.window
        if window.info is None or window.analytics_service is None:
            return
        scope = window.make_scope()
        signature = (window.analytics_service.identity(), scope)
        if signature == self.signature:
            if self.analytics is not None:
                self.fetch()
            return
        if window.analytics is not None and getattr(window.analytics, 'scope', None) == scope:
            self.signature = signature
            self.analytics = window.analytics
            self.targets = holding_targets(self.analytics, window.live_panel.instruments, self.overrides)
            self.fetch()
            return
        self.signature = signature
        self.status.setText('正在读取当前筛选的持仓…')
        service = window.analytics_service
        def task(cancel):
            try:
                return service.calculate(scope, cancel=cancel), ''
            except Exception:
                return None, '持仓计算失败，请刷新数据源重试。'
        def ready(result):
            if self.signature != signature:
                return
            self.analytics, error = result
            if error:
                self.signature = None
                self.status.setText(error)
                return
            self.targets = holding_targets(self.analytics, window.live_panel.instruments, self.overrides)
            self.fetch()
        window._submit('earnings_holdings', task, ready)

    def change_month(self, *_):
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
        month = self.month.date()
        self._center_today = (month.year(), month.month()) == (today.year, today.month)
        self.selected_day = None
        self.result = self.request = None
        self.fetching = False
        pending = self.window.jobs.get('earnings_calendar')
        if pending:
            pending.cancel.set()
        self.status.setText('正在读取所选月份的本地财报缓存…')
        self.draw()
        self.month_timer.start()

    def fetch(self, *, force=False):
        if self.analytics is None:
            self.activate()
            return
        if not self.service.store and getattr(self.window, 'quote_store', None):
            self.service.store = self.window.quote_store
        path = self.window.live_panel.path
        if not path or not Path(path).is_file():
            from portfolio.paths import CREDENTIALS_PATH
            if CREDENTIALS_PATH.is_file():
                path = str(CREDENTIALS_PATH)
        month = self.month.date()
        from portfolio.services.earnings_service import MARKET_ZONES
        markets = sorted({v[0].rsplit('.', 1)[-1] for v in self.targets.values() if v[0] and '.' in v[0] and v[0].rsplit('.', 1)[-1] in MARKET_ZONES})
        if not markets and any(v[0] for v in self.targets.values()):
            markets = ['US']
        request = (self.signature, month.year(), month.month(), tuple(markets), path, tuple(sorted(self.targets.items())))
        if self.fetching and self.request == request and not force:
            return
        self.request = request
        if not self.targets or not markets:
            self.result = None
            self.status.setText('当前筛选没有可查询的持仓公司或焦点标的。')
            self.draw()
            return
        if not path or not Path(path).is_file():
            self.result = None
            self.request = None
            self.status.setText('尚未配置长桥凭证。请在“当前持仓”的“行情配置”中选择，再回到此页刷新。')
            self.draw()
            return
        targets = {v[0] for v in self.targets.values() if v[0]}
        self.fetching = True
        self.status.setText('正在读取所选月份的本地财报缓存…')
        def load_cached(cancel):
            # SQLite may wait for a concurrent quote writer: never do it on the UI thread.
            try:
                cached = self.service.get_portfolio_cached(path, month.year(), month.month(), markets, targets)
                current = []
                for offset in range(3):
                    if cancel.is_set():
                        return None, False
                    index = month.year()*12+month.month()-1+offset
                    current.append(self.service.get_cached(path, index//12, index%12+1, markets))
                needs_refresh = (force or cached is None or any(item is None or item.warning for item in current)
                                 or any(session_refresh_due(e) for e in cached.coverage_events))
                return cached, needs_refresh
            except Exception:
                return None, True
        def cached_ready(value):
            if self.request != request:
                return
            cached, needs_refresh = value
            if cached is not None:
                self.result = cached
                events = self.visible_events()
                updated = cached.fetched_at.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M')
                self.status.setText(f'{month.year()}年{month.month()}月 · 当前持仓 {len(self.targets)} 个标的 · 匹配 {len(events)} 项财报 · 更新 {updated} 北京时间'
                                    + (' · '+cached.warning if cached.warning else '')
                                    + (' · 显示本机缓存，后台更新中' if needs_refresh else ' · 本机缓存'))
                self.draw()
                if self.window.pages.currentWidget() is self:
                    self.window.banner.hide()
            else:
                self.status.setText('该月份尚无本地缓存，正在后台查询财报日程…')
                self.coverage_status.setText(f'正在核查 {len(targets)} 家关联公司，覆盖所选月起3个月…')
            if needs_refresh:
                self.window._submit('earnings_calendar', task, ready)
            else:
                self.fetching = False
        def task(cancel):
            try:
                return self.service.fetch_portfolio(path, month.year(), month.month(), markets, cancel=cancel, force=force, targets=targets), ''
            except Exception:
                return None, '财报查询失败，请检查网络及长桥接口权限后点击“刷新财报”重试。'
        def ready(value):
            if self.request != request:
                return
            self.fetching = False
            fresh, error = value
            if error:
                self.request = None
                self.status.setText(('本机缓存已保留；' if self.result is not None else '')+error)
            else:
                self.result = fresh
                events = self.visible_events()
                updated = self.result.fetched_at.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M')
                self.status.setText(f'{month.year()}年{month.month()}月 · 当前持仓 {len(self.targets)} 个标的 · 匹配 {len(events)} 项财报 · 更新 {updated} 北京时间'+(' · '+self.result.warning if self.result.warning else ''))
            self.draw()
            if self.window.pages.currentWidget() is self:
                self.window.banner.hide()
        self.window._submit('earnings_calendar', load_cached, cached_ready)

    def visible_events(self):
        targets = {v[0] for v in self.targets.values() if v[0]}
        month = self.month.date()
        return tuple(e for e in reconciled_events((e for e in self.result.events if e.symbol in targets), year=month.year(), month=month.month()) if e.symbol in targets) if self.result else ()

    def linked_holdings(self, symbol):
        names = []
        for k, v in sorted(self.targets.items()):
            if v[0] == symbol:
                desc = v[1] if len(v) > 1 else ''
                if '[宏观焦点]' in desc:
                    names.append('宏观数据')
                elif '[重点盯盘]' in desc:
                    names.append('重点盯盘')
                else:
                    names.append(k)
        return '、'.join(names) or '关注标的'

    @staticmethod
    def change_label(event):
        if event.session_change is None:
            return event.session_status or ('待发布' if release_status(event).startswith('待发布') else '待获取对应时段行情')
        state = '时段最终值' if event.session_final else '时段内更新中'
        stamp = event.session_checked_at.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M') if event.session_checked_at else '待更新'
        prefix = f"大盘({event.macro_ticker or 'QQQ'}) " if getattr(event, 'is_macro', False) else ""
        return f'{prefix}{event.session_change:+.2%} · {state}（查询 {stamp} 北京）'

    def draw_coverage(self, theme):
        targets = sorted({v[0] for v in self.targets.values() if v[0]})
        events = (self.result.coverage_events or self.result.events) if self.result else ()
        today = datetime.now(ZoneInfo('America/New_York')).date()
        missing, phases_missing, rows = [], [], []
        for symbol in targets:
            choices = sorted((e for e in events if e.symbol == symbol), key=lambda e: e.day)
            upcoming = [e for e in choices if e.day >= today]
            event = upcoming[0] if upcoming else choices[-1] if choices else None
            if event is None:
                missing.append(symbol)
                values = (symbol, self.linked_holdings(symbol), '未获取，需补查', '未获取', '—', '未确认', '—', '接口未返回或查询未完成')
            else:
                if not any(phase in event.time_label for phase in ('盘前', '盘后', '盘中')):
                    phases_missing.append(symbol)
                source = '宏观日程' if getattr(event, 'is_macro', False) else ('长桥实际财报' if event.verification_source.startswith('longbridge:') else '公司公告' if event.verification_source else '长桥日历（待公告核实）')
                values = (event.name+' '+symbol, self.linked_holdings(symbol), event.day.isoformat(), event.time_label, event.beijing,
                          release_status(event), self.change_label(event), source)
            rows.append((values, event))
        self.coverage.setRowCount(len(rows))
        for r, (values, event) in enumerate(rows):
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if event is None:
                    item.setForeground(QColor(theme.warning))
                elif c == 6 and event.session_change is not None:
                    item.setForeground(QColor(theme.profit if event.session_change > 0 else theme.loss if event.session_change < 0 else theme.muted))
                self.coverage.setItem(r, c, item)
            self.coverage.setRowHeight(r, 48)
        self.coverage.setFixedHeight(self.coverage.horizontalHeader().height()+len(rows)*48+4)
        ready = [e for e in events if e.confirmed_published]
        pending_prices = sorted({e.symbol for e in ready if e.session_change is None})
        text = f'持仓与焦点关注标的：{len(targets)} 个；3个月内日程已获取 {len(targets)-len(missing)}/{len(targets)} 个。'
        if missing:
            text += ' 日期缺失：'+'、'.join(missing)+'。'
        if phases_missing:
            text += ' 发布时段未获取：'+'、'.join(phases_missing)+'。'
        if pending_prices:
            text += ' 已发布但时段行情暂缺：'+'、'.join(pending_prices)+'。'
        if self.result and self.result.coverage_warning:
            text += self.result.coverage_warning
        unresolved = [h for h, (target, _) in self.targets.items() if not target and h not in INDEX_FUNDS and h not in self.overrides]
        if unresolved:
            text += ' 正股关联待核实：'+'、'.join(unresolved)
        if targets:
            text += ' 盘前比前收盘，盘后比当日常规收盘，宏观数据盯盘纳指QQQ对应时段涨跌。'
        self.coverage_status.setText(text if targets else '当前没有需查询的关联公司或宏观日程。')
        self.coverage_status.setStyleSheet(f'color:{theme.warning if missing or phases_missing or pending_prices or unresolved else theme.muted};')

    def draw(self):
        # Batch table changes so Qt does not relayout/repaint after every inserted cell.
        self.setUpdatesEnabled(False)
        try:
            self._draw()
        finally:
            self.setUpdatesEnabled(True)
        self.schedule_center_today()

    def _draw(self):
        month = self.month.date()
        weeks = calendar.Calendar().monthdayscalendar(month.year(), month.month())
        events = self.visible_events()
        self.calendar.clearContents()
        self.calendar.setRowCount(len(weeks))
        current = datetime.now(ZoneInfo('Asia/Shanghai')).date()
        theme = tokens(self.window)
        self.draw_coverage(theme)

        rating_palette = {
            'S': ('#D97706', '#FEF3C7', '#B45309'),
            'A+': ('#8B5CF6', '#EDE9FE', '#6D28D9'),
            'A': ('#2563EB', '#DBEAFE', '#1D4ED8'),
            'B+': ('#0D9488', '#CCFBF1', '#0F766E'),
        }

        for r, week in enumerate(weeks):
            height = 110
            for c, number in enumerate(week):
                if not number:
                    continue
                day = date(month.year(), month.month(), number)
                items = [e for e in events if e.day == day]
                is_today = day == current
                title = f'<span style="color:{theme.brand};font-size:14px">{number} · 今天</span>' if is_today else str(number)
                text = f'<b>{title}</b>'
                for event in items:
                    rating_key = (getattr(event, 'rating', '') or '').strip()
                    border_col, badge_bg, badge_txt = rating_palette.get(rating_key, (theme.brand, theme.selected, theme.brand))
                    rating_span = f'<span style="background-color:{badge_bg}; color:{badge_txt}; font-size:10px; font-weight:bold; padding:1px 5px; border-radius:3px; margin-left:4px;">{rating_key}级</span>' if rating_key else ''

                    is_macro = getattr(event, 'is_macro', False)
                    icon = '🌐 ' if is_macro else ''

                    card = f'<div style="margin:4px 0; padding:6px 8px; background-color:{theme.alternate}; border-left:3px solid {border_col}; border-radius:4px; font-size:11px; line-height:1.35;">'
                    card += f'<div>{icon}<b>{html.escape(event.name)}</b> <span style="color:{theme.muted}; font-size:10px;">{html.escape(event.symbol)}</span>{rating_span}</div>'
                    card += f'<div style="color:{theme.muted}; font-size:10px; margin-top:2px;">{html.escape(event.time_label)}</div>'
                    card += f'<div style="margin-top:2px;"><b>{html.escape(release_status(event))}</b></div>'

                    if getattr(event, 'expectations', ''):
                        card += f'<div style="margin-top:2px; color:{theme.text}; font-size:10px;"><b>预期：</b>{html.escape(event.expectations)}</div>'
                    if getattr(event, 'highlights', ''):
                        card += f'<div style="margin-top:2px; color:{theme.text}; font-size:10px;"><b>看点：</b>{html.escape(event.highlights)}</div>'

                    if event.session_change is not None:
                        phase_label = '盘前涨跌' if '盘前' in event.time_label else '盘后涨跌' if '盘后' in event.time_label else '盘中涨跌'
                        if is_macro:
                            phase_label = f"大盘({event.macro_ticker or 'QQQ'}){phase_label}"
                        change_str = f"{event.session_change:+.2%}"
                        color = theme.profit if event.session_change > 0 else theme.loss if event.session_change < 0 else theme.muted
                        final_state = '时段最终值' if event.session_final else '时段内持续更新'
                        card += f'<div style="margin-top:2px;"><b>{phase_label}：<span style="color:{color}">{change_str}</span></b> <span style="color:{theme.muted}; font-size:9px;">({final_state})</span></div>'
                    elif event.session_status:
                        card += f'<div style="margin-top:2px; color:{theme.muted}; font-size:9px;">{html.escape(event.session_status)}</div>'

                    if is_macro and getattr(event, 'macro_ticker', ''):
                        card += f'<div style="margin-top:2px; color:{theme.muted}; font-size:9px;">盯盘基准：{html.escape(event.macro_ticker)}</div>'

                    linked = self.linked_holdings(event.symbol)
                    if any(held != event.symbol and target == event.symbol for held, (target, _) in self.targets.items()):
                        card += f'<div style="margin-top:2px; color:{theme.muted}; font-size:9px;">关联持仓：{html.escape(linked)}</div>'

                    card += '</div>'
                    text += card

                label = QLabel(text)
                if is_today:
                    label.setObjectName('earningsToday')
                    label.setStyleSheet(f'QLabel#earningsToday {{ background-color:{theme.selected}; border:2px solid {theme.brand}; border-radius:6px; color:{theme.text}; }}')
                label.setWordWrap(True)
                label.setAlignment(Qt.AlignmentFlag.AlignTop)
                label.setMargin(8)
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self.calendar.setCellWidget(r, c, label)

                day_height = 48 + sum(
                    76 + (20 if getattr(e, 'expectations', '') else 0)
                       + (20 if getattr(e, 'highlights', '') else 0)
                       + (24 if e.session_change is not None else 0)
                       + (16 if getattr(e, 'is_macro', False) else 0)
                       + (16 if any(held != e.symbol and target == e.symbol for held, (target, _) in self.targets.items()) else 0)
                    for e in items
                )
                height = max(height, day_height)
            self.calendar.setRowHeight(r, height)
        self.calendar.setFixedHeight(self.calendar.horizontalHeader().height()+sum(self.calendar.rowHeight(r) for r in range(len(weeks)))+4)

        selected = [e for e in events if self.selected_day is None or e.day == self.selected_day]
        self.details.setRowCount(len(selected))
        for r, event in enumerate(selected):
            change_text = self.change_label(event)
            is_macro = getattr(event, 'is_macro', False)
            name_text = (f"🌐 {event.name}" if is_macro else event.name) + (f" [{event.rating}级]" if getattr(event, 'rating', '') else "")
            sym_text = f"{event.symbol} (盯盘 {event.macro_ticker})" if is_macro and getattr(event, 'macro_ticker', '') else event.symbol
            exp_highlight = (f"预期：{event.expectations}  " if getattr(event, 'expectations', '') else "") + (f"看点：{event.highlights}" if getattr(event, 'highlights', '') else "") or "—"
            values = (event.day.isoformat(), name_text, sym_text, event.time_label, event.beijing, self.linked_holdings(event.symbol), release_status(event), change_text, exp_highlight)
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 7 and event.session_change is not None:
                    color = theme.profit if event.session_change > 0 else theme.loss if event.session_change < 0 else theme.muted
                    item.setForeground(QColor(color))
                self.details.setItem(r, c, item)
            self.details.setRowHeight(r, 48)
        self.details.setFixedHeight(46+len(selected)*48)
        self.day_label.setText((self.selected_day.isoformat()+' 财报与宏观日程' if self.selected_day else '当月财报与宏观明细')+'（点击日历日期筛选，再点同日恢复全部）')
        matched = {e.symbol for e in events}
        unknown = [f'{held} → {target}' for held, (target, _) in sorted(self.targets.items()) if target and target not in matched and not target.endswith('.MACRO') and not held.endswith('.MACRO')]
        unresolved = [held for held, (target, _) in sorted(self.targets.items()) if not target and held not in INDEX_FUNDS and held not in self.overrides]
        funds = [held for held, (target, _) in sorted(self.targets.items()) if not target and held in INDEX_FUNDS]
        notes = []
        if self.result and unknown:
            notes.append('本月未返回正股财报日程（不代表尚未公布）：'+ '；'.join(unknown))
        if unresolved:
            notes.append('待关联正股，请点“关联公司”设置：'+'、'.join(unresolved))
        if funds:
            notes.append('指数基金无单一正股财报：'+'、'.join(funds))
        self.unmatched.setText('\n'.join(notes))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Wheel, QEvent.Type.KeyPress):
            self.stop_centering()
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().eventFilter(watched, event)

    def select_day(self, row, column):
        self.stop_centering()
        weeks = calendar.Calendar().monthdayscalendar(self.month.date().year(), self.month.date().month())
        number = weeks[row][column]
        if number:
            day = date(self.month.date().year(), self.month.date().month(), number)
            self.selected_day = None if day == self.selected_day else day
            self.draw()

    def edit_links(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('持仓与财报公司关联')
        dialog.resize(720, 480)
        layout = QVBoxLayout(dialog)
        help_text = QLabel('ETF可填写标的公司的代码，例如TSLL.US → TSLA.US。普通股票保留自身代码；留空表示不关联。设置仅保存在本机。')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        table = QTableWidget(len(self.targets), 4)
        table.setHorizontalHeaderLabels(('持仓代码', '持仓名称', '财报公司代码', '关联方式'))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        holdings = sorted(self.targets)
        for r, held in enumerate(holdings):
            target, name = self.targets[held]
            status = link_description(held, target, self.overrides)
            for c, text in enumerate((held, name, target, status)):
                item = QTableWidgetItem(text)
                if c != 2:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c == 3:
                    item.setToolTip(SINGLE_STOCK_LINKS.get(held, ('', INDEX_FUNDS.get(held, '')))[1])
                table.setItem(r, c, item)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        def save():
            values = {held: table.item(r, 2).text().strip().upper() for r, held in enumerate(holdings)}
            if any(v and not re.fullmatch(r'[A-Z0-9_-]+\.(US|HK|SH|SZ|MACRO)', v) for v in values.values()):
                QMessageBox.information(dialog, '代码格式', '请使用TSLA.US、00700.HK等包含市场后缀的代码。')
                return
            automatic = holding_targets(self.analytics, self.window.live_panel.instruments, {})
            for held, value in values.items():
                if value == automatic[held][0]:
                    self.overrides.pop(held, None)
                else:
                    self.overrides[held] = value
            if self.window.settings:
                self.window.settings.setValue('earnings_company_links', json.dumps(self.overrides))
            self.targets = holding_targets(self.analytics, self.window.live_panel.instruments, self.overrides)
            dialog.accept()
            self.request = None
            self.fetch()
        buttons.accepted.connect(save)
        layout.addWidget(buttons)
        dialog.exec()
