# 国内资产 · 支付宝基金

Mac 左侧选择「国内资产 → 支付宝基金」。Web 登录管理员账号后使用同一入口。基金持仓支持按日期和基金查询。交易明细支持确认日期范围、基金代码、原始类型、交易时间排序、分页和 PDF 原始记录追溯。

## 本地存储

两个 SQLite 位于项目数据库目录的 `domestic_assets/` 子目录。源文件按 SHA-256 归档在 `imports/alipay/`，原文件名和哈希保存在导入批次中。原始库和计算库都属于本机数据。

| 数据库 | 表 |
|---|---|
| `domestic_assets_raw.db` | `raw_import_batches`、`raw_asset_accounts`、`raw_fund_opening_positions`、`raw_alipay_fund_transactions`、`raw_fund_nav`、`raw_parse_issues` |
| `domestic_assets_calc.db` | `calc_fund_master`、`calc_fund_transactions`、`calc_fund_share_movements`、`calc_fund_transfer_matches`、`calc_fund_positions_daily`、`calc_fund_positions_monthly`、`calc_fund_monthly_summary`、`calc_runs`、`calc_data_quality_checks`、`calc_raw_manifest` |

## 导入与重算

通过「数据导入与校验」分别选择资产证明和交易 PDF，先预检，再确认。CLI 可按以下方式操作，导入前为已存在的国内资产库生成 SQLite 备份。

```sh
.venv/bin/python -m pip install '.[domestic]'
PYTHONPATH=src .venv/bin/python -m portfolio.domestic preview --opening /path/opening.pdf --transactions /path/transactions.pdf --snapshot 2021-11-11
PYTHONPATH=src .venv/bin/python -m portfolio.domestic import --confirm --opening /path/opening.pdf --transactions /path/transactions.pdf --snapshot 2021-11-11
PYTHONPATH=src .venv/bin/python -m portfolio.domestic rebuild
PYTHONPATH=src .venv/bin/python -m portfolio.domestic rebuild --full
```

期初证明记录确认成功的份额，期初之后以确认日期更新份额。跨 TA 转换将同一订单的两个原始行分别映射为转出卖出和转入买入，并保持无外部现金流。新导入的记录优先重算受影响基金及转换对手方。整个计算的日期范围变化时重建所有基金的对应历史。

支付宝基金 002611、012414、019172 的 2021-11-11 期初份额经用户确认为零。资产证明中的份额优先，其他未确认基金继续标为待核对。

持仓起点缺失时保留已确认的变动，持有份额标记待核对。月末缺少对应净值时，市值留空。基金申购和赎回属于基金账本。支付宝资金账户与银行卡流水需要各自的来源文件。

后续平台接入可为对应 PDF、CSV 或 API 建独立原始表及解析入口，并注册账户。验证新来源的确认日期和资金口径后，将其接入同一标准交易与份额变化层。
