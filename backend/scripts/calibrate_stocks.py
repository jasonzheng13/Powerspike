"""Calibrate the stock detector (detect.EQUITY_PARAMS) on real tickers.

    python scripts/calibrate_stocks.py                 # jump counts per ticker over a small parameter grid
    python scripts/calibrate_stocks.py --show          # plus every spike under the current EQUITY_PARAMS
    python scripts/calibrate_stocks.py --contracts     # also pull every configured company contract (pre-warms the cache)
    python scripts/calibrate_stocks.py --tickers NVDA,AMD --days 7

Target: 3-10 jumps per ticker per 30 days. Pulls go through stocks.ensure_bars, so the API's
cache is warm afterwards.
"""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powerspike import config, stocks  # noqa: E402
from powerspike.detect import EQUITY_PARAMS, detect_jumps  # noqa: E402
from powerspike.ingest import cache  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--tickers", default="NVDA,TSLA,AAPL,MSFT")
ap.add_argument("--days", type=int, default=30)
ap.add_argument("--show", action="store_true")
ap.add_argument("--contracts", action="store_true")
args = ap.parse_args()

con = cache.connect()
tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
frames = {}
for t in tickers:
    try:
        frames[t] = stocks.ensure_bars(con, t, args.days)
        print(f"{t}: {len(frames[t])} bars, {frames[t].index[0]:%Y-%m-%d} .. {frames[t].index[-1]:%Y-%m-%d %H:%M} UTC")
    except Exception as e:
        print(f"{t}: FAILED {type(e).__name__}: {e}")

grid = [(ms, th) for ms in (0.001, 0.002, 0.004) for th in (3.0, 4.0, 6.0)]
print(f"\njumps per ticker over {args.days} days (min_scale x score_threshold); current = "
      f"{EQUITY_PARAMS.min_scale} x {EQUITY_PARAMS.score_threshold}")
print(f"{'min_scale':>10} {'thresh':>7} " + " ".join(f"{t:>6}" for t in frames))
for ms, th in grid:
    p = replace(EQUITY_PARAMS, min_scale=ms, score_threshold=th)
    counts = [len(detect_jumps(df, t, p)) for t, df in frames.items()]
    mark = " <-" if (ms, th) == (EQUITY_PARAMS.min_scale, EQUITY_PARAMS.score_threshold) else ""
    print(f"{ms:>10} {th:>7} " + " ".join(f"{c:>6}" for c in counts) + mark)

if args.show:
    for t, df in frames.items():
        print(f"\n{t} under EQUITY_PARAMS:")
        for s in detect_jumps(df, t, EQUITY_PARAMS):
            print(f"  {s.start:%m-%d %H:%M} -> {s.peak:%H:%M}  {s.headline():48}  z={s.z:5.1f} vol={s.volume_ratio:4.1f}x "
                  f"persist={s.persistence:.2f} score={s.score:5.1f}")

if args.contracts:
    print("\ncontracts:")
    for c in config.companies():
        for k in c["contracts"][:1]:
            try:
                last = stocks.ensure_contract(con, k, args.days)
                n = len(cache.load(con, k["id"]))
                print(f"  {c['ticker']:5} {k['id']:45} bars={n:>6} last={last}")
            except Exception as e:
                print(f"  {c['ticker']:5} {k['id']:45} FAILED {type(e).__name__}: {e}")
