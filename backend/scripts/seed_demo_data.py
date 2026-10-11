"""Write the synthetic demo market into the cache so the dashboard has something to show
before real data is pulled.   python scripts/seed_demo_data.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powerspike.ingest import cache  # noqa: E402
from powerspike.synthetic import demo_market  # noqa: E402

con = cache.connect()
print("synthetic-demo:", cache.save(con, "synthetic-demo", demo_market()), "bars")
