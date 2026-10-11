"""Copy every market in the local DuckDB cache into Tiger Cloud (DD_DATABASE_URL), one upsert per market.

    python scripts/migrate_cache.py                 # backend/data/cache.duckdb -> Tiger
    python scripts/migrate_cache.py path/to.duckdb

Safe to re-run: `bars` has PRIMARY KEY (market_id, ts), so rows are overwritten, not duplicated.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dislocation_desk import config  # noqa: E402
from dislocation_desk.ingest import cache, pg  # noqa: E402

if not config.DATABASE_URL:
    sys.exit("DD_DATABASE_URL is not set (see .env.example)")

src = cache.connect(sys.argv[1] if len(sys.argv) > 1 else config.CACHE_PATH)
dst = pg.connect(config.DATABASE_URL)
total = 0
for market_id in cache.cached_markets(src):
    n = cache.save(dst, market_id, cache.load(src, market_id))
    total += n
    print(f"{market_id}: {n} bars")
print(f"done: {total} bars now in Tiger ({len(cache.cached_markets(dst))} series)")
