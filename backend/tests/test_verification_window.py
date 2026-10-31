from backend.history import AssetHistory, History
from scripts.verify_recommendations import window


def test_holdout_window_keeps_the_boundary_close_and_matching_return_dates():
    history = History(source="historical", dataset_version="fixture", notice="Fixture",
                      value_basis="Fixture", time_basis="Fixture", sessions=list(range(5)),
                      dates=["2014-12-30", "2014-12-31", "2015-01-02", "2015-01-05", "2015-01-06"],
                      risk_free_returns=[.001, .002, .003, .004],
                      assets=[AssetHistory(symbol="A", name="A", index_values=[100, 101, 102, 103, 104])])
    train, held_out = window(history, 0, 2), window(history, 2, 5)
    assert train.dates[-1] < held_out.dates[0]
    assert train.assets[0].index_values == [100, 101]
    assert held_out.assets[0].index_values == [102, 103, 104]
    assert held_out.risk_free_returns == [.003, .004]
    assert held_out.sessions == [0, 1, 2]
    assert history.assets[0].index_values == [100, 101, 102, 103, 104]
