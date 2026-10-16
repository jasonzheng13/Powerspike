from powerspike.ingest import cache
from powerspike.synthetic import demo_market


def test_cache_roundtrip(tmp_path):
    con = cache.connect(tmp_path / "c.duckdb")
    df = demo_market()
    assert cache.save(con, "m", df) == len(df)
    back = cache.load(con, "m")
    assert len(back) == len(df)
    assert (back["price"].values == df["price"].values).all()
    assert cache.cached_markets(con) == ["m"]


def test_duckdb_15min_and_stats(tmp_path):
    con = cache.connect(tmp_path / "c.duckdb")
    df = demo_market()
    cache.save(con, "m", df)
    agg = cache.load_15min(con, "m")
    assert len(agg) == len(df) // 15
    assert (agg["n"] == 15).all()
    assert agg["price"].iloc[-1] == df["price"].iloc[-1]
    st = cache.stats(con)
    assert [s["market_id"] for s in st] == ["m"]
    assert st[0]["bars"] == len(df)


def test_connect_with_explicit_path_ignores_database_url(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "DATABASE_URL", "postgres://nowhere")
    con = cache.connect(tmp_path / "c.duckdb")
    assert cache.cached_markets(con) == []
