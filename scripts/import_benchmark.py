"""Import dated daily Treasury-bill returns for the bundled price archive"""

import csv
import hashlib
import io
import json
import zipfile
from collections import Counter
from datetime import date
from math import expm1, isfinite, log1p
from pathlib import Path
from urllib.request import urlopen

URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip"
MONTHLY_URL = URL.replace("_daily_CSV", "_CSV")
SOURCE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library/f-f_factors.html"
FOLDER = Path(__file__).resolve().parents[1] / "backend" / "data"


def parse_benchmark(daily_raw, monthly_raw, dates):
    # Daily RF rounds small rates to zero, so use its full calendar with monthly RF instead
    calendar = set()
    for row in csv.reader(io.StringIO(daily_raw.decode("utf-8-sig"))):
        key = row[0].strip() if row else ""
        if len(row) != 5 or len(key) != 8 or not key.isdigit():
            continue
        key = f"{key[:4]}-{key[4:6]}-{key[6:8]}"
        date.fromisoformat(key)
        if key in calendar:
            raise ValueError("Duplicate benchmark date")
        calendar.add(key)
    counts = Counter(day[:7] for day in calendar)
    monthly = {}
    for row in csv.reader(io.StringIO(monthly_raw.decode("utf-8-sig"))):
        key = row[0].strip() if row else ""
        # The same file ends with annual observations, which must never be treated as months
        if len(row) != 5 or len(key) != 6 or not key.isdigit():
            continue
        key = f"{key[:4]}-{key[4:6]}"
        date.fromisoformat(f"{key}-01")
        if key in monthly:
            raise ValueError("Duplicate benchmark month")
        value = float(row[4]) / 100
        if not isfinite(value) or not -0.1 < value < 0.1:
            raise ValueError("Invalid monthly Treasury-bill return")
        monthly[key] = value
    if any(day not in calendar or day[:7] not in monthly for day in dates):
        raise ValueError("Benchmark does not cover every portfolio return date")
    # Count the whole month, including days outside our price window, before selecting return dates
    return [expm1(log1p(monthly[day[:7]]) / counts[day[:7]]) for day in dates]


def main():
    dates = json.loads((FOLDER / "historical.json").read_text())["dates"][1:]
    payloads, hashes = [], []
    for url, filename in [(URL, "F-F_Research_Data_Factors_daily.csv"),
                          (MONTHLY_URL, "F-F_Research_Data_Factors.csv")]:
        with urlopen(url, timeout=30) as response:
            raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError("Unexpected benchmark archive size")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            payloads.append(archive.read(filename))
        hashes.append(hashlib.sha256(raw).hexdigest())
    result = dict(dates=dates, returns=parse_benchmark(*payloads, dates), source_url=SOURCE,
                  name="Kenneth French monthly RF converted to daily equivalent returns",
                  method="(1 + monthly RF) ** (1 / full-month trading days) - 1; retrospective reference, not observed daily returns",
                  retrieved_on=date.today().isoformat(), daily_source_sha256=hashes[0],
                  monthly_source_sha256=hashes[1])
    # Save a separate source snapshot so adding the benchmark does not invalidate saved allocations
    (FOLDER / "benchmark.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Validated {len(dates)} daily benchmark returns")


if __name__ == "__main__":
    main()
