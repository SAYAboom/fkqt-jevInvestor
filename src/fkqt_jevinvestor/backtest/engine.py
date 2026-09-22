"""日频回测事件循环。

只按交易日推进冻结数据、调用目标与执行端口、累计指标，不依赖网络、数据库
或任何外部服务；金额与比例一律 Decimal，不使用当前时间或随机数。
"""

from decimal import Decimal

from fkqt_jevinvestor.backtest.metrics import build_daily_record, summarize
from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    BacktestResult,
    DailyBacktestRecord,
    ExecutionPort,
    ReplayDataProvider,
    ReplayDay,
    TargetPositionBatch,
    TargetProvider,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState


class BacktestError(RuntimeError):
    """回测过程中可预期的失败；消息里包含稳定错误码。"""


class BacktestEngine:
    """按交易日推进冻结数据、调用目标与执行端口、累计指标的日频回测引擎。"""

    def __init__(
        self,
        replay: ReplayDataProvider,
        targets: TargetProvider,
        execution: ExecutionPort,
    ) -> None:
        self._replay = replay
        self._targets = targets
        self._execution = execution

    async def run(self, config: BacktestConfig) -> BacktestResult:
        # 1. 校验时间窗口
        if config.start_date > config.end_date:
            raise BacktestError("INVALID_BACKTEST_WINDOW")

        # 2. 取决策日
        dates = await self._replay.decision_dates(config.start_date, config.end_date)

        # 2a. 严格升序且无重复
        if list(dates) != sorted(dates) or len(dates) != len(set(dates)):
            raise BacktestError("DECISION_DATES_NOT_STRICTLY_ORDERED")

        # 2b. 非空
        if not dates:
            raise BacktestError("EMPTY_BACKTEST_WINDOW")

        # 3. 预热期：契约缺口，warmup_trading_days 暂不参与任何计算。
        # TODO(契约缺口): 契约要求加载预热期数据但不产生记录，但 ReplayDataProvider
        # 接口没有方法能表达"起始日之前的 N 个交易日是哪几天"。等契约答复后再实现。

        portfolio = self._initial_portfolio(config)
        previous_equity = config.initial_cash
        records: list[DailyBacktestRecord] = []

        for decision_date in dates:
            # 4a. 读当天回放数据
            day = await self._replay.load_day(decision_date)

            # 4b. 回放日的决策日期必须与请求一致
            if day.decision_date != decision_date:
                raise BacktestError("REPLAY_DAY_DATE_MISMATCH")

            # 5. 决策日必须早于计划执行日（错误码待确认，暂用 REPLAY_DAY_DATE_MISMATCH）
            if day.decision_date >= day.planned_execution_date:
                raise BacktestError("REPLAY_DAY_DATE_MISMATCH")

            # 6. 截止时间的日期必须等于决策日（错误码待确认，暂用 REPLAY_DAY_DATE_MISMATCH）
            if day.decision_cutoff.date() != day.decision_date:
                raise BacktestError("REPLAY_DAY_DATE_MISMATCH")

            # 7. 防前视：每个特征值的 as_of 不得晚于截止时间
            self._validate_point_in_time(day)

            # 8. 用不含次日行情的决策视图调用目标生成器
            batch = await self._targets.build_targets(
                config, day.decision_view(), portfolio
            )

            # 9. 校验目标批次
            self._validate_targets(config, day, batch)

            # 10. 用次日成交行情调用执行端口
            result = await self._execution.execute(
                portfolio, batch, day.execution_market
            )

            # 11. 生成逐日记录
            records.append(build_daily_record(previous_equity, result))

            # 更新组合状态与"前一日权益"
            portfolio = result.portfolio_after
            previous_equity = result.total_equity

        # 12. 汇总
        summary = summarize(config, tuple(records))

        # 13. 哈希：契约缺口。summary 里已带 64 位占位值，原样返回，不另造。
        # TODO(契约缺口): config_hash/result_hash 的覆盖范围与"自我引用"处理未定。

        return BacktestResult(
            config=config,
            daily_records=tuple(records),
            summary=summary,
        )

    @staticmethod
    def _initial_portfolio(config: BacktestConfig) -> PortfolioState:
        # 契约未定义引擎如何从 initial_cash 造出 PortfolioState，采用如下口径（待确认）：
        # portfolio_id 取 run_id、现金取初始资金、其余取零值/空仓。
        return PortfolioState(
            portfolio_id=config.run_id,
            cash_balance=config.initial_cash,
            frozen_cash=Decimal(0),
            realized_pnl=Decimal(0),
            positions=(),
            version=1,
        )

    @staticmethod
    def _validate_point_in_time(day: ReplayDay) -> None:
        cutoff = day.decision_cutoff
        for snapshot in day.features.values():
            for feature in snapshot.values.values():
                if feature.as_of > cutoff:
                    raise BacktestError("POINT_IN_TIME_VIOLATION")

    @staticmethod
    def _validate_targets(
        config: BacktestConfig,
        day: ReplayDay,
        batch: TargetPositionBatch,
    ) -> None:
        if (
            batch.decision_date != day.decision_date
            or batch.planned_execution_date != day.planned_execution_date
        ):
            raise BacktestError("TARGET_DATE_MISMATCH")
        if batch.experiment_arm != config.experiment_arm:
            raise BacktestError("TARGET_EXPERIMENT_ARM_MISMATCH")
        for target in batch.targets:
            if target.sizing_version != config.sizing_version:
                raise BacktestError("TARGET_SIZING_VERSION_MISMATCH")
