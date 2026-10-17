"""Tiger Cloud (Postgres + TimescaleDB) backend for the bar cache.

Same table as the DuckDB file, `bars(market_id, ts, price, volume)`, but as a *hypertable*
partitioned on `ts`, plus one *continuous aggregate*, `bars_15min`, that Postgres keeps refreshed in
the background: the 15-minute bars the drift detector works on, with `last(price)` and summed volume,
labelled by the bucket's left edge exactly like `pandas.resample`.

Selected by `DD_DATABASE_URL` (see config.py). Everything here is reached through `ingest/cache.py`,
which dispatches on the connection type, so nothing downstream knows which backend is in use.
"""

from __future__ import annotations

import math

import pandas as pd
import psycopg

from ..config import DATABASE_URL

SCHEMA = [
    "CREATE EXTENSION IF NOT EXISTS timescaledb",
    """CREATE TABLE IF NOT EXISTS bars (
        market_id TEXT NOT NULL,
        ts        TIMESTAMPTZ NOT NULL,
        price     DOUBLE PRECISION,
        volume    DOUBLE PRECISION,
        PRIMARY KEY (market_id, ts)
    )""",
    "SELECT create_hypertable('bars', 'ts', if_not_exists => TRUE)",
    # Continuous aggregate: the drift detector's 15-minute bars, materialised by Postgres.
    """CREATE MATERIALIZED VIEW IF NOT EXISTS bars_15min
       WITH (timescaledb.continuous) AS
       SELECT market_id,
              time_bucket('15 minutes', ts) AS bucket,
              last(price, ts)               AS price,
              sum(volume)                   AS volume,
              count(*)                      AS n
       FROM bars
       GROUP BY market_id, bucket
       WITH NO DATA""",
    # Real-time: rows newer than the last refresh are computed on read, so the view is never stale.
    "ALTER MATERIALIZED VIEW bars_15min SET (timescaledb.materialized_only = false)",
    # start_offset NULL: the policy may refresh any bucket that changed, however old. Invalidation
    # tracking makes that cheap, and a 60-day start_offset made older re-pulls invisible.
    """SELECT add_continuous_aggregate_policy('bars_15min',
         start_offset => NULL, end_offset => INTERVAL '1 minute',
         schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE)""",
]

_conn: psycopg.Connection | None = None


def connect(url: str = DATABASE_URL) -> psycopg.Connection:
    """One connection per process (the free/dev tiers have no pooler), reopened if it dropped."""
    global _conn
    if _conn is None or _conn.closed or _conn.broken:
        _conn = psycopg.connect(url, autocommit=True)
        ensure_schema(_conn)
    return _conn


def ensure_schema(con: psycopg.Connection) -> None:
    with con.cursor() as cur:
        for stmt in SCHEMA:
            cur.execute(stmt)


def save(con: psycopg.Connection, market_id: str, df: pd.DataFrame) -> int:
    """Upsert a bar frame. COPY into a temp table, then INSERT ... ON CONFLICT so re-pulls overwrite."""
    if df.empty:
        return 0
    rows = df.reset_index().rename(columns={df.index.name or "index": "ts"})[["ts", "price", "volume"]]
    # One transaction: the temp table lives exactly as long as the COPY + upsert, and a failed
    # pull leaves the table untouched.
    with con.transaction(), con.cursor() as cur:
        cur.execute("CREATE TEMP TABLE incoming (LIKE bars) ON COMMIT DROP")
        with cur.copy("COPY incoming (market_id, ts, price, volume) FROM STDIN") as copy:
            for ts, price, volume in rows.itertuples(index=False):
                copy.write_row((market_id, ts.to_pydatetime(), _f(price), _f(volume)))
        cur.execute(
            "INSERT INTO bars SELECT * FROM incoming "
            "ON CONFLICT (market_id, ts) DO UPDATE SET price = EXCLUDED.price, volume = EXCLUDED.volume"
        )
    # Materialise exactly the window just written so bars_15min is current before this returns
    # (cannot run inside the transaction above). Real-time mode covers anything newer than this.
    lo, hi = rows["ts"].min().to_pydatetime(), (rows["ts"].max() + pd.Timedelta(minutes=15)).to_pydatetime()
    with con.cursor() as cur:
        cur.execute("CALL refresh_continuous_aggregate('bars_15min', %s, %s)", [lo, hi])
    return len(rows)


def _f(x) -> float | None:
    """Postgres stores NaN natively, which keeps a volume-free series volume-free; None stays NULL."""
    return None if x is None else float(x)


def load(con: psycopg.Connection, market_id: str, start=None, end=None) -> pd.DataFrame:
    q = "SELECT ts, price, volume FROM bars WHERE market_id = %s"
    args: list = [market_id]
    if start is not None:
        q += " AND ts >= %s"
        args.append(pd.Timestamp(start).to_pydatetime())
    if end is not None:
        q += " AND ts <= %s"
        args.append(pd.Timestamp(end).to_pydatetime())
    with con.cursor() as cur:
        cur.execute(q + " ORDER BY ts", args)
        rows = cur.fetchall()
    return _frame(rows, "ts")


def load_15min(con: psycopg.Connection, market_id: str) -> pd.DataFrame:
    """The continuous aggregate for one market: 15-minute bars, left-edge labelled, as price/volume/n."""
    with con.cursor() as cur:
        cur.execute(
            "SELECT bucket, price, volume, n FROM bars_15min WHERE market_id = %s ORDER BY bucket",
            [market_id],
        )
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame(columns=["price", "volume", "n"])
    df = pd.DataFrame(rows, columns=["ts", "price", "volume", "n"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def last_ts(con: psycopg.Connection, market_id: str) -> pd.Timestamp | None:
    with con.cursor() as cur:
        cur.execute("SELECT max(ts) FROM bars WHERE market_id = %s", [market_id])
        row = cur.fetchone()
    return None if row is None or row[0] is None else pd.Timestamp(row[0]).tz_convert("UTC")


def cached_markets(con: psycopg.Connection) -> list[str]:
    with con.cursor() as cur:
        cur.execute("SELECT DISTINCT market_id FROM bars ORDER BY 1")
        return [r[0] for r in cur.fetchall()]


def stats(con: psycopg.Connection) -> list[dict]:
    """Per-market freshness read straight from the aggregate: last bar, 24h volume, bar count."""
    with con.cursor() as cur:
        cur.execute(
            """SELECT market_id, max(bucket) AS last_bucket, sum(n) AS bars,
                      sum(volume) FILTER (WHERE bucket >= now() - INTERVAL '24 hours') AS volume_24h
               FROM bars_15min GROUP BY market_id ORDER BY market_id"""
        )
        return [
            {"market_id": m, "last_bar": pd.Timestamp(lb).tz_convert("UTC").isoformat(), "bars": int(n),
             "volume_24h": None if v is None or math.isnan(v) else float(v)}
            for m, lb, n, v in cur.fetchall()
        ]


def _frame(rows, index: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["price", "volume"])
    df = pd.DataFrame(rows, columns=[index, "price", "volume"])
    df[index] = pd.to_datetime(df[index], utc=True)
    return df.set_index(index)
