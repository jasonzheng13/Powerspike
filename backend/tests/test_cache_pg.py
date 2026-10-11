"""Tiger Cloud backend round-trip. Runs only when DD_DATABASE_URL points at a service; otherwise skipped.

Writes under the `_test:` prefix and deletes those rows afterwards.
"""

import os

import numpy as np
import pandas as pd
import pytest

from powerspike.ingest import cache
from powerspike.synthetic import demo_market

pytestmark = pytest.mark.skipif(not os.getenv("DD_DATABASE_URL"), reason="DD_DATABASE_URL not set")


@pytest.fixture
def con():
    from powerspike.ingest import pg

    c = pg.connect(os.environ["DD_DATABASE_URL"])
    yield c
    df = demo_market()
    with c.cursor() as cur:
        cur.execute("DELETE FROM bars WHERE market_id LIKE '_test:%'")
        # drop the deleted rows from the aggregate now rather than at the policy's next run
        cur.execute("CALL refresh_continuous_aggregate('bars_15min', %s, %s)",
                    [df.index.min().to_pydatetime(), (df.index.max() + pd.Timedelta(minutes=15)).to_pydatetime()])


def test_pg_roundtrip_and_overwrite(con):
    df = demo_market()
    assert cache.save(con, "_test:m", df) == len(df)
    back = cache.load(con, "_test:m")
    assert len(back) == len(df)
    assert np.allclose(back["price"].values, df["price"].values)
    assert "_test:m" in cache.cached_markets(con)
    # re-saving the same window overwrites rather than duplicates
    assert cache.save(con, "_test:m", df) == len(df)
    assert len(cache.load(con, "_test:m")) == len(df)
    # a window slice honours start/end
    mid = df.index[len(df) // 2]
    assert len(cache.load(con, "_test:m", start=mid)) == len(df) - len(df) // 2


def test_pg_15min_matches_pandas_resample(con):
    df = demo_market()
    cache.save(con, "_test:agg", df)
    agg = cache.load_15min(con, "_test:agg")
    ref = df.resample("15min").agg(price=("price", "last"), volume=("volume", "sum"))
    assert len(agg) == len(ref)
    assert (agg.index == ref.index).all()
    assert np.allclose(agg["price"].values, ref["price"].values)
    assert np.allclose(agg["volume"].values, ref["volume"].values)


def test_pg_nan_volume_survives(con):
    df = demo_market()
    df["volume"] = np.nan
    cache.save(con, "_test:novol", df)
    back = cache.load(con, "_test:novol")
    assert back["volume"].isna().all()
