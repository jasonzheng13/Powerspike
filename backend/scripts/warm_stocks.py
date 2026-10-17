"""Warm the API's caches so a demo never starts cold.

    python scripts/warm_stocks.py                      # every company in config/companies.yaml
    python scripts/warm_stocks.py NVDA TSLA --days 30

For each ticker: bars, contract, spikes, one explanation per spike (one GDELT request each, spaced
GDELT_MIN_INTERVAL apart, so this is slow), the AI climate and the outlook. Run it in the same
process as the API to share its memo? No: the API is a separate process, so this warms the *bar
cache on disk* and prints timings; the per-spike explanations are memoised only in the API process.
Point it at a running API instead with --api http://localhost:8000 to warm that process's memos.
"""

import argparse
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powerspike import config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("tickers", nargs="*")
ap.add_argument("--days", type=int, default=30)
ap.add_argument("--api", default="http://localhost:8000")
args = ap.parse_args()

tickers = [t.upper() for t in args.tickers] or [c["ticker"] for c in config.companies()]
t0 = time.monotonic()
print(f"warming {args.api} for {', '.join(tickers)} ({args.days} days)")
r = httpx.get(f"{args.api}/api/climate", timeout=120)
print(f"  climate: {r.status_code}, {len(r.json().get('headlines', []))} headlines, {time.monotonic() - t0:.0f}s")
for t in tickers:
    t1 = time.monotonic()
    try:
        spikes = httpx.get(f"{args.api}/api/stocks/{t}/spikes", params={"days": args.days}, timeout=600).json()["spikes"]
        for s in spikes:
            httpx.post(f"{args.api}/api/stocks/{t}/explain", params={"days": args.days},
                       json={"spike": {"kind": s["kind"], "peak": s["peak"]}}, timeout=600)
        o = httpx.get(f"{args.api}/api/stocks/{t}/outlook", params={"days": args.days}, timeout=600).json()
        print(f"  {t}: {len(spikes)} spikes, outlook {o.get('label')} ({o.get('confidence', 0):.0%}), {time.monotonic() - t1:.0f}s")
    except Exception as e:
        print(f"  {t}: FAILED {type(e).__name__}: {str(e)[:120]}")
print(f"done in {time.monotonic() - t0:.0f}s")
