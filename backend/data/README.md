# Corrected historical snapshot

`historical.json` version `archive-2017-v2` contains 56,396 Yahoo Finance adjusted closes across 46 assets and 1,226 dates, 2 January 2013 through 10 November 2017. It replaces the original Kaggle price snapshot after daily-return discrepancies were found. The original snapshot and manifest are preserved locally in `docs/archive-2017-v1-backup`.

Prices include provider adjustments for splits and distributions. Dividends are not added again. See [Yahoo's adjusted-close definition](https://in.help.yahoo.com/kb/adjusted-close-sln28256.html). Provider terms apply; this replacement does not inherit the previous publisher's CC0 licence. Public redistribution rights have not been established by this correction.

The importer checks response identity, USD currency, positive finite prices, unique aligned dates and the full benchmark calendar. For every return, adjustment-factor changes must reconcile to the recorded dividend/capital-gain amount and preceding split-adjusted close within 1 basis point. This includes dates without a distribution, when the factor must remain constant. All 46 training files and all 46 extended evaluation files pass this check. Runtime verifies the snapshot checksum. These checks resolve the known input mismatch by using one consistent price series; they do not certify every provider observation independently.

The former SPY return on 13 March 2015 differed by about 2 percentage points. The corrected series matches the ratio of the independently recorded closes, 205.83/207.10, within 0.0001 percentage points. A regression test protects this case. Apple's June 2014 split is also checked for a false crash.

Rebuild with `.venv/Scripts/python.exe -m scripts.import_adjusted_history docs/review-20261028/independent-prices`. The retained provider responses and each file hash make the local rebuild reproducible. `scripts/import_history.py` is the legacy Kaggle importer and does not rebuild this version. Serving the snapshot needs no network access or provider credentials.

The date window and supported symbols did not change. Old allocation weights can be loaded and recalculated with corrected prices; their original SQL snapshots remain preserved. No historical calculated metrics are stored as if they were current.

This remains a selected surviving-security universe and revised historical data, not a point-in-time reconstruction of the whole market. No post-cutoff price is used in forecast fitting.

## Benchmark and dated holdings

`benchmark.json` contains 1,225 daily risk-free returns aligned to the archive return dates, sourced from the [Kenneth French daily factors](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library/f-f_factors.html). `scripts/import_benchmark.py` reconstructs daily equivalents from monthly RF using `(1 + monthly_return) ** (1 / full_month_trading_days) - 1`. The daily file supplies the full trading calendar, including days outside the selected price window. Its published RF column rounded every rate in this window to zero, so it is not used as the return input. This retrospective reference uses whole-month information and is not an observed daily return or an executable lending strategy. Monthly source values are themselves rounded. Missing dates/months and duplicate or invalid inputs are rejected. Both downloaded files have recorded hashes.

`exposures.json` contains 1,117 equity positions across 12 schedules dated 30 September 2017. `scripts/import_exposures.py` reconciles extracted dollar values to each report total before writing. Fund weights use reported net assets, so cash and other net assets outside the equity schedule are not silently redistributed. Sources:

- [SPY: 505 equity positions](https://www.sec.gov/Archives/edgar/data/884394/000119312517355427/d418234dn30d.htm)
- [QQQ: 107 equity positions](https://www.sec.gov/Archives/edgar/data/1067839/000119312518001208/d432220dn30b2.htm)
- [10 Select Sector SPDR schedules: 505 positions](https://www.sec.gov/Archives/edgar/data/1064641/000119312517361289/d410381dncsr.htm)

The source text is retained for reproducible extraction. The SPY report's `PPoG` name is mapped to PPG using the matching dated sector schedule and per-share value. Share classes remain separate. The sector groups reflect 2017 classifications, including telecom within technology. Only SPY, QQQ, XLF, XLK, XLV and XLE also belong to the price universe; the other sector schedules support classification. Uncovered funds and unclassified securities remain explicit. Complete means the listed equity schedule, not every balance-sheet asset or every date in the price archive.

The price snapshot is marked `-text` in `.gitattributes` because runtime validates its exact byte checksum. Rebuilds require restarting the API's process-local caches.
