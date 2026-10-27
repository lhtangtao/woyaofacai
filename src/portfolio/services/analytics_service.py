import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from decimal import localcontext
from pathlib import Path
from threading import RLock

from portfolio.db.connection import readonly_connection
from portfolio.db.repositories.analytics import load_snapshot
from portfolio.domain.analytics import Analytics, Issue, Report, ReportRow
from portfolio.domain.audit_period import AUDIT_START_DATE
from portfolio.domain.models import TRADE_COLUMNS, Ledger
from portfolio.engine.audit import audit
from portfolio.engine.dashboard import KPI_FORMULAS, KPI_NAMES, overview
from portfolio.engine.fx import convert_reports
from portfolio.engine.holdings import position_reports
from portfolio.engine.performance import DAILY_COLUMNS, MONTHLY_COLUMNS, NET_COLUMNS, performance
from portfolio.engine.replay import grain_conflicts
from portfolio.engine.scalping import scalping
from portfolio.services.charts_service import build_charts


class AnalyticsService:
    """Single-snapshot pipeline; bounded process-local cache; never writes source."""
    def __init__(self, path, *, audit_start=AUDIT_START_DATE, fx_store=None, quote_store=None, derived_store=None):
        self.path = Path(path).expanduser().resolve()
        from portfolio.services.derived_store import DerivedStore
        self.derived = derived_store or DerivedStore(self.path)
        self.audit_start = audit_start
        self.quote_store = quote_store
        if self.quote_store: self.quote_store.source = self.path
        self.fx_store = fx_store
        self._cache, self._lock = {}, RLock()

    def identity(self):
        # WAL changes must invalidate the cache even when the main file mtime is stable.
        files = (self.path, Path(str(self.path) + "-wal"))
        return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files if p.exists()) + ((self.fx_store.identity(),) if self.fx_store else ()) + ((self.quote_store.history_identity(),) if self.quote_store else ())

    def calculate_people(self, scope, *, cancel=None):
        from portfolio.engine.people_pnl import PeopleBundle
        identity = self.identity()
        scope = replace(scope, ledger=Ledger.PERSON, person_ids=None)
        aggregate = self.calculate(scope, cancel=cancel)
        with readonly_connection(self.path, cancel=cancel) as connection:
            people = list(connection.execute('SELECT id,name FROM persons' + ('' if scope.include_disabled else ' WHERE enabled=1') + ' ORDER BY id'))
        persons = []
        for person, name in people:
            if cancel and cancel.is_set():
                raise ValueError('计算已取消。')
            persons.append((person, name, self.calculate(replace(scope, person_ids=(person,)), cancel=cancel)))
        broker = self.calculate(replace(scope, ledger=Ledger.BROKER), cancel=cancel)
        if identity != self.identity():
            raise ValueError('源库在人员核对期间发生变化，请刷新重试。')
        return PeopleBundle(aggregate, tuple(persons), broker)

    def calculate(self, scope, *, cancel=None, use_cache=True):
        with self.derived.active():
            return self._calculate(scope, cancel=cancel, use_cache=use_cache)

    def _calculate(self, scope, *, cancel=None, use_cache=True):
        key = (self.identity(), scope, "decimal-engine-2", self.audit_start)
        with self._lock:
            if use_cache and key in self._cache:
                return self._cache[key]
        with localcontext() as ctx:
            ctx.prec = 50
            with readonly_connection(self.path, cancel=cancel, timeout=120) as connection:
                snapshot = load_snapshot(connection, replace(scope, reporting_currency="USD"), cancel=cancel)
                if self.fx_store and scope.reporting_currency == "CNY":
                    snapshot.extras["fx_rates"] = self.fx_store.supplement(snapshot.extras.get("fx_rates", []), scope.as_of)
                if self.quote_store:
                    self.quote_store.supplement(snapshot)
                # Full ownership-vs-broker quantity check, including inactive owners.
                for_owners = replace(scope, ledger=Ledger.PERSON, person_ids=None, include_disabled=True, search="", start_date=None, audit=False, reporting_currency="USD")
                for_broker = replace(for_owners, ledger=Ledger.BROKER)
                owner = load_snapshot(connection, for_owners, cancel=cancel, with_prices=False)
                broker = load_snapshot(connection, for_broker, cancel=cancel, with_prices=False)
                snapshot.extras["ledger_comparison"] = (broker, owner)
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            snapshot.source_identity = hashlib.sha256(repr(key[0]).encode()).hexdigest()[:16] + f":schema{schema_version}"
            micro, pairs, tags, profit_map = scalping(snapshot, cancel=cancel)
            blocked = grain_conflicts(snapshot)
            affected_micro = set()
            for event in snapshot.events:
                linked_id = event.id if scope.ledger == Ledger.BROKER else event.metadata.get("broker_trade_id")
                if linked_id in blocked:
                    affected_micro.add((event.day[:7], event.account_id, event.instrument_id))
                    tags[event.id] = "成交粒度待核对，不确认微操"
            if affected_micro:
                for key in affected_micro:
                    profit_map[key] = None
                changed = []
                present = set()
                for row in micro.rows:
                    key = (row.values[0], row.metadata["account_id"], row.metadata["instrument_id"])
                    present.add(key)
                    changed.append(replace(row, values=(*row.values[:4], *([None] * 8), *row.values[12:]),
                                           metadata={**row.metadata, "completeness": "GRAIN_CONFLICT"}) if key in affected_micro else row)
                for month, account, instrument in affected_micro - present:
                    changed.append(ReportRow((month, snapshot.accounts[account]["broker_code"], snapshot.instruments[instrument]["category"], snapshot.instruments[instrument]["symbol"], *([None] * 8), None, None),
                                              {"account_id": account, "instrument_id": instrument, "completeness": "GRAIN_CONFLICT"}))
                micro = replace(micro, rows=tuple(changed))
                pair_rows = []
                for row in pairs.rows:
                    key = (row.values[0], row.metadata["account_id"], row.metadata["instrument_id"])
                    pair_rows.append(replace(row, values=(*row.values[:5], *([None] * 5), *row.values[10:]),
                                             metadata={**row.metadata, "completeness": "GRAIN_CONFLICT", "candidate_values": row.values}) if key in affected_micro else row)
                pairs = replace(pairs, rows=tuple(pair_rows))
            positions, traces, performance_reports, days = performance(snapshot, profit_map, cancel=cancel)
            reports = position_reports(snapshot, positions, micro_profit=profit_map)
            reports.update(performance_reports or {"每日持仓收益": Report("每日持仓收益", DAILY_COLUMNS, ()), "每月持仓收益": Report("每月持仓收益", MONTHLY_COLUMNS, ()), "月度净投入明细": Report("月度净投入明细", NET_COLUMNS, ()), "完整交易净投入": Report("完整交易净投入", NET_COLUMNS, ())})
            reports.update({micro.name: micro, pairs.name: pairs})
            trade_rows = []
            direction = {"BUY": "买入", "SELL": "卖出", "TRANSFER_IN": "转入", "TRANSFER_OUT": "转出"}
            for event in reversed(snapshot.events):
                e = event
                meta = {**e.metadata, "source_record_ids": (e.source_id,), "instrument_id": e.instrument_id, "record_id": e.id}
                instrument = snapshot.instruments[e.instrument_id]
                source = f"{meta.get('source_path') or meta.get('source_kind', '')} / {meta.get('source_sheet', '')}:{meta.get('source_row', '')}"
                kind_label = {"MARKET_TRADE": "市场交易", "OWNER_TRANSFER": "人员划转", "EXTERNAL_TRANSFER": "外部划转", "BOOK_SETTLEMENT": "账面结算"}[e.kind]
                trade_rows.append(ReportRow((e.time, snapshot.accounts[e.account_id]["broker_code"], instrument["category"], instrument["symbol"],
                                            meta.get("legacy_direction") or direction[e.side], e.quantity, e.price, e.amount,
                                            tags.get(e.id, ""), *traces.get(e.id, (None, None, None)), meta.get("legacy_transaction_type") or kind_label, meta.get("counterparty", ""), meta.get("order_id", ""), meta.get("legacy_source") or source), meta))
            reports["交易回放"] = Report("交易回放", TRADE_COLUMNS, tuple(trade_rows), "此页为有效账本完整回放；审计原始页另可查看撤销/参考行。")
            reports["人员划转"] = Report("人员划转", TRADE_COLUMNS, tuple(r for r in trade_rows if r.values[12] in ("人员划转", "权益划转")), "权益在人员之间变更，不能计入券商真实成交。")
            issue_report = audit(snapshot, positions, start_date=self.audit_start)
            metrics, dashboard_reports, latest = overview(snapshot, reports)
            reports.update(dashboard_reports)
            valuation_days = tuple(sorted({r.metadata["valuation_date"] for r in reports["当前持仓"].rows if r.metadata.get("valuation_date")}))
            if latest and valuation_days and any(day != latest for day in valuation_days):
                snapshot.issues.append(Issue("DQ012", "WARN", f"持仓估值日 {','.join(valuation_days)} 与收益最大日期 {latest} 不一致。"))
                issue_report = audit(snapshot, positions, start_date=self.audit_start)
            reports[issue_report.name] = issue_report
            snapshot.extras["positions"] = positions
            self._data_center(snapshot, reports)
            reports["指标说明"] = Report("指标说明", ("指标", "公式/解释"), tuple(ReportRow((name, formula)) for name, formula in zip(KPI_NAMES, KPI_FORMULAS)))
            import json
            from importlib.resources import files
            help_catalog = json.loads(files("portfolio").joinpath("resources/metric_help.json").read_text(encoding="utf-8"))
            for name, values in help_catalog.items():
                width = len(values[0])
                columns = ("序号", "报表", "字段", "含义", "来源", "源字段", "公式", "备注") if width == 8 else ("范围", "指标", "公式", "说明", "备注")
                reports[name] = Report(name, columns, tuple(ReportRow(tuple(row)) for row in values[1:]), "原 Excel 说明只作规则证据；与新引擎差异见决策文档。")
            if scope.reporting_currency != "USD":
                metrics = convert_reports(snapshot, reports, metrics, scope.reporting_currency)
                reports["数据异常"] = audit(snapshot, positions, start_date=self.audit_start)
            # Overview/FX can add diagnostics after the first audit. Keep counts,
            # report rows and available KPI badges on the same final check window.
            reports["数据异常"] = audit(snapshot, positions, start_date=self.audit_start)
            review = any(i.severity in ("ERROR", "WARN") for i in snapshot.issues)
            metrics = tuple(replace(m, status="REVIEW_REQUIRED" if review else "COMPLETE")
                            if m.value is not None and m.status in ("COMPLETE", "REVIEW_REQUIRED") else m for m in metrics)
            result = Analytics(scope, reports, metrics, snapshot.issues, positions, traces, latest, valuation_days,
                               calculated_at=datetime.now(timezone.utc).isoformat(), source_identity=snapshot.source_identity,
                               trading_days=snapshot.extras.get("trading_days", ()))
            # Filter display rows only AFTER replay and dashboard calculations.
            if scope.start_date:
                for name in ("每日持仓收益", "每月持仓收益", "每月微操收益", "月度净投入明细", "完整交易净投入", "微操配对证据", "交易回放"):
                    report = reports[name]
                    cutoff = scope.start_date.isoformat()[:7] if name != "每日持仓收益" and name != "交易回放" else scope.start_date.isoformat()
                    reports[name] = replace(report, rows=tuple(r for r in report.rows if str(r.values[0])[:len(cutoff)] >= cutoff))
            result.charts = build_charts(result)
            with self._lock:
                if len(self._cache) >= 4:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[key] = result
            return result

    @staticmethod
    def _data_center(snapshot, reports):
        s = snapshot.scope
        def matches(row):
            return (s.account_ids is None or row.get("account_id") in s.account_ids) and (s.person_ids is None or row.get("person_id") in s.person_ids) and (not row.get("business_date") or row["business_date"] <= s.as_of.isoformat())
        for name, table, columns in (
            ("资金事件", "cash_events", ("id", "business_date", "account_id", "person_id", "event_type", "amount_e8", "currency", "accounting_role", "record_state", "description", "source_record_id")),
            ("资产调整", "asset_adjustments", ("id", "effective_from", "effective_to", "target_metric", "adjustment_mode", "amount_e8", "currency", "status", "reason", "source_record_id")),
            ("导入批次", "import_batches", ("id", "source_kind", "source_scope", "status", "imported_at_utc", "record_count", "parser_version")),
            ("汇率", "fx_rates", ("rate_date", "base_currency", "quote_currency", "rate_e8", "provider", "source_record_id")),
        ):
            from portfolio.domain.models import from_e8
            rows = []
            for row in snapshot.extras.get(table, []):
                if not matches(row) and table in ("cash_events", "asset_adjustments"):
                    continue
                rows.append(ReportRow(tuple(from_e8(row[c]) if c.endswith("_e8") else row[c] for c in columns), {"source_record_ids": (row["source_record_id"],) if row.get("source_record_id") else ()}))
            reports[name] = Report(name, columns, tuple(rows), "资金收付是净变动，未经可信期初余额不能称为现金余额。" if table == "cash_events" else "只读原始记录；不把现金/融资调整混入股票市值。")
        if snapshot.extras.get("fx_rates"):
            from portfolio.domain.models import from_e8
            columns = ("折算日期", "原币种", "目标币种", "汇率", "来源", "实际发布日期", "来源链接", "原始记录ID")
            rows = tuple(ReportRow((r["rate_date"], r["base_currency"], r["quote_currency"], from_e8(r["rate_e8"]), r["provider"], r.get("published_date", r["rate_date"]), r.get("source_url", ""), r.get("source_record_id")),
                                  {"source_record_ids": (r["source_record_id"],) if r.get("source_record_id") else (), "fx_payload_sha256": r.get("payload_sha256")}) for r in snapshot.extras["fx_rates"])
            reports["汇率"] = Report("汇率", columns, rows, "优先源库当日值；ECB参考汇率保存在独立缓存库。非发布日沿用此前最近公布值（最多7天），折算日与实际发布日期分别展示。")
        reports["行情状态"] = Report("行情状态", ("证券", "日期", "类型", "价格", "状态", "股本口径", "报价UTC", "原始记录ID"),
                                   tuple(ReportRow((snapshot.instruments[p.instrument_id]["symbol"], p.day, p.kind, p.value, p.status, p.basis, p.quoted_at, p.source_id), {"instrument_id": p.instrument_id, "source_record_ids": (p.source_id,)}) for p in snapshot.prices))
        raw_columns = ("id", "import_batch_id", "source_locator", "source_sheet", "source_row", "record_kind", "parse_status")
        reports["原始记录"] = Report("原始记录", raw_columns, tuple(ReportRow(tuple(r[c] for c in raw_columns), {"source_record_ids": (r["id"],)}) for r in snapshot.extras.get("raw_index", [])), "按来源行只读索引；点击查看遮盖后的 JSON，不自动导出原始敏感 payload。")
        from collections import defaultdict
        from decimal import Decimal
        from portfolio.domain.analytics import ZERO, ratio
        from portfolio.engine.holdings import current_price, display_quantity, grouped_positions

        raw_positions = snapshot.extras.get("positions", {})
        person_totals = defaultdict(Decimal)
        for key, p in raw_positions.items():
            untrusted = "成交粒度冲突" in p.warnings
            quantity = None if untrusted else display_quantity(p.quantity)
            if quantity and quantity > ZERO:
                price_obj = current_price(snapshot, key[2])
                if price_obj and price_obj.value:
                    person_totals[key[0]] += quantity * price_obj.value

        for dimension, name in ((0, "人员资产"), (1, "账户资产")):
            rows = []
            positions = raw_positions
            if dimension == 1:
                positions = grouped_positions(positions)
            for key, p in sorted(positions.items(), key=lambda item: str(item[0])):
                if dimension == 0:
                    broker = snapshot.accounts[key[1]]["broker_code"]
                    channel = {"LONGBRIDGE": "长桥", "FIRSTRADE": "第一证券", "SCHWAB": "嘉信", "BBAE": "BBAE"}.get(broker.upper(), broker)
                    untrusted = "成交粒度冲突" in p.warnings
                    quantity = None if untrusted else display_quantity(p.quantity)
                    state = "成交待核对" if untrusted else "数量未知" if quantity is None else "已清仓" if quantity == 0 else "持有中" if quantity > 0 else "负仓异常"

                    price_obj = current_price(snapshot, key[2])
                    price = price_obj.value if price_obj else (p.last_price if quantity == 0 else None)
                    closed_cost = ratio(p.buy, p.buy_quantity) if (quantity == 0 and p.buy is not None and p.buy_quantity and p.buy_quantity > ZERO) else None
                    cost = p.cost if (quantity and quantity > 0) else closed_cost
                    mv = (quantity * price) if (quantity and quantity > 0 and price is not None) else (Decimal(0) if quantity == 0 else None)
                    net = p.net
                    pnl = (mv - net) if (mv is not None and net is not None and quantity and quantity > 0) else None
                    pnl_rate = ratio(pnl, net) if (quantity and quantity > 0) else None
                    recovery = ((cost - price) / price) if (cost is not None and price is not None and price > 0 and cost > price and quantity and quantity > 0) else None
                    sell_price = ratio(p.sell, p.sell_quantity) if (quantity == 0 and p.sell is not None and p.sell_quantity and p.sell_quantity > ZERO) else None
                    post_exit_change = ((price - sell_price) / sell_price) if (quantity == 0 and sell_price and sell_price > ZERO and price is not None) else None
                    recovery_or_exit = recovery if (quantity and quantity > 0) else post_exit_change
                    gain = -net if (quantity == 0 and net is not None) else None
                    pnl_or_gain = pnl if (quantity and quantity > 0) else (gain if quantity == 0 else None)
                    tot = person_totals[key[0]]
                    weight = ratio(mv, tot) if (tot and quantity and quantity > 0 and mv is not None) else None

                    vals = (
                        snapshot.persons.get(key[0], {}).get("name", "券商真实账本"),
                        channel,
                        snapshot.instruments[key[2]]["symbol"],
                        quantity,
                        state,
                        cost,
                        price,
                        mv,
                        pnl_or_gain,
                        pnl_rate,
                        recovery_or_exit,
                        weight,
                        net,
                    )
                    meta = {
                        "person_id": key[0],
                        "account_id": key[1],
                        "instrument_id": key[2],
                        "broker_code": broker,
                        "source_record_ids": tuple(p.source_ids),
                        "valuation_date": price_obj.day if price_obj else None,
                        "buy_cost": ratio(p.buy, p.buy_quantity) if (p.buy is not None and p.buy_quantity) else None,
                        "sell_price": sell_price,
                        "buy_quantity": p.buy_quantity,
                        "sell_quantity": p.sell_quantity,
                    }
                    rows.append(ReportRow(vals, meta))
                    continue
                rows.append(ReportRow((snapshot.persons.get(key[0], {}).get("name", "券商真实账本") if dimension == 0 else snapshot.accounts[key[1]]["display_name"],
                                       snapshot.accounts[key[1]]["display_name"], snapshot.instruments[key[2]]["symbol"], p.quantity, p.net),
                                      {"person_id": key[0], "account_id": key[1], "instrument_id": key[2], "source_record_ids": tuple(p.source_ids)}))
            columns = (
                "人员", "渠道", "Ticker", "数量", "当前状态",
                "持仓成本/清仓成本($)", "最新价($)", "当前市值($)", "持仓盈亏/清仓收益($)", "盈亏比例", "回本涨幅/清仓后涨跌", "占比",
                "净投入($)"
            ) if dimension == 0 else ("账户", "券商账户", "Ticker", "数量", "净投入($)")
            reports[name] = Report(name, columns, tuple(rows))
