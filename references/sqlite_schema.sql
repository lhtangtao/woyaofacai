-- money_2 SQLite v2: 输入数据、人员账本及原始来源。
-- 价格使用独立简化结构；所有数值 *_e8 为 8 位定点整数。
PRAGMA foreign_keys = ON;

CREATE TABLE import_batches (
    id INTEGER PRIMARY KEY,
    batch_key TEXT NOT NULL UNIQUE,       -- 来源范围+内容摘要+解析版本形成的批次标识
    source_kind TEXT NOT NULL,            -- EXCEL / CSV / API / MANUAL
    source_scope TEXT NOT NULL,           -- 如交易流水/长桥账户A，不包含密钥
    source_path TEXT,
    snapshot_path TEXT,
    content_sha256 TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    request_start TEXT,
    request_end TEXT,
    resume_cursor TEXT,                   -- 同步游标；仅成功批次可用于推进进度
    status TEXT NOT NULL CHECK (status IN ('PENDING','SUCCESS','FAILED')),
    imported_at_utc TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0 CHECK (record_count >= 0),
    input_role TEXT NOT NULL DEFAULT 'ARCHIVE',
    error_message TEXT
);

CREATE TABLE raw_records (
    id INTEGER PRIMARY KEY,
    import_batch_id INTEGER NOT NULL REFERENCES import_batches(id),
    source_locator TEXT NOT NULL,         -- Sheet+行号，或 API 记录位置；区分相同内容的两行
    source_entity_id TEXT,                -- 来源明确提供的活动号/成交号等
    original_time_text TEXT,
    payload_json TEXT NOT NULL,           -- 原始字段；高精度数字可按原字符串保留
    payload_sha256 TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (parse_status IN ('PENDING','ACCEPTED','REVIEW_REQUIRED','IGNORED')),
    parse_message TEXT,
    source_sheet TEXT NOT NULL DEFAULT '',
    source_row INTEGER NOT NULL DEFAULT 0,
    record_kind TEXT NOT NULL DEFAULT 'DATA',
    UNIQUE (import_batch_id, source_locator)
);

CREATE TABLE persons (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    notes TEXT
);

CREATE TABLE broker_accounts (
    id INTEGER PRIMARY KEY,
    broker_code TEXT NOT NULL,
    account_key TEXT NOT NULL,            -- 本地稳定账户标识；缺真实账号时可明确使用 LEGACY_DEFAULT
    display_name TEXT NOT NULL,
    base_currency TEXT NOT NULL DEFAULT 'USD',
    book_timezone TEXT NOT NULL,         -- 账务归属规则使用的时区
    day_cutoff TEXT NOT NULL,             -- HH:MM；沿用当前渠道规则
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    source_record_id INTEGER REFERENCES raw_records(id),
    UNIQUE (broker_code, account_key)
);

CREATE TABLE instruments (
    id INTEGER PRIMARY KEY,
    market TEXT NOT NULL,                -- 如 US；不以代码前缀猜测市场
    symbol TEXT NOT NULL,
    name TEXT,
    currency TEXT NOT NULL,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('STOCK','ETF','FUND','UNKNOWN')),
    category TEXT NOT NULL CHECK (category IN ('大盘','杠杆','正股','待确认')),
    source_record_id INTEGER REFERENCES raw_records(id),
    UNIQUE (market, symbol)
);

CREATE TABLE instrument_aliases (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    market TEXT NOT NULL,
    source_symbol TEXT NOT NULL,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    source_record_id INTEGER REFERENCES raw_records(id),
    UNIQUE (provider, market, source_symbol)
);

CREATE TABLE broker_trades (
    id INTEGER PRIMARY KEY,
    trade_key TEXT NOT NULL UNIQUE,       -- 由渠道适配器生成稳定标识，不能仅用日期/数量去重
    account_id INTEGER NOT NULL REFERENCES broker_accounts(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    broker_order_id TEXT,
    broker_execution_id TEXT,
    record_granularity TEXT NOT NULL CHECK (record_granularity IN ('FILL','ORDER_TOTAL','SOURCE_RECORD')),
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    executed_at_utc TEXT,
    market_trade_date TEXT,               -- 券商给出的原交易日
    business_date TEXT NOT NULL,          -- 沿用现有账务归属日
    book_time TEXT NOT NULL,              -- 现有账本使用的日期时间，保留重放顺序
    quantity_e8 INTEGER NOT NULL CHECK (quantity_e8 > 0),
    price_e8 INTEGER CHECK (price_e8 >= 0),
    gross_amount_e8 INTEGER CHECK (gross_amount_e8 >= 0),
    fees_e8 INTEGER CHECK (fees_e8 >= 0),
    net_amount_e8 INTEGER,                -- 真实现金收付；收入为正、支出为负
    calculation_amount_e8 INTEGER NOT NULL CHECK (calculation_amount_e8 >= 0),
    amount_basis TEXT NOT NULL CHECK (amount_basis IN ('GROSS','NET','LEGACY')),
    currency TEXT NOT NULL,
    record_state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (record_state IN ('ACTIVE','REVERSED')),
    accounting_role TEXT NOT NULL DEFAULT 'ACCOUNT_LEDGER' CHECK (accounting_role IN ('REFERENCE','ACCOUNT_LEDGER')),
    assignment_text TEXT,
    source_record_id INTEGER NOT NULL REFERENCES raw_records(id)
);

CREATE TABLE person_operations (
    id INTEGER PRIMARY KEY,
    operation_key TEXT NOT NULL UNIQUE,
    person_id INTEGER NOT NULL REFERENCES persons(id),
    account_id INTEGER NOT NULL REFERENCES broker_accounts(id),
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    broker_trade_id INTEGER REFERENCES broker_trades(id),
    operation_kind TEXT NOT NULL
        CHECK (operation_kind IN ('MARKET_TRADE','OWNER_TRANSFER','EXTERNAL_TRANSFER','BOOK_SETTLEMENT')),
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL','TRANSFER_IN','TRANSFER_OUT')),
    business_date TEXT NOT NULL,
    book_time TEXT NOT NULL,
    executed_at_utc TEXT,
    sequence_no INTEGER NOT NULL,         -- 同一时间多条记录的原顺序
    quantity_e8 INTEGER NOT NULL CHECK (quantity_e8 > 0),
    price_e8 INTEGER CHECK (price_e8 >= 0),
    amount_e8 INTEGER CHECK (amount_e8 >= 0),
    amount_basis TEXT NOT NULL CHECK (amount_basis IN ('GROSS','NET','TRANSFER_VALUE','LEGACY')),
    currency TEXT NOT NULL,
    counterparty_person_id INTEGER REFERENCES persons(id),
    transfer_group_key TEXT,              -- 内部划转的转出、转入两条记录使用同一标识
    link_status TEXT NOT NULL
        CHECK (link_status IN ('LINKED','LEGACY_UNLINKED','REVIEW_REQUIRED')),
    source_record_id INTEGER NOT NULL REFERENCES raw_records(id),
    legacy_order_id TEXT,
    legacy_direction TEXT,
    legacy_transaction_type TEXT,
    legacy_source TEXT,
    legacy_counterparty TEXT,
    CHECK (counterparty_person_id IS NULL OR counterparty_person_id <> person_id),
    CHECK (operation_kind <> 'OWNER_TRANSFER' OR
        (broker_trade_id IS NULL AND counterparty_person_id IS NOT NULL
         AND side IN ('TRANSFER_IN','TRANSFER_OUT'))),
    CHECK (operation_kind <> 'MARKET_TRADE' OR side IN ('BUY','SELL'))
);

CREATE TABLE cash_events (
    id INTEGER PRIMARY KEY,
    cash_event_key TEXT NOT NULL UNIQUE,
    account_id INTEGER NOT NULL REFERENCES broker_accounts(id),
    person_id INTEGER REFERENCES persons(id), -- NULL 表示仅确认账户归属，不分给某个人
    instrument_id INTEGER REFERENCES instruments(id),
    broker_trade_id INTEGER REFERENCES broker_trades(id),
    event_type TEXT NOT NULL CHECK (event_type IN
        ('DEPOSIT','WITHDRAWAL','DIVIDEND','INTEREST','FEE','TAX','TRANSFER','OTHER')),
    business_date TEXT NOT NULL,
    occurred_at_utc TEXT,
    amount_e8 INTEGER NOT NULL,           -- 收入为正、支出为负
    currency TEXT NOT NULL,
    accounting_role TEXT NOT NULL CHECK (accounting_role IN
        ('CAPITAL','INCOME','EXPENSE','INFORMATIONAL','REVIEW_REQUIRED')),
    description TEXT,
    source_event_type TEXT,
    record_state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (record_state IN ('ACTIVE','REVERSED')),
    source_record_id INTEGER NOT NULL REFERENCES raw_records(id)
);

CREATE TABLE corporate_actions (
    id INTEGER PRIMARY KEY,
    action_key TEXT NOT NULL UNIQUE,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    effective_date TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type IN ('SPLIT','REVERSE_SPLIT')),
    broker_scope TEXT NOT NULL DEFAULT 'ALL', -- 保留现有适用渠道
    ratio_numerator INTEGER NOT NULL CHECK (ratio_numerator > 0),
    ratio_denominator INTEGER NOT NULL CHECK (ratio_denominator > 0),
    record_state TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (record_state IN ('ACTIVE','REVERSED')),
    source_record_id INTEGER NOT NULL REFERENCES raw_records(id),
    UNIQUE (instrument_id, effective_date, action_type, broker_scope)
);

CREATE TABLE historical_baselines (
    id INTEGER PRIMARY KEY,
    baseline_key TEXT NOT NULL UNIQUE,
    baseline_date TEXT NOT NULL,
    baseline_role TEXT NOT NULL CHECK (baseline_role IN ('OPENING','REFERENCE','ARCHIVED_PERIOD')),
    baseline_kind TEXT NOT NULL,
    period_key TEXT,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('PERSON_ACCOUNT','PERSON','ACCOUNT','TOTAL')),
    person_id INTEGER REFERENCES persons(id),
    account_id INTEGER REFERENCES broker_accounts(id),
    instrument_id INTEGER REFERENCES instruments(id), -- NULL 表示原来源本身仅有汇总数
    quantity_e8 INTEGER,
    market_value_e8 INTEGER,
    net_cost_e8 INTEGER,
    cumulative_buy_e8 INTEGER,
    cumulative_sell_e8 INTEGER,
    realized_pnl_e8 INTEGER,
    period_pnl_e8 INTEGER,
    period_buy_quantity_e8 INTEGER,
    period_sell_quantity_e8 INTEGER,
    period_buy_amount_e8 INTEGER,
    period_sell_amount_e8 INTEGER,
    opening_market_value_e8 INTEGER,
    period_net_investment_e8 INTEGER,
    cumulative_capital_e8 INTEGER,
    cost_price_e8 INTEGER,
    close_price_e8 INTEGER,
    currency TEXT NOT NULL,
    source_record_id INTEGER NOT NULL REFERENCES raw_records(id),
    CHECK ((scope_type = 'PERSON_ACCOUNT' AND person_id IS NOT NULL AND account_id IS NOT NULL)
        OR (scope_type = 'PERSON' AND person_id IS NOT NULL AND account_id IS NULL)
        OR (scope_type = 'ACCOUNT' AND person_id IS NULL AND account_id IS NOT NULL)
        OR (scope_type = 'TOTAL' AND person_id IS NULL AND account_id IS NULL))
);

CREATE TABLE asset_adjustments (
    id INTEGER PRIMARY KEY,
    adjustment_key TEXT NOT NULL UNIQUE,
    account_id INTEGER NOT NULL REFERENCES broker_accounts(id),
    person_id INTEGER REFERENCES persons(id),
    instrument_id INTEGER REFERENCES instruments(id),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    target_metric TEXT NOT NULL,          -- 明确调整现金、估值等哪项指标，由现有规则映射
    adjustment_mode TEXT NOT NULL CHECK (adjustment_mode IN ('BALANCE_ANCHOR','DELTA')),
    amount_e8 INTEGER NOT NULL,
    currency TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','VOID','REVIEW_REQUIRED')),
    source_record_id INTEGER NOT NULL REFERENCES raw_records(id),
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE TABLE market_prices (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    price_type TEXT NOT NULL CHECK (price_type IN ('LATEST','DAILY_CLOSE','PREV_CLOSE','MONTH_CLOSE')),
    trade_date TEXT,
    period_key TEXT NOT NULL,
    quoted_at_utc TEXT,
    price_e8 INTEGER NOT NULL CHECK (typeof(price_e8) = 'integer' AND price_e8 > 0),
    updated_at_utc TEXT NOT NULL,
    price_status TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN',
    source_record_id INTEGER NOT NULL REFERENCES raw_records(id),
    UNIQUE (symbol, price_type, period_key)
);

CREATE TABLE fx_rates (
    id INTEGER PRIMARY KEY,
    rate_date TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    rate_e8 INTEGER NOT NULL CHECK (rate_e8 > 0), -- 1 单位 base = rate 单位 quote
    provider TEXT NOT NULL,
    source_record_id INTEGER NOT NULL REFERENCES raw_records(id),
    CHECK (base_currency <> quote_currency),
    UNIQUE (rate_date, base_currency, quote_currency, provider)
);

CREATE INDEX ix_raw_entity ON raw_records (source_entity_id);
CREATE INDEX ix_trades_account_date ON broker_trades (account_id, business_date, id);
CREATE INDEX ix_trades_security_date ON broker_trades (instrument_id, business_date, id);
CREATE INDEX ix_trades_order ON broker_trades (account_id, broker_order_id);
CREATE INDEX ix_person_ledger ON person_operations
    (person_id, account_id, instrument_id, book_time, sequence_no);
CREATE INDEX ix_operations_trade ON person_operations (broker_trade_id);
CREATE INDEX ix_operations_transfer ON person_operations (transfer_group_key);
CREATE INDEX ix_cash_account_date ON cash_events (account_id, business_date);
CREATE INDEX ix_prices_lookup ON market_prices (symbol, price_type, trade_date);
CREATE INDEX ix_actions_security_date ON corporate_actions (instrument_id, effective_date);
CREATE INDEX ix_baselines_scope ON historical_baselines (person_id, account_id, baseline_date);
CREATE INDEX ix_raw_location ON raw_records (import_batch_id, source_sheet, source_row);

CREATE VIEW 检查_人员操作 AS
SELECT o.id AS 编号, p.name AS 姓名, a.broker_code AS 渠道, i.symbol AS Ticker,
       o.book_time AS 交易时间, o.legacy_direction AS 方向,
       o.quantity_e8 / 100000000.0 AS 成交数量, o.price_e8 / 100000000.0 AS 成交均价,
       o.amount_e8 / 100000000.0 AS 成交金额, o.currency AS 币种,
       o.legacy_transaction_type AS 交易类型, cp.name AS 划转对象,
       o.legacy_order_id AS 订单ID, o.link_status AS 关联状态,
       b.source_path AS 来源文件, r.source_sheet AS 来源表, r.source_row AS 来源行
FROM person_operations o JOIN persons p ON p.id = o.person_id
JOIN broker_accounts a ON a.id = o.account_id JOIN instruments i ON i.id = o.instrument_id
LEFT JOIN persons cp ON cp.id = o.counterparty_person_id
JOIN raw_records r ON r.id = o.source_record_id JOIN import_batches b ON b.id = r.import_batch_id;

CREATE VIEW 检查_券商成交 AS
SELECT t.id AS 编号, a.broker_code AS 渠道, i.symbol AS Ticker, t.book_time AS 交易时间,
       CASE t.side WHEN 'BUY' THEN '买入' ELSE '卖出' END AS 方向, t.quantity_e8 / 100000000.0 AS 成交数量,
       t.price_e8 / 100000000.0 AS 成交均价,
       t.calculation_amount_e8 / 100000000.0 AS 原账本成交金额,
       t.amount_basis AS 金额口径, t.assignment_text AS 谁, t.broker_order_id AS 订单ID,
       t.accounting_role AS 记账范围, b.source_path AS 来源文件, r.source_row AS 来源行
FROM broker_trades t JOIN broker_accounts a ON a.id = t.account_id
JOIN instruments i ON i.id = t.instrument_id
JOIN raw_records r ON r.id = t.source_record_id JOIN import_batches b ON b.id = r.import_batch_id;

CREATE VIEW 检查_证券行情 AS
SELECT m.id AS 编号, m.symbol AS 证券代码,
       CASE m.price_type WHEN 'LATEST' THEN '最新价' WHEN 'DAILY_CLOSE' THEN '历史日价'
       WHEN 'PREV_CLOSE' THEN '上一日价格快照' WHEN 'MONTH_CLOSE' THEN '月末价格' END AS 价格类型,
       m.trade_date AS 交易日期, m.period_key AS 期间, m.quoted_at_utc AS 报价时间UTC,
       m.price_e8 / 100000000.0 AS 价格,
       CASE m.price_status WHEN 'LEGACY_UNKNOWN' THEN '原缓存口径' ELSE m.price_status END AS 口径状态,
       json_extract(r.payload_json, '$.fields."数据来源"') AS 原缓存来源,
       b.source_path AS 来源文件, r.source_sheet AS 来源表, r.source_row AS 来源行
FROM market_prices m JOIN raw_records r ON r.id = m.source_record_id
JOIN import_batches b ON b.id = r.import_batch_id;

CREATE VIEW 检查_资金流水 AS
SELECT e.id AS 编号, a.broker_code AS 渠道, p.name AS 已确认归属人员,
       e.business_date AS 日期, e.event_type AS 分类, e.source_event_type AS 来源类型,
       e.amount_e8 / 100000000.0 AS 收付金额, e.currency AS 币种,
       e.accounting_role AS 记账范围, e.description AS 说明,
       b.source_path AS 来源文件, r.source_row AS 来源行
FROM cash_events e JOIN broker_accounts a ON a.id = e.account_id
LEFT JOIN persons p ON p.id = e.person_id
JOIN raw_records r ON r.id = e.source_record_id JOIN import_batches b ON b.id = r.import_batch_id;

CREATE VIEW 检查_历史基准 AS
SELECT h.id AS 编号, h.baseline_date AS 日期, h.baseline_kind AS 基准类型,
       h.baseline_role AS 用途, p.name AS 姓名, a.broker_code AS 渠道, i.symbol AS 证券,
       h.quantity_e8 / 100000000.0 AS 数量, h.market_value_e8 / 100000000.0 AS 市值,
       h.net_cost_e8 / 100000000.0 AS 累计净成本, h.period_pnl_e8 / 100000000.0 AS 本月收益,
       h.currency AS 币种, b.source_path AS 来源文件, r.source_sheet AS 来源表,
       r.source_row AS 来源行
FROM historical_baselines h LEFT JOIN persons p ON p.id = h.person_id
LEFT JOIN broker_accounts a ON a.id = h.account_id LEFT JOIN instruments i ON i.id = h.instrument_id
JOIN raw_records r ON r.id = h.source_record_id JOIN import_batches b ON b.id = r.import_batch_id;

CREATE VIEW 检查_资产调整 AS
SELECT x.id AS 编号, a.broker_code AS 渠道, x.effective_from AS 生效日期,
       x.adjustment_mode AS 调整方式, x.target_metric AS 调整指标,
       x.amount_e8 / 100000000.0 AS 金额, x.currency AS 币种, x.reason AS 说明,
       b.source_path AS 来源文件, r.source_row AS 来源行
FROM asset_adjustments x JOIN broker_accounts a ON a.id = x.account_id
JOIN raw_records r ON r.id = x.source_record_id JOIN import_batches b ON b.id = r.import_batch_id;

CREATE VIEW 检查_原始记录 AS
SELECT r.id AS 编号, b.source_path AS 来源文件, b.input_role AS 来源用途,
       r.source_sheet AS 来源表, r.source_row AS 来源行, r.record_kind AS 行类型,
       r.parse_status AS 处理状态, r.parse_message AS 处理说明, r.payload_json AS 原始内容
FROM raw_records r JOIN import_batches b ON b.id = r.import_batch_id;
