# 数据库字段映射与账本边界

由 references/sqlite_schema.sql 在内存中解析；真实源库不执行 DDL。

## 运行时数据流

Money3本地券商适配 → 本目录交易流水.xlsx → 数据库/money.sqlite3；展示层只读本地数据库。
Excel 是 money3 的迁移参考，不会在 money3 启动时充当运行时账本。

人员页读取 person_operations，券商页读取 broker_trades，两个账本不合并求和。
ACTIVE/ACCOUNT_LEDGER 为有效券商范围；撤销/参考记录只在显式审计模式展示。
人员默认只显示启用人员，可启用‘含停用人员’；历史对账必须包含停用人员。

## 16 列交易页映射

| 显示列 | 人员账本 | 券商账本 |
|---|---|---|
| 交易时间 | o.book_time | t.book_time |
| 渠道 | a.broker_code | a.broker_code |
| 分类 | i.category 原值 | i.category 原值 |
| Ticker | i.symbol + i.market | i.symbol + i.market |
| 方向 | legacy_direction / side | side |
| 成交数量 | quantity_e8 | quantity_e8 |
| 成交均价($) | price_e8；NULL 保留缺失 | price_e8；NULL 保留缺失 |
| 成交金额($) | amount_e8 | calculation_amount_e8 |
| 微操标记 | M5 待实现，不伪造 | M5 待实现，不伪造 |
| 后持仓数量 / 后累计净投入 / 后均价 | M2 待回放 | M2 待回放 |
| 交易类型 | operation_kind | MARKET_TRADE |
| 划转对象 | counterparty_person_id / legacy_counterparty | 不适用 |
| 订单ID | legacy_order_id 文本 | broker_order_id 文本 |
| 数据来源 | raw_records → import_batches | raw_records → import_batches |

## asset_adjustments（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| adjustment_key | TEXT | 1 | 0 |
| account_id | INTEGER | 1 | 0 |
| person_id | INTEGER | 0 | 0 |
| instrument_id | INTEGER | 0 | 0 |
| effective_from | TEXT | 1 | 0 |
| effective_to | TEXT | 0 | 0 |
| target_metric | TEXT | 1 | 0 |
| adjustment_mode | TEXT | 1 | 0 |
| amount_e8 | INTEGER | 1 | 0 |
| currency | TEXT | 1 | 0 |
| reason | TEXT | 1 | 0 |
| status | TEXT | 1 | 0 |
| source_record_id | INTEGER | 1 | 0 |

## broker_accounts（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| broker_code | TEXT | 1 | 0 |
| account_key | TEXT | 1 | 0 |
| display_name | TEXT | 1 | 0 |
| base_currency | TEXT | 1 | 0 |
| book_timezone | TEXT | 1 | 0 |
| day_cutoff | TEXT | 1 | 0 |
| enabled | INTEGER | 1 | 0 |
| source_record_id | INTEGER | 0 | 0 |

## broker_trades（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| trade_key | TEXT | 1 | 0 |
| account_id | INTEGER | 1 | 0 |
| instrument_id | INTEGER | 1 | 0 |
| broker_order_id | TEXT | 0 | 0 |
| broker_execution_id | TEXT | 0 | 0 |
| record_granularity | TEXT | 1 | 0 |
| side | TEXT | 1 | 0 |
| executed_at_utc | TEXT | 0 | 0 |
| market_trade_date | TEXT | 0 | 0 |
| business_date | TEXT | 1 | 0 |
| book_time | TEXT | 1 | 0 |
| quantity_e8 | INTEGER | 1 | 0 |
| price_e8 | INTEGER | 0 | 0 |
| gross_amount_e8 | INTEGER | 0 | 0 |
| fees_e8 | INTEGER | 0 | 0 |
| net_amount_e8 | INTEGER | 0 | 0 |
| calculation_amount_e8 | INTEGER | 1 | 0 |
| amount_basis | TEXT | 1 | 0 |
| currency | TEXT | 1 | 0 |
| record_state | TEXT | 1 | 0 |
| accounting_role | TEXT | 1 | 0 |
| assignment_text | TEXT | 0 | 0 |
| source_record_id | INTEGER | 1 | 0 |

## cash_events（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| cash_event_key | TEXT | 1 | 0 |
| account_id | INTEGER | 1 | 0 |
| person_id | INTEGER | 0 | 0 |
| instrument_id | INTEGER | 0 | 0 |
| broker_trade_id | INTEGER | 0 | 0 |
| event_type | TEXT | 1 | 0 |
| business_date | TEXT | 1 | 0 |
| occurred_at_utc | TEXT | 0 | 0 |
| amount_e8 | INTEGER | 1 | 0 |
| currency | TEXT | 1 | 0 |
| accounting_role | TEXT | 1 | 0 |
| description | TEXT | 0 | 0 |
| source_event_type | TEXT | 0 | 0 |
| record_state | TEXT | 1 | 0 |
| source_record_id | INTEGER | 1 | 0 |

## corporate_actions（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| action_key | TEXT | 1 | 0 |
| instrument_id | INTEGER | 1 | 0 |
| effective_date | TEXT | 1 | 0 |
| action_type | TEXT | 1 | 0 |
| broker_scope | TEXT | 1 | 0 |
| ratio_numerator | INTEGER | 1 | 0 |
| ratio_denominator | INTEGER | 1 | 0 |
| record_state | TEXT | 1 | 0 |
| source_record_id | INTEGER | 1 | 0 |

## fx_rates（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| rate_date | TEXT | 1 | 0 |
| base_currency | TEXT | 1 | 0 |
| quote_currency | TEXT | 1 | 0 |
| rate_e8 | INTEGER | 1 | 0 |
| provider | TEXT | 1 | 0 |
| source_record_id | INTEGER | 1 | 0 |

## historical_baselines（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| baseline_key | TEXT | 1 | 0 |
| baseline_date | TEXT | 1 | 0 |
| baseline_role | TEXT | 1 | 0 |
| baseline_kind | TEXT | 1 | 0 |
| period_key | TEXT | 0 | 0 |
| scope_type | TEXT | 1 | 0 |
| person_id | INTEGER | 0 | 0 |
| account_id | INTEGER | 0 | 0 |
| instrument_id | INTEGER | 0 | 0 |
| quantity_e8 | INTEGER | 0 | 0 |
| market_value_e8 | INTEGER | 0 | 0 |
| net_cost_e8 | INTEGER | 0 | 0 |
| cumulative_buy_e8 | INTEGER | 0 | 0 |
| cumulative_sell_e8 | INTEGER | 0 | 0 |
| realized_pnl_e8 | INTEGER | 0 | 0 |
| period_pnl_e8 | INTEGER | 0 | 0 |
| period_buy_quantity_e8 | INTEGER | 0 | 0 |
| period_sell_quantity_e8 | INTEGER | 0 | 0 |
| period_buy_amount_e8 | INTEGER | 0 | 0 |
| period_sell_amount_e8 | INTEGER | 0 | 0 |
| opening_market_value_e8 | INTEGER | 0 | 0 |
| period_net_investment_e8 | INTEGER | 0 | 0 |
| cumulative_capital_e8 | INTEGER | 0 | 0 |
| cost_price_e8 | INTEGER | 0 | 0 |
| close_price_e8 | INTEGER | 0 | 0 |
| currency | TEXT | 1 | 0 |
| source_record_id | INTEGER | 1 | 0 |

## import_batches（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| batch_key | TEXT | 1 | 0 |
| source_kind | TEXT | 1 | 0 |
| source_scope | TEXT | 1 | 0 |
| source_path | TEXT | 0 | 0 |
| snapshot_path | TEXT | 0 | 0 |
| content_sha256 | TEXT | 1 | 0 |
| parser_version | TEXT | 1 | 0 |
| request_start | TEXT | 0 | 0 |
| request_end | TEXT | 0 | 0 |
| resume_cursor | TEXT | 0 | 0 |
| status | TEXT | 1 | 0 |
| imported_at_utc | TEXT | 1 | 0 |
| record_count | INTEGER | 1 | 0 |
| input_role | TEXT | 1 | 0 |
| error_message | TEXT | 0 | 0 |

## instrument_aliases（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| provider | TEXT | 1 | 0 |
| market | TEXT | 1 | 0 |
| source_symbol | TEXT | 1 | 0 |
| instrument_id | INTEGER | 1 | 0 |
| source_record_id | INTEGER | 0 | 0 |

## instruments（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| market | TEXT | 1 | 0 |
| symbol | TEXT | 1 | 0 |
| name | TEXT | 0 | 0 |
| currency | TEXT | 1 | 0 |
| asset_type | TEXT | 1 | 0 |
| category | TEXT | 1 | 0 |
| source_record_id | INTEGER | 0 | 0 |

## market_prices（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| symbol | TEXT | 1 | 0 |
| price_type | TEXT | 1 | 0 |
| trade_date | TEXT | 0 | 0 |
| period_key | TEXT | 1 | 0 |
| quoted_at_utc | TEXT | 0 | 0 |
| price_e8 | INTEGER | 1 | 0 |
| updated_at_utc | TEXT | 1 | 0 |
| price_status | TEXT | 1 | 0 |
| source_record_id | INTEGER | 1 | 0 |

## person_operations（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| operation_key | TEXT | 1 | 0 |
| person_id | INTEGER | 1 | 0 |
| account_id | INTEGER | 1 | 0 |
| instrument_id | INTEGER | 1 | 0 |
| broker_trade_id | INTEGER | 0 | 0 |
| operation_kind | TEXT | 1 | 0 |
| side | TEXT | 1 | 0 |
| business_date | TEXT | 1 | 0 |
| book_time | TEXT | 1 | 0 |
| executed_at_utc | TEXT | 0 | 0 |
| sequence_no | INTEGER | 1 | 0 |
| quantity_e8 | INTEGER | 1 | 0 |
| price_e8 | INTEGER | 0 | 0 |
| amount_e8 | INTEGER | 0 | 0 |
| amount_basis | TEXT | 1 | 0 |
| currency | TEXT | 1 | 0 |
| counterparty_person_id | INTEGER | 0 | 0 |
| transfer_group_key | TEXT | 0 | 0 |
| link_status | TEXT | 1 | 0 |
| source_record_id | INTEGER | 1 | 0 |
| legacy_order_id | TEXT | 0 | 0 |
| legacy_direction | TEXT | 0 | 0 |
| legacy_transaction_type | TEXT | 0 | 0 |
| legacy_source | TEXT | 0 | 0 |
| legacy_counterparty | TEXT | 0 | 0 |

## persons（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| name | TEXT | 1 | 0 |
| enabled | INTEGER | 1 | 0 |
| notes | TEXT | 0 | 0 |

## raw_records（table）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| id | INTEGER | 0 | 1 |
| import_batch_id | INTEGER | 1 | 0 |
| source_locator | TEXT | 1 | 0 |
| source_entity_id | TEXT | 0 | 0 |
| original_time_text | TEXT | 0 | 0 |
| payload_json | TEXT | 1 | 0 |
| payload_sha256 | TEXT | 1 | 0 |
| parse_status | TEXT | 1 | 0 |
| parse_message | TEXT | 0 | 0 |
| source_sheet | TEXT | 1 | 0 |
| source_row | INTEGER | 1 | 0 |
| record_kind | TEXT | 1 | 0 |

## 检查_人员操作（view）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| 编号 | INTEGER | 0 | 0 |
| 姓名 | TEXT | 0 | 0 |
| 渠道 | TEXT | 0 | 0 |
| Ticker | TEXT | 0 | 0 |
| 交易时间 | TEXT | 0 | 0 |
| 方向 | TEXT | 0 | 0 |
| 成交数量 | 派生列 | 0 | 0 |
| 成交均价 | 派生列 | 0 | 0 |
| 成交金额 | 派生列 | 0 | 0 |
| 币种 | TEXT | 0 | 0 |
| 交易类型 | TEXT | 0 | 0 |
| 划转对象 | TEXT | 0 | 0 |
| 订单ID | TEXT | 0 | 0 |
| 关联状态 | TEXT | 0 | 0 |
| 来源文件 | TEXT | 0 | 0 |
| 来源表 | TEXT | 0 | 0 |
| 来源行 | INTEGER | 0 | 0 |

## 检查_券商成交（view）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| 编号 | INTEGER | 0 | 0 |
| 渠道 | TEXT | 0 | 0 |
| Ticker | TEXT | 0 | 0 |
| 交易时间 | TEXT | 0 | 0 |
| 方向 | 派生列 | 0 | 0 |
| 成交数量 | 派生列 | 0 | 0 |
| 成交均价 | 派生列 | 0 | 0 |
| 原账本成交金额 | 派生列 | 0 | 0 |
| 金额口径 | TEXT | 0 | 0 |
| 谁 | TEXT | 0 | 0 |
| 订单ID | TEXT | 0 | 0 |
| 记账范围 | TEXT | 0 | 0 |
| 来源文件 | TEXT | 0 | 0 |
| 来源行 | INTEGER | 0 | 0 |

## 检查_历史基准（view）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| 编号 | INTEGER | 0 | 0 |
| 日期 | TEXT | 0 | 0 |
| 基准类型 | TEXT | 0 | 0 |
| 用途 | TEXT | 0 | 0 |
| 姓名 | TEXT | 0 | 0 |
| 渠道 | TEXT | 0 | 0 |
| 证券 | TEXT | 0 | 0 |
| 数量 | 派生列 | 0 | 0 |
| 市值 | 派生列 | 0 | 0 |
| 累计净成本 | 派生列 | 0 | 0 |
| 本月收益 | 派生列 | 0 | 0 |
| 币种 | TEXT | 0 | 0 |
| 来源文件 | TEXT | 0 | 0 |
| 来源表 | TEXT | 0 | 0 |
| 来源行 | INTEGER | 0 | 0 |

## 检查_原始记录（view）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| 编号 | INTEGER | 0 | 0 |
| 来源文件 | TEXT | 0 | 0 |
| 来源用途 | TEXT | 0 | 0 |
| 来源表 | TEXT | 0 | 0 |
| 来源行 | INTEGER | 0 | 0 |
| 行类型 | TEXT | 0 | 0 |
| 处理状态 | TEXT | 0 | 0 |
| 处理说明 | TEXT | 0 | 0 |
| 原始内容 | TEXT | 0 | 0 |

## 检查_证券行情（view）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| 编号 | INTEGER | 0 | 0 |
| 证券代码 | TEXT | 0 | 0 |
| 价格类型 | 派生列 | 0 | 0 |
| 交易日期 | TEXT | 0 | 0 |
| 期间 | TEXT | 0 | 0 |
| 报价时间UTC | TEXT | 0 | 0 |
| 价格 | 派生列 | 0 | 0 |
| 口径状态 | 派生列 | 0 | 0 |
| 原缓存来源 | 派生列 | 0 | 0 |
| 来源文件 | TEXT | 0 | 0 |
| 来源表 | TEXT | 0 | 0 |
| 来源行 | INTEGER | 0 | 0 |

## 检查_资产调整（view）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| 编号 | INTEGER | 0 | 0 |
| 渠道 | TEXT | 0 | 0 |
| 生效日期 | TEXT | 0 | 0 |
| 调整方式 | TEXT | 0 | 0 |
| 调整指标 | TEXT | 0 | 0 |
| 金额 | 派生列 | 0 | 0 |
| 币种 | TEXT | 0 | 0 |
| 说明 | TEXT | 0 | 0 |
| 来源文件 | TEXT | 0 | 0 |
| 来源行 | INTEGER | 0 | 0 |

## 检查_资金流水（view）

| 字段 | 类型 | NOT NULL | 主键 |
|---|---|---|---|
| 编号 | INTEGER | 0 | 0 |
| 渠道 | TEXT | 0 | 0 |
| 已确认归属人员 | TEXT | 0 | 0 |
| 日期 | TEXT | 0 | 0 |
| 分类 | TEXT | 0 | 0 |
| 来源类型 | TEXT | 0 | 0 |
| 收付金额 | 派生列 | 0 | 0 |
| 币种 | TEXT | 0 | 0 |
| 记账范围 | TEXT | 0 | 0 |
| 说明 | TEXT | 0 | 0 |
| 来源文件 | TEXT | 0 | 0 |
| 来源行 | INTEGER | 0 | 0 |
