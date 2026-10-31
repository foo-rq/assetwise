"""Rebuild the pinned historical snapshot without requiring API credentials"""

import csv
import hashlib
import io
import json
from datetime import date
from math import isfinite
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "backend" / "data"
DATASET = "borismarjanovic/price-volume-data-for-all-us-stocks-etfs"
SOURCE_URL = f"https://www.kaggle.com/datasets/{DATASET}"
START, END = "2013-01-02", "2017-11-10"

# Keep the supported universe explicit rather than silently adding every archived security
ASSETS = (
    ("AAPL", "Apple", "Stocks"),
    ("MSFT", "Microsoft", "Stocks"),
    ("JNJ", "Johnson & Johnson", "Stocks"),
    ("XOM", "Exxon Mobil", "Stocks"),
    ("SPY", "SPDR S&P 500 ETF", "ETFs"),
    ("QQQ", "Invesco QQQ ETF", "ETFs"),
    ("IWM", "iShares Russell 2000 ETF", "ETFs"),
    ("EFA", "iShares MSCI EAFE ETF", "ETFs"),
    ("EEM", "iShares MSCI Emerging Markets ETF", "ETFs"),
    ("IEF", "iShares 7-10 Year Treasury Bond ETF", "ETFs"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF", "ETFs"),
    ("SHY", "iShares 1-3 Year Treasury Bond ETF", "ETFs"),
    ("AGG", "iShares Core US Aggregate Bond ETF", "ETFs"),
    ("LQD", "iShares Investment Grade Corporate Bond ETF", "ETFs"),
    ("GLD", "SPDR Gold Shares", "ETFs"),
    ("VNQ", "Vanguard Real Estate ETF", "ETFs"),
    ('AMZN', 'Amazon', 'Stocks'),
    ('GOOGL', 'Alphabet Class A', 'Stocks'),
    ('NVDA', 'NVIDIA', 'Stocks'),
    ('TSLA', 'Tesla', 'Stocks'),
    ('JPM', 'JPMorgan Chase', 'Stocks'),
    ('BAC', 'Bank of America', 'Stocks'),
    ('V', 'Visa', 'Stocks'),
    ('WMT', 'Walmart', 'Stocks'),
    ('KO', 'Coca-Cola', 'Stocks'),
    ('PEP', 'PepsiCo', 'Stocks'),
    ('PG', 'Procter & Gamble', 'Stocks'),
    ('DIS', 'Walt Disney', 'Stocks'),
    ('UNH', 'UnitedHealth Group', 'Stocks'),
    ('PFE', 'Pfizer', 'Stocks'),
    ('CVX', 'Chevron', 'Stocks'),
    ('CAT', 'Caterpillar', 'Stocks'),
    ('BA', 'Boeing', 'Stocks'),
    ('NEE', 'NextEra Energy', 'Stocks'),
    ('VTI', 'Vanguard Total Stock Market ETF', 'ETFs'),
    ('IVV', 'iShares Core S&P 500 ETF', 'ETFs'),
    ('VEA', 'Vanguard FTSE Developed Markets ETF', 'ETFs'),
    ('VWO', 'Vanguard FTSE Emerging Markets ETF', 'ETFs'),
    ('BND', 'Vanguard Total Bond Market ETF', 'ETFs'),
    ('TIP', 'iShares TIPS Bond ETF', 'ETFs'),
    ('HYG', 'iShares iBoxx High Yield Corporate Bond ETF', 'ETFs'),
    ('SLV', 'iShares Silver Trust', 'ETFs'),
    ('XLF', 'Financial Select Sector SPDR Fund', 'ETFs'),
    ('XLK', 'Technology Select Sector SPDR Fund', 'ETFs'),
    ('XLV', 'Health Care Select Sector SPDR Fund', 'ETFs'),
    ('XLE', 'Energy Select Sector SPDR Fund', 'ETFs'),
)


def parse_prices(raw):
    # Reject duplicate or unsorted dates before filtering so source errors cannot disappear
    prices = {}
    previous = ""
    # Decode a possible UTF-8 byte-order mark before matching the CSV's Date and Close headers
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        session = row["Date"]
        if date.fromisoformat(session).isoformat() != session or session <= previous:
            raise ValueError("Source dates must be unique ascending ISO dates")
        # Remember all source dates, including dates outside our selected analysis window
        previous = session
        close = float(row["Close"])
        if not isfinite(close) or close <= 0:
            raise ValueError("Source closes must be positive finite numbers")
        if START <= session <= END:
            # Keep the published close unchanged, avoiding another dividend or split adjustment
            prices[session] = close
    if len(prices) < 252:
        raise ValueError("At least 252 observations are required for this archive")
    return prices


def main():
    # Pin version 3 and record original byte hashes so later rebuilds can identify changes
    with urlopen(f"https://www.kaggle.com/api/v1/datasets/view/{DATASET}", timeout=30) as response:
        metadata = json.load(response)
    if metadata.get("licenseName") != "CC0: Public Domain":
        # A changed licence needs review rather than an unnoticed change to redistribution terms
        raise ValueError("The publisher licence changed; review before importing")
    paths, provenance = {}, []
    for symbol, name, folder in ASSETS:
        # Kaggle separates stock and ETF files and stores ticker filenames in lowercase
        filename = f"Data/{folder}/{symbol.lower()}.us.txt"
        query = urlencode({"fileName": filename, "datasetVersionNumber": 3})
        with urlopen(f"https://www.kaggle.com/api/v1/datasets/download/{DATASET}?{query}", timeout=30) as response:
            # Read 1 byte beyond our cap so oversized responses are detectable without loading everything
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Unexpected source file size")
        paths[symbol] = parse_prices(raw)
        # Hash the complete downloaded file, not only the dates retained in the smaller snapshot
        provenance.append({"symbol": symbol, "name": name, "file": filename,
                           "sha256": hashlib.sha256(raw).hexdigest()})
        print(f"Validated {symbol}: {len(paths[symbol])} observations")

    # SPY supplies the reference sessions; missing dates reject an asset rather than fill gaps
    dates = list(paths["SPY"])
    if dates[0] != START or dates[-1] != END:
        raise ValueError("The requested date range is incomplete")
    for symbol, values in paths.items():
        # Comparing ordered date lists also catches an extra or missing internal trading session
        if list(values) != dates:
            raise ValueError(f"{symbol} does not match the reference calendar")
    # Dictionary insertion order matches the validated dates, so each price keeps its correct session
    snapshot = {"dates": dates, "assets": [
        {"symbol": symbol, "name": name, "index_values": list(paths[symbol].values())}
        for symbol, name, _ in ASSETS
    ]}
    # Preserve source details separately from prices so the archive can be inspected and rebuilt
    # Publisher claims stay labelled as claims rather than being promoted to an independent audit
    manifest = {
        "dataset_version": "archive-2017-v1", "source_url": SOURCE_URL,
        "publisher": "Boris Marjanovic", "source_version": 3,
        "licence": metadata["licenseName"], "retrieved_on": date.today().isoformat(),
        "start": START, "end": END, "observations": len(dates),
        "adjustment": "Publisher states dividends/splits adjusted; not independently verified",
        "limitations": "Fixed surviving-security selection, not a point-in-time universe or investment recommendation",
        "files": provenance,
    }
    # Write only after every selected file passes, preserving the old snapshot on download failure
    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(snapshot, separators=(",", ":")) + "\n").encode()
    # Runtime loading checks this exact payload hash against the manifest before returning any data
    manifest["snapshot_sha256"] = hashlib.sha256(payload).hexdigest()
    (OUTPUT / "historical.json").write_bytes(payload)
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
