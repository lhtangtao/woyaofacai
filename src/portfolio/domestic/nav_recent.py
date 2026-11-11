"""Check and repair recent domestic NAV gaps without replaying the ledger."""
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import fcntl
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .nav_collection import Collector, FIELDS, HK_SOURCE, SCHEMA, SOURCE, START, encode, fund_list, number, publish
from .storage import RAW_FILE, backup, connect, now, operation


def mainland_history(code, first, last):
    from akshare.utils import http

    rows = []
    page = 1
    while True:
        response = http.get("https://api.fund.eastmoney.com/f10/lsjz", timeout=12,
                            headers={"Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html"},
                            params=dict(fundCode=code, pageIndex=page, pageSize=20,
                                        startDate=first, endDate=last))
        response.raise_for_status()
        payload = json.loads(response.text, parse_float=Decimal)
        total = int(payload["TotalCount"])
        batch = (payload.get("Data") or {}).get("LSJZList") or []
        rows.extend(batch)
        if len(rows) == total:
            return rows
        if not batch or len(rows) > total or page >= 4:
            raise ValueError("最近净值分页返回不完整")
        page += 1


def expected_gaps(first, last, calendar, money, fields, stored):
    gaps = {}
    for offset in range(max(0, (last - first).days + 1)):
        day = first + timedelta(days=offset)
        key = day.isoformat()
        if money or day in calendar or key in stored:
            missing = [field for field in fields if not stored.get(key, {}).get(field)]
            if missing:
                gaps[key] = missing
    return gaps


def check(root, *, today=None):
    root = Path(root)
    path = root / RAW_FILE
    if not path.is_file():
        print("国内资产原始库尚未建立。", flush=True)
        return False
    with (root / ".recent-nav.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("国内基金净值检查正在运行。", flush=True)
            return False
        return _check(root, path, today or datetime.now(ZoneInfo("Asia/Shanghai")).date())


def _check(root, path, today):
    import akshare as ak

    first = max(START, today - timedelta(days=30))
    last = today - timedelta(days=1)
    collector = Collector(ak, last, 3)
    funds = fund_list(path)
    if not funds:
        return False
    with connect(path, readonly=True) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        info = ({r["fund_code"]: dict(r) for r in db.execute("SELECT * FROM raw_fund_info")}
                if "raw_fund_info" in tables else {})
        stored = {}
        if "raw_fund_nav_daily" in tables:
            for row in db.execute("""SELECT * FROM raw_fund_nav_daily
                WHERE nav_date BETWEEN ? AND ? AND source IN (?,?)""",
                (first.isoformat(), last.isoformat(), SOURCE, HK_SOURCE)):
                stored.setdefault((row["fund_code"], row["source"]), {})[row["nav_date"]] = dict(row)
    dates = collector.fetch("*", "中国交易日历", ak.tool_trade_date_hist_sina)
    calendar = set() if dates is None or "trade_date" not in dates else {date.fromisoformat(str(d)) for d in dates["trade_date"]}
    calendar_end = max(calendar, default=date.min)
    unverified_days = {day for offset in range((last - first).days + 1)
                       if (day := first + timedelta(days=offset)) > calendar_end and day.weekday() < 5}
    if unverified_days:
        collector.issue("*", "中国交易日历", "日历未覆盖最近日期，缺少日历的工作日仅作补采候选")
        calendar.update(unverified_days)
    unknown = [f for f in funds if not (info.get(f["code"], {}).get("fund_type") or f["kind"])
               and not f["code"].startswith("968")]
    directory = collector.fetch("*", "基金目录", ak.fund_name_em) if unknown else None
    types = dict(zip(directory["基金代码"], directory["基金类型"])) if directory is not None and {
        "基金代码", "基金类型"}.issubset(directory.columns) else {}
    hk_products = None
    candidates, requested, gaps_before = [], 0, 0
    for fund in funds:
        code = fund["code"]
        metadata = info.get(code, {})
        hk = code.startswith("968")
        kind = metadata.get("fund_type") or fund["kind"] or types.get(code)
        if not kind and not hk:
            collector.issue(code, "fund_type", "基金类型待核实")
            continue
        money = "货币" in (kind or "")
        source = HK_SOURCE if hk else SOURCE
        local = stored.get((code, source), {})
        mapping = ({"DWJZ": "income_per_10k", "LJJZ": "yield_7d"} if money else
                   {"NAV": "unit_nav", "ACCNAV": "accumulated_nav", "NAVCHGRT": "daily_return_rate"} if hk else
                   {"DWJZ": "unit_nav", "LJJZ": "accumulated_nav", "JZZZL": "daily_return_rate"})
        required = ("unit_nav",) if hk else tuple(mapping.values())
        established = metadata.get("establishment_date")
        begin = max(first, date.fromisoformat(established)) if established else first
        details = json.loads(metadata.get("raw_payload") or "{}").get("details") or {}
        terminal = next((details[k] for k in ("清算日期", "终止日期", "基金终止日") if details.get(k)), None)
        try:
            finish = min(last, date.fromisoformat(terminal[:10])) if terminal else last
        except ValueError:
            collector.issue(code, "终止日期", "来源日期待核实")
            finish = last
        gaps = expected_gaps(begin, finish, calendar, money, required, local)
        if not gaps:
            continue
        gaps_before += len(gaps)
        requested += 1
        if hk:
            if hk_products is None:
                directory = collector.fetch("*", "香港互认基金目录", ak.fund_hk_rank_em)
                hk_products = dict(zip(directory["基金代码"].astype(str), directory["香港基金代码"].astype(str))) if (
                    directory is not None and {"基金代码", "香港基金代码"}.issubset(directory.columns)) else {}
            product = hk_products.get(code)
            if product:
                frame = collector.fetch(code, "最近净值", collector.hk_history, fund_code=code, product=product,
                                        action="2", start=date.fromisoformat(min(gaps)))
                response = frame.to_dict("records") if frame is not None else None
            else:
                collector.issue(code, "最近净值", "香港互认基金缺少来源产品标识")
                response = None
        else:
            response = collector.fetch(code, "最近净值", lambda: mainland_history(code, min(gaps), max(gaps)))
        repaired = {}
        seen = Counter(str(row.get("PDATE" if hk else "FSRQ"))[:10] for row in response or ())
        for raw in response or ():
            day = str(raw.get("PDATE" if hk else "FSRQ"))[:10]
            if day not in gaps:
                continue
            if seen[day] != 1:
                collector.issue(code, "最近净值", "同日来源记录重复，待核实", day)
                continue
            values = {}
            for provider_field, field in mapping.items():
                try:
                    value = number(raw.get(provider_field))
                    if value is not None and field in ("unit_nav", "accumulated_nav") and Decimal(value) <= 0:
                        raise InvalidOperation
                    values[field] = value
                except InvalidOperation:
                    collector.issue(code, field, "数值解析失败", day)
            old = local.get(day)
            if not any(values.get(field) is not None for field in gaps[day]):
                continue
            currency = metadata.get("currency")
            if old:
                for field, value in values.items():
                    if value is not None and old[field] is not None and Decimal(old[field]) == Decimal(value):
                        values[field] = old[field]
                values = {field: values.get(field) if values.get(field) is not None else old[field]
                          for field in FIELDS.values()}
                payload = json.loads(old["raw_payload"])
                currency = old["currency"] or currency
            else:
                payload = {}
            payload["最近净值复查"] = raw
            statuses = {field: ("已获取" if values.get(field) is not None else
                                "数据源未返回" if field in mapping.values() else "不适用")
                        for field in FIELDS.values()}
            repaired[day] = values
            candidates.append(dict(fund_code=code, nav_date=day, source=source, currency=currency,
                                   **{field: values.get(field) for field in FIELDS.values()},
                                   fetched_at=now(), raw_payload=encode(payload), field_status=encode(statuses)))
        for day, fields in gaps.items():
            for field in fields:
                if repaired.get(day, {}).get(field) is None:
                    collector.issue(code, field, "来源未返回或披露待核实" if response is not None else "查询失败，待重试", day)
    counts = {}
    if candidates:
        with operation(root):
            backup(root, names=(RAW_FILE,))
            with connect(path) as db:
                db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                counts = publish(db, "raw_fund_nav_daily", ("fund_code", "nav_date", "source"), candidates, collector)
    print(f"{today} · 检查 {first} 至 {last} · {len(funds)} 只基金 · {gaps_before} 个候选缺口 · "
          f"请求 {requested} 只 · 写入 {counts} · {len(collector.issues)} 项待核实", flush=True)
    for issue in collector.issues:
        print(encode(issue))
    return bool(collector.issues)
