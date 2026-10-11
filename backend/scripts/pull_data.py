"""Pull history for every configured market, plus the ETFs in exposure.yaml, into the bar cache
(Tiger Cloud when DD_DATABASE_URL is set, else the local DuckDB file).

    python scripts/pull_data.py --days 7
    python scripts/pull_data.py --start 2025-12-09 --end 2025-12-11   # a specific demo window
    python scripts/pull_data.py --no-equities                          # markets only
    python scripts/pull_data.py --equities-only --days 7               # refresh the ETFs only

ETF bars come from yfinance: 1-minute for the last ~30 days, 5-minute (carried forward onto the
1-minute grid) for the ~30 days before that, nothing older. They are cached as `equity:<TICKER>`.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from powerspike import config, expose  # noqa: E402
from powerspike.ingest import cache, equities, fetch, to_grid  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=7)
ap.add_argument("--start")
ap.add_argument("--end")
ap.add_argument("--no-equities", action="store_true", help="skip the ETF pull")
ap.add_argument("--equities-only", action="store_true", help="skip the market pull")
args = ap.parse_args()

end = pd.Timestamp(args.end, tz="UTC") if args.end else pd.Timestamp.now(tz="UTC")
start = pd.Timestamp(args.start, tz="UTC") if args.start else end - pd.Timedelta(days=args.days)

# cache.connect() per item: it returns the process's one connection unless that dropped (Tiger
# closes idle sockets, and macOS ran out of ephemeral ports mid-pull once), in which case it reopens.
for m in [] if args.equities_only else config.markets():
    con = cache.connect()
    if "TODO" in str(m.get("token_id", "")) + str(m.get("ticker", "")):
        print(f"skip {m['id']}: fill in its IDs in config/markets.yaml")
        continue
    try:
        n = cache.save(con, m["id"], to_grid(fetch(m, start, end)))
        print(f"{m['id']}: {n} bars")
    except Exception as e:
        print(f"{m['id']}: FAILED {type(e).__name__}: {e}")

if args.no_equities:
    sys.exit(0)
plan = equities.fetch_plan(start, end)
if not plan:
    print(f"skip equities: yfinance has no bars older than ~60 days (window {start.date()}..{end.date()})")
    sys.exit(0)
for s, e, iv in plan:
    print(f"equities: {s.date()}..{e.date()} at {iv}")
for t in expose.all_etfs():
    try:
        con = cache.connect()
        n = cache.save(con, equities.equity_id(t), equities.session_grid(equities.backfill_bars(t, start, end)))
        print(f"{equities.equity_id(t)}: {n} bars")
    except Exception as e:
        print(f"{equities.equity_id(t)}: FAILED {type(e).__name__}: {e}")
