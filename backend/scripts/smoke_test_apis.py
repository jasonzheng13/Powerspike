"""First thing to run: confirm each data source answers and returns the fields we expect.

    python scripts/smoke_test_apis.py

The endpoints were written from the docs without live calls, so expect to fix a field name or two.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from powerspike.explain import headlines  # noqa: E402
from powerspike.expose import edgar_companies  # noqa: E402
from powerspike.ingest import kalshi, polymarket  # noqa: E402


def check(name, fn):
    try:
        out = fn()
        n = len(out) if hasattr(out, "__len__") else "?"
        print(f"OK   {name}: {n} rows")
        if hasattr(out, "head"):
            print(out.head(3).to_string(), "\n")
        elif out:
            print(f"     first: {out[0]}\n")
        return out
    except Exception as e:
        print(f"FAIL {name}: {type(e).__name__}: {e}\n")


end = pd.Timestamp.now(tz="UTC")
start = end - pd.Timedelta(days=1)

pm = check("Polymarket Gamma search 'fed'", lambda: polymarket.search_markets("fed", limit=5))
if pm and pm[0].get("token_ids"):
    check("Polymarket prices-history (1 day, 1-min)", lambda: polymarket.price_history(pm[0]["token_ids"][0], start, end))
    check("Polymarket trades volume (1 day)", lambda: polymarket.trades_volume(polymarket.condition_id(pm[0]["token_ids"][0]), start, end))

km = check("Kalshi markets in series KXFED", lambda: kalshi.list_markets("KXFED"))
if km:
    check("Kalshi candlesticks (1 day, 1-min)", lambda: kalshi.candlesticks("KXFED", km[0]["ticker"], start, end))

check("GDELT headlines 'Federal Reserve' (last day)", lambda: headlines('"Federal Reserve"', start, end))
check("EDGAR full-text search for fed_rates (needs SEC_USER_AGENT)", lambda: edgar_companies("fed_rates"))
