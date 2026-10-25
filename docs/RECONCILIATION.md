# 对账与验收边界

## M6 当前可执行入口

```bash
python -m portfolio.reconcile --db <真实库> --excel <参考.xlsx> --as-of 2026-10-15 --person 个人 --report reports/新文件名.csv
```

同时输出 CSV/JSON；GUI 可选参考文件并导出 XLSX。包含18 KPI、8张明细、渠道/分类汇总、27图表系列。
KPI 公式无缓存时按保存的参考明细独立推导并标注；汇总或动态图表无缓存则保持 UNAVAILABLE，不伪造零。
源单元格和数据库来源 ID 都进入报告。差异原因包含日期、负仓、划转、缺价、微操等候选线索；候选原因不等于逐行根因已证实。
本次仍有 DIFF / UNAVAILABLE，财务一致性验收未通过，执行结果见 VALIDATION_M2_M7.md。
下方 M0/M1 记录为历史背景；原“后续目标命令”现已实现。

## M0 / M1 已实现的可执行入口

```bash
python scripts/audit_sources.py
python scripts/extract_excel_reference.py --input references/个人投资总览.xlsx --output reports/excel_snapshot.json
python scripts/inspect_database.py --db <用户指定库> --integrity
python -m portfolio.cli.health --db <用户指定库>
pytest -q
ruff check .
```

Excel 抽取保留保存时的精确 XML 数值字符串、公式文本、来源单元格、图表 series 的引用/缓存和定义名称。
不对原工作簿重算，不用抽取值作为运行时持仓；输出采用排他创建，重复执行请使用新的输出文件名。
tests/fixtures/excel_snapshot.json 是私有本地抽取，不应提交 Git；表格分析测试从实际原始参考文件读取。

## 现阶段真实库验证

只读连接采用 file URI + mode=ro + query_only，并额外拒绝 DML/DDL/ATTACH/可写 PRAGMA。
连接由执行查询的线程各自创建和关闭；同一分页查询的总数和数据使用同一个只读事务快照。
真实源库不执行 DDL，不更新或重建，不自动修复；结构验证比较 14 表/7 视图及预期列的类型/非空/主键定义。
M1 未宣称完全等价验证所有额外 CHECK 约束；缺关键列/类型改变会明确拒绝连接。

## 金样本的含义

参考 TQQQ 行只能先验证：数量×价格=市值、买入−卖出=净投入、市值−净投入=兼容盈亏。
这不等于从真实库重新计算持仓/收益成功；该金样本属于 M2–M6 的对账输入。
原 Excel 的统计日与日报最新日不同，要先对齐具体日期和人员/券商范围。

## 后续完整对账

`python -m portfolio.reconcile --db … --excel … --as-of … --report …` 属于 M6 目标接口，M1 未提供空实现冒充完成。
后续逐项输出 18 KPI、14/18 列持仓、日/月/微操/清仓样本、渠道/分类汇总、交易后状态及 27 图表的数据差异。
按日期、范围、缺行情、口径、费用、拆股、粒度冲突、未知成本分类解释差异；内部 e8 精确，显示兼容金额容差不超过 $0.01。
