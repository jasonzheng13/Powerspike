"""Find market IDs to paste into config/markets.yaml.

    python scripts/find_markets.py fed            # Polymarket markets whose question contains "fed"
    python scripts/find_markets.py --kalshi KXFED # Kalshi markets in a series
    python scripts/find_markets.py --event-slug largest-company-end-of-december-2026
                                                  # every leg of a Polymarket event, for companies.yaml
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powerspike.ingest import kalshi, polymarket  # noqa: E402

if len(sys.argv) >= 3 and sys.argv[1] == "--event-slug":
    for m in polymarket.event_markets(sys.argv[2]):
        px = "    ?" if m["yes_price"] is None else f"{m['yes_price']:5.1%}"
        print(f"{(m['question'] or '')[:72]:72} yes={px} vol={m['volume']:>10.0f}\n    YES token_id: {m['token_id']}")
elif len(sys.argv) >= 3 and sys.argv[1] == "--kalshi":
    for m in kalshi.list_markets(sys.argv[2]):
        print(f"{m['ticker']:40} vol={m['volume']!s:>10}  {m['title']}")
else:
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    for m in polymarket.search_markets(q, limit=25):
        yes = (m["token_ids"] or ["?"])[0]
        print(f"{m['question'][:70]:70}  24h vol={m['volume24hr']}\n    YES token_id: {yes}")
