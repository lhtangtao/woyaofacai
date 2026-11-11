"""Explicit local collection of public domestic fund data into the raw ledger."""
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from portfolio.paths import DOMESTIC_ASSETS_DIR
from .storage import RAW_FILE, backup, connect, now, operation

START = date(2021, 11, 11)
SOURCE = "AKShare/Eastmoney"
HK_SOURCE = "AKShare/Eastmoney Hong Kong"
FIELDS = {
    "单位净值": "unit_nav", "累计净值": "accumulated_nav", "日增长率": "daily_return_rate",
    "每万份收益": "income_per_10k", "7日年化收益率": "yield_7d",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_fund_nav_daily (
    fund_code TEXT NOT NULL, nav_date TEXT NOT NULL, source TEXT NOT NULL,
    unit_nav TEXT, accumulated_nav TEXT, daily_return_rate TEXT,
    income_per_10k TEXT, yield_7d TEXT, currency TEXT,
    fetched_at TEXT NOT NULL, raw_payload TEXT NOT NULL, field_status TEXT NOT NULL,
    PRIMARY KEY(fund_code, nav_date, source)
);
CREATE TABLE IF NOT EXISTS raw_fund_info (
    fund_code TEXT PRIMARY KEY, fund_name TEXT NOT NULL, fund_type TEXT,
    fund_company TEXT, establishment_date TEXT, benchmark TEXT, management_fee TEXT,
    custody_fee TEXT, currency TEXT, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
    raw_payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_fund_corporate_actions (
    fund_code TEXT NOT NULL, event_type TEXT NOT NULL, event_date TEXT NOT NULL,
    event_key TEXT NOT NULL, record_date TEXT, ex_date TEXT, payment_date TEXT,
    conversion_ratio TEXT, dividend_per_share TEXT, source TEXT NOT NULL,
    fetched_at TEXT NOT NULL, raw_payload TEXT NOT NULL,
    PRIMARY KEY(fund_code, event_type, event_date, event_key, source)
);
CREATE TABLE IF NOT EXISTS raw_fund_nav_conflicts (
    table_name TEXT NOT NULL, row_key TEXT NOT NULL, observed_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL, existing_json TEXT NOT NULL, observed_json TEXT NOT NULL,
    PRIMARY KEY(table_name, row_key, observed_hash)
);
"""


def encode(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def fund_list(path):
    funds = {}
    with connect(path, readonly=True) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        queries = [
            "SELECT fund_code,fund_name,NULL,platform FROM raw_fund_opening_positions JOIN raw_asset_accounts USING(account_id)",
            "SELECT fund_code,fund_name,NULL,'ALIPAY' FROM raw_alipay_fund_transactions",
        ]
        if "raw_fund_transactions" in tables:
            queries.append("SELECT fund_code,fund_name,fund_type,source_platform FROM raw_fund_transactions")
        for sql in queries:
            for code, name, kind, platform in db.execute(sql):
                if not code:
                    continue
                platform = {"支付宝": "ALIPAY", "天天基金": "TIANTIAN"}.get(platform, platform)
                row = funds.setdefault(code, dict(code=code, name=name or code, kind=None, platforms=set()))
                row["platforms"].add(platform)
                if kind:
                    row["kind"] = kind
    return [dict(funds[code], platforms=sorted(funds[code]["platforms"])) for code in sorted(funds)]


def text(value):
    value = str(value).strip()
    return None if value.lower() in {"", "nan", "nat", "none", "<na>", "-", "---", "--"} else value


def iso(value):
    value = text(value)
    if value is None:
        return None
    return date.fromisoformat(value[:10]).isoformat()


def number(value):
    value = text(value)
    if value is None:
        return None
    decimal = Decimal(value.removesuffix("%"))
    if not decimal.is_finite():
        raise InvalidOperation
    return format(decimal, "f")


class Collector:
    def __init__(self, ak, end, attempts):
        self.ak, self.end, self.attempts = ak, end, attempts
        self.issues = []
        self.retries = {}

    def issue(self, code, field, reason, day=None, **details):
        self.issues.append(dict(code=code, field=field, date=day, reason=reason, **details))

    def fetch(self, code, field, call, **kwargs):
        for attempt in range(1, self.attempts + 1):
            try:
                frame = call(**kwargs)
                self.retries[(code, field)] = attempt - 1
                return frame
            except Exception as exc:
                # URLs and request headers are unnecessary for the persistent report.
                self.retries[(code, field)] = attempt - 1
                if attempt == self.attempts:
                    network = isinstance(exc, (OSError, TimeoutError)) or any(
                        word in type(exc).__name__ for word in ("Connection", "Timeout", "HTTP", "Request"))
                    self.issue(code, field, "请求失败" if network else "接口或解析失败",
                               error=type(exc).__name__, retries=attempt - 1)
                    return None
                time.sleep(min(4, 2 ** (attempt - 1)))

    def rows(self, code, label, frame, date_key, required, *, allow_empty=False):
        if frame is None:
            return
        if frame.empty and allow_empty:
            return
        absent = set(required) - set(frame.columns)
        if absent:
            self.issue(code, label, "接口字段缺失", missing=sorted(absent))
            return
        if frame.empty:
            self.issue(code, label, "数据源返回空表")
        for row in frame.to_dict("records"):
            try:
                day = iso(row.get(date_key))
                if day is None:
                    raise ValueError
            except ValueError:
                self.issue(code, label, "日期解析失败", raw_date=text(row.get(date_key)))
                continue
            if START.isoformat() <= day <= self.end.isoformat():
                yield day, {k: text(v) for k, v in row.items()}

    def metadata(self, fund, cached):
        code = fund["code"]
        if cached and cached["fund_type"] and cached["establishment_date"]:
            return dict(cached), json.loads(cached["raw_payload"]).get("details", {})
        frame = self.fetch(code, "基金基本信息", self.ak.fund_individual_basic_info_xq, symbol=code)
        details = {}
        if frame is not None:
            if {"item", "value"}.issubset(frame.columns):
                details = {k: text(v) for k, v in zip(frame["item"], frame["value"])}
            if not details:
                self.issue(code, "基金基本信息", "数据源未返回可用信息")
        established = None
        try:
            established = iso(details.get("成立时间") or details.get("成立日期"))
        except ValueError:
            self.issue(code, "establishment_date", "日期解析失败")
        kind = fund["kind"] or details.get("基金类型")
        if not kind:
            self.issue(code, "fund_type", "基金类型待核实")
        if not established:
            self.issue(code, "establishment_date", "成立日期待核实")
        currency = details.get("币种") or details.get("交易币种")
        if not currency and "人民币" in fund["name"]:
            currency = "CNY"
        if currency in {"人民币", "人民币元"}:
            currency = "CNY"
        return dict(fund_code=code, fund_name=fund["name"], fund_type=kind, establishment_date=established,
                    currency=currency, fund_company=details.get("基金公司") or details.get("基金管理人"),
                    benchmark=details.get("业绩比较基准"), management_fee=details.get("管理费率"),
                    custody_fee=details.get("托管费率"), source="AKShare/Eastmoney+Xueqiu", fetched_at=now(),
                    raw_payload=encode(dict(details=details, directory=fund.get("directory")))), details

    def hk_history(self, fund_code, product, action):
        """Use the endpoint behind AKShare's HK adapter, with bounded date windows."""
        from akshare.fund.fund_em import headers
        from akshare.utils import http
        import pandas as pd

        fields = ({"PDATE": "净值日期", "NAV": "单位净值", "ACCNAV": "累计净值", "NAVCHGRT": "日增长率",
                   "CURRENCY": "单位"} if action == "2" else
                  {"REGDATE": "权益登记日", "EXDDATE": "除息日", "ISSDATE": "分红发放日",
                   "BONUS": "每份分红", "CURRENCY": "单位"})
        records = []
        for year in range(START.year, self.end.year + 1):
            response = http.get("https://overseas.1234567.com.cn/overseasapi/OpenApiHander.ashx",
                                headers=headers, params=dict(
                                    api="HKFDApi", m="MethodJZ", hkfcode=product, action=action,
                                    pageindex="0", pagesize="1000",
                                    date1=max(START, date(year, 1, 1)).isoformat(),
                                    date2=min(self.end, date(year, 12, 31)).isoformat()))
            response.raise_for_status()
            data = json.loads(response.text, parse_float=Decimal)
            rows = data["Data"]
            if not isinstance(rows, list) or int(data["TotalCount"]) != len(rows):
                raise ValueError("香港基金响应不完整")
            if any(str(row.get("FCODE", fund_code)) != fund_code for row in rows):
                raise ValueError("香港基金代码不匹配")
            for row in rows:
                records.append({**row, **{label: row.get(key) for key, label in fields.items()}})
        return pd.DataFrame(records)

    def nav(self, info, calendar, details, product=None):
        code, kind = info["fund_code"], info["fund_type"] or ""
        money = "货币" in kind
        foreign = product or "QDII" in kind.upper() or "海外" in kind or "QDII" in info["fund_name"].upper()
        open_fund = self.ak.fund_open_fund_info_em
        if product:
            specs = [("香港互认基金历史", self.hk_history, dict(fund_code=code, product=product, action="2"),
                      ("单位净值", "累计净值", "日增长率"))]
        elif money:
            specs = [("货币基金历史", self.ak.fund_money_fund_info_em, dict(symbol=code),
                      ("每万份收益", "7日年化收益率"))]
        elif re.search(r"ETF(?!联接)", info["fund_name"], re.I) and "联接" not in kind + info["fund_name"]:
            specs = [("场内基金历史", self.ak.fund_etf_fund_info_em,
                      dict(fund=code, start_date=START.strftime("%Y%m%d"), end_date=self.end.strftime("%Y%m%d")),
                      ("单位净值", "累计净值", "日增长率"))]
        else:
            specs = [("单位净值走势", open_fund, dict(symbol=code, indicator="单位净值走势"), ("单位净值", "日增长率")),
                     ("累计净值走势", open_fund, dict(symbol=code, indicator="累计净值走势"), ("累计净值",))]
        by_day, failed = {}, {}
        for label, call, kwargs, columns in specs:
            before = len(self.issues)
            frame = self.fetch(code, label, call, **kwargs)
            missing_columns = set(columns) - set(frame.columns) if frame is not None else set()
            if missing_columns:
                self.issue(code, label, "接口字段缺失", missing=sorted(missing_columns))
            for day, raw in self.rows(code, label, frame, "净值日期", ("净值日期",)):
                payload, values, invalid = by_day.setdefault(day, ({}, {}, set()))
                if label in payload and payload[label] != raw:
                    self.issue(code, label, "同日来源记录冲突", day)
                    for col in columns:
                        values[FIELDS[col]] = None
                        invalid.add(FIELDS[col])
                    continue
                payload[label] = raw
                for col in columns:
                    field = FIELDS[col]
                    try:
                        value = number(raw.get(col))
                        if field in {"unit_nav", "accumulated_nav"} and value is not None and Decimal(value) <= 0:
                            raise InvalidOperation
                        values[field] = value
                    except InvalidOperation:
                        invalid.add(field)
            errors = [e["reason"] for e in self.issues[before:]
                      if e["date"] is None and not e.get("missing")]
            for column in columns:
                reason = errors[0] if errors else "接口字段缺失" if column in missing_columns else None
                failed[FIELDS[column]] = (reason, self.retries.get((code, label), 0))

        first = max(START, date.fromisoformat(info["establishment_date"])) if info["establishment_date"] else START
        terminal = None
        for key in ("清算日期", "终止日期", "基金终止日"):
            try:
                terminal = iso(details.get(key))
            except ValueError:
                self.issue(code, key, "日期解析失败")
            if terminal:
                break
        expected = set()
        end = self.end.isoformat()
        calendar_end = max(calendar, default=date.min)
        for offset in range(max(0, (self.end - first).days + 1)):
            current = first + timedelta(days=offset)
            day = current.isoformat()
            if terminal and day > terminal:
                continue
            if money or current in calendar or current == self.end or day in by_day:
                expected.add(day)
            elif current > calendar_end:
                if current.weekday() < 5:
                    self.issue(code, "日历", "交易日历覆盖不足，净值日期待核实", day)
        for day in sorted(expected | by_day.keys()):
            payload, values, invalid = by_day.get(day, ({}, {}, set()))
            statuses = {}
            for field in FIELDS.values():
                if field not in failed:
                    statuses[field] = "不适用"
                elif field in invalid:
                    statuses[field] = "解析失败或来源冲突"
                elif values.get(field) is None:
                    reason, retries = failed[field]
                    if not reason and not payload and (day == end or foreign or not info["establishment_date"] or
                                                          (money and date.fromisoformat(day) not in calendar)):
                        reason = "披露日期待核实"
                    statuses[field] = reason or "数据源未返回"
                else:
                    statuses[field] = "已获取"
                if statuses[field] not in {"不适用", "已获取"}:
                    self.issue(code, field, statuses[field], day, retries=failed[field][1])
            if payload:
                yield dict(fund_code=code, nav_date=day, source=HK_SOURCE if product else SOURCE, currency=info["currency"],
                           **{field: values.get(field) for field in FIELDS.values()}, fetched_at=now(),
                           raw_payload=encode(payload), field_status=encode(statuses))
        self.expected_count = len(expected)
        self.terminal = terminal

    def events(self, code, product=None):
        for kind, label, date_key, field in (
            ("DIVIDEND", "分红送配详情", "除息日", "每份分红"),
            ("SPLIT", "拆分详情", "拆分折算日", "拆分折算比例"),
        ):
            if product and kind == "SPLIT":
                self.issue(code, label, "香港互认基金接口未提供拆分信息，待核实")
                continue
            if product:
                frame = self.fetch(code, label, self.hk_history, fund_code=code, product=product, action="3")
            else:
                frame = self.fetch(code, label, self.ak.fund_open_fund_info_em, symbol=code, indicator=label)
            if kind == "DIVIDEND" and frame is not None and "每10份分红" in frame.columns:
                field = "每10份分红"
            for day, raw in self.rows(code, label, frame, date_key, (date_key, field), allow_empty=True):
                try:
                    value = raw[field]
                    record, payment = iso(raw.get("权益登记日")), iso(raw.get("分红发放日"))
                    if kind == "DIVIDEND":
                        match = re.fullmatch(r"(?:每(?:10)?份派现金)?(\d+(?:\.\d+)?)(?:元)?", value or "")
                        if not match:
                            raise ValueError
                        ratio = None
                        dividend = format(Decimal(match[1]) / (10 if field == "每10份分红" else 1), "f")
                    else:
                        match = re.fullmatch(r"(\d+(?:\.\d+)?)[：:](\d+(?:\.\d+)?)", value or "")
                        if not match or min(Decimal(match[1]), Decimal(match[2])) <= 0:
                            raise ValueError
                        ratio, dividend = value, None
                except (ValueError, InvalidOperation):
                    self.issue(code, label, "事件字段解析失败", day, retries=self.retries.get((code, label), 0))
                    continue
                yield dict(fund_code=code, event_type=kind, event_date=day, event_key=record or raw.get("拆分类型") or "",
                           record_date=record, ex_date=day if kind=="DIVIDEND" else None, payment_date=payment,
                           conversion_ratio=ratio, dividend_per_share=dividend, source=HK_SOURCE if product else SOURCE,
                           fetched_at=now(), raw_payload=encode(raw))


def publish(db, table, keys, rows, collector):
    counts = Counter()
    for row in rows:
        key = {k: row[k] for k in keys}
        where = " AND ".join(f"{k}=?" for k in keys)
        old = db.execute(f"SELECT * FROM {table} WHERE {where}", tuple(key.values())).fetchone()
        if old is None:
            db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
            counts["inserted"] += 1
            continue
        compare = set(row) - {"fetched_at"}
        if all(old[k] == row[k] for k in compare):
            counts["existing"] += 1
            continue
        # A later fetch may supply fields missing from an earlier, partial response.
        data_fields = compare - {"raw_payload", "field_status"}
        corrections = {k for k in data_fields if old[k] is not None and row[k] is not None and old[k] != row[k]}
        additions = {k for k in data_fields if old[k] is None and row[k] is not None}
        observed = encode({k: row[k] for k in compare})
        db.execute("INSERT OR IGNORE INTO raw_fund_nav_conflicts VALUES (?,?,?,?,?,?)",
                   (table, encode(key), hashlib.sha256(observed.encode()).hexdigest(), row["fetched_at"],
                    encode(dict(old)), observed))
        if additions and not corrections and all(row[k] is not None for k in data_fields if old[k] is not None):
            db.execute(f"UPDATE {table} SET {','.join(f'{k}=?' for k in row)} WHERE {where}",
                       (*row.values(), *key.values()))
            counts["enriched"] += 1
            continue
        collector.issue(row["fund_code"], table, "新旧来源记录差异，保留原记录",
                        row.get("nav_date") or row.get("event_date"), fields=sorted(corrections))
        counts["conflict"] += 1
    return dict(counts)


def run(root=DOMESTIC_ASSETS_DIR, *, code=None, attempts=3):
    import akshare as ak

    root = Path(root)
    path = root / RAW_FILE
    funds = fund_list(path)
    codes = {code} if isinstance(code, str) else set(code or ())
    if codes:
        missing = codes - {r["code"] for r in funds}
        if missing:
            raise ValueError(f"基金代码不在国内历史基金清单中：{', '.join(sorted(missing))}")
        funds = [r for r in funds if r["code"] in codes]
    end = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    collector = Collector(ak, end, attempts)
    report_dir = root / "nav_reports"
    report_dir.mkdir(mode=0o700, exist_ok=True)
    label = funds[0]["code"] if len(funds) == 1 else "selected" if codes else "all"
    report_path = report_dir / f"{end.isoformat()}-{label}-{uuid4().hex[:8]}.json"
    report = dict(start=START.isoformat(), end=end.isoformat(), akshare=ak.__version__, funds=funds,
                  coverage=[], issues=collector.issues,
                  calendar_basis="境内基金使用中国交易日历及实际披露日；货币基金按日检查；境外或规则不明的日期留作待核实",
                  platform_counts=dict(Counter(platform for fund in funds for platform in fund["platforms"])))
    print(f"基金清单 {len(funds)} 只 · 报告 {report_path}", flush=True)
    directory = collector.fetch("*", "基金目录", ak.fund_name_em)
    lookup = {} if directory is None else {r["基金代码"]: r for r in directory.to_dict("records")
                                          if {"基金代码", "基金类型"}.issubset(r)}
    dates = collector.fetch("*", "中国交易日历", ak.tool_trade_date_hist_sina)
    calendar = set() if dates is None or "trade_date" not in dates else {date.fromisoformat(str(d)) for d in dates["trade_date"]}
    if not calendar:
        collector.issue("*", "中国交易日历", "没有可用日历，交易日期待核实")
    if any(fund["code"].startswith("968") for fund in funds):
        hk = collector.fetch("*", "香港互认基金目录", ak.fund_hk_rank_em)
        if hk is not None:
            lookup.update({str(row["基金代码"]): dict(row, 基金类型="香港互认基金")
                           for row in hk.to_dict("records") if {"基金代码", "香港基金代码"}.issubset(row)})
    for fund in funds:
        found = lookup.get(fund["code"])
        if found:
            fund["directory"] = found
            fund["kind"] = found["基金类型"] or fund["kind"]
        print(f"{fund['code']} {fund['name']} {fund['kind'] or '类型待核实'}", flush=True)
    with operation(root):
        backup(root, names=(RAW_FILE,))
        with connect(path) as db:
            db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
            db.commit()
        for fund in funds:
            symbol = fund["code"]
            with connect(path, readonly=True) as db:
                cached = db.execute("SELECT * FROM raw_fund_info WHERE fund_code=?", (symbol,)).fetchone()
            if not re.fullmatch(r"\d{6}", symbol):
                collector.issue(symbol, "fund_code", "不支持的基金代码，需核实标识")
                report["coverage"].append(dict(code=symbol, status="待核实"))
                continue
            info, details = collector.metadata(fund, cached)
            directory_entry = fund.get("directory", {})
            product = text(directory_entry.get("香港基金代码"))
            if product:
                info["fund_type"] = "香港互认基金"
                if directory_entry.get("币种") == "人民币":
                    info["currency"] = "CNY"
            elif symbol.startswith("968"):
                collector.issue(symbol, "fund_code", "香港互认基金缺少来源产品标识")
                report["coverage"].append(dict(code=symbol, status="待核实"))
                continue
            if not info["currency"]:
                collector.issue(symbol, "currency", "接口未提供可核实的净值币种")
            nav = list(collector.nav(info, calendar, details, product))
            events = list(collector.events(symbol, product))
            with connect(path) as db:
                db.execute("BEGIN IMMEDIATE")
                counts = {
                    "info": publish(db, "raw_fund_info", ("fund_code",), [info], collector),
                    "nav": publish(db, "raw_fund_nav_daily", ("fund_code","nav_date","source"), nav, collector),
                    "events": publish(db, "raw_fund_corporate_actions",
                                      ("fund_code","event_type","event_date","event_key","source"), events, collector),
                }
                db.commit()
            with connect(path, readonly=True) as db:
                stored = db.execute("""SELECT count(*),min(nav_date),max(nav_date),count(unit_nav),count(accumulated_nav)
                    FROM raw_fund_nav_daily WHERE fund_code=? AND nav_date BETWEEN ? AND ?""",
                    (symbol, START.isoformat(), end.isoformat())).fetchone()
                stored_events = [dict(r) for r in db.execute("""SELECT event_type,event_date,conversion_ratio,dividend_per_share
                    FROM raw_fund_corporate_actions WHERE fund_code=? AND event_date BETWEEN ? AND ?
                    ORDER BY event_date""", (symbol, START.isoformat(), end.isoformat()))]
            fund_issues = [e for e in collector.issues if e["code"] == symbol]
            missing = Counter(e["field"] for e in fund_issues if e["date"] and e["field"] in FIELDS.values()
                              and not e["reason"].startswith("新旧"))
            coverage = dict(code=symbol, missing=dict(missing), expected=collector.expected_count, fetched=len(nav), terminal=collector.terminal,
                            stored=list(stored), complete=sum(all(v in {"已获取","不适用"}
                            for v in json.loads(row["field_status"]).values()) for row in nav),
                            counts=counts, events=stored_events)
            report["coverage"].append(coverage)
            print(f"{symbol} 日期 {len(nav)}/{collector.expected_count} · 库存 {stored[0]} · 事件 {len(stored_events)}", flush=True)
            report_path.write_text(encode(report), encoding="utf-8")
    summary = Counter((issue["field"],issue["reason"]) for issue in collector.issues)
    names = {fund["code"]: fund["name"] for fund in funds}
    for issue in collector.issues:
        issue.update(fund_name=names.get(issue["code"]), status="待核实")
        issue.setdefault("retries", 0)
    report["status"] = "存在待核对数据" if collector.issues else "完成"
    report["summary"] = [{"field": k[0], "reason": k[1], "count": v} for k,v in sorted(summary.items())]
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"{report['status']} · {len(collector.issues)} 条异常或缺失 · {report_path}", flush=True)
    for row in report["summary"]:
        print(f"{row['field']} · {row['reason']} · {row['count']}")
    return bool(collector.issues)
