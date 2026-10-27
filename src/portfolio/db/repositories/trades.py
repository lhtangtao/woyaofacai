from datetime import datetime, timezone

from portfolio.domain.models import Ledger, QueryScope, TradeFilters, TradePage, TradeRow, TradeStats, from_e8

SORT_FIELDS = {0: "book_time", 1: "broker_code", 2: "category", 3: "symbol", 4: "side",
               5: "quantity_e8", 6: "price_e8", 7: "amount_e8", 12: "kind", 13: "counterparty", 14: "order_id",
               16: "display_name", 17: "person_name", 18: "fees_e8", 19: "link_status"}


def _in_filter(where, values, column, ids):
    if ids is None:
        return
    if not ids:
        where.append("0")
        return
    if len(ids) > 500 or any(type(value) is not int for value in ids):
        raise ValueError("筛选 ID 必须是最多 500 个整数。")
    where.append(f"{column} IN ({','.join('?' for _ in ids)})")
    values.extend(ids)


def has_fee_data(connection):
    names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('person_trade_fees','broker_fee_records')")}
    return len(names) == 2


def _base_query(scope: QueryScope, filters: TradeFilters | None = None, *, fees_available=False):
    values = [scope.as_of.isoformat(), "USD"]
    person = scope.ledger == Ledger.PERSON
    audit = scope.audit or bool(filters and (filters.include_reversed or filters.include_reference))
    alias = "o" if person else "t"
    where = [f"{alias}.business_date<=?", f"{alias}.currency=?"]
    if person:
        query = """SELECT o.id, o.source_record_id, o.book_time, o.business_date, coalesce(o.executed_at_utc,t.executed_at_utc) AS executed_at_utc,
            t.market_trade_date,
            a.broker_code,a.id AS account_id,a.display_name,i.category,i.symbol,i.market,i.currency,
            i.id AS instrument_id,i.name AS instrument_name,
            NULL AS fees_e8,t.fees_e8 AS broker_fees_e8,t.broker_execution_id,o.broker_trade_id,
            o.side,o.quantity_e8,o.price_e8,o.amount_e8,o.amount_basis,
            o.operation_kind AS kind,coalesce(cp.name,o.legacy_counterparty,'') AS counterparty,
            coalesce(o.legacy_order_id,'') AS order_id,o.link_status AS link_status,
            coalesce(t.record_state,'UNLINKED') AS record_state,
            coalesce(t.accounting_role,'PERSON_ONLY') AS accounting_role,
            coalesce(t.record_granularity,'PERSON_OPERATION') AS record_granularity,
            o.sequence_no,p.id AS person_id,p.name AS person_name,p.enabled AS person_enabled,
            coalesce(o.legacy_direction,'') AS legacy_direction,
            r.source_sheet,r.source_row,r.parse_status,b.source_path,b.source_kind,b.status AS batch_status
            FROM person_operations o JOIN persons p ON p.id=o.person_id
            JOIN broker_accounts a ON a.id=o.account_id JOIN instruments i ON i.id=o.instrument_id
            LEFT JOIN persons cp ON cp.id=o.counterparty_person_id
            LEFT JOIN broker_trades t ON t.id=o.broker_trade_id
            JOIN raw_records r ON r.id=o.source_record_id JOIN import_batches b ON b.id=r.import_batch_id"""
        _in_filter(where, values, "o.person_id", scope.person_ids)
        if not scope.include_disabled:
            where.append("p.enabled=1")
        if not audit:
            where.extend(["(t.id IS NULL OR (t.record_state='ACTIVE' AND t.accounting_role='ACCOUNT_LEDGER'))",
                          "a.enabled=1"])
    else:
        query = """SELECT t.id,t.source_record_id,t.book_time,t.business_date,t.executed_at_utc,
            t.market_trade_date,
            a.broker_code,a.id AS account_id,a.display_name,i.category,i.symbol,i.market,i.currency,
            i.id AS instrument_id,i.name AS instrument_name,
            t.fees_e8,t.fees_e8 AS broker_fees_e8,t.broker_execution_id,t.id AS broker_trade_id,
            t.side,t.quantity_e8,t.price_e8,t.calculation_amount_e8 AS amount_e8,t.amount_basis,
            'MARKET_TRADE' AS kind,'' AS counterparty,coalesce(t.broker_order_id,'') AS order_id,
            'NOT_APPLICABLE' AS link_status,t.record_state,t.accounting_role,t.record_granularity,
            t.id AS sequence_no,NULL AS person_id,'' AS person_name,NULL AS person_enabled,
            '' AS legacy_direction,r.source_sheet,r.source_row,r.parse_status,b.source_path,b.source_kind,b.status AS batch_status
            FROM broker_trades t JOIN broker_accounts a ON a.id=t.account_id
            JOIN instruments i ON i.id=t.instrument_id
            JOIN raw_records r ON r.id=t.source_record_id JOIN import_batches b ON b.id=r.import_batch_id"""
        if not audit:
            where.extend(["t.record_state='ACTIVE'", "t.accounting_role='ACCOUNT_LEDGER'", "a.enabled=1"])
    if fees_available:
        from portfolio.sync.trade_fees import TRADE_FIELDS
        signature = 'json_array('+','.join('t.'+field for field in TRADE_FIELDS)+')'
        known = f"bf.fingerprint={signature} AND bf.fees_e8 IS t.fees_e8 AND bf.status='KNOWN'"
        broker_fee = f"CASE WHEN {known} THEN t.fees_e8 ELSE NULL END"
        query += " LEFT JOIN broker_fee_records bf ON bf.broker_trade_id=t.id"
        if person:
            matches = " AND ".join(f"f.{field} IS o.{field}" for field in
                ('person_id','account_id','instrument_id','broker_trade_id','operation_kind','side','business_date','book_time','quantity_e8','amount_e8','currency'))
            query += f" LEFT JOIN person_trade_fees f ON f.operation_id=o.id AND {matches} AND f.broker_fee_e8 IS t.fees_e8"
            query = query.replace('NULL AS fees_e8,t.fees_e8 AS broker_fees_e8',
                f"CASE WHEN o.operation_kind='OWNER_TRANSFER' THEN 0 WHEN {known} THEN f.fees_e8 ELSE NULL END AS fees_e8,{broker_fee} AS broker_fees_e8")
            extra = "CASE WHEN o.operation_kind='OWNER_TRANSFER' THEN 'KNOWN' ELSE coalesce(f.status,'NOT_COLLECTED') END AS fee_status,coalesce(f.source,'') AS fee_source"
        else:
            query = query.replace('t.fees_e8,t.fees_e8 AS broker_fees_e8',f'{broker_fee} AS fees_e8,{broker_fee} AS broker_fees_e8')
            extra = "coalesce(bf.status,'NOT_COLLECTED') AS fee_status,coalesce(bf.source,'') AS fee_source"
        # Add metadata to the projection, before the FROM clause.
        marker = ' FROM person_operations o' if person else ' FROM broker_trades t'
        query = query.replace(marker, ','+extra+marker, 1)
    elif person:
        query = query.replace('NULL AS fees_e8', "CASE WHEN o.operation_kind='OWNER_TRANSFER' THEN 0 ELSE NULL END AS fees_e8")
    _in_filter(where, values, f"{alias}.account_id", scope.account_ids)
    _in_filter(where, values, f"{alias}.instrument_id", scope.instrument_ids)
    if scope.category is not None:
        where.append("i.category=?")
        values.append(scope.category)
    if scope.start_date:
        where.append(f"{alias}.business_date>=?")
        values.append(scope.start_date.isoformat())
    if scope.search:
        # LIKE 的通配符按字面匹配，不能扩大用户搜索范围。
        search = scope.search.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        fields = ["i.symbol", "a.broker_code", "b.source_path", f"{alias}.side"]
        fields += ["p.name", "o.legacy_order_id"] if person else ["t.broker_order_id"]
        where.append("(" + " OR ".join(f"{field} LIKE ? ESCAPE '\\'" for field in fields) + ")")
        values.extend([f"%{search}%"] * len(fields))
    if filters is not None:
        if filters.side:
            where.append(f"{alias}.side=?")
            values.append(filters.side)
        if filters.link_status:
            where.append("o.link_status=?" if person else "'NOT_APPLICABLE'=?")
            values.append(filters.link_status)
        if filters.source_file:
            source = filters.source_file.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            where.append("coalesce(b.source_path,'') LIKE ? ESCAPE '\\'")
            values.append(f"%{source}%")
        if filters.include_reversed is False:
            where.append("(t.id IS NULL OR t.record_state='ACTIVE')" if person else "t.record_state='ACTIVE'")
        if filters.include_reference is False:
            where.append("(t.id IS NULL OR t.accounting_role='ACCOUNT_LEDGER')" if person else "t.accounting_role='ACCOUNT_LEDGER'")
    return query + " WHERE " + " AND ".join(where), values


def query_trades(connection, scope: QueryScope, *, page=0, size=200, sort_column=0, descending=True, filters=None):
    if type(page) is not int or page < 0 or type(size) is not int or not 1 <= size <= 1000:
        raise ValueError("分页参数无效，每页允许 1–1000 条。")
    if sort_column not in SORT_FIELDS:
        raise ValueError("此列尚无可排序计算结果，请选择已实现的数据列。")
    query, values = _base_query(scope, filters, fees_available=has_fee_data(connection))
    counted = connection.execute(f"""SELECT count(*),
        coalesce(sum(CASE WHEN side='BUY' THEN 1 ELSE 0 END),0),
        coalesce(sum(CASE WHEN side='SELL' THEN 1 ELSE 0 END),0),
        coalesce(sum(CASE WHEN link_status='REVIEW_REQUIRED' OR parse_status IN ('PENDING','REVIEW_REQUIRED')
            OR batch_status<>'SUCCESS' THEN 1 ELSE 0 END),0) FROM ({query})""", values).fetchone()
    total = counted[0]
    sort = SORT_FIELDS[sort_column]
    order = "DESC" if descending else "ASC"
    rows = connection.execute(
        f"SELECT * FROM ({query}) ORDER BY {sort} {order},sequence_no {order},id {order} LIMIT ? OFFSET ?",
        [*values, size, page * size],
    ).fetchall()
    results = []
    directions = {"BUY": "买入", "SELL": "卖出", "TRANSFER_IN": "转入", "TRANSFER_OUT": "转出"}
    kinds = {"MARKET_TRADE": "市场交易", "OWNER_TRANSFER": "人员划转",
             "EXTERNAL_TRANSFER": "外部划转", "BOOK_SETTLEMENT": "账务结算"}
    for row in rows:
        source = f"{row['source_path'] or row['source_kind']} / {row['source_sheet']} : {row['source_row']}"
        display = (row["book_time"], row["broker_code"], row["category"], row["symbol"],
                   row["legacy_direction"] or directions[row["side"]], from_e8(row["quantity_e8"]),
                   from_e8(row["price_e8"]), from_e8(row["amount_e8"]), None, None, None, None,
                   kinds[row["kind"]], row["counterparty"], row["order_id"], source)
        results.append(TradeRow(row["id"], row["source_record_id"], display, dict(row)))
    return TradePage(tuple(results), total, page, size, scope, datetime.now(timezone.utc).isoformat(),
                     stats=TradeStats(*counted), filters=filters)
