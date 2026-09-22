"""回测引擎验收测试（契约第 9 节 12 条）。

引擎尚未实现，本批测试先立靶子：除预热期（第 2 条）与结果哈希（第 8 条）占位外，
其余 10 条现在应全部为红。期望值按规格 2026-09-22-engine-tests-spec.md 手工计算。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fkqt_jevinvestor.backtest.engine import BacktestEngine, BacktestError
from fkqt_jevinvestor.backtest.runner import run_experiment_arms
from fkqt_jevinvestor.domain.backtest import ExperimentArm
from tests.backtest.fixtures import (
    DEFAULT_SYMBOL,
    FakeExecutionPort,
    FakeReplayProvider,
    FakeTargetProvider,
    make_config,
    make_execution_market,
    make_execution_result,
    make_feature_snapshot,
    make_feature_value,
    make_replay_day,
    make_target_batch,
    make_target_position,
)


async def test_two_day_normal_backtest() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    d3 = date(2026, 1, 7)
    replay = FakeReplayProvider(
        days={
            d1: make_replay_day(decision_date=d1, planned_execution_date=d2),
            d2: make_replay_day(decision_date=d2, planned_execution_date=d3),
        }
    )
    targets = FakeTargetProvider(
        batches={
            d1: make_target_batch(decision_date=d1, planned_execution_date=d2),
            d2: make_target_batch(decision_date=d2, planned_execution_date=d3),
        }
    )
    port = FakeExecutionPort(results=(make_execution_result(), make_execution_result()))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    result = await engine.run(make_config(start_date=d1, end_date=d2))
    assert len(result.daily_records) == 2
    # 资产恒等式：总权益 = 现金 + 市值，误差不超过 0.01
    for record in result.daily_records:
        assert abs(record.total_equity - (record.cash_balance + record.market_value)) <= Decimal("0.01")


@pytest.mark.skip(
    reason="预热期语义未定：ReplayDataProvider 没有接口能表达'起始日之前的 N 个交易日'，待契约答复方案 A/B"
)
async def test_warmup_produces_no_records_or_target_calls() -> None:
    """预期：预热期数据被加载但不产生逐日记录，也不调用目标生成器（契约第 5 节步骤 3）。"""


async def test_weekend_execution_uses_provider_date() -> None:
    friday = date(2026, 1, 9)
    monday = date(2026, 1, 12)
    day = make_replay_day(
        decision_date=friday,
        planned_execution_date=monday,
        execution_market={DEFAULT_SYMBOL: make_execution_market(trade_date=monday)},
    )
    replay = FakeReplayProvider(days={friday: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(decision_date=friday, planned_execution_date=monday)
    )
    port = FakeExecutionPort(results=(make_execution_result(execution_date=monday),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    result = await engine.run(make_config(start_date=friday, end_date=friday))
    # D+1 必须来自提供方（周一），不能用自然日加一（周六）
    assert result.daily_records[0].execution_date == monday
    assert port.received_market_dates == [(monday,)]


async def test_future_feature_fails_whole_run() -> None:
    d1 = date(2026, 1, 5)
    cutoff = datetime(2026, 1, 5, 15, 0, 0, tzinfo=UTC)
    future = datetime(2026, 1, 6, 9, 30, 0, tzinfo=UTC)
    feature = make_feature_value(as_of=future)
    snapshot = make_feature_snapshot(values={"momentum_20": feature})
    day = make_replay_day(
        decision_date=d1,
        decision_cutoff=cutoff,
        features={DEFAULT_SYMBOL: snapshot},
    )
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(batch=make_target_batch(decision_date=d1))
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    with pytest.raises(BacktestError, match="POINT_IN_TIME_VIOLATION"):
        await engine.run(make_config(start_date=d1, end_date=d1))


async def test_target_date_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    day = make_replay_day(decision_date=d1, planned_execution_date=d2)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(decision_date=date(2026, 1, 7), planned_execution_date=d2)
    )
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    with pytest.raises(BacktestError, match="TARGET_DATE_MISMATCH"):
        await engine.run(make_config(start_date=d1, end_date=d1))


async def test_target_arm_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    day = make_replay_day(decision_date=d1)
    replay = FakeReplayProvider(days={d1: day})
    # B 组配置收到 A 组批次
    targets = FakeTargetProvider(batch=make_target_batch(decision_date=d1, experiment_arm=ExperimentArm.A_LLM))
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d1, experiment_arm=ExperimentArm.B_JEV_LLM)
    with pytest.raises(BacktestError, match="TARGET_EXPERIMENT_ARM_MISMATCH"):
        await engine.run(config)


async def test_empty_window_fails() -> None:
    replay = FakeReplayProvider(days={})
    targets = FakeTargetProvider(batch=make_target_batch())
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=date(2026, 1, 5), end_date=date(2026, 1, 6))
    with pytest.raises(BacktestError, match="EMPTY_BACKTEST_WINDOW"):
        await engine.run(config)


@pytest.mark.skip(
    reason="result_hash 覆盖范围未定：config_hash/result_hash 位于被哈希对象内部，会自我引用，待契约答复"
)
async def test_replay_is_deterministic() -> None:
    """预期：相同 Config 与 Fixture 运行两次，canonical JSON 与 result_hash 完全相同。"""


async def test_zero_volatility_sharpe_none() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    d3 = date(2026, 1, 7)
    replay = FakeReplayProvider(
        days={
            d1: make_replay_day(decision_date=d1, planned_execution_date=d2),
            d2: make_replay_day(decision_date=d2, planned_execution_date=d3),
        }
    )
    targets = FakeTargetProvider(
        batches={
            d1: make_target_batch(decision_date=d1, planned_execution_date=d2),
            d2: make_target_batch(decision_date=d2, planned_execution_date=d3),
        }
    )
    # 权益恒定：两个执行结果 total_equity 完全相同
    port = FakeExecutionPort(results=(make_execution_result(), make_execution_result()))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    result = await engine.run(make_config(start_date=d1, end_date=d2))
    assert result.summary.sharpe_ratio is None
    assert result.summary.sortino_ratio is None


async def test_no_orders_fill_rate_none() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    d3 = date(2026, 1, 7)
    replay = FakeReplayProvider(
        days={
            d1: make_replay_day(decision_date=d1, planned_execution_date=d2),
            d2: make_replay_day(decision_date=d2, planned_execution_date=d3),
        }
    )
    targets = FakeTargetProvider(
        batches={
            d1: make_target_batch(decision_date=d1, planned_execution_date=d2),
            d2: make_target_batch(decision_date=d2, planned_execution_date=d3),
        }
    )
    # 下单数为 0
    port = FakeExecutionPort(results=(make_execution_result(), make_execution_result()))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    result = await engine.run(make_config(start_date=d1, end_date=d2))
    assert result.summary.fill_rate is None


async def test_fairness_rejects_whole_batch() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    config_a = make_config(run_id="run-a", experiment_arm=ExperimentArm.A_LLM, start_date=d1, end_date=d2)
    config_b = make_config(
        run_id="run-b",
        experiment_arm=ExperimentArm.B_JEV_LLM,
        start_date=d1,
        end_date=d2,
        dataset_hash="1" * 64,
    )
    replay = FakeReplayProvider(days={d1: make_replay_day(decision_date=d1)})
    providers = {
        ExperimentArm.A_LLM: FakeTargetProvider(batch=make_target_batch(experiment_arm=ExperimentArm.A_LLM)),
        ExperimentArm.B_JEV_LLM: FakeTargetProvider(batch=make_target_batch(experiment_arm=ExperimentArm.B_JEV_LLM)),
    }
    ports: list[FakeExecutionPort] = []

    def execution_factory() -> FakeExecutionPort:
        port = FakeExecutionPort(results=(make_execution_result(),))
        ports.append(port)
        return port

    # 契约第 8 节要求整批拒绝；契约未定义公平性专属错误码，这里断言通用 BacktestError
    with pytest.raises(BacktestError):
        await run_experiment_arms((config_a, config_b), replay, providers, execution_factory)
    assert ports == []


async def test_arm_state_isolation() -> None:
    d1 = date(2026, 1, 5)
    arms = (
        ExperimentArm.A_LLM,
        ExperimentArm.B_JEV_LLM,
        ExperimentArm.C_JEV_DIRECT,
        ExperimentArm.D_RULE,
    )
    configs = tuple(
        make_config(run_id=f"run-{arm.value}", experiment_arm=arm, start_date=d1, end_date=d1)
        for arm in arms
    )
    replay = FakeReplayProvider(days={d1: make_replay_day(decision_date=d1)})
    providers = {
        arm: FakeTargetProvider(batch=make_target_batch(decision_date=d1, experiment_arm=arm))
        for arm in arms
    }
    ports: list[FakeExecutionPort] = []

    def execution_factory() -> FakeExecutionPort:
        port = FakeExecutionPort(results=(make_execution_result(),))
        ports.append(port)
        return port

    await run_experiment_arms(configs, replay, providers, execution_factory)
    # 四组各取得独立执行器实例，且各执行一次、互不影响
    assert len(ports) == 4
    assert all(port.calls == 1 for port in ports)


async def test_invalid_backtest_window_fails() -> None:
    replay = FakeReplayProvider(days={})
    targets = FakeTargetProvider(batch=make_target_batch())
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=date(2026, 1, 6), end_date=date(2026, 1, 5))
    with pytest.raises(BacktestError, match="INVALID_BACKTEST_WINDOW"):
        await engine.run(config)


async def test_decision_dates_not_strictly_ordered_fails() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    replay = FakeReplayProvider(
        days={
            d1: make_replay_day(decision_date=d1),
            d2: make_replay_day(decision_date=d2),
        },
        decision_dates=[d2, d1],
    )
    targets = FakeTargetProvider(batch=make_target_batch())
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d2)
    with pytest.raises(BacktestError, match="DECISION_DATES_NOT_STRICTLY_ORDERED"):
        await engine.run(config)


async def test_replay_day_date_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    wrong = date(2026, 1, 6)
    day = make_replay_day(decision_date=wrong)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(batch=make_target_batch())
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d1)
    with pytest.raises(BacktestError, match="REPLAY_DAY_DATE_MISMATCH"):
        await engine.run(config)


async def test_target_sizing_version_mismatch_fails() -> None:
    d1 = date(2026, 1, 5)
    day = make_replay_day(decision_date=d1)
    replay = FakeReplayProvider(days={d1: day})
    targets = FakeTargetProvider(
        batch=make_target_batch(
            decision_date=d1,
            targets=(make_target_position(sizing_version="sizing-other"),),
        )
    )
    port = FakeExecutionPort(results=(make_execution_result(),))
    engine = BacktestEngine(replay=replay, targets=targets, execution=port)
    config = make_config(start_date=d1, end_date=d1)
    with pytest.raises(BacktestError, match="TARGET_SIZING_VERSION_MISMATCH"):
        await engine.run(config)
