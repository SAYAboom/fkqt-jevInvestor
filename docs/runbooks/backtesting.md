# 回测运行手册

本手册记录回测模块的职责、运行方式、结果格式、错误码和已知边界。

## 1. 这个模块做什么

回测引擎负责按交易日推进冻结好的历史数据，调用目标仓位接口与虚拟执行端口，
累计逐日指标并输出规范化结果。它不负责行情下载、特征计算、模型调用或券商连接：
引擎不认识 FKQT、不调用 Jev/LLM、不连接数据库，只依赖三个 Protocol
（`ReplayDataProvider`、`TargetProvider`、`ExecutionPort`）和冻结快照。

## 2. 怎么跑

回测引擎是库，目前没有独立 CLI 入口。开发期验证用以下命令：

```cmd
uv run ruff check src tests
uv run pytest tests/backtest -q
uv run pyright
```

结果 JSON 由 `fkqt_jevinvestor.backtest.serialization` 的
`canonical_result_json`（生成字符串）与 `write_result`（写成文件）产出。

## 3. 结果文件的格式

`write_result` 写出的 JSON 具有以下性质：

- UTF-8 编码；
- 键按字典序排序；
- 金额与比例（Decimal）序列化为字符串；
- 日期使用 ISO 8601 格式（如 `2026-01-05`）；
- 末尾恰好一个换行；
- 不随平台转换换行：实现用字节写入，保证 Windows 与 Linux 写出的字节完全一致，
  否则同一份结果在两处写出的哈希会对不上。

两个哈希的覆盖范围：

- `config_hash`：`BacktestConfig` 全部字段的规范化 JSON 的 SHA-256；
- `result_hash`：完整 `BacktestResult` 的规范化 JSON 的 SHA-256，计算前从
  `summary` 中排除 `result_hash` 字段（`config_hash` 保留在预映像中）。

## 4. 错误码表

| 错误码 | 触发条件 |
|---|---|
| INVALID_BACKTEST_WINDOW | 起始日期晚于结束日期 |
| DECISION_DATES_NOT_STRICTLY_ORDERED | 决策日未严格升序或存在重复 |
| REPLAY_DAY_DATE_MISMATCH | 回放日的决策日期与请求不符 |
| EXECUTION_DATE_NOT_AFTER_DECISION_DATE | 计划执行日不晚于决策日 |
| DECISION_CUTOFF_DATE_MISMATCH | 截止时间不落在决策日当天 |
| POINT_IN_TIME_VIOLATION | 某特征值的 as_of 晚于决策截止时间 |
| TARGET_DATE_MISMATCH | 目标批次的决策日或计划执行日与当天不符 |
| TARGET_EXPERIMENT_ARM_MISMATCH | 目标批次的实验组与配置不符 |
| TARGET_SIZING_VERSION_MISMATCH | 目标仓位的 sizing_version 与配置不符 |
| TARGET_SYMBOL_COVERAGE_MISMATCH | 目标批次未恰好覆盖特征证券与当前持仓证券 |
| EMPTY_BACKTEST_WINDOW | 决策日为空 |
| INSUFFICIENT_WARMUP_DATA | 预热日不足、乱序、重复或晚于起始日 |
| PORTFOLIO_EQUITY_DEPLETED | 执行结果总权益归零 |

四组运行器在公平性检查失败时抛 `BacktestError`，消息以
`EXPERIMENT_CONFIG_MISMATCH` 开头。

## 5. 预热期与实现状态

预热期已实现：引擎按 `warmup_trading_days` 读取并校验起始日之前的预热日
（决策日一致、执行日在后、截止时间当天、特征不晚于截止时间），但预热期不产生
逐日记录、不调用目标生成器与执行端口、不重算特征、不推进权益与运行峰值。

累计收益与回撤由 `build_daily_record` 按初始资金与运行峰值计算；结果哈希由
`summarize` 计算。

## 6. 四组实验

- A：确定性因子 → 大模型 → 仓位引擎
- B：确定性因子 → Jev 概率 → 大模型 → 仓位引擎
- C：确定性因子 → Jev 概率 → 规则方向 → 仓位引擎
- D：确定性因子 → 规则方向 → 仓位引擎（纯规则基线）

本模块只提供引擎与四组运行器；四组各自的真实 `TargetProvider`、基于冻结快照的
数据适配器、目标批次到执行域的版本化映射，以及生产执行端口适配器，均由核心项目
在后续阶段提供。
