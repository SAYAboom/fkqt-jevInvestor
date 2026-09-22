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

## 4. 错误码表

| 错误码 | 触发条件 |
|---|---|
| INVALID_BACKTEST_WINDOW | 起始日期晚于结束日期 |
| DECISION_DATES_NOT_STRICTLY_ORDERED | 决策日未严格升序或存在重复 |
| REPLAY_DAY_DATE_MISMATCH | 回放日的决策日期与请求不符；或决策日晚于计划执行日；或截止时间不在决策日当天（后两者错误码待确认） |
| POINT_IN_TIME_VIOLATION | 某特征值的 as_of 晚于决策截止时间 |
| TARGET_DATE_MISMATCH | 目标批次的决策日或计划执行日与当天不符 |
| TARGET_EXPERIMENT_ARM_MISMATCH | 目标批次的实验组与配置不符 |
| TARGET_SIZING_VERSION_MISMATCH | 目标仓位的 sizing_version 与配置不符 |
| EMPTY_BACKTEST_WINDOW | 决策日为空 |

四组运行器在公平性检查失败时抛 `BacktestError`，消息以
`EXPERIMENT_CONFIG_MISMATCH` 开头（该错误码不在八个稳定错误码之列，待确认）。

## 5. 已知边界

- 预热期尚未实现：`warmup_trading_days` 暂不参与计算，等契约答复。
- 结果哈希尚未实现：`config_hash` / `result_hash` 当前是 64 位占位值，等契约答复。
- 逐日记录里的累计收益与回撤当前是占位值（0），等契约答复。

## 6. 四组实验

- A：确定性因子 → 大模型 → 仓位引擎
- B：确定性因子 → Jev 概率 → 大模型 → 仓位引擎
- C：确定性因子 → Jev 概率 → 规则方向 → 仓位引擎
- D：确定性因子 → 规则方向 → 仓位引擎（纯规则基线）

本模块只提供引擎与四组运行器；四组各自的真实 `TargetProvider` 由核心项目在
后续阶段提供。
