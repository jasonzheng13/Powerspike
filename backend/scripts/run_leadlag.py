"""Lead/lag proof slide: for every cached market, did its spikes lead or lag the exposed ETFs?

    python scripts/run_leadlag.py            # one row per (spike, ETF), then the summary

Needs the market bars and the ETF bars in the cache (scripts/pull_data.py pulls both).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powerspike import config, leadlag  # noqa: E402
from powerspike.ingest import cache  # noqa: E402

rows = leadlag.run_cached(cache.connect(), config.markets())
for ll in rows:
    print(f"\n{ll.market_id}  {ll.kind}  peak {ll.peak:%Y-%m-%d %H:%M} UTC  ->  {ll.verdict}"
          + (f" (median {ll.median_lag:+.0f} min)" if ll.median_lag is not None else ""))
    for r in ll.reactions:
        note = "  (after hours, from next open)" if r.after_hours else "  (window ran into the next session)" if r.spans_close else ""
        if r.status != "reacted":
            print(f"    {r.ticker:<4} {'no data' if r.status == 'no_data' else 'no move'}{note}")
            continue
        tick = {True: "consistent", False: "DIVERGED", None: "no view"}[r.consistent]
        kind = "gap" if (r.after_hours and r.lag_min == 0) else "ret"
        print(f"    {r.ticker:<4} {r.lag_min:+5.0f} min  z {r.z:+5.1f}  {kind} {r.ret:+.2%}  {tick}{note}")
print("\nsummary (lags in trading minutes; overnight spikes are their own bucket):", leadlag.summary(rows))
