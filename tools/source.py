"""Mock source-search provider. Reads canned data from the fixture dict."""


def search_symbol(fixture, symbol):
    if not symbol:
        return {"symbol": symbol, "found": False}
    hit = fixture.get("source", {}).get(symbol)
    if not hit:
        return {"symbol": symbol, "found": False}
    return {"symbol": symbol, "found": True, **hit}
