"""Load config files and environment settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent        # backend/
REPO_ROOT = ROOT.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"

# .env lives at the repo root (shared with the frontend); backend/.env also works.
# override=True: an empty shell-level var must not shadow .env
load_dotenv(REPO_ROOT / ".env", override=True)
load_dotenv(ROOT / ".env", override=True)

CACHE_PATH = Path(os.getenv("DD_CACHE_PATH") or DATA_DIR / "cache.duckdb")
# Tiger Cloud (Postgres + TimescaleDB) connection string. When set, ingest/cache.py stores bars there
# instead of the DuckDB file; tests pass an explicit DuckDB path and never see it.
DATABASE_URL = os.getenv("DD_DATABASE_URL") or ""
# A free-tier Flash model; the "-latest" alias tracks Google's current version.
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-flash-lite-latest"
# SEC asks every client to identify itself: "Name email@example.com"
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT") or ""


@lru_cache
def markets() -> list[dict]:
    return yaml.safe_load((CONFIG_DIR / "markets.yaml").read_text())["markets"]


def market(market_id: str) -> dict:
    for m in markets():
        if m["id"] == market_id:
            return m
    raise KeyError(market_id)


@lru_cache
def companies() -> list[dict]:
    """Tech companies with a news query and their Polymarket contracts (config/companies.yaml)."""
    return yaml.safe_load((CONFIG_DIR / "companies.yaml").read_text())["companies"]


def company(ticker: str) -> dict | None:
    t = ticker.upper()
    return next((c for c in companies() if c["ticker"] == t), None)


@lru_cache
def exposure() -> dict:
    return yaml.safe_load((CONFIG_DIR / "exposure.yaml").read_text())
