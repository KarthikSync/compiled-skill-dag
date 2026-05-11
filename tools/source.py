"""Mock source-search provider. Reads canned data from the fixture dict.

Returns a non-empty dict on a hit, or {} on a miss. The runner stamps the
artifact `status` based on emptiness.
"""


def search_symbol(fixture, symbol):
    if not symbol:
        return {}
    hit = fixture.get("source", {}).get(symbol)
    if not hit:
        return {}
    return {"symbol": symbol, **hit}
