# Assetwise

A hypothetical portfolio builder with a Python API. The current version has an interactive design prototype, reproducible simulated history and portfolio return/volatility calculations. Market data and SQL storage are not implemented yet.

## Run the API on Windows

Use Python 3.14. Run these commands in PowerShell from the repository folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

The virtual environment keeps this project's packages separate from other Python projects. Using its Python directly avoids PowerShell activation-policy changes. Create the environment once; repeat the install command when dependencies change.

The requirements files pin the tested packages, including indirect dependencies in `requirements-lock.txt`. For an API-only install without test tools, use `requirements.txt` instead of `requirements-dev.txt`.

Open [the health endpoint](http://127.0.0.1:8000/health). It should return:

```json
{"status":"ok"}
```

This confirms that the API responds. It does not check a database or market-data service. [Interactive API documentation](http://127.0.0.1:8000/docs) is available while the server runs. Press Ctrl+C in the terminal to stop it.

If port 8000 is already in use, change `--port 8000` to `--port 8001` and use that port in the browser links. If `python` is not found, install Python from [python.org](https://www.python.org/downloads/) and reopen PowerShell.

## Simulated history

Open [the demo history endpoint](http://127.0.0.1:8000/demo/history) while the API runs. It returns 4 fictional assets with 253 index values each: a starting value of 100 followed by 252 synthetic daily changes. Refreshing returns the same dataset, identified as `demo-v1` with `source: simulated`.

The generator combines a shared movement with a separate movement per asset. Its coefficients are invented and its movements are bounded. This is demonstration data, not a calibrated market model, historical performance or a forecast. Session numbers have no calendar dates. The indices have no currency or separate dividends. Asset names do not represent real securities.

The prototype is not connected to this endpoint yet. Change the dataset version when changing its generation rules so saved comparisons can identify their inputs.

## Portfolio calculations

`POST /demo/analysis` accepts percentages by fictional asset identifier:

```json
{"weights": {"DEMO-EQ1": 60, "DEMO-BOND": 30}}
```

This leaves 10% in cash earning 0%. An empty `weights` object means 100% cash. Negative weights, totals above 100%, unknown identifiers and nonnumeric values are rejected with HTTP 422. Weights are never silently normalised.

The response includes session returns, cumulative return, annualised arithmetic return and annualised volatility. Results use decimals: `0.05` means 5%. Every response identifies its simulated source and dataset version.

Calculation assumptions:

- Asset return = current index/previous index minus 1
- Portfolio return = sum of asset returns multiplied by their decimal weights
- Weights stay constant through daily rebalancing, excluding taxes and trading costs
- Cumulative return compounds the session returns
- Annualised arithmetic return = average session return multiplied by 252, not compounded growth or a forecast
- Annualised volatility = sample standard deviation of portfolio returns multiplied by the square root of 252. This equals the covariance-based calculation for aligned constant weights

The engine currently requires consecutive synthetic sessions and at least 3 index observations, giving 2 returns for sample volatility. That is a mathematical minimum, not a sufficient history requirement for real investment analysis. Missing held-asset values are rejected rather than filled. Drawdown, Sharpe ratio and real-calendar alignment are not implemented yet.

The [interactive API documentation](http://127.0.0.1:8000/docs) can send the example request. Manual inspection of raw numbers is optional; automated tests verify hand-calculated cases. The visible prototype will use these calculations in a separate batch.

## Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

Tests cover the health response, simulation repeatability, hand-calculated returns, diversification, cash handling and invalid inputs. They need no API key or internet connection once the packages are installed.

## Open the prototype

Open `prototype/index.html` in a browser. It still runs independently of the API and uses sample allocations. See [the prototype review steps](prototype/README.md) for its supported interactions and limitations.

## Implementation references

- [FastAPI first steps](https://fastapi.tiangolo.com/tutorial/first-steps/)
- [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/)
- [Starlette TestClient](https://starlette.dev/testclient/) for the current HTTPX2 dependency
- [Python random generator](https://docs.python.org/3.14/library/random.html#notes-on-reproducibility) for seeded local random state
- [CFA Institute: portfolio risk and return](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/portfolio-risk-return-part-1) for portfolio variance and covariance
- [Python sample standard deviation](https://docs.python.org/3.14/library/statistics.html#statistics.stdev)
