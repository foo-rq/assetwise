"""Reconcile extracted 2017 filing schedules before publishing fund composition"""

import json
import re
from pathlib import Path

FOLDER = Path(__file__).resolve().parents[1] / "backend" / "data"
SECTOR_SOURCE = "https://www.sec.gov/Archives/edgar/data/1064641/000119312517361289/d410381dncsr.htm"


def source_lines(pattern):
    # Numbered extracts may overlap; identical source lines represent only 1 holding
    lines = {}
    for path in FOLDER.glob(pattern):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"L(\d+): (.*)", line)
            if match:
                lines[int(match[1])] = match[2]
    return sorted(lines.items())


def clean_name(name):
    # Footnote letters identify non-income-producing shares, not a different security
    name = re.sub(r"\^?\{?\([a-z]\)\}?", "", name)
    return " ".join(name.replace("(The)", "").split()).strip()


def identity(name):
    # Keep share classes distinct while ignoring punctuation and case across the 2 publishers
    return re.sub(r"[^a-z0-9]", "", clean_name(name).lower())


def holdings(lines, start, end, shares_first=False):
    rows = []
    pending = ""
    previous = ""
    for number, line in lines:
        if not start <= number <= end:
            continue
        if pending:
            line = pending + " " + line
            pending = ""
        cells = [clean_name(cell) for cell in line.split("|") if cell.strip() not in ("", "$")]
        # A name wrapped onto the next line has shares but no closing market-value cell yet
        if shares_first and len(cells) == 2 and re.fullmatch(r"[\d,]+", cells[0]):
            pending = line
            continue
        if (len(cells) == 3 and re.fullmatch(r"[\d,]+", cells[-1])
                and re.fullmatch(r"[\d,]+", cells[0 if shares_first else 1])):
            # A wrapped share class belongs to the company on the immediately preceding line
            if not shares_first and cells[0].startswith("Class "):
                if not previous or "|" in previous:
                    raise ValueError(f"Missing company before share class at line {number}")
                cells[0] = clean_name(previous) + " " + cells[0]
            rows.append((cells[1 if shares_first else 0], int(cells[-1].replace(",", ""))))
        previous = line
    return rows


def main():
    data = json.loads((FOLDER / "exposures.json").read_text())
    names = {
        "AAPL": "Apple, Inc.", "MSFT": "Microsoft Corp.", "JNJ": "Johnson & Johnson",
        "XOM": "Exxon Mobil Corp.", "AMZN": "Amazon.com, Inc.", "GOOGL": "Alphabet, Inc. Class A",
        "NVDA": "NVIDIA Corp.", "TSLA": "Tesla, Inc.", "JPM": "JPMorgan Chase & Co.",
        "BAC": "Bank of America Corp.", "V": "Visa, Inc. Class A", "WMT": "Wal-Mart Stores, Inc.",
        "KO": "Coca-Cola Co.", "PEP": "PepsiCo, Inc.", "PG": "Procter & Gamble Co.",
        "DIS": "Walt Disney Co.", "UNH": "UnitedHealth Group, Inc.", "PFE": "Pfizer, Inc.",
        "CVX": "Chevron Corp.", "CAT": "Caterpillar, Inc.", "BA": "Boeing Co.", "NEE": "NextEra Energy, Inc."
    }
    aliases = {identity(name): symbol for symbol, name in names.items()}
    # SPY prints PPoG; the same-date sector schedule identifies PPG at the same per-share value
    aliases["ppogindustriesinc"] = "ppgindustriesinc"
    data["security_names"] = dict(names)

    def add_fund(symbol, rows, total, nav, url):
        # Reconcile exact reported dollars before accepting the claim of complete equity coverage
        actual = sum(value for _, value in rows)
        if actual != total:
            raise ValueError(f"{symbol}: extracted {actual}, report total {total}, difference {total-actual}")
        values = {}
        for name, value in rows:
            key = aliases.get(identity(name), identity(name))
            if key in values:
                raise ValueError(f"Duplicate security in {symbol}: {name}")
            data["security_names"][key] = name
            values[key] = value
        data["funds"][symbol] = dict(source_url=url, as_of="2017-09-30", complete=True,
            coverage_basis="Complete common-stock schedule; cash and other net assets excluded",
            net_assets_usd=nav, holdings_value_usd=values,
            holdings_percent={key: value/nav*100 for key, value in values.items()})
        print(f"{symbol}: reconciled {len(rows)} equity positions")

    add_fund("SPY", holdings(source_lines("spy-2017-holdings-*.txt"), 64, 699),
             243028279842, 243299575525, data["funds"]["SPY"]["source_url"])
    add_fund("QQQ", holdings(source_lines("qqq-2017-holdings-*.txt"), 66, 298, True),
             52162471046, 52177634729, data["funds"]["QQQ"]["source_url"])
    schedules = [
        ("XLY", "Consumer discretionary", 1599, 1757, 11493355325, 11518585004),
        ("XLP", "Consumer staples", 1872, 1939, 8783650030, 8808903063),
        ("XLE", "Energy", 2044, 2092, 16603445488, 16617834949),
        ("XLF", "Financials", 2197, 2292, 27369288798, 27418851716),
        ("XLV", "Healthcare", 2400, 2492, 17689194289, 17711626587),
        ("XLI", "Industrials", 2598, 2722, 11029882600, 11055679261),
        ("XLB", "Materials", 2824, 2873, 4041605708, 4051401851),
        ("XLRE", "Real estate", 2975, 3022, 2349423969, 2354817940),
        ("XLK", "Technology and telecom", 3124, 3237, 17805503596, 17832444304),
        ("XLU", "Utilities", 3342, 3392, 7741102453, 7775414261),
    ]
    lines = source_lines("sectors-2017-source-*.txt")
    for symbol, sector, start, end, total, nav in schedules:
        rows = holdings(lines, start, end)
        add_fund(symbol, rows, total, nav, SECTOR_SOURCE)
        # These dated sector portfolios classify the actual shares in the same month's SPY filing
        for name, _ in rows:
            data["sectors"][aliases.get(identity(name), identity(name))] = sector
    data["classification_note"] = "2017 Select Sector SPDR groups; Technology includes telecom in this classification"
    data["classification_source"] = SECTOR_SOURCE
    # Write only after all schedules reconcile, preserving the last usable file on parser failures
    (FOLDER / "exposures.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
