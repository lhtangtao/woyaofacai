# Money3独立运行与依赖审计

2026-10-24，修改者：Codex。

## 原先发现的实际依赖

| 原依赖 | 当前处理 |
|---|---|
| QSettings记住Money2的业务库 | 默认优先打开Money3本地业务库，旧外部默认值不再自动启用 |
| sync_data动态导入Money2/src | 必要同步组件移植到portfolio.sync，不导入兄弟项目 |
| 两份流水按修改时间互相覆盖 | 仅使用Money3/交易流水.xlsx，无跨目录镜像 |
| 借用上级Python环境 | 本目录.venv；同步依赖写入pyproject.toml的sync可选组 |
| 长桥和嘉信配置、Token留在旧目录 | 复制本地配置；嘉信token_file改为Money3内的相对路径 |
| 历史补齐脚本和月度页面硬编码旧库 | 改用Money3本地数据库及凭证 |
| 同步失败提示要求运行旧授权脚本 | 本地scripts/authorize_schwab.py |
| 旧打包程序可能仍运行旧代码 | 当前启动脚本只启动本地源码，不回退到旧dist |

同步代码从原适配器迁入，裁剪非同步报表生成模块，保留流水归属、撤销、内部划转、金额精度和时区规则；不是另造一套交易算法。

## 当前路径与用法

- 业务库：`money3/数据库/money.sqlite3`。
- 唯一日常流水：`money3/交易流水.xlsx`。迁移不覆盖该文件，不自动选择另一份更新的流水。
- 长桥配置：`money3/longbridge_app_key.py`（仅解析常量）。
- 嘉信配置与Token：`money3/schwab_config.json`、`money3/schwab_token.json`。
- BBAE/第一证券导出：`money3/原始数据/手动维护/`。
- 行情/财报/曲线缓存仍使用macOS MoneyPortfolio应用数据目录，独立于Money2。源库或凭证路径改变后，旧路径对应缓存不会冒充新来源；日内曲线可以复用已有K线重新计算，财报新路径首次查询可能需要联网。
- macOS界面偏好、日志、汇率缓存仍使用系统应用数据目录，不属于Money2。

在Money3目录执行：

```sh
.venv/bin/python -m pip install -e '.[sync]'
./启动Money3.command                   # 不同步，打开Money3本地库
./启动Money3.command --excel           # Excel五个核心sheet及资金表入本地库
./启动Money3.command --api             # 券商更新本地流水后，再入本地库
.venv/bin/python scripts/sync_data.py --no-api --check  # 只读预检
.venv/bin/python scripts/authorize_schwab.py            # 仅需要重新授权时运行
```

一次性迁移使用 `scripts/migrate_local_data.py --source 旧项目目录`。SQLite只读备份得到一致副本，逐表核对行数；已有目标不覆盖，配置副本只在本机保存，不会写回来源。日常启动和同步不再运行迁移。

## 验证边界

已迁入数据库副本并核对全部业务表行数；本地数据库结构和五个核心sheet只读预检通过。同步全部模块导入后，已加载的Money2模块数量为0；长桥配置可解析、嘉信Token路径在Money3内。Python未定义变量检查及启动脚本语法检查通过。没有运行自动测试，也没有执行券商API同步或交易入库，因此不声称已验证网络授权是否仍有效或本地Excel与副本库此刻逐笔一致。

旧历史验收文档、数据库中的历史来源描述、兼容算法注释里的Money2名称保留，属于历史证据，不会触发访问旧目录。用户显式选择外部数据库/配置仍可用于查看；默认运行无此依赖。已有旧dist应用未重打包，应使用当前启动脚本。
