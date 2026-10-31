from math import sqrt
from statistics import mean, stdev

import pytest

from backend.analytics import AllocationRequest, analyse_portfolio
from backend.history import AssetHistory, History
from scripts.import_benchmark import parse_benchmark


def sample(reference):
    return History(source="simulated", dataset_version="test", notice="Fixture",
                   value_basis="Fixture", time_basis="Fixture", sessions=[0, 1, 2, 3],
                   risk_free_returns=reference,
                   assets=[AssetHistory(symbol="A", name="A", index_values=[100, 110, 99, 108.9])])


def test_sharpe_uses_dated_excess_returns_and_sample_deviation():
    result = analyse_portfolio(sample([.001, .002, .003]), AllocationRequest(weights={"A": 100}))
    excess = [.099, -.102, .097]
    assert result.sharpe_ratio == pytest.approx(mean(excess) / stdev(excess) * sqrt(252))


def test_negative_sharpe_and_constant_portfolio():
    result = analyse_portfolio(sample([.001] * 3), AllocationRequest(weights={}))
    assert result.sharpe_ratio is None
    assert "constant" in result.sharpe_unavailable_reason
    history = sample([.001] * 3)
    history.assets[0].index_values = [100, 99, 97, 96]
    assert analyse_portfolio(history, AllocationRequest(weights={"A": 100})).sharpe_ratio < 0


@pytest.mark.parametrize("reference", [[.001], [float("nan")] * 3])
def test_incomplete_or_nonfinite_benchmark_is_rejected(reference):
    with pytest.raises(ValueError, match="Risk-free"):
        analyse_portfolio(sample(reference), AllocationRequest(weights={"A": 100}))


def test_import_reconstructs_monthly_return_using_the_full_month():
    daily = b",Mkt-RF,SMB,HML,RF\n20130102,1,2,3,0.00\n20130103,1,2,3,0.00\n"
    monthly = b",Mkt-RF,SMB,HML,RF\n201301,1,2,3,0.02\n2013,1,2,3,99\n"
    result = parse_benchmark(daily, monthly, ["2013-01-03"])
    assert result[0] > 0
    assert (1 + result[0]) ** 2 - 1 == pytest.approx(.0002)
    with pytest.raises(ValueError, match="cover every"):
        parse_benchmark(daily, monthly, ["2013-01-04"])
    with pytest.raises(ValueError, match="cover every"):
        parse_benchmark(daily, b"", ["2013-01-03"])
    with pytest.raises(ValueError, match="Duplicate"):
        parse_benchmark(daily + daily, monthly, ["2013-01-03"])
    with pytest.raises(ValueError, match="Duplicate"):
        parse_benchmark(daily, monthly + monthly, ["2013-01-03"])
    with pytest.raises(ValueError, match="Invalid monthly"):
        parse_benchmark(daily, monthly.replace(b"0.02", b"nan"), ["2013-01-03"])


def test_bundled_2017_reference_is_not_rounded_to_zero():
    from backend.historical import build_historical_history
    history = build_historical_history()
    july = [rate for day, rate in zip(history.dates[1:], history.risk_free_returns)
            if day.startswith("2017-07")]
    assert len(july) == 20
    assert all(rate > 0 for rate in july)
    # French monthly RF is 0.07% for July 2017, distributed across all 20 trading sessions
    assert (1 + july[0]) ** len(july) - 1 == pytest.approx(.0007)
