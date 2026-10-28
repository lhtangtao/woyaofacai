"""Read-only display of shared persisted five-minute P&L contributions."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from portfolio.services.charts_service import Chart, Series
from portfolio.domain.live_quotes import NY, UTC
from portfolio.services.intraday_history import scope_key
from portfolio.ui.charts_page import ChartCanvas
from portfolio.ui.theme import tokens


def _resolve_effective_day(scope_as_of: date, session, report, trading_days: tuple[str, ...] = ()) -> tuple[str, str, bool]:
    """计算用于日内走势图的实际交易日，非交易日/休市自动回溯到最近有效交易日。

    返回: (effective_day, target_day, is_fallback)
    - effective_day: 实际绘制走势的交易日 ISO 字符串 (如 '2026-10-23')
    - target_day: 界面选定的目标日期 ISO 字符串 (如 '2026-10-24')
    - is_fallback: 是否触发了非交易日自动回溯
    """
    target_day = scope_as_of.isoformat()
    report_day = next((r.metadata.get('trading_day') for r in report.rows if r.metadata.get('trading_day')), '') if report and report.rows else ''

    # 1. 处于开市/盘中时段：严格跟随当前开盘时钟与活跃交易日
    if session is not None:
        active_day = report_day or target_day
        return active_day, target_day, False

    # 2. 处于休市时段：
    # 2.1 如果用户选中的目标日期本身就是美股交易日（如周五或历史交易日），直接按所选日期呈现，不回溯
    if trading_days and target_day in trading_days:
        return target_day, target_day, False

    # 2.2 目标日期不是美股交易日（如周六、周日、美股节假日休市），触发回溯
    # 优先使用 report_rows 中持仓引擎已计算出的最新有效收盘日（若 <= target_day）
    if report_day and (not trading_days or report_day in trading_days) and report_day <= target_day:
        return report_day, target_day, (report_day != target_day)

    # 从交易日历中寻找 <= target_day 的最近一个交易日
    if trading_days:
        past = [d for d in trading_days if d <= target_day]
        if past:
            effective = max(past)
            return effective, target_day, (effective != target_day)

    # 备选保底：若是周六/周日，回退至当周周五
    if scope_as_of.weekday() >= 5:
        prior = scope_as_of - timedelta(days=scope_as_of.weekday() - 4)
        return prior.isoformat(), target_day, True

    return target_day, target_day, False


class IntradayPnlChart(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = ChartCanvas(card_mode=True)
        self.canvas.setFixedHeight(420)
        self.status = QLabel('后台独立采集五分钟K线并计算，图表读取已保存明细。')
        self.status.setWordWrap(True)
        self.status.setObjectName('secondary')
        self.alert = QLabel()
        self.alert.setWordWrap(True)
        self.alert.hide()
        layout.addWidget(self.canvas)
        layout.addWidget(self.alert)
        layout.addWidget(self.status)
        self.snapshots, self.day, self.error = (), '', ''
        self.target_day = ''
        self.is_fallback = False
        self.history, self.history_warning, self.key = (), '', None
        self.hide()

    def bind(self, store, source_path, scope, report, session, now, *, record=False, trading_days=()):
        self.error = ''
        self.scope = scope
        self.person_names = {}
        self.day, self.target_day, self.is_fallback = _resolve_effective_day(
            scope.as_of, session, report, trading_days
        )
        self.snapshots = ()
        if not store or not source_path or not self.day:
            return
        try:
            from portfolio.db.connection import readonly_connection
            with readonly_connection(source_path) as c:
                self.person_names = {r[0]: r[1] for r in c.execute('SELECT id, name FROM persons')}
        except Exception:
            pass
        key=scope_key(source_path,scope)
        self.key=key,self.day
        self.history=();self.snapshots=()
        try:
            from portfolio.services.derived_queries import day_history
            self.history, state = day_history(source_path, store, scope, self.day)
            self.history_warning = state.get('message', '')
            report_day = next((r.metadata.get('trading_day') for r in report.rows if r.metadata.get('trading_day')), '') if report and report.rows else ''
            if not self.history and report_day and report_day != self.day and session is None:
                fallback_history, fallback_state = day_history(source_path, store, scope, report_day)
                if fallback_history:
                    self.history = fallback_history
                    self.day = report_day
                    self.is_fallback = True
                    self.history_warning = fallback_state.get('message', '')
        except Exception:
            self.error='已保存的五分钟明细暂时读取失败，请查看后台任务状态；未使用分钟快照替代。'

    def draw(self, search='', channel=None):
        zone = ZoneInfo('Asia/Shanghai')
        samples = {};parts={}
        complete = 0
        history = True
        observations = self.history
        interval = timedelta(minutes=5)
        win = self.window()
        others_mode = getattr(getattr(win, 'person', None), '_others_mode', False) if win else False
        scope_pids = getattr(getattr(self, 'scope', None), 'person_ids', None)
        multi_pids = scope_pids if (scope_pids and len(scope_pids) > 1 and not others_mode) else None
        per_person_samples = {pid: {} for pid in (multi_pids or ())}
        for stamp, phase, rows in observations:
            selected = [r for r in rows if (channel is None or r['channel'] == channel)
                        and search.casefold().strip() in r['ticker'].casefold()]
            with localcontext() as context:
                context.prec = 50
                value = sum((Decimal(r['pnl']) for r in selected), Decimal(0)) if selected and all(r['pnl'] is not None for r in selected) else None
                if multi_pids:
                    for pid in multi_pids:
                        p_sel = [r for r in selected if r.get('person_id') == pid]
                        p_val = sum((Decimal(r['pnl']) for r in p_sel), Decimal(0)) if p_sel and all(r['pnl'] is not None for r in p_sel) else (Decimal(0) if rows else None)
                        per_person_samples[pid][stamp.astimezone(UTC)] = p_val
            complete += value is not None
            samples[stamp.astimezone(UTC)] = value
            from portfolio.engine.today_holdings import intraday_scope_parts
            parts[stamp.astimezone(UTC)]=intraday_scope_parts(selected)
        # Keep the entire session on screen, even before the market closes.
        # ISO keys remain unique across midnight; only tick labels omit dates.
        points, labels, ticks = [], {}, []
        person_points = {pid: [] for pid in (multi_pids or ())}
        vertical_lines = []
        if self.day:
            target = date.fromisoformat(self.day)
            start = datetime.combine(target-timedelta(days=1), time(20), NY).astimezone(UTC)
            end = datetime.combine(target, time(20), NY).astimezone(UTC)
            max_stamp = max((s for s, v in samples.items() if v is not None), default=None)
            last_val = None
            last_p_vals = {pid: None for pid in (multi_pids or ())}
            stamp = start
            while stamp <= end:
                key = stamp.isoformat()
                val = samples.get(stamp)
                if val is not None:
                    last_val = val
                elif max_stamp is not None and stamp <= max_stamp and last_val is not None:
                    val = last_val
                points.append((key, val))
                if multi_pids:
                    for pid in multi_pids:
                        pv = per_person_samples[pid].get(stamp)
                        if pv is not None:
                            last_p_vals[pid] = pv
                        elif max_stamp is not None and stamp <= max_stamp and last_p_vals[pid] is not None:
                            pv = last_p_vals[pid]
                        person_points[pid].append((key, pv))
                labels[key] = stamp.astimezone(zone).strftime('%H:%M')
                if (stamp-start).total_seconds() % 1800 == 0 or stamp == end:
                    ticks.append(key)
                stamp += interval

            # 美股时段分界线（美东时间转换为UTC时间，自动适配夏令时/冬令时）：
            # 1. 夜盘开始：前一日 20:00 NY -> 夏令时北京 08:00
            # 2. 盘前开始：当日 04:00 NY -> 夏令时北京 16:00
            # 3. 盘中开始：当日 09:30 NY -> 夏令时北京 21:30
            # 4. 盘后开始：当日 16:00 NY -> 夏令时北京 04:00 (次日)
            t_pre = datetime.combine(target, time(4, 0), NY).astimezone(UTC)
            t_intra = datetime.combine(target, time(9, 30), NY).astimezone(UTC)
            t_post = datetime.combine(target, time(16, 0), NY).astimezone(UTC)

            vertical_lines = [
                (start.isoformat(), f"夜盘 {start.astimezone(zone).strftime('%H:%M')}"),
                (t_pre.isoformat(), f"盘前 {t_pre.astimezone(zone).strftime('%H:%M')}"),
                (t_intra.isoformat(), f"盘中 {t_intra.astimezone(zone).strftime('%H:%M')}"),
                (t_post.isoformat(), f"盘后 {t_post.astimezone(zone).strftime('%H:%M')}"),
            ]
        title = '当日全部交易盈亏五分钟走势（含当日清仓）'
        day_sub = f'{self.day}（非交易日自动回溯）' if self.is_fallback else (self.day or "—")
        if multi_pids:
            series_tuple = tuple(
                Series(getattr(self, 'person_names', {}).get(pid, f'人员{pid}'), tuple(person_points[pid]))
                for pid in multi_pids
            )
        else:
            series_tuple = (Series('当日全部交易盈亏（含当日清仓）', tuple(points)),)
        self.canvas.chart = Chart(-100, f'{title}（USD） · 实盘交易日 {day_sub}', 'line',
                                  series_tuple,
                                  '北京时间；完整交易日从夜盘至盘后，跨日后时钟归零。未到时段留白；五分钟K线估值，含当日清仓。' if history else
                                  '北京时间；完整交易时段固定显示，未到时段留白。正在补取长桥历史五分钟行情。',
                                  x_labels=labels, x_ticks=tuple(ticks),
                                  show_points=False,
                                  vertical_lines=tuple(vertical_lines),
                                  highlight_extremes=True)
        self.canvas.update()
        regular_alert = '盘中行情缺失' in self.history_warning
        self.alert.setVisible(regular_alert)
        if regular_alert:
            theme = tokens(self)
            self.alert.setText('⚠ '+self.history_warning)
            self.alert.setStyleSheet(f'color:{theme.warning};background:{theme.panel};border:1px solid {theme.warning};border-radius:8px;padding:10px;font-size:13px;')
        fallback_tag = f'［所选日期 {self.target_day} 非美股交易日，已自动回溯至最近交易日 {self.day}］ ' if self.is_fallback else ''
        if self.error:
            self.status.setText(fallback_tag + self.error)
        elif not complete:
            self.status.setText(fallback_tag + (self.history_warning or '尚无完整有效五分钟点，请查看后台采集状态；不使用实时快照代替历史。'))
        else:
            latest_stamp, latest = next((stamp, value) for stamp, value in sorted(samples.items(), reverse=True) if value is not None)
            components=parts.get(latest_stamp,{})
            describe=lambda value:f'{value:+,.2f}' if value is not None else '—'
            scope_note=f" 同一五分钟点：持仓部分 {describe(components.get('current_holdings'))}，当日全清仓部分 {describe(components.get('closed_today'))} USD。"
            latest_stamp = latest_stamp.astimezone(zone).strftime('%H:%M')
            interval_text = '五分钟历史' if history else '实时分钟'
            self.status.setText(fallback_tag + f'{complete} 个完整{interval_text}点 · 最近有效盈亏 {latest:+,.2f} USD · 最后有效五分钟点 {latest_stamp} 北京时间（上方卡片为独立10秒报价）。'
                                + ('长桥行情已落库，重启后优先读库；含当日清仓，可能与仅当前持仓的上方卡片不同。' if history else '正在补取开机前的历史行情。')
                                + (' 提示：'+self.history_warning if self.history_warning and not regular_alert else ''))
            self.status.setText(self.status.text()+scope_note)
