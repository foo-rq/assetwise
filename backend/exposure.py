"""Separate dated composition evidence from return-path calculations"""

import json
from collections import defaultdict
from functools import lru_cache
from math import isfinite
from pathlib import Path


@lru_cache(maxsize=1)
def load_exposures():
    data = json.loads((Path(__file__).parent / "data" / "exposures.json").read_text(encoding="utf-8"))
    for fund in data["funds"].values():
        values = fund["holdings_percent"].values()
        if any(not isfinite(value) or value < 0 for value in values) or sum(values) > 100:
            raise ValueError("Invalid fund composition weights")
    return data


def portfolio_exposure(weights, data=None):
    # A partial holdings file proves some overlap but cannot prove that missing positions are absent
    data = load_exposures() if data is None else data
    sectors = defaultdict(float)
    paths = defaultdict(dict)
    unknown = 0.0
    sources = []
    for symbol, weight in weights.items():
        if weight <= 0:
            continue
        if symbol in data["sectors"]:
            sectors[data["sectors"][symbol]] += weight
            paths[symbol][symbol] = weight
        elif symbol in data["funds"]:
            fund = data["funds"][symbol]
            known = 0.0
            for underlying, portion in fund["holdings_percent"].items():
                # A 50% fund allocation holding 4% Apple gives 2% portfolio exposure to Apple
                exposure = weight * portion / 100
                paths[underlying][symbol] = exposure
                sector = data["sectors"].get(underlying)
                if sector:
                    sectors[sector] += exposure
                    known += exposure
            unknown += weight - known
            sources.append(dict(symbol=symbol, as_of=fund["as_of"], url=fund["source_url"],
                                holdings_coverage_percent=sum(fund["holdings_percent"].values()),
                                complete=fund["complete"]))
        else:
            unknown += weight
    # Multiple routes to the same security reveal duplicate exposure without counting it twice
    overlaps = [dict(symbol=symbol, name=data.get("security_names", {}).get(symbol, symbol),
                     total_percent=sum(routes.values()), routes=routes)
                for symbol, routes in paths.items() if len(routes) > 1]
    overlaps.sort(key=lambda item: (-item["total_percent"], item["symbol"]))
    return dict(sectors=dict(sorted(sectors.items(), key=lambda item: (-item[1], item[0]))),
                unclassified_percent=max(0, unknown), cash_percent=max(0, 100-sum(weights.values())),
                overlaps=overlaps, sources=sources, as_of=data["as_of"],
                classification_note=data.get("classification_note", "Broad business groups"),
                note="Dated equity schedules exclude cash and other net assets inside funds. Uncovered funds may add further overlap; no identified overlap is not proof of diversification.")
