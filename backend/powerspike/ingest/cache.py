"""Bar cache: a local DuckDB file by default, Tiger Cloud (Postgres + TimescaleDB) when
`DD_DATABASE_URL` is set.

Table `bars(market_id, ts, price, volume)`, one row per market per bar, `PRIMARY KEY (market_id, ts)`
so a re-pull overwrites rather than duplicates. Every function takes the connection `connect()`
returned and dispatches on its type, so callers never know which backend is in use. Tests pass an
explicit DuckDB path and stay offline; `pg.py` holds the Tiger side (hypertable + `bars_15min`
continuous aggregate).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from ..config import CACHE_PATH, DATABASE_URL


def backend() -> str:
    """Which backend `connect()` with no arguments will use: 'tiger' or 'duckdb'."""
    return "tiger" if DATABASE_URL else "duckdb"


def connect(path: Path | str | None = None):
    """Default connection: Tiger when DD_DATABASE_URL is set, else the DuckDB file at `path`/CACHE_PATH."""
    if path is None and DATABASE_URL:
        from . import pg
        return pg.connect(DATABASE_URL)
    path = CACHE_PATH if path is None else path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE IF NOT EXISTS bars (market_id VARCHAR, ts TIMESTAMPTZ, price DOUBLE, volume DOUBLE, PRIMARY KEY (market_id, ts))"
    )
    return con


def _is_duck(con) -> bool:
    return isinstance(con, duckdb.DuckDBPyConnection)


def save(con, market_id: str, df: pd.DataFrame) -> int:
    if not _is_duck(con):
        from . import pg
        return pg.save(con, market_id, df)
    if df.empty:
        return 0
    rows = df.reset_index().rename(columns={df.index.name or "index": "ts"})[["ts", "price", "volume"]]
    rows.insert(0, "market_id", market_id)
    con.register("incoming", rows)
    con.execute("INSERT OR REPLACE INTO bars SELECT market_id, ts, price, volume FROM incoming")
    con.unregister("incoming")
    return len(rows)


def load(con, market_id: str, start=None, end=None) -> pd.DataFrame:
    if not _is_duck(con):
        from . import pg
        return pg.load(con, market_id, start, end)
    q = "SELECT ts, price, volume FROM bars WHERE market_id = ?"
    args: list = [market_id]
    if start is not None:
        q += " AND ts >= ?"
        args.append(pd.Timestamp(start))
    if end is not None:
        q += " AND ts <= ?"
        args.append(pd.Timestamp(end))
    df = con.execute(q + " ORDER BY ts", args).df()
    if df.empty:
        return pd.DataFrame(columns=["price", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def load_15min(con, market_id: str) -> pd.DataFrame:
    """15-minute bars (left-edge labelled): the continuous aggregate on Tiger, a pandas resample on DuckDB."""
    if not _is_duck(con):
        from . import pg
        return pg.load_15min(con, market_id)
    df = load(con, market_id)
    if df.empty:
        return pd.DataFrame(columns=["price", "volume", "n"])
    g = df.resample("15min")
    out = pd.DataFrame({"price": g["price"].last(), "volume": g["volume"].sum(min_count=1), "n": g["price"].count()})
    return out[out["n"] > 0]


def cached_markets(con) -> list[str]:
    if not _is_duck(con):
        from . import pg
        return pg.cached_markets(con)
    return [r[0] for r in con.execute("SELECT DISTINCT market_id FROM bars ORDER BY 1").fetchall()]


def stats(con) -> list[dict]:
    """Per-market freshness: last bar, bar count, volume in the last 24 h of data."""
    if not _is_duck(con):
        from . import pg
        return pg.stats(con)
    rows = con.execute(
        """SELECT market_id, max(ts), count(*),
                  sum(volume) FILTER (WHERE ts >= now() - INTERVAL 24 HOUR)
           FROM bars GROUP BY market_id ORDER BY market_id"""
    ).fetchall()
    return [
        {"market_id": m, "last_bar": pd.Timestamp(lb).tz_convert("UTC").isoformat(), "bars": int(n),
         "volume_24h": None if v is None or pd.isna(v) else float(v)}
        for m, lb, n, v in rows
    ]
