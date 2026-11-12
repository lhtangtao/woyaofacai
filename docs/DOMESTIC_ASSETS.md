# 国内资产 · 支付宝基金

Mac 左侧选择「国内资产 → 持仓管理」。Web 登录管理员账号后使用同一入口。可筛选支付宝平台并点击基金查看该账户交易和历史持仓。

## 持仓状态与估值

持仓管理分别显示最后确认的份额及日期、交易资料覆盖日期和实际净值日期。已确认零份额的市值为零。超过交易资料覆盖范围时保留历史持仓或清仓事实，当前份额及市值留空，并在「待核对记录」中显示原因。历史清仓日期取最后确认份额连续归零的首日，须有前一日的正份额记录。

通过 `python -m portfolio.domestic confirm-current-positions --confirm` 可在明确确认整套当前持仓后登记状态。命令以最近已核对的正份额为当前持仓范围，将其余历史基金登记为用户确认清仓，写入前备份计算库。确认仅适用于登记日及之后的查询。用户确认清仓但当前账本份额未知时，市值保持空白。后续出现新的已确认交易时，由账本重新判断状态。

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

## 天天基金对账单原始入库

```sh
PYTHONPATH=src .venv/bin/python -m portfolio.domestic tiantian-preview /path/statement.pdf
PYTHONPATH=src .venv/bin/python -m portfolio.domestic tiantian-import --confirm /path/statement.pdf
```

可一次指定多个 PDF。终端对每份文件单独隐藏输入密码。预检显示各期页数、日期、业务类型、解析问题及重复记录数。正式导入前备份 Raw DB，并将原始加密文件按 SHA-256 归档到 `imports/tiantian/`。

统一原始表 `raw_fund_transactions` 按账户区分支付宝与天天基金。`raw_fund_source_files` 保存来源期间及实际日期，`raw_fund_transaction_sources` 保留每次出现的来源页码和原始字段。旧支付宝表继续支持现有查询，后续支付宝导入同时镜像至统一表。

天天基金保留交易和单列的分红记录。订单号、申请字段和交易表未披露的基金类型留空，`--` 按缺值处理，原样仍保存在原始字段中。强减保留原业务类型，标记待核对。相同文件哈希只导入一次。跨文件重叠按完整字段与出现次数去重，同一文件内字段相同的多行仍分别保留。月度或年度期末持仓汇总不作为期初数据。

本阶段只有 CLI 与原始库入库。Calc DB、持仓计算和应用页面沿用现有支付宝范围。
